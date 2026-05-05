"""Async system-agent gates for backlog intake and completed-task review."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict, dataclass, field
from typing import Any

from taskbrew.agents.base import AgentRunner
from taskbrew.config import AgentConfig
from taskbrew.orchestrator.task_board import BACKLOG_STATUS, REVIEW_STATUS, TaskBoard

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BacklogIntakeResult:
    """System-agent decision for one backlog task."""

    needs_review: bool
    reason: str
    signals: list[str] = field(default_factory=list)
    confidence: str = "medium"


@dataclass(frozen=True)
class RevisionRequest:
    """A requested follow-up task from the review gate."""

    title: str | None = None
    description: str | None = None
    assigned_to: str | None = None
    task_type: str | None = None
    priority: str | None = None


@dataclass(frozen=True)
class ReviewResult:
    """System-agent outcome for a task waiting at the review gate."""

    outcome: str
    reason: str
    revisions: list[RevisionRequest] = field(default_factory=list)


class SystemGateAnalysisError(Exception):
    """Raised when system-gate agent output cannot be trusted."""


class SystemGateAnalyzer:
    """Analyzer interface used by :class:`SystemGateManager`."""

    async def decide_needs_review(self, context: dict) -> BacklogIntakeResult:
        raise NotImplementedError

    async def review_completed_task(self, context: dict) -> ReviewResult:
        raise NotImplementedError


def _extract_json_object(text: str) -> dict[str, Any]:
    """Extract and parse the JSON object embedded in an agent response."""
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise SystemGateAnalysisError("Agent response did not contain a JSON object")

    raw = text[start : end + 1]
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemGateAnalysisError(f"Agent response JSON was invalid: {exc}") from exc
    if not isinstance(parsed, dict):
        raise SystemGateAnalysisError("Agent response JSON must be an object")
    return parsed


class AgentRunnerSystemGateAnalyzer(SystemGateAnalyzer):
    """Production analyzer backed by the locked TaskBrew system agent."""

    _REVIEW_OUTCOMES = {"approved", "needs_revision", "rejected", "failed_review"}

    def __init__(
        self,
        config: AgentConfig,
        *,
        event_bus=None,
        project_dir: str | None = None,
    ) -> None:
        self._runner = AgentRunner(config, event_bus=event_bus)
        self._project_dir = project_dir or (str(config.cwd) if config.cwd else None)

    async def decide_needs_review(self, context: dict) -> BacklogIntakeResult:
        prompt = (
            "You are running TaskBrew backlog intake for exactly one task.\n"
            "Decide only whether the task should require system review after completion.\n"
            "Do not rewrite, split, reprioritize, or modify the task.\n"
            "Return only strict JSON with this shape:\n"
            '{"needs_review": true, "reason": "string", '
            '"signals": ["string"], "confidence": "low|medium|high"}\n\n'
            f"Context:\n{json.dumps(context, default=str, indent=2)}"
        )
        text = await self._runner.run(prompt=prompt, cwd=self._project_dir)
        data = _extract_json_object(text)
        return self._parse_backlog_result(data)

    async def review_completed_task(self, context: dict) -> ReviewResult:
        prompt = (
            "You are running TaskBrew review gate analysis for exactly one completed task.\n"
            "Choose exactly one outcome: approved, needs_revision, rejected, failed_review.\n"
            "Use rejected only when the task should not continue; fixable work must become "
            "revision tasks.\n"
            "Return only strict JSON with this shape:\n"
            '{"outcome": "approved|needs_revision|rejected|failed_review", '
            '"reason": "string", "revisions": [{"title": "string", '
            '"description": "string", "assigned_to": "role", '
            '"task_type": "revision", "priority": "medium"}]}\n\n'
            f"Context:\n{json.dumps(context, default=str, indent=2)}"
        )
        text = await self._runner.run(prompt=prompt, cwd=self._project_dir)
        data = _extract_json_object(text)
        return self._parse_review_result(data)

    def _parse_backlog_result(self, data: dict[str, Any]) -> BacklogIntakeResult:
        needs_review = data.get("needs_review")
        reason = data.get("reason")
        if not isinstance(needs_review, bool):
            raise SystemGateAnalysisError("needs_review must be a boolean")
        if not isinstance(reason, str) or not reason.strip():
            raise SystemGateAnalysisError("reason must be a non-empty string")

        signals = data.get("signals", [])
        if not isinstance(signals, list) or not all(
            isinstance(signal, str) for signal in signals
        ):
            raise SystemGateAnalysisError("signals must be a list of strings")

        confidence = data.get("confidence", "medium")
        if confidence not in {"low", "medium", "high"}:
            raise SystemGateAnalysisError("confidence must be low, medium, or high")

        return BacklogIntakeResult(
            needs_review=needs_review,
            reason=reason.strip(),
            signals=signals,
            confidence=confidence,
        )

    def _parse_review_result(self, data: dict[str, Any]) -> ReviewResult:
        outcome = data.get("outcome")
        reason = data.get("reason")
        if outcome not in self._REVIEW_OUTCOMES:
            raise SystemGateAnalysisError(
                "outcome must be approved, needs_revision, rejected, or failed_review"
            )
        if not isinstance(reason, str) or not reason.strip():
            raise SystemGateAnalysisError("reason must be a non-empty string")

        raw_revisions = data.get("revisions", [])
        if raw_revisions is None:
            raw_revisions = []
        if not isinstance(raw_revisions, list):
            raise SystemGateAnalysisError("revisions must be a list")

        revisions: list[RevisionRequest] = []
        for raw in raw_revisions:
            if not isinstance(raw, dict):
                raise SystemGateAnalysisError("each revision must be an object")
            revisions.append(
                RevisionRequest(
                    title=self._optional_string(raw, "title"),
                    description=self._optional_string(raw, "description"),
                    assigned_to=self._optional_string(raw, "assigned_to"),
                    task_type=self._optional_string(raw, "task_type"),
                    priority=self._optional_string(raw, "priority"),
                )
            )
        return ReviewResult(outcome=outcome, reason=reason.strip(), revisions=revisions)

    def _optional_string(self, data: dict[str, Any], key: str) -> str | None:
        value = data.get(key)
        if value is None:
            return None
        if not isinstance(value, str):
            raise SystemGateAnalysisError(f"{key} must be a string")
        stripped = value.strip()
        return stripped or None


class SystemGateManager:
    """Poll and process TaskBrew system-gate work."""

    def __init__(
        self,
        *,
        board: TaskBoard,
        analyzer: SystemGateAnalyzer,
        interval_seconds: float = 5.0,
        batch_size: int = 10,
    ) -> None:
        self._board = board
        self._analyzer = analyzer
        self._interval_seconds = interval_seconds
        self._batch_size = batch_size
        self._stop_requested = False

    async def _backlog_context(self, task: dict) -> dict:
        return {
            "task": task,
            "dependencies": await self._dependencies(task["id"]),
            "nearby_tasks": await self._nearby_tasks(task),
        }

    async def _review_context(self, task: dict) -> dict:
        return {
            "task": task,
            "dependencies": await self._dependencies(task["id"]),
            "revisions": await self._revisions(task["id"]),
            "needs_review_reason": task.get("needs_review_reason"),
            "output_text": task.get("output_text") or "",
        }

    async def process_backlog_task(self, task_id: str) -> bool:
        task = await self._board.get_task(task_id)
        if task is None or task["status"] != BACKLOG_STATUS:
            return False

        rows = await self._board._db.execute_returning(
            "UPDATE tasks SET backlog_intake_status = 'running' "
            "WHERE id = ? AND status = 'backlog' "
            "AND COALESCE(backlog_intake_status, 'pending') IN ('pending', 'failed') "
            "RETURNING *",
            (task_id,),
        )
        if not rows:
            return False

        running_task = rows[0]
        try:
            result = await self._analyzer.decide_needs_review(
                await self._backlog_context(running_task)
            )
            await self._board.apply_backlog_intake_decision(
                task_id,
                needs_review=result.needs_review,
                reason=result.reason,
                signals=result.signals,
                confidence=result.confidence,
            )
        except Exception as exc:
            logger.exception("Backlog intake failed for task %s", task_id)
            await self._board.mark_backlog_intake_failed(task_id, str(exc))
            return False
        return True

    async def process_review_task(self, task_id: str) -> bool:
        task = await self._board.get_task(task_id)
        if task is None or task["status"] != REVIEW_STATUS:
            return False
        if await self._board._has_unresolved_dependencies(task_id):
            return False

        rows = await self._board._db.execute_returning(
            "UPDATE tasks SET review_status = 'running' "
            "WHERE id = ? AND status = 'review' "
            "AND COALESCE(review_status, 'pending') IN ('pending', 'failed') "
            "AND NOT EXISTS ("
            "  SELECT 1 FROM task_dependencies "
            "  WHERE task_id = ? AND resolved = 0"
            ") RETURNING *",
            (task_id, task_id),
        )
        if not rows:
            return False

        running_task = rows[0]
        try:
            result = await self._analyzer.review_completed_task(
                await self._review_context(running_task)
            )
            await self._handle_review_result(task_id, result)
        except Exception as exc:
            logger.exception("Review gate failed for task %s", task_id)
            await self._board.mark_review_failed(task_id, str(exc))
            return False
        return True

    async def process_pending_once(self) -> dict[str, int]:
        counts = {"backlog": 0, "review": 0}
        if self._batch_size <= 0:
            return counts

        backlog_rows = await self._board._db.execute_fetchall(
            "SELECT id FROM tasks WHERE status = 'backlog' "
            "AND COALESCE(backlog_intake_status, 'pending') IN ('pending', 'failed') "
            "ORDER BY created_at LIMIT ?",
            (self._batch_size,),
        )
        for row in backlog_rows:
            if await self.process_backlog_task(row["id"]):
                counts["backlog"] += 1

        remaining = self._batch_size - counts["backlog"]
        if remaining <= 0:
            return counts

        review_rows = await self._board._db.execute_fetchall(
            "SELECT id FROM tasks WHERE status = 'review' "
            "AND COALESCE(review_status, 'pending') IN ('pending', 'failed') "
            "AND NOT EXISTS ("
            "  SELECT 1 FROM task_dependencies "
            "  WHERE task_id = tasks.id AND resolved = 0"
            ") "
            "ORDER BY created_at LIMIT ?",
            (remaining,),
        )
        for row in review_rows:
            if await self.process_review_task(row["id"]):
                counts["review"] += 1

        return counts

    async def run(self) -> None:
        self._stop_requested = False
        while not self._stop_requested:
            await self.process_pending_once()
            await asyncio.sleep(self._interval_seconds)

    def stop(self) -> None:
        self._stop_requested = True

    async def _handle_review_result(self, task_id: str, result: ReviewResult) -> None:
        outcome = result.outcome
        if outcome == "approved":
            await self._restore_pending_review_status(task_id)
            await self._board.approve_review_gate(task_id, reason=result.reason)
        elif outcome == "rejected":
            await self._board.reject_review_gate(task_id, reason=result.reason)
        elif outcome == "needs_revision":
            revisions = result.revisions or [
                RevisionRequest(description=result.reason)
            ]
            await self._board.create_review_revision_tasks(
                task_id,
                [self._revision_to_dict(revision) for revision in revisions],
            )
        elif outcome == "failed_review":
            await self._board.mark_review_failed(task_id, result.reason)
        else:
            raise SystemGateAnalysisError(f"Unsupported review outcome: {outcome}")

    async def _restore_pending_review_status(self, task_id: str) -> None:
        await self._board._db.execute(
            "UPDATE tasks SET review_status = 'pending' "
            "WHERE id = ? AND status = 'review' AND review_status = 'running'",
            (task_id,),
        )

    def _revision_to_dict(self, revision: RevisionRequest) -> dict:
        return {
            key: value
            for key, value in asdict(revision).items()
            if value not in (None, "")
        }

    async def _dependencies(self, task_id: str) -> list[dict]:
        return await self._board._db.execute_fetchall(
            "SELECT d.blocked_by, d.resolved, d.resolved_at, "
            "t.title, t.status, t.assigned_to "
            "FROM task_dependencies d "
            "LEFT JOIN tasks t ON t.id = d.blocked_by "
            "WHERE d.task_id = ? ORDER BY d.blocked_by",
            (task_id,),
        )

    async def _nearby_tasks(self, task: dict) -> list[dict]:
        group_id = task.get("group_id")
        if not group_id:
            return []
        return await self._board._db.execute_fetchall(
            "SELECT id, title, status, task_type, assigned_to, priority "
            "FROM tasks WHERE group_id = ? AND id != ? "
            "ORDER BY created_at LIMIT 8",
            (group_id, task["id"]),
        )

    async def _revisions(self, task_id: str) -> list[dict]:
        return await self._board._db.execute_fetchall(
            "SELECT id, title, status, task_type, assigned_to, priority, "
            "description, output_text, rejection_reason "
            "FROM tasks "
            "WHERE review_parent_task_id = ? OR revision_of = ? "
            "ORDER BY created_at",
            (task_id, task_id),
        )
