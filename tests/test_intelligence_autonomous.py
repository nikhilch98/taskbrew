"""Tests for the AutonomousManager."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from taskbrew.orchestrator.database import Database
from taskbrew.intelligence.autonomous import AutonomousManager


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
        CREATE TABLE IF NOT EXISTS task_decompositions (
            id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            subtask_title TEXT NOT NULL,
            subtask_description TEXT,
            reasoning TEXT,
            estimated_effort TEXT,
            status TEXT DEFAULT 'pending',
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS work_discoveries (
            id TEXT PRIMARY KEY,
            agent_id TEXT NOT NULL,
            discovery_type TEXT NOT NULL,
            file_path TEXT,
            description TEXT,
            priority TEXT DEFAULT 'medium',
            status TEXT DEFAULT 'pending',
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS priority_bids (
            id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            agent_id TEXT NOT NULL,
            bid_score REAL NOT NULL,
            reasoning TEXT,
            workload_factor REAL,
            skill_factor REAL,
            urgency_factor REAL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS retry_strategies (
            id TEXT PRIMARY KEY,
            failure_type TEXT NOT NULL,
            strategy TEXT NOT NULL,
            success_count INTEGER DEFAULT 0,
            failure_count INTEGER DEFAULT 0,
            avg_recovery_time_ms INTEGER DEFAULT 0,
            last_updated TEXT,
            UNIQUE(failure_type, strategy)
        );
        CREATE TABLE IF NOT EXISTS pipeline_fixes (
            id TEXT PRIMARY KEY,
            failure_signature TEXT NOT NULL,
            fix_applied TEXT NOT NULL,
            success INTEGER DEFAULT 0,
            source_task_id TEXT,
            created_at TEXT NOT NULL
        );
    """)

    yield database
    await database.close()


@pytest.fixture
async def mgr(db: Database) -> AutonomousManager:
    """Create an AutonomousManager backed by the in-memory database."""
    return AutonomousManager(db)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


async def _create_task(
    db: Database,
    title: str = "Test Task",
    description: str = "Implement feature and write tests",
) -> str:
    now = datetime.now(timezone.utc).isoformat()
    group_id = f"GRP-{uuid.uuid4().hex[:4]}"
    await db.execute(
        "INSERT INTO groups (id, title, origin, status, created_at) VALUES (?, 'Test', 'test', 'active', ?)",
        (group_id, now),
    )
    task_id = f"TSK-{uuid.uuid4().hex[:4]}"
    await db.execute(
        "INSERT INTO tasks (id, group_id, title, description, task_type, priority, "
        "assigned_to, status, created_by, created_at) "
        "VALUES (?, ?, ?, ?, 'implementation', 'medium', 'coder', 'pending', 'test', ?)",
        (task_id, group_id, title, description, now),
    )
    return task_id


# ------------------------------------------------------------------
# Tests: Feature 1 – Task Decomposition
# ------------------------------------------------------------------


async def test_decompose_with_reasoning_from_description(
    mgr: AutonomousManager, db: Database
):
    """Heuristic decomposition splits on 'and' in description."""
    task_id = await _create_task(
        db, description="Implement the login page and write unit tests"
    )
    result = await mgr.decompose_with_reasoning(task_id)

    assert result["task_id"] == task_id
    assert result["count"] == 2
    assert len(result["subtasks"]) == 2
    titles = [s["subtask_title"] for s in result["subtasks"]]
    assert any("login" in t.lower() for t in titles)
    assert any("test" in t.lower() for t in titles)

    # Verify persisted
    rows = await db.execute_fetchall(
        "SELECT * FROM task_decompositions WHERE task_id = ?", (task_id,)
    )
    assert len(rows) == 2


async def test_decompose_with_llm_output(mgr: AutonomousManager, db: Database):
    """Decomposition from LLM output parses numbered lists."""
    task_id = await _create_task(db)
    llm_output = """Here are the subtasks:
1. Set up the project structure
2. Implement the data model
3. Write integration tests
"""
    result = await mgr.decompose_with_reasoning(task_id, llm_output=llm_output)

    assert result["count"] == 3
    assert len(result["subtasks"]) == 3
    assert result["subtasks"][0]["subtask_title"] == "Set up the project structure"
    assert result["subtasks"][1]["subtask_title"] == "Implement the data model"
    assert result["subtasks"][2]["subtask_title"] == "Write integration tests"

    for st in result["subtasks"]:
        assert st["reasoning"] == "Extracted from LLM output"


async def test_decompose_nonexistent_task(mgr: AutonomousManager):
    """Decomposition of nonexistent task returns empty subtasks."""
    result = await mgr.decompose_with_reasoning("NONEXISTENT-999")
    assert result["count"] == 0
    assert result["subtasks"] == []


# ------------------------------------------------------------------
# Tests: Feature 2 – Work Discovery
# ------------------------------------------------------------------


async def test_discover_work_finds_todos(mgr: AutonomousManager, tmp_path):
    """discover_work detects TODO and FIXME comments in Python files."""
    # Create temp file with TODO
    py_file = tmp_path / "module.py"
    py_file.write_text("def foo():\n    # TODO: implement this\n    pass\n")

    discoveries = await mgr.discover_work("agent-1", str(tmp_path))

    todo_items = [d for d in discoveries if d["discovery_type"] == "todo_comment"]
    assert len(todo_items) >= 1
    assert "TODO" in todo_items[0]["description"]


async def test_discover_work_finds_missing_tests(mgr: AutonomousManager, tmp_path):
    """discover_work detects Python files without corresponding test files."""
    # Create a source file without a test
    (tmp_path / "utils.py").write_text("def helper(): pass\n")

    discoveries = await mgr.discover_work("agent-1", str(tmp_path))

    missing = [d for d in discoveries if d["discovery_type"] == "missing_test"]
    assert len(missing) >= 1
    assert "test_utils.py" in missing[0]["description"]


async def test_get_discoveries(mgr: AutonomousManager, db: Database, tmp_path):
    """get_discoveries returns persisted work discoveries."""
    py_file = tmp_path / "code.py"
    py_file.write_text("# FIXME: broken\n")

    await mgr.discover_work("agent-1", str(tmp_path))

    results = await mgr.get_discoveries(status="pending")
    assert len(results) >= 1
    assert results[0]["status"] == "pending"


# ------------------------------------------------------------------
# Tests: Feature 3 – Priority Negotiation
# ------------------------------------------------------------------


async def test_submit_bid_and_resolve(mgr: AutonomousManager, db: Database):
    """Submit a single bid and resolve it."""
    task_id = await _create_task(db)

    bid = await mgr.submit_bid(task_id, "coder-1", workload=0.2, skill=0.9, urgency=0.8)

    assert bid["task_id"] == task_id
    assert bid["agent_id"] == "coder-1"
    expected_score = round(0.3 * 0.8 + 0.4 * 0.9 + 0.3 * 0.8, 4)
    assert bid["bid_score"] == expected_score

    resolution = await mgr.resolve_bids(task_id)
    assert resolution["winner"] == "coder-1"
    assert resolution["total_bids"] == 1


async def test_resolve_bids_selects_highest(mgr: AutonomousManager, db: Database):
    """resolve_bids picks the agent with the highest bid_score."""
    task_id = await _create_task(db)

    # Low-skill agent
    await mgr.submit_bid(task_id, "coder-1", workload=0.8, skill=0.3, urgency=0.2)
    # High-skill agent
    await mgr.submit_bid(task_id, "coder-2", workload=0.1, skill=0.95, urgency=0.9)

    resolution = await mgr.resolve_bids(task_id)

    assert resolution["winner"] == "coder-2"
    assert resolution["total_bids"] == 2
    assert resolution["bid_score"] > 0.5


async def test_resolve_bids_no_bids(mgr: AutonomousManager):
    """resolve_bids returns None winner when no bids exist."""
    result = await mgr.resolve_bids("NO-BIDS-TASK")
    assert result["winner"] is None
    assert result["total_bids"] == 0


# ------------------------------------------------------------------
# Tests: Feature 4 – Adaptive Retry Strategies
# ------------------------------------------------------------------


async def test_record_retry_outcome_success(mgr: AutonomousManager):
    """Record a successful retry outcome."""
    result = await mgr.record_retry_outcome(
        "timeout", "exponential_backoff", success=True, recovery_time_ms=500
    )

    assert result["failure_type"] == "timeout"
    assert result["strategy"] == "exponential_backoff"
    assert result["success_count"] == 1
    assert result["failure_count"] == 0
    assert result["avg_recovery_time_ms"] == 500


async def test_record_retry_outcome_accumulates(mgr: AutonomousManager):
    """Multiple outcomes accumulate counts correctly."""
    await mgr.record_retry_outcome("timeout", "retry", success=True, recovery_time_ms=100)
    await mgr.record_retry_outcome("timeout", "retry", success=True, recovery_time_ms=200)
    result = await mgr.record_retry_outcome(
        "timeout", "retry", success=False, recovery_time_ms=300
    )

    assert result["success_count"] == 2
    assert result["failure_count"] == 1


async def test_get_best_retry_strategy(mgr: AutonomousManager):
    """get_best_retry_strategy returns the strategy with highest success rate."""
    # Good strategy: 3 successes, 0 failures
    for _ in range(3):
        await mgr.record_retry_outcome("connection_error", "retry_with_jitter", True, 100)
    # Bad strategy: 1 success, 3 failures
    await mgr.record_retry_outcome("connection_error", "simple_retry", True, 200)
    for _ in range(3):
        await mgr.record_retry_outcome("connection_error", "simple_retry", False, 500)

    best = await mgr.get_best_retry_strategy("connection_error")

    assert best is not None
    assert best["strategy"] == "retry_with_jitter"
    assert best["success_rate"] == 1.0


async def test_get_best_retry_strategy_none(mgr: AutonomousManager):
    """get_best_retry_strategy returns None for unknown failure type."""
    result = await mgr.get_best_retry_strategy("unknown_failure")
    assert result is None


# ------------------------------------------------------------------
# Tests: Feature 5 – Self-Healing Pipelines
# ------------------------------------------------------------------


async def test_find_similar_fix(mgr: AutonomousManager):
    """find_similar_fix matches on partial failure signature."""
    await mgr.record_fix("ImportError: no module named foo", "pip install foo", True)

    result = await mgr.find_similar_fix("no module named foo")

    assert result is not None
    assert result["fix_applied"] == "pip install foo"
    assert result["success"] == 1


async def test_find_similar_fix_no_match(mgr: AutonomousManager):
    """find_similar_fix returns None when nothing matches."""
    result = await mgr.find_similar_fix("completely unique error 12345")
    assert result is None


async def test_record_fix(mgr: AutonomousManager):
    """record_fix persists a pipeline fix record."""
    result = await mgr.record_fix(
        "SyntaxError: unexpected indent",
        "reformat with black",
        success=True,
        source_task_id="TSK-001",
    )

    assert result["id"].startswith("FIX-")
    assert result["failure_signature"] == "SyntaxError: unexpected indent"
    assert result["fix_applied"] == "reformat with black"
    assert result["success"] is True
    assert result["source_task_id"] == "TSK-001"
