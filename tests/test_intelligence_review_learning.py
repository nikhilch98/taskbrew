"""Tests for the ReviewLearningManager."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from taskbrew.orchestrator.database import Database
from taskbrew.intelligence.review_learning import ReviewLearningManager


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
async def rl(db: Database) -> ReviewLearningManager:
    """Create a ReviewLearningManager backed by the in-memory database."""
    return ReviewLearningManager(db)


async def _create_dummy_task(db: Database, task_id: str) -> None:
    """Insert a minimal task row so foreign key constraints are satisfied."""
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "INSERT OR IGNORE INTO groups (id, title, status, created_at) VALUES (?, ?, ?, ?)",
        (f"GRP-{task_id}", "Test Group", "active", now),
    )
    await db.execute(
        "INSERT OR IGNORE INTO tasks (id, group_id, title, status, created_at) VALUES (?, ?, ?, ?, ?)",
        (task_id, f"GRP-{task_id}", "Test Task", "pending", now),
    )


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------


async def test_extract_feedback_missing_tests(rl: ReviewLearningManager, db: Database):
    """Detect 'missing_tests' pattern from review text."""
    await _create_dummy_task(db, "RV-001")
    patterns = await rl.extract_feedback(
        "RV-001", "alice", "This PR has no test coverage. Please add tests."
    )

    types = [p["feedback_type"] for p in patterns]
    assert "missing_tests" in types


async def test_extract_feedback_multiple_patterns(rl: ReviewLearningManager, db: Database):
    """Detect multiple patterns from a single review."""
    await _create_dummy_task(db, "RV-002")
    patterns = await rl.extract_feedback(
        "RV-002",
        "bob",
        "Missing test for the new endpoint. Also has a security issue with SQL injection.",
    )

    types = [p["feedback_type"] for p in patterns]
    assert "missing_tests" in types
    assert "security" in types
    assert len(types) >= 2


async def test_extract_feedback_increments_frequency(rl: ReviewLearningManager, db: Database):
    """Repeated extractions for the same reviewer should increment frequency."""
    await _create_dummy_task(db, "RV-003")
    await _create_dummy_task(db, "RV-004")

    patterns1 = await rl.extract_feedback(
        "RV-003", "alice", "No tests provided."
    )
    freq1 = next(p["frequency"] for p in patterns1 if p["feedback_type"] == "missing_tests")
    assert freq1 == 1

    patterns2 = await rl.extract_feedback(
        "RV-004", "alice", "Still missing test coverage."
    )
    freq2 = next(p["frequency"] for p in patterns2 if p["feedback_type"] == "missing_tests")
    assert freq2 == 2


async def test_extract_feedback_no_match(rl: ReviewLearningManager, db: Database):
    """Review text with no known patterns returns empty list."""
    await _create_dummy_task(db, "RV-005")
    patterns = await rl.extract_feedback(
        "RV-005", "charlie", "Looks good to me! Ship it."
    )
    assert patterns == []


async def test_get_top_patterns_sorted(rl: ReviewLearningManager, db: Database):
    """Top patterns are sorted by total frequency descending."""
    await _create_dummy_task(db, "RV-010")
    await _create_dummy_task(db, "RV-011")
    await _create_dummy_task(db, "RV-012")

    # Create patterns with different frequencies
    await rl.extract_feedback("RV-010", "alice", "Missing test coverage")
    await rl.extract_feedback("RV-011", "alice", "Add tests for this")
    await rl.extract_feedback("RV-012", "alice", "Security issue with injection")

    top = await rl.get_top_patterns(reviewer="alice")
    assert len(top) >= 2
    # missing_tests was flagged twice, security once
    assert top[0]["feedback_type"] == "missing_tests"
    freqs = [p["total_frequency"] for p in top]
    assert freqs == sorted(freqs, reverse=True)


async def test_get_top_patterns_all_reviewers(rl: ReviewLearningManager, db: Database):
    """get_top_patterns without reviewer returns patterns from all reviewers."""
    await _create_dummy_task(db, "RV-020")
    await _create_dummy_task(db, "RV-021")

    await rl.extract_feedback("RV-020", "alice", "No tests.")
    await rl.extract_feedback("RV-021", "bob", "Missing test for this feature.")

    top = await rl.get_top_patterns()
    assert len(top) >= 1
    # Both alice and bob flagged missing_tests
    mt = next(p for p in top if p["feedback_type"] == "missing_tests")
    assert mt["total_frequency"] >= 2


async def test_get_feedback_for_context(rl: ReviewLearningManager, db: Database):
    """get_feedback_for_context generates a human-readable context string."""
    await _create_dummy_task(db, "RV-030")
    await rl.extract_feedback(
        "RV-030", "alice", "Missing test and error handling is poor."
    )

    context = await rl.get_feedback_for_context("alice")
    assert "Common review feedback patterns" in context
    assert "missing_tests" in context
    assert "error_handling" in context


async def test_get_feedback_for_context_empty(rl: ReviewLearningManager):
    """get_feedback_for_context returns empty string when no patterns exist."""
    context = await rl.get_feedback_for_context("nobody")
    assert context == ""


async def test_get_reviewer_stats(rl: ReviewLearningManager, db: Database):
    """get_reviewer_stats returns structured statistics."""
    await _create_dummy_task(db, "RV-040")
    await _create_dummy_task(db, "RV-041")

    await rl.extract_feedback("RV-040", "dave", "No docstring on the class.")
    await rl.extract_feedback("RV-041", "dave", "Missing doc for the helper.")

    stats = await rl.get_reviewer_stats("dave")
    assert stats["reviewer"] == "dave"
    assert stats["unique_patterns"] >= 1
    assert stats["total_feedback_instances"] >= 2
    assert len(stats["top_patterns"]) >= 1


async def test_get_reviewer_stats_unknown(rl: ReviewLearningManager):
    """Stats for unknown reviewer returns zero counts."""
    stats = await rl.get_reviewer_stats("unknown_reviewer")
    assert stats["reviewer"] == "unknown_reviewer"
    assert stats["unique_patterns"] == 0
    assert stats["total_feedback_instances"] == 0
    assert stats["top_patterns"] == []
