"""Observability: decision audit trails, behavior analytics, cost attribution, bottleneck and anomaly detection, quality trends."""

from __future__ import annotations

import json
import logging
import math
import uuid
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


class ObservabilityManager:
    """Centralised observability for the AI team pipeline."""

    # Number of standard deviations for anomaly detection boundary
    ANOMALY_STD_DEV_THRESHOLD: float = 2.0

    # Number of standard deviations for high-severity anomalies
    ANOMALY_HIGH_SEVERITY_THRESHOLD: float = 3.0

    # Minimum samples required before anomaly detection runs
    ANOMALY_MIN_SAMPLES: int = 3

    def __init__(
        self,
        db,
        event_bus=None,
        *,
        anomaly_std_dev_threshold: float | None = None,
        anomaly_high_severity_threshold: float | None = None,
        anomaly_min_samples: int | None = None,
    ) -> None:
        self._db = db
        self._event_bus = event_bus
        if anomaly_std_dev_threshold is not None:
            self.ANOMALY_STD_DEV_THRESHOLD = anomaly_std_dev_threshold
        if anomaly_high_severity_threshold is not None:
            self.ANOMALY_HIGH_SEVERITY_THRESHOLD = anomaly_high_severity_threshold
        if anomaly_min_samples is not None:
            self.ANOMALY_MIN_SAMPLES = anomaly_min_samples

    # ------------------------------------------------------------------
    # Schema bootstrap (called once; CREATE IF NOT EXISTS is idempotent)
    # ------------------------------------------------------------------

    async def ensure_tables(self) -> None:
        await self._db.executescript("""
            CREATE TABLE IF NOT EXISTS decision_audit_log (
                id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL,
                task_id TEXT,
                decision_type TEXT NOT NULL,
                decision TEXT NOT NULL,
                reasoning TEXT,
                context TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS agent_behavior_metrics (
                id TEXT PRIMARY KEY,
                agent_role TEXT NOT NULL,
                metric_type TEXT NOT NULL,
                value REAL NOT NULL,
                period_start TEXT NOT NULL,
                period_end TEXT NOT NULL,
                metadata TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS cost_attributions (
                id TEXT PRIMARY KEY,
                task_id TEXT,
                feature_tag TEXT,
                agent_id TEXT NOT NULL,
                input_tokens INTEGER NOT NULL DEFAULT 0,
                output_tokens INTEGER NOT NULL DEFAULT 0,
                cost_usd REAL NOT NULL DEFAULT 0,
                attributed_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS pipeline_bottlenecks (
                id TEXT PRIMARY KEY,
                stage TEXT NOT NULL,
                avg_wait_ms REAL NOT NULL DEFAULT 0,
                avg_process_ms REAL NOT NULL DEFAULT 0,
                queue_depth INTEGER NOT NULL DEFAULT 0,
                detected_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS anomaly_detections (
                id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL,
                anomaly_type TEXT NOT NULL,
                description TEXT,
                severity TEXT NOT NULL DEFAULT 'medium',
                metric_value REAL,
                expected_range TEXT,
                detected_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS quality_trends (
                id TEXT PRIMARY KEY,
                metric_name TEXT NOT NULL,
                metric_value REAL NOT NULL,
                dimension TEXT,
                period TEXT NOT NULL DEFAULT 'daily',
                recorded_at TEXT NOT NULL
            );
        """)

    # ------------------------------------------------------------------
    # Feature 39: Decision Audit Trail
    # ------------------------------------------------------------------

    async def log_decision(
        self,
        agent_id: str,
        decision_type: str,
        decision: str,
        reasoning: str | None = None,
        task_id: str | None = None,
        context: dict | None = None,
    ) -> dict:
        """Record a decision in the audit log."""
        now = _utcnow()
        record_id = _new_id()
        context_json = json.dumps(context) if context else None
        await self._db.execute(
            "INSERT INTO decision_audit_log (id, agent_id, task_id, decision_type, decision, reasoning, context, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (record_id, agent_id, task_id, decision_type, decision, reasoning, context_json, now),
        )
        record = {
            "id": record_id,
            "agent_id": agent_id,
            "task_id": task_id,
            "decision_type": decision_type,
            "decision": decision,
            "reasoning": reasoning,
            "context": context,
            "created_at": now,
        }
        if self._event_bus:
            await self._event_bus.emit("decision.logged", record)
        return record

    async def get_audit_trail(
        self,
        agent_id: str | None = None,
        task_id: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """Query the decision audit log with optional filters."""
        conditions: list[str] = []
        params: list = []
        if agent_id:
            conditions.append("agent_id = ?")
            params.append(agent_id)
        if task_id:
            conditions.append("task_id = ?")
            params.append(task_id)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        params.append(limit)
        rows = await self._db.execute_fetchall(
            f"SELECT * FROM decision_audit_log {where} ORDER BY created_at DESC LIMIT ?",
            tuple(params),
        )
        for row in rows:
            if isinstance(row.get("context"), str):
                try:
                    row["context"] = json.loads(row["context"])
                except (json.JSONDecodeError, TypeError) as exc:
                    logger.warning("Failed to parse audit context JSON: %s", exc)
        return rows

    # ------------------------------------------------------------------
    # Feature 40: Agent Behavior Analytics
    # ------------------------------------------------------------------

    async def record_behavior_metric(
        self,
        agent_role: str,
        metric_type: str,
        value: float,
        period_start: str,
        period_end: str,
        metadata: dict | None = None,
    ) -> dict:
        """Record a behavior metric for an agent role."""
        now = _utcnow()
        record_id = _new_id()
        metadata_json = json.dumps(metadata) if metadata else None
        await self._db.execute(
            "INSERT INTO agent_behavior_metrics (id, agent_role, metric_type, value, period_start, period_end, metadata, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (record_id, agent_role, metric_type, value, period_start, period_end, metadata_json, now),
        )
        return {
            "id": record_id,
            "agent_role": agent_role,
            "metric_type": metric_type,
            "value": value,
            "period_start": period_start,
            "period_end": period_end,
            "metadata": metadata,
            "created_at": now,
        }

    async def get_behavior_analytics(
        self,
        agent_role: str,
        metric_type: str | None = None,
    ) -> list[dict]:
        """Query behavior metrics for an agent role."""
        if metric_type:
            rows = await self._db.execute_fetchall(
                "SELECT * FROM agent_behavior_metrics WHERE agent_role = ? AND metric_type = ? ORDER BY created_at DESC",
                (agent_role, metric_type),
            )
        else:
            rows = await self._db.execute_fetchall(
                "SELECT * FROM agent_behavior_metrics WHERE agent_role = ? ORDER BY created_at DESC",
                (agent_role,),
            )
        for row in rows:
            if isinstance(row.get("metadata"), str):
                try:
                    row["metadata"] = json.loads(row["metadata"])
                except (json.JSONDecodeError, TypeError) as exc:
                    logger.warning("Failed to parse behavior metadata JSON: %s", exc)
        return rows

    # ------------------------------------------------------------------
    # Feature 41: Granular Cost Attribution
    # ------------------------------------------------------------------

    async def attribute_cost(
        self,
        agent_id: str,
        cost_usd: float,
        input_tokens: int,
        output_tokens: int,
        task_id: str | None = None,
        feature_tag: str | None = None,
    ) -> dict:
        """Attribute a cost record to an agent and optional feature/task."""
        now = _utcnow()
        record_id = _new_id()
        await self._db.execute(
            "INSERT INTO cost_attributions (id, task_id, feature_tag, agent_id, input_tokens, output_tokens, cost_usd, attributed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (record_id, task_id, feature_tag, agent_id, input_tokens, output_tokens, cost_usd, now),
        )
        return {
            "id": record_id,
            "task_id": task_id,
            "feature_tag": feature_tag,
            "agent_id": agent_id,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": cost_usd,
            "attributed_at": now,
        }

    async def get_cost_by_feature(self) -> list[dict]:
        """Aggregate costs grouped by feature_tag."""
        return await self._db.execute_fetchall(
            "SELECT feature_tag, SUM(cost_usd) AS total_cost, SUM(input_tokens) AS total_input_tokens, "
            "SUM(output_tokens) AS total_output_tokens, COUNT(*) AS record_count "
            "FROM cost_attributions GROUP BY feature_tag ORDER BY SUM(cost_usd) DESC",
        )

    async def get_cost_by_agent(self) -> list[dict]:
        """Aggregate costs grouped by agent_id."""
        return await self._db.execute_fetchall(
            "SELECT agent_id, SUM(cost_usd) AS total_cost, SUM(input_tokens) AS total_input_tokens, "
            "SUM(output_tokens) AS total_output_tokens, COUNT(*) AS record_count "
            "FROM cost_attributions GROUP BY agent_id ORDER BY SUM(cost_usd) DESC",
        )

    # ------------------------------------------------------------------
    # Feature 42: Pipeline Bottleneck Detection
    # ------------------------------------------------------------------

    async def detect_bottlenecks(self) -> list[dict]:
        """Detect pipeline bottlenecks by analysing task queues and durations."""
        now = _utcnow()
        detected_at = now
        bottlenecks: list[dict] = []

        # In-progress tasks: avg processing time = now - started_at
        in_progress = await self._db.execute_fetchall(
            "SELECT status, started_at FROM tasks WHERE status = 'in_progress' AND started_at IS NOT NULL",
        )
        if in_progress:
            now_dt = datetime.fromisoformat(now)
            durations = []
            for row in in_progress:
                started = datetime.fromisoformat(row["started_at"])
                delta_ms = (now_dt - started).total_seconds() * 1000
                durations.append(delta_ms)
            if not durations:
                return bottlenecks
            avg_process = sum(durations) / len(durations)
            bid = _new_id()
            await self._db.execute(
                "INSERT INTO pipeline_bottlenecks (id, stage, avg_wait_ms, avg_process_ms, queue_depth, detected_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (bid, "in_progress", 0, avg_process, len(in_progress), detected_at),
            )
            bottlenecks.append({
                "id": bid,
                "stage": "in_progress",
                "avg_wait_ms": 0,
                "avg_process_ms": avg_process,
                "queue_depth": len(in_progress),
                "detected_at": detected_at,
            })

        # Pending tasks: avg wait time = now - created_at
        pending = await self._db.execute_fetchall(
            "SELECT status, created_at FROM tasks WHERE status = 'pending' AND created_at IS NOT NULL",
        )
        if pending:
            now_dt = datetime.fromisoformat(now)
            waits = []
            for row in pending:
                created = datetime.fromisoformat(row["created_at"])
                delta_ms = (now_dt - created).total_seconds() * 1000
                waits.append(delta_ms)
            if not waits:
                return bottlenecks
            avg_wait = sum(waits) / len(waits)
            bid = _new_id()
            await self._db.execute(
                "INSERT INTO pipeline_bottlenecks (id, stage, avg_wait_ms, avg_process_ms, queue_depth, detected_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (bid, "pending", avg_wait, 0, len(pending), detected_at),
            )
            bottlenecks.append({
                "id": bid,
                "stage": "pending",
                "avg_wait_ms": avg_wait,
                "avg_process_ms": 0,
                "queue_depth": len(pending),
                "detected_at": detected_at,
            })

        # Sort by queue_depth descending
        bottlenecks.sort(key=lambda b: b["queue_depth"], reverse=True)
        return bottlenecks

    # ------------------------------------------------------------------
    # Feature 43: Anomaly Detection
    # ------------------------------------------------------------------

    async def detect_anomalies(self, agent_id: str) -> list[dict]:
        """Detect anomalies in recent behavior metrics for an agent."""
        now = _utcnow()
        rows = await self._db.execute_fetchall(
            "SELECT * FROM agent_behavior_metrics WHERE agent_role = ? ORDER BY created_at DESC",
            (agent_id,),
        )
        if not rows:
            return []

        # Group values by metric_type
        metric_values: dict[str, list[float]] = {}
        for row in rows:
            mt = row["metric_type"]
            metric_values.setdefault(mt, []).append(row["value"])

        anomalies: list[dict] = []
        for metric_type, values in metric_values.items():
            if len(values) < self.ANOMALY_MIN_SAMPLES:
                continue
            mean = sum(values) / len(values)
            variance = sum((v - mean) ** 2 for v in values) / len(values)
            std_dev = math.sqrt(variance)
            if std_dev == 0:
                continue
            upper = mean + self.ANOMALY_STD_DEV_THRESHOLD * std_dev
            lower = mean - self.ANOMALY_STD_DEV_THRESHOLD * std_dev
            for val in values:
                if val > upper or val < lower:
                    aid = _new_id()
                    severity = "high" if abs(val - mean) > self.ANOMALY_HIGH_SEVERITY_THRESHOLD * std_dev else "medium"
                    description = (
                        f"Metric '{metric_type}' value {val} is outside expected range "
                        f"[{round(lower, 2)}, {round(upper, 2)}]"
                    )
                    await self._db.execute(
                        "INSERT INTO anomaly_detections (id, agent_id, anomaly_type, description, severity, metric_value, expected_range, detected_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (aid, agent_id, metric_type, description, severity, val, json.dumps([round(lower, 2), round(upper, 2)]), now),
                    )
                    anomalies.append({
                        "id": aid,
                        "agent_id": agent_id,
                        "anomaly_type": metric_type,
                        "description": description,
                        "severity": severity,
                        "metric_value": val,
                        "expected_range": [round(lower, 2), round(upper, 2)],
                        "detected_at": now,
                    })
        if self._event_bus and anomalies:
            await self._event_bus.emit("anomalies.detected", {"agent_id": agent_id, "count": len(anomalies)})
        return anomalies

    async def get_anomalies(
        self,
        agent_id: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """Query stored anomaly detections."""
        if agent_id:
            rows = await self._db.execute_fetchall(
                "SELECT * FROM anomaly_detections WHERE agent_id = ? ORDER BY detected_at DESC LIMIT ?",
                (agent_id, limit),
            )
        else:
            rows = await self._db.execute_fetchall(
                "SELECT * FROM anomaly_detections ORDER BY detected_at DESC LIMIT ?",
                (limit,),
            )
        for row in rows:
            if isinstance(row.get("expected_range"), str):
                try:
                    row["expected_range"] = json.loads(row["expected_range"])
                except (json.JSONDecodeError, TypeError) as exc:
                    logger.warning("Failed to parse anomaly expected_range JSON: %s", exc)
        return rows

    # ------------------------------------------------------------------
    # Feature 44: Quality Trend Dashboards
    # ------------------------------------------------------------------

    async def record_trend(
        self,
        metric_name: str,
        metric_value: float,
        dimension: str | None = None,
        period: str = "daily",
    ) -> dict:
        """Record a quality trend data point."""
        now = _utcnow()
        record_id = _new_id()
        await self._db.execute(
            "INSERT INTO quality_trends (id, metric_name, metric_value, dimension, period, recorded_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (record_id, metric_name, metric_value, dimension, period, now),
        )
        return {
            "id": record_id,
            "metric_name": metric_name,
            "metric_value": metric_value,
            "dimension": dimension,
            "period": period,
            "recorded_at": now,
        }

    async def get_trends(
        self,
        metric_name: str,
        period: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """Query quality trends."""
        if period:
            rows = await self._db.execute_fetchall(
                "SELECT * FROM quality_trends WHERE metric_name = ? AND period = ? ORDER BY recorded_at DESC LIMIT ?",
                (metric_name, period, limit),
            )
        else:
            rows = await self._db.execute_fetchall(
                "SELECT * FROM quality_trends WHERE metric_name = ? ORDER BY recorded_at DESC LIMIT ?",
                (metric_name, limit),
            )
        return rows
