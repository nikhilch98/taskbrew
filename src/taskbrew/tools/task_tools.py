"""MCP task creation tool — runs as a FastMCP stdio server.

Usage (as subprocess):
    python -m taskbrew.tools.task_tools

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
            assigned_to: Role that should pick up the task: pm, architect, coder, verifier.
            assigned_by: Your agent instance ID (e.g. pm-1) — who is creating this task.
            task_type: Task type the target role accepts.
                - pm: goal, revision
                - architect: tech_design, architecture_review, rejection
                - coder: implementation, bug_fix, revision
                - verifier: verification
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
                task_id = result.get("id", "<unknown>")
                task_title = result.get("title", "<unknown>")
                task_status = result.get("status", "<unknown>")
                return f"Task created: {task_id} — {task_title} (status: {task_status})"
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            return f"Error creating task (HTTP {e.code}): {body}"
        except urllib.error.URLError as e:
            return f"Error creating task (connection failed — is the dashboard running?): {e.reason}"
        except Exception as e:
            return f"Error creating task (unexpected error): {e}"

    @mcp.tool()
    def list_tasks(
        group_id: str = "",
        assigned_to: str = "",
        status: str = "pending",
    ) -> str:
        """List tasks on the board filtered by group, role, and status.

        Use this to check for other pending tasks you could batch with your current work.

        Args:
            group_id: Filter by group ID (e.g. GRP-010). Leave empty for all groups.
            assigned_to: Filter by assigned role (e.g. "verifier"). Leave empty for all roles.
            status: Filter by status: pending, in_progress, completed, blocked, failed. Default: pending.
        """
        params = []
        if group_id:
            params.append(f"group_id={group_id}")
        if assigned_to:
            params.append(f"assigned_to={assigned_to}")
        query = "&".join(params)
        url = f"{api_url}/api/board"
        if query:
            url += f"?{query}"
        req = urllib.request.Request(url, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
                # data is dict like {"pending": [...], "in_progress": [...]}
                lines = []
                total = 0
                for s, tasks in data.items():
                    if status and s != status:
                        continue
                    for t in tasks:
                        lines.append(f"  {t['id']}: {t['title']} [{s}] (assigned: {t.get('assigned_to', '?')})")
                        total += 1
                if not lines:
                    return "No tasks found matching filters."
                return f"Found {total} tasks:\n" + "\n".join(lines[:30])  # cap at 30 for token efficiency
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            return f"Error listing tasks (HTTP {e.code}): {body}"
        except urllib.error.URLError as e:
            return f"Error listing tasks (connection failed): {e.reason}"
        except Exception as e:
            return f"Error listing tasks: {e}"

    @mcp.tool()
    def complete_task(
        task_id: str,
        status: str = "completed",
    ) -> str:
        """Mark a task as completed (or failed).

        Use this after you have finished working on a task to update its status
        on the board.

        Args:
            task_id: The ID of the task to complete (e.g. TSK-042).
            status: Final status — "completed" (default) or "failed".
        """
        payload: dict = {"status": status}
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            f"{api_url}/api/tasks/{task_id}/complete",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read())
                return f"Task {task_id} marked as {result.get('status', status)}."
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            return f"Error completing task (HTTP {e.code}): {body}"
        except urllib.error.URLError as e:
            return f"Error completing task (connection failed — is the dashboard running?): {e.reason}"
        except Exception as e:
            return f"Error completing task (unexpected error): {e}"

    @mcp.tool()
    def update_task(
        task_id: str,
        priority: str = "",
        assigned_to: str = "",
    ) -> str:
        """Update fields on an existing task (priority, assignment).

        Use this to re-prioritise or reassign a task without cancelling it.

        Args:
            task_id: The ID of the task to update (e.g. TSK-042).
            priority: New priority — critical, high, medium, or low. Leave empty to keep current.
            assigned_to: New assignee role. Leave empty to keep current.
        """
        payload: dict = {}
        if priority:
            payload["priority"] = priority
        if assigned_to:
            payload["assigned_to"] = assigned_to
        if not payload:
            return "No fields to update — specify at least priority or assigned_to."
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            f"{api_url}/api/tasks/{task_id}",
            data=data,
            headers={"Content-Type": "application/json"},
            method="PATCH",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read())
                parts = []
                if priority:
                    parts.append(f"priority={result.get('priority', priority)}")
                if assigned_to:
                    parts.append(f"assigned_to={result.get('assigned_to', assigned_to)}")
                return f"Task {task_id} updated: {', '.join(parts)}."
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            return f"Error updating task (HTTP {e.code}): {body}"
        except urllib.error.URLError as e:
            return f"Error updating task (connection failed — is the dashboard running?): {e.reason}"
        except Exception as e:
            return f"Error updating task (unexpected error): {e}"

    @mcp.tool()
    def send_message(
        from_agent: str,
        to_agent: str,
        content: str,
        priority: str = "normal",
    ) -> str:
        """Send a message to another agent.

        Args:
            from_agent: Your agent instance ID (e.g. coder-1).
            to_agent: Target agent instance ID (e.g. reviewer-1).
            content: Message content.
            priority: Message priority — normal (default), high, or urgent.
        """
        payload = {"from_agent": from_agent, "to_agent": to_agent, "content": content, "priority": priority}
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            f"{api_url}/api/messages",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as _resp:
                return f"Message sent to {to_agent}."
        except urllib.error.HTTPError as e:
            return f"Error sending message (HTTP {e.code}): {e.read().decode()}"
        except Exception as e:
            return f"Error sending message: {e}"

    @mcp.tool()
    def escalate_task(
        task_id: str,
        from_agent: str,
        reason: str,
        severity: str = "medium",
    ) -> str:
        """Escalate a task that you're stuck on or uncertain about.

        Args:
            task_id: The task ID to escalate.
            from_agent: Your agent instance ID.
            reason: Why you're escalating this task.
            severity: Escalation severity — low, medium (default), high, or critical.
        """
        payload = {"task_id": task_id, "from_agent": from_agent, "reason": reason, "severity": severity}
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            f"{api_url}/api/escalations",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as _resp:
                return f"Task {task_id} escalated ({severity})."
        except urllib.error.HTTPError as e:
            return f"Error escalating (HTTP {e.code}): {e.read().decode()}"
        except Exception as e:
            return f"Error escalating: {e}"

    return mcp


if __name__ == "__main__":
    api_url = os.environ.get("AI_TEAM_API_URL", "http://127.0.0.1:8420")
    server = build_task_tools_server(api_url=api_url)
    server.run(transport="stdio")
