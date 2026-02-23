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
        """Broadcast a message to all agents (stored as message_type='broadcast')."""
        now = datetime.now(timezone.utc).isoformat()
        # Get all known agent instances
        instances = await self._db.execute_fetchall(
            "SELECT DISTINCT instance_id FROM agent_instances"
        )
        messages = []
        for inst in instances:
            if inst["instance_id"] != from_agent:
                try:
                    await self._db.execute(
                        "INSERT INTO agent_messages (from_agent, to_agent, content, message_type, priority, created_at) "
                        "VALUES (?, ?, ?, 'broadcast', 'normal', ?)",
                        (from_agent, inst["instance_id"], content, now),
                    )
                except Exception:
                    # Fallback if migration 4 enhanced columns don't exist yet
                    await self._db.execute(
                        "INSERT INTO agent_messages (from_agent, to_agent, content, created_at) "
                        "VALUES (?, ?, ?, ?)",
                        (from_agent, inst["instance_id"], content, now),
                    )
                messages.append({"to_agent": inst["instance_id"]})
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
