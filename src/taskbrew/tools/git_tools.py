"""MCP tools for git operations."""

import asyncio
import re

from claude_agent_sdk import tool, create_sdk_mcp_server

# Characters and sequences forbidden in git branch names
_BRANCH_FORBIDDEN_PATTERNS = re.compile(r'[~^:?*\[\]\\]|\.\.|@\{|\s')


def sanitize_branch_name(name: str) -> str:
    """Sanitize and validate a git branch name.

    Applies the following rules:
    - Strip leading hyphens to prevent git flag injection
    - Reject names containing ``..``, spaces, ``~``, ``^``, ``:``, ``?``,
      ``*``, ``[``, or ``\\``
    - Reject empty names

    Returns the validated branch name.

    Raises
    ------
    ValueError
        If the branch name is empty, starts with a hyphen after stripping,
        or contains forbidden characters/sequences.
    """
    if not name:
        raise ValueError("Branch name must not be empty")

    # Strip leading hyphens to prevent git flag injection (e.g. "--upload-pack")
    sanitized = name.lstrip("-")
    if not sanitized:
        raise ValueError(f"Invalid branch name: {name!r} (resolves to empty after stripping leading hyphens)")

    if sanitized != name:
        raise ValueError(
            f"Invalid branch name: {name!r} (cannot start with dash — "
            "leading hyphens are not allowed to prevent git flag injection)"
        )

    # Reject forbidden characters and sequences
    match = _BRANCH_FORBIDDEN_PATTERNS.search(sanitized)
    if match:
        raise ValueError(
            f"Invalid branch name: {name!r} (contains invalid characters — "
            f"forbidden character or sequence {match.group()!r})"
        )

    # Only allow safe characters: alphanumerics, dots, underscores, slashes, hyphens
    if not re.match(r'^[a-zA-Z0-9._/\-]+$', sanitized):
        raise ValueError(f"Invalid branch name: {name!r} (contains invalid characters)")

    return sanitized


# Keep backward compatibility alias
_validate_branch_name = sanitize_branch_name


def build_git_tools_server():
    @tool(
        "create_feature_branch",
        "Create a new git branch for a feature.",
        {"branch_name": str},
    )
    async def create_feature_branch(args):
        branch = args["branch_name"]
        try:
            _validate_branch_name(branch)
        except ValueError as exc:
            return {
                "content": [
                    {
                        "type": "text",
                        "text": f"Error: {exc}",
                    }
                ]
            }
        proc = await asyncio.create_subprocess_exec(
            "git",
            "checkout",
            "-b",
            branch,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"Branch '{branch}' created.\n{stdout.decode() + stderr.decode()}",
                }
            ]
        }

    @tool(
        "get_diff_summary",
        "Get a summary of current uncommitted changes.",
        {},
    )
    async def get_diff_summary(args):
        proc = await asyncio.create_subprocess_exec(
            "git",
            "diff",
            "--stat",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        return {
            "content": [
                {"type": "text", "text": stdout.decode() or "No changes."}
            ]
        }

    return create_sdk_mcp_server(
        name="git-tools", version="1.0.0", tools=[create_feature_branch, get_diff_summary]
    )
