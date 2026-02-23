"""Tests for pipeline execution visualizer endpoints."""

import json

import pytest
from httpx import AsyncClient, ASGITransport

from taskbrew.orchestrator.database import Database
from taskbrew.orchestrator.task_board import TaskBoard
from taskbrew.orchestrator.event_bus import EventBus
from taskbrew.agents.instance_manager import InstanceManager


@pytest.fixture
async def app_client(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    await db.initialize()
    board = TaskBoard(db, group_prefixes={"pm": "FEAT"})
    await board.register_prefixes({"pm": "PM", "coder": "CD"})
    event_bus = EventBus()
    instance_mgr = InstanceManager(db)

    from taskbrew.dashboard.app import create_app

    app = create_app(
        event_bus=event_bus,
        task_board=board,
        instance_manager=instance_mgr,
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield {"client": client, "board": board, "db": db}
    await db.close()


async def _seed(app_client):
    board = app_client["board"]
    g = await board.create_group(title="Login Feature", origin="goal", created_by="pm")
    t1 = await board.create_task(
        group_id=g["id"], title="Design API",
        task_type="design", assigned_to="pm", priority="high",
    )
    t2 = await board.create_task(
        group_id=g["id"], title="Implement API",
        task_type="code", assigned_to="coder", priority="high",
        blocked_by=[t1["id"]],
    )
    t3 = await board.create_task(
        group_id=g["id"], title="Write tests",
        task_type="test", assigned_to="coder", priority="medium",
        blocked_by=[t2["id"]],
    )
    return g, t1, t2, t3


class TestWorkflowsList:
    async def test_empty(self, app_client):
        resp = await app_client["client"].get("/api/pipelines/workflows")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 0

    async def test_with_workflow(self, app_client):
        db = app_client["db"]
        await db.execute(
            "INSERT INTO workflow_definitions (id, name, description, steps, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("wf1", "Deploy", "Deploy pipeline", json.dumps([
                {"role": "coder", "task_type": "code"},
                {"role": "coder", "task_type": "test"},
            ]), "2026-02-26T00:00:00Z"),
        )
        resp = await app_client["client"].get("/api/pipelines/workflows")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["workflows"][0]["steps_count"] == 2


class TestPipelineGroups:
    async def test_empty(self, app_client):
        resp = await app_client["client"].get("/api/pipelines/groups")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 0

    async def test_with_group(self, app_client):
        g, t1, t2, t3 = await _seed(app_client)
        resp = await app_client["client"].get("/api/pipelines/groups")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        group = data["groups"][0]
        assert group["tasks"]["total"] == 3
        assert group["progress"] == 0.0  # Nothing completed yet

    async def test_with_progress(self, app_client):
        board = app_client["board"]
        g, t1, t2, t3 = await _seed(app_client)
        # Complete first task
        await board.claim_task("pm", "pm-1")
        await board.complete_task(t1["id"])
        resp = await app_client["client"].get("/api/pipelines/groups")
        data = resp.json()
        group = data["groups"][0]
        assert group["tasks"]["completed"] == 1
        assert group["progress"] == pytest.approx(33.3, abs=0.1)

    async def test_filter_by_status(self, app_client):
        await _seed(app_client)
        resp = await app_client["client"].get("/api/pipelines/groups?status=active")
        assert resp.status_code == 200
        assert resp.json()["count"] == 1

        resp = await app_client["client"].get("/api/pipelines/groups?status=completed")
        assert resp.json()["count"] == 0


class TestPipelineDetail:
    async def test_not_found(self, app_client):
        resp = await app_client["client"].get("/api/pipelines/groups/nonexistent")
        assert resp.status_code == 404

    async def test_detail(self, app_client):
        g, t1, t2, t3 = await _seed(app_client)
        resp = await app_client["client"].get(f"/api/pipelines/groups/{g['id']}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_tasks"] == 3
        assert len(data["nodes"]) == 3
        # t2 blocked by t1, t3 blocked by t2
        assert len(data["edges"]) == 2
        # Check edges direction
        edge_pairs = [(e["from"], e["to"]) for e in data["edges"]]
        assert (t1["id"], t2["id"]) in edge_pairs
        assert (t2["id"], t3["id"]) in edge_pairs


class TestPipelineHistory:
    async def test_empty(self, app_client):
        resp = await app_client["client"].get("/api/pipelines/history")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_completed"] == 0
