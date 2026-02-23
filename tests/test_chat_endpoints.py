# tests/test_chat_endpoints.py
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
async def client_no_chat(app_deps):
    """Client without chat_manager (backward compat)."""
    event_bus, team_mgr, task_queue, workflow = app_deps
    app = create_app(event_bus=event_bus, team_manager=team_mgr, task_queue=task_queue, workflow_engine=workflow)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def client_with_chat(app_deps):
    """Client with a ChatManager."""
    from unittest.mock import AsyncMock, MagicMock
    from ai_team.dashboard.chat_manager import ChatManager
    event_bus, team_mgr, task_queue, workflow = app_deps
    chat_mgr = ChatManager()
    app = create_app(event_bus=event_bus, team_manager=team_mgr, task_queue=task_queue, workflow_engine=workflow, chat_manager=chat_mgr)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_backward_compat_no_chat_manager(client_no_chat):
    """create_app without chat_manager still works."""
    resp = await client_no_chat.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


async def test_chat_sessions_endpoint_empty(client_with_chat):
    """GET /api/chat/sessions returns empty dict when no sessions."""
    resp = await client_with_chat.get("/api/chat/sessions")
    assert resp.status_code == 200
    assert resp.json() == {}


async def test_chat_history_not_found(client_with_chat):
    """GET /api/chat/{agent}/history returns 404 when no session."""
    resp = await client_with_chat.get("/api/chat/pm/history")
    assert resp.status_code == 404


async def test_delete_chat_not_found(client_with_chat):
    """DELETE /api/chat/{agent} returns 404 when no session."""
    resp = await client_with_chat.delete("/api/chat/pm")
    assert resp.status_code == 404
