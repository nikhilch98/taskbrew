"""Human-in-the-loop checkpoint management."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class CheckpointManager:
    """Manage human-in-the-loop checkpoints for agent tasks."""

    def __init__(self, db, event_bus=None) -> None:
        self._db = db
        self._event_bus = event_bus

    async def create_checkpoint(
        self,
        task_id: str,
        agent_id: str,
        checkpoint_type: str,
        description: str,
        context: dict | None = None,
    ) -> dict:
        """Create a checkpoint that requires human approval before continuing."""
        now = datetime.now(timezone.utc).isoformat()
        context_json = json.dumps(context) if context else None
        await self._db.execute(
            "INSERT INTO checkpoints (task_id, agent_id, checkpoint_type, description, status, context, created_at) "
            "VALUES (?, ?, ?, ?, 'pending', ?, ?)",
            (task_id, agent_id, checkpoint_type, description, context_json, now),
        )
        checkpoint = {
            "task_id": task_id,
            "agent_id": agent_id,
            "checkpoint_type": checkpoint_type,
            "description": description,
            "status": "pending",
            "created_at": now,
        }
        if self._event_bus:
            await self._event_bus.emit("checkpoint.created", checkpoint)
        # Create notification
        await self._db.create_notification(
            type="checkpoint",
            title=f"Checkpoint: {checkpoint_type} — Task {task_id}",
            message=description,
            severity="warning",
        )
        return checkpoint

    async def decide(
        self, checkpoint_id: int, approved: bool, decided_by: str, reason: str | None = None
    ) -> dict:
        """Approve or reject a checkpoint."""
        now = datetime.now(timezone.utc).isoformat()
        status = "approved" if approved else "rejected"
        await self._db.execute(
            "UPDATE checkpoints SET status = ?, decided_by = ?, decided_at = ? WHERE id = ?",
            (status, decided_by, now, checkpoint_id),
        )
        result = {"id": checkpoint_id, "status": status, "decided_by": decided_by, "decided_at": now}
        if self._event_bus:
            await self._event_bus.emit(f"checkpoint.{status}", result)
        return result

    async def get_pending_checkpoints(self, limit: int = 20) -> list[dict]:
        """Get all pending checkpoints awaiting decision."""
        return await self._db.execute_fetchall(
            "SELECT * FROM checkpoints WHERE status = 'pending' ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )

    async def get_checkpoints_for_task(self, task_id: str) -> list[dict]:
        """Get all checkpoints for a task."""
        return await self._db.execute_fetchall(
            "SELECT * FROM checkpoints WHERE task_id = ? ORDER BY created_at DESC",
            (task_id,),
        )
