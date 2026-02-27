"""Detect ambiguity in task descriptions and generate clarification questions."""

from __future__ import annotations

import re
import logging

logger = logging.getLogger(__name__)


class ClarificationDetector:
    """Detect ambiguity in task descriptions and suggest clarification questions."""

    VAGUE_TERMS = [
        "improve", "fix", "update", "handle", "manage", "better",
        "some", "various", "several", "appropriate", "proper",
        "etc", "and so on", "and more", "as needed",
    ]

    MISSING_CRITERIA_PATTERNS = [
        r"\bshould\b",  # "should work" without defining what "work" means
        r"\bmaybe\b",
        r"\bpossibly\b",
        r"\bsomehow\b",
    ]

    async def detect_ambiguity(self, title: str, description: str) -> dict:
        """Analyze a task for ambiguity. Returns score and detected issues."""
        issues = []
        full_text = f"{title} {description}".lower()
        score = 0.0

        # Check for vague terms
        for term in self.VAGUE_TERMS:
            if term in full_text:
                issues.append(f"Vague term: '{term}'")
                score += 0.1

        # Check for missing acceptance criteria
        has_criteria = any(
            kw in description.lower()
            for kw in ["acceptance criteria", "expected", "should return", "must", "assert", "verify that"]
        )
        if not has_criteria and len(description) > 50:
            issues.append("No clear acceptance criteria found")
            score += 0.3

        # Check for hedging language
        for pattern in self.MISSING_CRITERIA_PATTERNS:
            if re.search(pattern, full_text):
                issues.append(f"Hedging language detected: {pattern}")
                score += 0.1

        # Check description length
        if len(description) < 20:
            issues.append("Very short description — may lack detail")
            score += 0.2

        # Check for conflicting requirements
        has_both = ("add" in full_text and "remove" in full_text) or ("enable" in full_text and "disable" in full_text)
        if has_both:
            issues.append("Potentially conflicting requirements detected")
            score += 0.2

        score = min(1.0, score)
        return {
            "ambiguity_score": round(score, 2),
            "is_ambiguous": score >= 0.4,
            "issues": issues,
        }

    async def generate_questions(self, title: str, description: str) -> list[str]:
        """Generate clarification questions based on detected ambiguity."""
        result = await self.detect_ambiguity(title, description)
        questions = []

        if not result["is_ambiguous"]:
            return questions

        for issue in result["issues"]:
            if "Vague term" in issue:
                term = issue.split("'")[1]
                questions.append(f"What specifically does '{term}' mean in this context?")
            elif "acceptance criteria" in issue.lower():
                questions.append("What are the specific acceptance criteria for this task?")
            elif "short description" in issue.lower():
                questions.append("Can you provide more detail about what needs to be done?")
            elif "conflicting" in issue.lower():
                questions.append("There seem to be conflicting requirements — can you clarify the intended behavior?")

        return questions[:3]  # Cap at 3 questions
