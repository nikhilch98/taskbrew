"""Tests for the TaskIntelligenceManager (features 25-32)."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import pytest

from taskbrew.orchestrator.database import Database
from taskbrew.intelligence.task_intelligence import TaskIntelligenceManager


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture
async def manager(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    await db.initialize()
    mgr = TaskIntelligenceManager(db)
    await mgr.ensure_tables()
    yield mgr
    await db.close()


@pytest.fixture
async def db_and_mgr(tmp_path):
    """Return both the database and the manager for tests that need raw DB access."""
    db = Database(str(tmp_path / "test.db"))
    await db.initialize()
    mgr = TaskIntelligenceManager(db)
    await mgr.ensure_tables()
    yield db, mgr
    await db.close()


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _create_task_row(db, task_id, group_id="GRP-001", status="pending", files=None):
    """Insert a minimal task row and optionally a complexity estimate with files."""
    now = _now_iso()
    await db.execute(
        "INSERT OR IGNORE INTO groups (id, title, origin, status, created_at) "
        "VALUES (?, 'Test Group', 'test', 'active', ?)",
        (group_id, now),
    )
    await db.execute(
        "INSERT INTO tasks (id, group_id, title, description, task_type, priority, "
        "assigned_to, status, created_by, created_at) "
        "VALUES (?, ?, 'Test Task', 'Test description', 'implementation', 'medium', "
        "'coder', ?, 'test', ?)",
        (task_id, group_id, status, now),
    )
    if files is not None:
        await db.execute(
            "INSERT INTO complexity_estimates "
            "(id, task_id, title, description, files_involved, complexity_score, created_at) "
            "VALUES (?, ?, 'Test', 'desc', ?, 5, ?)",
            (f"CE-{uuid.uuid4().hex[:8]}", task_id, json.dumps(files), now),
        )


# ------------------------------------------------------------------
# Feature 25: Task Complexity Estimator
# ------------------------------------------------------------------


async def test_estimate_complexity_basic(manager: TaskIntelligenceManager):
    """Estimate complexity for a simple task."""
    result = await manager.estimate_complexity(
        "TSK-001", "Add login button", "Add a login button to the homepage"
    )
    assert result["task_id"] == "TSK-001"
    assert 1 <= result["complexity_score"] <= 10
    assert result["keyword_hits"] == 0  # No high-complexity keywords


async def test_estimate_complexity_high_keywords(manager: TaskIntelligenceManager):
    """Tasks with complexity keywords score higher."""
    simple = await manager.estimate_complexity(
        "TSK-002", "Fix typo", "Fix a typo in the readme"
    )
    complex_task = await manager.estimate_complexity(
        "TSK-003", "Refactor authentication",
        "Refactor the security module and migrate the encryption layer",
        files_involved=["auth.py", "crypto.py", "middleware.py", "tests.py", "config.py"],
    )
    assert complex_task["complexity_score"] > simple["complexity_score"]
    assert complex_task["keyword_hits"] >= 3


async def test_calibrate_complexity(manager: TaskIntelligenceManager):
    """calibrate records actual complexity."""
    await manager.estimate_complexity("TSK-010", "Task", "Description")
    result = await manager.calibrate("TSK-010", 8)
    assert result["actual_complexity"] == 8

    est = await manager.get_estimate("TSK-010")
    assert est["actual_complexity"] == 8


# ------------------------------------------------------------------
# Feature 26: Prerequisite Auto-Detector
# ------------------------------------------------------------------


async def test_detect_prerequisites_sequential_keyword(manager: TaskIntelligenceManager):
    """Detect prerequisites from sequential keywords and task references."""
    prereqs = await manager.detect_prerequisites(
        "TSK-020",
        "This task depends on TSK-019 being completed first",
    )
    assert len(prereqs) >= 1
    assert prereqs[0]["prerequisite_task_id"] == "TSK-019"
    assert "depends on" in prereqs[0]["reason"]


async def test_confirm_prerequisite(manager: TaskIntelligenceManager):
    """confirm_prerequisite marks the detection as confirmed."""
    prereqs = await manager.detect_prerequisites(
        "TSK-021", "After TSK-020, we can start this"
    )
    assert len(prereqs) >= 1

    result = await manager.confirm_prerequisite(prereqs[0]["id"], confirmed=True)
    assert result["confirmed"] is True

    stored = await manager.get_prerequisites("TSK-021")
    assert stored[0]["confirmed"] == 1


# ------------------------------------------------------------------
# Feature 27: Decomposition Optimizer
# ------------------------------------------------------------------


async def test_record_decomposition_and_get_optimal(manager: TaskIntelligenceManager):
    """Record decomposition metrics and get optimal granularity."""
    await manager.record_decomposition("P-001", 3, 5000.0, 0.95)
    await manager.record_decomposition("P-002", 3, 6000.0, 0.90)
    await manager.record_decomposition("P-003", 8, 3000.0, 0.60)

    optimal = await manager.get_optimal_granularity()
    assert optimal["based_on"] > 0
    # 3 subtasks had higher success rate
    assert optimal["best_subtask_count"] == 3


async def test_get_decomposition_metrics(manager: TaskIntelligenceManager):
    """get_metrics returns recorded decomposition history."""
    await manager.record_decomposition("P-010", 5, 4000.0, 0.85, task_type="feature")

    metrics = await manager.get_metrics()
    assert len(metrics) >= 1
    assert metrics[0]["parent_task_id"] == "P-010"
    assert metrics[0]["task_type"] == "feature"


# ------------------------------------------------------------------
# Feature 28: Parallel Opportunity Finder
# ------------------------------------------------------------------


async def test_find_parallel_tasks_disjoint_files(db_and_mgr):
    """Find parallel opportunities between tasks with disjoint files."""
    db, mgr = db_and_mgr
    group_id = "GRP-PAR-001"
    await _create_task_row(db, "TSK-A", group_id, files=["frontend.js", "styles.css"])
    await _create_task_row(db, "TSK-B", group_id, files=["backend.py", "models.py"])

    opps = await mgr.find_parallel_tasks(group_id)
    assert len(opps) >= 1
    task_set = json.loads(opps[0]["task_set"]) if isinstance(opps[0]["task_set"], str) else opps[0]["task_set"]
    assert "TSK-A" in task_set
    assert "TSK-B" in task_set


async def test_mark_parallel_opportunity_exploited(db_and_mgr):
    """mark_exploited updates the opportunity."""
    db, mgr = db_and_mgr
    group_id = "GRP-PAR-002"
    await _create_task_row(db, "TSK-C", group_id, files=["a.py"])
    await _create_task_row(db, "TSK-D", group_id, files=["b.py"])

    opps = await mgr.find_parallel_tasks(group_id)
    assert len(opps) >= 1

    result = await mgr.mark_exploited(opps[0]["id"])
    assert result["exploited"] is True


# ------------------------------------------------------------------
# Feature 29: Context Budget Planner
# ------------------------------------------------------------------


async def test_plan_and_get_budget(manager: TaskIntelligenceManager):
    """Plan a context budget and retrieve it."""
    result = await manager.plan_budget("TSK-050", estimated_files=10, estimated_tokens_per_file=800)
    assert result["total_budget"] == 8000
    assert result["task_id"] == "TSK-050"

    budget = await manager.get_budget("TSK-050")
    assert budget is not None
    assert budget["total_budget"] == 8000


async def test_record_actual_tokens(manager: TaskIntelligenceManager):
    """record_actual stores actual token usage."""
    await manager.plan_budget("TSK-051", estimated_files=5)
    result = await manager.record_actual("TSK-051", actual_tokens_used=3200)
    assert result["actual_tokens_used"] == 3200
    assert result["total_budget"] == 2500  # 5 * 500


# ------------------------------------------------------------------
# Feature 30: Task Outcome Predictor
# ------------------------------------------------------------------


async def test_predict_outcome_low_complexity(manager: TaskIntelligenceManager):
    """Low complexity tasks should have high predicted success."""
    result = await manager.predict_outcome("TSK-060", complexity_score=2, agent_role="coder")
    assert result["predicted_success"] > 0.7


async def test_predict_outcome_high_complexity(manager: TaskIntelligenceManager):
    """High complexity tasks should have lower predicted success."""
    low = await manager.predict_outcome("TSK-061", complexity_score=2, agent_role="coder")
    high = await manager.predict_outcome("TSK-062", complexity_score=9, agent_role="coder")
    assert high["predicted_success"] < low["predicted_success"]


async def test_prediction_accuracy(manager: TaskIntelligenceManager):
    """get_prediction_accuracy computes correctness."""
    # Predict success for easy task (should predict >0.5)
    pred1 = await manager.predict_outcome("T1", 2, "coder")
    await manager.record_actual_outcome(pred1["id"], success=True)

    # Predict success for hard task (should predict <0.5)
    pred2 = await manager.predict_outcome("T2", 9, "coder")
    await manager.record_actual_outcome(pred2["id"], success=False)

    accuracy = await manager.get_prediction_accuracy(agent_role="coder")
    assert accuracy["sample_count"] == 2
    assert accuracy["accuracy"] == 1.0  # Both predictions were correct


# ------------------------------------------------------------------
# Feature 31: Task Similarity Matcher
# ------------------------------------------------------------------


async def test_fingerprint_and_find_similar(manager: TaskIntelligenceManager):
    """Fingerprint tasks and find similar ones."""
    await manager.fingerprint_task("TSK-070", "Implement user authentication", "Add login and signup endpoints")
    await manager.fingerprint_task("TSK-071", "Build user login page", "Create authentication form with validation")
    await manager.fingerprint_task("TSK-072", "Fix database migration", "Update schema migration scripts")

    results = await manager.find_similar("User authentication system", "Implement login endpoint")
    assert len(results) >= 1
    # TSK-070 and TSK-071 should be more similar than TSK-072
    task_ids = [r["task_id"] for r in results]
    assert "TSK-070" in task_ids or "TSK-071" in task_ids


async def test_get_fingerprint(manager: TaskIntelligenceManager):
    """get_fingerprint retrieves stored task fingerprint."""
    await manager.fingerprint_task("TSK-080", "Deploy to staging", "Deploy the application to staging environment")

    fp = await manager.get_fingerprint("TSK-080")
    assert fp is not None
    assert fp["task_id"] == "TSK-080"
    assert len(fp["keywords"]) > 0


# ------------------------------------------------------------------
# Feature 32: Effort Drift Detector
# ------------------------------------------------------------------


async def test_start_and_check_drift(manager: TaskIntelligenceManager):
    """start_tracking and check_drift compute drift ratio."""
    await manager.start_tracking("TSK-090", estimated_duration_ms=10000)

    # Small delay so elapsed > 0
    drift = await manager.check_drift("TSK-090")
    assert drift["task_id"] == "TSK-090"
    assert drift["drift_ratio"] >= 0
    assert "alert" in drift


async def test_complete_tracking(manager: TaskIntelligenceManager):
    """complete_tracking finalizes with actual duration."""
    await manager.start_tracking("TSK-091", estimated_duration_ms=5000)

    result = await manager.complete_tracking("TSK-091")
    assert result["status"] == "completed"
    assert result["actual_duration_ms"] >= 0
    assert result["drift_ratio"] >= 0


async def test_drift_history(manager: TaskIntelligenceManager):
    """get_drift_history returns completed tracking entries."""
    await manager.start_tracking("TSK-092", estimated_duration_ms=1000)
    await manager.complete_tracking("TSK-092")

    history = await manager.get_drift_history()
    assert len(history) >= 1
    assert history[0]["task_id"] == "TSK-092"
    assert history[0]["status"] == "completed"


async def test_check_drift_no_tracking(manager: TaskIntelligenceManager):
    """check_drift returns error for untracked task."""
    result = await manager.check_drift("NONEXISTENT")
    assert "error" in result
