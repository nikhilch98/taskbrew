"""Tests for the cross-project comparison API endpoints."""

import pytest
import yaml
from httpx import AsyncClient, ASGITransport

from taskbrew.orchestrator.database import Database
from taskbrew.project_manager import ProjectManager


async def _scaffold_project(tmp_path, name, slug, *, tasks=None, usage=None, agents=None, groups=None):
    """Create a project directory with config/team.yaml and a populated database.

    Parameters
    ----------
    tasks : list[dict] | None
        Each dict has keys: title, status, and optionally completed_at.
    usage : list[dict] | None
        Each dict has keys: task_id, agent_id, cost_usd.
    agents : list[dict] | None
        Each dict has keys: instance_id, role, status.
    groups : list[dict] | None
        Each dict has keys: id, title.
    """
    project_dir = tmp_path / slug
    config_dir = project_dir / "config"
    data_dir = project_dir / "data"
    config_dir.mkdir(parents=True)
    data_dir.mkdir(parents=True)

    # Write team.yaml
    team_yaml = config_dir / "team.yaml"
    team_yaml.write_text(yaml.dump({
        "team_name": name,
        "database": {"path": "data/taskbrew.db"},
        "dashboard": {"host": "0.0.0.0", "port": 8420},
        "artifacts": {"base_dir": "artifacts"},
        "defaults": {"max_instances": 1, "poll_interval_seconds": 5, "idle_timeout_minutes": 30},
    }))

    # Create and populate database
    db_path = str(data_dir / "taskbrew.db")
    db = Database(db_path)
    await db.initialize()

    now = "2026-02-26T00:00:00+00:00"

    if groups:
        for g in groups:
            await db.execute(
                "INSERT INTO groups (id, title, origin, status, created_at) VALUES (?, ?, 'pm', 'active', ?)",
                (g["id"], g["title"], now),
            )

    if tasks:
        group_id = (groups[0]["id"] if groups else "GRP-001")
        # Ensure at least one group exists for FK
        if not groups:
            await db.execute(
                "INSERT INTO groups (id, title, origin, status, created_at) VALUES (?, ?, 'pm', 'active', ?)",
                (group_id, "Default Group", now),
            )
        for i, t in enumerate(tasks):
            task_id = t.get("id", f"TSK-{i+1:03d}")
            await db.execute(
                "INSERT INTO tasks (id, group_id, title, status, assigned_to, created_at, completed_at) "
                "VALUES (?, ?, ?, ?, 'coder', ?, ?)",
                (task_id, group_id, t["title"], t["status"], now, t.get("completed_at")),
            )

    if usage:
        for u in usage:
            await db.execute(
                "INSERT INTO task_usage (task_id, agent_id, cost_usd, recorded_at) "
                "VALUES (?, ?, ?, ?)",
                (u["task_id"], u["agent_id"], u["cost_usd"], now),
            )

    if agents:
        for a in agents:
            await db.execute(
                "INSERT INTO agent_instances (instance_id, role, status, started_at) "
                "VALUES (?, ?, ?, ?)",
                (a["instance_id"], a["role"], a["status"], now),
            )

    await db.close()
    return str(project_dir)


@pytest.fixture
async def comparison_client(tmp_path):
    """Create an app with a ProjectManager that has two test projects."""
    registry_path = tmp_path / "projects.yaml"
    pm = ProjectManager(registry_path=registry_path)

    # Project Alpha: 3 tasks (2 completed, 1 pending), cost $1.50, 1 active agent, 2 groups
    dir_alpha = await _scaffold_project(
        tmp_path, "Alpha", "alpha",
        groups=[{"id": "GRP-A1", "title": "Feature A"}, {"id": "GRP-A2", "title": "Feature B"}],
        tasks=[
            {"id": "TSK-A01", "title": "Task A1", "status": "completed", "completed_at": "2026-02-25T12:00:00+00:00"},
            {"id": "TSK-A02", "title": "Task A2", "status": "completed", "completed_at": "2026-02-24T12:00:00+00:00"},
            {"id": "TSK-A03", "title": "Task A3", "status": "pending"},
        ],
        usage=[
            {"task_id": "TSK-A01", "agent_id": "coder-1", "cost_usd": 1.0},
            {"task_id": "TSK-A02", "agent_id": "coder-1", "cost_usd": 0.5},
        ],
        agents=[
            {"instance_id": "coder-1", "role": "coder", "status": "busy"},
        ],
    )

    # Project Beta: 1 task (0 completed), cost $0, 0 active agents, 1 group
    dir_beta = await _scaffold_project(
        tmp_path, "Beta", "beta",
        groups=[{"id": "GRP-B1", "title": "Feature X"}],
        tasks=[
            {"id": "TSK-B01", "title": "Task B1", "status": "pending"},
        ],
    )

    # Register projects in the registry (bypass scaffolding since dirs already exist)
    from datetime import datetime, timezone
    registry_data = {
        "projects": [
            {"id": "alpha", "name": "Alpha", "directory": dir_alpha, "created_at": datetime.now(timezone.utc).isoformat()},
            {"id": "beta", "name": "Beta", "directory": dir_beta, "created_at": datetime.now(timezone.utc).isoformat()},
        ],
        "active_project": None,
    }
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(yaml.dump(registry_data))

    # Create app with project_manager (no active orchestrator needed)
    from taskbrew.dashboard.app import create_app
    app = create_app(project_manager=pm)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield {"client": client, "pm": pm}


# ------------------------------------------------------------------
# GET /api/comparison/projects
# ------------------------------------------------------------------


async def test_comparison_projects_lists_all(comparison_client):
    resp = await comparison_client["client"].get("/api/comparison/projects")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2

    # Find Alpha and Beta
    alpha = next(p for p in data if p["id"] == "alpha")
    beta = next(p for p in data if p["id"] == "beta")

    # Alpha stats
    assert alpha["name"] == "Alpha"
    assert alpha["total_tasks"] == 3
    assert alpha["completed_tasks"] == 2
    assert alpha["completion_rate"] == pytest.approx(66.7, abs=0.1)
    assert alpha["total_cost"] == pytest.approx(1.5, abs=0.01)
    assert alpha["active_agents"] == 1
    assert alpha["groups_count"] == 2

    # Beta stats
    assert beta["name"] == "Beta"
    assert beta["total_tasks"] == 1
    assert beta["completed_tasks"] == 0
    assert beta["completion_rate"] == 0.0
    assert beta["total_cost"] == 0.0
    assert beta["active_agents"] == 0
    assert beta["groups_count"] == 1


async def test_comparison_projects_empty_when_no_manager(tmp_path):
    """With no project_manager, should return empty list."""
    from taskbrew.dashboard.app import create_app
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/comparison/projects")
        assert resp.status_code == 200
        assert resp.json() == []


async def test_comparison_projects_includes_metadata(comparison_client):
    resp = await comparison_client["client"].get("/api/comparison/projects")
    data = resp.json()
    for project in data:
        assert "id" in project
        assert "name" in project
        assert "directory" in project
        assert "created_at" in project


# ------------------------------------------------------------------
# GET /api/comparison/metrics
# ------------------------------------------------------------------


async def test_comparison_metrics_all(comparison_client):
    resp = await comparison_client["client"].get("/api/comparison/metrics")
    assert resp.status_code == 200
    data = resp.json()
    assert "projects" in data
    assert len(data["projects"]) == 2

    alpha = next(p for p in data["projects"] if p["id"] == "alpha")
    beta = next(p for p in data["projects"] if p["id"] == "beta")

    # Alpha metrics
    assert alpha["tasks_completed"] == 2
    assert alpha["tasks_in_progress"] == 0
    assert alpha["tasks_pending"] == 1
    assert alpha["total_cost"] == pytest.approx(1.5, abs=0.01)
    assert alpha["avg_cost_per_task"] == pytest.approx(0.75, abs=0.01)
    # velocity_7d depends on sqlite datetime('now') so just check it's a number
    assert isinstance(alpha["velocity_7d"], (int, float))

    # Beta metrics
    assert beta["tasks_completed"] == 0
    assert beta["tasks_pending"] == 1
    assert beta["total_cost"] == 0.0
    assert beta["avg_cost_per_task"] == 0.0
    assert beta["velocity_7d"] == 0.0


async def test_comparison_metrics_filter_by_id(comparison_client):
    resp = await comparison_client["client"].get("/api/comparison/metrics?project_ids=alpha")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["projects"]) == 1
    assert data["projects"][0]["id"] == "alpha"


async def test_comparison_metrics_filter_multiple_ids(comparison_client):
    resp = await comparison_client["client"].get("/api/comparison/metrics?project_ids=alpha,beta")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["projects"]) == 2


async def test_comparison_metrics_unknown_project_id(comparison_client):
    resp = await comparison_client["client"].get("/api/comparison/metrics?project_ids=nonexistent")
    assert resp.status_code == 404
    assert "nonexistent" in resp.json()["detail"]


async def test_comparison_metrics_empty_when_no_manager(tmp_path):
    from taskbrew.dashboard.app import create_app
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/comparison/metrics")
        assert resp.status_code == 200
        assert resp.json() == {"projects": []}


# ------------------------------------------------------------------
# Edge cases
# ------------------------------------------------------------------


async def test_comparison_project_with_missing_db(tmp_path):
    """Project registered but database file doesn't exist yet."""
    registry_path = tmp_path / "projects.yaml"
    pm = ProjectManager(registry_path=registry_path)

    # Create project dir with config but no database
    project_dir = tmp_path / "ghost"
    config_dir = project_dir / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "team.yaml").write_text(yaml.dump({
        "team_name": "Ghost",
        "database": {"path": "data/taskbrew.db"},
        "dashboard": {"host": "0.0.0.0", "port": 8420},
        "artifacts": {"base_dir": "artifacts"},
    }))

    from datetime import datetime, timezone
    registry_data = {
        "projects": [
            {"id": "ghost", "name": "Ghost", "directory": str(project_dir), "created_at": datetime.now(timezone.utc).isoformat()},
        ],
        "active_project": None,
    }
    registry_path.write_text(yaml.dump(registry_data))

    from taskbrew.dashboard.app import create_app
    app = create_app(project_manager=pm)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Should return zeros, not error
        resp = await client.get("/api/comparison/projects")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["total_tasks"] == 0
        assert data[0]["completed_tasks"] == 0

        resp = await client.get("/api/comparison/metrics")
        assert resp.status_code == 200
        assert data[0]["total_tasks"] == 0


async def test_comparison_project_with_no_team_yaml(tmp_path):
    """Project registered but config/team.yaml is missing."""
    registry_path = tmp_path / "projects.yaml"
    pm = ProjectManager(registry_path=registry_path)

    project_dir = tmp_path / "noconfig"
    project_dir.mkdir(parents=True)

    from datetime import datetime, timezone
    registry_data = {
        "projects": [
            {"id": "noconfig", "name": "No Config", "directory": str(project_dir), "created_at": datetime.now(timezone.utc).isoformat()},
        ],
        "active_project": None,
    }
    registry_path.write_text(yaml.dump(registry_data))

    from taskbrew.dashboard.app import create_app
    app = create_app(project_manager=pm)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/comparison/projects")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        # Should return zero stats gracefully
        assert data[0]["total_tasks"] == 0
        assert data[0]["completion_rate"] == 0.0
