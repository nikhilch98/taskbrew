"""Learn from code review feedback patterns."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class ReviewLearningManager:
    """Extract and track patterns from code review feedback.

    Uses the ``review_feedback`` table.
    """

    def __init__(self, db) -> None:
        self._db = db

    async def extract_feedback(
        self, task_id: str, reviewer: str, output: str
    ) -> list[dict]:
        """Extract feedback patterns from review output text.

        Looks for common review patterns like:
        - Missing tests
        - Code style issues
        - Error handling gaps
        - Documentation needs
        - Security concerns
        """
        now = datetime.now(timezone.utc).isoformat()
        patterns_found: list[dict] = []

        # Define feedback patterns to detect
        PATTERN_RULES = [
            ("missing_tests", ["no test", "missing test", "add test", "needs test", "without test", "untested"]),
            ("error_handling", ["error handling", "try/except", "exception", "unhandled", "no error"]),
            ("documentation", ["no docstring", "missing doc", "needs doc", "undocumented", "add comment"]),
            ("code_style", ["naming", "style", "formatting", "convention", "pep8", "lint"]),
            ("security", ["security", "injection", "sanitize", "validate input", "xss", "sql injection"]),
            ("performance", ["performance", "slow", "optimize", "efficient", "complexity", "O(n"]),
            ("duplication", ["duplicate", "duplicat", "DRY", "repeated", "copy-paste"]),
            ("type_safety", ["type hint", "type annotation", "typing", "Any type", "untyped"]),
        ]

        output_lower = output.lower()
        for feedback_type, keywords in PATTERN_RULES:
            if any(kw in output_lower for kw in keywords):
                # Check if this pattern already exists for this reviewer
                existing = await self._db.execute_fetchone(
                    "SELECT id, frequency FROM review_feedback "
                    "WHERE reviewer = ? AND feedback_type = ? AND pattern = ?",
                    (reviewer, feedback_type, feedback_type),
                )

                if existing:
                    await self._db.execute(
                        "UPDATE review_feedback SET frequency = frequency + 1 WHERE id = ?",
                        (existing["id"],),
                    )
                    freq = existing["frequency"] + 1
                else:
                    await self._db.execute(
                        "INSERT INTO review_feedback (task_id, reviewer, feedback_type, pattern, frequency, created_at) "
                        "VALUES (?, ?, ?, ?, 1, ?)",
                        (task_id, reviewer, feedback_type, feedback_type, now),
                    )
                    freq = 1

                patterns_found.append({
                    "feedback_type": feedback_type,
                    "frequency": freq,
                })

        return patterns_found

    async def get_top_patterns(
        self, reviewer: str | None = None, limit: int = 10
    ) -> list[dict]:
        """Get the most common feedback patterns, optionally filtered by reviewer."""
        if reviewer:
            rows = await self._db.execute_fetchall(
                "SELECT feedback_type, pattern, SUM(frequency) as total_frequency, reviewer "
                "FROM review_feedback WHERE reviewer = ? "
                "GROUP BY feedback_type, pattern ORDER BY total_frequency DESC LIMIT ?",
                (reviewer, limit),
            )
        else:
            rows = await self._db.execute_fetchall(
                "SELECT feedback_type, pattern, SUM(frequency) as total_frequency "
                "FROM review_feedback "
                "GROUP BY feedback_type, pattern ORDER BY total_frequency DESC LIMIT ?",
                (limit,),
            )
        return rows

    async def get_feedback_for_context(
        self, reviewer: str, task_type: str | None = None
    ) -> str:
        """Generate a context string of common feedback patterns for a reviewer.

        This can be injected into an agent's context to help them avoid
        commonly flagged issues.
        """
        patterns = await self.get_top_patterns(reviewer=reviewer, limit=5)

        if not patterns:
            return ""

        lines = ["Common review feedback patterns to watch for:"]
        for p in patterns:
            freq = p.get("total_frequency", p.get("frequency", 0))
            lines.append(f"- {p['feedback_type']} (flagged {freq} times)")

        return "\n".join(lines)

    async def get_reviewer_stats(self, reviewer: str) -> dict:
        """Get statistics about a reviewer's feedback patterns."""
        total = await self._db.execute_fetchone(
            "SELECT COUNT(*) as count, SUM(frequency) as total_reviews "
            "FROM review_feedback WHERE reviewer = ?",
            (reviewer,),
        )
        patterns = await self.get_top_patterns(reviewer=reviewer)

        return {
            "reviewer": reviewer,
            "unique_patterns": total["count"] if total else 0,
            "total_feedback_instances": total["total_reviews"] if total and total["total_reviews"] else 0,
            "top_patterns": patterns,
        }
