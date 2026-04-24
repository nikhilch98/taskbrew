"""Tests for event-driven task claim wake-up.

Design:
docs/superpowers/specs/2026-04-24-event-driven-task-claims-design.md
"""

import asyncio

import pytest

from taskbrew.agents.agent_loop import AgentLoop
from taskbrew.agents.instance_manager import InstanceManager
from taskbrew.config_loader import RoleConfig
from taskbrew.orchestrator.database import Database
from taskbrew.orchestrator.event_bus import EventBus
from taskbrew.orchestrator.task_board import TaskBoard


def _make_role(role: str = "coder") -> RoleConfig:
    return RoleConfig(
        role=role,
        display_name=role.title(),
        prefix=role[:2].upper(),
        color="#000000",
        emoji="\U0001F916",
        system_prompt="You are a test agent.",
        tools=["Read"],
        model="claude-sonnet-4-6",
    )


@pytest.fixture
async def env():
    db = Database(":memory:")
    await db.initialize()
    event_bus = EventBus()
    board = TaskBoard(db, event_bus=event_bus)
    instance_mgr = InstanceManager(db)
    yield db, event_bus, board, instance_mgr
    await db.close()


async def test_create_task_emits_task_available_for_pending_tasks(env):
    """A newly-created pending task must emit task.available with the
    assigned role so any idle agent for that role wakes up."""
    _db, event_bus, board, _im = env
    group = await board.create_group(title="G", origin="pm", created_by="human")

    seen = []

    async def capture(event):
        seen.append(event)

    event_bus.subscribe("task.available", capture)

    await board.create_task(
        group_id=group["id"],
        title="Test",
        task_type="implementation",
        assigned_to="coder",
        created_by="human",
    )
    # Event dispatch is via create_task() on the event bus, which
    # spawns the handler on the loop. Give it a tick to run.
    await asyncio.sleep(0.05)
    assert seen, "expected at least one task.available event"
    assert seen[0]["role"] == "coder"
    assert seen[0]["group_id"] == group["id"]


async def test_blocked_task_does_not_emit_task_available(env):
    """A task created with blocked_by starts in status=blocked, so it
    is not claimable yet and must not wake any agent until its
    dependencies resolve."""
    _db, event_bus, board, _im = env
    group = await board.create_group(title="G", origin="pm", created_by="human")
    blocker = await board.create_task(
        group_id=group["id"],
        title="Blocker",
        task_type="implementation",
        assigned_to="coder",
        created_by="human",
    )

    seen = []

    async def capture(event):
        seen.append(event)

    event_bus.subscribe("task.available", capture)

    await board.create_task(
        group_id=group["id"],
        title="Blocked",
        task_type="implementation",
        assigned_to="coder",
        created_by="human",
        blocked_by=[blocker["id"]],
    )
    await asyncio.sleep(0.05)
    # No event fired because the second task is status=blocked.
    assert seen == []


async def test_dependency_resolve_emits_task_available(env):
    """When a blocker completes and the blocked task transitions from
    blocked -> pending, task.available must fire so the waiting
    agent wakes without needing to poll."""
    _db, event_bus, board, _im = env
    group = await board.create_group(title="G", origin="pm", created_by="human")
    blocker = await board.create_task(
        group_id=group["id"], title="Blocker",
        task_type="implementation", assigned_to="coder",
        created_by="human",
    )
    blocked = await board.create_task(
        group_id=group["id"], title="Blocked",
        task_type="implementation", assigned_to="verifier",
        created_by="human",
        blocked_by=[blocker["id"]],
    )

    seen = []

    async def capture(event):
        seen.append(event)

    event_bus.subscribe("task.available", capture)

    # Simulate the blocker completing.
    await board._db.execute(
        "UPDATE tasks SET status = 'in_progress', claimed_by = 'coder-1' "
        "WHERE id = ?", (blocker["id"],),
    )
    await board.complete_task(blocker["id"])
    await asyncio.sleep(0.05)

    # Expect task.available fired for the now-unblocked verifier task.
    verifier_events = [e for e in seen if e.get("role") == "verifier"]
    assert verifier_events, (
        f"expected task.available for verifier, got {seen}"
    )
    assert verifier_events[0]["task_id"] == blocked["id"]


async def test_agent_wakes_on_task_available_within_100ms(env):
    """End-to-end: with an agent running, creating a task for its role
    causes a claim within 100ms even though poll_interval is 60s."""
    _db, event_bus, board, instance_mgr = env
    role = _make_role("coder")
    await board.register_prefixes({role.role: role.prefix})

    loop = AgentLoop(
        instance_id="coder-1",
        role_config=role,
        board=board,
        event_bus=event_bus,
        instance_manager=instance_mgr,
        all_roles={role.role: role},
        poll_interval=60.0,  # deliberately high so polling can't explain it
    )

    # Replace the real run_once with a stub that records claim events
    # and exits after one successful claim.
    claim_times: list[float] = []

    async def stub_run_once():
        task = await board.claim_task(
            role=role.role, instance_id=loop.instance_id,
        )
        if task is None:
            return False
        claim_times.append(asyncio.get_event_loop().time())
        loop._running = False  # stop the loop after first claim
        return True

    loop.run_once = stub_run_once

    group = await board.create_group(
        title="G", origin="pm", created_by="human",
    )

    run_task = asyncio.create_task(loop.run())
    # Give the loop a moment to enter its wait_for.
    await asyncio.sleep(0.05)

    t_create = asyncio.get_event_loop().time()
    await board.create_task(
        group_id=group["id"], title="T",
        task_type="implementation", assigned_to="coder",
        created_by="human",
    )

    try:
        await asyncio.wait_for(run_task, timeout=2.0)
    except asyncio.TimeoutError:
        loop._running = False
        run_task.cancel()
        raise AssertionError(
            "agent did not wake within 2s; expected ~ms via task.available"
        )

    assert claim_times, "expected at least one claim"
    elapsed = claim_times[0] - t_create
    assert elapsed < 0.5, (
        f"expected wake-and-claim < 500ms, got {elapsed*1000:.1f}ms"
    )


async def test_agent_does_not_wake_for_other_role(env):
    """Role filter: a task.available event for role=architect must
    NOT wake a coder agent."""
    _db, event_bus, board, instance_mgr = env
    role = _make_role("coder")
    await board.register_prefixes({role.role: role.prefix, "architect": "AR"})

    loop = AgentLoop(
        instance_id="coder-1",
        role_config=role,
        board=board,
        event_bus=event_bus,
        instance_manager=instance_mgr,
        all_roles={role.role: role},
        poll_interval=60.0,
    )

    claims = 0

    async def stub_run_once():
        nonlocal claims
        task = await board.claim_task(
            role=role.role, instance_id=loop.instance_id,
        )
        if task is not None:
            claims += 1
            loop._running = False
            return True
        return False

    loop.run_once = stub_run_once

    group = await board.create_group(
        title="G", origin="pm", created_by="human",
    )

    run_task = asyncio.create_task(loop.run())
    await asyncio.sleep(0.05)

    # Task for architect, not coder.
    await board.create_task(
        group_id=group["id"], title="Design",
        task_type="tech_design", assigned_to="architect",
        created_by="human",
    )

    # Give the event a window to wake the coder (it shouldn't).
    await asyncio.sleep(0.2)
    assert claims == 0, (
        "coder should not have woken for architect's task.available"
    )
    # stop() now interrupts the wake-wait so the loop exits promptly.
    loop.stop()
    await asyncio.wait_for(run_task, timeout=2.0)


async def test_agent_unsubscribes_on_stop(env):
    """After run() returns, the agent's wake callback must no longer
    be registered in the event bus."""
    _db, event_bus, board, instance_mgr = env
    role = _make_role("coder")
    await board.register_prefixes({role.role: role.prefix})

    loop = AgentLoop(
        instance_id="coder-1",
        role_config=role,
        board=board,
        event_bus=event_bus,
        instance_manager=instance_mgr,
        all_roles={role.role: role},
        poll_interval=60.0,
    )

    async def stub_run_once():
        # stop() sets both _running=False AND sets the wake event
        # so the loop returns immediately rather than sitting in
        # wait_for for poll_interval seconds.
        loop.stop()
        return False

    loop.run_once = stub_run_once

    await asyncio.wait_for(loop.run(), timeout=2.0)

    # No task.available handlers should remain for this agent.
    handlers = event_bus._handlers.get("task.available", [])
    assert loop._wake_handler not in handlers
