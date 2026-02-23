"""Tests for export/reporting and global search endpoints."""

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


async def _seed_data(app_client):
    """Create sample groups and tasks for testing."""
    board = app_client["board"]
    group = await board.create_group(title="Auth Feature", origin="goal", created_by="pm")
    t1 = await board.create_task(
        group_id=group["id"], title="Implement login",
        task_type="code", assigned_to="coder", priority="high",
        description="Build the login endpoint",
    )
    t2 = await board.create_task(
        group_id=group["id"], title="Write tests for auth",
        task_type="test", assigned_to="coder", priority="medium",
        description="Unit tests for authentication module",
    )
    return group, t1, t2


# ------------------------------------------------------------------
# Export endpoints
# ------------------------------------------------------------------


class TestExportFull:
    async def test_export_full_json(self, app_client):
        await _seed_data(app_client)
        resp = await app_client["client"].get("/api/export/full")
        assert resp.status_code == 200
        data = json.loads(resp.text)
        assert "tasks" in data
        assert "groups" in data
        assert "summary" in data
        assert data["summary"]["total_tasks"] == 2
        assert data["summary"]["total_groups"] == 1

    async def test_export_full_csv(self, app_client):
        await _seed_data(app_client)
        resp = await app_client["client"].get("/api/export/full?format=csv")
        assert resp.status_code == 200
        assert "text/csv" in resp.headers["content-type"]
        assert "Implement login" in resp.text

    async def test_export_full_empty(self, app_client):
        resp = await app_client["client"].get("/api/export/full")
        assert resp.status_code == 200
        data = json.loads(resp.text)
        assert data["summary"]["total_tasks"] == 0


class TestExportTasks:
    async def test_export_tasks_json(self, app_client):
        await _seed_data(app_client)
        resp = await app_client["client"].get("/api/export/tasks")
        assert resp.status_code == 200
        data = json.loads(resp.text)
        assert data["count"] == 2

    async def test_export_tasks_filter_priority(self, app_client):
        await _seed_data(app_client)
        resp = await app_client["client"].get("/api/export/tasks?priority=high")
        assert resp.status_code == 200
        data = json.loads(resp.text)
        assert data["count"] == 1
        assert data["tasks"][0]["title"] == "Implement login"

    async def test_export_tasks_filter_status(self, app_client):
        await _seed_data(app_client)
        resp = await app_client["client"].get("/api/export/tasks?status=pending")
        assert resp.status_code == 200
        data = json.loads(resp.text)
        assert data["count"] == 2

    async def test_export_tasks_csv(self, app_client):
        await _seed_data(app_client)
        resp = await app_client["client"].get("/api/export/tasks?format=csv")
        assert resp.status_code == 200
        assert "text/csv" in resp.headers["content-type"]


class TestExportUsage:
    async def test_export_usage_empty(self, app_client):
        resp = await app_client["client"].get("/api/export/usage")
        assert resp.status_code == 200
        data = json.loads(resp.text)
        assert data["count"] == 0

    async def test_export_usage_with_data(self, app_client):
        _, t1, _ = await _seed_data(app_client)
        db = app_client["db"]
        await db.record_task_usage(
            task_id=t1["id"], agent_id="coder-1",
            input_tokens=100, output_tokens=50, cost_usd=0.01,
        )
        resp = await app_client["client"].get("/api/export/usage")
        assert resp.status_code == 200
        data = json.loads(resp.text)
        assert data["count"] == 1


# ------------------------------------------------------------------
# Report endpoints
# ------------------------------------------------------------------


class TestSummaryReport:
    async def test_summary_empty(self, app_client):
        resp = await app_client["client"].get("/api/reports/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert "tasks" in data
        assert data["tasks"]["total"] == 0

    async def test_summary_with_data(self, app_client):
        await _seed_data(app_client)
        resp = await app_client["client"].get("/api/reports/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert data["tasks"]["total"] == 2
        assert data["tasks"]["by_status"]["pending"] == 2
        assert data["tasks"]["by_priority"]["high"] == 1


class TestVelocityReport:
    async def test_velocity_empty(self, app_client):
        resp = await app_client["client"].get("/api/reports/velocity")
        assert resp.status_code == 200
        data = resp.json()
        assert data["velocity"] == []
        assert data["average_per_day"] == 0.0

    async def test_velocity_with_completions(self, app_client):
        _, t1, _ = await _seed_data(app_client)
        board = app_client["board"]
        await board.claim_task("coder", "coder-1")
        await board.complete_task(t1["id"])
        resp = await app_client["client"].get("/api/reports/velocity?days=7")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["velocity"]) >= 1
        assert data["velocity"][0]["completed"] >= 1


class TestCostReport:
    async def test_cost_report_empty(self, app_client):
        resp = await app_client["client"].get("/api/reports/cost")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_cost"] == 0

    async def test_cost_report_with_data(self, app_client):
        _, t1, _ = await _seed_data(app_client)
        db = app_client["db"]
        await db.record_task_usage(
            task_id=t1["id"], agent_id="coder-1",
            input_tokens=1000, output_tokens=500, cost_usd=0.05,
        )
        resp = await app_client["client"].get("/api/reports/cost?days=7")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_cost"] > 0
        assert len(data["by_agent"]) == 1
        assert data["by_agent"][0]["agent_id"] == "coder-1"


# ------------------------------------------------------------------
# Global Search
# ------------------------------------------------------------------


class TestGlobalSearch:
    async def test_search_tasks(self, app_client):
        await _seed_data(app_client)
        resp = await app_client["client"].get("/api/search?q=login")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1
        assert any(t["title"] == "Implement login" for t in data["results"].get("tasks", []))

    async def test_search_by_description(self, app_client):
        await _seed_data(app_client)
        resp = await app_client["client"].get("/api/search?q=authentication")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1

    async def test_search_groups(self, app_client):
        await _seed_data(app_client)
        resp = await app_client["client"].get("/api/search?q=Auth+Feature")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["results"].get("groups", [])) >= 1

    async def test_search_filter_entity(self, app_client):
        await _seed_data(app_client)
        resp = await app_client["client"].get("/api/search?q=login&entity=tasks")
        assert resp.status_code == 200
        data = resp.json()
        assert "tasks" in data["results"]
        assert "groups" not in data["results"]

    async def test_search_no_results(self, app_client):
        resp = await app_client["client"].get("/api/search?q=nonexistent_xyz_query")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0

    async def test_search_requires_query(self, app_client):
        resp = await app_client["client"].get("/api/search")
        assert resp.status_code == 422  # Missing required parameter

    async def test_search_by_task_id(self, app_client):
        _, t1, _ = await _seed_data(app_client)
        resp = await app_client["client"].get(f"/api/search?q={t1['id']}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1
