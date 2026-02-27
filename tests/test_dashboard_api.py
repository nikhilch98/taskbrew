"""Tests for the rewritten dashboard API endpoints."""

import shutil

import pytest
from httpx import AsyncClient, ASGITransport

from taskbrew.orchestrator.database import Database
from taskbrew.orchestrator.task_board import TaskBoard
from taskbrew.orchestrator.event_bus import EventBus
from taskbrew.agents.instance_manager import InstanceManager
from taskbrew.project_manager import ProjectManager


@pytest.fixture
async def app_client(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    await db.initialize()
    board = TaskBoard(db, group_prefixes={"pm": "FEAT", "architect": "DEBT"})
    await board.register_prefixes(
        {"pm": "PM", "architect": "AR", "coder": "CD", "verifier": "VR"}
    )
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
        yield {"client": client, "board": board, "db": db, "event_bus": event_bus, "instance_mgr": instance_mgr}
    await db.close()


async def test_health(app_client):
    resp = await app_client["client"].get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] in ("ok", "degraded")


async def test_health_check_returns_db_status(app_client):
    """Health endpoint should return db connectivity status."""
    resp = await app_client["client"].get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert "db" in data
    assert data["status"] == "ok"
    assert data["db"] == "connected"


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
    assert resp.status_code in (400, 422)  # 422 when Pydantic validates


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


async def test_post_task(app_client):
    c = app_client["client"]
    goal_resp = await c.post("/api/goals", json={"title": "Test Goal"})
    group_id = goal_resp.json()["group_id"]

    resp = await c.post("/api/tasks", json={
        "group_id": group_id,
        "title": "Design the architecture",
        "assigned_to": "architect",
        "assigned_by": "pm-1",
        "task_type": "tech_design",
        "description": "Create a detailed tech design for the flappy bird PRD.",
        "priority": "high",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["title"] == "Design the architecture"
    assert data["assigned_to"] == "architect"
    assert data["created_by"] == "pm-1"
    assert data["status"] == "pending"
    assert data["id"].startswith("AR-")


async def test_post_task_missing_required_fields(app_client):
    resp = await app_client["client"].post("/api/tasks", json={"title": "No group"})
    assert resp.status_code == 422


async def test_post_task_with_blocked_by(app_client):
    c = app_client["client"]
    goal_resp = await c.post("/api/goals", json={"title": "Dep test"})
    group_id = goal_resp.json()["group_id"]
    first_task_id = goal_resp.json()["task_id"]

    resp = await c.post("/api/tasks", json={
        "group_id": group_id,
        "title": "Blocked task",
        "assigned_to": "architect",
        "assigned_by": "pm-1",
        "task_type": "tech_design",
        "blocked_by": [first_task_id],
    })
    assert resp.status_code == 200
    assert resp.json()["status"] == "blocked"


# ---------------------------------------------------------------------------
# C3: Route validation tests
# ---------------------------------------------------------------------------


@pytest.fixture
async def routed_client(tmp_path):
    """App client with roles configured for route validation testing."""
    from taskbrew.config_loader import RoleConfig, RouteTarget

    roles = {
        "pm": RoleConfig(
            role="pm", display_name="PM", prefix="PM", color="#3b82f6",
            emoji="\U0001F4CB", system_prompt="PM prompt",
            produces=["prd"], accepts=["goal"],
            routes_to=[RouteTarget(role="architect", task_types=["tech_design"])],
            routing_mode="restricted",
        ),
        "architect": RoleConfig(
            role="architect", display_name="Architect", prefix="AR", color="#8b5cf6",
            emoji="\U0001F3D7", system_prompt="Architect prompt",
            produces=["tech_design"], accepts=["prd", "tech_design"],
            routes_to=[RouteTarget(role="coder", task_types=["implementation", "bug_fix"])],
            routing_mode="restricted",
        ),
        "coder": RoleConfig(
            role="coder", display_name="Coder", prefix="CD", color="#f59e0b",
            emoji="\U0001F4BB", system_prompt="Coder prompt",
            produces=["implementation"], accepts=["implementation", "bug_fix", "revision"],
            routes_to=[RouteTarget(role="verifier", task_types=["verification"])],
            routing_mode="restricted",
        ),
        "verifier": RoleConfig(
            role="verifier", display_name="Verifier", prefix="VR", color="#06b6d4",
            emoji="\U00002705", system_prompt="Verifier prompt",
            produces=["verification"], accepts=["verification"],
            routes_to=[
                RouteTarget(role="coder", task_types=["revision", "bug_fix"]),
                RouteTarget(role="architect", task_types=["rejection"]),
            ],
            routing_mode="restricted",
        ),
    }

    db = Database(str(tmp_path / "test.db"))
    await db.initialize()
    board = TaskBoard(db, group_prefixes={"pm": "FEAT", "architect": "DEBT"})
    await board.register_prefixes(
        {"pm": "PM", "architect": "AR", "coder": "CD", "verifier": "VR"}
    )
    event_bus = EventBus()
    instance_mgr = InstanceManager(db)

    from taskbrew.dashboard.app import create_app

    app = create_app(
        event_bus=event_bus,
        task_board=board,
        instance_manager=instance_mgr,
        roles=roles,
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield {"client": client, "board": board, "db": db}
    await db.close()


async def test_route_validation_unknown_role(routed_client):
    """C3: assigning to an unknown role returns 400."""
    c = routed_client["client"]
    goal = await c.post("/api/goals", json={"title": "Test"})
    gid = goal.json()["group_id"]

    resp = await c.post("/api/tasks", json={
        "group_id": gid, "title": "Bad role", "assigned_to": "nonexistent",
        "assigned_by": "pm-1", "task_type": "tech_design",
    })
    assert resp.status_code == 400
    assert "Unknown target role" in resp.json()["detail"]


async def test_route_validation_bad_task_type(routed_client):
    """C3: task_type not accepted by target role returns 400."""
    c = routed_client["client"]
    goal = await c.post("/api/goals", json={"title": "Test"})
    gid = goal.json()["group_id"]

    resp = await c.post("/api/tasks", json={
        "group_id": gid, "title": "Bad type", "assigned_to": "architect",
        "assigned_by": "pm-1", "task_type": "verification",
    })
    assert resp.status_code == 400
    assert "does not accept" in resp.json()["detail"]


async def test_route_validation_unauthorized_route(routed_client):
    """C3: creator role not allowed to route to target returns 403."""
    c = routed_client["client"]
    goal = await c.post("/api/goals", json={"title": "Test"})
    gid = goal.json()["group_id"]

    # Coder can only route to verifier, not architect
    resp = await c.post("/api/tasks", json={
        "group_id": gid, "title": "Unauthorized", "assigned_to": "architect",
        "assigned_by": "coder-1", "task_type": "tech_design",
    })
    assert resp.status_code == 403
    assert "not allowed" in resp.json()["detail"]


async def test_route_validation_human_bypass(routed_client):
    """C3: human-created tasks bypass route validation."""
    c = routed_client["client"]
    goal = await c.post("/api/goals", json={"title": "Test"})
    gid = goal.json()["group_id"]

    resp = await c.post("/api/tasks", json={
        "group_id": gid, "title": "Human task", "assigned_to": "coder",
        "assigned_by": "human", "task_type": "implementation",
    })
    assert resp.status_code == 200


async def test_route_validation_valid_route(routed_client):
    """C3: valid route succeeds."""
    c = routed_client["client"]
    goal = await c.post("/api/goals", json={"title": "Test"})
    gid = goal.json()["group_id"]

    resp = await c.post("/api/tasks", json={
        "group_id": gid, "title": "Valid route", "assigned_to": "architect",
        "assigned_by": "pm-1", "task_type": "tech_design",
    })
    assert resp.status_code == 200
    assert resp.json()["id"].startswith("AR-")


# ---------------------------------------------------------------------------
# C3b: Open routing mode tests
# ---------------------------------------------------------------------------


@pytest.fixture
async def open_routed_client(tmp_path):
    """App client with roles using open routing_mode (default)."""
    from taskbrew.config_loader import RoleConfig, RouteTarget

    roles = {
        "pm": RoleConfig(
            role="pm", display_name="PM", prefix="PM", color="#3b82f6",
            emoji="\U0001F4CB", system_prompt="PM prompt",
            produces=["prd"], accepts=["goal"],
            routes_to=[RouteTarget(role="architect", task_types=["tech_design"])],
            routing_mode="open",
        ),
        "architect": RoleConfig(
            role="architect", display_name="Architect", prefix="AR", color="#8b5cf6",
            emoji="\U0001F3D7", system_prompt="Architect prompt",
            produces=["tech_design"], accepts=["prd", "tech_design"],
            routes_to=[RouteTarget(role="coder", task_types=["implementation", "bug_fix"])],
            routing_mode="open",
        ),
        "coder": RoleConfig(
            role="coder", display_name="Coder", prefix="CD", color="#f59e0b",
            emoji="\U0001F4BB", system_prompt="Coder prompt",
            produces=["implementation"], accepts=["implementation", "bug_fix", "revision"],
            routes_to=[RouteTarget(role="verifier", task_types=["verification"])],
            routing_mode="open",
        ),
        "verifier": RoleConfig(
            role="verifier", display_name="Verifier", prefix="VR", color="#06b6d4",
            emoji="\U00002705", system_prompt="Verifier prompt",
            produces=["verification"], accepts=["verification"],
            routes_to=[
                RouteTarget(role="coder", task_types=["revision", "bug_fix"]),
                RouteTarget(role="architect", task_types=["rejection"]),
            ],
            routing_mode="open",
        ),
    }

    db = Database(str(tmp_path / "test.db"))
    await db.initialize()
    board = TaskBoard(db, group_prefixes={"pm": "FEAT", "architect": "DEBT"})
    await board.register_prefixes(
        {"pm": "PM", "architect": "AR", "coder": "CD", "verifier": "VR"}
    )
    event_bus = EventBus()
    instance_mgr = InstanceManager(db)

    from taskbrew.dashboard.app import create_app

    app = create_app(
        event_bus=event_bus,
        task_board=board,
        instance_manager=instance_mgr,
        roles=roles,
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield {"client": client, "board": board, "db": db}
    await db.close()


async def test_create_task_open_routing_allows_any_role(open_routed_client):
    """In open routing mode, any role can create tasks for any other role."""
    c = open_routed_client["client"]

    # Create a task group first
    goal = await c.post("/api/goals", json={"title": "Test Group"})
    group_id = goal.json()["group_id"]

    # pm creating a task for verifier (not normally in pm's routes_to)
    # With routing_mode="open" (default), this should succeed
    resp = await c.post("/api/tasks", json={
        "group_id": group_id,
        "title": "Security check",
        "description": "Check security",
        "task_type": "verification",
        "assigned_to": "verifier",
        "assigned_by": "pm-1",
    })
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# C4: Rejection cycle limit tests
# ---------------------------------------------------------------------------


async def test_rejection_cycle_limit_blocks(routed_client):
    """C4: 3+ revision/bug_fix ancestors → 409."""
    c = routed_client["client"]
    board = routed_client["board"]

    goal = await c.post("/api/goals", json={"title": "Cycle test"})
    gid = goal.json()["group_id"]

    # Build chain: impl → revision → revision → revision → (attempt 4th)
    t1 = await board.create_task(
        group_id=gid, title="Original", task_type="implementation",
        assigned_to="coder", created_by="architect-1",
    )
    t2 = await board.create_task(
        group_id=gid, title="Revision 1", task_type="revision",
        assigned_to="coder", created_by="verifier-1", parent_id=t1["id"],
    )
    t3 = await board.create_task(
        group_id=gid, title="Revision 2", task_type="revision",
        assigned_to="coder", created_by="verifier-1", parent_id=t2["id"],
    )
    t4 = await board.create_task(
        group_id=gid, title="Revision 3", task_type="revision",
        assigned_to="coder", created_by="verifier-1", parent_id=t3["id"],
    )

    # 4th revision should be blocked
    resp = await c.post("/api/tasks", json={
        "group_id": gid, "title": "Revision 4", "assigned_to": "coder",
        "assigned_by": "verifier-1", "task_type": "revision",
        "parent_id": t4["id"],
    })
    assert resp.status_code == 409
    assert "cycle limit" in resp.json()["detail"].lower()


async def test_rejection_cycle_under_limit_passes(routed_client):
    """C4: under 3 revisions in chain → 200."""
    c = routed_client["client"]
    board = routed_client["board"]

    goal = await c.post("/api/goals", json={"title": "Under limit"})
    gid = goal.json()["group_id"]

    t1 = await board.create_task(
        group_id=gid, title="Original", task_type="implementation",
        assigned_to="coder", created_by="architect-1",
    )
    t2 = await board.create_task(
        group_id=gid, title="Revision 1", task_type="revision",
        assigned_to="coder", created_by="verifier-1", parent_id=t1["id"],
    )

    # 2nd revision (only 1 revision ancestor) — should pass
    resp = await c.post("/api/tasks", json={
        "group_id": gid, "title": "Revision 2", "assigned_to": "coder",
        "assigned_by": "verifier-1", "task_type": "revision",
        "parent_id": t2["id"],
    })
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Browse directory endpoint
# ---------------------------------------------------------------------------


async def test_browse_directory_returns_path(app_client, monkeypatch):
    """browse-directory returns the selected path with trailing slash stripped."""
    import sys
    monkeypatch.setattr(sys, "platform", "darwin")

    async def fake_exec(*args, **kwargs):
        class FakeProc:
            returncode = 0
            async def communicate(self):
                return (b"/Users/me/projects/my-app/\n", b"")
        return FakeProc()

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    resp = await app_client["client"].post("/api/browse-directory")
    assert resp.status_code == 200
    data = resp.json()
    assert data["path"] == "/Users/me/projects/my-app"
    assert data["cancelled"] is False


async def test_browse_directory_cancelled(app_client, monkeypatch):
    """User cancellation returns cancelled=True, not an error."""
    import sys
    monkeypatch.setattr(sys, "platform", "darwin")

    async def fake_exec(*args, **kwargs):
        class FakeProc:
            returncode = 1
            async def communicate(self):
                return (b"", b"execution error: User canceled. (-128)")
        return FakeProc()

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    resp = await app_client["client"].post("/api/browse-directory")
    assert resp.status_code == 200
    data = resp.json()
    assert data["cancelled"] is True
    assert data["path"] is None


async def test_browse_directory_unsupported_platform(app_client, monkeypatch):
    """Non-macOS platforms get 501."""
    import sys
    monkeypatch.setattr(sys, "platform", "linux")

    resp = await app_client["client"].post("/api/browse-directory")
    assert resp.status_code == 501


# ---------------------------------------------------------------------------
# Project activate endpoint — edge-case HTTP status codes
# ---------------------------------------------------------------------------


@pytest.fixture
async def pm_client(tmp_path):
    """App client wired with a real ProjectManager (no orchestrator components)."""
    registry = tmp_path / "registry" / "projects.yaml"
    pm = ProjectManager(registry_path=registry)

    from taskbrew.dashboard.app import create_app

    app = create_app(project_manager=pm)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield {"client": client, "pm": pm, "tmp_path": tmp_path}


async def test_activate_unknown_project_returns_404(pm_client):
    resp = await pm_client["client"].post("/api/projects/no-such/activate")
    assert resp.status_code == 404


async def test_activate_missing_directory_returns_410(pm_client):
    """When the project directory has been deleted, the endpoint returns 410."""
    pm = pm_client["pm"]
    d = pm_client["tmp_path"] / "vanished"
    d.mkdir()
    pm.create_project("Vanished", str(d))

    # Remove directory after registration
    shutil.rmtree(d)

    resp = await pm_client["client"].post("/api/projects/vanished/activate")
    assert resp.status_code == 410
    assert "no longer exists" in resp.json()["detail"]

    # Should be auto-removed from registry
    assert pm.list_projects() == []


async def test_activate_missing_team_yaml_returns_410(pm_client):
    """When config/team.yaml is missing, the endpoint returns 410."""
    pm = pm_client["pm"]
    d = pm_client["tmp_path"] / "no-config"
    d.mkdir()
    pm.create_project("No Config", str(d), with_defaults=False)
    (d / "config" / "team.yaml").unlink()

    resp = await pm_client["client"].post("/api/projects/no-config/activate")
    assert resp.status_code == 410
    assert "team.yaml" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Intelligence endpoint error handling tests
# ---------------------------------------------------------------------------


async def test_quality_scores_returns_503_when_manager_none(app_client):
    """Intelligence endpoints return 503 when the manager is not initialized."""
    resp = await app_client["client"].get("/api/quality/scores")
    assert resp.status_code == 503
    assert "Quality manager not initialized" in resp.json()["detail"]


async def test_planning_estimate_returns_503_when_manager_none(app_client):
    """Planning estimate returns 503 when planning_manager is None."""
    resp = await app_client["client"].post("/api/tasks/FAKE-001/estimate")
    assert resp.status_code == 503
    assert "Planning manager not initialized" in resp.json()["detail"]


async def test_memory_endpoint_returns_503_when_manager_none(app_client):
    """Memory endpoint returns 503 when memory_manager is None."""
    resp = await app_client["client"].get("/api/memories")
    assert resp.status_code == 503
    assert "Memory manager not initialized" in resp.json()["detail"]


async def test_collaboration_returns_503_when_manager_none(app_client):
    """Collaboration endpoint returns 503 when collaboration_manager is None."""
    resp = await app_client["client"].get("/api/collaborations")
    assert resp.status_code == 503
    assert "Collaboration manager not initialized" in resp.json()["detail"]


async def test_gemini_usage_endpoint_returns_data(app_client):
    """GET /api/usage/gemini/summary should return a response."""
    resp = await app_client["client"].get("/api/usage/gemini/summary")
    assert resp.status_code == 200
    data = resp.json()
    assert "available" in data
    # May not be available in test env, but structure is correct
    assert isinstance(data["available"], bool)


async def test_create_project_with_cli_provider(app_client, tmp_path):
    """POST /api/projects with cli_provider should be accepted."""
    from taskbrew.dashboard.routers.system import set_project_deps

    pm = ProjectManager(registry_path=tmp_path / "registry.yaml")
    set_project_deps(pm, None)

    project_dir = str(tmp_path / "gemini-project")
    resp = await app_client["client"].post(
        "/api/projects",
        json={
            "name": "Gemini Test",
            "directory": project_dir,
            "with_defaults": True,
            "cli_provider": "gemini",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "Gemini Test"

    # Verify the generated role YAMLs contain Gemini model IDs
    import yaml
    from pathlib import Path

    pm_yaml = Path(project_dir) / "config" / "roles" / "pm.yaml"
    assert pm_yaml.exists()
    with open(pm_yaml) as f:
        pm_data = yaml.safe_load(f)
    assert pm_data["model"] == "gemini-3.1-pro-preview"

    coder_yaml = Path(project_dir) / "config" / "roles" / "coder.yaml"
    with open(coder_yaml) as f:
        coder_data = yaml.safe_load(f)
    assert coder_data["model"] == "gemini-3-flash-preview"

    # Verify team.yaml contains cli_provider
    team_yaml = Path(project_dir) / "config" / "team.yaml"
    with open(team_yaml) as f:
        team_data = yaml.safe_load(f)
    assert team_data["cli_provider"] == "gemini"

    # Cleanup
    set_project_deps(None, None)


# ---------------------------------------------------------------------------
# Fix 1: PATCH field whitelist
# ---------------------------------------------------------------------------


async def test_patch_task_valid_field(app_client):
    """PATCH with valid fields should succeed."""
    board = app_client["board"]
    group = await board.create_group(title="Patch test", origin="pm", created_by="pm")
    task = await board.create_task(
        group_id=group["id"],
        title="Patchable",
        task_type="implement",
        assigned_to="coder",
        created_by="pm",
    )
    resp = await app_client["client"].patch(
        f"/api/tasks/{task['id']}", json={"priority": "high"}
    )
    assert resp.status_code == 200
    assert resp.json()["priority"] == "high"


# ---------------------------------------------------------------------------
# Fix 2 & 3: Guardrails (max_tasks_per_group, max_task_depth)
# ---------------------------------------------------------------------------


@pytest.fixture
async def guardrail_client(tmp_path):
    """App client with team_config guardrails configured."""
    from taskbrew.config_loader import RoleConfig, RouteTarget, GuardrailsConfig, TeamConfig, AutoScaleDefaults

    roles = {
        "pm": RoleConfig(
            role="pm", display_name="PM", prefix="PM", color="#3b82f6",
            emoji="\U0001F4CB", system_prompt="PM prompt",
            produces=["prd"], accepts=["goal"],
            routes_to=[RouteTarget(role="coder", task_types=["implementation"])],
            routing_mode="open",
        ),
        "coder": RoleConfig(
            role="coder", display_name="Coder", prefix="CD", color="#f59e0b",
            emoji="\U0001F4BB", system_prompt="Coder prompt",
            produces=["implementation"], accepts=["implementation", "bug_fix", "revision"],
            routes_to=[],
            routing_mode="open",
        ),
    }

    team_config = TeamConfig(
        team_name="test-team",
        db_path=":memory:",
        dashboard_host="0.0.0.0",
        dashboard_port=8080,
        artifacts_base_dir="artifacts",
        default_max_instances=2,
        default_poll_interval=5,
        default_idle_timeout=300,
        default_auto_scale=AutoScaleDefaults(enabled=False),
        guardrails=GuardrailsConfig(
            max_task_depth=5,
            max_tasks_per_group=20,
            rejection_cycle_limit=2,
        ),
    )

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
        roles=roles,
        team_config=team_config,
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield {"client": client, "board": board, "db": db}
    await db.close()


async def test_guardrail_max_tasks_per_group(guardrail_client):
    """Should reject task creation when group exceeds max_tasks_per_group (20)."""
    c = guardrail_client["client"]
    board = guardrail_client["board"]

    group = await board.create_group(title="Full group", origin="pm", created_by="pm")

    # Create 20 tasks (the limit)
    for i in range(20):
        await board.create_task(
            group_id=group["id"],
            title=f"Task {i}",
            task_type="implementation",
            assigned_to="coder",
            created_by="pm",
        )

    # The 21st task via API should be rejected with 409
    resp = await c.post("/api/tasks", json={
        "group_id": group["id"],
        "title": "One too many",
        "assigned_to": "coder",
        "assigned_by": "human",
        "task_type": "implementation",
    })
    assert resp.status_code == 409
    assert "exceeding limit" in resp.json()["detail"]


async def test_guardrail_max_task_depth(guardrail_client):
    """Should reject task creation when depth exceeds max_task_depth (5)."""
    c = guardrail_client["client"]
    board = guardrail_client["board"]

    group = await board.create_group(title="Deep group", origin="pm", created_by="pm")

    # Build chain of depth 5: root -> c1 -> c2 -> c3 -> c4 -> c5
    current_parent = None
    for i in range(6):
        t = await board.create_task(
            group_id=group["id"], title=f"Level {i}", task_type="implementation",
            assigned_to="coder", created_by="pm",
            parent_id=current_parent,
        )
        current_parent = t["id"]

    # Now current_parent is at depth 5. Creating a new child should exceed max_task_depth=5.
    resp = await c.post("/api/tasks", json={
        "group_id": group["id"],
        "title": "Too deep",
        "assigned_to": "coder",
        "assigned_by": "human",
        "task_type": "implementation",
        "parent_id": current_parent,
    })
    assert resp.status_code == 409
    assert "depth" in resp.json()["detail"].lower()


async def test_guardrail_rejection_cycle_limit_configurable(guardrail_client):
    """With rejection_cycle_limit=2, the 3rd revision (which sees 2 ancestors) should be blocked."""
    c = guardrail_client["client"]
    board = guardrail_client["board"]

    group = await board.create_group(title="Cycle limit", origin="pm", created_by="pm")

    # Chain: Original (implementation) -> Revision 1 -> Revision 2
    # When creating Revision 3, it walks the parent chain and finds 2 revision
    # ancestors (Revision 2, Revision 1), which equals the limit of 2.
    t1 = await board.create_task(
        group_id=group["id"], title="Original", task_type="implementation",
        assigned_to="coder", created_by="pm",
    )
    t2 = await board.create_task(
        group_id=group["id"], title="Revision 1", task_type="revision",
        assigned_to="coder", created_by="pm", parent_id=t1["id"],
    )

    # 2nd revision as child of t2 (depth=2, within max_task_depth=3)
    # This sees 1 revision ancestor (t2), under the cycle limit of 2
    resp = await c.post("/api/tasks", json={
        "group_id": group["id"],
        "title": "Revision 2",
        "assigned_to": "coder",
        "assigned_by": "human",
        "task_type": "revision",
        "parent_id": t2["id"],
    })
    assert resp.status_code == 200
    t3_id = resp.json()["id"]

    # 3rd revision as child of t3 (depth=3, at max_task_depth=3)
    # This sees 2 revision ancestors (t3, t2), which hits the cycle limit of 2
    resp = await c.post("/api/tasks", json={
        "group_id": group["id"],
        "title": "Revision 3",
        "assigned_to": "coder",
        "assigned_by": "human",
        "task_type": "revision",
        "parent_id": t3_id,
    })
    assert resp.status_code == 409
    assert "cycle limit" in resp.json()["detail"].lower()
