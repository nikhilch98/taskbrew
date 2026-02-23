"""Tests for the MessagingManager."""

from __future__ import annotations

import pytest

from taskbrew.orchestrator.database import Database
from taskbrew.intelligence.messaging import MessagingManager


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
async def messaging(db: Database) -> MessagingManager:
    """Create a MessagingManager backed by the in-memory database."""
    return MessagingManager(db)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


async def _register_agents(db: Database, *agent_ids: str) -> None:
    """Insert agent instances so broadcast can discover them."""
    for aid in agent_ids:
        role = aid.split("-")[0] if "-" in aid else aid
        await db.execute(
            "INSERT OR IGNORE INTO agent_instances (instance_id, role, status, started_at) "
            "VALUES (?, ?, 'idle', '2025-01-01T00:00:00+00:00')",
            (aid, role),
        )


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------


async def test_send_and_get_inbox(messaging: MessagingManager):
    """Send a message and verify it appears in the recipient's inbox."""
    result = await messaging.send(
        from_agent="coder-1",
        to_agent="reviewer-1",
        content="Please review PR #42",
        priority="high",
    )
    assert result["from_agent"] == "coder-1"
    assert result["to_agent"] == "reviewer-1"
    assert result["priority"] == "high"

    inbox = await messaging.get_inbox("reviewer-1")
    assert len(inbox) == 1
    assert inbox[0]["content"] == "Please review PR #42"
    assert inbox[0]["from_agent"] == "coder-1"


async def test_inbox_unread_filter(messaging: MessagingManager):
    """Unread-only inbox should exclude read messages."""
    await messaging.send("a", "b", "msg-1")
    await messaging.send("a", "b", "msg-2")

    inbox_all = await messaging.get_inbox("b", unread_only=False)
    assert len(inbox_all) == 2

    # Mark first message as read
    msg_id = inbox_all[-1]["id"]  # oldest first in DESC order is last
    await messaging.mark_read(msg_id)

    inbox_unread = await messaging.get_inbox("b", unread_only=True)
    assert len(inbox_unread) == 1


async def test_mark_read(messaging: MessagingManager, db: Database):
    """mark_read should set read = 1."""
    await messaging.send("a", "b", "hello")
    inbox = await messaging.get_inbox("b")
    msg_id = inbox[0]["id"]

    await messaging.mark_read(msg_id)

    row = await db.execute_fetchone(
        "SELECT read FROM agent_messages WHERE id = ?", (msg_id,)
    )
    assert row["read"] == 1


async def test_mark_delivered(messaging: MessagingManager, db: Database):
    """mark_delivered should set delivered_at timestamp."""
    await messaging.send("a", "b", "hello")
    inbox = await messaging.get_inbox("b")
    msg_id = inbox[0]["id"]

    await messaging.mark_delivered(msg_id)

    row = await db.execute_fetchone(
        "SELECT delivered_at FROM agent_messages WHERE id = ?", (msg_id,)
    )
    assert row["delivered_at"] is not None


async def test_broadcast(messaging: MessagingManager, db: Database):
    """Broadcast should send a message to all agents except the sender."""
    await _register_agents(db, "pm-1", "coder-1", "reviewer-1", "tester-1")

    messages = await messaging.broadcast("pm-1", "Sprint 3 kickoff!")
    assert len(messages) == 3  # everyone except pm-1

    recipients = {m["to_agent"] for m in messages}
    assert "pm-1" not in recipients
    assert "coder-1" in recipients
    assert "reviewer-1" in recipients
    assert "tester-1" in recipients

    # Verify messages are stored in DB
    rows = await db.execute_fetchall(
        "SELECT * FROM agent_messages WHERE message_type = 'broadcast'"
    )
    assert len(rows) == 3


async def test_get_thread(messaging: MessagingManager):
    """get_thread should return all messages with the same thread_id in chronological order."""
    thread = "thread-review-42"
    await messaging.send("coder-1", "reviewer-1", "Ready for review", thread_id=thread)
    await messaging.send("reviewer-1", "coder-1", "Looks good, minor nit", thread_id=thread)
    await messaging.send("coder-1", "reviewer-1", "Fixed, PTAL", thread_id=thread)

    # Unrelated message (no thread)
    await messaging.send("pm-1", "coder-1", "How is it going?")

    thread_msgs = await messaging.get_thread(thread)
    assert len(thread_msgs) == 3
    assert thread_msgs[0]["content"] == "Ready for review"
    assert thread_msgs[2]["content"] == "Fixed, PTAL"


async def test_send_with_message_type(messaging: MessagingManager, db: Database):
    """Send with a custom message_type should persist correctly."""
    await messaging.send(
        from_agent="pm-1",
        to_agent="architect-1",
        content="Design review needed",
        message_type="request",
    )
    row = await db.execute_fetchone(
        "SELECT message_type FROM agent_messages WHERE from_agent = 'pm-1'"
    )
    assert row["message_type"] == "request"


# ------------------------------------------------------------------
# Regression: schema-drift fallback (migration 4 columns missing)
# ------------------------------------------------------------------


@pytest.fixture
async def db_no_migration4():
    """Database initialised *without* migration 4 enhanced columns."""
    database = Database(":memory:")
    # Manually initialise but skip migrations so the enhanced columns
    # (message_type, priority, delivered_at, thread_id) do not exist.
    database._conn = await __import__("aiosqlite").connect(":memory:")
    database._conn.row_factory = __import__("aiosqlite").Row
    await database._conn.execute("PRAGMA journal_mode=WAL")
    await database._conn.execute("PRAGMA foreign_keys=ON")
    # Create only the base agent_messages table (no migration 4 columns)
    await database._conn.executescript("""
        CREATE TABLE IF NOT EXISTS agent_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_agent TEXT NOT NULL,
            to_agent TEXT NOT NULL,
            content TEXT NOT NULL,
            read INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS agent_instances (
            instance_id TEXT PRIMARY KEY,
            role TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'idle',
            current_task TEXT,
            started_at TEXT,
            last_heartbeat TEXT
        );
    """)
    await database._conn.commit()
    yield database
    await database.close()


@pytest.fixture
async def messaging_no_mig4(db_no_migration4: Database) -> MessagingManager:
    """MessagingManager backed by DB without migration 4 columns."""
    return MessagingManager(db_no_migration4)


async def test_send_fallback_without_migration4(messaging_no_mig4: MessagingManager, db_no_migration4: Database):
    """send() should fall back to base columns when migration 4 columns are missing."""
    result = await messaging_no_mig4.send(
        from_agent="coder-1",
        to_agent="reviewer-1",
        content="Fallback test",
        priority="high",
        message_type="request",
    )
    assert result["from_agent"] == "coder-1"
    assert result["content"] == "Fallback test"

    # Verify the message was actually persisted
    inbox = await messaging_no_mig4.get_inbox("reviewer-1")
    assert len(inbox) == 1
    assert inbox[0]["content"] == "Fallback test"


async def test_broadcast_fallback_without_migration4(messaging_no_mig4: MessagingManager, db_no_migration4: Database):
    """broadcast() should fall back to base columns when migration 4 columns are missing."""
    await _register_agents(db_no_migration4, "pm-1", "coder-1", "reviewer-1")

    messages = await messaging_no_mig4.broadcast("pm-1", "Hello everyone!")
    assert len(messages) == 2  # everyone except pm-1

    # Verify messages stored
    rows = await db_no_migration4.execute_fetchall(
        "SELECT * FROM agent_messages"
    )
    assert len(rows) == 2


async def test_mark_delivered_fallback_without_migration4(messaging_no_mig4: MessagingManager, db_no_migration4: Database):
    """mark_delivered() should not raise when delivered_at column is missing."""
    await messaging_no_mig4.send("a", "b", "test")
    inbox = await messaging_no_mig4.get_inbox("b")
    msg_id = inbox[0]["id"]

    # Should not raise even though delivered_at column doesn't exist
    await messaging_no_mig4.mark_delivered(msg_id)


async def test_get_thread_fallback_without_migration4(messaging_no_mig4: MessagingManager):
    """get_thread() should return empty list when thread_id column is missing."""
    result = await messaging_no_mig4.get_thread("thread-123")
    assert result == []
