"""Tests for the SocialIntelligenceManager (features 9-16)."""

from __future__ import annotations

import pytest

from taskbrew.orchestrator.database import Database
from taskbrew.intelligence.social_intelligence import SocialIntelligenceManager


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
async def mgr(db: Database) -> SocialIntelligenceManager:
    m = SocialIntelligenceManager(db)
    await m.ensure_tables()
    return m


# ------------------------------------------------------------------
# Tests: Feature 9 - Argument Resolution Protocol
# ------------------------------------------------------------------


async def test_open_and_resolve_argument(mgr: SocialIntelligenceManager):
    """open_argument + submit_evidence + resolve_argument picks the highest weighted position."""
    arg = await mgr.open_argument("Use REST vs GraphQL", ["coder-1", "architect-1"])
    assert arg["id"].startswith("ARG-")
    assert arg["status"] == "open"

    await mgr.submit_evidence(arg["id"], "coder-1", "REST", "Simpler to implement", 0.7)
    await mgr.submit_evidence(arg["id"], "architect-1", "GraphQL", "Better for complex queries", 0.9)

    result = await mgr.resolve_argument(arg["id"])
    assert result["status"] == "resolved"
    assert result["winner"] == "GraphQL"


async def test_resolve_argument_no_evidence(mgr: SocialIntelligenceManager):
    """resolve_argument returns no_evidence when session has no submissions."""
    arg = await mgr.open_argument("Empty debate", ["agent-1"])
    result = await mgr.resolve_argument(arg["id"])
    assert result["status"] == "no_evidence"
    assert result["winner"] is None


async def test_get_argument_history(mgr: SocialIntelligenceManager):
    """get_argument_history returns all sessions."""
    await mgr.open_argument("Topic A", ["a1"])
    await mgr.open_argument("Topic B", ["a2"])

    history = await mgr.get_argument_history()
    assert len(history) == 2


# ------------------------------------------------------------------
# Tests: Feature 10 - Trust Score Network
# ------------------------------------------------------------------


async def test_update_and_get_trust(mgr: SocialIntelligenceManager):
    """update_trust creates a trust score and get_trust retrieves it."""
    result = await mgr.update_trust("coder-1", "reviewer-1", "code_review", 0.8)
    assert result["score"] == 0.8
    assert result["interaction_count"] == 1

    trust = await mgr.get_trust("coder-1", "reviewer-1")
    assert trust is not None
    assert trust["score"] == 0.8


async def test_update_trust_ema_update(mgr: SocialIntelligenceManager):
    """update_trust applies exponential moving average on subsequent updates."""
    await mgr.update_trust("a1", "a2", "review", 0.8)  # first: score = 0.8
    result = await mgr.update_trust("a1", "a2", "review", 0.2)  # EMA update

    # EMA: 0.3 * 0.2 + 0.7 * 0.8 = 0.06 + 0.56 = 0.62
    assert abs(result["score"] - 0.62) < 0.01
    assert result["interaction_count"] == 2


async def test_get_most_trusted(mgr: SocialIntelligenceManager):
    """get_most_trusted returns agents ordered by trust score."""
    await mgr.update_trust("coder-1", "reviewer-1", "review", 0.9)
    await mgr.update_trust("coder-1", "tester-1", "test", 0.5)

    most_trusted = await mgr.get_most_trusted("coder-1", limit=5)
    assert len(most_trusted) == 2
    assert most_trusted[0]["to_agent"] == "reviewer-1"


# ------------------------------------------------------------------
# Tests: Feature 11 - Communication Style Adapter
# ------------------------------------------------------------------


async def test_record_and_get_style(mgr: SocialIntelligenceManager):
    """record_preference stores preferences and get_style returns them as dict."""
    await mgr.record_preference("coder", "verbosity", "low")
    await mgr.record_preference("coder", "format", "bullet_points")

    style = await mgr.get_style("coder")
    assert style["verbosity"] == "low"
    assert style["format"] == "bullet_points"


async def test_adapt_message_with_defaults(mgr: SocialIntelligenceManager):
    """adapt_message returns defaults when no preferences are recorded."""
    result = await mgr.adapt_message("unknown_role", "status_update")
    assert result["verbosity"] == "medium"
    assert result["format"] == "structured"
    assert result["target_role"] == "unknown_role"


# ------------------------------------------------------------------
# Tests: Feature 12 - Shared Mental Model Builder
# ------------------------------------------------------------------


async def test_assert_and_get_model(mgr: SocialIntelligenceManager):
    """assert_fact stores a fact and get_model retrieves it."""
    f = await mgr.assert_fact("project.language", "python", "architect-1")
    assert f["updated"] is False

    model = await mgr.get_model()
    assert len(model) == 1
    assert model[0]["value"] == "python"


async def test_get_conflicts_detects_disagreement(mgr: SocialIntelligenceManager):
    """get_conflicts finds keys where agents asserted different values."""
    await mgr.assert_fact("db.engine", "postgres", "architect-1", 0.9)
    await mgr.assert_fact("db.engine", "sqlite", "coder-1", 0.7)

    conflicts = await mgr.get_conflicts()
    assert len(conflicts) == 1
    assert conflicts[0]["key"] == "db.engine"
    assert conflicts[0]["distinct_values"] == 2


async def test_retract_fact_removes_from_model(mgr: SocialIntelligenceManager):
    """retract_fact marks a fact as retracted so it no longer appears in get_model."""
    await mgr.assert_fact("old.fact", "stale_value", "agent-1")
    await mgr.retract_fact("old.fact", "agent-1")

    model = await mgr.get_model()
    assert len(model) == 0


# ------------------------------------------------------------------
# Tests: Feature 13 - Implicit Coordination Detector
# ------------------------------------------------------------------


async def test_report_work_area_and_detect_overlaps(mgr: SocialIntelligenceManager):
    """detect_overlaps finds agents working on the same files."""
    await mgr.report_work_area("coder-1", ["src/main.py", "src/utils.py"], "task-1")
    await mgr.report_work_area("coder-2", ["src/main.py"], "task-2")

    overlaps = await mgr.detect_overlaps()
    assert len(overlaps) >= 1
    assert overlaps[0]["file"] == "src/main.py"
    assert set(overlaps[0]["agents"]) == {"coder-1", "coder-2"}


async def test_detect_overlaps_no_conflict(mgr: SocialIntelligenceManager):
    """detect_overlaps returns empty when agents work on different files."""
    await mgr.report_work_area("coder-1", ["src/a.py"], "task-1")
    await mgr.report_work_area("coder-2", ["src/b.py"], "task-2")

    overlaps = await mgr.detect_overlaps()
    assert len(overlaps) == 0


async def test_resolve_alert(mgr: SocialIntelligenceManager):
    """resolve_alert marks an alert as resolved."""
    await mgr.report_work_area("coder-1", ["file.py"], "t1")
    await mgr.report_work_area("coder-2", ["file.py"], "t2")
    overlaps = await mgr.detect_overlaps()
    assert len(overlaps) >= 1

    result = await mgr.resolve_alert(overlaps[0]["id"])
    assert result["resolved"] is True

    unresolved = await mgr.get_alerts(resolved=False)
    assert len(unresolved) == 0


# ------------------------------------------------------------------
# Tests: Feature 14 - Cross-Agent Context Bridge
# ------------------------------------------------------------------


async def test_share_and_get_context(mgr: SocialIntelligenceManager):
    """share_context stores a context item and get_shared_context retrieves it."""
    s = await mgr.share_context("architect-1", "coder-1", "api_design", "Use REST endpoints", 0.9)
    assert s["id"].startswith("CS-")

    shared = await mgr.get_shared_context("coder-1")
    assert len(shared) == 1
    assert shared[0]["context_key"] == "api_design"


async def test_acknowledge_context_marks_consumed(mgr: SocialIntelligenceManager):
    """acknowledge_context removes the item from unconsumed results."""
    s = await mgr.share_context("a1", "a2", "key1", "value1")
    await mgr.acknowledge_context(s["id"])

    shared = await mgr.get_shared_context("a2")
    assert len(shared) == 0


# ------------------------------------------------------------------
# Tests: Feature 15 - Collaboration Effectiveness Scorer
# ------------------------------------------------------------------


async def test_record_and_get_pair_score(mgr: SocialIntelligenceManager):
    """record_collaboration stores a score and get_pair_score returns the average."""
    await mgr.record_collaboration("coder-1", "reviewer-1", "task-1", 4.0)
    await mgr.record_collaboration("coder-1", "reviewer-1", "task-2", 3.0)

    score = await mgr.get_pair_score("coder-1", "reviewer-1")
    assert score["avg_effectiveness"] == 3.5
    assert score["collaboration_count"] == 2


async def test_get_best_and_worst_pairs(mgr: SocialIntelligenceManager):
    """get_best_pairs and get_worst_pairs return correctly ordered results."""
    await mgr.record_collaboration("a1", "a2", "t1", 4.5)
    await mgr.record_collaboration("a3", "a4", "t2", 1.5)

    best = await mgr.get_best_pairs(limit=5)
    assert len(best) == 2
    assert best[0]["avg_effectiveness"] > best[1]["avg_effectiveness"]

    worst = await mgr.get_worst_pairs(limit=5)
    assert worst[0]["avg_effectiveness"] < worst[1]["avg_effectiveness"]


async def test_get_pair_score_no_data(mgr: SocialIntelligenceManager):
    """get_pair_score returns None effectiveness when no collaborations exist."""
    score = await mgr.get_pair_score("unknown-a", "unknown-b")
    assert score["avg_effectiveness"] is None
    assert score["collaboration_count"] == 0


# ------------------------------------------------------------------
# Tests: Feature 16 - Consensus Prediction Engine
# ------------------------------------------------------------------


async def test_predict_consensus_and_record_outcome(mgr: SocialIntelligenceManager):
    """predict_consensus creates a prediction and record_prediction_outcome resolves it."""
    pred = await mgr.predict_consensus("Should we refactor?", ["coder-1", "architect-1"])
    assert pred["id"].startswith("CPR-")
    assert pred["predicted_outcome"] in ("likely_consensus", "likely_disagreement")

    outcome = await mgr.record_prediction_outcome(pred["id"], pred["predicted_outcome"])
    assert outcome["correct"] is True


async def test_get_prediction_accuracy(mgr: SocialIntelligenceManager):
    """get_prediction_accuracy returns correct percentage."""
    p1 = await mgr.predict_consensus("Proposal A", ["a1", "a2"])
    p2 = await mgr.predict_consensus("Proposal B", ["a1", "a2"])

    await mgr.record_prediction_outcome(p1["id"], p1["predicted_outcome"])  # correct
    await mgr.record_prediction_outcome(p2["id"], "wrong_answer")  # incorrect

    acc = await mgr.get_prediction_accuracy()
    assert acc["total_predictions"] == 2
    assert acc["correct"] == 1
    assert acc["accuracy"] == 50.0


async def test_get_prediction_accuracy_no_data(mgr: SocialIntelligenceManager):
    """get_prediction_accuracy returns None when no resolved predictions exist."""
    acc = await mgr.get_prediction_accuracy()
    assert acc["accuracy"] is None
    assert acc["total_predictions"] == 0
