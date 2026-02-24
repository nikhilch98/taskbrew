"""Tests for TaskBoard: group/task CRUD and dependency resolution."""

from __future__ import annotations

import pytest

from ai_team.orchestrator.database import Database
from ai_team.orchestrator.task_board import TaskBoard


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
    tb = TaskBoard(
        db,
        group_prefixes={"pm": "FEAT", "architect": "DEBT"},
    )
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


# ------------------------------------------------------------------
# Task 5: Group CRUD
# ------------------------------------------------------------------


async def test_create_group(board: TaskBoard):
    """create_group should return a dict with auto-generated ID and fields."""
    group = await board.create_group(
        title="Implement login feature",
        origin="user-request",
        created_by="pm",
    )
    assert group["id"] == "FEAT-001"
    assert group["title"] == "Implement login feature"
    assert group["origin"] == "user-request"
    assert group["status"] == "active"
    assert group["created_by"] == "pm"
    assert group["created_at"] is not None
    assert group["completed_at"] is None


# ------------------------------------------------------------------
# Task 5: Task CRUD
# ------------------------------------------------------------------


async def test_create_task(board: TaskBoard):
    """create_task should return a dict with auto-generated ID and correct fields."""
    group = await board.create_group(title="Feature A", created_by="pm")
    task = await board.create_task(
        group_id=group["id"],
        title="Design API",
        task_type="tech_design",
        assigned_to="architect",
        created_by="pm",
    )
    # The ID uses the architect prefix because assigned_to="architect".
    assert task["id"] == "AR-001"
    assert task["status"] == "pending"
    assert task["group_id"] == group["id"]
    assert task["assigned_to"] == "architect"
    assert task["created_by"] == "pm"


async def test_create_task_with_parent(board: TaskBoard):
    """A task with parent_id should store the reference."""
    group = await board.create_group(title="Feature B", created_by="pm")
    parent = await board.create_task(
        group_id=group["id"],
        title="Parent task",
        task_type="tech_design",
        assigned_to="architect",
    )
    child = await board.create_task(
        group_id=group["id"],
        title="Child task",
        task_type="implementation",
        assigned_to="coder",
        parent_id=parent["id"],
    )
    assert child["parent_id"] == parent["id"]
    assert child["id"].startswith("CD-")


async def test_claim_task(board: TaskBoard):
    """claim_task should atomically claim the first pending task for a role."""
    group = await board.create_group(title="Feature C", created_by="pm")
    await board.create_task(
        group_id=group["id"],
        title="Implement endpoint",
        task_type="implementation",
        assigned_to="coder",
    )
    claimed = await board.claim_task("coder", "coder-instance-1")
    assert claimed is not None
    assert claimed["claimed_by"] == "coder-instance-1"
    assert claimed["status"] == "in_progress"
    assert claimed["started_at"] is not None


async def test_claim_task_empty_queue(board: TaskBoard):
    """claim_task should return None when no pending tasks exist for the role."""
    result = await board.claim_task("coder", "coder-instance-1")
    assert result is None


async def test_complete_task(board: TaskBoard):
    """complete_task should set status to 'completed' and record timestamp."""
    group = await board.create_group(title="Feature D", created_by="pm")
    task = await board.create_task(
        group_id=group["id"],
        title="Build widget",
        task_type="implementation",
        assigned_to="coder",
    )
    result = await board.complete_task(task["id"])
    assert result["status"] == "completed"
    assert result["completed_at"] is not None


async def test_reject_task(board: TaskBoard):
    """reject_task should set status and store the reason."""
    group = await board.create_group(title="Feature E", created_by="pm")
    task = await board.create_task(
        group_id=group["id"],
        title="Review code",
        task_type="code_review",
        assigned_to="reviewer",
    )
    result = await board.reject_task(task["id"], "Needs more unit tests")
    assert result["status"] == "rejected"
    assert result["rejection_reason"] == "Needs more unit tests"


async def test_get_board(board: TaskBoard):
    """get_board should group tasks by their status."""
    group = await board.create_group(title="Feature F", created_by="pm")
    t1 = await board.create_task(
        group_id=group["id"],
        title="Task 1",
        task_type="implementation",
        assigned_to="coder",
    )
    t2 = await board.create_task(
        group_id=group["id"],
        title="Task 2",
        task_type="implementation",
        assigned_to="coder",
    )
    # Complete one task.
    await board.complete_task(t1["id"])

    result = await board.get_board()
    assert "completed" in result
    assert "pending" in result
    assert len(result["completed"]) == 1
    assert len(result["pending"]) == 1
    assert result["completed"][0]["id"] == t1["id"]
    assert result["pending"][0]["id"] == t2["id"]


async def test_get_board_filtered_by_group(board: TaskBoard):
    """get_board should respect the group_id filter."""
    g1 = await board.create_group(title="Group 1", created_by="pm")
    g2 = await board.create_group(title="Group 2", created_by="pm")
    await board.create_task(
        group_id=g1["id"],
        title="T in G1",
        task_type="implementation",
        assigned_to="coder",
    )
    await board.create_task(
        group_id=g2["id"],
        title="T in G2",
        task_type="implementation",
        assigned_to="coder",
    )
    result = await board.get_board(group_id=g1["id"])
    all_tasks = [t for tasks in result.values() for t in tasks]
    assert len(all_tasks) == 1
    assert all_tasks[0]["group_id"] == g1["id"]


# ------------------------------------------------------------------
# Task 6: Dependency resolution
# ------------------------------------------------------------------


async def test_blocked_task_unblocks_when_dependency_completes(board: TaskBoard):
    """A blocked task should become 'pending' when its dependency completes."""
    group = await board.create_group(title="Dep test", created_by="pm")
    dep = await board.create_task(
        group_id=group["id"],
        title="Write code",
        task_type="implementation",
        assigned_to="coder",
    )
    blocked = await board.create_task(
        group_id=group["id"],
        title="Run tests",
        task_type="qa_verification",
        assigned_to="tester",
        blocked_by=[dep["id"]],
    )
    assert blocked["status"] == "blocked"

    # Complete the dependency.
    await board.complete_task(dep["id"])

    # The blocked task should now be pending.
    updated = await board.get_task(blocked["id"])
    assert updated is not None
    assert updated["status"] == "pending"


async def test_task_with_multiple_deps_stays_blocked_until_all_complete(
    board: TaskBoard,
):
    """A task blocked by two dependencies should stay blocked until both complete."""
    group = await board.create_group(title="Multi dep", created_by="pm")
    dep1 = await board.create_task(
        group_id=group["id"],
        title="Dep 1",
        task_type="implementation",
        assigned_to="coder",
    )
    dep2 = await board.create_task(
        group_id=group["id"],
        title="Dep 2",
        task_type="implementation",
        assigned_to="coder",
    )
    blocked = await board.create_task(
        group_id=group["id"],
        title="Review",
        task_type="code_review",
        assigned_to="reviewer",
        blocked_by=[dep1["id"], dep2["id"]],
    )
    assert blocked["status"] == "blocked"

    # Complete only the first dependency.
    await board.complete_task(dep1["id"])
    still_blocked = await board.get_task(blocked["id"])
    assert still_blocked is not None
    assert still_blocked["status"] == "blocked"

    # Complete the second dependency.
    await board.complete_task(dep2["id"])
    now_free = await board.get_task(blocked["id"])
    assert now_free is not None
    assert now_free["status"] == "pending"


# ------------------------------------------------------------------
# Task 6: Cycle detection
# ------------------------------------------------------------------


async def test_cycle_detection_direct_cycle(board: TaskBoard):
    """has_cycle should detect A -> B -> A."""
    group = await board.create_group(title="Cycle test", created_by="pm")
    task_a = await board.create_task(
        group_id=group["id"],
        title="Task A",
        task_type="implementation",
        assigned_to="coder",
    )
    task_b = await board.create_task(
        group_id=group["id"],
        title="Task B",
        task_type="implementation",
        assigned_to="coder",
        blocked_by=[task_a["id"]],
    )
    # Adding B blocks A would create a cycle: A -> B -> A.
    assert await board.has_cycle(task_a["id"], task_b["id"]) is True


async def test_cycle_detection_no_cycle(board: TaskBoard):
    """has_cycle should return False for a valid linear dependency chain."""
    group = await board.create_group(title="No cycle", created_by="pm")
    task_a = await board.create_task(
        group_id=group["id"],
        title="Task A",
        task_type="implementation",
        assigned_to="coder",
    )
    task_b = await board.create_task(
        group_id=group["id"],
        title="Task B",
        task_type="implementation",
        assigned_to="coder",
        blocked_by=[task_a["id"]],
    )
    task_c = await board.create_task(
        group_id=group["id"],
        title="Task C",
        task_type="implementation",
        assigned_to="coder",
    )
    # C -> B (B is blocked by A already).  No cycle since C is not
    # upstream of B in any way.
    assert await board.has_cycle(task_c["id"], task_b["id"]) is False
