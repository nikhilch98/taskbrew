"""Quality management: self-review, confidence scoring, code quality, and iterative refinement."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Confidence indicators
LOW_CONFIDENCE_PHRASES = [
    "i'm not sure", "i think", "maybe", "perhaps", "might",
    "could be", "not certain", "unclear", "i believe",
    "approximately", "roughly", "possibly", "probably",
]

HIGH_CONFIDENCE_PHRASES = [
    "verified", "confirmed", "tested", "all tests pass",
    "successfully", "correct", "implemented", "complete",
]


class QualityManager:
    """Track and enforce quality metrics for agent outputs."""

    def __init__(self, db, memory_manager=None) -> None:
        self._db = db
        self._memory_manager = memory_manager

    async def record_score(
        self,
        task_id: str,
        agent_id: str,
        score_type: str,
        score: float,
        details: dict | None = None,
    ) -> dict:
        """Record a quality score for a task."""
        now = datetime.now(timezone.utc).isoformat()
        details_json = json.dumps(details) if details else None
        await self._db.execute(
            "INSERT INTO quality_scores (task_id, agent_id, score_type, score, details, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (task_id, agent_id, score_type, score, details_json, now),
        )
        return {
            "task_id": task_id,
            "agent_id": agent_id,
            "score_type": score_type,
            "score": score,
            "created_at": now,
        }

    async def extract_self_review(self, task_id: str, agent_id: str, output: str) -> dict:
        """Parse agent output for self-review quality signals.

        Feature 41: Analyze the output for indicators of quality:
        - Mentions of testing
        - Code review patterns
        - Error handling mentions
        - Documentation references
        """
        signals = {
            "mentions_testing": bool(re.search(r'\b(test|pytest|unittest|assert)\b', output, re.I)),
            "mentions_error_handling": bool(re.search(r'\b(try|except|error handling|catch)\b', output, re.I)),
            "mentions_documentation": bool(re.search(r'\b(docstring|comment|readme|docs)\b', output, re.I)),
            "mentions_review": bool(re.search(r'\b(review|check|verify|validate)\b', output, re.I)),
            "has_code_blocks": "```" in output,
            "output_length": len(output),
        }

        # Calculate quality score based on signals
        score = 0.5  # baseline
        if signals["mentions_testing"]:
            score += 0.15
        if signals["mentions_error_handling"]:
            score += 0.1
        if signals["mentions_documentation"]:
            score += 0.05
        if signals["mentions_review"]:
            score += 0.1
        if signals["has_code_blocks"]:
            score += 0.1

        score = min(1.0, score)

        await self.record_score(task_id, agent_id, "self_review", score, signals)

        return {"score": score, "signals": signals}

    async def score_confidence(self, task_id: str, agent_id: str, output: str) -> float:
        """Feature 45: Score agent confidence based on output language analysis."""
        output_lower = output.lower()

        low_count = sum(1 for phrase in LOW_CONFIDENCE_PHRASES if phrase in output_lower)
        high_count = sum(1 for phrase in HIGH_CONFIDENCE_PHRASES if phrase in output_lower)

        # Base confidence
        confidence = 0.7

        # Adjust based on phrase counts
        confidence -= 0.05 * low_count
        confidence += 0.05 * high_count

        # Clamp to [0.1, 1.0]
        confidence = max(0.1, min(1.0, confidence))

        details = {
            "low_confidence_count": low_count,
            "high_confidence_count": high_count,
        }

        await self.record_score(task_id, agent_id, "confidence", confidence, details)

        return confidence

    async def score_code_quality(self, task_id: str, agent_id: str, output: str) -> dict:
        """Feature 44: Score code quality based on output analysis."""
        checks = {
            "has_imports": bool(re.search(r'^(import |from .+ import )', output, re.M)),
            "has_functions": bool(re.search(r'^\s*(def |async def )', output, re.M)),
            "has_classes": bool(re.search(r'^\s*class ', output, re.M)),
            "has_type_hints": bool(re.search(r':\s*(str|int|float|bool|list|dict|None)', output)),
            "has_error_handling": bool(re.search(r'try:|except ', output)),
            "no_print_debugging": "print(" not in output and "print(f" not in output,
            "has_docstrings": '"""' in output or "'''" in output,
        }

        passed = sum(1 for v in checks.values() if v)
        total = len(checks)
        score = passed / total if total > 0 else 0.5

        await self.record_score(task_id, agent_id, "code_quality", score, checks)

        return {"score": score, "checks": checks, "passed": passed, "total": total}

    async def check_regression(self, task_id: str, project_dir: str | None = None) -> dict:
        """Feature 43: Check for potential regressions.

        Compares with previous quality scores for the same task type.
        """
        task = await self._db.execute_fetchone("SELECT * FROM tasks WHERE id = ?", (task_id,))
        if not task:
            return {"error": "Task not found"}

        task_type = task.get("task_type", "general")

        # Get average score for this task type from history
        avg = await self._db.execute_fetchone(
            "SELECT AVG(qs.score) as avg_score, COUNT(*) as sample_size "
            "FROM quality_scores qs JOIN tasks t ON qs.task_id = t.id "
            "WHERE t.task_type = ? AND qs.score_type = 'self_review'",
            (task_type,),
        )

        # Get current task's score
        current = await self._db.execute_fetchone(
            "SELECT score FROM quality_scores WHERE task_id = ? AND score_type = 'self_review' "
            "ORDER BY created_at DESC LIMIT 1",
            (task_id,),
        )

        result = {
            "task_type": task_type,
            "current_score": current["score"] if current else None,
            "historical_avg": round(avg["avg_score"], 3) if avg and avg["avg_score"] else None,
            "sample_size": avg["sample_size"] if avg else 0,
            "regression_detected": False,
        }

        if current and avg and avg["avg_score"]:
            if current["score"] < avg["avg_score"] - 0.15:
                result["regression_detected"] = True
                result["regression_delta"] = round(current["score"] - avg["avg_score"], 3)

        return result

    async def should_iterate(self, task_id: str, threshold: float = 0.6) -> bool:
        """Feature 46: Determine if a task should undergo iterative refinement.

        Returns True if the latest quality score is below the threshold.
        """
        latest = await self._db.execute_fetchone(
            "SELECT score FROM quality_scores WHERE task_id = ? "
            "ORDER BY created_at DESC LIMIT 1",
            (task_id,),
        )
        if not latest:
            return False
        return latest["score"] < threshold

    async def get_scores(self, task_id: str | None = None, score_type: str | None = None, limit: int = 50) -> list[dict]:
        """Get quality scores with optional filters."""
        conditions = []
        params: list = []
        if task_id:
            conditions.append("task_id = ?")
            params.append(task_id)
        if score_type:
            conditions.append("score_type = ?")
            params.append(score_type)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        params.append(limit)
        rows = await self._db.execute_fetchall(
            f"SELECT * FROM quality_scores {where} ORDER BY created_at DESC LIMIT ?",
            tuple(params),
        )
        # Parse JSON details
        for row in rows:
            if isinstance(row.get("details"), str):
                try:
                    row["details"] = json.loads(row["details"])
                except (json.JSONDecodeError, TypeError):
                    pass
        return rows

    async def get_task_quality_summary(self, task_id: str) -> dict:
        """Get a summary of all quality scores for a task."""
        scores = await self.get_scores(task_id=task_id)
        summary = {}
        for s in scores:
            summary[s["score_type"]] = {
                "score": s["score"],
                "agent_id": s["agent_id"],
                "created_at": s["created_at"],
                "details": s.get("details"),
            }
        return {"task_id": task_id, "scores": summary}
