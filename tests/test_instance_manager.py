"""Tests for the agent InstanceManager."""

from __future__ import annotations

import pytest

from ai_team.config_loader import RoleConfig
from ai_team.orchestrator.database import Database
from ai_team.orchestrator.task_board import TaskBoard
from ai_team.agents.instance_manager import InstanceManager


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _make_role(role: str = "coder", display_name: str = "Coder") -> RoleConfig:
    """Create a minimal RoleConfig for testing."""
    return RoleConfig(
        role=role,
        display_name=display_name,
        prefix="CD",
        color="#00ff00",
        emoji="",
        system_prompt="You are a coder.",
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
async def mgr(db: Database) -> InstanceManager:
    """Create an InstanceManager backed by the in-memory database."""
    return InstanceManager(db)


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------


async def test_register_instance(mgr: InstanceManager):
    """register_instance should insert a row and return the instance dict."""
    role_cfg = _make_role()
    result = await mgr.register_instance("coder-1", role_cfg)

    assert result["instance_id"] == "coder-1"
    assert result["role"] == "coder"
    assert result["status"] == "idle"
    assert result["current_task"] is None
    assert result["started_at"] is not None
    assert result["last_heartbeat"] is None


async def test_update_status(mgr: InstanceManager, db: Database):
    """update_status should change the status and current_task."""
    role_cfg = _make_role()
    await mgr.register_instance("coder-1", role_cfg)

    # Create a real task so the foreign key constraint is satisfied.
    board = TaskBoard(db, group_prefixes={"pm": "FEAT"})
    await board.register_prefixes({"coder": "CD"})
    group = await board.create_group(title="Feature", created_by="pm")
    task = await board.create_task(
        group_id=group["id"],
        title="Implement X",
        task_type="implementation",
        assigned_to="coder",
    )

    updated = await mgr.update_status("coder-1", "working", current_task=task["id"])

    assert updated["status"] == "working"
    assert updated["current_task"] == task["id"]


async def test_get_all_instances(mgr: InstanceManager):
    """get_all_instances should return all registered instances."""
    await mgr.register_instance("coder-1", _make_role("coder"))
    await mgr.register_instance("tester-1", _make_role("tester", "Tester"))

    instances = await mgr.get_all_instances()

    assert len(instances) == 2


async def test_heartbeat(mgr: InstanceManager):
    """heartbeat should update last_heartbeat to a non-None value."""
    role_cfg = _make_role()
    await mgr.register_instance("coder-1", role_cfg)

    # Initially last_heartbeat is None.
    instance = await mgr.get_instance("coder-1")
    assert instance is not None
    assert instance["last_heartbeat"] is None

    await mgr.heartbeat("coder-1")

    instance = await mgr.get_instance("coder-1")
    assert instance is not None
    assert instance["last_heartbeat"] is not None


async def test_remove_instance(mgr: InstanceManager):
    """remove_instance should delete the row; get_instance should return None."""
    role_cfg = _make_role()
    await mgr.register_instance("coder-1", role_cfg)

    await mgr.remove_instance("coder-1")

    result = await mgr.get_instance("coder-1")
    assert result is None
