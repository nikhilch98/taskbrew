"""Enhanced agent-to-agent messaging with delivery tracking."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class MessagingManager:
    """Enhanced messaging between agents with delivery tracking and broadcasting."""

    def __init__(self, db, event_bus=None) -> None:
        self._db = db
        self._event_bus = event_bus

    async def send(
        self,
        from_agent: str,
        to_agent: str,
        content: str,
        priority: str = "normal",
        message_type: str = "direct",
        thread_id: str | None = None,
    ) -> dict:
        """Send a message from one agent to another."""
        now = datetime.now(timezone.utc).isoformat()
        try:
            await self._db.execute(
                "INSERT INTO agent_messages (from_agent, to_agent, content, message_type, priority, thread_id, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (from_agent, to_agent, content, message_type, priority, thread_id, now),
            )
        except Exception:
            # Fallback if migration 4 enhanced columns don't exist yet
            await self._db.execute(
                "INSERT INTO agent_messages (from_agent, to_agent, content, created_at) "
                "VALUES (?, ?, ?, ?)",
                (from_agent, to_agent, content, now),
            )
        msg = {
            "from_agent": from_agent,
            "to_agent": to_agent,
            "content": content,
            "priority": priority,
            "message_type": message_type,
            "thread_id": thread_id,
            "created_at": now,
        }
        if self._event_bus:
            await self._event_bus.emit("message.sent", msg)
        return msg

    async def broadcast(self, from_agent: str, content: str, tag: str = "announcement") -> list[dict]:
        """Broadcast a message to all agents (stored as message_type='broadcast').

        audit 09 F#7: previously issued one INSERT per recipient with
        no transaction, so a broadcast to N agents was N + 1 round
        trips and a crash mid-loop left partial inboxes. We now
        collect the rows up front and use ``executemany`` inside one
        transaction so either every inbox receives the broadcast or
        none do.
        """
        now = datetime.now(timezone.utc).isoformat()
        instances = await self._db.execute_fetchall(
            "SELECT DISTINCT instance_id FROM agent_instances"
        )
        recipients = [
            inst["instance_id"] for inst in instances
            if inst["instance_id"] != from_agent
        ]
        if not recipients:
            return []

        # Try the enhanced schema first (migration 4 columns). If it
        # fails, fall through to the legacy schema on the same
        # transaction.
        enhanced_rows = [
            (from_agent, to_agent, content, "broadcast", "normal", now)
            for to_agent in recipients
        ]
        legacy_rows = [
            (from_agent, to_agent, content, now)
            for to_agent in recipients
        ]
        messages = [{"to_agent": to_agent} for to_agent in recipients]
        try:
            async with self._db.transaction() as conn:
                await conn.executemany(
                    "INSERT INTO agent_messages (from_agent, to_agent, content, message_type, priority, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    enhanced_rows,
                )
        except Exception:
            # Enhanced schema missing -- retry with legacy columns in
            # its own transaction.
            async with self._db.transaction() as conn:
                await conn.executemany(
                    "INSERT INTO agent_messages (from_agent, to_agent, content, created_at) "
                    "VALUES (?, ?, ?, ?)",
                    legacy_rows,
                )
        if self._event_bus:
            await self._event_bus.emit("message.broadcast", {"from_agent": from_agent, "tag": tag, "recipients": len(messages)})
        return messages

    async def get_inbox(self, agent_id: str, unread_only: bool = True, limit: int = 20) -> list[dict]:
        """Get messages for an agent."""
        if unread_only:
            return await self._db.execute_fetchall(
                "SELECT * FROM agent_messages WHERE to_agent = ? AND read = 0 ORDER BY created_at DESC LIMIT ?",
                (agent_id, limit),
            )
        return await self._db.execute_fetchall(
            "SELECT * FROM agent_messages WHERE to_agent = ? ORDER BY created_at DESC LIMIT ?",
            (agent_id, limit),
        )

    async def mark_read(self, message_id: int) -> None:
        """Mark a message as read."""
        await self._db.execute(
            "UPDATE agent_messages SET read = 1 WHERE id = ?", (message_id,)
        )

    async def mark_delivered(self, message_id: int) -> None:
        """Mark a message as delivered."""
        now = datetime.now(timezone.utc).isoformat()
        try:
            await self._db.execute(
                "UPDATE agent_messages SET delivered_at = ? WHERE id = ?", (now, message_id)
            )
        except Exception:
            # Fallback: delivered_at column doesn't exist (migration 4 not applied)
            logger.debug("delivered_at column not available, skipping mark_delivered for message %s", message_id)

    async def get_thread(self, thread_id: str, limit: int = 50) -> list[dict]:
        """Get all messages in a thread."""
        try:
            return await self._db.execute_fetchall(
                "SELECT * FROM agent_messages WHERE thread_id = ? ORDER BY created_at ASC LIMIT ?",
                (thread_id, limit),
            )
        except Exception:
            # Fallback: thread_id column doesn't exist (migration 4 not applied)
            return []
