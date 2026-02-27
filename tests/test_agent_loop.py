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
