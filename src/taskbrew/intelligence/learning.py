"""Learning intelligence: A/B testing, cross-project knowledge, benchmarks, strategy, feedback, conventions, error patterns."""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from collections import Counter
from datetime import datetime, timezone

from ._utils import validate_path

logger = logging.getLogger(__name__)


class LearningManager:
    """Track learning signals and adapt agent behaviour over time."""

    def __init__(self, db, memory_manager=None) -> None:
        self._db = db
        self._memory_manager = memory_manager

    # ------------------------------------------------------------------
    # Feature 13: Prompt A/B Testing
    # ------------------------------------------------------------------

    async def create_experiment(
        self, name: str, agent_role: str, variant_a: str, variant_b: str
    ) -> dict:
        """Create a two-variant prompt experiment."""
        now = datetime.now(timezone.utc).isoformat()
        experiment_id = f"EXP-{uuid.uuid4().hex[:8]}"

        for variant_key, prompt_text in [("A", variant_a), ("B", variant_b)]:
            vid = f"{experiment_id}-{variant_key}"
            await self._db.execute(
                "INSERT INTO prompt_experiments "
                "(id, experiment_name, agent_role, variant_key, prompt_text, "
                "trials, successes, avg_quality_score, status, created_at) "
                "VALUES (?, ?, ?, ?, ?, 0, 0, 0.0, 'active', ?)",
                (vid, name, agent_role, variant_key, prompt_text, now),
            )

        return {
            "experiment_id": experiment_id,
            "name": name,
            "agent_role": agent_role,
            "variant_a_id": f"{experiment_id}-A",
            "variant_b_id": f"{experiment_id}-B",
            "status": "active",
            "created_at": now,
        }

    async def record_trial(
        self, experiment_id: str, variant_key: str, success: bool, quality_score: float | None = None
    ) -> dict:
        """Record a trial result for a variant and update running averages."""
        vid = f"{experiment_id}-{variant_key}"
        row = await self._db.execute_fetchone(
            "SELECT * FROM prompt_experiments WHERE id = ?", (vid,)
        )
        if not row:
            return {"error": "Variant not found"}

        new_trials = row["trials"] + 1
        new_successes = row["successes"] + (1 if success else 0)

        if quality_score is not None:
            # Running average
            old_avg = row["avg_quality_score"] or 0.0
            new_avg = ((old_avg * row["trials"]) + quality_score) / new_trials
        else:
            new_avg = row["avg_quality_score"] or 0.0

        await self._db.execute(
            "UPDATE prompt_experiments SET trials = ?, successes = ?, avg_quality_score = ? WHERE id = ?",
            (new_trials, new_successes, new_avg, vid),
        )

        return {
            "id": vid,
            "variant_key": variant_key,
            "trials": new_trials,
            "successes": new_successes,
            "avg_quality_score": new_avg,
        }

    async def get_winner(self, experiment_id: str) -> dict | None:
        """Compare both variants and return the one with the higher success rate + quality."""
        rows = await self._db.execute_fetchall(
            "SELECT * FROM prompt_experiments WHERE id LIKE ?",
            (f"{experiment_id}-%",),
        )
        if len(rows) < 2:
            return None

        def _score(row: dict) -> float:
            success_rate = row["successes"] / row["trials"] if row["trials"] > 0 else 0.0
            quality = row["avg_quality_score"] or 0.0
            return success_rate + quality

        best = max(rows, key=_score)
        return {
            "experiment_id": experiment_id,
            "winner": best["variant_key"],
            "trials": best["trials"],
            "successes": best["successes"],
            "success_rate": best["successes"] / best["trials"] if best["trials"] > 0 else 0.0,
            "avg_quality_score": best["avg_quality_score"],
        }

    # ------------------------------------------------------------------
    # Feature 14: Cross-Project Knowledge Transfer
    # ------------------------------------------------------------------

    async def store_cross_project(
        self, source_project: str, knowledge_type: str, title: str, content: str
    ) -> dict:
        """Store a cross-project knowledge entry."""
        now = datetime.now(timezone.utc).isoformat()
        kid = f"CPK-{uuid.uuid4().hex[:8]}"
        applicability_score = 1.0

        await self._db.execute(
            "INSERT INTO cross_project_knowledge "
            "(id, source_project, knowledge_type, title, content, applicability_score, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (kid, source_project, knowledge_type, title, content, applicability_score, now),
        )

        return {
            "id": kid,
            "source_project": source_project,
            "knowledge_type": knowledge_type,
            "title": title,
            "content": content,
            "applicability_score": applicability_score,
            "created_at": now,
        }

    async def find_applicable(self, knowledge_type: str, limit: int = 10) -> list[dict]:
        """Find applicable cross-project knowledge by type, ordered by score."""
        return await self._db.execute_fetchall(
            "SELECT * FROM cross_project_knowledge WHERE knowledge_type = ? "
            "ORDER BY applicability_score DESC LIMIT ?",
            (knowledge_type, limit),
        )

    # ------------------------------------------------------------------
    # Feature 15: Agent Performance Benchmarking
    # ------------------------------------------------------------------

    async def record_benchmark(
        self,
        agent_role: str,
        metric_name: str,
        metric_value: float,
        period: str,
        details: str | None = None,
    ) -> dict:
        """Record a benchmark metric for an agent role."""
        now = datetime.now(timezone.utc).isoformat()
        bid = f"BM-{uuid.uuid4().hex[:8]}"

        await self._db.execute(
            "INSERT INTO agent_benchmarks "
            "(id, agent_role, metric_name, metric_value, period, details, recorded_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (bid, agent_role, metric_name, metric_value, period, details, now),
        )

        return {
            "id": bid,
            "agent_role": agent_role,
            "metric_name": metric_name,
            "metric_value": metric_value,
            "period": period,
            "details": details,
            "recorded_at": now,
        }

    async def compare_agents(self, metric_name: str, period: str | None = None) -> list[dict]:
        """Compare agents on a metric, optionally filtered by period."""
        if period:
            rows = await self._db.execute_fetchall(
                "SELECT agent_role, AVG(metric_value) as avg_value, COUNT(*) as samples "
                "FROM agent_benchmarks WHERE metric_name = ? AND period = ? "
                "GROUP BY agent_role ORDER BY avg_value DESC",
                (metric_name, period),
            )
        else:
            rows = await self._db.execute_fetchall(
                "SELECT agent_role, AVG(metric_value) as avg_value, COUNT(*) as samples "
                "FROM agent_benchmarks WHERE metric_name = ? "
                "GROUP BY agent_role ORDER BY avg_value DESC",
                (metric_name,),
            )
        return rows

    # ------------------------------------------------------------------
    # Feature 16: Outcome-Based Strategy Adjustment
    # ------------------------------------------------------------------

    async def suggest_adjustments(self, agent_role: str) -> list[dict]:
        """Suggest strategy adjustments for task types with <50% success."""
        try:
            rows = await self._db.execute_fetchall(
                "SELECT task_type, "
                "COUNT(*) as total, "
                "SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as successes, "
                "SUM(CASE WHEN status IN ('failed', 'rejected') THEN 1 ELSE 0 END) as failures "
                "FROM tasks WHERE assigned_to = ? AND status IN ('completed', 'failed', 'rejected') "
                "GROUP BY task_type",
                (agent_role,),
            )
        except Exception as exc:
            logger.warning("Failed to query tasks for adjustments: %s", exc)
            return []

        suggestions: list[dict] = []
        for row in rows:
            total = row["total"]
            successes = row["successes"] or 0
            rate = successes / total if total > 0 else 0.0
            if rate < 0.5:
                suggestions.append({
                    "agent_role": agent_role,
                    "task_type": row["task_type"],
                    "success_rate": round(rate, 3),
                    "total_tasks": total,
                    "suggestion": (
                        f"Task type '{row['task_type']}' has a {rate:.0%} success rate. "
                        f"Consider reviewing approach, adding more context, or breaking tasks into smaller units."
                    ),
                })

        return suggestions

    # ------------------------------------------------------------------
    # Feature 17: Review Feedback Loop
    # ------------------------------------------------------------------

    async def track_repeated_corrections(self, agent_role: str) -> list[dict]:
        """Find repeated rejection patterns for an agent role."""
        rows = await self._db.execute_fetchall(
            "SELECT rejection_reason FROM tasks "
            "WHERE assigned_to = ? AND rejection_reason IS NOT NULL AND rejection_reason != ''",
            (agent_role,),
        )

        if not rows:
            return []

        # Count common keywords across rejection reasons
        word_counter: Counter = Counter()
        stop_words = {"the", "a", "an", "is", "was", "are", "to", "and", "of", "in", "for", "it", "not", "on", "with"}
        for row in rows:
            reason = row["rejection_reason"]
            words = re.findall(r'\b[a-zA-Z]{3,}\b', reason.lower())
            unique_words = set(words) - stop_words
            word_counter.update(unique_words)

        patterns: list[dict] = []
        for word, count in word_counter.most_common(10):
            if count >= 2:
                patterns.append({
                    "agent_role": agent_role,
                    "pattern": word,
                    "count": count,
                })

        return patterns

    # ------------------------------------------------------------------
    # Feature 18: Codebase Convention Learning
    # ------------------------------------------------------------------

    async def learn_conventions(self, directory: str = "src/") -> list[dict]:
        """Walk Python files and detect naming conventions, upsert into DB."""
        directory = validate_path(directory)
        now = datetime.now(timezone.utc).isoformat()

        conventions: dict[tuple[str, str], dict] = {}

        for root, _dirs, files in os.walk(directory):
            for fname in files:
                if not fname.endswith(".py"):
                    continue
                fpath = os.path.join(root, fname)
                try:
                    with open(fpath, encoding="utf-8", errors="ignore") as f:
                        content = f.read()
                except OSError as exc:
                    logger.warning("Cannot read %s: %s", fpath, exc)
                    continue

                # Detect snake_case function names
                func_names = re.findall(r'def\s+([a-z_][a-z0-9_]*)\s*\(', content)
                for name in func_names:
                    key = ("function_naming", "snake_case")
                    entry = conventions.setdefault(key, {"count": 0, "examples": []})
                    entry["count"] += 1
                    if len(entry["examples"]) < 3:
                        entry["examples"].append(name)

                # Detect CamelCase class names
                class_names = re.findall(r'class\s+([A-Z][a-zA-Z0-9]*)', content)
                for name in class_names:
                    key = ("class_naming", "CamelCase")
                    entry = conventions.setdefault(key, {"count": 0, "examples": []})
                    entry["count"] += 1
                    if len(entry["examples"]) < 3:
                        entry["examples"].append(name)

                # Detect UPPER_CASE constants
                constant_names = re.findall(r'^([A-Z][A-Z0-9_]+)\s*=', content, re.MULTILINE)
                for name in constant_names:
                    key = ("constant_naming", "UPPER_CASE")
                    entry = conventions.setdefault(key, {"count": 0, "examples": []})
                    entry["count"] += 1
                    if len(entry["examples"]) < 3:
                        entry["examples"].append(name)

        results: list[dict] = []
        for (conv_type, pattern), data in conventions.items():
            confidence = min(1.0, data["count"] / 10.0)
            examples_json = json.dumps(data["examples"])

            await self._db.execute(
                "INSERT INTO codebase_conventions (id, convention_type, pattern, frequency, confidence, examples, last_updated) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(convention_type, pattern) DO UPDATE SET "
                "frequency = ?, confidence = ?, examples = ?, last_updated = ?",
                (
                    f"CONV-{uuid.uuid4().hex[:8]}",
                    conv_type, pattern, data["count"], confidence, examples_json, now,
                    data["count"], confidence, examples_json, now,
                ),
            )

            results.append({
                "convention_type": conv_type,
                "pattern": pattern,
                "frequency": data["count"],
                "confidence": confidence,
                "examples": data["examples"],
            })

        return results

    async def get_conventions(self) -> list[dict]:
        """Get all known codebase conventions ordered by confidence."""
        rows = await self._db.execute_fetchall(
            "SELECT * FROM codebase_conventions ORDER BY confidence DESC"
        )
        for row in rows:
            if isinstance(row.get("examples"), str):
                try:
                    row["examples"] = json.loads(row["examples"])
                except (json.JSONDecodeError, TypeError) as exc:
                    logger.warning("Failed to parse convention examples JSON: %s", exc)
        return rows

    # ------------------------------------------------------------------
    # Feature 19: Error Pattern Recognition
    # ------------------------------------------------------------------

    async def cluster_errors(self, lookback_limit: int = 100) -> list[dict]:
        """Cluster recent failed tasks by common words in rejection_reason."""
        rows = await self._db.execute_fetchall(
            "SELECT id, rejection_reason FROM tasks "
            "WHERE status IN ('failed', 'rejected') AND rejection_reason IS NOT NULL "
            "ORDER BY created_at DESC LIMIT ?",
            (lookback_limit,),
        )

        if not rows:
            return []

        now = datetime.now(timezone.utc).isoformat()
        stop_words = {"the", "a", "an", "is", "was", "are", "to", "and", "of", "in", "for", "it", "not", "on", "with"}
        word_counter: Counter = Counter()
        for row in rows:
            reason = row["rejection_reason"]
            words = re.findall(r'\b[a-zA-Z]{3,}\b', reason.lower())
            word_counter.update(set(words) - stop_words)

        clusters: list[dict] = []
        for word, count in word_counter.most_common(10):
            if count < 2:
                continue
            cluster_name = f"error-{word}"
            prevention_hint = f"Address recurring '{word}' issues by reviewing related patterns before implementation."

            await self._db.execute(
                "INSERT INTO error_clusters (id, cluster_name, root_cause, error_pattern, "
                "occurrence_count, last_seen, prevention_hint) VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(cluster_name) DO UPDATE SET "
                "occurrence_count = ?, last_seen = ?, prevention_hint = ?",
                (
                    f"EC-{uuid.uuid4().hex[:8]}",
                    cluster_name, word, word, count, now, prevention_hint,
                    count, now, prevention_hint,
                ),
            )

            clusters.append({
                "cluster_name": cluster_name,
                "root_cause": word,
                "error_pattern": word,
                "occurrence_count": count,
                "prevention_hint": prevention_hint,
            })

        return clusters

    async def get_prevention_hints(self, error_pattern: str) -> list[dict]:
        """Get prevention hints matching an error pattern."""
        return await self._db.execute_fetchall(
            "SELECT cluster_name, prevention_hint, occurrence_count FROM error_clusters "
            "WHERE error_pattern LIKE ?",
            (f"%{error_pattern}%",),
        )
