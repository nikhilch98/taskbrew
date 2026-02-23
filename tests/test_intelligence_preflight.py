"""Tests for the PreflightChecker."""

from __future__ import annotations

import pytest

from taskbrew.orchestrator.database import Database
from taskbrew.orchestrator.task_board import TaskBoard
from taskbrew.intelligence.preflight import PreflightChecker


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
async def task_board(db):
    board = TaskBoard(db)
    await board.register_prefixes({"PM": "PM", "CD": "CD"})
    return board


@pytest.fixture
async def checker(db):
    return PreflightChecker(db)


# ------------------------------------------------------------------
# Helper
# ------------------------------------------------------------------


async def _make_task(task_board, title, description=None, blocked_by=None):
    group = await task_board.create_group(title="Test Group", origin="test", created_by="test")
    return await task_board.create_task(
        group_id=group["id"],
        title=title,
        description=description,
        task_type="implementation",
        assigned_to="CD",
        created_by="test",
        blocked_by=blocked_by,
    )


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------


async def test_preflight_all_pass(checker, task_board):
    """All pre-flight checks pass for a clean task."""
    task = await _make_task(task_board, title="Simple task", description="Implement the feature as specified.")
    result = await checker.run_checks(task, role="coder")
    assert result["passed"] is True
    check_names = [c["name"] for c in result["checks"]]
    assert "task_completeness" in check_names
    assert "dependencies_resolved" in check_names
    # All individual checks should pass
    for check in result["checks"]:
        assert check["passed"] is True


async def test_preflight_budget_exceeded(db, task_board):
    """Pre-flight fails when budget is exceeded."""

    class FakeCostManager:
        async def check_budget(self, role=None):
            return {"allowed": False, "scope": "daily", "remaining": 0}

    checker = PreflightChecker(db, cost_manager=FakeCostManager())
    task = await _make_task(task_board, title="Expensive task", description="Do something costly.")
    result = await checker.run_checks(task, role="coder")
    assert result["passed"] is False
    budget_check = next(c for c in result["checks"] if c["name"] == "budget")
    assert budget_check["passed"] is False
    assert "exceeded" in budget_check["details"].lower()


async def test_preflight_unresolved_deps(checker, task_board):
    """Pre-flight fails when task has unresolved dependencies."""
    blocker = await _make_task(task_board, title="Blocker", description="Must finish first.")
    task = await _make_task(task_board, title="Blocked task", description="Depends on blocker.", blocked_by=[blocker["id"]])
    result = await checker.run_checks(task, role="coder")
    assert result["passed"] is False
    deps_check = next(c for c in result["checks"] if c["name"] == "dependencies_resolved")
    assert deps_check["passed"] is False
    assert "1 unresolved" in deps_check["details"]


async def test_preflight_missing_description(checker, task_board):
    """Pre-flight warns but still passes when description is missing."""
    task = await _make_task(task_board, title="No description task")
    result = await checker.run_checks(task, role="coder")
    # Should still pass overall (missing description is a warning, not a failure)
    assert result["passed"] is True
    completeness = next(c for c in result["checks"] if c["name"] == "task_completeness")
    assert completeness["passed"] is False
    assert "missing" in completeness["details"].lower()
