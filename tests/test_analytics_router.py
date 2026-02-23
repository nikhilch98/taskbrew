"""Tests for agent performance analytics endpoints."""

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
    db = app_client["db"]
    group = await board.create_group(title="Test", origin="goal", created_by="pm")
    t1 = await board.create_task(
        group_id=group["id"], title="Implement feature",
        task_type="code", assigned_to="coder", priority="high",
    )
    t2 = await board.create_task(
        group_id=group["id"], title="Write tests",
        task_type="test", assigned_to="coder", priority="medium",
    )
    # Claim and complete first task
    await board.claim_task("coder", "coder-1")
    await board.complete_task(t1["id"])
    # Record usage
    await db.record_task_usage(
        task_id=t1["id"], agent_id="coder-1",
        input_tokens=500, output_tokens=200, cost_usd=0.03,
        duration_api_ms=5000, num_turns=3,
    )
    await db.record_task_usage(
        task_id=t2["id"], agent_id="coder-1",
        input_tokens=300, output_tokens=100, cost_usd=0.02,
        duration_api_ms=3000, num_turns=2,
    )
    return group, t1, t2


class TestAgentPerformanceSummary:
    async def test_empty(self, app_client):
        resp = await app_client["client"].get("/api/analytics/agents")
        assert resp.status_code == 200
        data = resp.json()
        assert data["agents"] == []

    async def test_with_data(self, app_client):
        await _seed(app_client)
        resp = await app_client["client"].get("/api/analytics/agents")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["agents"]) == 1
        agent = data["agents"][0]
        assert agent["agent_id"] == "coder-1"
        assert agent["total_runs"] == 2
        assert agent["total_cost"] == 0.05
        assert agent["tasks_completed"] >= 1

    async def test_days_filter(self, app_client):
        await _seed(app_client)
        resp = await app_client["client"].get("/api/analytics/agents?days=1")
        assert resp.status_code == 200


class TestAgentDetail:
    async def test_with_data(self, app_client):
        await _seed(app_client)
        resp = await app_client["client"].get("/api/analytics/agents/coder-1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["agent_id"] == "coder-1"
        assert len(data["daily_usage"]) >= 1
        assert len(data["recent_tasks"]) >= 1

    async def test_unknown_agent(self, app_client):
        resp = await app_client["client"].get("/api/analytics/agents/nonexistent")
        assert resp.status_code == 200
        data = resp.json()
        assert data["daily_usage"] == []


class TestThroughput:
    async def test_empty(self, app_client):
        resp = await app_client["client"].get("/api/analytics/throughput")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_completed"] == 0

    async def test_with_completions(self, app_client):
        await _seed(app_client)
        resp = await app_client["client"].get("/api/analytics/throughput?days=7")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_completed"] >= 1
        assert len(data["daily"]) >= 1


class TestEfficiency:
    async def test_empty(self, app_client):
        resp = await app_client["client"].get("/api/analytics/efficiency")
        assert resp.status_code == 200
        data = resp.json()
        assert data["overall"]["total_runs"] == 0

    async def test_with_data(self, app_client):
        await _seed(app_client)
        resp = await app_client["client"].get("/api/analytics/efficiency")
        assert resp.status_code == 200
        data = resp.json()
        assert data["overall"]["total_runs"] == 2
        assert data["overall"]["avg_cost"] > 0
