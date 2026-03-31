"""Tests for human-in-the-loop database schema and interactions."""

import pytest
import aiosqlite
from pathlib import Path
from httpx import AsyncClient, ASGITransport
from fastapi import FastAPI
from taskbrew.orchestrator.interactions import InteractionManager


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


@pytest.fixture
async def db_for_interactions(db):
    """Database pre-seeded with groups and tasks needed by InteractionManager tests."""
    for gid in ("g1",):
        await db.execute(
            "INSERT OR IGNORE INTO groups (id, title, status, created_at) VALUES (?, ?, ?, datetime('now'))",
            (gid, f"Group {gid}", "active"),
        )
    for tid in ("t1", "t2", "t3"):
        await db.execute(
            "INSERT OR IGNORE INTO tasks (id, title, status, created_at) VALUES (?, ?, ?, datetime('now'))",
            (tid, f"Task {tid}", "pending"),
        )
    return db


class TestInteractionManager:
    """Test InteractionManager CRUD operations."""

    @pytest.mark.asyncio
    async def test_create_and_get_pending(self, db_for_interactions):
        mgr = InteractionManager(db_for_interactions)
        req = await mgr.create_request(
            task_id="t1", group_id="g1", agent_role="coder",
            instance_token="tok1", req_type="approval",
            request_data={"summary": "test work"},
        )
        assert req["status"] == "pending"
        assert req["type"] == "approval"

        pending = await mgr.get_pending()
        assert len(pending) == 1
        assert pending[0]["id"] == req["id"]

    @pytest.mark.asyncio
    async def test_resolve_and_history(self, db_for_interactions):
        mgr = InteractionManager(db_for_interactions)
        req = await mgr.create_request(
            task_id="t2", group_id="g1", agent_role="designer",
            instance_token="tok2", req_type="approval",
            request_data={"summary": "mockup"},
        )
        resolved = await mgr.resolve(req["id"], "approved", {"notes": "looks good"})
        assert resolved["status"] == "approved"
        assert resolved["response_data"]["notes"] == "looks good"

        pending = await mgr.get_pending()
        assert len(pending) == 0

        history = await mgr.get_history()
        assert len(history) == 1

    @pytest.mark.asyncio
    async def test_idempotent_create(self, db_for_interactions):
        mgr = InteractionManager(db_for_interactions)
        req1 = await mgr.create_request(
            task_id="t3", group_id="g1", agent_role="coder",
            instance_token="tok3", req_type="clarification",
            request_data={"question": "which DB?"},
            request_key="t3:clarification:0",
        )
        req2 = await mgr.create_request(
            task_id="t3", group_id="g1", agent_role="coder",
            instance_token="tok3", req_type="clarification",
            request_data={"question": "which DB?"},
            request_key="t3:clarification:0",
        )
        assert req1["id"] == req2["id"]

    @pytest.mark.asyncio
    async def test_first_run_check_and_record(self, db_for_interactions):
        mgr = InteractionManager(db_for_interactions)
        assert await mgr.check_first_run("g1", "architect") is False
        await mgr.record_first_run("g1", "architect")
        assert await mgr.check_first_run("g1", "architect") is True
        # Idempotent
        await mgr.record_first_run("g1", "architect")
        assert await mgr.check_first_run("g1", "architect") is True


# ---------------------------------------------------------------------------
# Dashboard Interactions API tests
# ---------------------------------------------------------------------------


@pytest.fixture
async def db_for_api(db):
    """Database pre-seeded with groups and tasks needed by API tests."""
    for gid in ("g1",):
        await db.execute(
            "INSERT OR IGNORE INTO groups (id, title, status, created_at) VALUES (?, ?, ?, datetime('now'))",
            (gid, f"Group {gid}", "active"),
        )
    for tid in ("t1", "t2", "t3", "t4"):
        await db.execute(
            "INSERT OR IGNORE INTO tasks (id, title, status, created_at) VALUES (?, ?, ?, datetime('now'))",
            (tid, f"Task {tid}", "pending"),
        )
    return db


class TestInteractionsAPI:
    """Test /api/interactions endpoints."""

    @pytest.fixture
    async def app_with_interactions(self, db_for_api):
        from taskbrew.dashboard.routers.interactions import router as int_router, set_interaction_deps
        mgr = InteractionManager(db_for_api)
        set_interaction_deps(mgr)
        app = FastAPI()
        app.include_router(int_router)
        yield app, mgr

    @pytest.fixture
    async def int_client(self, app_with_interactions):
        app, mgr = app_with_interactions
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c, mgr

    @pytest.mark.asyncio
    async def test_pending_empty(self, int_client):
        client, mgr = int_client
        resp = await client.get("/api/interactions/pending")
        assert resp.status_code == 200
        assert resp.json()["count"] == 0

    @pytest.mark.asyncio
    async def test_approve_flow(self, int_client):
        client, mgr = int_client
        # Create a pending request directly
        req = await mgr.create_request(
            task_id="t1", group_id="g1", agent_role="designer",
            instance_token="tok1", req_type="approval",
            request_data={"summary": "mockup done"},
        )
        # Check pending
        resp = await client.get("/api/interactions/pending")
        assert resp.json()["count"] == 1

        # Approve
        resp = await client.post(f"/api/interactions/{req['id']}/approve", json={"notes": "looks great"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "approved"

        # No longer pending
        resp = await client.get("/api/interactions/pending")
        assert resp.json()["count"] == 0

        # In history
        resp = await client.get("/api/interactions/history")
        assert resp.json()["count"] == 1

    @pytest.mark.asyncio
    async def test_reject_flow(self, int_client):
        client, mgr = int_client
        req = await mgr.create_request(
            task_id="t2", group_id="g1", agent_role="coder",
            instance_token="tok2", req_type="approval",
            request_data={"summary": "code review"},
        )
        resp = await client.post(f"/api/interactions/{req['id']}/reject", json={"feedback": "fix the tests"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "rejected"

    @pytest.mark.asyncio
    async def test_respond_to_clarification(self, int_client):
        client, mgr = int_client
        req = await mgr.create_request(
            task_id="t3", group_id="g1", agent_role="coder_be",
            instance_token="tok3", req_type="clarification",
            request_data={"question": "REST or GraphQL?"},
        )
        resp = await client.post(f"/api/interactions/{req['id']}/respond", json={"response": "Use REST"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "responded"

    @pytest.mark.asyncio
    async def test_skip_clarification(self, int_client):
        client, mgr = int_client
        req = await mgr.create_request(
            task_id="t4", group_id="g1", agent_role="pm",
            instance_token="tok4", req_type="clarification",
            request_data={"question": "which framework?"},
        )
        resp = await client.post(f"/api/interactions/{req['id']}/skip")
        assert resp.status_code == 200
        assert resp.json()["status"] == "skipped"
