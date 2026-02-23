"""Unit tests for AgentLoop methods."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

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
        model="claude-sonnet-4-20250514",
        routes_to=routes_to or [],
        context_includes=context_includes or [],
    )


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture
async def db():
    database = Database(":memory:")
    await database.initialize()
    yield database
    await database.close()


@pytest.fixture
async def board(db):
    b = TaskBoard(db, group_prefixes={"pm": "FEAT"})
    await b.register_prefixes({"pm": "PM", "coder": "CD", "architect": "AR", "tester": "TS", "reviewer": "RV"})
    return b


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
    **kwargs,
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
        **kwargs,
    )


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------


async def test_build_context_includes_task_info(
    board: TaskBoard, event_bus: EventBus, instance_mgr: InstanceManager
):
    """build_context should return a string containing the task ID, title,
    task_type, priority, and group_id."""
    group = await board.create_group(title="Feature Alpha", created_by="pm")
    task = await board.create_task(
        group_id=group["id"],
        title="Implement user login",
        task_type="implementation",
        assigned_to="coder",
        description="Build the login form and backend auth handler.",
        priority="high",
    )

    role_config = _make_role()
    loop = _make_loop(board, event_bus, instance_mgr, role_config=role_config)

    context = await loop.build_context(task)

    assert isinstance(context, str)
    assert task["id"] in context
    assert "Implement user login" in context
    assert "implementation" in context
    assert "high" in context
    assert group["id"] in context
    assert "Build the login form" in context
    # Role display name and instance id should appear
    assert "Coder" in context
    assert "coder-1" in context


async def test_build_context_handles_provider_failure(
    board: TaskBoard, event_bus: EventBus, instance_mgr: InstanceManager
):
    """build_context should succeed even when a context provider raises an exception."""
    group = await board.create_group(title="Feature Beta", created_by="pm")
    task = await board.create_task(
        group_id=group["id"],
        title="Implement endpoint",
        task_type="implementation",
        assigned_to="coder",
    )

    # Create a mock context registry where get_available_providers works
    # but get_context raises an exception
    mock_registry = MagicMock()
    mock_registry.get_available_providers.return_value = ["git_status"]
    mock_registry.get_context = AsyncMock(side_effect=RuntimeError("Provider exploded"))

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

    # Should NOT raise -- the error is caught and logged
    context = await loop.build_context(task)

    # Basic context should still be present
    assert task["id"] in context
    assert "Implement endpoint" in context


async def test_complete_and_handoff_skips_duplicate(
    board: TaskBoard, event_bus: EventBus, instance_mgr: InstanceManager
):
    """When downstream (child) tasks already exist, complete_and_handoff should
    still complete the current task but log a warning about existing children.
    The duplicate guard checks parent_id (not revision_of)."""
    group = await board.create_group(title="Dup Guard", created_by="pm")
    original = await board.create_task(
        group_id=group["id"],
        title="Original task",
        task_type="implementation",
        assigned_to="coder",
    )

    # Create a downstream/child task pointing to the original via parent_id
    _child = await board.create_task(
        group_id=group["id"],
        title="Child of original",
        task_type="code_review",
        assigned_to="coder",
        parent_id=original["id"],
    )

    loop = _make_loop(board, event_bus, instance_mgr)

    # Spy on complete_task_with_output to verify it IS still called
    original_complete = board.complete_task_with_output
    board.complete_task_with_output = AsyncMock()

    await loop.complete_and_handoff(original, "output text")

    # Should still complete the task even though downstream tasks exist
    board.complete_task_with_output.assert_called_once_with(original["id"], "output text")

    # Restore
    board.complete_task_with_output = original_complete


async def test_complete_and_handoff_emits_event(
    board: TaskBoard, event_bus: EventBus, instance_mgr: InstanceManager
):
    """complete_and_handoff should call event_bus.emit with 'task.completed'."""
    group = await board.create_group(title="Event Emit", created_by="pm")
    task = await board.create_task(
        group_id=group["id"],
        title="Task for event",
        task_type="implementation",
        assigned_to="coder",
    )

    # Replace event_bus.emit with a spy
    original_emit = event_bus.emit
    event_bus.emit = AsyncMock()

    loop = _make_loop(board, event_bus, instance_mgr)

    await loop.complete_and_handoff(task, "done output")

    # Verify emit was called with task.completed
    event_bus.emit.assert_called()
    call_args_list = event_bus.emit.call_args_list
    event_types = [call.args[0] for call in call_args_list]
    assert "task.completed" in event_types

    # Check the data passed to the task.completed event
    for call in call_args_list:
        if call.args[0] == "task.completed":
            data = call.args[1]
            assert data["task_id"] == task["id"]
            assert data["group_id"] == task["group_id"]
            assert data["agent_id"] == "coder-1"
            break

    # Restore
    event_bus.emit = original_emit


async def test_agent_loop_init_stores_config(
    board: TaskBoard, event_bus: EventBus, instance_mgr: InstanceManager
):
    """The AgentLoop constructor should store all parameters correctly."""
    role_config = _make_role(
        role="architect",
        display_name="Architect",
    )
    all_roles = {"architect": role_config}

    loop = AgentLoop(
        instance_id="architect-1",
        role_config=role_config,
        board=board,
        event_bus=event_bus,
        instance_manager=instance_mgr,
        all_roles=all_roles,
        cli_path="/usr/local/bin/claude",
        project_dir="/tmp/project",
        poll_interval=10.0,
        api_url="http://localhost:9999",
    )

    assert loop.instance_id == "architect-1"
    assert loop.role_config is role_config
    assert loop.role_config.role == "architect"
    assert loop.role_config.display_name == "Architect"
    assert loop.board is board
    assert loop.event_bus is event_bus
    assert loop.instance_manager is instance_mgr
    assert loop.all_roles is all_roles
    assert loop.cli_path == "/usr/local/bin/claude"
    assert loop.project_dir == "/tmp/project"
    assert loop.poll_interval == 10.0
    assert loop.api_url == "http://localhost:9999"
    assert loop._running is False
    assert loop.worktree_manager is None
    assert loop.memory_manager is None
    assert loop.context_registry is None


async def test_cli_provider_forwarded_to_agent_config(
    board: TaskBoard, event_bus: EventBus, instance_mgr: InstanceManager
):
    """cli_provider should be forwarded through AgentLoop to AgentConfig."""
    rc = _make_role()
    loop = _make_loop(
        board, event_bus, instance_mgr,
        role_config=rc,
        cli_provider="gemini",
    )
    assert loop.cli_provider == "gemini"

    # Default should be "claude"
    loop2 = _make_loop(board, event_bus, instance_mgr, role_config=rc)
    assert loop2.cli_provider == "claude"


def test_provider_detect():
    """detect_provider should infer provider from model name."""
    from taskbrew.agents.provider import detect_provider

    assert detect_provider(model="gemini-3.1-pro-preview") == "gemini"
    assert detect_provider(model="claude-opus-4-6") == "claude"
    assert detect_provider(cli_provider="gemini") == "gemini"
    assert detect_provider() == "claude"


async def test_build_context_open_routing_injects_manifest(
    board: TaskBoard, event_bus: EventBus, instance_mgr: InstanceManager
):
    """Open routing mode should inject all available agents into context."""
    pm_role = _make_role(
        role="pm",
        display_name="PM",
    )
    # Override fields that _make_role doesn't expose
    pm_role.prefix = "PM"
    pm_role.routing_mode = "open"
    pm_role.accepts = ["task_group"]

    arch_role = _make_role(
        role="architect",
        display_name="Architect",
    )
    arch_role.prefix = "AR"
    arch_role.routing_mode = "open"
    arch_role.accepts = ["tech_design"]

    coder_role = _make_role(
        role="coder",
        display_name="Coder",
    )
    coder_role.prefix = "CD"
    coder_role.routing_mode = "open"
    coder_role.accepts = ["implementation", "bug_fix"]

    all_roles = {"pm": pm_role, "architect": arch_role, "coder": coder_role}

    loop = AgentLoop(
        instance_id="pm-1",
        role_config=pm_role,
        board=board,
        event_bus=event_bus,
        instance_manager=instance_mgr,
        all_roles=all_roles,
    )

    group = await board.create_group(title="Manifest Test", created_by="pm")
    task = await board.create_task(
        group_id=group["id"],
        title="Test",
        task_type="task_group",
        assigned_to="pm",
        description="Test task",
    )

    context = await loop.build_context(task)

    assert "## Available Agents" in context
    assert 'assigned_to="architect"' in context
    assert 'assigned_to="coder"' in context
    assert 'assigned_to="pm"' not in context  # should not include self


def test_provider_model_mapping():
    """_model_for_role should return correct models per provider."""
    from taskbrew.project_manager import _model_for_role

    assert _model_for_role("pm", "claude") == "claude-opus-4-6"
    assert _model_for_role("architect", "claude") == "claude-opus-4-6"
    assert _model_for_role("coder", "claude") == "claude-sonnet-4-6"
    assert _model_for_role("verifier", "claude") == "claude-sonnet-4-6"

    assert _model_for_role("pm", "gemini") == "gemini-3.1-pro-preview"
    assert _model_for_role("architect", "gemini") == "gemini-3.1-pro-preview"
    assert _model_for_role("coder", "gemini") == "gemini-3-flash-preview"
    assert _model_for_role("verifier", "gemini") == "gemini-3-flash-preview"
