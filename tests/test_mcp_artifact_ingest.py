"""Tests for /mcp/tools/complete_task artifact_paths ingestion.

Fix scope: artifact_paths declared by an agent on complete_task is
now copied into the artifact_store under <group>/<task>/<basename>
so the dashboard's artifact viewer can find them after the worktree
resets between tasks. Previously the list was silently dropped in
the auto-approval path.
"""

import os
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from taskbrew.orchestrator.database import Database
from taskbrew.orchestrator.event_bus import EventBus
from taskbrew.orchestrator.task_board import TaskBoard


@pytest.fixture
async def mcp_env(tmp_path):
    """FastAPI app with mcp_tools wired against a real Database, real
    TaskBoard, real ArtifactStore on disk, and a mocked WorktreeManager
    that points at an actual directory under tmp_path."""
    db = Database(":memory:")
    await db.initialize()
    event_bus = EventBus()
    board = TaskBoard(db, event_bus=event_bus)

    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    artifacts_dir = project_dir / "artifacts"
    artifacts_dir.mkdir()

    # Real worktree directory the agent "writes" to.
    worktree_path = project_dir / ".worktrees" / "coder-1"
    worktree_path.mkdir(parents=True)

    # Mock orchestrator that exposes just the surface mcp_tools cares
    # about: project_dir, team_config, artifact_store, worktree_manager.
    orch = MagicMock()
    orch.project_dir = str(project_dir)
    orch.team_config = MagicMock()
    orch.team_config.artifacts_base_dir = "artifacts"
    from taskbrew.orchestrator.artifact_store import ArtifactStore
    orch.artifact_store = ArtifactStore(base_dir=str(artifacts_dir))
    orch.worktree_manager = MagicMock()
    orch.worktree_manager.get_worktree_path = (
        lambda agent_id: str(worktree_path)
        if agent_id == "coder-1"
        else None
    )

    group = await board.create_group(
        title="G", origin="pm", created_by="human",
    )
    task = await board.create_task(
        group_id=group["id"], title="Impl",
        task_type="bug_fix", assigned_to="coder", created_by="human",
    )
    # Mark in_progress + claimed_by so complete_task_with_output finds it.
    await db.execute(
        "UPDATE tasks SET status = 'in_progress', claimed_by = 'coder-1' "
        "WHERE id = ?",
        (task["id"],),
    )

    from taskbrew.dashboard.routers.mcp_tools import router, set_mcp_deps
    set_mcp_deps(
        interaction_mgr=None,
        pipeline_getter=None,
        task_board=board,
        auth_manager=None,
        event_bus=event_bus,
        orchestrator_getter=lambda: orch,
    )
    app = FastAPI()
    app.include_router(router)
    yield {
        "app": app,
        "board": board,
        "orch": orch,
        "worktree_path": worktree_path,
        "artifacts_dir": artifacts_dir,
        "group_id": group["id"],
        "task_id": task["id"],
    }
    await db.close()


@pytest.fixture
async def mcp_client(mcp_env):
    transport = ASGITransport(app=mcp_env["app"])
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c, mcp_env


async def test_complete_task_ingests_artifact_paths(mcp_client):
    """Agent declares artifact_paths; files are copied from worktree into
    artifact_store under <group>/<task>/<basename>, so the dashboard's
    artifact viewer finds them after the worktree resets."""
    c, env = mcp_client
    # Agent's worktree contains the artifacts.
    (env["worktree_path"] / "impl_summary.md").write_text(
        "# Implementation summary\nDid the thing.\n"
    )
    (env["worktree_path"] / "change_notes.md").write_text(
        "changed: foo.py, bar.py\n"
    )

    resp = await c.post(
        "/mcp/tools/complete_task",
        json={
            "task_id": env["task_id"],
            "group_id": env["group_id"],
            "agent_role": "coder",
            "approval_mode": "auto",
            "summary": "Done",
            "artifact_paths": ["impl_summary.md", "change_notes.md"],
        },
        headers={"Authorization": "Bearer test-token"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "approved"
    assert sorted(data["ingested_artifacts"]) == [
        "change_notes.md", "impl_summary.md",
    ]

    # Files actually live in the artifact_store now.
    dest = (
        env["artifacts_dir"] / env["group_id"] / env["task_id"]
    )
    assert (dest / "impl_summary.md").exists()
    assert (dest / "change_notes.md").exists()
    assert "# Implementation summary" in (
        dest / "impl_summary.md"
    ).read_text()


async def test_complete_task_rejects_path_outside_worktree(mcp_client):
    """An agent that tries to declare ../../etc/passwd as an artifact
    must NOT be allowed to ingest it. Path is silently rejected; the
    rest of complete_task still succeeds."""
    c, env = mcp_client
    # Sneaky relative-traversal path. Even though no such real file
    # exists in the test, the rejection should happen at the path-shape
    # check before any filesystem read.
    resp = await c.post(
        "/mcp/tools/complete_task",
        json={
            "task_id": env["task_id"],
            "group_id": env["group_id"],
            "agent_role": "coder",
            "approval_mode": "auto",
            "summary": "Done",
            "artifact_paths": ["../../etc/passwd"],
        },
        headers={"Authorization": "Bearer test-token"},
    )
    assert resp.status_code == 200
    assert resp.json()["ingested_artifacts"] == []


async def test_complete_task_rejects_absolute_path(mcp_client):
    c, env = mcp_client
    resp = await c.post(
        "/mcp/tools/complete_task",
        json={
            "task_id": env["task_id"],
            "group_id": env["group_id"],
            "agent_role": "coder",
            "approval_mode": "auto",
            "summary": "Done",
            "artifact_paths": ["/etc/passwd"],
        },
        headers={"Authorization": "Bearer test-token"},
    )
    assert resp.status_code == 200
    assert resp.json()["ingested_artifacts"] == []


async def test_complete_task_skips_missing_files_quietly(mcp_client):
    """If an agent declares a path that doesn't exist, ingestion just
    skips it; complete_task still succeeds."""
    c, env = mcp_client
    resp = await c.post(
        "/mcp/tools/complete_task",
        json={
            "task_id": env["task_id"],
            "group_id": env["group_id"],
            "agent_role": "coder",
            "approval_mode": "auto",
            "summary": "Done",
            "artifact_paths": ["does-not-exist.md"],
        },
        headers={"Authorization": "Bearer test-token"},
    )
    assert resp.status_code == 200
    assert resp.json()["ingested_artifacts"] == []


async def test_complete_task_with_no_artifact_paths_still_works(mcp_client):
    """Regression: complete_task without artifact_paths is the common
    case and must still succeed (back-compat)."""
    c, env = mcp_client
    resp = await c.post(
        "/mcp/tools/complete_task",
        json={
            "task_id": env["task_id"],
            "group_id": env["group_id"],
            "agent_role": "coder",
            "approval_mode": "auto",
            "summary": "Done",
        },
        headers={"Authorization": "Bearer test-token"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "approved"
    assert resp.json()["ingested_artifacts"] == []
