"""Process intelligence: velocity forecasting, risk heat maps, bottleneck mining, release readiness scoring, stakeholder impact assessment, sprint retrospective generation."""

from __future__ import annotations

import json
import logging
import random
import statistics

from taskbrew.intelligence._utils import utcnow, new_id, clamp

logger = logging.getLogger(__name__)


# Valid process phases for bottleneck mining
_VALID_PHASES = ("planning", "coding", "review", "testing", "deployment")

# Impact levels ordered from lowest to highest
_IMPACT_LEVELS = ("none", "low", "medium", "high", "critical")

# Release readiness scoring weights
_READINESS_WEIGHTS = {
    "test_pass_rate": 0.30,
    "open_bugs": 0.20,
    "doc_freshness": 0.15,
    "code_review_coverage": 0.20,
    "security_scan": 0.15,
}


class ProcessIntelligenceManager:
    """Manage process intelligence and analytics capabilities."""

    def __init__(self, db, task_board=None) -> None:
        self._db = db
        self._task_board = task_board

    # ------------------------------------------------------------------
    # Schema bootstrap
    # ------------------------------------------------------------------

    async def ensure_tables(self) -> None:
        await self._db.executescript("""
            CREATE TABLE IF NOT EXISTS velocity_samples (
                id TEXT PRIMARY KEY,
                sprint_id TEXT NOT NULL,
                tasks_completed INTEGER NOT NULL,
                story_points REAL,
                duration_days REAL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS risk_scores (
                id TEXT PRIMARY KEY,
                file_path TEXT NOT NULL UNIQUE,
                change_frequency INTEGER NOT NULL DEFAULT 0,
                complexity_score REAL NOT NULL DEFAULT 0.0,
                test_coverage_pct REAL NOT NULL DEFAULT 0.0,
                risk_score REAL NOT NULL DEFAULT 0.0,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS process_metrics (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                phase TEXT NOT NULL,
                duration_ms INTEGER NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS readiness_assessments (
                id TEXT PRIMARY KEY,
                release_id TEXT NOT NULL,
                score REAL NOT NULL,
                metrics TEXT NOT NULL,
                breakdown TEXT NOT NULL,
                assessed_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS stakeholder_impacts (
                id TEXT PRIMARY KEY,
                change_id TEXT NOT NULL,
                stakeholder_group TEXT NOT NULL,
                impact_level TEXT NOT NULL,
                description TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sprint_retrospectives (
                id TEXT PRIMARY KEY,
                sprint_id TEXT NOT NULL UNIQUE,
                what_improved TEXT,
                what_regressed TEXT,
                stalled TEXT,
                recommendations TEXT,
                generated_at TEXT NOT NULL
            );
        """)

    # ------------------------------------------------------------------
    # Feature 39: Velocity Forecaster
    # ------------------------------------------------------------------

    async def record_velocity(
        self,
        sprint_id: str,
        tasks_completed: int,
        story_points: float | None = None,
        duration_days: float | None = None,
    ) -> dict:
        """Record sprint velocity data."""
        vel_id = f"VS-{new_id(8)}"
        now = utcnow()
        await self._db.execute(
            "INSERT INTO velocity_samples "
            "(id, sprint_id, tasks_completed, story_points, duration_days, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (vel_id, sprint_id, tasks_completed, story_points, duration_days, now),
        )
        return {
            "id": vel_id,
            "sprint_id": sprint_id,
            "tasks_completed": tasks_completed,
            "story_points": story_points,
            "duration_days": duration_days,
            "created_at": now,
        }

    async def forecast(
        self, remaining_points: float, num_simulations: int = 1000
    ) -> dict:
        """Monte Carlo forecast: sample from historical velocities to estimate sprints needed.

        Returns p50, p75, p90 sprint estimates for completing *remaining_points*.
        """
        rows = await self._db.execute_fetchall(
            "SELECT story_points FROM velocity_samples WHERE story_points IS NOT NULL AND story_points > 0",
        )
        if not rows:
            return {
                "remaining_points": remaining_points,
                "p50": None,
                "p75": None,
                "p90": None,
                "error": "No historical velocity data with story points",
            }

        velocities = [row["story_points"] for row in rows]
        results = []

        for _ in range(num_simulations):
            total = 0.0
            sprints = 0
            while total < remaining_points:
                sampled = random.choice(velocities)
                total += sampled
                sprints += 1
            results.append(sprints)

        results.sort()
        n = len(results)
        p50 = results[int(n * 0.50)]
        p75 = results[int(n * 0.75)]
        p90 = results[min(int(n * 0.90), n - 1)]

        return {
            "remaining_points": remaining_points,
            "p50": p50,
            "p75": p75,
            "p90": p90,
            "simulations": num_simulations,
            "historical_samples": len(velocities),
        }

    async def get_velocity_history(self, limit: int = 20) -> list[dict]:
        """Return historical velocity samples."""
        return await self._db.execute_fetchall(
            "SELECT * FROM velocity_samples ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )

    # ------------------------------------------------------------------
    # Feature 40: Risk Heat Map Generator
    # ------------------------------------------------------------------

    async def score_file(
        self,
        file_path: str,
        change_frequency: int,
        complexity_score: float,
        test_coverage_pct: float,
    ) -> dict:
        """Compute composite risk score for a file.

        risk = change_frequency * complexity_score * (1 - test_coverage_pct / 100)
        """
        coverage_fraction = clamp(test_coverage_pct / 100.0)
        risk = round(change_frequency * complexity_score * (1.0 - coverage_fraction), 4)

        rec_id = f"RS-{new_id(8)}"
        now = utcnow()

        # Upsert: delete existing then insert
        await self._db.execute(
            "DELETE FROM risk_scores WHERE file_path = ?",
            (file_path,),
        )
        await self._db.execute(
            "INSERT INTO risk_scores "
            "(id, file_path, change_frequency, complexity_score, test_coverage_pct, risk_score, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (rec_id, file_path, change_frequency, complexity_score, test_coverage_pct, risk, now),
        )
        return {
            "id": rec_id,
            "file_path": file_path,
            "change_frequency": change_frequency,
            "complexity_score": complexity_score,
            "test_coverage_pct": test_coverage_pct,
            "risk_score": risk,
            "updated_at": now,
        }

    async def get_heat_map(
        self, min_risk: float = 0.0, limit: int = 50
    ) -> list[dict]:
        """Return files sorted by risk score descending."""
        return await self._db.execute_fetchall(
            "SELECT * FROM risk_scores WHERE risk_score >= ? "
            "ORDER BY risk_score DESC LIMIT ?",
            (min_risk, limit),
        )

    async def refresh_scores(self) -> dict:
        """Recalculate all risk scores from stored metrics. Returns count updated."""
        rows = await self._db.execute_fetchall(
            "SELECT * FROM risk_scores",
        )
        count = 0
        now = utcnow()
        for row in rows:
            coverage_fraction = clamp(row["test_coverage_pct"] / 100.0)
            new_risk = round(
                row["change_frequency"] * row["complexity_score"] * (1.0 - coverage_fraction), 4
            )
            await self._db.execute(
                "UPDATE risk_scores SET risk_score = ?, updated_at = ? WHERE id = ?",
                (new_risk, now, row["id"]),
            )
            count += 1
        return {"updated": count}

    # ------------------------------------------------------------------
    # Feature 41: Process Bottleneck Miner
    # ------------------------------------------------------------------

    async def record_phase_duration(
        self, task_id: str, phase: str, duration_ms: int
    ) -> dict:
        """Record the duration of a process phase for a task."""
        rec_id = f"PM-{new_id(8)}"
        now = utcnow()
        await self._db.execute(
            "INSERT INTO process_metrics (id, task_id, phase, duration_ms, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (rec_id, task_id, phase, duration_ms, now),
        )
        return {
            "id": rec_id,
            "task_id": task_id,
            "phase": phase,
            "duration_ms": duration_ms,
            "created_at": now,
        }

    async def find_bottlenecks(self) -> list[dict]:
        """Aggregate durations by phase and return sorted by avg duration (slowest first)."""
        rows = await self._db.execute_fetchall(
            "SELECT phase, "
            "AVG(duration_ms) as avg_duration_ms, "
            "COUNT(*) as sample_count "
            "FROM process_metrics "
            "GROUP BY phase "
            "ORDER BY avg_duration_ms DESC",
        )
        result = []
        for row in rows:
            result.append({
                "phase": row["phase"],
                "avg_duration_ms": round(row["avg_duration_ms"], 2),
                "sample_count": row["sample_count"],
            })
        return result

    async def get_phase_stats(self, phase: str | None = None) -> list[dict]:
        """Return statistics per phase: avg, median, p95."""
        if phase:
            phases = [phase]
        else:
            phase_rows = await self._db.execute_fetchall(
                "SELECT DISTINCT phase FROM process_metrics ORDER BY phase",
            )
            phases = [r["phase"] for r in phase_rows]

        result = []
        for p in phases:
            rows = await self._db.execute_fetchall(
                "SELECT duration_ms FROM process_metrics WHERE phase = ? ORDER BY duration_ms",
                (p,),
            )
            if not rows:
                continue

            durations = [r["duration_ms"] for r in rows]
            n = len(durations)
            avg = round(statistics.mean(durations), 2)
            median = round(statistics.median(durations), 2)
            p95_idx = min(int(n * 0.95), n - 1)
            p95 = durations[p95_idx]

            result.append({
                "phase": p,
                "avg_ms": avg,
                "median_ms": median,
                "p95_ms": p95,
                "sample_count": n,
            })
        return result

    # ------------------------------------------------------------------
    # Feature 42: Release Readiness Scorer
    # ------------------------------------------------------------------

    async def assess_readiness(self, release_id: str, metrics: dict) -> dict:
        """Score release readiness 0-100 based on weighted metrics.

        Expected metrics keys and their weights:
        - test_pass_rate (30%): 0-100 scale
        - open_bugs (20%): lower is better; scored as max(0, 100 - open_bugs * 10)
        - doc_freshness (15%): 0-100 scale
        - code_review_coverage (20%): 0-100 scale
        - security_scan (15%): 0-100 scale
        """
        breakdown = {}
        total_score = 0.0

        for metric_key, weight in _READINESS_WEIGHTS.items():
            raw = metrics.get(metric_key, 0)
            if metric_key == "open_bugs":
                # Invert: fewer bugs = higher score
                normalized = max(0, 100 - raw * 10)
            else:
                normalized = clamp(raw, 0.0, 100.0)
            weighted = normalized * weight
            breakdown[metric_key] = {
                "raw": raw,
                "normalized": round(normalized, 2),
                "weight": weight,
                "weighted_score": round(weighted, 2),
            }
            total_score += weighted

        score = round(clamp(total_score, 0.0, 100.0), 2)

        rec_id = f"RA-{new_id(8)}"
        now = utcnow()
        await self._db.execute(
            "INSERT INTO readiness_assessments "
            "(id, release_id, score, metrics, breakdown, assessed_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (rec_id, release_id, score, json.dumps(metrics), json.dumps(breakdown), now),
        )
        return {
            "id": rec_id,
            "release_id": release_id,
            "score": score,
            "breakdown": breakdown,
            "assessed_at": now,
        }

    async def get_assessment(self, release_id: str) -> dict | None:
        """Retrieve the most recent assessment for a release."""
        row = await self._db.execute_fetchone(
            "SELECT * FROM readiness_assessments WHERE release_id = ? "
            "ORDER BY assessed_at DESC LIMIT 1",
            (release_id,),
        )
        if row and isinstance(row.get("breakdown"), str):
            row["breakdown"] = json.loads(row["breakdown"])
        if row and isinstance(row.get("metrics"), str):
            row["metrics"] = json.loads(row["metrics"])
        return row

    async def get_history(self, limit: int = 10) -> list[dict]:
        """Return past readiness assessments."""
        return await self._db.execute_fetchall(
            "SELECT * FROM readiness_assessments ORDER BY assessed_at DESC LIMIT ?",
            (limit,),
        )

    # ------------------------------------------------------------------
    # Feature 43: Stakeholder Impact Assessor
    # ------------------------------------------------------------------

    async def record_impact(
        self,
        change_id: str,
        stakeholder_group: str,
        impact_level: str,
        description: str,
    ) -> dict:
        """Store a stakeholder impact record.

        impact_level must be one of: none, low, medium, high, critical.
        """
        if impact_level not in _IMPACT_LEVELS:
            raise ValueError(
                f"Invalid impact_level {impact_level!r}; must be one of {_IMPACT_LEVELS}"
            )
        rec_id = f"SI-{new_id(8)}"
        now = utcnow()
        await self._db.execute(
            "INSERT INTO stakeholder_impacts "
            "(id, change_id, stakeholder_group, impact_level, description, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (rec_id, change_id, stakeholder_group, impact_level, description, now),
        )
        return {
            "id": rec_id,
            "change_id": change_id,
            "stakeholder_group": stakeholder_group,
            "impact_level": impact_level,
            "description": description,
            "created_at": now,
        }

    async def get_impacts(
        self,
        change_id: str | None = None,
        stakeholder_group: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        """List stakeholder impacts with optional filters."""
        if change_id and stakeholder_group:
            return await self._db.execute_fetchall(
                "SELECT * FROM stakeholder_impacts "
                "WHERE change_id = ? AND stakeholder_group = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (change_id, stakeholder_group, limit),
            )
        if change_id:
            return await self._db.execute_fetchall(
                "SELECT * FROM stakeholder_impacts WHERE change_id = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (change_id, limit),
            )
        if stakeholder_group:
            return await self._db.execute_fetchall(
                "SELECT * FROM stakeholder_impacts WHERE stakeholder_group = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (stakeholder_group, limit),
            )
        return await self._db.execute_fetchall(
            "SELECT * FROM stakeholder_impacts ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )

    async def get_most_impacted(self, limit: int = 10) -> list[dict]:
        """Return stakeholder groups sorted by total impact count."""
        return await self._db.execute_fetchall(
            "SELECT stakeholder_group, COUNT(*) as impact_count "
            "FROM stakeholder_impacts "
            "GROUP BY stakeholder_group "
            "ORDER BY impact_count DESC LIMIT ?",
            (limit,),
        )

    # ------------------------------------------------------------------
    # Feature 44: Sprint Retrospective Generator
    # ------------------------------------------------------------------

    async def generate_retro(self, sprint_id: str, tasks_data: list[dict]) -> dict:
        """Auto-generate a sprint retrospective from task data.

        Each task dict should contain: title, status, duration_ms (optional),
        created_at (optional), failure_count (optional).

        Analyzes:
        - what_improved: tasks completed faster than average
        - what_regressed: tasks with high failure counts
        - stalled: tasks still pending or old
        - recommendations: actionable suggestions
        """
        if not tasks_data:
            retro = {
                "what_improved": [],
                "what_regressed": [],
                "stalled": [],
                "recommendations": ["No task data provided for analysis"],
            }
        else:
            # Compute average duration for completed tasks
            completed = [
                t for t in tasks_data
                if t.get("status") == "completed" and t.get("duration_ms")
            ]
            avg_duration = (
                statistics.mean([t["duration_ms"] for t in completed])
                if completed
                else None
            )

            what_improved = []
            what_regressed = []
            stalled = []

            for task in tasks_data:
                title = task.get("title", "Untitled")
                status = task.get("status", "unknown")
                duration = task.get("duration_ms")
                failures = task.get("failure_count", 0)

                if status == "completed" and duration and avg_duration and duration < avg_duration:
                    what_improved.append(title)

                if failures and failures > 0:
                    what_regressed.append(title)

                if status in ("pending", "blocked", "stalled"):
                    stalled.append(title)

            recommendations = []
            if what_regressed:
                recommendations.append(
                    f"Investigate root causes for {len(what_regressed)} tasks with failures"
                )
            if stalled:
                recommendations.append(
                    f"Unblock {len(stalled)} stalled tasks to improve throughput"
                )
            if what_improved:
                recommendations.append(
                    f"Replicate practices from {len(what_improved)} high-performing tasks"
                )
            if not recommendations:
                recommendations.append("Sprint completed smoothly; maintain current practices")

            retro = {
                "what_improved": what_improved,
                "what_regressed": what_regressed,
                "stalled": stalled,
                "recommendations": recommendations,
            }

        rec_id = f"SR-{new_id(8)}"
        now = utcnow()
        await self._db.execute(
            "INSERT OR REPLACE INTO sprint_retrospectives "
            "(id, sprint_id, what_improved, what_regressed, stalled, recommendations, generated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                rec_id,
                sprint_id,
                json.dumps(retro["what_improved"]),
                json.dumps(retro["what_regressed"]),
                json.dumps(retro["stalled"]),
                json.dumps(retro["recommendations"]),
                now,
            ),
        )

        return {
            "id": rec_id,
            "sprint_id": sprint_id,
            **retro,
            "generated_at": now,
        }

    async def get_retro(self, sprint_id: str) -> dict | None:
        """Retrieve a sprint retrospective."""
        row = await self._db.execute_fetchone(
            "SELECT * FROM sprint_retrospectives WHERE sprint_id = ?",
            (sprint_id,),
        )
        if row:
            for key in ("what_improved", "what_regressed", "stalled", "recommendations"):
                if isinstance(row.get(key), str):
                    row[key] = json.loads(row[key])
        return row

    async def get_retros(self, limit: int = 10) -> list[dict]:
        """List sprint retrospectives."""
        rows = await self._db.execute_fetchall(
            "SELECT * FROM sprint_retrospectives ORDER BY generated_at DESC LIMIT ?",
            (limit,),
        )
        for row in rows:
            for key in ("what_improved", "what_regressed", "stalled", "recommendations"):
                if isinstance(row.get(key), str):
                    row[key] = json.loads(row[key])
        return rows
