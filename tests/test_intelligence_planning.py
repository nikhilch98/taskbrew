"""Tests for the PlanningManager."""

from __future__ import annotations

import pytest

from taskbrew.orchestrator.database import Database
from taskbrew.orchestrator.task_board import TaskBoard
from taskbrew.intelligence.planning import PlanningManager


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
async def planning(db, task_board):
    return PlanningManager(db, task_board)


# ------------------------------------------------------------------
# Helper to create a task
# ------------------------------------------------------------------


async def _make_task(task_board, title, description="", task_type="implementation", priority="medium", assigned_to="CD", blocked_by=None):
    group = await task_board.create_group(title="Test Group", origin="test", created_by="test")
    return await task_board.create_task(
        group_id=group["id"],
        title=title,
        description=description,
        task_type=task_type,
        assigned_to=assigned_to,
        created_by="test",
        priority=priority,
        blocked_by=blocked_by,
    )


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------


async def test_decompose_task_with_list(planning, task_board):
    """Decompose a task that has a numbered list in description."""
    task = await _make_task(
        task_board,
        title="Implement features",
        description="1. Add login page with form validation\n2. Add signup form with email verification\n3. Add password reset flow",
    )
    result = await planning.decompose_task(task["id"])
    assert result["content"]["complexity"] in ("medium", "complex")
    assert len(result["content"]["subtasks"]) >= 2
    assert result["plan_type"] == "decomposition"
    assert result["confidence"] == 0.7


async def test_decompose_task_without_list(planning, task_board):
    """Decompose a task without explicit list — keyword-based heuristics."""
    task = await _make_task(
        task_board,
        title="Implement new caching layer",
        description="Build a Redis-based caching layer for the API.",
    )
    result = await planning.decompose_task(task["id"])
    assert result["content"]["complexity"] in ("simple", "medium")
    # Should find "implement" / "build" keywords
    subtask_types = [s["type"] for s in result["content"]["subtasks"]]
    assert "implementation" in subtask_types


async def test_decompose_task_not_found(planning):
    """Decompose returns error for non-existent task."""
    result = await planning.decompose_task("NONEXISTENT-999")
    assert "error" in result


async def test_estimate_effort_simple(planning, task_board):
    """Estimate a simple task (short description, few file refs)."""
    task = await _make_task(
        task_board,
        title="Fix typo",
        description="Fix typo in readme",
    )
    result = await planning.estimate_effort(task["id"])
    assert result["content"]["complexity"] == "simple"
    assert result["content"]["tokens_estimate"] == 5000
    assert result["content"]["time_estimate_min"] == 2
    assert result["plan_type"] == "estimate"


async def test_estimate_effort_complex(planning, task_board):
    """Estimate a complex task (long description, many file refs)."""
    # Build a description that's > 200 words with many file references
    files = " ".join([f"src/module_{i}/handler.py" for i in range(10)])
    long_desc = ("Refactor the entire authentication system. " * 30) + f"\nFiles: {files}"
    task = await _make_task(
        task_board,
        title="Major auth refactor",
        description=long_desc,
    )
    result = await planning.estimate_effort(task["id"])
    assert result["content"]["complexity"] == "complex"
    assert result["content"]["tokens_estimate"] == 40000
    assert result["content"]["time_estimate_min"] == 30


async def test_assess_risk_low(planning, task_board):
    """Assess risk for a simple task with no dependencies and no high-risk files."""
    task = await _make_task(
        task_board,
        title="Update README",
        description="Add installation instructions to README.",
    )
    result = await planning.assess_risk(task["id"], files_to_change=["README.md"])
    assert result["content"]["risk_level"] == "low"
    assert result["content"]["risk_score"] < 0.3
    assert result["plan_type"] == "risk"


async def test_assess_risk_high(planning, task_board, db):
    """Assess risk for a critical task with high-risk files and dependencies."""
    # Create a blocker task first
    blocker = await _make_task(task_board, title="Blocker task", description="Must be done first")
    # Create task blocked by the blocker
    task = await _make_task(
        task_board,
        title="Critical migration",
        description="Run database migration",
        priority="critical",
        blocked_by=[blocker["id"]],
    )
    result = await planning.assess_risk(
        task["id"],
        files_to_change=["src/database.py", "src/__init__.py", "src/config.py", "src/migration.py"],
    )
    assert result["content"]["risk_level"] == "high"
    assert result["content"]["risk_score"] >= 0.6
    assert len(result["content"]["mitigations"]) >= 2
    assert "Request human review before merge" in result["content"]["mitigations"]


async def test_generate_alternatives(planning, task_board):
    """Generate alternative approaches for a task."""
    task = await _make_task(
        task_board,
        title="Add user dashboard",
        description="Create a new user dashboard page with analytics widgets.",
    )
    result = await planning.generate_alternatives(task["id"])
    assert result["plan_type"] == "alternatives"
    approaches = result["content"]["approaches"]
    assert len(approaches) == 3
    assert result["content"]["recommended"] == "Direct Implementation"
    # Check all approaches have required fields
    for approach in approaches:
        assert "name" in approach
        assert "description" in approach
        assert "risk" in approach
        assert "effort" in approach


async def test_generate_alternatives_implementation_type(planning, task_board):
    """Regression: implementation tasks get implementation-specific alternatives."""
    task = await _make_task(
        task_board,
        title="Build caching layer",
        description="Implement Redis caching",
        task_type="implementation",
    )
    result = await planning.generate_alternatives(task["id"])
    names = [a["name"] for a in result["content"]["approaches"]]
    assert "Direct Implementation" in names
    assert "Test-Driven (write tests first)" in names
    assert "Spike/Prototype first" in names
    assert result["content"]["task_type"] == "implementation"


async def test_generate_alternatives_bug_fix_type(planning, task_board):
    """Regression: bug_fix tasks get bug-fix-specific alternatives."""
    task = await _make_task(
        task_board,
        title="Fix login timeout",
        description="Users get timeout errors on login",
        task_type="bug_fix",
    )
    result = await planning.generate_alternatives(task["id"])
    names = [a["name"] for a in result["content"]["approaches"]]
    assert "Hot fix (minimal change)" in names
    assert "Root cause fix (deeper refactor)" in names
    assert "Workaround + backlog cleanup ticket" in names
    assert result["content"]["task_type"] == "bug_fix"


async def test_generate_alternatives_code_review_type(planning, task_board):
    """Regression: code_review tasks get review-specific alternatives."""
    task = await _make_task(
        task_board,
        title="Review auth module",
        description="Review the authentication module changes",
        task_type="code_review",
    )
    result = await planning.generate_alternatives(task["id"])
    names = [a["name"] for a in result["content"]["approaches"]]
    assert "Automated lint + manual spot check" in names
    assert "Full line-by-line review" in names
    assert "Focus on test coverage gaps" in names
    assert result["content"]["task_type"] == "code_review"


async def test_generate_alternatives_unknown_type_uses_default(planning, task_board):
    """Regression: unknown task types fall back to default alternatives."""
    task = await _make_task(
        task_board,
        title="Misc task",
        description="Some general task",
        task_type="general",
    )
    result = await planning.generate_alternatives(task["id"])
    names = [a["name"] for a in result["content"]["approaches"]]
    assert "Direct Implementation" in names
    assert "Incremental Approach" in names
    assert "Prototype First" in names


async def test_create_rollback_plan(planning, task_board):
    """Create a rollback plan for a task."""
    task = await _make_task(
        task_board,
        title="Deploy v2.0",
        description="Deploy version 2.0 to production.",
    )
    result = await planning.create_rollback_plan(task["id"])
    assert result["plan_type"] == "rollback"
    assert result["confidence"] == 0.8
    assert len(result["content"]["steps"]) >= 3
    assert "verification" in result["content"]
    assert "communication" in result["content"]


async def test_get_plans_filtered(planning, task_board):
    """Get plans filtered by type."""
    task = await _make_task(
        task_board,
        title="Multi-plan task",
        description="A task that will have multiple plans.",
    )
    # Create multiple plan types
    await planning.estimate_effort(task["id"])
    await planning.create_rollback_plan(task["id"])

    # Get all plans
    all_plans = await planning.get_plans(task["id"])
    assert len(all_plans) == 2

    # Get filtered by type
    estimates = await planning.get_plans(task["id"], plan_type="estimate")
    assert len(estimates) == 1
    assert estimates[0]["plan_type"] == "estimate"
    # Content should be parsed from JSON
    assert isinstance(estimates[0]["content"], dict)

    rollbacks = await planning.get_plans(task["id"], plan_type="rollback")
    assert len(rollbacks) == 1
    assert rollbacks[0]["plan_type"] == "rollback"
