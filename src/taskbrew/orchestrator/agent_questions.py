"""AgentQuestionManager: persistence + asyncio coordination for the
structured ``ask_question`` MCP tool.

In auto mode the manager records the agent's preferred answer and
returns immediately. In manual mode it sets ``tasks.awaiting_input_since``
on the task row, blocks on a per-question ``asyncio.Event`` until
either the user answers via the dashboard or the task is cancelled,
then clears the pause column and returns the resolved row.

Events are in-memory (per-process). On server restart the question
row stays in 'pending' status; the agent's blocked call is gone
with its process. The next agent that re-claims the task is
responsible for re-issuing the question if it still wants the
answer.

Design:
docs/superpowers/specs/2026-04-25-agent-questions-design.md
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_MAX_QUESTION_LEN = 2000
_MAX_REASONING_LEN = 4000
_MAX_OPTION_LEN = 500
_MAX_OPTIONS = 10
_MIN_OPTIONS = 2


class AgentQuestionManager:
    def __init__(self, db, event_bus=None) -> None:
        self._db = db
        self._event_bus = event_bus
        # Per-pending-question wake events. Key = question_id.
        # Value = (asyncio.Event, slot dict). The slot is filled by
        # the answer / cancel path before set() is called so the
        # waiter reads it on wake.
        self._waiters: dict[str, tuple[asyncio.Event, dict]] = {}

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _new_id() -> str:
        return f"qst-{uuid.uuid4().hex[:12]}"

    def _validate(
        self,
        *,
        question: str,
        options: list,
        preferred_answer: str,
        reasoning: str,
    ) -> None:
        if not isinstance(question, str) or not question.strip():
            raise ValueError("question must be a non-empty string")
        if len(question) > _MAX_QUESTION_LEN:
            raise ValueError(
                f"question exceeds {_MAX_QUESTION_LEN} chars"
            )
        if not isinstance(reasoning, str) or not reasoning.strip():
            raise ValueError("reasoning must be a non-empty string")
        if len(reasoning) > _MAX_REASONING_LEN:
            raise ValueError(
                f"reasoning exceeds {_MAX_REASONING_LEN} chars"
            )
        if not isinstance(options, list):
            raise ValueError("options must be a list")
        if not (_MIN_OPTIONS <= len(options) <= _MAX_OPTIONS):
            raise ValueError(
                f"options must contain {_MIN_OPTIONS}-{_MAX_OPTIONS} entries"
            )
        seen = set()
        for o in options:
            if not isinstance(o, str) or not o.strip():
                raise ValueError("each option must be a non-empty string")
            if len(o) > _MAX_OPTION_LEN:
                raise ValueError(
                    f"option exceeds {_MAX_OPTION_LEN} chars: {o[:80]}..."
                )
            if o in seen:
                raise ValueError(f"duplicate option: {o}")
            seen.add(o)
        if preferred_answer not in options:
            raise ValueError(
                "preferred_answer must be one of the supplied options"
            )

    async def count_for_task(self, task_id: str, agent_role: str) -> int:
        row = await self._db.execute_fetchone(
            "SELECT COUNT(*) AS n FROM agent_questions "
            "WHERE task_id = ? AND agent_role = ?",
            (task_id, agent_role),
        )
        return int(row["n"] or 0) if row else 0

    async def ask(
        self,
        *,
        task_id: str,
        group_id: str,
        agent_role: str,
        instance_id: str | None,
        question: str,
        options: list,
        preferred_answer: str,
        reasoning: str,
        mode: str,  # "auto" | "manual"
    ) -> dict:
        """Persist a question and (in manual mode) wait for an answer.

        Returns a dict shaped for the MCP caller:
        ``{request_id, status, selected_answer, selected_by}``.
        """
        self._validate(
            question=question, options=options,
            preferred_answer=preferred_answer, reasoning=reasoning,
        )
        if mode not in ("auto", "manual"):
            raise ValueError(f"invalid mode {mode!r}")

        question_id = self._new_id()
        now = self._now()
        options_json = json.dumps(options)

        if mode == "auto":
            await self._db.execute(
                "INSERT INTO agent_questions "
                "(id, task_id, group_id, agent_role, instance_id, "
                " question, options, preferred_answer, reasoning, "
                " selected_answer, selected_by, status, "
                " created_at, resolved_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'agent', "
                " 'resolved', ?, ?)",
                (
                    question_id, task_id, group_id, agent_role,
                    instance_id, question, options_json,
                    preferred_answer, reasoning, preferred_answer,
                    now, now,
                ),
            )
            if self._event_bus is not None:
                await self._event_bus.emit("question.resolved", {
                    "question_id": question_id,
                    "task_id": task_id,
                    "group_id": group_id,
                    "selected_answer": preferred_answer,
                    "selected_by": "agent",
                })
            return {
                "request_id": question_id,
                "status": "answered",
                "selected_answer": preferred_answer,
                "selected_by": "agent",
            }

        # Manual mode: persist as pending, mark the task awaiting input,
        # set up a wake event, and block until answered or cancelled.
        await self._db.execute(
            "INSERT INTO agent_questions "
            "(id, task_id, group_id, agent_role, instance_id, "
            " question, options, preferred_answer, reasoning, "
            " status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)",
            (
                question_id, task_id, group_id, agent_role,
                instance_id, question, options_json,
                preferred_answer, reasoning, now,
            ),
        )
        await self._db.execute(
            "UPDATE tasks SET awaiting_input_since = ? WHERE id = ?",
            (now, task_id),
        )
        if self._event_bus is not None:
            await self._event_bus.emit("question.pending", {
                "question_id": question_id,
                "task_id": task_id,
                "group_id": group_id,
                "role": agent_role,
            })

        wake = asyncio.Event()
        slot: dict = {}
        self._waiters[question_id] = (wake, slot)
        try:
            await wake.wait()
        finally:
            self._waiters.pop(question_id, None)
            # Clear the pause anchor so the activity watchdog re-arms.
            await self._db.execute(
                "UPDATE tasks SET awaiting_input_since = NULL "
                "WHERE id = ?",
                (task_id,),
            )

        return {
            "request_id": question_id,
            "status": slot.get("status", "answered"),
            "selected_answer": slot.get("selected_answer"),
            "selected_by": slot.get("selected_by"),
        }

    async def answer(
        self, question_id: str, selected_answer: str,
    ) -> dict:
        """Human submits an answer via the dashboard.

        Validates the answer is in the original options list, persists
        the resolution, and wakes the blocked agent if there's one.
        """
        row = await self._db.execute_fetchone(
            "SELECT * FROM agent_questions WHERE id = ?",
            (question_id,),
        )
        if row is None:
            raise ValueError("question not found")
        if row["status"] != "pending":
            raise ValueError(
                f"question is already {row['status']!r}"
            )
        try:
            options = json.loads(row["options"])
        except json.JSONDecodeError:
            options = []
        if selected_answer not in options:
            raise ValueError(
                "selected_answer must be one of the original options"
            )
        now = self._now()
        await self._db.execute(
            "UPDATE agent_questions "
            "SET selected_answer = ?, selected_by = 'user', "
            "    status = 'resolved', resolved_at = ? "
            "WHERE id = ? AND status = 'pending'",
            (selected_answer, now, question_id),
        )
        # Wake the blocked agent (if it's still in this process; on a
        # restart there's no waiter, the row stays resolved on disk).
        waiter = self._waiters.get(question_id)
        if waiter is not None:
            evt, slot = waiter
            slot["status"] = "answered"
            slot["selected_answer"] = selected_answer
            slot["selected_by"] = "user"
            evt.set()
        if self._event_bus is not None:
            await self._event_bus.emit("question.resolved", {
                "question_id": question_id,
                "task_id": row["task_id"],
                "group_id": row["group_id"],
                "selected_answer": selected_answer,
                "selected_by": "user",
            })
        return {
            "id": question_id,
            "selected_answer": selected_answer,
            "selected_by": "user",
            "status": "resolved",
        }

    async def cancel_for_task(self, task_id: str) -> int:
        """Cancel any pending questions for a task.

        Called from the task-cancel path so a waiting agent's
        ask_question call returns ``{status: cancelled}`` and the
        agent fails out cleanly. Returns the number cancelled.
        """
        rows = await self._db.execute_fetchall(
            "SELECT id FROM agent_questions "
            "WHERE task_id = ? AND status = 'pending'",
            (task_id,),
        )
        if not rows:
            return 0
        now = self._now()
        for row in rows:
            qid = row["id"]
            await self._db.execute(
                "UPDATE agent_questions "
                "SET status = 'cancelled', resolved_at = ? "
                "WHERE id = ?",
                (now, qid),
            )
            waiter = self._waiters.get(qid)
            if waiter is not None:
                evt, slot = waiter
                slot["status"] = "cancelled"
                slot["selected_answer"] = None
                slot["selected_by"] = None
                evt.set()
        return len(rows)

    async def get_pending(self) -> list[dict]:
        rows = await self._db.execute_fetchall(
            "SELECT * FROM agent_questions WHERE status = 'pending' "
            "ORDER BY created_at"
        )
        return [self._serialise(r) for r in rows]

    async def get(self, question_id: str) -> dict | None:
        row = await self._db.execute_fetchone(
            "SELECT * FROM agent_questions WHERE id = ?",
            (question_id,),
        )
        return self._serialise(row) if row else None

    @staticmethod
    def _serialise(row) -> dict:
        out = dict(row)
        try:
            out["options"] = json.loads(out.get("options") or "[]")
        except json.JSONDecodeError:
            out["options"] = []
        return out
