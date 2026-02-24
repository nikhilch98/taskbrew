"""MCP task creation tool — runs as a FastMCP stdio server.

Usage (as subprocess):
    python -m ai_team.tools.task_tools

Environment:
    AI_TEAM_API_URL  Base URL of the dashboard API (default: http://127.0.0.1:8420)
"""

from __future__ import annotations

import json
import os
import urllib.request
import urllib.error

from mcp.server.fastmcp import FastMCP


def build_task_tools_server(api_url: str = "http://127.0.0.1:8420") -> FastMCP:
    mcp = FastMCP("task-tools")

    @mcp.tool()
    def create_task(
        group_id: str,
        title: str,
        assigned_to: str,
        assigned_by: str,
        task_type: str,
        description: str = "",
        priority: str = "medium",
        parent_id: str = "",
        blocked_by: str = "",
    ) -> str:
        """Create a new task on the task board and assign it to an agent role.

        Args:
            group_id: ID of the group this task belongs to (e.g. GRP-009).
            title: Short task title describing the work to be done.
            assigned_to: Role that should pick up the task: architect, coder, tester, reviewer.
            assigned_by: Your agent instance ID (e.g. pm-1) — who is creating this task.
            task_type: Task type the target role accepts. For architect: tech_design, architecture_review. For coder: implementation, bug_fix.
            description: Detailed description with acceptance criteria, file references, etc.
            priority: Task priority — critical, high, medium (default), or low.
            parent_id: Optional parent task ID to link this task in the hierarchy.
            blocked_by: Comma-separated list of task IDs that must complete before this one starts. Leave empty if not blocked.
        """
        payload: dict = {
            "group_id": group_id,
            "title": title,
            "assigned_to": assigned_to,
            "assigned_by": assigned_by,
            "task_type": task_type,
        }
        if description:
            payload["description"] = description
        if priority:
            payload["priority"] = priority
        if parent_id:
            payload["parent_id"] = parent_id
        if blocked_by:
            payload["blocked_by"] = [t.strip() for t in blocked_by.split(",") if t.strip()]

        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            f"{api_url}/api/tasks",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read())
                return f"Task created: {result['id']} — {result['title']} (status: {result['status']})"
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            return f"Error creating task (HTTP {e.code}): {body}"
        except Exception as e:
            return f"Error creating task: {e}"

    return mcp


if __name__ == "__main__":
    api_url = os.environ.get("AI_TEAM_API_URL", "http://127.0.0.1:8420")
    server = build_task_tools_server(api_url=api_url)
    server.run(transport="stdio")
