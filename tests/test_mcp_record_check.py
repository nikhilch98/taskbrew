"""Tests for the record_check MCP tool.

Design: docs/superpowers/specs/2026-04-24-per-task-completion-checks-design.md
"""

import json

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from taskbrew.orchestrator.database import Database
from taskbrew.orchestrator.task_board import TaskBoard
from taskbrew.orchestrator.event_bus import EventBus


@pytest.fixture
async def mcp_app():
    """FastAPI app with the mcp_tools router wired up against an in-memory DB."""
    db = Database(":memory:")
    await db.initialize()
    board = TaskBoard(db)
    event_bus = EventBus()

    group = await board.create_group(
        title="G", origin="pm", created_by="human",
    )
    task = await board.create_task(
        group_id=group["id"], title="Impl",
        task_type="bug_fix", assigned_to="coder", created_by="human",
    )

    from taskbrew.dashboard.routers.mcp_tools import router, set_mcp_deps
    set_mcp_deps(
        interaction_mgr=None,
        pipeline_getter=None,
        task_board=board,
        auth_manager=None,
        event_bus=event_bus,
    )
    app = FastAPI()
    app.include_router(router)
    yield app, board, task["id"]
    await db.close()


@pytest.fixture
async def client(mcp_app):
    app, board, task_id = mcp_app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c, board, task_id


async def test_record_check_writes_entry(client):
    c, board, task_id = client
    resp = await c.post(
        "/mcp/tools/record_check",
        json={
            "task_id": task_id,
            "check_name": "build",
            "status": "pass",
            "command": "npm run build",
            "duration_ms": 2300,
        },
        headers={"Authorization": "Bearer test-token"},
    )
    assert resp.status_code == 200

    row = await board.get_task(task_id)
    checks = json.loads(row["completion_checks"])
    assert checks == {
        "build": {
            "status": "pass",
            "command": "npm run build",
            "duration_ms": 2300,
        },
    }


async def test_record_check_overwrites_same_name(client):
    """Calling record_check twice with the same check_name overwrites the
    prior entry so re-running a check after a fix is idempotent."""
    c, board, task_id = client
    # First call: fail
    await c.post(
        "/mcp/tools/record_check",
        json={"task_id": task_id, "check_name": "tests",
              "status": "fail", "details": "3 failed"},
        headers={"Authorization": "Bearer test-token"},
    )
    # Second call: pass after fix
    await c.post(
        "/mcp/tools/record_check",
        json={"task_id": task_id, "check_name": "tests",
              "status": "pass", "details": "42 passed"},
        headers={"Authorization": "Bearer test-token"},
    )

    row = await board.get_task(task_id)
    checks = json.loads(row["completion_checks"])
    assert checks["tests"] == {"status": "pass", "details": "42 passed"}


async def test_record_check_preserves_other_names(client):
    """Writing one check_name must not clobber a previously-recorded one
    with a different name -- the JSON merge is keyed per check."""
    c, board, task_id = client
    await c.post(
        "/mcp/tools/record_check",
        json={"task_id": task_id, "check_name": "build", "status": "pass"},
        headers={"Authorization": "Bearer test-token"},
    )
    await c.post(
        "/mcp/tools/record_check",
        json={"task_id": task_id, "check_name": "lint",
              "status": "skipped", "details": "no linter configured"},
        headers={"Authorization": "Bearer test-token"},
    )

    row = await board.get_task(task_id)
    checks = json.loads(row["completion_checks"])
    assert set(checks.keys()) == {"build", "lint"}
    assert checks["build"]["status"] == "pass"
    assert checks["lint"]["status"] == "skipped"


async def test_record_check_rejects_bad_status(client):
    c, _board, task_id = client
    resp = await c.post(
        "/mcp/tools/record_check",
        json={"task_id": task_id, "check_name": "build", "status": "maybe"},
        headers={"Authorization": "Bearer test-token"},
    )
    assert resp.status_code == 400


async def test_record_check_rejects_empty_check_name(client):
    c, _board, task_id = client
    resp = await c.post(
        "/mcp/tools/record_check",
        json={"task_id": task_id, "check_name": "", "status": "pass"},
        headers={"Authorization": "Bearer test-token"},
    )
    assert resp.status_code == 400


async def test_record_check_rejects_unknown_task(client):
    c, _board, _task_id = client
    resp = await c.post(
        "/mcp/tools/record_check",
        json={"task_id": "does-not-exist", "check_name": "build",
              "status": "pass"},
        headers={"Authorization": "Bearer test-token"},
    )
    assert resp.status_code == 404


async def test_record_check_stores_artifact_paths(client):
    """Structured failure feedback: artifact_paths is an optional list of
    paths where the agent saved full stderr / logs. Retry context will
    render them as pointers so the next attempt reads the actual output
    rather than acting on the summarised details string."""
    c, board, task_id = client
    resp = await c.post(
        "/mcp/tools/record_check",
        json={
            "task_id": task_id,
            "check_name": "tests",
            "status": "fail",
            "details": "3 tests failed",
            "artifact_paths": [
                "artifacts/T-1-tests.log",
                "artifacts/T-1-tests.stderr",
            ],
        },
        headers={"Authorization": "Bearer test-token"},
    )
    assert resp.status_code == 200

    row = await board.get_task(task_id)
    checks = json.loads(row["completion_checks"])
    assert checks["tests"]["artifact_paths"] == [
        "artifacts/T-1-tests.log",
        "artifacts/T-1-tests.stderr",
    ]


async def test_record_check_rejects_non_list_artifact_paths(client):
    c, _board, task_id = client
    resp = await c.post(
        "/mcp/tools/record_check",
        json={
            "task_id": task_id, "check_name": "build", "status": "pass",
            "artifact_paths": "single/string/not/list.log",
        },
        headers={"Authorization": "Bearer test-token"},
    )
    assert resp.status_code == 400


async def test_record_check_rejects_empty_artifact_path(client):
    c, _board, task_id = client
    resp = await c.post(
        "/mcp/tools/record_check",
        json={
            "task_id": task_id, "check_name": "build", "status": "pass",
            "artifact_paths": [""],
        },
        headers={"Authorization": "Bearer test-token"},
    )
    assert resp.status_code == 400


async def test_record_check_rejects_oversize_artifact_path(client):
    c, _board, task_id = client
    resp = await c.post(
        "/mcp/tools/record_check",
        json={
            "task_id": task_id, "check_name": "build", "status": "pass",
            "artifact_paths": ["a" * 3000],
        },
        headers={"Authorization": "Bearer test-token"},
    )
    assert resp.status_code == 400
