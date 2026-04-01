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


# ---------------------------------------------------------------------------
# Revision chain tracking tests (Task 7)
# ---------------------------------------------------------------------------


class TestRevisionTracking:
    """Test revision chain tracking."""

    @pytest.mark.asyncio
    async def test_chain_insert_and_query(self, db_with_group_and_task):
        db = db_with_group_and_task
        # Insert a second task for the chain
        await db.execute(
            "INSERT INTO tasks (id, title, status, created_at) VALUES (?, ?, ?, datetime('now'))",
            ("task-2", "Revised Task", "pending"),
        )
        # Insert two revisions in the same chain
        await db.execute(
            "INSERT INTO task_chains (id, original_task_id, current_task_id, agent_role, revision_count, max_revision_cycles, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, datetime('now'))",
            ("chain-100", "task-1", "task-1", "coder_be", 0, 5),
        )
        await db.execute(
            "INSERT INTO task_chains (id, original_task_id, current_task_id, agent_role, revision_count, max_revision_cycles, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, datetime('now'))",
            ("chain-101", "task-1", "task-2", "coder_be", 1, 5),
        )
        rows = await db.execute_fetchall(
            "SELECT * FROM task_chains WHERE original_task_id = ? ORDER BY revision_count",
            ("task-1",),
        )
        assert len(rows) == 2
        assert rows[0]["revision_count"] == 0
        assert rows[1]["revision_count"] == 1
        assert rows[0]["max_revision_cycles"] == 5


# ---------------------------------------------------------------------------
# Integration tests (Task 8)
# ---------------------------------------------------------------------------


@pytest.fixture
async def db_for_integration(db):
    """Database pre-seeded for integration tests (groups + tasks + first_run groups)."""
    for gid in ("g-int", "g-fr", "g-other"):
        await db.execute(
            "INSERT OR IGNORE INTO groups (id, title, status, created_at) VALUES (?, ?, ?, datetime('now'))",
            (gid, f"Group {gid}", "active"),
        )
    for tid in ("t-int-1", "t-int-2"):
        await db.execute(
            "INSERT OR IGNORE INTO tasks (id, title, status, created_at) VALUES (?, ?, ?, datetime('now'))",
            (tid, f"Task {tid}", "pending"),
        )
    return db


class TestHILIntegration:
    """End-to-end human-in-the-loop flow tests."""

    @pytest.mark.asyncio
    async def test_full_approval_flow(self, db_for_integration):
        """Agent creates request -> user approves -> agent gets response."""
        mgr = InteractionManager(db_for_integration)
        # Agent creates approval request
        req = await mgr.create_request(
            task_id="t-int-1", group_id="g-int", agent_role="designer_web",
            instance_token="tok-int-1", req_type="approval",
            request_data={"summary": "Homepage mockup ready", "artifact_paths": ["/mockup.html"]},
        )
        assert req["status"] == "pending"

        # User approves via dashboard
        resolved = await mgr.resolve(req["id"], "approved", {"notes": "Looks great!"})
        assert resolved["status"] == "approved"

        # Agent polls and gets response
        status = await mgr.check_status(req["id"])
        assert status["status"] == "approved"
        assert status["response_data"]["notes"] == "Looks great!"

    @pytest.mark.asyncio
    async def test_clarification_flow(self, db_for_integration):
        """Agent asks question -> user responds -> agent gets answer."""
        mgr = InteractionManager(db_for_integration)
        req = await mgr.create_request(
            task_id="t-int-2", group_id="g-int", agent_role="coder_be",
            instance_token="tok-int-2", req_type="clarification",
            request_data={"question": "REST or GraphQL?", "suggested_options": ["REST", "GraphQL"]},
        )
        resolved = await mgr.resolve(req["id"], "responded", {"response": "REST"})
        status = await mgr.check_status(req["id"])
        assert status["status"] == "responded"
        assert status["response_data"]["response"] == "REST"

    @pytest.mark.asyncio
    async def test_first_run_unlock_all_instances(self, db_for_integration):
        """Approving one instance's first_run unlocks all instances of that role."""
        mgr = InteractionManager(db_for_integration)
        # Not approved yet
        assert await mgr.check_first_run("g-fr", "architect") is False
        # Approve
        await mgr.record_first_run("g-fr", "architect")
        # All instances should see it as approved
        assert await mgr.check_first_run("g-fr", "architect") is True
        # Different group is NOT approved
        assert await mgr.check_first_run("g-other", "architect") is False


# ---------------------------------------------------------------------------
# MCP Tools API tests (Tasks 32-33)
# ---------------------------------------------------------------------------


class TestMCPToolsAPI:
    """Test MCP tool endpoints."""

    @pytest.fixture
    async def mcp_app(self, db):
        from fastapi import FastAPI
        from taskbrew.dashboard.routers.mcp_tools import router as mcp_router, set_mcp_deps
        from taskbrew.config_loader import PipelineConfig, PipelineEdge

        mgr = InteractionManager(db)
        pipeline = PipelineConfig(
            id="test", start_agent="pm",
            edges=[
                PipelineEdge(id="e1", from_agent="pm", to_agent="architect", task_types=["tech_design"]),
                PipelineEdge(id="e2", from_agent="architect", to_agent="coder", task_types=["implementation"]),
            ],
        )
        # Seed groups and tasks needed by the endpoints
        for gid in ("g1",):
            await db.execute(
                "INSERT OR IGNORE INTO groups (id, title, status, created_at) VALUES (?, ?, ?, datetime('now'))",
                (gid, f"Group {gid}", "active"),
            )
        for tid in ("t1", "t2", "t5", "t6"):
            await db.execute(
                "INSERT OR IGNORE INTO tasks (id, title, status, created_at) VALUES (?, ?, ?, datetime('now'))",
                (tid, f"Task {tid}", "pending"),
            )
        set_mcp_deps(mgr, lambda: pipeline)
        app = FastAPI()
        app.include_router(mcp_router)
        yield app

    @pytest.fixture
    async def mcp_client(self, mcp_app):
        transport = ASGITransport(app=mcp_app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c

    @pytest.mark.asyncio
    async def test_complete_task_auto_approved(self, mcp_client):
        resp = await mcp_client.post("/mcp/tools/complete_task",
            json={"task_id": "t1", "group_id": "g1", "agent_role": "coder", "approval_mode": "auto", "summary": "done"},
            headers={"Authorization": "Bearer test-token"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "approved"

    @pytest.mark.asyncio
    async def test_complete_task_manual_creates_request(self, mcp_client):
        resp = await mcp_client.post("/mcp/tools/complete_task",
            json={"task_id": "t2", "group_id": "g1", "agent_role": "designer", "approval_mode": "manual", "summary": "mockup"},
            headers={"Authorization": "Bearer test-token"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "pending"
        assert "request_id" in resp.json()

    @pytest.mark.asyncio
    async def test_route_task_validates_edge(self, mcp_client):
        # Valid edge: pm -> architect
        resp = await mcp_client.post("/mcp/tools/route_task",
            json={"agent_role": "pm", "target_agent": "architect", "task_type": "tech_design", "title": "Design task"},
            headers={"Authorization": "Bearer test-token"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    @pytest.mark.asyncio
    async def test_route_task_rejects_invalid_edge(self, mcp_client):
        # Invalid edge: pm -> coder (no direct edge)
        resp = await mcp_client.post("/mcp/tools/route_task",
            json={"agent_role": "pm", "target_agent": "coder", "task_type": "implementation", "title": "Code task"},
            headers={"Authorization": "Bearer test-token"},
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_get_connections(self, mcp_client):
        resp = await mcp_client.post("/mcp/tools/get_my_connections",
            json={"agent_role": "pm"},
            headers={"Authorization": "Bearer test-token"},
        )
        assert resp.status_code == 200
        conns = resp.json()["connections"]
        assert len(conns) == 1
        assert conns[0]["target"] == "architect"

    @pytest.mark.asyncio
    async def test_request_clarification(self, mcp_client):
        resp = await mcp_client.post("/mcp/tools/request_clarification",
            json={"task_id": "t5", "group_id": "g1", "agent_role": "coder", "question": "REST or GraphQL?"},
            headers={"Authorization": "Bearer test-token"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "pending"

    @pytest.mark.asyncio
    async def test_no_auth_rejected(self, mcp_client):
        resp = await mcp_client.post("/mcp/tools/get_my_connections", json={"agent_role": "pm"})
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_poll_status(self, mcp_client):
        # Create a request first
        resp = await mcp_client.post("/mcp/tools/request_clarification",
            json={"task_id": "t6", "group_id": "g1", "agent_role": "pm", "question": "test?"},
            headers={"Authorization": "Bearer test-token"},
        )
        req_id = resp.json()["request_id"]
        # Poll
        resp = await mcp_client.get(f"/mcp/tools/poll/{req_id}", headers={"Authorization": "Bearer test-token"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "pending"
