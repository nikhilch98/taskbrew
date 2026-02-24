"""Tests for the rewritten dashboard API endpoints."""

import pytest
from httpx import AsyncClient, ASGITransport

from ai_team.orchestrator.database import Database
from ai_team.orchestrator.task_board import TaskBoard
from ai_team.orchestrator.event_bus import EventBus
from ai_team.agents.instance_manager import InstanceManager


@pytest.fixture
async def app_client(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    await db.initialize()
    board = TaskBoard(db, group_prefixes={"pm": "FEAT", "architect": "DEBT"})
    await board.register_prefixes(
        {"pm": "PM", "architect": "AR", "coder": "CD", "tester": "TS", "reviewer": "RV"}
    )
    event_bus = EventBus()
    instance_mgr = InstanceManager(db)

    from ai_team.dashboard.app import create_app

    app = create_app(
        event_bus=event_bus,
        task_board=board,
        instance_manager=instance_mgr,
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield {"client": client, "board": board, "db": db, "event_bus": event_bus, "instance_mgr": instance_mgr}
    await db.close()


async def test_health(app_client):
    resp = await app_client["client"].get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_get_board_empty(app_client):
    resp = await app_client["client"].get("/api/board")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, dict)
    # Empty board should have no keys or empty lists
    for status_key in data:
        assert isinstance(data[status_key], list)


async def test_get_board_with_tasks(app_client):
    board = app_client["board"]
    group = await board.create_group(title="Test Feature", origin="pm", created_by="pm")
    await board.create_task(
        group_id=group["id"],
        title="Implement login",
        task_type="implement",
        assigned_to="coder",
        created_by="pm",
    )
    resp = await app_client["client"].get("/api/board")
    assert resp.status_code == 200
    data = resp.json()
    assert "pending" in data
    assert len(data["pending"]) == 1
    assert data["pending"][0]["title"] == "Implement login"


async def test_get_board_filtered(app_client):
    board = app_client["board"]
    group1 = await board.create_group(title="Feature A", origin="pm", created_by="pm")
    group2 = await board.create_group(title="Feature B", origin="pm", created_by="pm")
    await board.create_task(
        group_id=group1["id"],
        title="Task in group 1",
        task_type="implement",
        assigned_to="coder",
        created_by="pm",
    )
    await board.create_task(
        group_id=group2["id"],
        title="Task in group 2",
        task_type="implement",
        assigned_to="coder",
        created_by="pm",
    )
    resp = await app_client["client"].get(f"/api/board?group_id={group1['id']}")
    assert resp.status_code == 200
    data = resp.json()
    # Should only contain the task from group1
    all_tasks = []
    for tasks in data.values():
        all_tasks.extend(tasks)
    assert len(all_tasks) == 1
    assert all_tasks[0]["title"] == "Task in group 1"


async def test_get_groups(app_client):
    board = app_client["board"]
    await board.create_group(title="My Group", origin="pm", created_by="pm")
    resp = await app_client["client"].get("/api/groups")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["title"] == "My Group"


async def test_post_goal(app_client):
    resp = await app_client["client"].post(
        "/api/goals",
        json={"title": "Add authentication", "description": "OAuth2 flow"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "group_id" in data
    assert "task_id" in data


async def test_post_goal_no_title(app_client):
    resp = await app_client["client"].post("/api/goals", json={"description": "missing title"})
    assert resp.status_code == 400


async def test_get_agents(app_client):
    resp = await app_client["client"].get("/api/agents")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)


async def test_get_group_graph(app_client):
    board = app_client["board"]
    group = await board.create_group(title="Graph Test", origin="pm", created_by="pm")
    parent = await board.create_task(
        group_id=group["id"],
        title="Parent task",
        task_type="goal",
        assigned_to="pm",
        created_by="human",
    )
    child = await board.create_task(
        group_id=group["id"],
        title="Child task",
        task_type="implement",
        assigned_to="coder",
        created_by="pm",
        parent_id=parent["id"],
    )
    resp = await app_client["client"].get(f"/api/groups/{group['id']}/graph")
    assert resp.status_code == 200
    data = resp.json()
    assert "nodes" in data
    assert "edges" in data
    assert len(data["nodes"]) == 2
    # Should have a parent edge from parent to child
    parent_edges = [e for e in data["edges"] if e["type"] == "parent"]
    assert len(parent_edges) == 1
    assert parent_edges[0]["from"] == parent["id"]
    assert parent_edges[0]["to"] == child["id"]


async def test_get_group_graph_with_dependencies(app_client):
    board = app_client["board"]
    group = await board.create_group(title="Dep Graph Test", origin="pm", created_by="pm")
    task_a = await board.create_task(
        group_id=group["id"],
        title="Task A",
        task_type="implement",
        assigned_to="coder",
        created_by="pm",
    )
    task_b = await board.create_task(
        group_id=group["id"],
        title="Task B",
        task_type="test",
        assigned_to="tester",
        created_by="pm",
        blocked_by=[task_a["id"]],
    )
    resp = await app_client["client"].get(f"/api/groups/{group['id']}/graph")
    assert resp.status_code == 200
    data = resp.json()
    blocked_edges = [e for e in data["edges"] if e["type"] == "blocked_by"]
    assert len(blocked_edges) == 1
    assert blocked_edges[0]["from"] == task_a["id"]
    assert blocked_edges[0]["to"] == task_b["id"]


async def test_get_board_filters(app_client):
    resp = await app_client["client"].get("/api/board/filters")
    assert resp.status_code == 200
    data = resp.json()
    assert "groups" in data
    assert "roles" in data
    assert "statuses" in data
    assert "priorities" in data
    assert "blocked" in data["statuses"]
    assert "pending" in data["statuses"]
    assert "critical" in data["priorities"]
    assert "low" in data["priorities"]
