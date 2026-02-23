"""Task intelligence: complexity estimation, prerequisite detection, decomposition optimization, parallel opportunity finding, context budget planning, outcome prediction, similarity matching, effort drift detection."""

from __future__ import annotations

import json
import logging
import math
import re
import time

from taskbrew.intelligence._utils import utcnow, new_id, clamp

logger = logging.getLogger(__name__)


# Keywords that increase estimated complexity
_HIGH_COMPLEXITY_KEYWORDS = frozenset({
    "refactor", "migrate", "security", "encryption", "authentication",
    "distributed", "concurrency", "async", "migration", "redesign",
    "optimize", "performance", "scalability",
})

# Keywords signaling sequential dependency
_SEQUENTIAL_KEYWORDS = frozenset({
    "after", "depends on", "requires", "once", "following", "before",
    "prerequisite", "blocked by", "waiting for",
})


class TaskIntelligenceManager:
    """Analyze tasks for complexity, dependencies, parallelism, and outcomes."""

    def __init__(self, db, task_board=None, memory_manager=None) -> None:
        self._db = db
        self._task_board = task_board
        self._memory_manager = memory_manager

    # ------------------------------------------------------------------
    # Schema bootstrap
    # ------------------------------------------------------------------

    async def ensure_tables(self) -> None:
        await self._db.executescript("""
            CREATE TABLE IF NOT EXISTS complexity_estimates (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                description TEXT,
                files_involved TEXT,
                complexity_score INTEGER NOT NULL,
                actual_complexity INTEGER,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS detected_prerequisites (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                prerequisite_task_id TEXT,
                reason TEXT NOT NULL,
                confirmed INTEGER,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS decomposition_metrics (
                id TEXT PRIMARY KEY,
                parent_task_id TEXT NOT NULL,
                subtask_count INTEGER NOT NULL,
                avg_subtask_duration_ms REAL NOT NULL,
                success_rate REAL NOT NULL,
                task_type TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS parallel_opportunities (
                id TEXT PRIMARY KEY,
                group_id TEXT NOT NULL,
                task_set TEXT NOT NULL,
                reason TEXT NOT NULL,
                exploited INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS context_budgets (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL UNIQUE,
                estimated_files INTEGER NOT NULL,
                estimated_tokens_per_file INTEGER NOT NULL DEFAULT 500,
                total_budget INTEGER NOT NULL,
                actual_tokens_used INTEGER,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS outcome_predictions (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                complexity_score INTEGER NOT NULL,
                agent_role TEXT NOT NULL,
                historical_success_rate REAL,
                predicted_success REAL NOT NULL,
                actual_success INTEGER,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS task_fingerprints (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                description TEXT,
                task_type TEXT,
                keywords TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS effort_tracking (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL UNIQUE,
                estimated_duration_ms INTEGER NOT NULL,
                started_at_epoch_ms INTEGER NOT NULL,
                completed_at_epoch_ms INTEGER,
                actual_duration_ms INTEGER,
                drift_ratio REAL,
                status TEXT NOT NULL DEFAULT 'tracking',
                created_at TEXT NOT NULL
            );
        """)

    # ------------------------------------------------------------------
    # Feature 25: Task Complexity Estimator
    # ------------------------------------------------------------------

    async def estimate_complexity(
        self,
        task_id: str,
        title: str,
        description: str,
        files_involved: list[str] | None = None,
    ) -> dict:
        """Score task complexity 1-10 based on word count, file count, and keywords."""
        text = f"{title} {description}".lower()
        words = text.split()
        word_count = len(words)

        # Word count contribution (0-3 points)
        word_score = min(word_count / 50.0, 1.0) * 3

        # File count contribution (0-3 points)
        file_count = len(files_involved) if files_involved else 0
        file_score = min(file_count / 10.0, 1.0) * 3

        # Keyword complexity boost (0-4 points)
        keyword_hits = sum(1 for kw in _HIGH_COMPLEXITY_KEYWORDS if kw in text)
        keyword_score = min(keyword_hits / 3.0, 1.0) * 4

        raw_score = word_score + file_score + keyword_score
        complexity_score = max(1, min(10, round(raw_score)))

        now = utcnow()
        est_id = f"CE-{new_id(8)}"
        files_json = json.dumps(files_involved) if files_involved else None

        # Use INSERT OR REPLACE for idempotency on task_id
        await self._db.execute(
            "INSERT OR REPLACE INTO complexity_estimates "
            "(id, task_id, title, description, files_involved, complexity_score, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (est_id, task_id, title, description, files_json, complexity_score, now),
        )
        return {
            "id": est_id,
            "task_id": task_id,
            "complexity_score": complexity_score,
            "word_count": word_count,
            "file_count": file_count,
            "keyword_hits": keyword_hits,
            "created_at": now,
        }

    async def get_estimate(self, task_id: str) -> dict | None:
        """Retrieve a complexity estimate for a task."""
        return await self._db.execute_fetchone(
            "SELECT * FROM complexity_estimates WHERE task_id = ?",
            (task_id,),
        )

    async def calibrate(self, task_id: str, actual_complexity: int) -> dict:
        """Record actual complexity for future calibration."""
        actual_complexity = max(1, min(10, actual_complexity))
        await self._db.execute(
            "UPDATE complexity_estimates SET actual_complexity = ? WHERE task_id = ?",
            (actual_complexity, task_id),
        )
        return {"task_id": task_id, "actual_complexity": actual_complexity}

    # ------------------------------------------------------------------
    # Feature 26: Prerequisite Auto-Detector
    # ------------------------------------------------------------------

    async def detect_prerequisites(
        self,
        task_id: str,
        description: str,
        files_involved: list[str] | None = None,
    ) -> list[dict]:
        """Scan description for implicit dependencies.

        Looks for sequential keywords and shared file references.
        """
        now = utcnow()
        prereqs: list[dict] = []
        desc_lower = description.lower()

        # Check for sequential keywords referencing other tasks
        for kw in _SEQUENTIAL_KEYWORDS:
            if kw in desc_lower:
                # Extract task IDs (e.g., CD-123, TSK-456)
                task_refs = re.findall(r'[A-Z]{2,}-\d{3,}', description)
                for ref in task_refs:
                    if ref != task_id:
                        prereq_id = f"PR-{new_id(8)}"
                        prereq = {
                            "id": prereq_id,
                            "task_id": task_id,
                            "prerequisite_task_id": ref,
                            "reason": f"Sequential keyword '{kw}' with reference to {ref}",
                            "confirmed": None,
                            "created_at": now,
                        }
                        prereqs.append(prereq)

                # Also add a generic dependency if keyword found but no task ref
                if not task_refs:
                    prereq_id = f"PR-{new_id(8)}"
                    prereq = {
                        "id": prereq_id,
                        "task_id": task_id,
                        "prerequisite_task_id": None,
                        "reason": f"Sequential keyword '{kw}' detected in description",
                        "confirmed": None,
                        "created_at": now,
                    }
                    prereqs.append(prereq)
                break  # One keyword detection is sufficient

        # Check for shared files with other tasks
        if files_involved:
            existing = await self._db.execute_fetchall(
                "SELECT task_id, files_involved FROM complexity_estimates "
                "WHERE task_id != ? AND files_involved IS NOT NULL",
                (task_id,),
            )
            for row in existing:
                try:
                    other_files = json.loads(row["files_involved"])
                except (json.JSONDecodeError, TypeError):
                    continue
                shared = set(files_involved) & set(other_files)
                if shared:
                    prereq_id = f"PR-{new_id(8)}"
                    prereq = {
                        "id": prereq_id,
                        "task_id": task_id,
                        "prerequisite_task_id": row["task_id"],
                        "reason": f"Shared files: {', '.join(list(shared)[:3])}",
                        "confirmed": None,
                        "created_at": now,
                    }
                    prereqs.append(prereq)

        # Persist
        for p in prereqs:
            await self._db.execute(
                "INSERT INTO detected_prerequisites "
                "(id, task_id, prerequisite_task_id, reason, confirmed, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (p["id"], p["task_id"], p["prerequisite_task_id"],
                 p["reason"], p["confirmed"], p["created_at"]),
            )

        return prereqs

    async def get_prerequisites(self, task_id: str) -> list[dict]:
        """List detected prerequisites for a task."""
        return await self._db.execute_fetchall(
            "SELECT * FROM detected_prerequisites WHERE task_id = ? "
            "ORDER BY created_at DESC",
            (task_id,),
        )

    async def confirm_prerequisite(
        self, prereq_id: str, confirmed: bool = True
    ) -> dict:
        """Mark a prerequisite as confirmed or rejected."""
        await self._db.execute(
            "UPDATE detected_prerequisites SET confirmed = ? WHERE id = ?",
            (int(confirmed), prereq_id),
        )
        return {"id": prereq_id, "confirmed": confirmed}

    # ------------------------------------------------------------------
    # Feature 27: Decomposition Optimizer
    # ------------------------------------------------------------------

    async def record_decomposition(
        self,
        parent_task_id: str,
        subtask_count: int,
        avg_subtask_duration_ms: float,
        success_rate: float,
        task_type: str | None = None,
    ) -> dict:
        """Record decomposition metrics for a parent task."""
        success_rate = clamp(success_rate)
        now = utcnow()
        metric_id = f"DM-{new_id(8)}"
        await self._db.execute(
            "INSERT INTO decomposition_metrics "
            "(id, parent_task_id, subtask_count, avg_subtask_duration_ms, "
            "success_rate, task_type, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (metric_id, parent_task_id, subtask_count, avg_subtask_duration_ms,
             success_rate, task_type, now),
        )
        return {
            "id": metric_id,
            "parent_task_id": parent_task_id,
            "subtask_count": subtask_count,
            "avg_subtask_duration_ms": avg_subtask_duration_ms,
            "success_rate": success_rate,
            "task_type": task_type,
            "created_at": now,
        }

    async def get_optimal_granularity(
        self, task_type: str | None = None
    ) -> dict:
        """Recommend subtask count range based on historical success.

        Returns the subtask count range that had the highest average success rate.
        """
        if task_type:
            rows = await self._db.execute_fetchall(
                "SELECT subtask_count, AVG(success_rate) AS avg_success "
                "FROM decomposition_metrics WHERE task_type = ? "
                "GROUP BY subtask_count ORDER BY avg_success DESC",
                (task_type,),
            )
        else:
            rows = await self._db.execute_fetchall(
                "SELECT subtask_count, AVG(success_rate) AS avg_success "
                "FROM decomposition_metrics "
                "GROUP BY subtask_count ORDER BY avg_success DESC",
            )

        if not rows:
            return {"recommended_min": 2, "recommended_max": 5, "based_on": 0}

        # Take the top performing subtask counts
        best = rows[0]
        best_count = best["subtask_count"]
        return {
            "recommended_min": max(1, best_count - 1),
            "recommended_max": best_count + 1,
            "best_subtask_count": best_count,
            "best_avg_success": round(best["avg_success"], 4),
            "based_on": len(rows),
        }

    async def get_metrics(self, limit: int = 20) -> list[dict]:
        """Return decomposition metric history."""
        return await self._db.execute_fetchall(
            "SELECT * FROM decomposition_metrics ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )

    # ------------------------------------------------------------------
    # Feature 28: Parallel Opportunity Finder
    # ------------------------------------------------------------------

    async def find_parallel_tasks(self, group_id: str) -> list[dict]:
        """Analyze tasks in a group and identify sets that touch disjoint files."""
        # Get tasks with files from complexity estimates
        tasks = await self._db.execute_fetchall(
            "SELECT ce.task_id, ce.files_involved "
            "FROM complexity_estimates ce "
            "JOIN tasks t ON t.id = ce.task_id "
            "WHERE t.group_id = ? AND t.status = 'pending' AND ce.files_involved IS NOT NULL",
            (group_id,),
        )

        if len(tasks) < 2:
            return []

        now = utcnow()
        opportunities: list[dict] = []

        # Parse file lists
        task_files: list[tuple[str, set[str]]] = []
        for t in tasks:
            try:
                files = set(json.loads(t["files_involved"]))
            except (json.JSONDecodeError, TypeError):
                continue
            task_files.append((t["task_id"], files))

        # Find pairs with disjoint files
        for i in range(len(task_files)):
            for j in range(i + 1, len(task_files)):
                tid_a, files_a = task_files[i]
                tid_b, files_b = task_files[j]
                if files_a.isdisjoint(files_b):
                    opp_id = f"PO-{new_id(8)}"
                    opp = {
                        "id": opp_id,
                        "group_id": group_id,
                        "task_set": json.dumps([tid_a, tid_b]),
                        "reason": f"Disjoint file sets: {len(files_a)} vs {len(files_b)} files",
                        "exploited": 0,
                        "created_at": now,
                    }
                    await self._db.execute(
                        "INSERT INTO parallel_opportunities "
                        "(id, group_id, task_set, reason, exploited, created_at) "
                        "VALUES (?, ?, ?, ?, 0, ?)",
                        (opp_id, group_id, opp["task_set"], opp["reason"], now),
                    )
                    opportunities.append(opp)

        return opportunities

    async def get_opportunities(
        self, group_id: str | None = None, limit: int = 20
    ) -> list[dict]:
        """List parallel opportunities."""
        if group_id:
            rows = await self._db.execute_fetchall(
                "SELECT * FROM parallel_opportunities WHERE group_id = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (group_id, limit),
            )
        else:
            rows = await self._db.execute_fetchall(
                "SELECT * FROM parallel_opportunities "
                "ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
        for row in rows:
            if isinstance(row.get("task_set"), str):
                try:
                    row["task_set"] = json.loads(row["task_set"])
                except (json.JSONDecodeError, TypeError):
                    pass
        return rows

    async def mark_exploited(self, opportunity_id: str) -> dict:
        """Mark a parallel opportunity as exploited."""
        await self._db.execute(
            "UPDATE parallel_opportunities SET exploited = 1 WHERE id = ?",
            (opportunity_id,),
        )
        return {"id": opportunity_id, "exploited": True}

    # ------------------------------------------------------------------
    # Feature 29: Context Budget Planner
    # ------------------------------------------------------------------

    async def plan_budget(
        self,
        task_id: str,
        estimated_files: int,
        estimated_tokens_per_file: int = 500,
    ) -> dict:
        """Compute total token budget for a task."""
        total_budget = estimated_files * estimated_tokens_per_file
        now = utcnow()
        budget_id = f"CB-{new_id(8)}"

        await self._db.execute(
            "INSERT OR REPLACE INTO context_budgets "
            "(id, task_id, estimated_files, estimated_tokens_per_file, total_budget, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (budget_id, task_id, estimated_files, estimated_tokens_per_file, total_budget, now),
        )
        return {
            "id": budget_id,
            "task_id": task_id,
            "estimated_files": estimated_files,
            "estimated_tokens_per_file": estimated_tokens_per_file,
            "total_budget": total_budget,
            "created_at": now,
        }

    async def get_budget(self, task_id: str) -> dict | None:
        """Retrieve the budget for a task."""
        return await self._db.execute_fetchone(
            "SELECT * FROM context_budgets WHERE task_id = ?",
            (task_id,),
        )

    async def record_actual(self, task_id: str, actual_tokens_used: int) -> dict:
        """Record actual token usage for calibration."""
        await self._db.execute(
            "UPDATE context_budgets SET actual_tokens_used = ? WHERE task_id = ?",
            (actual_tokens_used, task_id),
        )
        budget = await self.get_budget(task_id)
        return {
            "task_id": task_id,
            "actual_tokens_used": actual_tokens_used,
            "total_budget": budget["total_budget"] if budget else None,
        }

    # ------------------------------------------------------------------
    # Feature 30: Task Outcome Predictor
    # ------------------------------------------------------------------

    async def predict_outcome(
        self,
        task_id: str,
        complexity_score: int,
        agent_role: str,
        historical_success_rate: float | None = None,
    ) -> dict:
        """Predict P(success) using a logistic-style formula.

        P(success) = 1 / (1 + exp(0.5 * complexity - 3))
        Adjusted by historical_success_rate if provided.
        """
        complexity_score = max(1, min(10, complexity_score))

        # Base prediction via logistic function
        z = 0.5 * complexity_score - 3
        base_p = 1.0 / (1.0 + math.exp(z))

        if historical_success_rate is not None:
            historical_success_rate = clamp(historical_success_rate)
            # Blend: 60% model, 40% historical
            predicted = 0.6 * base_p + 0.4 * historical_success_rate
        else:
            predicted = base_p

        predicted = round(clamp(predicted), 4)

        now = utcnow()
        pred_id = f"OP-{new_id(8)}"
        await self._db.execute(
            "INSERT INTO outcome_predictions "
            "(id, task_id, complexity_score, agent_role, historical_success_rate, "
            "predicted_success, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (pred_id, task_id, complexity_score, agent_role,
             historical_success_rate, predicted, now),
        )
        return {
            "id": pred_id,
            "task_id": task_id,
            "complexity_score": complexity_score,
            "agent_role": agent_role,
            "predicted_success": predicted,
            "created_at": now,
        }

    async def record_actual_outcome(
        self, prediction_id: str, success: bool
    ) -> dict:
        """Record the actual outcome for a prediction."""
        await self._db.execute(
            "UPDATE outcome_predictions SET actual_success = ? WHERE id = ?",
            (int(success), prediction_id),
        )
        return {"id": prediction_id, "actual_success": success}

    async def get_prediction_accuracy(
        self, agent_role: str | None = None
    ) -> dict:
        """Compute prediction accuracy overall or per agent_role."""
        if agent_role:
            rows = await self._db.execute_fetchall(
                "SELECT predicted_success, actual_success FROM outcome_predictions "
                "WHERE agent_role = ? AND actual_success IS NOT NULL",
                (agent_role,),
            )
        else:
            rows = await self._db.execute_fetchall(
                "SELECT predicted_success, actual_success FROM outcome_predictions "
                "WHERE actual_success IS NOT NULL",
            )

        if not rows:
            return {"accuracy": None, "sample_count": 0}

        correct = 0
        for row in rows:
            predicted_bool = row["predicted_success"] >= 0.5
            actual_bool = bool(row["actual_success"])
            if predicted_bool == actual_bool:
                correct += 1

        accuracy = round(correct / len(rows), 4)
        return {"accuracy": accuracy, "sample_count": len(rows), "agent_role": agent_role}

    # ------------------------------------------------------------------
    # Feature 31: Task Similarity Matcher
    # ------------------------------------------------------------------

    async def fingerprint_task(
        self,
        task_id: str,
        title: str,
        description: str,
        task_type: str | None = None,
    ) -> dict:
        """Extract keyword set and store as fingerprint."""
        text = f"{title} {description}".lower()
        # Extract meaningful words (3+ chars, no stopwords)
        stopwords = {"the", "and", "for", "with", "this", "that", "from", "are", "was",
                      "will", "can", "has", "have", "had", "not", "but", "all", "any"}
        words = re.findall(r'[a-z_][a-z0-9_]{2,}', text)
        keywords = sorted(set(w for w in words if w not in stopwords))
        keywords_str = ",".join(keywords)

        now = utcnow()
        fp_id = f"FP-{new_id(8)}"
        await self._db.execute(
            "INSERT OR REPLACE INTO task_fingerprints "
            "(id, task_id, title, description, task_type, keywords, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (fp_id, task_id, title, description, task_type, keywords_str, now),
        )
        return {
            "id": fp_id,
            "task_id": task_id,
            "keywords": keywords,
            "keyword_count": len(keywords),
            "created_at": now,
        }

    async def find_similar(
        self, title: str, description: str, limit: int = 5
    ) -> list[dict]:
        """Find similar tasks using Jaccard similarity on keyword sets."""
        text = f"{title} {description}".lower()
        stopwords = {"the", "and", "for", "with", "this", "that", "from", "are", "was",
                      "will", "can", "has", "have", "had", "not", "but", "all", "any"}
        words = re.findall(r'[a-z_][a-z0-9_]{2,}', text)
        query_keywords = set(w for w in words if w not in stopwords)

        if not query_keywords:
            return []

        all_fps = await self._db.execute_fetchall(
            "SELECT * FROM task_fingerprints ORDER BY created_at DESC",
        )

        results: list[dict] = []
        for fp in all_fps:
            fp_keywords = set(fp["keywords"].split(",")) if fp["keywords"] else set()
            intersection = query_keywords & fp_keywords
            union = query_keywords | fp_keywords
            if union:
                similarity = len(intersection) / len(union)
            else:
                similarity = 0.0

            if similarity > 0:
                results.append({
                    "task_id": fp["task_id"],
                    "title": fp["title"],
                    "similarity": round(similarity, 4),
                    "shared_keywords": sorted(intersection),
                })

        results.sort(key=lambda x: x["similarity"], reverse=True)
        return results[:limit]

    async def get_fingerprint(self, task_id: str) -> dict | None:
        """Retrieve a task fingerprint."""
        return await self._db.execute_fetchone(
            "SELECT * FROM task_fingerprints WHERE task_id = ?",
            (task_id,),
        )

    # ------------------------------------------------------------------
    # Feature 32: Effort Drift Detector
    # ------------------------------------------------------------------

    async def start_tracking(
        self, task_id: str, estimated_duration_ms: int
    ) -> dict:
        """Start effort tracking for a task."""
        now = utcnow()
        track_id = f"ET-{new_id(8)}"
        started_at_epoch_ms = int(time.time() * 1000)

        await self._db.execute(
            "INSERT OR REPLACE INTO effort_tracking "
            "(id, task_id, estimated_duration_ms, started_at_epoch_ms, "
            "status, created_at) "
            "VALUES (?, ?, ?, ?, 'tracking', ?)",
            (track_id, task_id, estimated_duration_ms, started_at_epoch_ms, now),
        )
        return {
            "id": track_id,
            "task_id": task_id,
            "estimated_duration_ms": estimated_duration_ms,
            "started_at_epoch_ms": started_at_epoch_ms,
            "status": "tracking",
            "created_at": now,
        }

    async def check_drift(self, task_id: str) -> dict:
        """Compare elapsed time vs estimated duration.

        Returns drift_ratio and alert flag if >1.5x.
        """
        row = await self._db.execute_fetchone(
            "SELECT * FROM effort_tracking WHERE task_id = ?",
            (task_id,),
        )
        if not row:
            return {"task_id": task_id, "error": "No tracking found"}

        current_epoch_ms = int(time.time() * 1000)
        elapsed_ms = current_epoch_ms - row["started_at_epoch_ms"]
        estimated = row["estimated_duration_ms"]

        if estimated <= 0:
            drift_ratio = 0.0
        else:
            drift_ratio = round(elapsed_ms / estimated, 4)

        alert = drift_ratio > 1.5

        return {
            "task_id": task_id,
            "elapsed_ms": elapsed_ms,
            "estimated_duration_ms": estimated,
            "drift_ratio": drift_ratio,
            "alert": alert,
        }

    async def complete_tracking(self, task_id: str) -> dict:
        """Finalize tracking with actual duration."""
        row = await self._db.execute_fetchone(
            "SELECT * FROM effort_tracking WHERE task_id = ?",
            (task_id,),
        )
        if not row:
            return {"task_id": task_id, "error": "No tracking found"}

        completed_epoch_ms = int(time.time() * 1000)
        actual_duration_ms = completed_epoch_ms - row["started_at_epoch_ms"]
        estimated = row["estimated_duration_ms"]

        if estimated <= 0:
            drift_ratio = 0.0
        else:
            drift_ratio = round(actual_duration_ms / estimated, 4)

        await self._db.execute(
            "UPDATE effort_tracking SET completed_at_epoch_ms = ?, "
            "actual_duration_ms = ?, drift_ratio = ?, status = 'completed' "
            "WHERE task_id = ?",
            (completed_epoch_ms, actual_duration_ms, drift_ratio, task_id),
        )
        return {
            "task_id": task_id,
            "actual_duration_ms": actual_duration_ms,
            "estimated_duration_ms": estimated,
            "drift_ratio": drift_ratio,
            "status": "completed",
        }

    async def get_drift_history(self, limit: int = 20) -> list[dict]:
        """List completed tasks with drift ratios."""
        return await self._db.execute_fetchall(
            "SELECT * FROM effort_tracking WHERE status = 'completed' "
            "ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
