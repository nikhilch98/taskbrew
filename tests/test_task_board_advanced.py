"""Advanced TaskBoard tests: edge cases for claim, complete, filter, dependency, search, cancel."""
from __future__ import annotations

import pytest

from taskbrew.orchestrator.database import Database
from taskbrew.orchestrator.task_board import TaskBoard


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


# ------------------------------------------------------------------
# Claim tests
# ------------------------------------------------------------------


async def test_claim_task_assigns_to_agent(board: TaskBoard):
    """After claiming, the task should have claimed_by set to the instance id."""
    group = await board.create_group(title="Claim Test", created_by="pm")
    task = await board.create_task(
        group_id=group["id"],
        title="Build feature",
        task_type="implementation",
        assigned_to="coder",
    )

    claimed = await board.claim_task("coder", "coder-instance-42")
    assert claimed is not None
    assert claimed["claimed_by"] == "coder-instance-42"
    assert claimed["status"] == "in_progress"
    assert claimed["started_at"] is not None

    # Verify via direct fetch as well
    fetched = await board.get_task(task["id"])
    assert fetched["claimed_by"] == "coder-instance-42"
    assert fetched["status"] == "in_progress"


async def test_claim_already_claimed_task_fails(board: TaskBoard):
    """Claiming the only task again should return None (no pending tasks left)."""
    group = await board.create_group(title="Double Claim", created_by="pm")
    await board.create_task(
        group_id=group["id"],
        title="Sole task",
        task_type="implementation",
        assigned_to="coder",
    )

    first = await board.claim_task("coder", "coder-1")
    assert first is not None
    assert first["status"] == "in_progress"

    # Second claim should return None since the task is now in_progress
    second = await board.claim_task("coder", "coder-2")
    assert second is None


# ------------------------------------------------------------------
# Complete tests
# ------------------------------------------------------------------


async def test_complete_task_sets_status(board: TaskBoard):
    """After creating and claiming a task, completing it should set status to 'completed'."""
    group = await board.create_group(title="Complete Test", created_by="pm")
    task = await board.create_task(
        group_id=group["id"],
        title="Finish widget",
        task_type="implementation",
        assigned_to="coder",
    )

    # Claim then complete
    await board.claim_task("coder", "coder-1")
    result = await board.complete_task(task["id"])

    assert result["status"] == "completed"
    assert result["completed_at"] is not None

    # Verify via direct fetch
    fetched = await board.get_task(task["id"])
    assert fetched["status"] == "completed"


# ------------------------------------------------------------------
# Board filtering tests
# ------------------------------------------------------------------


async def test_get_board_filters_by_status(board: TaskBoard):
    """get_board should return tasks grouped by status; completing one task
    should move it to the 'completed' bucket."""
    group = await board.create_group(title="Filter Status", created_by="pm")
    t1 = await board.create_task(
        group_id=group["id"],
        title="Task A",
        task_type="implementation",
        assigned_to="coder",
    )
    t2 = await board.create_task(
        group_id=group["id"],
        title="Task B",
        task_type="implementation",
        assigned_to="coder",
    )
    t3 = await board.create_task(
        group_id=group["id"],
        title="Task C",
        task_type="implementation",
        assigned_to="coder",
    )

    # Claim then complete t1, leave t2 and t3 pending
    await board.claim_task("coder", "coder-1")
    await board.complete_task(t1["id"])

    result = await board.get_board(group_id=group["id"])

    assert "completed" in result
    assert len(result["completed"]) == 1
    assert result["completed"][0]["id"] == t1["id"]

    assert "pending" in result
    pending_ids = {t["id"] for t in result["pending"]}
    assert t2["id"] in pending_ids
    assert t3["id"] in pending_ids


async def test_get_board_filters_by_assigned_to(board: TaskBoard):
    """get_board with assigned_to filter should only return tasks for that role."""
    group = await board.create_group(title="Filter Role", created_by="pm")
    t_coder = await board.create_task(
        group_id=group["id"],
        title="Coder task",
        task_type="implementation",
        assigned_to="coder",
    )
    t_tester = await board.create_task(
        group_id=group["id"],
        title="Tester task",
        task_type="qa_verification",
        assigned_to="tester",
    )
    await board.create_task(
        group_id=group["id"],
        title="Architect task",
        task_type="tech_design",
        assigned_to="architect",
    )

    # Filter for coder only
    result = await board.get_board(assigned_to="coder")
    all_tasks = [t for tasks in result.values() for t in tasks]
    assert len(all_tasks) == 1
    assert all_tasks[0]["id"] == t_coder["id"]

    # Filter for tester only
    result = await board.get_board(assigned_to="tester")
    all_tasks = [t for tasks in result.values() for t in tasks]
    assert len(all_tasks) == 1
    assert all_tasks[0]["id"] == t_tester["id"]


# ------------------------------------------------------------------
# Dependency tests
# ------------------------------------------------------------------


async def test_create_task_with_dependencies(board: TaskBoard):
    """A task created with blocked_by should start with status 'blocked'."""
    group = await board.create_group(title="Dep Create", created_by="pm")
    task_a = await board.create_task(
        group_id=group["id"],
        title="Task A",
        task_type="implementation",
        assigned_to="coder",
    )
    task_b = await board.create_task(
        group_id=group["id"],
        title="Task B depends on A",
        task_type="qa_verification",
        assigned_to="tester",
        blocked_by=[task_a["id"]],
    )

    assert task_b["status"] == "blocked"

    # Verify the dependency row exists in the database
    deps = await board._db.execute_fetchall(
        "SELECT * FROM task_dependencies WHERE task_id = ?",
        (task_b["id"],),
    )
    assert len(deps) == 1
    assert deps[0]["blocked_by"] == task_a["id"]
    assert deps[0]["resolved"] == 0


async def test_dependency_resolution(board: TaskBoard):
    """Completing the blocking task should resolve the dependency and
    transition the blocked task to 'pending'."""
    group = await board.create_group(title="Dep Resolution", created_by="pm")
    task_a = await board.create_task(
        group_id=group["id"],
        title="Blocking task",
        task_type="implementation",
        assigned_to="coder",
    )
    task_b = await board.create_task(
        group_id=group["id"],
        title="Blocked task",
        task_type="qa_verification",
        assigned_to="tester",
        blocked_by=[task_a["id"]],
    )

    assert task_b["status"] == "blocked"

    # Claim then complete the blocker
    await board.claim_task("coder", "coder-1")
    await board.complete_task(task_a["id"])

    # The blocked task should now be pending
    updated = await board.get_task(task_b["id"])
    assert updated["status"] == "pending"

    # The dependency row should be resolved
    deps = await board._db.execute_fetchall(
        "SELECT * FROM task_dependencies WHERE task_id = ?",
        (task_b["id"],),
    )
    assert len(deps) == 1
    assert deps[0]["resolved"] == 1
    assert deps[0]["resolved_at"] is not None


# ------------------------------------------------------------------
# Group tests
# ------------------------------------------------------------------


async def test_create_group(board: TaskBoard):
    """Creating a group should return a dict with auto-generated ID and correct fields."""
    group = await board.create_group(
        title="New Feature Group",
        origin="slack-request",
        created_by="pm",
    )

    assert group["id"].startswith("FEAT-")
    assert group["title"] == "New Feature Group"
    assert group["origin"] == "slack-request"
    assert group["status"] == "active"
    assert group["created_by"] == "pm"
    assert group["created_at"] is not None

    # Verify it can be retrieved
    groups = await board.get_groups()
    assert len(groups) >= 1
    found = [g for g in groups if g["id"] == group["id"]]
    assert len(found) == 1
    assert found[0]["title"] == "New Feature Group"


# ------------------------------------------------------------------
# Search tests
# ------------------------------------------------------------------


async def test_task_search(board: TaskBoard):
    """search_tasks should find tasks matching the query string in title or description."""
    group = await board.create_group(title="Search Test", created_by="pm")
    t1 = await board.create_task(
        group_id=group["id"],
        title="Implement login page",
        task_type="implementation",
        assigned_to="coder",
        description="Build the authentication form.",
    )
    t2 = await board.create_task(
        group_id=group["id"],
        title="Write unit tests for API",
        task_type="qa_verification",
        assigned_to="tester",
        description="Cover all endpoint handlers.",
    )
    t3 = await board.create_task(
        group_id=group["id"],
        title="Review login implementation",
        task_type="code_review",
        assigned_to="reviewer",
        description="Check the login code for security issues.",
    )

    # Search for "login" -- should match t1 and t3
    result = await board.search_tasks("login")
    assert result["total"] == 2
    found_ids = {t["id"] for t in result["tasks"]}
    assert t1["id"] in found_ids
    assert t3["id"] in found_ids

    # Search for "endpoint" -- should match t2 (in description)
    result = await board.search_tasks("endpoint")
    assert result["total"] == 1
    assert result["tasks"][0]["id"] == t2["id"]

    # Search for something that matches nothing
    result = await board.search_tasks("xyznonexistent")
    assert result["total"] == 0
    assert result["tasks"] == []


# ------------------------------------------------------------------
# Cancel tests
# ------------------------------------------------------------------


async def test_cancel_task(board: TaskBoard):
    """cancel_task should set status to 'cancelled' and store the reason."""
    group = await board.create_group(title="Cancel Test", created_by="pm")
    task = await board.create_task(
        group_id=group["id"],
        title="Task to cancel",
        task_type="implementation",
        assigned_to="coder",
    )

    result = await board.cancel_task(task["id"], reason="No longer needed")

    assert result["status"] == "cancelled"
    assert result["rejection_reason"] == "No longer needed"
    assert result["completed_at"] is not None

    # Verify via direct fetch
    fetched = await board.get_task(task["id"])
    assert fetched["status"] == "cancelled"
    assert fetched["rejection_reason"] == "No longer needed"
