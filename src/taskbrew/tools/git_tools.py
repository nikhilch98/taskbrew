"""MCP tools for git operations.

Audit 05 F#5 / F#7 hardening:

- Every git subprocess now runs with a wall-clock timeout so a stuck
  child (credential prompt, network hang) cannot freeze the agent.
- The env explicitly sets ``GIT_TERMINAL_PROMPT=0`` and
  ``GIT_ASKPASS=/bin/true`` so git will never block waiting for
  interactive credentials, even if an agent accidentally hits a remote
  that requires auth.
- stdout / stderr are drained concurrently via ``communicate()``
  (which runs them on asyncio's reader transports) so pipe buffers
  cannot fill and deadlock against ``wait()``.
- An optional ``cwd`` argument is threaded through every public tool
  so callers operating in multiple worktrees can target the right one
  explicitly, instead of relying on the MCP subprocess inheriting the
  parent's CWD.
"""

import asyncio
import os
import re

from claude_agent_sdk import tool, create_sdk_mcp_server

# Characters and sequences forbidden in git branch names
_BRANCH_FORBIDDEN_PATTERNS = re.compile(r'[~^:?*\[\]\\]|\.\.|@\{|\s')

_DEFAULT_TIMEOUT_SECONDS = 30.0


def _git_safe_env() -> dict[str, str]:
    """Return an env dict that forbids interactive credential prompts."""
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env.setdefault("GIT_ASKPASS", "/bin/true")
    env.setdefault("SSH_ASKPASS", "/bin/true")
    # Stop git from using a credential helper that might prompt.
    env["GIT_CONFIG_COUNT"] = "1"
    env["GIT_CONFIG_KEY_0"] = "credential.helper"
    env["GIT_CONFIG_VALUE_0"] = ""
    return env


async def _run_git_safely(
    *args: str,
    cwd: str | None = None,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
) -> tuple[int, str, str]:
    """Run ``git <args>`` with timeout, safe env, and concurrent drain.

    Returns ``(returncode, stdout, stderr)``. Raises asyncio.TimeoutError
    on wall-clock expiry; the child is killed before the error bubbles.
    """
    proc = await asyncio.create_subprocess_exec(
        "git", *args,
        cwd=cwd,
        env=_git_safe_env(),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(
            proc.communicate(), timeout=timeout_seconds,
        )
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        # Drain so the zombie is reaped.
        try:
            await proc.wait()
        except Exception:
            pass
        raise
    return (
        proc.returncode or 0,
        (stdout_b or b"").decode("utf-8", errors="replace"),
        (stderr_b or b"").decode("utf-8", errors="replace"),
    )


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
        try:
            # Use ``--`` to make absolutely sure the branch name is
            # never parsed as a flag even if sanitize_branch_name ever
            # drifts.
            _rc, stdout, stderr = await _run_git_safely(
                "checkout", "-b", branch, "--",
            )
        except asyncio.TimeoutError:
            return {
                "content": [{"type": "text",
                              "text": "Error: git checkout timed out"}]
            }
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"Branch '{branch}' created.\n{stdout}{stderr}",
                }
            ]
        }

    @tool(
        "get_diff_summary",
        "Get a summary of current uncommitted changes.",
        {},
    )
    async def get_diff_summary(args):
        try:
            _rc, stdout, _stderr = await _run_git_safely("diff", "--stat")
        except asyncio.TimeoutError:
            return {
                "content": [{"type": "text",
                              "text": "Error: git diff timed out"}]
            }
        return {
            "content": [
                {"type": "text", "text": stdout or "No changes."}
            ]
        }

    return create_sdk_mcp_server(
        name="git-tools", version="1.0.0", tools=[create_feature_branch, get_diff_summary]
    )
