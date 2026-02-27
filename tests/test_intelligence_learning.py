"""Tests for the LearningManager."""

from __future__ import annotations

import textwrap
import uuid
from datetime import datetime, timezone

import pytest

from taskbrew.orchestrator.database import Database
from taskbrew.intelligence.learning import LearningManager


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture
async def db():
    """Create and initialise an in-memory database."""
    database = Database(":memory:")
    await database.initialize()

    # Module-specific tables
    await database.executescript("""
        CREATE TABLE IF NOT EXISTS prompt_experiments (
            id TEXT PRIMARY KEY,
            experiment_name TEXT NOT NULL,
            agent_role TEXT NOT NULL,
            variant_key TEXT NOT NULL,
            prompt_text TEXT NOT NULL,
            trials INTEGER DEFAULT 0,
            successes INTEGER DEFAULT 0,
            avg_quality_score REAL DEFAULT 0.0,
            status TEXT DEFAULT 'active',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS cross_project_knowledge (
            id TEXT PRIMARY KEY,
            source_project TEXT NOT NULL,
            knowledge_type TEXT NOT NULL,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            applicability_score REAL DEFAULT 1.0,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS agent_benchmarks (
            id TEXT PRIMARY KEY,
            agent_role TEXT NOT NULL,
            metric_name TEXT NOT NULL,
            metric_value REAL NOT NULL,
            period TEXT NOT NULL,
            details TEXT,
            recorded_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS codebase_conventions (
            id TEXT PRIMARY KEY,
            convention_type TEXT NOT NULL,
            pattern TEXT NOT NULL,
            frequency INTEGER DEFAULT 0,
            confidence REAL DEFAULT 0.0,
            examples TEXT,
            last_updated TEXT NOT NULL,
            UNIQUE(convention_type, pattern)
        );

        CREATE TABLE IF NOT EXISTS error_clusters (
            id TEXT PRIMARY KEY,
            cluster_name TEXT NOT NULL UNIQUE,
            root_cause TEXT,
            error_pattern TEXT,
            occurrence_count INTEGER DEFAULT 0,
            last_seen TEXT,
            prevention_hint TEXT
        );
    """)

    yield database
    await database.close()


@pytest.fixture
async def learning(db: Database) -> LearningManager:
    """Create a LearningManager backed by the in-memory database."""
    return LearningManager(db)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


async def _seed_task(
    db: Database,
    status: str = "completed",
    assigned_to: str = "coder",
    task_type: str = "implementation",
    rejection_reason: str | None = None,
) -> str:
    """Insert a minimal group + task for testing."""
    now = datetime.now(timezone.utc).isoformat()
    group_id = f"GRP-{uuid.uuid4().hex[:4]}"
    await db.execute(
        "INSERT INTO groups (id, title, origin, status, created_at) VALUES (?, 'Test', 'test', 'active', ?)",
        (group_id, now),
    )
    task_id = f"TSK-{uuid.uuid4().hex[:4]}"
    await db.execute(
        "INSERT INTO tasks (id, group_id, title, task_type, priority, assigned_to, status, "
        "created_by, created_at, rejection_reason) "
        "VALUES (?, ?, 'Test Task', ?, 'medium', ?, ?, 'test', ?, ?)",
        (task_id, group_id, task_type, assigned_to, status, now, rejection_reason),
    )
    return task_id


# ------------------------------------------------------------------
# Feature 13: Prompt A/B Testing
# ------------------------------------------------------------------


async def test_create_experiment(learning: LearningManager, db: Database):
    """create_experiment inserts two variant rows."""
    result = await learning.create_experiment("tone-test", "coder", "Be formal", "Be casual")

    assert result["name"] == "tone-test"
    assert result["agent_role"] == "coder"
    assert "experiment_id" in result

    rows = await db.execute_fetchall(
        "SELECT * FROM prompt_experiments WHERE experiment_name = 'tone-test'"
    )
    assert len(rows) == 2
    keys = {r["variant_key"] for r in rows}
    assert keys == {"A", "B"}


async def test_record_trial(learning: LearningManager):
    """record_trial updates trials, successes, and running average."""
    exp = await learning.create_experiment("test-exp", "coder", "prompt A", "prompt B")
    eid = exp["experiment_id"]

    r1 = await learning.record_trial(eid, "A", True, quality_score=0.8)
    assert r1["trials"] == 1
    assert r1["successes"] == 1
    assert r1["avg_quality_score"] == pytest.approx(0.8)

    r2 = await learning.record_trial(eid, "A", False, quality_score=0.4)
    assert r2["trials"] == 2
    assert r2["successes"] == 1
    assert r2["avg_quality_score"] == pytest.approx(0.6)


async def test_get_winner(learning: LearningManager):
    """get_winner returns the variant with higher success rate + quality."""
    exp = await learning.create_experiment("winner-test", "coder", "prompt A", "prompt B")
    eid = exp["experiment_id"]

    # A: 2/3 success, avg quality 0.7
    await learning.record_trial(eid, "A", True, quality_score=0.8)
    await learning.record_trial(eid, "A", True, quality_score=0.7)
    await learning.record_trial(eid, "A", False, quality_score=0.6)

    # B: 1/3 success, avg quality 0.3
    await learning.record_trial(eid, "B", True, quality_score=0.5)
    await learning.record_trial(eid, "B", False, quality_score=0.2)
    await learning.record_trial(eid, "B", False, quality_score=0.2)

    winner = await learning.get_winner(eid)
    assert winner is not None
    assert winner["winner"] == "A"


# ------------------------------------------------------------------
# Feature 14: Cross-Project Knowledge Transfer
# ------------------------------------------------------------------


async def test_store_and_find_cross_project(learning: LearningManager):
    """store_cross_project persists and find_applicable retrieves by type."""
    await learning.store_cross_project("proj-a", "pattern", "Retry logic", "Use exponential backoff")
    await learning.store_cross_project("proj-b", "pattern", "Caching", "Use TTL-based cache")
    await learning.store_cross_project("proj-c", "anti-pattern", "God class", "Avoid large classes")

    results = await learning.find_applicable("pattern")
    assert len(results) == 2
    assert all(r["knowledge_type"] == "pattern" for r in results)


# ------------------------------------------------------------------
# Feature 15: Agent Performance Benchmarking
# ------------------------------------------------------------------


async def test_record_and_compare_benchmarks(learning: LearningManager):
    """record_benchmark and compare_agents return correct averages."""
    await learning.record_benchmark("coder", "completion_time", 120.0, "weekly")
    await learning.record_benchmark("coder", "completion_time", 80.0, "weekly")
    await learning.record_benchmark("reviewer", "completion_time", 60.0, "weekly")

    comparison = await learning.compare_agents("completion_time", period="weekly")
    assert len(comparison) == 2
    # Reviewer has lower avg time (60), coder has (100); sorted DESC so coder first
    assert comparison[0]["agent_role"] == "coder"
    assert comparison[0]["avg_value"] == pytest.approx(100.0)
    assert comparison[1]["agent_role"] == "reviewer"


async def test_compare_agents_no_period_filter(learning: LearningManager):
    """compare_agents without period merges all periods."""
    await learning.record_benchmark("coder", "quality", 0.9, "weekly")
    await learning.record_benchmark("coder", "quality", 0.8, "monthly")

    comparison = await learning.compare_agents("quality")
    assert len(comparison) == 1
    assert comparison[0]["avg_value"] == pytest.approx(0.85)


# ------------------------------------------------------------------
# Feature 16: Outcome-Based Strategy Adjustment
# ------------------------------------------------------------------


async def test_suggest_adjustments(learning: LearningManager, db: Database):
    """suggest_adjustments flags task types with <50% success rate."""
    # 3 completed, 4 failed for coder + implementation => ~43%
    for _ in range(3):
        await _seed_task(db, status="completed", assigned_to="coder", task_type="implementation")
    for _ in range(4):
        await _seed_task(db, status="failed", assigned_to="coder", task_type="implementation")
    # 5 completed, 0 failed for coder + code_review => 100%
    for _ in range(5):
        await _seed_task(db, status="completed", assigned_to="coder", task_type="code_review")

    suggestions = await learning.suggest_adjustments("coder")
    assert len(suggestions) == 1
    assert suggestions[0]["task_type"] == "implementation"
    assert suggestions[0]["success_rate"] < 0.5


# ------------------------------------------------------------------
# Feature 17: Review Feedback Loop
# ------------------------------------------------------------------


async def test_track_repeated_corrections(learning: LearningManager, db: Database):
    """track_repeated_corrections groups rejection reasons by keywords."""
    await _seed_task(db, status="rejected", assigned_to="coder", rejection_reason="Missing error handling in parser")
    await _seed_task(db, status="rejected", assigned_to="coder", rejection_reason="No error handling for edge cases")
    await _seed_task(db, status="rejected", assigned_to="coder", rejection_reason="Tests are incomplete and missing")

    patterns = await learning.track_repeated_corrections("coder")
    keywords = {p["pattern"] for p in patterns}
    assert "error" in keywords or "handling" in keywords or "missing" in keywords
    # At least one pattern should have count >= 2
    assert any(p["count"] >= 2 for p in patterns)


# ------------------------------------------------------------------
# Feature 18: Codebase Convention Learning
# ------------------------------------------------------------------


async def test_learn_conventions(learning: LearningManager, tmp_path):
    """learn_conventions detects naming patterns from Python files."""
    # Create a temporary Python file
    py_file = tmp_path / "sample.py"
    py_file.write_text(textwrap.dedent("""\
        GLOBAL_TIMEOUT = 30
        MAX_RETRIES = 5

        class DataProcessor:
            pass

        class RequestHandler:
            pass

        def process_data():
            pass

        def handle_request():
            pass
    """))

    results = await learning.learn_conventions(directory=str(tmp_path))

    types = {r["convention_type"] for r in results}
    assert "function_naming" in types
    assert "class_naming" in types
    assert "constant_naming" in types


async def test_get_conventions(learning: LearningManager, tmp_path):
    """get_conventions returns persisted conventions ordered by confidence."""
    py_file = tmp_path / "example.py"
    py_file.write_text(textwrap.dedent("""\
        class MyClass:
            pass

        def my_func():
            pass
    """))

    await learning.learn_conventions(directory=str(tmp_path))
    conventions = await learning.get_conventions()
    assert len(conventions) >= 1
    # Ordered by confidence DESC
    confidences = [c["confidence"] for c in conventions]
    assert confidences == sorted(confidences, reverse=True)


# ------------------------------------------------------------------
# Feature 19: Error Pattern Recognition
# ------------------------------------------------------------------


async def test_suggest_adjustments_no_issues(learning: LearningManager, db: Database):
    """suggest_adjustments returns empty list when all task types have >=50% success."""
    for _ in range(5):
        await _seed_task(db, status="completed", assigned_to="coder", task_type="implementation")
    for _ in range(2):
        await _seed_task(db, status="failed", assigned_to="coder", task_type="implementation")

    suggestions = await learning.suggest_adjustments("coder")
    # 5/7 ~71% success rate, above 50% threshold
    assert len(suggestions) == 0


async def test_cluster_errors(learning: LearningManager, db: Database):
    """cluster_errors groups failed tasks by rejection keyword frequency."""
    await _seed_task(db, status="failed", rejection_reason="timeout in API request handling")
    await _seed_task(db, status="failed", rejection_reason="timeout when connecting to service")
    await _seed_task(db, status="rejected", rejection_reason="syntax error in parser module")

    clusters = await learning.cluster_errors()
    names = {c["cluster_name"] for c in clusters}
    assert "error-timeout" in names


async def test_get_prevention_hints(learning: LearningManager, db: Database):
    """get_prevention_hints returns hints for matching error patterns."""
    await _seed_task(db, status="failed", rejection_reason="timeout in API call")
    await _seed_task(db, status="failed", rejection_reason="timeout connecting to DB")

    await learning.cluster_errors()
    hints = await learning.get_prevention_hints("timeout")
    assert len(hints) >= 1
    assert "timeout" in hints[0]["prevention_hint"]
