"""Async system-agent gates for backlog intake and completed-task review."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from time import monotonic
from typing import Any

from taskbrew.agents.base import AgentRunner
from taskbrew.config import AgentConfig
from taskbrew.orchestrator.task_board import BACKLOG_STATUS, REVIEW_STATUS, TaskBoard

logger = logging.getLogger(__name__)
_GATE_LOCKS: dict[tuple[str, str], tuple[asyncio.AbstractEventLoop, asyncio.Lock]] = {}
_GATE_RECENT_ATTEMPTS: dict[
    tuple[str, str], tuple[asyncio.AbstractEventLoop, float]
] = {}


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


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
    for match in re.finditer(r"```json\s*(.*?)```", text, flags=re.IGNORECASE | re.DOTALL):
        try:
            parsed = json.loads(match.group(1).strip())
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed

    decoder = json.JSONDecoder()
    last_object: dict[str, Any] | None = None
    saw_object_start = False
    for index, char in enumerate(text):
        if char != "{":
            continue
        saw_object_start = True
        try:
            parsed, _ = decoder.raw_decode(text, index)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            last_object = parsed

    if last_object is not None:
        return last_object

    stripped = text.strip()
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError as exc:
        if saw_object_start:
            raise SystemGateAnalysisError(
                f"Agent response JSON was invalid: {exc}"
            ) from exc
        raise SystemGateAnalysisError(
            "Agent response did not contain a JSON object"
        ) from exc
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
        running_timeout_seconds: float = 300.0,
        retry_cooldown_seconds: float = 1.0,
    ) -> None:
        self._board = board
        self._analyzer = analyzer
        self._interval_seconds = interval_seconds
        self._batch_size = batch_size
        self._running_timeout_seconds = running_timeout_seconds
        self._retry_cooldown_seconds = retry_cooldown_seconds
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
        async with self._gate_lock("backlog", task_id):
            return await self._process_backlog_task(task_id)

    async def _process_backlog_task(self, task_id: str) -> bool:
        task = await self._board.get_task(task_id)
        if task is None or task["status"] != BACKLOG_STATUS:
            return False
        backlog_status = task.get("backlog_intake_status") or "pending"
        if backlog_status == "failed" and self._recent_attempt_blocks_retry(
            "backlog", task_id
        ):
            return False
        include_running = (
            backlog_status == "running" and self._is_running_stale(task, "backlog")
        )
        if backlog_status == "running" and not include_running:
            return False
        status_predicate = (
            "IN ('pending', 'failed', 'running')"
            if include_running
            else "IN ('pending', 'failed')"
        )

        rows = await self._board._db.execute_returning(
            "UPDATE tasks SET backlog_intake_status = 'running' "
            "WHERE id = ? AND status = 'backlog' "
            f"AND COALESCE(backlog_intake_status, 'pending') {status_predicate} "
            "RETURNING *",
            (task_id,),
        )
        if not rows:
            return False

        self._mark_gate_attempt("backlog", task_id)
        await self._append_running_gate_run(task_id, "backlog")
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
        except asyncio.CancelledError as exc:
            await self._board.mark_backlog_intake_failed(task_id, str(exc))
            raise
        except Exception as exc:
            logger.exception("Backlog intake failed for task %s", task_id)
            await self._board.mark_backlog_intake_failed(task_id, str(exc))
            return False
        finally:
            self._mark_gate_attempt("backlog", task_id)
        return True

    async def process_review_task(self, task_id: str) -> bool:
        async with self._gate_lock("review", task_id):
            return await self._process_review_task(task_id)

    async def _process_review_task(self, task_id: str) -> bool:
        task = await self._board.get_task(task_id)
        if task is None or task["status"] != REVIEW_STATUS:
            return False
        if await self._board._has_unresolved_dependencies(task_id):
            return False
        review_status = task.get("review_status") or "pending"
        if review_status == "failed" and self._recent_attempt_blocks_retry(
            "review", task_id
        ):
            return False
        include_running = (
            review_status == "running" and self._is_running_stale(task, "review")
        )
        if review_status == "running" and not include_running:
            return False
        status_predicate = (
            "IN ('pending', 'failed', 'running')"
            if include_running
            else "IN ('pending', 'failed')"
        )

        rows = await self._board._db.execute_returning(
            "UPDATE tasks SET review_status = 'running' "
            "WHERE id = ? AND status = 'review' "
            f"AND COALESCE(review_status, 'pending') {status_predicate} "
            "AND NOT EXISTS ("
            "  SELECT 1 FROM task_dependencies "
            "  WHERE task_id = ? AND resolved = 0"
            ") RETURNING *",
            (task_id, task_id),
        )
        if not rows:
            return False

        self._mark_gate_attempt("review", task_id)
        await self._append_running_gate_run(task_id, "review")
        running_task = rows[0]
        try:
            result = await self._analyzer.review_completed_task(
                await self._review_context(running_task)
            )
            return await self._handle_review_result(task_id, result)
        except asyncio.CancelledError as exc:
            await self._board.mark_review_failed(task_id, str(exc))
            raise
        except Exception as exc:
            logger.exception("Review gate failed for task %s", task_id)
            await self._board.mark_review_failed(task_id, str(exc))
            return False
        finally:
            self._mark_gate_attempt("review", task_id)
        return True

    async def process_pending_once(self) -> dict[str, int]:
        counts = {"backlog": 0, "review": 0}
        if self._batch_size <= 0:
            return counts

        backlog_rows = await self._board._db.execute_fetchall(
            "SELECT id, backlog_intake_status, system_gate_runs "
            "FROM tasks WHERE status = 'backlog' "
            "AND COALESCE(backlog_intake_status, 'pending') IN ('pending', 'failed') "
            "ORDER BY created_at LIMIT ?",
            (self._batch_size,),
        )
        for row in backlog_rows:
            if await self.process_backlog_task(row["id"]):
                counts["backlog"] += 1
            if counts["backlog"] >= self._batch_size:
                break

        if counts["backlog"] < self._batch_size:
            backlog_running_rows = await self._board._db.execute_fetchall(
                "SELECT id, backlog_intake_status, system_gate_runs "
                "FROM tasks WHERE status = 'backlog' "
                "AND backlog_intake_status = 'running' "
                "ORDER BY created_at",
            )
            for row in backlog_running_rows:
                if not self._is_running_stale(row, "backlog"):
                    continue
                if await self.process_backlog_task(row["id"]):
                    counts["backlog"] += 1
                if counts["backlog"] >= self._batch_size:
                    break

        remaining = self._batch_size - counts["backlog"]
        if remaining <= 0:
            return counts

        review_rows = await self._board._db.execute_fetchall(
            "SELECT id, review_status, system_gate_runs "
            "FROM tasks WHERE status = 'review' "
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
            if counts["review"] >= remaining:
                break

        if counts["review"] < remaining:
            review_running_rows = await self._board._db.execute_fetchall(
                "SELECT id, review_status, system_gate_runs "
                "FROM tasks WHERE status = 'review' "
                "AND review_status = 'running' "
                "AND NOT EXISTS ("
                "  SELECT 1 FROM task_dependencies "
                "  WHERE task_id = tasks.id AND resolved = 0"
                ") "
                "ORDER BY created_at",
            )
            for row in review_running_rows:
                if not self._is_running_stale(row, "review"):
                    continue
                if await self.process_review_task(row["id"]):
                    counts["review"] += 1
                if counts["review"] >= remaining:
                    break

        return counts

    async def run(self) -> None:
        self._stop_requested = False
        while not self._stop_requested:
            await self.process_pending_once()
            await asyncio.sleep(self._interval_seconds)

    def stop(self) -> None:
        self._stop_requested = True

    async def _handle_review_result(self, task_id: str, result: ReviewResult) -> bool:
        outcome = result.outcome
        if outcome == "approved":
            await self._restore_pending_review_status(task_id)
            await self._board.approve_review_gate(task_id, reason=result.reason)
            return True
        elif outcome == "rejected":
            await self._board.reject_review_gate(task_id, reason=result.reason)
            return True
        elif outcome == "needs_revision":
            if not await self._review_gate_still_running(task_id):
                return False
            revisions = result.revisions or [
                RevisionRequest(description=result.reason)
            ]
            created = await self._board.create_review_revision_tasks(
                task_id,
                [self._revision_to_dict(revision) for revision in revisions],
            )
            return bool(created)
        elif outcome == "failed_review":
            await self._board.mark_review_failed(task_id, result.reason)
            return True
        else:
            raise SystemGateAnalysisError(f"Unsupported review outcome: {outcome}")

    async def _restore_pending_review_status(self, task_id: str) -> None:
        await self._board._db.execute(
            "UPDATE tasks SET review_status = 'pending' "
            "WHERE id = ? AND status = 'review' AND review_status = 'running' "
            "AND NOT EXISTS ("
            "  SELECT 1 FROM task_dependencies "
            "  WHERE task_id = ? AND resolved = 0"
            ")",
            (task_id, task_id),
        )

    async def _review_gate_still_running(self, task_id: str) -> bool:
        row = await self._board._db.execute_fetchone(
            "SELECT 1 FROM tasks WHERE id = ? AND status = 'review' "
            "AND review_status = 'running' "
            "AND NOT EXISTS ("
            "  SELECT 1 FROM task_dependencies "
            "  WHERE task_id = ? AND resolved = 0"
            ") LIMIT 1",
            (task_id, task_id),
        )
        return row is not None

    async def _append_running_gate_run(self, task_id: str, gate: str) -> None:
        await self._board._append_system_gate_run(
            task_id,
            {
                "gate": gate,
                "outcome": "running",
                "started_at": _utcnow(),
            },
        )

    def _gate_lock(self, gate: str, task_id: str) -> asyncio.Lock:
        key = (gate, task_id)
        loop = asyncio.get_running_loop()
        entry = _GATE_LOCKS.get(key)
        if entry is None or entry[0] is not loop:
            lock = asyncio.Lock()
            _GATE_LOCKS[key] = (loop, lock)
            return lock
        return entry[1]

    def _mark_gate_attempt(self, gate: str, task_id: str) -> None:
        _GATE_RECENT_ATTEMPTS[(gate, task_id)] = (
            asyncio.get_running_loop(),
            monotonic(),
        )

    def _recent_attempt_blocks_retry(self, gate: str, task_id: str) -> bool:
        if self._retry_cooldown_seconds <= 0:
            return False
        entry = _GATE_RECENT_ATTEMPTS.get((gate, task_id))
        if entry is None:
            return False
        loop, attempted_at = entry
        if loop is not asyncio.get_running_loop():
            return False
        return monotonic() - attempted_at < self._retry_cooldown_seconds

    def _is_running_stale(self, task: dict, gate: str) -> bool:
        if self._running_timeout_seconds <= 0:
            return True
        started_at = self._latest_running_started_at(task, gate)
        if started_at is None:
            return True
        return (
            datetime.now(timezone.utc) - started_at
        ).total_seconds() >= self._running_timeout_seconds

    def _latest_running_started_at(self, task: dict, gate: str) -> datetime | None:
        runs = self._board._json_list(task.get("system_gate_runs"))
        for run in reversed(runs):
            if run.get("gate") != gate or run.get("outcome") != "running":
                continue
            started_at = run.get("started_at")
            if not isinstance(started_at, str):
                return None
            try:
                parsed = datetime.fromisoformat(started_at)
            except ValueError:
                return None
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                return None
            return parsed
        return None

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
