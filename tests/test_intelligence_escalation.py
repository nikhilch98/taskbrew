"""Tests for the EscalationManager."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta

import pytest

from taskbrew.orchestrator.database import Database
from taskbrew.intelligence.escalation import EscalationManager


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
async def escalation(db: Database) -> EscalationManager:
    """Create an EscalationManager backed by the in-memory database."""
    return EscalationManager(db)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

_group_counter = 0


async def _ensure_group(db: Database, group_id: str = "GRP-001") -> None:
    """Insert a group row if it doesn't already exist."""
    now = datetime.now(timezone.utc).isoformat()
    try:
        await db.execute(
            "INSERT INTO groups (id, title, status, created_at) VALUES (?, ?, 'active', ?)",
            (group_id, f"Test Group {group_id}", now),
        )
    except Exception:
        pass  # already exists


async def _ensure_task(db: Database, task_id: str, group_id: str = "GRP-001") -> None:
    """Insert a minimal task row and its group if needed."""
    await _ensure_group(db, group_id)
    now = datetime.now(timezone.utc).isoformat()
    try:
        await db.execute(
            "INSERT INTO tasks (id, group_id, title, status, created_at) "
            "VALUES (?, ?, 'Test task', 'pending', ?)",
            (task_id, group_id, now),
        )
    except Exception:
        pass  # already exists


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------


async def test_escalate_creates_entry(escalation: EscalationManager, db: Database):
    """escalate() should create an escalation record."""
    await _ensure_task(db, "TSK-001")
    result = await escalation.escalate(
        task_id="TSK-001",
        from_agent="coder-1",
        reason="Cannot resolve merge conflict",
        severity="high",
        to_agent="architect-1",
    )
    assert result["task_id"] == "TSK-001"
    assert result["from_agent"] == "coder-1"
    assert result["to_agent"] == "architect-1"
    assert result["severity"] == "high"
    assert result["status"] == "open"


async def test_resolve_escalation(escalation: EscalationManager, db: Database):
    """resolve_escalation() should mark escalation as resolved."""
    await _ensure_task(db, "TSK-002")
    await escalation.escalate(
        task_id="TSK-002",
        from_agent="coder-1",
        reason="Stuck on dependency issue",
    )
    # Get the escalation id
    rows = await db.execute_fetchall("SELECT id FROM escalations")
    esc_id = rows[0]["id"]

    await escalation.resolve_escalation(esc_id, resolution="Dependency updated by architect")

    row = await db.execute_fetchone("SELECT status, resolution FROM escalations WHERE id = ?", (esc_id,))
    assert row["status"] == "resolved"
    assert row["resolution"] == "Dependency updated by architect"


async def test_get_open_escalations(escalation: EscalationManager, db: Database):
    """get_open_escalations() should return only open escalations."""
    await _ensure_task(db, "TSK-A")
    await _ensure_task(db, "TSK-B")
    await escalation.escalate("TSK-A", "agent-1", "Issue A")
    await escalation.escalate("TSK-B", "agent-2", "Issue B")

    open_escs = await escalation.get_open_escalations()
    assert len(open_escs) == 2

    # Resolve one
    esc_id = open_escs[0]["id"]
    await escalation.resolve_escalation(esc_id, "Fixed")

    open_escs = await escalation.get_open_escalations()
    assert len(open_escs) == 1


async def test_check_stuck_tasks(escalation: EscalationManager, db: Database):
    """check_stuck_tasks() should find tasks in_progress past the timeout with no recent heartbeat."""
    # Create a group first
    now = datetime.now(timezone.utc).isoformat()
    await _ensure_group(db, "GRP-001")

    # Create a task that started 60 minutes ago
    old_start = (datetime.now(timezone.utc) - timedelta(minutes=60)).isoformat()
    await db.execute(
        "INSERT INTO tasks (id, group_id, title, status, created_at, started_at, claimed_by) "
        "VALUES (?, 'GRP-001', 'Old task', 'in_progress', ?, ?, 'coder-1')",
        ("TSK-OLD", now, old_start),
    )

    # Create an agent instance with a stale heartbeat
    stale_heartbeat = (datetime.now(timezone.utc) - timedelta(minutes=45)).isoformat()
    await db.execute(
        "INSERT INTO agent_instances (instance_id, role, status, last_heartbeat) "
        "VALUES ('coder-1', 'coder', 'busy', ?)",
        (stale_heartbeat,),
    )

    # Create a recent task (should NOT be stuck)
    recent_start = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    await db.execute(
        "INSERT INTO tasks (id, group_id, title, status, created_at, started_at, claimed_by) "
        "VALUES (?, 'GRP-001', 'Recent task', 'in_progress', ?, ?, 'coder-2')",
        ("TSK-NEW", now, recent_start),
    )

    stuck = await escalation.check_stuck_tasks(timeout_minutes=30)
    assert len(stuck) == 1
    assert stuck[0]["id"] == "TSK-OLD"


async def test_escalation_creates_notification(escalation: EscalationManager, db: Database):
    """escalate() should also create a notification."""
    await _ensure_task(db, "TSK-003")
    await escalation.escalate(
        task_id="TSK-003",
        from_agent="tester-1",
        reason="All tests failing unexpectedly",
        severity="critical",
    )

    notifications = await db.get_unread_notifications()
    assert len(notifications) >= 1
    notif = notifications[0]
    assert notif["type"] == "escalation"
    assert "TSK-003" in notif["title"]
    assert notif["severity"] == "critical"


async def test_get_escalations_for_task(escalation: EscalationManager, db: Database):
    """get_escalations_for_task() should return escalations for a specific task."""
    await _ensure_task(db, "TSK-X")
    await _ensure_task(db, "TSK-Y")
    await escalation.escalate("TSK-X", "agent-1", "First issue")
    await escalation.escalate("TSK-X", "agent-2", "Second issue")
    await escalation.escalate("TSK-Y", "agent-3", "Different task")

    escs = await escalation.get_escalations_for_task("TSK-X")
    assert len(escs) == 2
    assert all(e["task_id"] == "TSK-X" for e in escs)
