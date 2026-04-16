"""Tests for the AgentLoop (non-SDK parts only)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from taskbrew.agents.agent_loop import AgentLoop
from taskbrew.agents.instance_manager import InstanceManager
from taskbrew.config_loader import RoleConfig, RouteTarget
from taskbrew.orchestrator.database import Database
from taskbrew.orchestrator.event_bus import EventBus
from taskbrew.orchestrator.task_board import TaskBoard


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _make_role(
    role: str = "coder",
    display_name: str = "Coder",
    routes_to: list[RouteTarget] | None = None,
    context_includes: list[str] | None = None,
) -> RoleConfig:
    """Create a minimal RoleConfig for testing."""
    return RoleConfig(
        role=role,
        display_name=display_name,
        prefix="CD",
        color="#00ff00",
        emoji="",
        system_prompt="You are a coder.",
        tools=["Read", "Write", "Bash"],
        routes_to=routes_to or [],
        context_includes=context_includes or [],
    )


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture
async def db():
    """Create and initialise an in-memory database."""
    database = Database(":memory:")
    await database.initialize()
    yield database
    await database.close()


@pytest.fixture
async def board(db: Database) -> TaskBoard:
    """Create a TaskBoard with typical prefixes registered."""
    tb = TaskBoard(db, group_prefixes={"pm": "FEAT"})
    await tb.register_prefixes(
        {
            "pm": "PM",
            "architect": "AR",
            "coder": "CD",
            "tester": "TS",
            "reviewer": "RV",
        }
    )
    return tb


@pytest.fixture
def event_bus() -> EventBus:
    return EventBus()


@pytest.fixture
async def instance_mgr(db: Database) -> InstanceManager:
    return InstanceManager(db)


def _make_loop(
    board: TaskBoard,
    event_bus: EventBus,
    instance_mgr: InstanceManager,
    role_config: RoleConfig | None = None,
    instance_id: str = "coder-1",
) -> AgentLoop:
    """Create an AgentLoop for testing."""
    rc = role_config or _make_role()
    return AgentLoop(
        instance_id=instance_id,
        role_config=rc,
        board=board,
        event_bus=event_bus,
        instance_manager=instance_mgr,
        all_roles={rc.role: rc},
    )


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------


async def test_poll_claims_task(
    board: TaskBoard, event_bus: EventBus, instance_mgr: InstanceManager
):
    """poll_for_task should claim a pending task assigned to this role."""
    group = await board.create_group(title="Feature", created_by="pm")
    await board.create_task(
        group_id=group["id"],
        title="Implement endpoint",
        task_type="implementation",
        assigned_to="coder",
    )

    loop = _make_loop(board, event_bus, instance_mgr)
    task = await loop.poll_for_task()

    assert task is not None
    assert task["claimed_by"] == "coder-1"
    assert task["status"] == "in_progress"


async def test_poll_returns_none_when_empty(
    board: TaskBoard, event_bus: EventBus, instance_mgr: InstanceManager
):
    """poll_for_task should return None when no pending tasks exist."""
    loop = _make_loop(board, event_bus, instance_mgr)
    task = await loop.poll_for_task()

    assert task is None


async def test_build_context(
    board: TaskBoard, event_bus: EventBus, instance_mgr: InstanceManager
):
    """build_context should produce a string with task info and parent details."""
    group = await board.create_group(title="Feature X", created_by="pm")
    parent = await board.create_task(
        group_id=group["id"],
        title="Design API schema",
        task_type="tech_design",
        assigned_to="architect",
    )
    child = await board.create_task(
        group_id=group["id"],
        title="Implement API endpoint",
        task_type="implementation",
        assigned_to="coder",
        parent_id=parent["id"],
        description="Build the REST endpoint for user registration.",
    )

    role_config = _make_role(
        routes_to=[RouteTarget(role="verifier", task_types=["verification"])],
        context_includes=["parent_artifact"],
    )
    loop = _make_loop(board, event_bus, instance_mgr, role_config=role_config)

    context = await loop.build_context(child)

    # Check that key parts are present.
    assert "Coder" in context
    assert "coder-1" in context
    assert child["id"] in context
    assert "Implement API endpoint" in context
    assert "implementation" in context
    assert "medium" in context
    assert group["id"] in context
    assert "Build the REST endpoint" in context
    # Parent info
    assert parent["id"] in context
    assert "Design API schema" in context
    # Coder role does NOT get routing hints (D4: conditional routing)
    assert "When Complete" not in context


async def test_build_context_memory_recall_failure(
    board: TaskBoard, event_bus: EventBus, instance_mgr: InstanceManager
):
    """build_context should still succeed when memory recall raises an exception."""
    group = await board.create_group(title="Feature", created_by="pm")
    task = await board.create_task(
        group_id=group["id"],
        title="Implement endpoint",
        task_type="implementation",
        assigned_to="coder",
    )

    # Create a mock memory manager that raises on recall
    mock_memory = AsyncMock()
    mock_memory.recall = AsyncMock(side_effect=RuntimeError("Memory DB unavailable"))

    role_config = _make_role(context_includes=["agent_memory"])
    loop = AgentLoop(
        instance_id="coder-1",
        role_config=role_config,
        board=board,
        event_bus=event_bus,
        instance_manager=instance_mgr,
        all_roles={role_config.role: role_config},
        memory_manager=mock_memory,
    )

    # Should NOT raise -- error is caught and logged
    context = await loop.build_context(task)

    # Basic context should still be present
    assert "Coder" in context
    assert task["id"] in context
    # Memory section should NOT be present since recall failed
    assert "Past Lessons" not in context


async def test_build_context_context_provider_failure(
    board: TaskBoard, event_bus: EventBus, instance_mgr: InstanceManager
):
    """build_context should still succeed when context provider raises an exception."""
    group = await board.create_group(title="Feature", created_by="pm")
    task = await board.create_task(
        group_id=group["id"],
        title="Implement endpoint",
        task_type="implementation",
        assigned_to="coder",
    )

    # Create a mock context registry that raises
    mock_registry = MagicMock()
    mock_registry.get_available_providers.side_effect = RuntimeError("Provider crash")

    role_config = _make_role(context_includes=["git_status"])
    loop = AgentLoop(
        instance_id="coder-1",
        role_config=role_config,
        board=board,
        event_bus=event_bus,
        instance_manager=instance_mgr,
        all_roles={role_config.role: role_config},
        context_registry=mock_registry,
    )

    # Should NOT raise -- error is caught and logged
    context = await loop.build_context(task)

    # Basic context should still be present
    assert "Coder" in context
    assert task["id"] in context


async def test_build_context_provider_get_context_failure(
    board: TaskBoard, event_bus: EventBus, instance_mgr: InstanceManager
):
    """build_context should handle failure in get_context (not just get_available_providers)."""
    group = await board.create_group(title="Feature", created_by="pm")
    task = await board.create_task(
        group_id=group["id"],
        title="Implement endpoint",
        task_type="implementation",
        assigned_to="coder",
    )

    # get_available_providers succeeds but get_context fails
    mock_registry = MagicMock()
    mock_registry.get_available_providers.return_value = ["git_status"]
    mock_registry.get_context = AsyncMock(side_effect=RuntimeError("Context fetch failed"))

    role_config = _make_role(context_includes=["git_status"])
    loop = AgentLoop(
        instance_id="coder-1",
        role_config=role_config,
        board=board,
        event_bus=event_bus,
        instance_manager=instance_mgr,
        all_roles={role_config.role: role_config},
        context_registry=mock_registry,
    )

    # Should NOT raise
    context = await loop.build_context(task)
    assert task["id"] in context


# ------------------------------------------------------------------
# Stage-1 completion gate tests (Fix #1 + Fix #2)
# ------------------------------------------------------------------


async def _complete_architect_task_with_no_children(
    board: TaskBoard,
    event_bus: EventBus,
    instance_mgr: InstanceManager,
    *,
    requires_fanout=None,
    fanout_retries: int = 0,
    task_type: str = "tech_design",
):
    """Helper: create an architect task in-progress and run the completion gate."""
    group = await board.create_group(title="F", origin="pm", created_by="human")
    task = await board.create_task(
        group_id=group["id"],
        title="Design something",
        task_type=task_type,
        assigned_to="architect",
        created_by="human",
        requires_fanout=requires_fanout,
    )
    # Move to in_progress (agent has already been working on it).
    await board._db.execute(
        "UPDATE tasks SET status = 'in_progress', claimed_by = ?, "
        "fanout_retries = ? WHERE id = ?",
        ("architect-1", fanout_retries, task["id"]),
    )
    task_row = await board.get_task(task["id"])

    role = _make_role(role="architect", display_name="Architect")
    loop = _make_loop(board, event_bus, instance_mgr,
                      role_config=role, instance_id="architect-1")
    await loop.complete_and_handoff(task_row, "I designed it.")
    return task_row["id"]


async def test_fanout_gate_requeues_tech_design_without_children(
    board: TaskBoard, event_bus: EventBus, instance_mgr: InstanceManager,
):
    """Fix #2: tech_design with zero actionable children is re-queued."""
    task_id = await _complete_architect_task_with_no_children(
        board, event_bus, instance_mgr,
    )
    row = await board.get_task(task_id)
    assert row["status"] == "pending"
    assert row["claimed_by"] is None
    assert row["fanout_retries"] == 1


async def test_fanout_gate_escalates_after_two_retries(
    board: TaskBoard, event_bus: EventBus, instance_mgr: InstanceManager,
):
    """Fix #2: after 2 retries with no fan-out, complete anyway and emit
    escalation event — don't stall the queue forever."""
    task_id = await _complete_architect_task_with_no_children(
        board, event_bus, instance_mgr, fanout_retries=2,
    )
    row = await board.get_task(task_id)
    assert row["status"] == "completed"


async def test_fanout_gate_skips_when_requires_fanout_is_false(
    board: TaskBoard, event_bus: EventBus, instance_mgr: InstanceManager,
):
    """Fix #2: explicit requires_fanout=False (e.g. ADR/research) passes the
    gate even with no children. This is the escape hatch for design-only
    tasks."""
    task_id = await _complete_architect_task_with_no_children(
        board, event_bus, instance_mgr, requires_fanout=False,
    )
    row = await board.get_task(task_id)
    assert row["status"] == "completed"


async def test_fanout_gate_passes_when_actionable_child_exists(
    board: TaskBoard, event_bus: EventBus, instance_mgr: InstanceManager,
):
    """Fix #2: a tech_design with at least one coder child passes."""
    group = await board.create_group(title="F", origin="pm", created_by="human")
    parent = await board.create_task(
        group_id=group["id"], title="Design",
        task_type="tech_design", assigned_to="architect", created_by="human",
    )
    await board._db.execute(
        "UPDATE tasks SET status = 'in_progress', claimed_by = 'architect-1' "
        "WHERE id = ?", (parent["id"],),
    )
    await board.create_task(
        group_id=group["id"], title="Code it",
        task_type="implementation", assigned_to="coder",
        created_by="architect-1", parent_id=parent["id"],
    )
    parent_row = await board.get_task(parent["id"])

    role = _make_role(role="architect", display_name="Architect")
    loop = _make_loop(board, event_bus, instance_mgr,
                      role_config=role, instance_id="architect-1")
    await loop.complete_and_handoff(parent_row, "done")
    row = await board.get_task(parent["id"])
    assert row["status"] == "completed"


async def test_fanout_gate_ignores_non_actionable_children(
    board: TaskBoard, event_bus: EventBus, instance_mgr: InstanceManager,
):
    """Peer-architecture-review children don't count as fan-out — they don't
    produce code, so the gate must still re-queue."""
    group = await board.create_group(title="F", origin="pm", created_by="human")
    parent = await board.create_task(
        group_id=group["id"], title="Design",
        task_type="tech_design", assigned_to="architect", created_by="human",
    )
    await board._db.execute(
        "UPDATE tasks SET status = 'in_progress', claimed_by = 'architect-1' "
        "WHERE id = ?", (parent["id"],),
    )
    # Peer review task — same role, not coder/verifier.
    await board.create_task(
        group_id=group["id"], title="Review",
        task_type="architecture_review", assigned_to="architect",
        created_by="architect-1", parent_id=parent["id"],
    )
    parent_row = await board.get_task(parent["id"])
    role = _make_role(role="architect", display_name="Architect")
    loop = _make_loop(board, event_bus, instance_mgr,
                      role_config=role, instance_id="architect-1")
    await loop.complete_and_handoff(parent_row, "done")
    row = await board.get_task(parent["id"])
    assert row["status"] == "pending"
    assert row["fanout_retries"] == 1


async def test_merge_gate_auto_creates_verifier_task(
    board: TaskBoard, event_bus: EventBus, instance_mgr: InstanceManager,
    monkeypatch,
):
    """Fix #1: completing an implementation task with no VR child and a
    substantial diff auto-creates a verifier task."""
    group = await board.create_group(title="F", origin="pm", created_by="human")
    architect_task = await board.create_task(
        group_id=group["id"], title="Design",
        task_type="tech_design", assigned_to="architect", created_by="human",
    )
    impl = await board.create_task(
        group_id=group["id"], title="Implement",
        task_type="implementation", assigned_to="coder",
        created_by="architect-1", parent_id=architect_task["id"],
    )
    await board._db.execute(
        "UPDATE tasks SET status = 'in_progress', claimed_by = 'coder-1' "
        "WHERE id = ?", (impl["id"],),
    )

    role = _make_role(role="coder", display_name="Coder")
    loop = _make_loop(board, event_bus, instance_mgr,
                      role_config=role, instance_id="coder-1")

    # Simulate a 100-LOC diff — large enough to demand verification.
    async def fake_count(worktree_path, branch_name):
        return 100
    monkeypatch.setattr(loop, "_count_changed_loc", fake_count)

    impl_row = await board.get_task(impl["id"])
    await loop.complete_and_handoff(impl_row, "I wrote the feature.")

    # Verify the task completed AND a verifier child was auto-created.
    impl_after = await board.get_task(impl["id"])
    assert impl_after["status"] == "completed"

    verifier_children = await board._db.execute_fetchall(
        "SELECT id, assigned_to, status FROM tasks "
        "WHERE parent_id = ? AND assigned_to = 'verifier'",
        (impl["id"],),
    )
    assert len(verifier_children) == 1, (
        f"expected auto-created VR, got {verifier_children}"
    )
    assert verifier_children[0]["status"] == "pending"


async def test_merge_gate_skips_tiny_diffs(
    board: TaskBoard, event_bus: EventBus, instance_mgr: InstanceManager,
    monkeypatch,
):
    """Fix #1: a <20 LOC bug-fix shouldn't force a verifier task. CD-032's
    3-line pyproject tweak is the canonical example."""
    group = await board.create_group(title="F", origin="pm", created_by="human")
    impl = await board.create_task(
        group_id=group["id"], title="3-line fix",
        task_type="bug_fix", assigned_to="coder", created_by="human",
    )
    await board._db.execute(
        "UPDATE tasks SET status = 'in_progress', claimed_by = 'coder-1' "
        "WHERE id = ?", (impl["id"],),
    )

    role = _make_role(role="coder", display_name="Coder")
    loop = _make_loop(board, event_bus, instance_mgr,
                      role_config=role, instance_id="coder-1")

    async def fake_count(worktree_path, branch_name):
        return 3
    monkeypatch.setattr(loop, "_count_changed_loc", fake_count)

    impl_row = await board.get_task(impl["id"])
    await loop.complete_and_handoff(impl_row, "fixed the typo")

    children = await board._db.execute_fetchall(
        "SELECT id FROM tasks WHERE parent_id = ?", (impl["id"],),
    )
    assert children == []


async def test_merge_gate_does_not_double_create_when_vr_exists(
    board: TaskBoard, event_bus: EventBus, instance_mgr: InstanceManager,
    monkeypatch,
):
    """Fix #1: if the coder already created a VR, don't add a second one."""
    group = await board.create_group(title="F", origin="pm", created_by="human")
    impl = await board.create_task(
        group_id=group["id"], title="Implement",
        task_type="implementation", assigned_to="coder", created_by="human",
    )
    await board._db.execute(
        "UPDATE tasks SET status = 'in_progress', claimed_by = 'coder-1' "
        "WHERE id = ?", (impl["id"],),
    )
    # Coder manually created its VR — gate should respect it.
    await board.create_task(
        group_id=group["id"], title="Verify",
        task_type="verification", assigned_to="verifier",
        created_by="coder-1", parent_id=impl["id"],
    )

    role = _make_role(role="coder", display_name="Coder")
    loop = _make_loop(board, event_bus, instance_mgr,
                      role_config=role, instance_id="coder-1")

    async def fake_count(worktree_path, branch_name):
        return 500
    monkeypatch.setattr(loop, "_count_changed_loc", fake_count)

    impl_row = await board.get_task(impl["id"])
    await loop.complete_and_handoff(impl_row, "done")

    vrs = await board._db.execute_fetchall(
        "SELECT id FROM tasks WHERE parent_id = ? AND assigned_to = 'verifier'",
        (impl["id"],),
    )
    assert len(vrs) == 1
