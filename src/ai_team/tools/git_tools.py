"""MCP tools for git operations."""

import asyncio

from claude_agent_sdk import tool, create_sdk_mcp_server


def build_git_tools_server():
    @tool(
        "create_feature_branch",
        "Create a new git branch for a feature.",
        {"branch_name": str},
    )
    async def create_feature_branch(args):
        branch = args["branch_name"]
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
