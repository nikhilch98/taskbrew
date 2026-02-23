"""Escalation workflows for stuck or uncertain tasks."""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)


class EscalationManager:
    """Detect stuck tasks and manage escalation workflows."""

    def __init__(self, db, task_board=None, event_bus=None, instance_manager=None) -> None:
        self._db = db
        self._task_board = task_board
        self._event_bus = event_bus
        self._instance_manager = instance_manager

    async def check_stuck_tasks(self, timeout_minutes: int = 30) -> list[dict]:
        """Find tasks that have been in_progress longer than timeout_minutes without heartbeat updates."""
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=timeout_minutes)).isoformat()
        stuck = await self._db.execute_fetchall(
            "SELECT t.id, t.title, t.claimed_by, t.started_at, ai.last_heartbeat "
            "FROM tasks t LEFT JOIN agent_instances ai ON t.claimed_by = ai.instance_id "
            "WHERE t.status = 'in_progress' AND t.started_at < ? "
            "AND (ai.last_heartbeat IS NULL OR ai.last_heartbeat < ?)",
            (cutoff, cutoff),
        )
        return stuck

    async def escalate(
        self,
        task_id: str,
        from_agent: str,
        reason: str,
        severity: str = "medium",
        to_agent: str | None = None,
    ) -> dict:
        """Create an escalation for a task."""
        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            "INSERT INTO escalations (task_id, from_agent, to_agent, reason, severity, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, 'open', ?)",
            (task_id, from_agent, to_agent, reason, severity, now),
        )
        escalation = {
            "task_id": task_id,
            "from_agent": from_agent,
            "to_agent": to_agent,
            "reason": reason,
            "severity": severity,
            "status": "open",
            "created_at": now,
        }
        if self._event_bus:
            await self._event_bus.emit("escalation.created", escalation)
        # Create notification
        await self._db.create_notification(
            type="escalation",
            title=f"Escalation: {severity} — Task {task_id}",
            message=reason,
            severity="warning" if severity in ("medium", "low") else "critical",
        )
        return escalation

    async def resolve_escalation(self, escalation_id: int, resolution: str, resolved_by: str | None = None) -> None:
        """Resolve an open escalation."""
        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            "UPDATE escalations SET status = 'resolved', resolution = ?, resolved_at = ? WHERE id = ?",
            (resolution, now, escalation_id),
        )
        if self._event_bus:
            await self._event_bus.emit("escalation.resolved", {"id": escalation_id, "resolution": resolution})

    async def get_open_escalations(self, limit: int = 20) -> list[dict]:
        """Get all open escalations."""
        return await self._db.execute_fetchall(
            "SELECT * FROM escalations WHERE status = 'open' ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )

    async def get_escalations_for_task(self, task_id: str) -> list[dict]:
        """Get all escalations for a specific task."""
        return await self._db.execute_fetchall(
            "SELECT * FROM escalations WHERE task_id = ? ORDER BY created_at DESC",
            (task_id,),
        )
