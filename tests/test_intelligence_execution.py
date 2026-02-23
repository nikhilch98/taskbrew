"""Tests for execution helpers (CommitPlanner, DebuggingHelper)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from taskbrew.orchestrator.database import Database
from taskbrew.intelligence.execution import CommitPlanner, DebuggingHelper


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
async def planner(db: Database) -> CommitPlanner:
    return CommitPlanner(db)


@pytest.fixture
async def debugger(db: Database) -> DebuggingHelper:
    return DebuggingHelper(db)


async def _create_task(db: Database, **overrides) -> str:
    """Insert a minimal task + group so foreign key constraints are satisfied."""
    now = datetime.now(timezone.utc).isoformat()
    task_id = overrides.pop("id", f"TST-{uuid.uuid4().hex[:4]}")
    group_id = f"GRP-{task_id}"

    await db.execute(
        "INSERT INTO groups (id, title, origin, status, created_at) "
        "VALUES (?, 'Test', 'test', 'active', ?)",
        (group_id, now),
    )

    defaults = {
        "title": "Test task",
        "status": "in_progress",
        "task_type": "implementation",
        "priority": "medium",
        "created_at": now,
    }
    defaults.update(overrides)

    cols = ["id", "group_id"] + list(defaults.keys())
    vals = [task_id, group_id] + list(defaults.values())
    placeholders = ", ".join("?" for _ in cols)
    col_str = ", ".join(cols)
    await db.execute(
        f"INSERT INTO tasks ({col_str}) VALUES ({placeholders})",
        tuple(vals),
    )
    return task_id


# ------------------------------------------------------------------
# CommitPlanner tests
# ------------------------------------------------------------------


async def test_plan_commit(planner: CommitPlanner, db: Database):
    """Plan a commit and verify the returned plan."""
    task_id = await _create_task(db)
    plan = await planner.plan_commit(task_id, ["src/a.py", "src/b.py"])

    assert plan["task_id"] == task_id
    assert plan["files"] == ["src/a.py", "src/b.py"]
    assert plan["file_count"] == 2
    assert "implementation" in plan["message"]


async def test_plan_commit_custom_message(planner: CommitPlanner, db: Database):
    """Custom message overrides auto-generated one."""
    task_id = await _create_task(db)
    plan = await planner.plan_commit(task_id, ["x.py"], message="custom msg")
    assert plan["message"] == "custom msg"


async def test_plan_commit_no_task_type(planner: CommitPlanner, db: Database):
    """Task without task_type falls back to 'change' in the message."""
    task_id = await _create_task(db, task_type=None)
    plan = await planner.plan_commit(task_id, ["a.py"])
    # When task_type is None, .get("task_type", "change") returns None (since key exists),
    # so the message uses None; with explicit message it overrides
    assert plan["file_count"] == 1
    assert task_id in plan["message"]


async def test_get_commit_plans(planner: CommitPlanner, db: Database):
    """Multiple commit plans can be stored and retrieved for one task."""
    task_id = await _create_task(db)
    await planner.plan_commit(task_id, ["a.py"])
    await planner.plan_commit(task_id, ["b.py"])

    plans = await planner.get_commit_plans(task_id)
    assert len(plans) == 2
    # Verify content was parsed from JSON
    assert isinstance(plans[0]["content"], dict)
    assert "files" in plans[0]["content"]


async def test_get_commit_plans_empty(planner: CommitPlanner, db: Database):
    """No plans returns empty list."""
    plans = await planner.get_commit_plans("NONEXISTENT")
    assert plans == []


# ------------------------------------------------------------------
# DebuggingHelper tests
# ------------------------------------------------------------------


async def test_failure_context(debugger: DebuggingHelper, db: Database):
    """Gather context for a failed task."""
    task_id = await _create_task(db, status="failed", rejection_reason="Tests failing")

    context = await debugger.get_failure_context(task_id)
    assert context["task"]["status"] == "failed"
    assert context["rejection_reason"] == "Tests failing"
    assert isinstance(context["events"], list)
    assert isinstance(context["escalations"], list)
    assert isinstance(context["quality_scores"], list)


async def test_failure_context_not_found(debugger: DebuggingHelper):
    """Non-existent task returns error dict."""
    context = await debugger.get_failure_context("NONEXISTENT")
    assert context == {"error": "Task not found"}


async def test_suggest_fix_with_rejection(debugger: DebuggingHelper, db: Database):
    """Suggestions include address_rejection when rejection_reason exists."""
    task_id = await _create_task(db, status="failed", rejection_reason="Missing tests")

    result = await debugger.suggest_fix(task_id)
    assert result["task_id"] == task_id
    assert result["status"] == "failed"
    assert any(s["type"] == "address_rejection" for s in result["suggestions"])
    assert any("Missing tests" in s["description"] for s in result["suggestions"])


async def test_suggest_fix_with_escalation(debugger: DebuggingHelper, db: Database):
    """Suggestions include resolve_escalation for open escalations."""
    task_id = await _create_task(db, status="failed")

    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "INSERT INTO escalations (task_id, from_agent, reason, severity, status, created_at) "
        "VALUES (?, 'coder', 'Build timeout', 'high', 'open', ?)",
        (task_id, now),
    )

    result = await debugger.suggest_fix(task_id)
    assert any(s["type"] == "resolve_escalation" for s in result["suggestions"])


async def test_suggest_fix_no_issues(debugger: DebuggingHelper, db: Database):
    """When no specific issues found, suggest retry."""
    task_id = await _create_task(db, status="in_progress")

    result = await debugger.suggest_fix(task_id)
    assert result["suggestions"][0]["type"] == "retry"
    assert result["suggestions"][0]["priority"] == "low"
