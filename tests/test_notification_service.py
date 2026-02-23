"""Tests for the NotificationService and the POST /api/notifications endpoint."""

from __future__ import annotations


import pytest

from taskbrew.orchestrator.database import Database
from taskbrew.orchestrator.event_bus import EventBus
from taskbrew.orchestrator.notification_service import NotificationService


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture
async def db():
    """Create and initialise an in-memory database."""
    database = Database(":memory:")
    await database.initialize()
    yield database
    await database.close()


@pytest.fixture
def event_bus() -> EventBus:
    return EventBus()


@pytest.fixture
def notif_service(db: Database, event_bus: EventBus) -> NotificationService:
    svc = NotificationService(db=db, event_bus=event_bus)
    svc.subscribe()
    return svc


# ------------------------------------------------------------------
# NotificationService unit tests
# ------------------------------------------------------------------


async def test_task_completed_creates_notification(
    notif_service: NotificationService, db: Database, event_bus: EventBus
):
    """Emitting task.completed should create a notification in the DB."""
    await event_bus.emit("task.completed", {
        "task_id": "CD-001",
        "agent_id": "coder-1",
        "group_id": "GRP-1",
        "model": "claude-sonnet-4-6",
    })
    # Give the async task a chance to run
    import asyncio
    await asyncio.sleep(0.05)

    notifications = await db.get_unread_notifications()
    assert len(notifications) >= 1
    n = notifications[0]
    assert n["type"] == "task_completed"
    assert "CD-001" in n["title"]
    assert n["severity"] == "info"


async def test_task_failed_creates_notification(
    notif_service: NotificationService, db: Database, event_bus: EventBus
):
    """Emitting task.failed should create an error-severity notification."""
    await event_bus.emit("task.failed", {
        "task_id": "CD-002",
        "error": "SyntaxError: unexpected token",
        "model": "claude-sonnet-4-6",
    })
    import asyncio
    await asyncio.sleep(0.05)

    notifications = await db.get_unread_notifications()
    assert len(notifications) >= 1
    n = notifications[0]
    assert n["type"] == "task_failed"
    assert "CD-002" in n["title"]
    assert n["severity"] == "error"
    assert "SyntaxError" in n["message"]


async def test_agent_error_creates_critical_notification(
    notif_service: NotificationService, db: Database, event_bus: EventBus
):
    """Emitting agent.error should create a critical-severity notification."""
    await event_bus.emit("agent.error", {
        "agent_id": "coder-1",
        "error": "Connection refused",
    })
    import asyncio
    await asyncio.sleep(0.05)

    notifications = await db.get_unread_notifications()
    assert len(notifications) >= 1
    n = notifications[0]
    assert n["type"] == "agent_error"
    assert "coder-1" in n["title"]
    assert n["severity"] == "critical"


async def test_escalation_created_notification(
    notif_service: NotificationService, db: Database, event_bus: EventBus
):
    """Emitting escalation.created should create a warning notification."""
    await event_bus.emit("escalation.created", {
        "task_id": "CD-003",
        "from_agent": "coder-1",
        "reason": "Cannot resolve merge conflict",
    })
    import asyncio
    await asyncio.sleep(0.05)

    notifications = await db.get_unread_notifications()
    assert len(notifications) >= 1
    n = notifications[0]
    assert n["type"] == "escalation"
    assert "CD-003" in n["title"]
    assert n["severity"] == "warning"
    assert "merge conflict" in n["message"]


async def test_notification_created_event_is_emitted(
    notif_service: NotificationService, db: Database, event_bus: EventBus
):
    """When a notification is created, a notification.created event should be emitted."""
    received = []

    async def handler(event):
        received.append(event)

    event_bus.subscribe("notification.created", handler)

    await event_bus.emit("task.completed", {
        "task_id": "CD-010",
        "agent_id": "coder-1",
        "group_id": "GRP-1",
        "model": "claude-sonnet-4-6",
    })
    import asyncio
    await asyncio.sleep(0.1)

    # The notification.created event should have been emitted
    notif_events = [e for e in received if e.get("type") == "notification.created"]
    assert len(notif_events) >= 1
    assert notif_events[0]["notification"]["type"] == "task_completed"


async def test_unhandled_events_do_not_create_notifications(
    notif_service: NotificationService, db: Database, event_bus: EventBus
):
    """Events that are not watched should not create notifications."""
    await event_bus.emit("task.created", {
        "task_id": "CD-099",
    })
    import asyncio
    await asyncio.sleep(0.05)

    notifications = await db.get_unread_notifications()
    assert len(notifications) == 0


async def test_long_error_message_truncated(
    notif_service: NotificationService, db: Database, event_bus: EventBus
):
    """Very long error messages should be truncated in the notification."""
    long_error = "x" * 500
    await event_bus.emit("task.failed", {
        "task_id": "CD-004",
        "error": long_error,
        "model": "claude-sonnet-4-6",
    })
    import asyncio
    await asyncio.sleep(0.05)

    notifications = await db.get_unread_notifications()
    assert len(notifications) >= 1
    # Message should be truncated to 200 chars
    assert len(notifications[0]["message"]) == 200


# ------------------------------------------------------------------
# POST /api/notifications endpoint test
# ------------------------------------------------------------------


async def test_create_notification_endpoint():
    """POST /api/notifications should create a notification and return it."""
    from pathlib import Path
    import tempfile

    from httpx import AsyncClient, ASGITransport

    from taskbrew.orchestrator.database import Database
    from taskbrew.orchestrator.event_bus import EventBus
    from taskbrew.orchestrator.task_board import TaskBoard
    from taskbrew.agents.instance_manager import InstanceManager
    from taskbrew.orchestrator.migration import MigrationManager

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        db = Database(str(tmp_path / "test.db"))
        await db.initialize()
        mm = MigrationManager(db)
        await mm.apply_pending()

        board = TaskBoard(db, group_prefixes={"pm": "FEAT"})
        await board.register_prefixes({"pm": "PM"})
        event_bus = EventBus()
        instance_mgr = InstanceManager(db)

        from taskbrew.dashboard.app import create_app

        app = create_app(
            event_bus=event_bus,
            task_board=board,
            instance_manager=instance_mgr,
            roles={},
            team_config=None,
            project_dir=str(tmp_path),
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Create a notification
            resp = await client.post("/api/notifications", json={
                "type": "test_alert",
                "title": "Test notification",
                "message": "This is a test",
                "severity": "info",
            })
            assert resp.status_code == 200
            body = resp.json()
            assert body["type"] == "test_alert"
            assert body["title"] == "Test notification"
            assert body["id"] is not None

            # Verify it appears in the list
            resp2 = await client.get("/api/notifications")
            assert resp2.status_code == 200
            notifs = resp2.json()
            assert any(n["type"] == "test_alert" for n in notifs)

            # Mark all as read
            resp3 = await client.post("/api/notifications/read-all")
            assert resp3.status_code == 200

            # List should be empty now
            resp4 = await client.get("/api/notifications")
            assert resp4.status_code == 200
            assert len(resp4.json()) == 0

        await db.close()
