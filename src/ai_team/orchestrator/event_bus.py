"""Async event bus for inter-component communication."""

import asyncio
from collections import defaultdict
from typing import Any, Callable, Coroutine

EventHandler = Callable[[dict[str, Any]], Coroutine[Any, Any, None]]


class EventBus:
    """Simple asyncio-based pub/sub event bus."""

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

        handlers = list(self._handlers.get(event_type, []))
        handlers.extend(self._handlers.get("*", []))

        for handler in handlers:
            asyncio.create_task(handler(event))

    def get_history(self, event_type: str | None = None) -> list[dict[str, Any]]:
        if event_type is None:
            return list(self._history)
        return [e for e in self._history if e["type"] == event_type]
