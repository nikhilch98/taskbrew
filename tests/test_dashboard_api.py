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


# ------------------------------------------------------------------
# Execution tracing endpoint
# docs/superpowers/specs/2026-04-24-execution-tracing-endpoint-design.md
# ------------------------------------------------------------------


async def test_trace_page_renders(app_client):
    """GET /trace returns the standalone execution-trace HTML page."""
    resp = await app_client["client"].get("/trace")
    assert resp.status_code == 200
    body = resp.text
    assert "Execution Trace" in body
    assert "groupIdInput" in body
    assert "/api/groups/" in body  # the JS calls back into the API


async def test_trace_page_with_group_id_query(app_client):
    """The page works as a bookmarkable URL with ?group_id=X.
    The page itself is the same; the JS auto-loads on paint."""
    board = app_client["board"]
    group = await board.create_group(title="X", origin="pm", created_by="pm")
    resp = await app_client["client"].get(
        f"/trace?group_id={group['id']}"
    )
    assert resp.status_code == 200
    # The HTML is static; the JS reads the query param client-side.
    assert "groupIdInput" in resp.text


async def test_group_trace_unknown_group_returns_404(app_client):
    resp = await app_client["client"].get("/api/groups/nonexistent/trace")
    assert resp.status_code == 404


async def test_group_trace_empty_group(app_client):
    board = app_client["board"]
    group = await board.create_group(title="Empty", origin="pm", created_by="pm")

    resp = await app_client["client"].get(f"/api/groups/{group['id']}/trace")
    assert resp.status_code == 200
    data = resp.json()
    assert data["group_id"] == group["id"]
    assert data["total_tasks"] == 0
    assert data["tasks"] == []
    assert data["total_cost_usd"] == 0
    assert data["total_input_tokens"] == 0
    assert data["wall_clock_ms"] is None
    assert data["truncated"] is False


async def test_group_trace_single_task_aggregates(app_client):
    board = app_client["board"]
    db = app_client["db"]
    group = await board.create_group(title="Solo", origin="pm", created_by="pm")
    task = await board.create_task(
        group_id=group["id"],
        title="Implement thing",
        task_type="implementation",
        assigned_to="coder",
        created_by="pm",
    )
    # Record usage so the aggregate paths have data.
    await db.record_task_usage(
        task_id=task["id"],
        agent_id="coder-1",
        input_tokens=1000,
        output_tokens=500,
        cost_usd=0.03,
        num_turns=7,
        duration_api_ms=12_500,
    )

    resp = await app_client["client"].get(f"/api/groups/{group['id']}/trace")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_tasks"] == 1
    assert len(data["tasks"]) == 1
    entry = data["tasks"][0]
    assert entry["id"] == task["id"]
    assert entry["cost_usd"] == 0.03
    assert entry["input_tokens"] == 1000
    assert entry["output_tokens"] == 500
    assert entry["num_turns"] == 7
    assert entry["duration_api_ms"] == 12_500
    # Aggregates mirror the single task's values.
    assert data["total_cost_usd"] == 0.03
    assert data["total_input_tokens"] == 1000
    assert data["total_output_tokens"] == 500
    assert data["total_num_turns"] == 7


async def test_group_trace_fanout_children_relationships(app_client):
    """A fan-out group (architect + coders) should surface children[] on
    the architect and parent_id on each coder."""
    board = app_client["board"]
    group = await board.create_group(title="Feature", origin="pm", created_by="pm")
    arch = await board.create_task(
        group_id=group["id"],
        title="Design",
        task_type="tech_design",
        assigned_to="architect",
        created_by="pm",
    )
    coder_a = await board.create_task(
        group_id=group["id"],
        title="Code A",
        task_type="implementation",
        assigned_to="coder",
        created_by="architect-1",
        parent_id=arch["id"],
    )
    coder_b = await board.create_task(
        group_id=group["id"],
        title="Code B",
        task_type="implementation",
        assigned_to="coder",
        created_by="architect-1",
        parent_id=arch["id"],
    )

    resp = await app_client["client"].get(f"/api/groups/{group['id']}/trace")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_tasks"] == 3
    by_id = {t["id"]: t for t in data["tasks"]}
    assert set(by_id[arch["id"]]["children"]) == {coder_a["id"], coder_b["id"]}
    assert by_id[coder_a["id"]]["parent_id"] == arch["id"]
    assert by_id[coder_b["id"]]["parent_id"] == arch["id"]
    # Both coder tasks are implementation, architect is tech_design.
    assert data["status_counts"]["pending"] == 3


async def test_group_trace_surfaces_completion_checks_and_merge_status(app_client):
    """The trace should include the new per-task verification fields
    (completion_checks, merge_status, verification_retries) so the
    dashboard can tell verified merges from unverified ones."""
    board = app_client["board"]
    db = app_client["db"]
    import json as _json
    group = await board.create_group(title="V", origin="pm", created_by="pm")
    task = await board.create_task(
        group_id=group["id"],
        title="T",
        task_type="bug_fix",
        assigned_to="coder",
        created_by="pm",
    )
    await db.execute(
        "UPDATE tasks SET merge_status = ?, verification_retries = ?, "
        "completion_checks = ? WHERE id = ?",
        (
            "merged",
            1,
            _json.dumps({"build": {"status": "pass"}, "tests": {"status": "pass"}}),
            task["id"],
        ),
    )

    resp = await app_client["client"].get(f"/api/groups/{group['id']}/trace")
    assert resp.status_code == 200
    data = resp.json()
    entry = data["tasks"][0]
    assert entry["merge_status"] == "merged"
    assert entry["verification_retries"] == 1
    assert entry["completion_checks"]["build"]["status"] == "pass"
    assert data["merge_status_counts"]["merged"] == 1
    assert data["verification_retries_total"] == 1


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


# ---------------------------------------------------------------------------
# Artifact viewer: agents write flat files + output_text, read-side must union
# structured/flat/output_text sources so the UI surfaces real content.
# ---------------------------------------------------------------------------


@pytest.fixture
async def artifact_client(tmp_path):
    from taskbrew.config_loader import (
        AutoScaleDefaults,
        GuardrailsConfig,
        RoleConfig,
        TeamConfig,
    )

    project_dir = tmp_path / "project"
    (project_dir / "artifacts").mkdir(parents=True)

    roles = {
        "architect": RoleConfig(
            role="architect", display_name="Architect", prefix="AR",
            color="#8b5cf6", emoji="\U0001F3D7", system_prompt="Arch prompt",
            produces=["tech_design"], accepts=["tech_design"],
            routes_to=[], routing_mode="open",
        ),
    }
    team_config = TeamConfig(
        team_name="artifact-test",
        db_path=":memory:",
        dashboard_host="0.0.0.0",
        dashboard_port=8421,
        artifacts_base_dir="artifacts",
        default_max_instances=1,
        default_poll_interval=5,
        default_idle_timeout=300,
        default_auto_scale=AutoScaleDefaults(enabled=False),
        guardrails=GuardrailsConfig(
            max_task_depth=5, max_tasks_per_group=20, rejection_cycle_limit=2,
        ),
    )

    db = Database(str(tmp_path / "artifacts.db"))
    await db.initialize()
    board = TaskBoard(db, group_prefixes={"architect": "FEAT"})
    await board.register_prefixes({"architect": "AR"})
    event_bus = EventBus()
    instance_mgr = InstanceManager(db)

    from taskbrew.dashboard.app import create_app

    app = create_app(
        event_bus=event_bus,
        task_board=board,
        instance_manager=instance_mgr,
        roles=roles,
        team_config=team_config,
        project_dir=str(project_dir),
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield {
            "client": client,
            "board": board,
            "db": db,
            "project_dir": project_dir,
        }
    await db.close()


async def test_task_artifacts_include_flat_files_by_prefix(artifact_client):
    """Files written flat under artifacts/ should surface for the owning task."""
    c = artifact_client["client"]
    board = artifact_client["board"]
    project_dir = artifact_client["project_dir"]

    group = await board.create_group(title="Docs feature", origin="architect", created_by="human")
    task = await board.create_task(
        group_id=group["id"],
        title="Design README",
        task_type="tech_design",
        assigned_to="architect",
        created_by="human",
    )
    task_id = task["id"]

    flat_path = project_dir / "artifacts" / f"{task_id}_design.md"
    flat_path.write_text("# Design\n\nHello from the architect.\n")

    # Unrelated flat file with a prefix-collision must NOT leak in.
    (project_dir / "artifacts" / f"{task_id}1_other.md").write_text("unrelated")

    resp = await c.get(f"/api/artifacts/{group['id']}/{task_id}")
    assert resp.status_code == 200
    files = resp.json()["files"]
    assert f"{task_id}_design.md" in files
    assert f"{task_id}1_other.md" not in files

    resp = await c.get(
        f"/api/artifacts/{group['id']}/{task_id}/{task_id}_design.md"
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["content"].startswith("# Design")


async def test_task_artifacts_include_output_text_as_synthetic_file(artifact_client):
    """tasks.output_text should surface as a synthetic agent_output.md artifact."""
    c = artifact_client["client"]
    board = artifact_client["board"]
    db = artifact_client["db"]

    group = await board.create_group(title="Docs feature", origin="architect", created_by="human")
    task = await board.create_task(
        group_id=group["id"],
        title="Design README",
        task_type="tech_design",
        assigned_to="architect",
        created_by="human",
    )
    task_id = task["id"]

    summary = "Design document created at artifacts/AR-007_design.md.\n\nAll sections covered."
    await db.execute("UPDATE tasks SET output_text = ? WHERE id = ?", (summary, task_id))

    resp = await c.get(f"/api/artifacts/{group['id']}/{task_id}")
    assert resp.status_code == 200
    files = resp.json()["files"]
    assert "agent_output.md" in files

    resp = await c.get(
        f"/api/artifacts/{group['id']}/{task_id}/agent_output.md"
    )
    assert resp.status_code == 200
    assert resp.json()["content"] == summary


async def test_task_artifacts_no_output_and_no_files(artifact_client):
    """A task with no output_text and no files should list nothing (no synthetic entry)."""
    c = artifact_client["client"]
    board = artifact_client["board"]

    group = await board.create_group(title="Empty", origin="architect", created_by="human")
    task = await board.create_task(
        group_id=group["id"],
        title="Nothing produced",
        task_type="tech_design",
        assigned_to="architect",
        created_by="human",
    )

    resp = await c.get(f"/api/artifacts/{group['id']}/{task['id']}")
    assert resp.status_code == 200
    assert resp.json()["files"] == []


async def test_list_all_artifacts_merges_flat_and_output_text(artifact_client):
    """/api/artifacts should list tasks whose only artifact is output_text or a flat file."""
    c = artifact_client["client"]
    board = artifact_client["board"]
    db = artifact_client["db"]
    project_dir = artifact_client["project_dir"]

    group = await board.create_group(title="Docs", origin="architect", created_by="human")
    t1 = await board.create_task(
        group_id=group["id"], title="Has flat file",
        task_type="tech_design", assigned_to="architect", created_by="human",
    )
    t2 = await board.create_task(
        group_id=group["id"], title="Has output text",
        task_type="tech_design", assigned_to="architect", created_by="human",
    )
    (project_dir / "artifacts" / f"{t1['id']}_design.md").write_text("content")
    await db.execute(
        "UPDATE tasks SET output_text = ? WHERE id = ?",
        ("summary", t2["id"]),
    )

    resp = await c.get("/api/artifacts")
    assert resp.status_code == 200
    rows = resp.json()
    by_task = {r["task_id"]: r for r in rows}
    assert t1["id"] in by_task
    assert f"{t1['id']}_design.md" in by_task[t1["id"]]["files"]
    assert t2["id"] in by_task
    assert "agent_output.md" in by_task[t2["id"]]["files"]


# ---------------------------------------------------------------------------
# Stage-1 Fix #3: architect -> coder tasks must include parent_id
# Stage-1 Fix #12: reject duplicate verification tasks for the same parent
# Stage-1 Fix #4: group completion triggers PM goal_verification
# ---------------------------------------------------------------------------


@pytest.fixture
async def stage1_client(tmp_path):
    """App client with PM/architect/coder/verifier roles — exercises the
    create_task validations and group completion trigger."""
    from taskbrew.config_loader import (
        AutoScaleDefaults,
        GuardrailsConfig,
        RoleConfig,
        RouteTarget,
        TeamConfig,
    )

    roles = {
        "pm": RoleConfig(
            role="pm", display_name="PM", prefix="PM", color="#3b82f6",
            emoji="\U0001F4CB", system_prompt="PM prompt",
            produces=["prd"],
            accepts=["goal", "revision", "goal_verification"],
            routes_to=[RouteTarget(role="architect", task_types=["tech_design"])],
            routing_mode="open",
        ),
        "architect": RoleConfig(
            role="architect", display_name="Architect", prefix="AR",
            color="#8b5cf6", emoji="\U0001F3D7", system_prompt="Arch prompt",
            produces=["tech_design"],
            accepts=["tech_design", "architecture_review", "research"],
            routes_to=[RouteTarget(role="coder", task_types=["implementation"])],
            routing_mode="open",
        ),
        "coder": RoleConfig(
            role="coder", display_name="Coder", prefix="CD", color="#f59e0b",
            emoji="\U0001F4BB", system_prompt="Coder prompt",
            produces=["implementation"],
            accepts=["implementation", "bug_fix", "revision"],
            routes_to=[RouteTarget(role="verifier", task_types=["verification"])],
            routing_mode="open",
        ),
        "verifier": RoleConfig(
            role="verifier", display_name="Verifier", prefix="VR",
            color="#06b6d4", emoji="\u2705", system_prompt="Verifier prompt",
            produces=["verification"], accepts=["verification"],
            routes_to=[], routing_mode="open",
        ),
    }
    team_config = TeamConfig(
        team_name="stage1-test", db_path=":memory:",
        dashboard_host="0.0.0.0", dashboard_port=8422,
        artifacts_base_dir="artifacts",
        default_max_instances=1, default_poll_interval=5,
        default_idle_timeout=300,
        default_auto_scale=AutoScaleDefaults(enabled=False),
        guardrails=GuardrailsConfig(
            max_task_depth=10, max_tasks_per_group=50,
            rejection_cycle_limit=3,
        ),
    )
    db = Database(str(tmp_path / "stage1.db"))
    await db.initialize()
    board = TaskBoard(db, group_prefixes={"pm": "FEAT"})
    await board.register_prefixes(
        {"pm": "PM", "architect": "AR", "coder": "CD", "verifier": "VR"}
    )
    event_bus = EventBus()
    instance_mgr = InstanceManager(db)

    from taskbrew.dashboard.app import create_app
    app = create_app(
        event_bus=event_bus, task_board=board, instance_manager=instance_mgr,
        roles=roles, team_config=team_config,
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield {"client": client, "board": board, "db": db}
    await db.close()


async def test_fix3_architect_coder_task_requires_parent_id(stage1_client):
    """Fix #3: architect-origin coder task without parent_id -> 400."""
    c = stage1_client["client"]
    board = stage1_client["board"]
    group = await board.create_group(
        title="Build it", origin="pm", created_by="human",
    )

    resp = await c.post("/api/tasks", json={
        "group_id": group["id"],
        "title": "Write the code",
        "assigned_to": "coder",
        "assigned_by": "architect-1",
        "task_type": "implementation",
        # parent_id deliberately missing
    })
    assert resp.status_code == 400
    assert "parent_id" in resp.json()["detail"].lower()


async def test_fix3_architect_coder_task_succeeds_with_parent_id(stage1_client):
    """Fix #3: supplying parent_id makes the same call succeed."""
    c = stage1_client["client"]
    board = stage1_client["board"]
    group = await board.create_group(
        title="Build it", origin="pm", created_by="human",
    )
    parent = await board.create_task(
        group_id=group["id"], title="Design", task_type="tech_design",
        assigned_to="architect", created_by="human",
    )

    resp = await c.post("/api/tasks", json={
        "group_id": group["id"],
        "title": "Write the code",
        "assigned_to": "coder",
        "assigned_by": "architect-1",
        "task_type": "implementation",
        "parent_id": parent["id"],
    })
    assert resp.status_code == 200


async def test_fix3_human_created_coder_task_exempt(stage1_client):
    """Fix #3: humans (and system) can create CD tasks without parent_id."""
    c = stage1_client["client"]
    board = stage1_client["board"]
    group = await board.create_group(
        title="Ops hotfix", origin="pm", created_by="human",
    )
    resp = await c.post("/api/tasks", json={
        "group_id": group["id"],
        "title": "Hotfix",
        "assigned_to": "coder",
        "assigned_by": "human",
        "task_type": "bug_fix",
    })
    assert resp.status_code == 200


async def test_fix12_duplicate_verifier_task_rejected(stage1_client):
    """Fix #12: second VR for the same parent -> 409."""
    c = stage1_client["client"]
    board = stage1_client["board"]
    group = await board.create_group(
        title="F", origin="pm", created_by="human",
    )
    parent = await board.create_task(
        group_id=group["id"], title="Impl",
        task_type="implementation", assigned_to="coder", created_by="human",
    )
    first = await c.post("/api/tasks", json={
        "group_id": group["id"],
        "title": "Verify",
        "assigned_to": "verifier",
        "assigned_by": "coder-1",
        "task_type": "verification",
        "parent_id": parent["id"],
    })
    assert first.status_code == 200

    second = await c.post("/api/tasks", json={
        "group_id": group["id"],
        "title": "Verify again",
        "assigned_to": "verifier",
        "assigned_by": "coder-1",
        "task_type": "verification",
        "parent_id": parent["id"],
    })
    assert second.status_code == 409
    assert "already exists" in second.json()["detail"].lower()


async def test_fix12_cancelled_verifier_allows_new_one(stage1_client):
    """Fix #12: cancelled VR doesn't block a fresh verification task."""
    c = stage1_client["client"]
    board = stage1_client["board"]
    db = stage1_client["db"]

    group = await board.create_group(
        title="F", origin="pm", created_by="human",
    )
    parent = await board.create_task(
        group_id=group["id"], title="Impl",
        task_type="implementation", assigned_to="coder", created_by="human",
    )
    first = await c.post("/api/tasks", json={
        "group_id": group["id"], "title": "VR",
        "assigned_to": "verifier", "assigned_by": "coder-1",
        "task_type": "verification", "parent_id": parent["id"],
    })
    assert first.status_code == 200
    # Cancel the first VR.
    await db.execute(
        "UPDATE tasks SET status = 'cancelled' WHERE id = ?",
        (first.json()["id"],),
    )

    second = await c.post("/api/tasks", json={
        "group_id": group["id"], "title": "VR again",
        "assigned_to": "verifier", "assigned_by": "coder-1",
        "task_type": "verification", "parent_id": parent["id"],
    })
    assert second.status_code == 200


async def test_fix4_group_completion_spawns_goal_verification(stage1_client):
    """Fix #4: when the last task in a >=5-task group goes terminal, a PM
    goal_verification task is auto-created and blocks the group from sealing."""
    board = stage1_client["board"]
    db = stage1_client["db"]

    group = await board.create_group(
        title="Large feature", origin="pm", created_by="human",
    )
    pm_goal = await board.create_task(
        group_id=group["id"], title="PRD",
        task_type="goal", assigned_to="pm", created_by="human",
    )
    # Need >=5 tasks total to clear the trivial-goal skip.
    tasks = [pm_goal]
    for i in range(5):
        tasks.append(await board.create_task(
            group_id=group["id"], title=f"child {i}",
            task_type="implementation", assigned_to="coder",
            created_by="human",
        ))

    # Mark all as in_progress then complete them one by one.
    for t in tasks:
        await db.execute(
            "UPDATE tasks SET status = 'in_progress' WHERE id = ?", (t["id"],),
        )
    for t in tasks:
        await board.complete_task(t["id"])

    # The goal_verification task should now exist, pending for PM.
    gv = await db.execute_fetchone(
        "SELECT id, status, assigned_to, parent_id, requires_fanout "
        "FROM tasks WHERE group_id = ? AND task_type = 'goal_verification'",
        (group["id"],),
    )
    assert gv is not None
    assert gv["assigned_to"] == "pm"
    assert gv["status"] == "pending"
    assert gv["parent_id"] == pm_goal["id"]
    # Goal-verify should NOT be subject to the fan-out gate itself.
    assert gv["requires_fanout"] == 0

    # The group must stay active while goal verification is pending.
    grp = await db.execute_fetchone(
        "SELECT status FROM groups WHERE id = ?", (group["id"],),
    )
    assert grp["status"] == "active"


async def test_fix4_small_group_skips_goal_verification(stage1_client):
    """Fix #4: groups with <5 tasks (e.g. FEAT-002 README) don't need a
    second PM pass — avoids token waste on trivial goals."""
    board = stage1_client["board"]
    db = stage1_client["db"]

    group = await board.create_group(
        title="docs", origin="pm", created_by="human",
    )
    t1 = await board.create_task(
        group_id=group["id"], title="PRD",
        task_type="goal", assigned_to="pm", created_by="human",
    )
    t2 = await board.create_task(
        group_id=group["id"], title="write doc",
        task_type="implementation", assigned_to="coder", created_by="human",
    )
    for t in (t1, t2):
        await db.execute(
            "UPDATE tasks SET status = 'in_progress' WHERE id = ?", (t["id"],),
        )
    for t in (t1, t2):
        await board.complete_task(t["id"])

    gv = await db.execute_fetchone(
        "SELECT id FROM tasks WHERE group_id = ? "
        "AND task_type = 'goal_verification'",
        (group["id"],),
    )
    assert gv is None
    grp = await db.execute_fetchone(
        "SELECT status FROM groups WHERE id = ?", (group["id"],),
    )
    assert grp["status"] == "completed"


async def test_fix4_only_fires_once_per_group(stage1_client):
    """Fix #4: completing the auto-generated goal_verification task shouldn't
    spawn a second one — that would loop forever."""
    board = stage1_client["board"]
    db = stage1_client["db"]

    group = await board.create_group(
        title="F", origin="pm", created_by="human",
    )
    pm_goal = await board.create_task(
        group_id=group["id"], title="PRD",
        task_type="goal", assigned_to="pm", created_by="human",
    )
    children = [pm_goal]
    for i in range(5):
        children.append(await board.create_task(
            group_id=group["id"], title=f"c{i}",
            task_type="implementation", assigned_to="coder",
            created_by="human",
        ))
    for t in children:
        await db.execute(
            "UPDATE tasks SET status = 'in_progress' WHERE id = ?", (t["id"],),
        )
    for t in children:
        await board.complete_task(t["id"])

    gv_rows = await db.execute_fetchall(
        "SELECT id FROM tasks WHERE group_id = ? "
        "AND task_type = 'goal_verification'",
        (group["id"],),
    )
    assert len(gv_rows) == 1

    # Complete the goal_verification task — group should seal, no second GV.
    gv_id = gv_rows[0]["id"]
    await db.execute(
        "UPDATE tasks SET status = 'in_progress' WHERE id = ?", (gv_id,),
    )
    await board.complete_task(gv_id)

    gv_after = await db.execute_fetchall(
        "SELECT id FROM tasks WHERE group_id = ? "
        "AND task_type = 'goal_verification'",
        (group["id"],),
    )
    assert len(gv_after) == 1  # still just the one

    grp = await db.execute_fetchone(
        "SELECT status FROM groups WHERE id = ?", (group["id"],),
    )
    assert grp["status"] == "completed"
