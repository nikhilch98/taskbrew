"""Tests for the AutoScaler."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from taskbrew.agents.auto_scaler import AutoScaler
from taskbrew.agents.instance_manager import InstanceManager
from taskbrew.config_loader import AutoScaleConfig, RoleConfig
from taskbrew.orchestrator.database import Database
from taskbrew.orchestrator.task_board import TaskBoard


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _make_role(
    role: str = "coder",
    display_name: str = "Coder",
    prefix: str = "CD",
    max_instances: int = 5,
    auto_scale: AutoScaleConfig | None = None,
) -> RoleConfig:
    """Create a RoleConfig with optional auto-scale settings."""
    return RoleConfig(
        role=role,
        display_name=display_name,
        prefix=prefix,
        color="#00ff00",
        emoji="",
        system_prompt="You are a coder.",
        max_instances=max_instances,
        auto_scale=auto_scale,
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
async def task_board(db: Database) -> TaskBoard:
    """Create a TaskBoard backed by the in-memory database."""
    board = TaskBoard(db, group_prefixes={"pm": "FEAT"})
    await board.register_prefixes({"coder": "CD", "pm": "PM"})
    return board


@pytest.fixture
async def instance_mgr(db: Database) -> InstanceManager:
    """Create an InstanceManager backed by the in-memory database."""
    return InstanceManager(db)


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------


async def test_no_scale_when_disabled(
    task_board: TaskBoard, instance_mgr: InstanceManager
):
    """Roles without auto_scale enabled are skipped entirely."""
    role = _make_role(auto_scale=None)
    roles = {"coder": role}

    factory_calls = []

    async def factory(iid, rcfg):
        factory_calls.append(iid)

    scaler = AutoScaler(
        task_board, instance_mgr, roles,
        agent_factory=factory,
    )

    # Manually trigger a scaling check
    await scaler._check_and_scale()

    # Factory should never be called since auto_scale is not enabled
    assert factory_calls == []


async def test_scale_up_calls_factory(
    task_board: TaskBoard, instance_mgr: InstanceManager
):
    """When pending > threshold, the agent_factory callback is called."""
    auto_cfg = AutoScaleConfig(enabled=True, scale_up_threshold=1)
    role = _make_role(max_instances=5, auto_scale=auto_cfg)
    roles = {"coder": role}

    # Register one existing instance
    await instance_mgr.register_instance("coder-1", role)

    # Create a group and multiple pending tasks to exceed threshold
    group = await task_board.create_group(title="Feature", created_by="pm")
    for i in range(3):
        await task_board.create_task(
            group_id=group["id"],
            title=f"Task {i}",
            task_type="implementation",
            assigned_to="coder",
        )

    factory_calls = []

    async def factory(iid, rcfg):
        factory_calls.append(iid)
        return asyncio.current_task()

    scaler = AutoScaler(
        task_board, instance_mgr, roles,
        agent_factory=factory,
    )

    await scaler._check_and_scale()

    # 3 pending > threshold of 1, active = 1, max = 5
    # needed = min(3-1, 5-1) = min(2, 4) = 2
    assert len(factory_calls) == 2
    assert "coder-auto-1" in factory_calls
    assert "coder-auto-2" in factory_calls


async def test_scale_down_calls_stopper(
    task_board: TaskBoard, instance_mgr: InstanceManager, db: Database
):
    """When pending=0 and extra>0, the agent_stopper callback is called for idle agents."""
    auto_cfg = AutoScaleConfig(enabled=True, scale_up_threshold=1)
    role = _make_role(max_instances=5, auto_scale=auto_cfg)
    roles = {"coder": role}

    # Register idle instances
    await instance_mgr.register_instance("coder-1", role)
    await instance_mgr.register_instance("coder-auto-1", role)

    # Backdate heartbeats so they appear idle for > 5 minutes
    old_time = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    await db.execute(
        "UPDATE agent_instances SET last_heartbeat = ? WHERE instance_id = ?",
        (old_time, "coder-1"),
    )
    await db.execute(
        "UPDATE agent_instances SET last_heartbeat = ? WHERE instance_id = ?",
        (old_time, "coder-auto-1"),
    )

    stopper_calls = []

    async def stopper(iid):
        stopper_calls.append(iid)

    scaler = AutoScaler(
        task_board, instance_mgr, roles,
        agent_stopper=stopper,
    )

    # Simulate that we previously scaled up by 1
    scaler._active_extra["coder"] = 1

    # No pending tasks, so scale-down should trigger
    await scaler._check_and_scale()

    assert len(stopper_calls) == 1
    # One of the idle instances should have been stopped
    assert stopper_calls[0] in ("coder-1", "coder-auto-1")
    assert scaler._active_extra["coder"] == 0


async def test_no_scale_at_max_instances(
    task_board: TaskBoard, instance_mgr: InstanceManager
):
    """Won't exceed max_instances even when many tasks are pending."""
    auto_cfg = AutoScaleConfig(enabled=True, scale_up_threshold=1)
    role = _make_role(max_instances=2, auto_scale=auto_cfg)
    roles = {"coder": role}

    # Register 2 instances (already at max)
    await instance_mgr.register_instance("coder-1", role)
    await instance_mgr.register_instance("coder-2", role)

    # Create many pending tasks
    group = await task_board.create_group(title="Feature", created_by="pm")
    for i in range(10):
        await task_board.create_task(
            group_id=group["id"],
            title=f"Task {i}",
            task_type="implementation",
            assigned_to="coder",
        )

    factory_calls = []

    async def factory(iid, rcfg):
        factory_calls.append(iid)
        return asyncio.current_task()

    scaler = AutoScaler(
        task_board, instance_mgr, roles,
        agent_factory=factory,
    )

    await scaler._check_and_scale()

    # active_count (2) == max_instances (2), so no scale-up
    assert factory_calls == []


async def test_scale_up_cooldown(
    task_board: TaskBoard, instance_mgr: InstanceManager
):
    """Scale-up should be blocked during the cooldown period."""
    auto_cfg = AutoScaleConfig(enabled=True, scale_up_threshold=1)
    role = _make_role(max_instances=10, auto_scale=auto_cfg)
    roles = {"coder": role}

    await instance_mgr.register_instance("coder-1", role)

    group = await task_board.create_group(title="Feature", created_by="pm")
    for i in range(5):
        await task_board.create_task(
            group_id=group["id"],
            title=f"Task {i}",
            task_type="implementation",
            assigned_to="coder",
        )

    factory_calls = []

    async def factory(iid, rcfg):
        factory_calls.append(iid)
        return asyncio.current_task()

    # Use a long cooldown (1 hour) so the second call is still within cooldown
    scaler = AutoScaler(
        task_board, instance_mgr, roles,
        agent_factory=factory,
        cooldown_seconds=3600,
    )

    # First check triggers scale-up
    await scaler._check_and_scale()
    first_count = len(factory_calls)
    assert first_count > 0

    # Second check within cooldown should NOT trigger more scale-up
    await scaler._check_and_scale()
    assert len(factory_calls) == first_count  # No additional calls


async def test_scale_down_respects_idle_threshold(
    task_board: TaskBoard, instance_mgr: InstanceManager
):
    """Scale-down should not stop agents that haven't been idle long enough."""
    auto_cfg = AutoScaleConfig(enabled=True, scale_up_threshold=1)
    role = _make_role(max_instances=5, auto_scale=auto_cfg)
    roles = {"coder": role}

    # Register instances that were JUST created (not idle long enough)
    await instance_mgr.register_instance("coder-1", role)
    await instance_mgr.register_instance("coder-auto-1", role)

    # The default started_at is "now", so they haven't been idle for 5 minutes

    stopper_calls = []

    async def stopper(iid):
        stopper_calls.append(iid)

    # Use default idle_threshold_seconds (300 = 5 minutes)
    scaler = AutoScaler(
        task_board, instance_mgr, roles,
        agent_stopper=stopper,
    )
    scaler._active_extra["coder"] = 1

    await scaler._check_and_scale()

    # No agents should be stopped because they're not idle long enough
    assert len(stopper_calls) == 0
    # Extra count should remain unchanged
    assert scaler._active_extra["coder"] == 1


async def test_scale_down_cooldown(
    task_board: TaskBoard, instance_mgr: InstanceManager, db: Database
):
    """Scale-down should be blocked during the cooldown period."""
    auto_cfg = AutoScaleConfig(enabled=True, scale_up_threshold=1)
    role = _make_role(max_instances=5, auto_scale=auto_cfg)
    roles = {"coder": role}

    # Register instances
    await instance_mgr.register_instance("coder-1", role)
    await instance_mgr.register_instance("coder-auto-1", role)
    await instance_mgr.register_instance("coder-auto-2", role)

    # Backdate heartbeats so they appear idle for > 5 minutes
    old_time = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    for iid in ("coder-1", "coder-auto-1", "coder-auto-2"):
        await db.execute(
            "UPDATE agent_instances SET last_heartbeat = ? WHERE instance_id = ?",
            (old_time, iid),
        )

    stopper_calls = []

    async def stopper(iid):
        stopper_calls.append(iid)

    # Use a long cooldown so second call is within cooldown
    scaler = AutoScaler(
        task_board, instance_mgr, roles,
        agent_stopper=stopper,
        cooldown_seconds=3600,
    )
    scaler._active_extra["coder"] = 2

    # First check triggers scale-down
    await scaler._check_and_scale()
    first_count = len(stopper_calls)
    assert first_count > 0

    # Second check within cooldown should NOT trigger more scale-down
    await scaler._check_and_scale()
    assert len(stopper_calls) == first_count


async def test_idle_seconds_calculation():
    """_idle_seconds correctly computes time since last heartbeat."""
    ten_min_ago = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    instance = {"status": "idle", "last_heartbeat": ten_min_ago, "started_at": ten_min_ago}
    idle = AutoScaler._idle_seconds(instance)
    # Should be approximately 600 seconds (10 minutes)
    assert 580 < idle < 620

    # Non-idle instance returns 0
    working_instance = {"status": "working", "last_heartbeat": ten_min_ago, "started_at": ten_min_ago}
    assert AutoScaler._idle_seconds(working_instance) == 0.0

    # Instance with no timestamps returns 0
    no_ts = {"status": "idle", "last_heartbeat": None, "started_at": None}
    assert AutoScaler._idle_seconds(no_ts) == 0.0


async def test_get_scaling_status(
    task_board: TaskBoard, instance_mgr: InstanceManager
):
    """get_scaling_status returns current extra instance counts."""
    role = _make_role(auto_scale=None)
    roles = {"coder": role}

    scaler = AutoScaler(task_board, instance_mgr, roles)
    scaler._active_extra["coder"] = 3

    status = scaler.get_scaling_status()
    assert status["extra_instances"] == {"coder": 3}
    assert status["running"] is False
