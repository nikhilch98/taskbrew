"""Tests for human-in-the-loop database schema and interactions."""

import pytest
import aiosqlite
from pathlib import Path


@pytest.fixture
async def db():
    """Create an in-memory database with the schema."""
    from taskbrew.orchestrator.database import Database
    database = Database(":memory:")
    await database.initialize()
    yield database
    await database.close()


@pytest.fixture
async def db_with_group_and_task(db):
    """Database with a pre-inserted group and task for FK-dependent tests."""
    await db.execute(
        "INSERT INTO groups (id, title, status, created_at) VALUES (?, ?, ?, datetime('now'))",
        ("grp-1", "Test Group", "active"),
    )
    await db.execute(
        "INSERT INTO tasks (id, title, status, created_at) VALUES (?, ?, ?, datetime('now'))",
        ("task-1", "Test Task", "pending"),
    )
    return db


class TestHILSchema:
    """Test human_interaction_requests table exists and works."""

    @pytest.mark.asyncio
    async def test_interaction_request_table_exists(self, db):
        result = await db.execute_fetchall(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='human_interaction_requests'"
        )
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_task_chains_table_exists(self, db):
        result = await db.execute_fetchall(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='task_chains'"
        )
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_first_run_approvals_table_exists(self, db):
        result = await db.execute_fetchall(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='first_run_approvals'"
        )
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_insert_interaction_request(self, db_with_group_and_task):
        db = db_with_group_and_task
        await db.execute(
            "INSERT INTO human_interaction_requests "
            "(id, request_key, task_id, instance_token, request_type, status, payload, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))",
            ("req-1", "task-1:approval:0", "task-1", "tok-123", "approval", "pending", '{"summary": "test"}'),
        )
        row = await db.execute_fetchone("SELECT * FROM human_interaction_requests WHERE id = ?", ("req-1",))
        assert row is not None
        assert row["status"] == "pending"
        assert row["request_type"] == "approval"

    @pytest.mark.asyncio
    async def test_insert_task_chain(self, db_with_group_and_task):
        db = db_with_group_and_task
        await db.execute(
            "INSERT INTO task_chains (id, original_task_id, current_task_id, agent_role, revision_count, max_revision_cycles, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, datetime('now'))",
            ("chain-1", "task-1", "task-1", "coder_be", 0, 5),
        )
        row = await db.execute_fetchone("SELECT * FROM task_chains WHERE id = ?", ("chain-1",))
        assert row is not None
        assert row["revision_count"] == 0

    @pytest.mark.asyncio
    async def test_insert_first_run_approval(self, db_with_group_and_task):
        db = db_with_group_and_task
        await db.execute(
            "INSERT INTO first_run_approvals (id, group_id, agent_role, approved_at) VALUES (?, ?, ?, datetime('now'))",
            ("fra-1", "grp-1", "architect"),
        )
        row = await db.execute_fetchone("SELECT * FROM first_run_approvals WHERE group_id = ? AND agent_role = ?", ("grp-1", "architect"))
        assert row is not None
