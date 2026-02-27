"""Tests for the SelfImprovementManager (features 1-8)."""

from __future__ import annotations

import pytest

from taskbrew.orchestrator.database import Database
from taskbrew.intelligence.self_improvement import SelfImprovementManager


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture
async def db(tmp_path):
    database = Database(str(tmp_path / "test.db"))
    await database.initialize()
    yield database
    await database.close()


@pytest.fixture
async def mgr(db: Database) -> SelfImprovementManager:
    m = SelfImprovementManager(db)
    await m.ensure_tables()
    return m


# ------------------------------------------------------------------
# Tests: Feature 1 - Prompt Evolution Engine
# ------------------------------------------------------------------


async def test_store_and_get_prompt_history(mgr: SelfImprovementManager):
    """store_prompt_version persists a version and get_prompt_history retrieves it."""
    v = await mgr.store_prompt_version("coder", "You are a coder agent.", version_tag="v1")
    assert v["id"].startswith("PV-")
    assert v["agent_role"] == "coder"

    history = await mgr.get_prompt_history("coder")
    assert len(history) == 1
    assert history[0]["prompt_text"] == "You are a coder agent."


async def test_get_best_prompt_requires_min_trials(mgr: SelfImprovementManager):
    """get_best_prompt returns None when fewer than 5 trials exist."""
    v = await mgr.store_prompt_version("coder", "prompt text", version_tag="v1")
    # Only 3 trials - below minimum of 5
    for i in range(3):
        await mgr.record_prompt_outcome(v["id"], f"task-{i}", success=True)

    best = await mgr.get_best_prompt("coder")
    assert best is None


async def test_get_best_prompt_selects_highest_success_rate(mgr: SelfImprovementManager):
    """get_best_prompt returns the version with the highest success rate when min trials met."""
    v1 = await mgr.store_prompt_version("coder", "prompt v1", version_tag="v1")
    v2 = await mgr.store_prompt_version("coder", "prompt v2", version_tag="v2")

    # v1: 3/5 success = 60%
    for i in range(3):
        await mgr.record_prompt_outcome(v1["id"], f"task-a{i}", success=True)
    for i in range(2):
        await mgr.record_prompt_outcome(v1["id"], f"task-b{i}", success=False)

    # v2: 5/5 success = 100%
    for i in range(5):
        await mgr.record_prompt_outcome(v2["id"], f"task-c{i}", success=True)

    best = await mgr.get_best_prompt("coder")
    assert best is not None
    assert best["id"] == v2["id"]
    assert best["success_rate"] == 1.0


# ------------------------------------------------------------------
# Tests: Feature 2 - Strategy Portfolio Manager
# ------------------------------------------------------------------


async def test_register_and_get_portfolio(mgr: SelfImprovementManager):
    """register_strategy adds a strategy retrievable via get_portfolio."""
    s = await mgr.register_strategy("coder", "TDD", "development", "Test-driven development")
    assert s["id"].startswith("STR-")

    portfolio = await mgr.get_portfolio("coder")
    assert len(portfolio) == 1
    assert portfolio[0]["strategy_name"] == "TDD"


async def test_select_strategy_by_success_rate(mgr: SelfImprovementManager):
    """select_strategy returns the strategy with the highest success rate."""
    s1 = await mgr.register_strategy("coder", "TDD", "dev", "Test driven")
    s2 = await mgr.register_strategy("coder", "BDD", "dev", "Behavior driven")

    # s1: 1 success, 3 failures
    await mgr.record_strategy_use(s1["id"], "task-1", True)
    for i in range(3):
        await mgr.record_strategy_use(s1["id"], f"task-f{i}", False)

    # s2: 4 successes, 0 failures
    for i in range(4):
        await mgr.record_strategy_use(s2["id"], f"task-s{i}", True)

    best = await mgr.select_strategy("coder")
    assert best is not None
    assert best["strategy_name"] == "BDD"


# ------------------------------------------------------------------
# Tests: Feature 3 - Skill Transfer Protocol
# ------------------------------------------------------------------


async def test_create_and_get_pending_transfers(mgr: SelfImprovementManager):
    """create_transfer creates a pending transfer retrievable by target role."""
    t = await mgr.create_transfer("architect", "coder", "design_patterns", "Use factory pattern for X")
    assert t["status"] == "pending"

    pending = await mgr.get_pending_transfers("coder")
    assert len(pending) == 1
    assert pending[0]["skill_area"] == "design_patterns"


async def test_acknowledge_transfer_marks_applied(mgr: SelfImprovementManager):
    """acknowledge_transfer changes status to applied and clears from pending."""
    t = await mgr.create_transfer("reviewer", "coder", "code_style", "Use type hints everywhere")
    result = await mgr.acknowledge_transfer(t["id"], applied=True)
    assert result["status"] == "applied"

    pending = await mgr.get_pending_transfers("coder")
    assert len(pending) == 0


# ------------------------------------------------------------------
# Tests: Feature 4 - Cognitive Load Balancer
# ------------------------------------------------------------------


async def test_record_and_get_load_history(mgr: SelfImprovementManager):
    """record_load stores a snapshot and get_load_history retrieves it."""
    snap = await mgr.record_load("agent-1", 50000, 100000, 10, task_id="task-1")
    assert snap["load_ratio"] == 0.5
    assert snap["id"].startswith("CL-")

    history = await mgr.get_load_history("agent-1")
    assert len(history) == 1


async def test_recommend_eviction_when_overloaded(mgr: SelfImprovementManager):
    """recommend_eviction suggests eviction when load ratio >= 0.8."""
    await mgr.record_load("agent-1", 95000, 100000, 20)
    rec = await mgr.recommend_eviction("agent-1")
    assert rec["recommendation"] == "evict_oldest"
    assert rec["items_to_evict"] >= 1


async def test_recommend_eviction_no_data(mgr: SelfImprovementManager):
    """recommend_eviction returns no_data when no snapshots exist."""
    rec = await mgr.recommend_eviction("nonexistent-agent")
    assert rec["recommendation"] == "no_data"


# ------------------------------------------------------------------
# Tests: Feature 5 - Reflection Engine
# ------------------------------------------------------------------


async def test_create_and_get_reflections(mgr: SelfImprovementManager):
    """create_reflection stores a reflection and get_reflections retrieves it."""
    r = await mgr.create_reflection(
        "task-1", "coder-1", "Clear specs", "Slow feedback loop", "Write tests early", 4.0
    )
    assert r["id"].startswith("REF-")
    assert r["approach_rating"] == 4.0

    refs = await mgr.get_reflections(agent_id="coder-1")
    assert len(refs) == 1
    assert refs[0]["what_worked"] == "Clear specs"


async def test_find_relevant_reflections_by_keyword(mgr: SelfImprovementManager):
    """find_relevant_reflections returns reflections matching keywords in lessons."""
    await mgr.create_reflection("t1", "a1", "good", "bad", "Testing with pytest helped", 3.0)
    await mgr.create_reflection("t2", "a2", "good", "bad", "Database migration was smooth", 4.0)

    results = await mgr.find_relevant_reflections("pytest testing strategies")
    assert len(results) >= 1
    assert any("pytest" in r["lessons"] for r in results)


async def test_find_relevant_reflections_empty_query(mgr: SelfImprovementManager):
    """find_relevant_reflections returns empty list for short words only."""
    await mgr.create_reflection("t1", "a1", "good", "bad", "Some lessons", 3.0)
    results = await mgr.find_relevant_reflections("a b c")
    assert results == []


# ------------------------------------------------------------------
# Tests: Feature 6 - Failure Mode Taxonomy
# ------------------------------------------------------------------


async def test_classify_and_get_taxonomy(mgr: SelfImprovementManager):
    """classify_failure stores a failure and get_taxonomy groups by category."""
    f = await mgr.classify_failure("task-1", "runtime", "timeout", "API call timed out", "high")
    assert f["id"].startswith("FM-")

    taxonomy = await mgr.get_taxonomy()
    assert len(taxonomy) >= 1
    assert taxonomy[0]["category"] == "runtime"


async def test_get_recovery_playbook_empty(mgr: SelfImprovementManager):
    """get_recovery_playbook returns empty when no recovered failures exist."""
    await mgr.classify_failure("task-1", "runtime", "timeout", "Timed out", "high")
    playbook = await mgr.get_recovery_playbook("runtime")
    assert playbook == []


# ------------------------------------------------------------------
# Tests: Feature 7 - Agent Personality Profiler
# ------------------------------------------------------------------


async def test_update_and_get_profile(mgr: SelfImprovementManager):
    """update_profile stores traits and get_profile returns all traits."""
    await mgr.update_profile("coder", "thoroughness", 0.85)
    await mgr.update_profile("coder", "speed", 0.7)

    profile = await mgr.get_profile("coder")
    assert profile["agent_role"] == "coder"
    assert profile["trait_count"] == 2
    assert profile["traits"]["thoroughness"] == 0.85
    assert profile["traits"]["speed"] == 0.7


async def test_match_task_to_agent(mgr: SelfImprovementManager):
    """match_task_to_agent ranks agents by how well their traits match requirements."""
    await mgr.update_profile("coder", "thoroughness", 0.9)
    await mgr.update_profile("coder", "speed", 0.5)
    await mgr.update_profile("reviewer", "thoroughness", 0.95)
    await mgr.update_profile("reviewer", "speed", 0.3)

    matches = await mgr.match_task_to_agent(
        "code_review", {"thoroughness": 0.8, "speed": 0.4}
    )
    assert len(matches) == 2
    # Both meet thoroughness, but only coder meets speed >= 0.4
    coder_match = next(m for m in matches if m["agent_role"] == "coder")
    assert coder_match["match_score"] == 1.0  # 2/2 traits met


async def test_update_profile_upsert(mgr: SelfImprovementManager):
    """update_profile updates existing trait value rather than creating duplicate."""
    await mgr.update_profile("coder", "speed", 0.5)
    await mgr.update_profile("coder", "speed", 0.9)

    profile = await mgr.get_profile("coder")
    assert profile["trait_count"] == 1
    assert profile["traits"]["speed"] == 0.9


# ------------------------------------------------------------------
# Tests: Feature 8 - Confidence Calibration Tracker
# ------------------------------------------------------------------


async def test_record_and_get_calibration_history(mgr: SelfImprovementManager):
    """record_confidence persists a record and get_calibration_history retrieves it."""
    r = await mgr.record_confidence("agent-1", "task-1", 0.8, True)
    assert r["id"].startswith("CC-")
    assert r["predicted_confidence"] == 0.8

    history = await mgr.get_calibration_history("agent-1")
    assert len(history) == 1


async def test_get_calibration_score_brier(mgr: SelfImprovementManager):
    """get_calibration_score computes correct Brier score."""
    # Perfect calibration: predicted 0.9, outcome 1 -> error = 0.01
    await mgr.record_confidence("agent-1", "task-1", 0.9, True)
    # Bad calibration: predicted 0.9, outcome 0 -> error = 0.81
    await mgr.record_confidence("agent-1", "task-2", 0.9, False)

    score = await mgr.get_calibration_score("agent-1")
    assert score["sample_count"] == 2
    # Mean of 0.01 and 0.81 = 0.41
    assert abs(score["brier_score"] - 0.41) < 0.01


async def test_get_calibration_score_no_data(mgr: SelfImprovementManager):
    """get_calibration_score returns None when no records exist."""
    score = await mgr.get_calibration_score("nonexistent")
    assert score["brier_score"] is None
    assert score["sample_count"] == 0
