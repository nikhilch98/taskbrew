"""Tests for the SpecializationManager."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from taskbrew.orchestrator.database import Database
from taskbrew.intelligence.specialization import SpecializationManager


@pytest.fixture
async def db():
    database = Database(":memory:")
    await database.initialize()
    yield database
    await database.close()


@pytest.fixture
async def spec(db: Database) -> SpecializationManager:
    return SpecializationManager(db)


async def _create_task(db: Database, task_id: str | None = None, **overrides) -> str:
    """Insert a minimal task + group so foreign key constraints are satisfied."""
    now = datetime.now(timezone.utc).isoformat()
    if task_id is None:
        task_id = f"TST-{uuid.uuid4().hex[:6]}"
    group_id = f"GRP-{task_id}"

    await db.execute(
        "INSERT INTO groups (id, title, origin, status, created_at) "
        "VALUES (?, 'Test', 'test', 'active', ?)",
        (group_id, now),
    )

    defaults = {
        "title": "Test task",
        "status": "completed",
        "priority": "medium",
        "assigned_to": "coder",
        "task_type": "implementation",
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


# ---- Skill Badges ----


async def test_update_skill_badge_new(spec: SpecializationManager):
    result = await spec.update_skill_badge("coder", "python", success=True)
    assert result["proficiency"] == 1.0
    assert result["tasks_completed"] == 1
    assert result["success_rate"] == 1.0


async def test_update_skill_badge_existing(spec: SpecializationManager):
    await spec.update_skill_badge("coder", "python", success=True)
    result = await spec.update_skill_badge("coder", "python", success=False)
    # 0.9 * 1.0 + 0.1 * 0.0 = 0.9
    assert result["proficiency"] == pytest.approx(0.9)
    assert result["tasks_completed"] == 2
    assert result["success_rate"] == pytest.approx(0.5)


async def test_update_skill_badge_failure(spec: SpecializationManager):
    result = await spec.update_skill_badge("coder", "testing", success=False)
    assert result["proficiency"] == 0.0
    assert result["success_rate"] == 0.0


async def test_update_skill_badge_multiple_updates(spec: SpecializationManager):
    """Verify weighted moving average converges correctly over many updates."""
    await spec.update_skill_badge("coder", "python", success=True)   # prof=1.0
    await spec.update_skill_badge("coder", "python", success=True)   # prof=0.9*1.0+0.1*1.0=1.0
    result = await spec.update_skill_badge("coder", "python", success=False)  # prof=0.9*1.0+0.1*0.0=0.9
    assert result["proficiency"] == pytest.approx(0.9)
    assert result["tasks_completed"] == 3
    assert result["success_rate"] == pytest.approx(2 / 3)


async def test_get_agent_skills(spec: SpecializationManager):
    await spec.update_skill_badge("coder", "python", success=True)
    await spec.update_skill_badge("coder", "testing", success=True)
    skills = await spec.get_agent_skills("coder")
    assert len(skills) == 2


async def test_get_agent_skills_ordered_by_proficiency(spec: SpecializationManager):
    """Skills should be ordered by proficiency descending."""
    await spec.update_skill_badge("coder", "low_skill", success=False)
    await spec.update_skill_badge("coder", "high_skill", success=True)
    skills = await spec.get_agent_skills("coder")
    assert skills[0]["skill_type"] == "high_skill"
    assert skills[1]["skill_type"] == "low_skill"


async def test_get_agent_skills_empty(spec: SpecializationManager):
    skills = await spec.get_agent_skills("nonexistent")
    assert skills == []


async def test_get_best_agent_for_task(spec: SpecializationManager):
    await spec.update_skill_badge("coder", "python", success=True)
    await spec.update_skill_badge("reviewer", "python", success=False)
    best = await spec.get_best_agent_for_task("python")
    assert best["agent_role"] == "coder"


async def test_get_best_agent_none(spec: SpecializationManager):
    best = await spec.get_best_agent_for_task("nonexistent")
    assert best is None


# ---- Model Routing ----


async def test_set_and_route_model(spec: SpecializationManager):
    rule_id = await spec.set_routing_rule("coder", "high", "claude-opus-4-6")
    assert rule_id > 0
    model = await spec.route_model("coder", "high")
    assert model == "claude-opus-4-6"


async def test_route_model_no_match(spec: SpecializationManager):
    model = await spec.route_model("coder", "low")
    assert model is None


async def test_routing_rule_deactivation(spec: SpecializationManager):
    await spec.set_routing_rule("coder", "high", "old-model")
    await spec.set_routing_rule("coder", "high", "new-model")
    model = await spec.route_model("coder", "high")
    assert model == "new-model"
    rules = await spec.get_routing_rules("coder")
    active_high = [r for r in rules if r["complexity_threshold"] == "high"]
    assert len(active_high) == 1


async def test_routing_rule_with_criteria(spec: SpecializationManager):
    criteria = {"min_tokens": 1000, "max_cost": 0.5}
    rule_id = await spec.set_routing_rule("coder", "medium", "claude-sonnet-4-20250514", criteria=criteria)
    assert rule_id > 0
    rules = await spec.get_routing_rules("coder")
    assert len(rules) == 1


async def test_get_routing_rules_all(spec: SpecializationManager):
    await spec.set_routing_rule("coder", "high", "model-a")
    await spec.set_routing_rule("reviewer", "low", "model-b")
    rules = await spec.get_routing_rules()
    assert len(rules) == 2


async def test_route_model_default_complexity(spec: SpecializationManager):
    """route_model defaults to 'medium' complexity."""
    await spec.set_routing_rule("coder", "medium", "default-model")
    model = await spec.route_model("coder")
    assert model == "default-model"


# ---- Prompt Tuning ----


async def test_store_and_get_prompt_tunings(spec: SpecializationManager):
    tid = await spec.store_prompt_tuning("coder", "Always run tests", "Before completing, run pytest")
    assert tid > 0
    tunings = await spec.get_prompt_tunings("coder")
    assert len(tunings) >= 1
    assert tunings[0]["title"] == "Always run tests"


async def test_prompt_tunings_empty(spec: SpecializationManager):
    tunings = await spec.get_prompt_tunings("nonexistent")
    assert tunings == []


async def test_store_multiple_prompt_tunings(spec: SpecializationManager):
    await spec.store_prompt_tuning("coder", "Rule 1", "Content 1")
    await spec.store_prompt_tuning("coder", "Rule 2", "Content 2")
    tunings = await spec.get_prompt_tunings("coder")
    assert len(tunings) == 2


async def test_prompt_tunings_scoped_to_role(spec: SpecializationManager):
    """Prompt tunings for one role should not appear for another."""
    await spec.store_prompt_tuning("coder", "Coder rule", "Content")
    await spec.store_prompt_tuning("reviewer", "Reviewer rule", "Content")
    coder_tunings = await spec.get_prompt_tunings("coder")
    assert len(coder_tunings) == 1
    assert coder_tunings[0]["title"] == "Coder rule"


# ---- Rejection Analysis ----


async def test_analyze_rejections(spec: SpecializationManager, db: Database):
    await _create_task(db, "T-001", status="rejected", rejection_reason="Missing test coverage", assigned_to="coder")
    await _create_task(db, "T-002", status="failed", rejection_reason="No tests provided", assigned_to="coder")

    result = await spec.analyze_rejections("coder")
    assert result["rejections"] == 2
    assert any(p["category"] == "testing" for p in result["patterns"])


async def test_analyze_rejections_empty(spec: SpecializationManager):
    result = await spec.analyze_rejections("coder")
    assert result["rejections"] == 0
    assert result["patterns"] == []


async def test_analyze_rejections_multiple_categories(spec: SpecializationManager, db: Database):
    await _create_task(db, "T-010", status="rejected", rejection_reason="Missing test", assigned_to="coder")
    await _create_task(db, "T-011", status="rejected", rejection_reason="Bad style formatting", assigned_to="coder")
    await _create_task(db, "T-012", status="failed", rejection_reason="Security vulnerability", assigned_to="coder")
    await _create_task(db, "T-013", status="rejected", rejection_reason="Out of scope", assigned_to="coder")

    result = await spec.analyze_rejections("coder")
    assert result["rejections"] == 4
    categories = {p["category"] for p in result["patterns"]}
    assert "testing" in categories
    assert "code_style" in categories
    assert "security" in categories
    assert "scope_adherence" in categories


async def test_analyze_rejections_suggestions_threshold(spec: SpecializationManager, db: Database):
    """Suggestions only appear when a category has >= 2 rejections."""
    await _create_task(db, "T-020", status="rejected", rejection_reason="No test", assigned_to="coder")
    await _create_task(db, "T-021", status="rejected", rejection_reason="Missing tests", assigned_to="coder")
    await _create_task(db, "T-022", status="failed", rejection_reason="Bad security", assigned_to="coder")

    result = await spec.analyze_rejections("coder")
    # 'testing' has 2 rejections -> should generate a suggestion
    assert any("testing" in s for s in result["suggestions"])
    # 'security' has 1 rejection -> no suggestion
    assert not any("security" in s for s in result["suggestions"])


async def test_analyze_rejections_ignores_other_roles(spec: SpecializationManager, db: Database):
    await _create_task(db, "T-030", status="rejected", rejection_reason="No tests", assigned_to="coder")
    await _create_task(db, "T-031", status="rejected", rejection_reason="No tests", assigned_to="reviewer")

    result = await spec.analyze_rejections("coder")
    assert result["rejections"] == 1


# ---- Role Gaps ----


async def test_detect_role_gaps(spec: SpecializationManager, db: Database):
    # Create tasks with high failure rate for one role
    for i in range(5):
        status = "failed" if i < 3 else "completed"
        await _create_task(
            db,
            f"G-{i:03d}",
            status=status,
            assigned_to="bad_role",
            rejection_reason="Failed" if status == "failed" else None,
        )

    # Create tasks with low failure rate for another
    for i in range(5):
        await _create_task(db, f"H-{i:03d}", status="completed", assigned_to="good_role")

    gaps = await spec.detect_role_gaps()
    assert len(gaps) >= 1
    assert gaps[0]["role"] == "bad_role"
    assert gaps[0]["failure_rate"] == 0.6


async def test_detect_role_gaps_empty(spec: SpecializationManager):
    gaps = await spec.detect_role_gaps()
    assert gaps == []


async def test_detect_role_gaps_below_threshold(spec: SpecializationManager, db: Database):
    """Roles with <= 30% failure rate should not appear in gaps."""
    for i in range(10):
        status = "failed" if i < 2 else "completed"
        await _create_task(
            db,
            f"OK-{i:03d}",
            status=status,
            assigned_to="ok_role",
            rejection_reason="Failed" if status == "failed" else None,
        )

    gaps = await spec.detect_role_gaps()
    assert len(gaps) == 0


async def test_detect_role_gaps_minimum_tasks(spec: SpecializationManager, db: Database):
    """Roles with fewer than 3 tasks should not appear in gaps even if all failed."""
    await _create_task(db, "FEW-001", status="failed", assigned_to="tiny_role", rejection_reason="Error")
    await _create_task(db, "FEW-002", status="failed", assigned_to="tiny_role", rejection_reason="Error")

    gaps = await spec.detect_role_gaps()
    assert len(gaps) == 0


async def test_detect_role_gaps_sorted_by_failure_rate(spec: SpecializationManager, db: Database):
    """Gaps should be sorted by failure_rate descending."""
    # Role with 60% failure
    for i in range(5):
        status = "failed" if i < 3 else "completed"
        await _create_task(db, f"MID-{i:03d}", status=status, assigned_to="mid_role",
                          rejection_reason="Error" if status == "failed" else None)

    # Role with 80% failure
    for i in range(5):
        status = "failed" if i < 4 else "completed"
        await _create_task(db, f"BAD-{i:03d}", status=status, assigned_to="worst_role",
                          rejection_reason="Error" if status == "failed" else None)

    gaps = await spec.detect_role_gaps()
    assert len(gaps) == 2
    assert gaps[0]["role"] == "worst_role"
    assert gaps[1]["role"] == "mid_role"
