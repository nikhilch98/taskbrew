"""Tests for the ObservabilityManager (features 39-44)."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from taskbrew.orchestrator.database import Database
from taskbrew.intelligence.observability import ObservabilityManager


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture
async def db(tmp_path):
    database = Database(str(tmp_path / "test.db"))
    await database.initialize()
    await database.execute("""CREATE TABLE IF NOT EXISTS tasks (
        id TEXT PRIMARY KEY, title TEXT, description TEXT, task_type TEXT,
        priority TEXT, status TEXT DEFAULT 'pending', assigned_to TEXT,
        claimed_by TEXT, group_id TEXT, parent_id TEXT, created_by TEXT,
        created_at TEXT, started_at TEXT, completed_at TEXT,
        output_text TEXT, rejection_reason TEXT, rejection_count INTEGER DEFAULT 0
    )""")
    await database.execute("""CREATE TABLE IF NOT EXISTS task_dependencies (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id TEXT NOT NULL, blocked_by TEXT NOT NULL
    )""")
    yield database
    await database.close()


@pytest.fixture
async def obs(db: Database) -> ObservabilityManager:
    mgr = ObservabilityManager(db)
    await mgr.ensure_tables()
    return mgr


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _past_iso(minutes: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()


async def _create_task(db, *, status="pending", started_at=None, created_at=None, task_type="implementation"):
    tid = f"TSK-{uuid.uuid4().hex[:6]}"
    created_at = created_at or _now_iso()
    await db.execute(
        "INSERT INTO tasks (id, title, task_type, priority, status, created_by, created_at, started_at) "
        "VALUES (?, 'Test', ?, 'medium', ?, 'test', ?, ?)",
        (tid, task_type, status, created_at, started_at),
    )
    return tid


# ------------------------------------------------------------------
# Feature 39: Decision Audit Trail
# ------------------------------------------------------------------


async def test_log_decision_basic(obs: ObservabilityManager):
    """Log a decision and verify the returned record."""
    result = await obs.log_decision(
        agent_id="coder",
        decision_type="task_claim",
        decision="claimed task CD-001",
        reasoning="High priority and within my skill set",
        task_id="CD-001",
        context={"priority": "high"},
    )
    assert result["agent_id"] == "coder"
    assert result["decision_type"] == "task_claim"
    assert result["decision"] == "claimed task CD-001"
    assert result["reasoning"] == "High priority and within my skill set"
    assert result["context"] == {"priority": "high"}
    assert result["id"]
    assert result["created_at"]


async def test_get_audit_trail_filters(obs: ObservabilityManager):
    """Audit trail can be filtered by agent_id and task_id."""
    await obs.log_decision("coder", "claim", "claimed A", task_id="T-1")
    await obs.log_decision("reviewer", "approve", "approved B", task_id="T-2")
    await obs.log_decision("coder", "submit", "submitted C", task_id="T-1")

    # Filter by agent
    coder_trail = await obs.get_audit_trail(agent_id="coder")
    assert len(coder_trail) == 2
    assert all(r["agent_id"] == "coder" for r in coder_trail)

    # Filter by task
    t1_trail = await obs.get_audit_trail(task_id="T-1")
    assert len(t1_trail) == 2
    assert all(r["task_id"] == "T-1" for r in t1_trail)

    # Filter by both
    combined = await obs.get_audit_trail(agent_id="coder", task_id="T-1")
    assert len(combined) == 2


async def test_audit_trail_parses_json_context(obs: ObservabilityManager):
    """Context should be stored as JSON and parsed on retrieval."""
    await obs.log_decision("coder", "decide", "chose X", context={"key": [1, 2, 3]})
    trail = await obs.get_audit_trail(agent_id="coder")
    assert trail[0]["context"] == {"key": [1, 2, 3]}


# ------------------------------------------------------------------
# Feature 40: Agent Behavior Analytics
# ------------------------------------------------------------------


async def test_record_behavior_metric(obs: ObservabilityManager):
    """Record and retrieve a behavior metric."""
    result = await obs.record_behavior_metric(
        agent_role="coder",
        metric_type="task_completion_rate",
        value=0.92,
        period_start="2026-01-01",
        period_end="2026-01-31",
        metadata={"tasks_total": 50},
    )
    assert result["agent_role"] == "coder"
    assert result["metric_type"] == "task_completion_rate"
    assert result["value"] == 0.92
    assert result["metadata"] == {"tasks_total": 50}


async def test_get_behavior_analytics_filtered(obs: ObservabilityManager):
    """Analytics can be filtered by metric_type."""
    await obs.record_behavior_metric("coder", "completion_rate", 0.9, "2026-01-01", "2026-01-31")
    await obs.record_behavior_metric("coder", "avg_duration", 25.0, "2026-01-01", "2026-01-31")
    await obs.record_behavior_metric("coder", "completion_rate", 0.95, "2026-02-01", "2026-02-28")

    all_metrics = await obs.get_behavior_analytics("coder")
    assert len(all_metrics) == 3

    rate_only = await obs.get_behavior_analytics("coder", metric_type="completion_rate")
    assert len(rate_only) == 2
    assert all(m["metric_type"] == "completion_rate" for m in rate_only)


# ------------------------------------------------------------------
# Feature 41: Granular Cost Attribution
# ------------------------------------------------------------------


async def test_attribute_cost_and_aggregate_by_feature(obs: ObservabilityManager):
    """Attribute costs and aggregate by feature tag."""
    await obs.attribute_cost("coder", 0.05, 1000, 500, task_id="T-1", feature_tag="auth")
    await obs.attribute_cost("coder", 0.08, 2000, 800, task_id="T-2", feature_tag="auth")
    await obs.attribute_cost("reviewer", 0.03, 800, 200, task_id="T-3", feature_tag="dashboard")

    by_feature = await obs.get_cost_by_feature()
    assert len(by_feature) == 2
    # Highest cost first
    assert by_feature[0]["feature_tag"] == "auth"
    assert by_feature[0]["total_cost"] == pytest.approx(0.13)
    assert by_feature[0]["total_input_tokens"] == 3000


async def test_get_cost_by_agent(obs: ObservabilityManager):
    """Aggregate costs by agent."""
    await obs.attribute_cost("coder", 0.10, 3000, 1000)
    await obs.attribute_cost("reviewer", 0.04, 1000, 500)
    await obs.attribute_cost("coder", 0.06, 2000, 800)

    by_agent = await obs.get_cost_by_agent()
    assert len(by_agent) == 2
    assert by_agent[0]["agent_id"] == "coder"
    assert by_agent[0]["total_cost"] == pytest.approx(0.16)


# ------------------------------------------------------------------
# Feature 42: Pipeline Bottleneck Detection
# ------------------------------------------------------------------


async def test_detect_bottlenecks_in_progress(obs: ObservabilityManager, db: Database):
    """Detect bottleneck in in_progress tasks."""
    started = _past_iso(30)
    await _create_task(db, status="in_progress", started_at=started)
    await _create_task(db, status="in_progress", started_at=started)

    bottlenecks = await obs.detect_bottlenecks()
    in_prog = [b for b in bottlenecks if b["stage"] == "in_progress"]
    assert len(in_prog) == 1
    assert in_prog[0]["queue_depth"] == 2
    assert in_prog[0]["avg_process_ms"] > 0


async def test_detect_bottlenecks_pending(obs: ObservabilityManager, db: Database):
    """Detect bottleneck in pending tasks."""
    created = _past_iso(60)
    await _create_task(db, status="pending", created_at=created)
    await _create_task(db, status="pending", created_at=created)
    await _create_task(db, status="pending", created_at=created)

    bottlenecks = await obs.detect_bottlenecks()
    pending = [b for b in bottlenecks if b["stage"] == "pending"]
    assert len(pending) == 1
    assert pending[0]["queue_depth"] == 3
    assert pending[0]["avg_wait_ms"] > 0


async def test_detect_bottlenecks_sorted_by_queue_depth(obs: ObservabilityManager, db: Database):
    """Bottlenecks should be sorted by queue_depth descending."""
    started = _past_iso(10)
    created = _past_iso(20)
    await _create_task(db, status="in_progress", started_at=started)
    for _ in range(5):
        await _create_task(db, status="pending", created_at=created)

    bottlenecks = await obs.detect_bottlenecks()
    assert len(bottlenecks) == 2
    assert bottlenecks[0]["queue_depth"] >= bottlenecks[1]["queue_depth"]


# ------------------------------------------------------------------
# Feature 43: Anomaly Detection
# ------------------------------------------------------------------


async def test_detect_anomalies_finds_outlier(obs: ObservabilityManager):
    """Values outside mean +/- 2*std_dev are flagged as anomalies."""
    # Record normal values
    for v in [10, 11, 10, 12, 10, 11]:
        await obs.record_behavior_metric("coder", "duration", v, "2026-01-01", "2026-01-31")
    # Record an outlier
    await obs.record_behavior_metric("coder", "duration", 50, "2026-02-01", "2026-02-28")

    anomalies = await obs.detect_anomalies("coder")
    assert len(anomalies) >= 1
    assert any(a["metric_value"] == 50 for a in anomalies)


async def test_detect_anomalies_no_anomalies(obs: ObservabilityManager):
    """All values within normal range should yield no anomalies."""
    for v in [10.0, 10.1, 9.9, 10.0, 10.2]:
        await obs.record_behavior_metric("coder", "latency", v, "2026-01-01", "2026-01-31")

    anomalies = await obs.detect_anomalies("coder")
    assert len(anomalies) == 0


async def test_get_anomalies_query(obs: ObservabilityManager):
    """get_anomalies retrieves stored detections with parsed expected_range."""
    for v in [10, 11, 10, 12, 10, 11]:
        await obs.record_behavior_metric("coder", "speed", v, "2026-01-01", "2026-01-31")
    await obs.record_behavior_metric("coder", "speed", 100, "2026-02-01", "2026-02-28")

    await obs.detect_anomalies("coder")

    stored = await obs.get_anomalies(agent_id="coder")
    assert len(stored) >= 1
    assert isinstance(stored[0]["expected_range"], list)


# ------------------------------------------------------------------
# Feature 44: Quality Trend Dashboards
# ------------------------------------------------------------------


async def test_record_and_get_trend(obs: ObservabilityManager):
    """Record trend data points and retrieve them."""
    await obs.record_trend("code_quality", 0.85, dimension="coder", period="daily")
    await obs.record_trend("code_quality", 0.90, dimension="coder", period="daily")
    await obs.record_trend("code_quality", 0.80, dimension="coder", period="weekly")

    daily = await obs.get_trends("code_quality", period="daily")
    assert len(daily) == 2

    all_trends = await obs.get_trends("code_quality")
    assert len(all_trends) == 3


async def test_trend_returns_record(obs: ObservabilityManager):
    """record_trend returns the created record."""
    result = await obs.record_trend("test_pass_rate", 0.95, period="weekly")
    assert result["metric_name"] == "test_pass_rate"
    assert result["metric_value"] == 0.95
    assert result["period"] == "weekly"
    assert result["id"]
    assert result["recorded_at"]
