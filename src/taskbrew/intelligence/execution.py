"""Execution helpers: commit planning, debugging aids."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class CommitPlanner:
    """Plan atomic multi-file commits from task output."""

    def __init__(self, db) -> None:
        self._db = db

    async def plan_commit(self, task_id: str, files: list[str], message: str | None = None) -> dict:
        """Create a commit plan for the given files.

        Returns a plan dict with files, message, and metadata.
        """
        task = await self._db.execute_fetchone(
            "SELECT * FROM tasks WHERE id = ?", (task_id,)
        )

        auto_message = message
        if not auto_message and task:
            task_type = task.get("task_type", "change")
            title = task.get("title", "update")
            auto_message = f"{task_type}({task_id}): {title}"

        plan = {
            "task_id": task_id,
            "files": files,
            "message": auto_message or f"feat({task_id}): update files",
            "file_count": len(files),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        # Store in task_plans table
        await self._db.execute(
            "INSERT INTO task_plans (task_id, plan_type, content, created_by, status, created_at) "
            "VALUES (?, 'commit', ?, 'system', 'ready', ?)",
            (task_id, json.dumps(plan), plan["created_at"]),
        )

        return plan

    async def get_commit_plans(self, task_id: str) -> list[dict]:
        """Get all commit plans for a task."""
        rows = await self._db.execute_fetchall(
            "SELECT * FROM task_plans WHERE task_id = ? AND plan_type = 'commit' ORDER BY created_at",
            (task_id,),
        )
        result = []
        for row in rows:
            entry = dict(row)
            if entry.get("content"):
                entry["content"] = json.loads(entry["content"])
            result.append(entry)
        return result


class DebuggingHelper:
    """Helpers for debugging task failures."""

    def __init__(self, db) -> None:
        self._db = db

    async def get_failure_context(self, task_id: str) -> dict:
        """Gather context about a failed task for debugging."""
        task = await self._db.execute_fetchone(
            "SELECT * FROM tasks WHERE id = ?", (task_id,)
        )
        if not task:
            return {"error": "Task not found"}

        # Get related events
        events = await self._db.execute_fetchall(
            "SELECT * FROM events WHERE task_id = ? ORDER BY created_at DESC LIMIT 20",
            (task_id,),
        )

        # Get escalations
        escalations = await self._db.execute_fetchall(
            "SELECT * FROM escalations WHERE task_id = ? ORDER BY created_at DESC",
            (task_id,),
        )

        # Get quality scores
        scores = await self._db.execute_fetchall(
            "SELECT * FROM quality_scores WHERE task_id = ? ORDER BY created_at DESC",
            (task_id,),
        )

        return {
            "task": dict(task),
            "events": events,
            "escalations": escalations,
            "quality_scores": scores,
            "rejection_reason": task.get("rejection_reason"),
        }

    async def suggest_fix(self, task_id: str) -> dict:
        """Suggest potential fixes based on failure patterns."""
        context = await self.get_failure_context(task_id)
        task = context.get("task", {})

        suggestions = []

        if task.get("rejection_reason"):
            suggestions.append({
                "type": "address_rejection",
                "description": f"Address rejection: {task['rejection_reason']}",
                "priority": "high",
            })

        if context.get("escalations"):
            for esc in context["escalations"]:
                if esc.get("status") == "open":
                    suggestions.append({
                        "type": "resolve_escalation",
                        "description": f"Resolve escalation: {esc.get('reason', 'unknown')}",
                        "priority": esc.get("severity", "medium"),
                    })

        if not suggestions:
            suggestions.append({
                "type": "retry",
                "description": "No specific issues found. Consider retrying the task.",
                "priority": "low",
            })

        return {
            "task_id": task_id,
            "status": task.get("status", "unknown"),
            "suggestions": suggestions,
        }
