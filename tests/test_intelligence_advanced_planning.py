"""Tests for the AdvancedPlanningManager (features 45-50)."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from taskbrew.orchestrator.database import Database
from taskbrew.intelligence.advanced_planning import AdvancedPlanningManager


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture
async def db():
    database = Database(":memory:")
    await database.initialize()
    # Add depends_on column that the module queries
    await database.execute(
        "ALTER TABLE tasks ADD COLUMN depends_on TEXT"
    )
    yield database
    await database.close()


@pytest.fixture
async def planner(db: Database) -> AdvancedPlanningManager:
    mgr = AdvancedPlanningManager(db)
    await mgr.ensure_tables()
    return mgr


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _past_iso(minutes: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()


async def _ensure_group(db, group_id: str):
    existing = await db.execute_fetchone("SELECT id FROM groups WHERE id = ?", (group_id,))
    if existing:
        return
    now = _now_iso()
    await db.execute(
        "INSERT INTO groups (id, title, origin, status, created_at) VALUES (?, 'Test Group', 'test', 'active', ?)",
        (group_id, now),
    )


async def _create_task(
    db,
    *,
    group_id="GRP-1",
    status="pending",
    task_type="implementation",
    description="A task",
    started_at=None,
    completed_at=None,
    rejection_reason=None,
    claimed_by=None,
    assigned_to="coder",
    depends_on=None,
):
    tid = f"TSK-{uuid.uuid4().hex[:6]}"
    now = _now_iso()
    if group_id:
        await _ensure_group(db, group_id)
    await db.execute(
        "INSERT INTO tasks (id, title, description, task_type, priority, status, "
        "created_by, created_at, group_id, started_at, completed_at, rejection_reason, "
        "claimed_by, assigned_to, depends_on) "
        "VALUES (?, 'Test', ?, ?, 'medium', ?, 'test', ?, ?, ?, ?, ?, ?, ?, ?)",
        (tid, description, task_type, status, now, group_id, started_at, completed_at,
         rejection_reason, claimed_by, assigned_to, depends_on),
    )
    return tid


# ------------------------------------------------------------------
# Feature 45: Dependency-Aware Scheduling
# ------------------------------------------------------------------


async def test_build_schedule_linear_chain(planner: AdvancedPlanningManager, db: Database):
    """A -> B -> C should be scheduled in order A, B, C."""
    group = "GRP-chain"
    t_a = await _create_task(db, group_id=group)
    t_b = await _create_task(db, group_id=group, depends_on=t_a)
    t_c = await _create_task(db, group_id=group, depends_on=t_b)

    result = await planner.build_schedule(group)
    assert len(result) == 3

    order_map = {s["task_id"]: s["scheduled_order"] for s in result}
    assert order_map[t_a] < order_map[t_b]
    assert order_map[t_b] < order_map[t_c]


async def test_build_schedule_parallel_tasks(planner: AdvancedPlanningManager, db: Database):
    """Independent tasks should all have the same scheduled_order."""
    group = "GRP-parallel"
    await _create_task(db, group_id=group)
    await _create_task(db, group_id=group)
    await _create_task(db, group_id=group)

    result = await planner.build_schedule(group)
    assert len(result) == 3

    orders = {s["scheduled_order"] for s in result}
    # All should be at the same level (order 0) since no dependencies
    assert len(orders) == 1
    assert 0 in orders


async def test_get_schedule(planner: AdvancedPlanningManager, db: Database):
    """get_schedule retrieves the stored schedule ordered by scheduled_order."""
    group = "GRP-get"
    t_a = await _create_task(db, group_id=group)
    await _create_task(db, group_id=group, depends_on=t_a)

    await planner.build_schedule(group)
    schedule = await planner.get_schedule(group)
    assert len(schedule) == 2
    assert schedule[0]["scheduled_order"] <= schedule[1]["scheduled_order"]


async def test_build_schedule_empty_group(planner: AdvancedPlanningManager):
    """Empty group returns empty schedule."""
    result = await planner.build_schedule("GRP-empty")
    assert result == []


# ------------------------------------------------------------------
# Feature 46: Resource-Aware Planning
# ------------------------------------------------------------------


async def test_snapshot_resources(planner: AdvancedPlanningManager, db: Database):
    """Snapshot captures active agents and their task counts."""
    await _create_task(db, status="in_progress", assigned_to="coder-1")
    await _create_task(db, status="in_progress", assigned_to="coder-1")
    await _create_task(db, status="in_progress", assigned_to="reviewer-1")

    snapshots = await planner.snapshot_resources()
    assert len(snapshots) == 2

    coder = next(s for s in snapshots if s["agent_id"] == "coder-1")
    assert coder["active_tasks"] == 2

    reviewer = next(s for s in snapshots if s["agent_id"] == "reviewer-1")
    assert reviewer["active_tasks"] == 1


async def test_plan_with_resources(planner: AdvancedPlanningManager, db: Database):
    """plan_with_resources returns assignments for pending tasks."""
    group = "GRP-res"
    await _create_task(db, group_id=group)
    await _create_task(db, group_id=group)
    # Create an active agent
    await _create_task(db, status="in_progress", assigned_to="coder-1")

    result = await planner.plan_with_resources(group)
    assert len(result) == 2
    assert all("task_id" in a for a in result)
    assert all("assigned_to" in a for a in result)


# ------------------------------------------------------------------
# Feature 47: Deadline Estimation
# ------------------------------------------------------------------


async def test_estimate_deadline_with_history(planner: AdvancedPlanningManager, db: Database):
    """Deadline estimate uses historical completion data."""
    for _ in range(5):
        started = _past_iso(60)
        completed = _past_iso(30)  # ~30 min duration
        await _create_task(db, status="completed", task_type="bugfix",
                          started_at=started, completed_at=completed)

    tid = await _create_task(db, task_type="bugfix")
    estimate = await planner.estimate_deadline(tid)

    assert "id" in estimate
    assert estimate["task_id"] == tid
    assert estimate["based_on_samples"] == 5
    assert estimate["estimated_hours"] > 0
    assert estimate["confidence_low"] < estimate["estimated_hours"]
    assert estimate["confidence_high"] > estimate["estimated_hours"]


async def test_estimate_deadline_no_history(planner: AdvancedPlanningManager, db: Database):
    """Without history, uses default estimate."""
    tid = await _create_task(db, task_type="novel_type")
    estimate = await planner.estimate_deadline(tid)

    assert estimate["based_on_samples"] == 0
    assert estimate["estimated_hours"] == 1.0  # default
    assert estimate["id"]


# ------------------------------------------------------------------
# Feature 48: Scope Creep Detection
# ------------------------------------------------------------------


async def test_check_scope_creep_detected(planner: AdvancedPlanningManager, db: Database):
    """Detect scope creep when description more than doubles."""
    original = "Fix the login button."
    tid = await _create_task(db, description=original)

    expanded = (
        "Fix the login button. Also refactor the auth module. "
        "Add OAuth2 support. Integrate with the new API gateway. "
        "Update the database schema for new user fields. "
        "Add rate limiting to the endpoint. Deploy to staging."
    )
    flag = await planner.check_scope_creep(tid, expanded)
    assert flag is not None
    assert flag["task_id"] == tid
    assert flag["growth_pct"] > 50.0


async def test_check_scope_creep_not_detected(planner: AdvancedPlanningManager, db: Database):
    """No scope creep when description stays similar."""
    original = "Fix the login button on the homepage."
    tid = await _create_task(db, description=original)

    minor_change = "Fix the login button on the homepage. Added a border."
    flag = await planner.check_scope_creep(tid, minor_change)
    assert flag is None


async def test_get_scope_flags(planner: AdvancedPlanningManager, db: Database):
    """get_scope_flags returns stored flags."""
    original = "Build a page."
    tid = await _create_task(db, description=original)
    expanded = original + " " + " ".join(
        f"Add api_endpoint_{i} with database_migration_{i} and config_setup_{i}." for i in range(10)
    )
    await planner.check_scope_creep(tid, expanded)

    flags = await planner.get_scope_flags(task_id=tid)
    assert len(flags) >= 1
    assert flags[0]["task_id"] == tid


# ------------------------------------------------------------------
# Feature 49: Incremental Delivery Planning
# ------------------------------------------------------------------


async def test_plan_increments_multi_sentence(planner: AdvancedPlanningManager):
    """Multi-part description is split into increments on 'and'/'then'."""
    increments = await planner.plan_increments(
        feature_id="FEAT-001",
        title="User Dashboard",
        description="Build the user profile page and add analytics widgets then implement notification preferences",
    )
    assert len(increments) == 3
    assert all(inc["feature_id"] == "FEAT-001" for inc in increments)
    assert all(inc["status"] == "planned" for inc in increments)
    assert increments[0]["increment_order"] == 0
    assert increments[1]["increment_order"] == 1
    assert increments[2]["increment_order"] == 2


async def test_get_increments(planner: AdvancedPlanningManager):
    """get_increments returns increments ordered by increment_order."""
    await planner.plan_increments(
        feature_id="FEAT-002",
        title="Auth",
        description="Add login and add signup and add password reset",
    )
    increments = await planner.get_increments("FEAT-002")
    assert len(increments) >= 2
    orders = [i["increment_order"] for i in increments]
    assert orders == sorted(orders)


# ------------------------------------------------------------------
# Feature 50: Automated Post-Mortems
# ------------------------------------------------------------------


async def test_generate_post_mortem_for_task(planner: AdvancedPlanningManager, db: Database):
    """Generate post-mortem for a single rejected task."""
    tid = await _create_task(db, status="rejected", rejection_reason="Missing test coverage")

    pm = await planner.generate_post_mortem(task_id=tid)
    assert pm["task_id"] == tid
    assert pm["total_tasks"] == 1
    assert pm["failed"] == 1
    assert pm["success_rate"] == 0.0
    assert pm["common_failures"] is not None


async def test_generate_post_mortem_for_group(planner: AdvancedPlanningManager, db: Database):
    """Generate post-mortem for a group with multiple failures."""
    group = "GRP-pm"
    await _create_task(db, group_id=group, status="rejected", rejection_reason="Bad implementation")
    await _create_task(db, group_id=group, status="failed", rejection_reason="Timeout error")
    await _create_task(db, group_id=group, status="completed")

    pm = await planner.generate_post_mortem(group_id=group)
    assert pm["group_id"] == group
    assert pm["total_tasks"] == 3
    assert pm["completed"] == 1
    assert pm["failed"] == 2
    assert pm["lessons"] is not None


async def test_generate_post_mortem_no_failures(planner: AdvancedPlanningManager, db: Database):
    """Post-mortem with all completed tasks shows high success rate."""
    group = "GRP-ok"
    await _create_task(db, group_id=group, status="completed")
    await _create_task(db, group_id=group, status="completed")

    pm = await planner.generate_post_mortem(group_id=group)
    assert pm["success_rate"] == 1.0
    assert pm["failed"] == 0


async def test_get_post_mortems(planner: AdvancedPlanningManager, db: Database):
    """get_post_mortems returns stored post-mortems."""
    tid = await _create_task(db, status="rejected", rejection_reason="Bug")
    await planner.generate_post_mortem(task_id=tid)

    pms = await planner.get_post_mortems()
    assert len(pms) >= 1
    assert pms[0]["task_id"] == tid
