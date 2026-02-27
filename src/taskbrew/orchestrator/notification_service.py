"""Notification service: subscribes to event bus events and creates
push notifications that are broadcast to WebSocket clients."""

from __future__ import annotations

import json
import logging
from typing import Any

from taskbrew.orchestrator.database import Database
from taskbrew.orchestrator.event_bus import EventBus

logger = logging.getLogger(__name__)

# Event types that trigger notifications
_WATCHED_EVENTS = {
    "task.completed",
    "task.failed",
    "agent.error",
    "budget_warning",
    "escalation.created",
}


class NotificationService:
    """Listens for key orchestrator events and creates notifications.

    Each notification is persisted to the database and then broadcast
    via the event bus as a ``notification.created`` event so that
    WebSocket-connected clients receive it in real time.
    """

    def __init__(
        self,
        db: Database,
        event_bus: EventBus,
    ) -> None:
        self._db = db
        self._event_bus = event_bus

    def subscribe(self) -> None:
        """Subscribe to the event bus for relevant events."""
        for event_type in _WATCHED_EVENTS:
            self._event_bus.subscribe(event_type, self._handle_event)

    async def _handle_event(self, event: dict[str, Any]) -> None:
        """Route an event to the appropriate notification creator."""
        event_type = event.get("type", "")
        try:
            if event_type == "task.completed":
                await self._on_task_completed(event)
            elif event_type == "task.failed":
                await self._on_task_failed(event)
            elif event_type == "agent.error":
                await self._on_agent_error(event)
            elif event_type == "escalation.created":
                await self._on_escalation_created(event)
            # budget_warning notifications are already created by CostManager
            # so we only need to broadcast the WebSocket event for them.
        except Exception:
            logger.exception(
                "Failed to create notification for event %s", event_type
            )

    async def _on_task_completed(self, event: dict[str, Any]) -> None:
        task_id = event.get("task_id", "unknown")
        agent_id = event.get("agent_id", "")
        notif = await self._db.create_notification(
            type="task_completed",
            title=f"Task {task_id} completed",
            message=f"Completed by {agent_id}" if agent_id else None,
            severity="info",
            data=json.dumps({"task_id": task_id, "agent_id": agent_id}),
        )
        await self._event_bus.emit("notification.created", {"notification": notif})

    async def _on_task_failed(self, event: dict[str, Any]) -> None:
        task_id = event.get("task_id", "unknown")
        error = event.get("error", "Unknown error")
        # Truncate error for the notification message
        short_error = error[:200] if len(error) > 200 else error
        notif = await self._db.create_notification(
            type="task_failed",
            title=f"Task {task_id} failed",
            message=short_error,
            severity="error",
            data=json.dumps({"task_id": task_id, "error": error[:1000]}),
        )
        await self._event_bus.emit("notification.created", {"notification": notif})

    async def _on_agent_error(self, event: dict[str, Any]) -> None:
        agent_id = event.get("agent_id", event.get("instance_id", "unknown"))
        error = event.get("error", "Unknown error")
        short_error = error[:200] if len(error) > 200 else error
        notif = await self._db.create_notification(
            type="agent_error",
            title=f"Agent {agent_id} error",
            message=short_error,
            severity="critical",
            data=json.dumps({"agent_id": agent_id, "error": error[:1000]}),
        )
        await self._event_bus.emit("notification.created", {"notification": notif})

    async def _on_escalation_created(self, event: dict[str, Any]) -> None:
        task_id = event.get("task_id", "unknown")
        from_agent = event.get("from_agent", "")
        reason = event.get("reason", "")
        short_reason = reason[:200] if len(reason) > 200 else reason
        notif = await self._db.create_notification(
            type="escalation",
            title=f"Escalation for task {task_id}",
            message=f"From {from_agent}: {short_reason}" if from_agent else short_reason,
            severity="warning",
            data=json.dumps({"task_id": task_id, "from_agent": from_agent}),
        )
        await self._event_bus.emit("notification.created", {"notification": notif})
