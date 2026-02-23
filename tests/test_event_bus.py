import asyncio
import pytest
from ai_team.orchestrator.event_bus import EventBus


async def test_subscribe_and_emit():
    bus = EventBus()
    received = []

    async def handler(event):
        received.append(event)

    bus.subscribe("task_completed", handler)
    await bus.emit("task_completed", {"task_id": "1", "agent": "coder"})
    await asyncio.sleep(0.01)
    assert len(received) == 1
    assert received[0]["task_id"] == "1"


async def test_multiple_subscribers():
    bus = EventBus()
    results_a, results_b = [], []

    async def handler_a(event):
        results_a.append(event)

    async def handler_b(event):
        results_b.append(event)

    bus.subscribe("test_event", handler_a)
    bus.subscribe("test_event", handler_b)
    await bus.emit("test_event", {"data": "hello"})
    await asyncio.sleep(0.01)
    assert len(results_a) == 1
    assert len(results_b) == 1


async def test_unsubscribe():
    bus = EventBus()
    received = []

    async def handler(event):
        received.append(event)

    bus.subscribe("test_event", handler)
    bus.unsubscribe("test_event", handler)
    await bus.emit("test_event", {"data": "hello"})
    await asyncio.sleep(0.01)
    assert len(received) == 0


async def test_emit_unsubscribed_event_no_error():
    bus = EventBus()
    await bus.emit("nonexistent", {"data": "hello"})  # Should not raise


async def test_wildcard_subscriber():
    bus = EventBus()
    received = []

    async def handler(event):
        received.append(event)

    bus.subscribe("*", handler)
    await bus.emit("any_event", {"data": "1"})
    await bus.emit("another_event", {"data": "2"})
    await asyncio.sleep(0.01)
    assert len(received) == 2
