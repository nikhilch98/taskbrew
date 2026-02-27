"""Agent collaboration: peer review, pair programming, handoff summaries, debate."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class CollaborationManager:
    """Manage collaborative workflows between agents."""

    def __init__(self, db, task_board=None, event_bus=None) -> None:
        self._db = db
        self._task_board = task_board
        self._event_bus = event_bus

    # --- Feature 11: Peer Review ---

    async def request_peer_review(self, task_id: str, reviewer_role: str = "coder") -> dict:
        """Create a peer review task for a completed task.

        After a coder completes a task, this creates a code_review task
        assigned to another coder instance (or specified role) before
        sending to the verifier.
        """
        task = await self._db.execute_fetchone("SELECT * FROM tasks WHERE id = ?", (task_id,))
        if not task:
            return {"error": "Task not found"}

        now = datetime.now(timezone.utc).isoformat()
        review_id = f"PR-{uuid.uuid4().hex[:6]}"

        await self._db.execute(
            "INSERT INTO tasks (id, group_id, parent_id, title, description, task_type, "
            "priority, assigned_to, status, created_by, created_at) "
            "VALUES (?, ?, ?, ?, ?, 'code_review', ?, ?, 'pending', ?, ?)",
            (
                review_id, task["group_id"], task_id,
                f"Peer Review: {task['title'][:80]}",
                f"Review the implementation from task {task_id}.\n\nOriginal description:\n{task.get('description', '')}",
                task.get("priority", "medium"),
                reviewer_role,
                task.get("claimed_by", "system"),
                now,
            ),
        )

        result = {"review_task_id": review_id, "original_task_id": task_id, "reviewer_role": reviewer_role}
        if self._event_bus:
            await self._event_bus.emit("collaboration.peer_review_requested", result)
        return result

    # --- Feature 12: Pair Programming ---

    async def start_pair_session(self, task_id: str, agent1: str, agent2: str) -> dict:
        """Start a pair programming session between two agents.

        Creates a shared thread for the two agents to communicate during
        the task execution.
        """
        thread_id = f"pair-{uuid.uuid4().hex[:8]}"
        now = datetime.now(timezone.utc).isoformat()

        # Store pair session metadata as a message thread (wrapped in transaction)
        async with self._db.transaction() as conn:
            for agent_from, agent_to in [(agent1, agent2), (agent2, agent1)]:
                await conn.execute(
                    "INSERT INTO agent_messages (from_agent, to_agent, content, message_type, thread_id, created_at) "
                    "VALUES (?, ?, ?, 'pair_session', ?, ?)",
                    (agent_from, agent_to,
                     f"Pair session started for task {task_id}. Collaborate via this thread.",
                     thread_id, now),
                )

        result = {
            "task_id": task_id,
            "agent1": agent1,
            "agent2": agent2,
            "thread_id": thread_id,
            "status": "active",
            "created_at": now,
        }
        if self._event_bus:
            await self._event_bus.emit("collaboration.pair_started", result)
        return result

    async def get_pair_context(self, thread_id: str, for_agent: str, limit: int = 10) -> str:
        """Get pair session context for an agent's prompt."""
        messages = await self._db.execute_fetchall(
            "SELECT from_agent, content, created_at FROM agent_messages "
            "WHERE thread_id = ? ORDER BY created_at DESC LIMIT ?",
            (thread_id, limit),
        )
        if not messages:
            return ""

        parts = ["## Pair Session"]
        for msg in reversed(messages):
            label = "You" if msg["from_agent"] == for_agent else f"Partner ({msg['from_agent']})"
            parts.append(f"**{label}**: {msg['content'][:200]}")
        return "\n".join(parts)

    # --- Feature 13: Handoff Summaries ---

    async def generate_handoff_summary(self, task_id: str, output: str) -> str:
        """Generate a structured handoff summary from task output.

        The summary is stored in the task's output_text and included in
        downstream task context.
        """
        # Extract key sections from output
        summary_parts = []

        # Determine what was done
        summary_parts.append(f"## Handoff Summary for {task_id}")

        if output:
            # Take the first ~300 chars as summary
            brief = output[:300].strip()
            if len(output) > 300:
                brief += "..."
            summary_parts.append(f"**Output preview**: {brief}")

            # Look for file changes
            file_refs = []
            for line in output.split("\n"):
                if any(ext in line for ext in [".py", ".js", ".ts", ".html", ".yaml", ".json"]):
                    stripped = line.strip()[:120]
                    if stripped and len(stripped) > 5:
                        file_refs.append(stripped)
            if file_refs:
                summary_parts.append("**Files referenced**: " + "; ".join(file_refs[:5]))

        summary = "\n".join(summary_parts)

        # Store the summary
        await self._db.execute(
            "UPDATE tasks SET output_text = ? WHERE id = ?",
            (summary, task_id),
        )

        return summary

    async def get_handoff_context(self, task_id: str) -> str:
        """Get handoff summary from parent/predecessor tasks."""
        task = await self._db.execute_fetchone("SELECT * FROM tasks WHERE id = ?", (task_id,))
        if not task or not task.get("parent_id"):
            return ""

        parent = await self._db.execute_fetchone(
            "SELECT id, title, output_text FROM tasks WHERE id = ?",
            (task["parent_id"],),
        )
        if not parent or not parent.get("output_text"):
            return ""

        return f"\n## Previous Task Output ({parent['id']}: {parent['title']})\n{parent['output_text']}"

    # --- Feature 14: Debate Protocol ---

    async def start_debate(self, task_id: str, debater_role: str = "coder", judge_role: str = "architect") -> dict:
        """Start a debate protocol: two debaters propose alternatives, a judge decides.

        Creates three tasks: debater A, debater B, and a judge task that
        is blocked by both debaters.
        """
        task = await self._db.execute_fetchone("SELECT * FROM tasks WHERE id = ?", (task_id,))
        if not task:
            return {"error": "Task not found"}

        now = datetime.now(timezone.utc).isoformat()
        debate_a_id = f"DEB-A-{uuid.uuid4().hex[:4]}"
        debate_b_id = f"DEB-B-{uuid.uuid4().hex[:4]}"
        judge_id = f"DEB-J-{uuid.uuid4().hex[:4]}"

        description_a = (
            f"DEBATE ROLE: Propose Approach A for task {task_id}.\n\n"
            f"Original task: {task['title']}\n{task.get('description', '')}\n\n"
            "Present your best approach with rationale. Focus on simplicity and correctness."
        )
        description_b = (
            f"DEBATE ROLE: Propose Approach B for task {task_id}.\n\n"
            f"Original task: {task['title']}\n{task.get('description', '')}\n\n"
            "Present an alternative approach with rationale. Focus on performance and scalability."
        )
        description_judge = (
            f"DEBATE JUDGE: Evaluate approaches for task {task_id}.\n\n"
            f"Original task: {task['title']}\n"
            "Review Approach A and Approach B from the debater tasks, then select the best approach."
        )

        # Wrap all inserts in a transaction for atomicity
        async with self._db.transaction() as conn:
            for task_data in [
                (debate_a_id, f"Debate A: {task['title'][:60]}", description_a, debater_role),
                (debate_b_id, f"Debate B: {task['title'][:60]}", description_b, debater_role),
            ]:
                await conn.execute(
                    "INSERT INTO tasks (id, group_id, parent_id, title, description, task_type, "
                    "priority, assigned_to, status, created_by, created_at) "
                    "VALUES (?, ?, ?, ?, ?, 'tech_design', ?, ?, 'pending', 'system', ?)",
                    (task_data[0], task["group_id"], task_id, task_data[1], task_data[2],
                     task.get("priority", "medium"), task_data[3], now),
                )

            # Judge task blocked by both debaters
            await conn.execute(
                "INSERT INTO tasks (id, group_id, parent_id, title, description, task_type, "
                "priority, assigned_to, status, created_by, created_at) "
                "VALUES (?, ?, ?, ?, ?, 'architecture_review', ?, ?, 'blocked', 'system', ?)",
                (judge_id, task["group_id"], task_id, f"Judge: {task['title'][:60]}",
                 description_judge, task.get("priority", "medium"), judge_role, now),
            )

            # Create dependencies
            for debater_id in [debate_a_id, debate_b_id]:
                await conn.execute(
                    "INSERT INTO task_dependencies (task_id, blocked_by) VALUES (?, ?)",
                    (judge_id, debater_id),
                )

        result = {
            "original_task_id": task_id,
            "debate_a_id": debate_a_id,
            "debate_b_id": debate_b_id,
            "judge_id": judge_id,
            "status": "started",
        }
        if self._event_bus:
            await self._event_bus.emit("collaboration.debate_started", result)
        return result

    async def get_active_collaborations(self, limit: int = 10) -> dict:
        """Get active collaboration sessions (pair sessions and debates)."""
        # Pair sessions
        pairs = await self._db.execute_fetchall(
            "SELECT DISTINCT thread_id, from_agent, to_agent, created_at "
            "FROM agent_messages WHERE message_type = 'pair_session' "
            "ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
        # Debates (tasks starting with DEB-)
        debates = await self._db.execute_fetchall(
            "SELECT id, title, status, parent_id FROM tasks "
            "WHERE id LIKE 'DEB-J-%' ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
        return {
            "pair_sessions": pairs,
            "active_debates": debates,
        }
