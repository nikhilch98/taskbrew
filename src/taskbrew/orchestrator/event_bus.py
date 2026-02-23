"""Async event bus for inter-component communication."""

import asyncio
import logging
from collections import defaultdict
from typing import Any, Callable, Coroutine

EventHandler = Callable[[dict[str, Any]], Coroutine[Any, Any, None]]


class EventBus:
    """Simple asyncio-based pub/sub event bus."""

    MAX_HISTORY = 10000

    def __init__(self):
        self._handlers: dict[str, list[EventHandler]] = defaultdict(list)
        self._history: list[dict[str, Any]] = []

    def subscribe(self, event_type: str, handler: EventHandler) -> None:
        self._handlers[event_type].append(handler)

    def unsubscribe(self, event_type: str, handler: EventHandler) -> None:
        if event_type in self._handlers:
            self._handlers[event_type] = [
                h for h in self._handlers[event_type] if h is not handler
            ]

    async def emit(self, event_type: str, data: dict[str, Any]) -> None:
        event = {"type": event_type, **data}
        self._history.append(event)

        if len(self._history) > self.MAX_HISTORY:
            self._history = self._history[-self.MAX_HISTORY:]

        handlers = list(self._handlers.get(event_type, []))
        handlers.extend(self._handlers.get("*", []))

        for handler in handlers:
            asyncio.create_task(self._safe_dispatch(handler, event))

    async def send_message(self, from_agent: str, to_agent: str, content: str) -> None:
        """Send a direct message between agents.

        Creates an ``agent.message`` event and dispatches it to all
        registered handlers for that event type (plus wildcard handlers).
        """
        event: dict[str, Any] = {
            "type": "agent.message",
            "from": from_agent,
            "to": to_agent,
            "content": content,
        }
        self._history.append(event)
        if len(self._history) > self.MAX_HISTORY:
            self._history = self._history[-self.MAX_HISTORY:]

        handlers = list(self._handlers.get("agent.message", []))
        handlers.extend(self._handlers.get("*", []))
        for handler in handlers:
            asyncio.create_task(self._safe_dispatch(handler, event))

    async def _safe_dispatch(self, handler: EventHandler, event: dict[str, Any]) -> None:
        """Dispatch an event to a handler with error handling."""
        try:
            await handler(event)
        except Exception:
            logging.getLogger(__name__).exception(
                "Event handler error for %s", event.get("type", "unknown")
            )

    def get_history(self, event_type: str | None = None) -> list[dict[str, Any]]:
        if event_type is None:
            return list(self._history)
        return [e for e in self._history if e["type"] == event_type]
