"""Tests for the ProcessIntelligenceManager."""

from __future__ import annotations

import pytest

from taskbrew.orchestrator.database import Database
from taskbrew.intelligence.process_intelligence import ProcessIntelligenceManager


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture
async def manager(tmp_path):
    db = Database(":memory:")
    await db.initialize()
    mgr = ProcessIntelligenceManager(db)
    await mgr.ensure_tables()
    yield mgr
    await db.close()


# ------------------------------------------------------------------
# Feature 39: Velocity Forecaster
# ------------------------------------------------------------------


async def test_record_velocity(manager):
    """record_velocity persists a velocity sample."""
    result = await manager.record_velocity("sprint-1", 10, story_points=20, duration_days=14)
    assert result["id"].startswith("VS-")
    assert result["sprint_id"] == "sprint-1"
    assert result["tasks_completed"] == 10
    assert result["story_points"] == 20
    assert result["duration_days"] == 14


async def test_forecast_monte_carlo(manager):
    """forecast returns p50/p75/p90 estimates based on historical velocity."""
    # Record some velocity history
    for i in range(10):
        await manager.record_velocity(f"sprint-{i}", 5 + i, story_points=10 + i)

    result = await manager.forecast(remaining_points=30, num_simulations=500)
    assert result["p50"] is not None
    assert result["p75"] is not None
    assert result["p90"] is not None
    assert result["p50"] <= result["p75"] <= result["p90"]
    assert result["p50"] >= 1


async def test_forecast_no_data(manager):
    """forecast returns error when no historical data exists."""
    result = await manager.forecast(remaining_points=100)
    assert result["p50"] is None
    assert "error" in result


async def test_get_velocity_history(manager):
    """get_velocity_history returns recorded samples."""
    await manager.record_velocity("sprint-1", 10, story_points=20)
    await manager.record_velocity("sprint-2", 12, story_points=25)

    history = await manager.get_velocity_history()
    assert len(history) == 2


# ------------------------------------------------------------------
# Feature 40: Risk Heat Map Generator
# ------------------------------------------------------------------


async def test_score_file_computes_risk(manager):
    """score_file computes composite risk correctly."""
    result = await manager.score_file("src/app.py", change_frequency=10, complexity_score=5.0, test_coverage_pct=50)
    # risk = 10 * 5.0 * (1 - 0.5) = 25.0
    assert result["risk_score"] == 25.0
    assert result["file_path"] == "src/app.py"


async def test_score_file_full_coverage_zero_risk(manager):
    """score_file returns zero risk for 100% test coverage."""
    result = await manager.score_file("src/safe.py", change_frequency=100, complexity_score=10.0, test_coverage_pct=100)
    assert result["risk_score"] == 0.0


async def test_get_heat_map_sorted(manager):
    """get_heat_map returns files sorted by risk descending."""
    await manager.score_file("high.py", 20, 8.0, 10)
    await manager.score_file("low.py", 2, 1.0, 90)
    await manager.score_file("mid.py", 10, 5.0, 50)

    heatmap = await manager.get_heat_map()
    assert len(heatmap) == 3
    assert heatmap[0]["file_path"] == "high.py"
    assert heatmap[-1]["file_path"] == "low.py"


async def test_refresh_scores(manager):
    """refresh_scores recalculates all risk scores."""
    await manager.score_file("a.py", 10, 5.0, 50)
    await manager.score_file("b.py", 5, 3.0, 80)

    result = await manager.refresh_scores()
    assert result["updated"] == 2


# ------------------------------------------------------------------
# Feature 41: Process Bottleneck Miner
# ------------------------------------------------------------------


async def test_record_phase_duration(manager):
    """record_phase_duration persists phase timing data."""
    result = await manager.record_phase_duration("task-1", "coding", 5000)
    assert result["id"].startswith("PM-")
    assert result["phase"] == "coding"
    assert result["duration_ms"] == 5000


async def test_find_bottlenecks(manager):
    """find_bottlenecks returns phases sorted by avg duration descending."""
    await manager.record_phase_duration("t1", "coding", 5000)
    await manager.record_phase_duration("t2", "coding", 7000)
    await manager.record_phase_duration("t1", "review", 10000)
    await manager.record_phase_duration("t2", "review", 12000)
    await manager.record_phase_duration("t1", "testing", 2000)

    bottlenecks = await manager.find_bottlenecks()
    assert len(bottlenecks) == 3
    # Review is slowest
    assert bottlenecks[0]["phase"] == "review"
    assert bottlenecks[0]["avg_duration_ms"] == 11000.0


async def test_get_phase_stats(manager):
    """get_phase_stats returns avg, median, p95 for a phase."""
    for dur in [1000, 2000, 3000, 4000, 5000]:
        await manager.record_phase_duration(f"t-{dur}", "coding", dur)

    stats = await manager.get_phase_stats(phase="coding")
    assert len(stats) == 1
    assert stats[0]["phase"] == "coding"
    assert stats[0]["avg_ms"] == 3000.0
    assert stats[0]["median_ms"] == 3000.0
    assert stats[0]["sample_count"] == 5


async def test_get_phase_stats_all_phases(manager):
    """get_phase_stats returns stats for all phases when no filter."""
    await manager.record_phase_duration("t1", "coding", 5000)
    await manager.record_phase_duration("t1", "review", 8000)

    stats = await manager.get_phase_stats()
    assert len(stats) == 2
    phase_names = {s["phase"] for s in stats}
    assert phase_names == {"coding", "review"}


# ------------------------------------------------------------------
# Feature 42: Release Readiness Scorer
# ------------------------------------------------------------------


async def test_assess_readiness_high_score(manager):
    """assess_readiness returns high score for good metrics."""
    result = await manager.assess_readiness("v1.0", {
        "test_pass_rate": 98,
        "open_bugs": 0,
        "doc_freshness": 90,
        "code_review_coverage": 95,
        "security_scan": 100,
    })
    assert result["score"] > 90
    assert result["release_id"] == "v1.0"
    assert "breakdown" in result


async def test_assess_readiness_low_score(manager):
    """assess_readiness returns low score for poor metrics."""
    result = await manager.assess_readiness("v0.1", {
        "test_pass_rate": 20,
        "open_bugs": 15,
        "doc_freshness": 10,
        "code_review_coverage": 5,
        "security_scan": 0,
    })
    assert result["score"] < 30


async def test_get_assessment(manager):
    """get_assessment retrieves a stored assessment."""
    await manager.assess_readiness("v2.0", {
        "test_pass_rate": 80,
        "open_bugs": 2,
        "doc_freshness": 70,
        "code_review_coverage": 85,
        "security_scan": 90,
    })

    assessment = await manager.get_assessment("v2.0")
    assert assessment is not None
    assert assessment["release_id"] == "v2.0"


async def test_get_readiness_history(manager):
    """get_history returns past assessments."""
    await manager.assess_readiness("v1.0", {"test_pass_rate": 90, "open_bugs": 1, "doc_freshness": 80, "code_review_coverage": 85, "security_scan": 95})
    await manager.assess_readiness("v2.0", {"test_pass_rate": 95, "open_bugs": 0, "doc_freshness": 90, "code_review_coverage": 90, "security_scan": 100})

    history = await manager.get_history()
    assert len(history) == 2


# ------------------------------------------------------------------
# Feature 43: Stakeholder Impact Assessor
# ------------------------------------------------------------------


async def test_record_impact(manager):
    """record_impact stores a stakeholder impact record."""
    result = await manager.record_impact(
        "change-1", "end-users", "high", "Breaking API change"
    )
    assert result["id"].startswith("SI-")
    assert result["impact_level"] == "high"
    assert result["stakeholder_group"] == "end-users"


async def test_record_impact_invalid_level(manager):
    """record_impact raises ValueError for invalid impact level."""
    with pytest.raises(ValueError, match="Invalid impact_level"):
        await manager.record_impact("change-1", "users", "catastrophic", "Oops")


async def test_get_impacts_filtered(manager):
    """get_impacts filters by change_id and stakeholder_group."""
    await manager.record_impact("c1", "developers", "low", "Minor")
    await manager.record_impact("c1", "end-users", "high", "Breaking")
    await manager.record_impact("c2", "developers", "medium", "Moderate")

    by_change = await manager.get_impacts(change_id="c1")
    assert len(by_change) == 2

    by_group = await manager.get_impacts(stakeholder_group="developers")
    assert len(by_group) == 2


async def test_get_most_impacted(manager):
    """get_most_impacted returns groups sorted by total impact count."""
    await manager.record_impact("c1", "developers", "low", "A")
    await manager.record_impact("c2", "developers", "medium", "B")
    await manager.record_impact("c3", "developers", "high", "C")
    await manager.record_impact("c1", "end-users", "low", "D")

    most_impacted = await manager.get_most_impacted()
    assert len(most_impacted) == 2
    assert most_impacted[0]["stakeholder_group"] == "developers"
    assert most_impacted[0]["impact_count"] == 3


# ------------------------------------------------------------------
# Feature 44: Sprint Retrospective Generator
# ------------------------------------------------------------------


async def test_generate_retro(manager):
    """generate_retro creates a retrospective from task data."""
    tasks = [
        {"title": "Fast task", "status": "completed", "duration_ms": 1000, "failure_count": 0},
        {"title": "Slow task", "status": "completed", "duration_ms": 10000, "failure_count": 0},
        {"title": "Failing task", "status": "completed", "duration_ms": 5000, "failure_count": 3},
        {"title": "Blocked task", "status": "blocked", "duration_ms": None, "failure_count": 0},
    ]

    result = await manager.generate_retro("sprint-5", tasks)
    assert result["sprint_id"] == "sprint-5"
    assert "Fast task" in result["what_improved"]
    assert "Failing task" in result["what_regressed"]
    assert "Blocked task" in result["stalled"]
    assert len(result["recommendations"]) >= 1


async def test_generate_retro_empty_tasks(manager):
    """generate_retro handles empty task data gracefully."""
    result = await manager.generate_retro("sprint-empty", [])
    assert result["what_improved"] == []
    assert result["what_regressed"] == []
    assert result["stalled"] == []
    assert len(result["recommendations"]) >= 1


async def test_get_retro(manager):
    """get_retro retrieves a stored retrospective."""
    await manager.generate_retro("sprint-10", [
        {"title": "Task A", "status": "completed", "duration_ms": 3000, "failure_count": 0},
    ])

    retro = await manager.get_retro("sprint-10")
    assert retro is not None
    assert retro["sprint_id"] == "sprint-10"
    assert isinstance(retro["what_improved"], list)


async def test_get_retros(manager):
    """get_retros lists all retrospectives."""
    await manager.generate_retro("s1", [{"title": "T1", "status": "completed", "duration_ms": 1000}])
    await manager.generate_retro("s2", [{"title": "T2", "status": "pending"}])

    retros = await manager.get_retros()
    assert len(retros) == 2
