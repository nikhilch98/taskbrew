"""Tests for the CheckpointManager."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from taskbrew.orchestrator.database import Database
from taskbrew.intelligence.checkpoints import CheckpointManager


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
async def checkpoints(db: Database) -> CheckpointManager:
    """Create a CheckpointManager backed by the in-memory database."""
    return CheckpointManager(db)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


async def _ensure_group(db: Database, group_id: str = "GRP-001") -> None:
    """Insert a group row if it doesn't already exist."""
    now = datetime.now(timezone.utc).isoformat()
    try:
        await db.execute(
            "INSERT INTO groups (id, title, status, created_at) VALUES (?, ?, 'active', ?)",
            (group_id, f"Test Group {group_id}", now),
        )
    except Exception:
        pass


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
        pass


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------


async def test_create_checkpoint(checkpoints: CheckpointManager, db: Database):
    """create_checkpoint() should insert a pending checkpoint."""
    await _ensure_task(db, "TSK-001")
    result = await checkpoints.create_checkpoint(
        task_id="TSK-001",
        agent_id="coder-1",
        checkpoint_type="deploy_approval",
        description="Ready to deploy to staging",
        context={"branch": "feat/new-feature", "commit": "abc123"},
    )
    assert result["task_id"] == "TSK-001"
    assert result["agent_id"] == "coder-1"
    assert result["checkpoint_type"] == "deploy_approval"
    assert result["status"] == "pending"

    # Verify stored in DB
    rows = await db.execute_fetchall("SELECT * FROM checkpoints")
    assert len(rows) == 1
    assert rows[0]["status"] == "pending"
    ctx = json.loads(rows[0]["context"])
    assert ctx["branch"] == "feat/new-feature"


async def test_approve_checkpoint(checkpoints: CheckpointManager, db: Database):
    """decide() with approved=True should set status to 'approved'."""
    await _ensure_task(db, "TSK-002")
    await checkpoints.create_checkpoint(
        task_id="TSK-002",
        agent_id="coder-1",
        checkpoint_type="merge_approval",
        description="PR ready to merge",
    )
    rows = await db.execute_fetchall("SELECT id FROM checkpoints")
    cp_id = rows[0]["id"]

    result = await checkpoints.decide(cp_id, approved=True, decided_by="human-admin")
    assert result["status"] == "approved"
    assert result["decided_by"] == "human-admin"

    row = await db.execute_fetchone("SELECT status, decided_by, decided_at FROM checkpoints WHERE id = ?", (cp_id,))
    assert row["status"] == "approved"
    assert row["decided_at"] is not None


async def test_reject_checkpoint(checkpoints: CheckpointManager, db: Database):
    """decide() with approved=False should set status to 'rejected'."""
    await _ensure_task(db, "TSK-003")
    await checkpoints.create_checkpoint(
        task_id="TSK-003",
        agent_id="coder-1",
        checkpoint_type="deploy_approval",
        description="Deploy to production",
    )
    rows = await db.execute_fetchall("SELECT id FROM checkpoints")
    cp_id = rows[0]["id"]

    result = await checkpoints.decide(cp_id, approved=False, decided_by="human-admin", reason="Not ready yet")
    assert result["status"] == "rejected"


async def test_get_pending_checkpoints(checkpoints: CheckpointManager, db: Database):
    """get_pending_checkpoints() should return only pending checkpoints."""
    await _ensure_task(db, "TSK-A")
    await _ensure_task(db, "TSK-B")
    await checkpoints.create_checkpoint("TSK-A", "agent-1", "review", "Review needed")
    await checkpoints.create_checkpoint("TSK-B", "agent-2", "deploy", "Deploy ready")

    pending = await checkpoints.get_pending_checkpoints()
    assert len(pending) == 2

    # Approve one
    cp_id = pending[0]["id"]
    await checkpoints.decide(cp_id, approved=True, decided_by="admin")

    pending = await checkpoints.get_pending_checkpoints()
    assert len(pending) == 1


async def test_checkpoint_creates_notification(checkpoints: CheckpointManager, db: Database):
    """create_checkpoint() should also create a notification."""
    await _ensure_task(db, "TSK-004")
    await checkpoints.create_checkpoint(
        task_id="TSK-004",
        agent_id="coder-1",
        checkpoint_type="security_review",
        description="Security-sensitive change requires human review",
    )

    notifications = await db.get_unread_notifications()
    assert len(notifications) >= 1
    notif = notifications[0]
    assert notif["type"] == "checkpoint"
    assert "TSK-004" in notif["title"]
    assert notif["severity"] == "warning"


async def test_get_checkpoints_for_task(checkpoints: CheckpointManager, db: Database):
    """get_checkpoints_for_task() should return checkpoints for a specific task only."""
    await _ensure_task(db, "TSK-X")
    await _ensure_task(db, "TSK-Y")
    await checkpoints.create_checkpoint("TSK-X", "agent-1", "review", "First checkpoint")
    await checkpoints.create_checkpoint("TSK-X", "agent-1", "deploy", "Second checkpoint")
    await checkpoints.create_checkpoint("TSK-Y", "agent-2", "review", "Different task")

    task_cps = await checkpoints.get_checkpoints_for_task("TSK-X")
    assert len(task_cps) == 2
    assert all(cp["task_id"] == "TSK-X" for cp in task_cps)
