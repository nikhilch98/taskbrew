# tests/test_dashboard.py
import pytest
from httpx import AsyncClient, ASGITransport
from ai_team.dashboard.app import create_app
from ai_team.orchestrator.event_bus import EventBus
from ai_team.orchestrator.team_manager import TeamManager
from ai_team.orchestrator.task_queue import TaskQueue
from ai_team.orchestrator.workflow import WorkflowEngine

@pytest.fixture
async def app_deps(tmp_path):
    event_bus = EventBus()
    team_mgr = TeamManager(event_bus=event_bus)
    task_queue = TaskQueue(db_path=tmp_path / "test.db")
    await task_queue.initialize()
    workflow = WorkflowEngine()
    return event_bus, team_mgr, task_queue, workflow

@pytest.fixture
async def app(app_deps):
    event_bus, team_mgr, task_queue, workflow = app_deps
    return create_app(event_bus=event_bus, team_manager=team_mgr, task_queue=task_queue, workflow_engine=workflow)

@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

async def test_health_endpoint(client):
    resp = await client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"

async def test_team_status_endpoint(client):
    resp = await client.get("/api/team")
    assert resp.status_code == 200
    assert isinstance(resp.json(), dict)

async def test_tasks_endpoint(app_deps, client):
    _, _, task_queue, _ = app_deps
    await task_queue.create_task(pipeline_id="p1", task_type="implement", input_context="Build auth")
    resp = await client.get("/api/tasks")
    assert resp.status_code == 200
    tasks = resp.json()
    assert len(tasks) >= 1

async def test_pipelines_endpoint(client):
    resp = await client.get("/api/pipelines")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)
