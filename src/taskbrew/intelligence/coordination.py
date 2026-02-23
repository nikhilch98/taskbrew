"""Coordination intelligence: standups, file locks, digests, mentoring, consensus, work stealing, heartbeats."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)


class CoordinationManager:
    """Coordinate multi-agent workflows and communication."""

    def __init__(self, db, task_board=None, event_bus=None, instance_manager=None) -> None:
        self._db = db
        self._task_board = task_board
        self._event_bus = event_bus
        self._instance_manager = instance_manager

    # ------------------------------------------------------------------
    # Feature 20: Agent Standup Reports
    # ------------------------------------------------------------------

    async def generate_standup(self, agent_id: str) -> dict:
        """Generate a standup report for an agent based on recent activity."""
        now = datetime.now(timezone.utc)
        since = (now - timedelta(hours=24)).isoformat()
        now_iso = now.isoformat()

        completed = await self._db.execute_fetchall(
            "SELECT id, title FROM tasks WHERE claimed_by = ? AND status = 'completed' "
            "AND completed_at >= ?",
            (agent_id, since),
        )

        in_progress = await self._db.execute_fetchall(
            "SELECT id, title FROM tasks WHERE claimed_by = ? AND status = 'in_progress'",
            (agent_id,),
        )

        blocked = await self._db.execute_fetchall(
            "SELECT id, title FROM tasks WHERE claimed_by = ? AND status = 'blocked'",
            (agent_id,),
        )

        completed_list = [{"id": t["id"], "title": t["title"]} for t in completed]
        in_progress_list = [{"id": t["id"], "title": t["title"]} for t in in_progress]
        blocker_list = [{"id": t["id"], "title": t["title"]} for t in blocked]

        report_id = f"SU-{uuid.uuid4().hex[:8]}"
        plan = f"Continue work on {len(in_progress_list)} in-progress task(s)."

        await self._db.execute(
            "INSERT INTO standup_reports "
            "(id, agent_id, report_type, completed_tasks, in_progress_tasks, blockers, plan, created_at) "
            "VALUES (?, ?, 'daily', ?, ?, ?, ?, ?)",
            (
                report_id, agent_id,
                json.dumps(completed_list),
                json.dumps(in_progress_list),
                json.dumps(blocker_list),
                plan, now_iso,
            ),
        )

        return {
            "id": report_id,
            "agent_id": agent_id,
            "report_type": "daily",
            "completed_tasks": completed_list,
            "in_progress_tasks": in_progress_list,
            "blockers": blocker_list,
            "plan": plan,
            "created_at": now_iso,
        }

    async def get_standups(self, agent_id: str | None = None, limit: int = 10) -> list[dict]:
        """Retrieve standup reports, optionally filtered by agent."""
        if agent_id:
            rows = await self._db.execute_fetchall(
                "SELECT * FROM standup_reports WHERE agent_id = ? ORDER BY created_at DESC LIMIT ?",
                (agent_id, limit),
            )
        else:
            rows = await self._db.execute_fetchall(
                "SELECT * FROM standup_reports ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
        for row in rows:
            for field in ("completed_tasks", "in_progress_tasks", "blockers"):
                if isinstance(row.get(field), str):
                    try:
                        row[field] = json.loads(row[field])
                    except (json.JSONDecodeError, TypeError) as exc:
                        logger.warning("Failed to parse standup field %s JSON: %s", field, exc)
        return rows

    # ------------------------------------------------------------------
    # Feature 21: File Conflict Detection
    # ------------------------------------------------------------------

    async def acquire_lock(self, file_path: str, agent_id: str, task_id: str | None = None) -> dict:
        """Try to acquire a file lock. Returns conflict info if already locked."""
        now = datetime.now(timezone.utc)
        expires = (now + timedelta(hours=1)).isoformat()
        now_iso = now.isoformat()
        lock_id = f"FL-{uuid.uuid4().hex[:8]}"

        try:
            await self._db.execute(
                "INSERT INTO file_locks (id, file_path, locked_by, task_id, locked_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (lock_id, file_path, agent_id, task_id, now_iso, expires),
            )
        except Exception as exc:
            # UNIQUE violation — file already locked
            logger.warning("File lock conflict for %s: %s", file_path, exc)
            existing = await self._db.execute_fetchone(
                "SELECT * FROM file_locks WHERE file_path = ?", (file_path,)
            )
            if existing:
                return {
                    "conflict": True,
                    "locked_by": existing["locked_by"],
                    "file_path": file_path,
                    "locked_at": existing["locked_at"],
                    "expires_at": existing["expires_at"],
                }
            return {"conflict": True, "locked_by": "unknown", "file_path": file_path}

        return {
            "conflict": False,
            "id": lock_id,
            "file_path": file_path,
            "locked_by": agent_id,
            "task_id": task_id,
            "locked_at": now_iso,
            "expires_at": expires,
        }

    async def release_lock(self, file_path: str, agent_id: str) -> dict:
        """Release a file lock owned by the given agent."""
        row = await self._db.execute_fetchone(
            "SELECT id FROM file_locks WHERE file_path = ? AND locked_by = ?",
            (file_path, agent_id),
        )
        if not row:
            return {"released": False, "reason": "not_found"}

        await self._db.execute(
            "DELETE FROM file_locks WHERE file_path = ? AND locked_by = ?",
            (file_path, agent_id),
        )
        return {"released": True, "file_path": file_path, "agent_id": agent_id}

    async def detect_conflicts(self) -> list[dict]:
        """Detect files with active (non-expired) locks from multiple agents."""
        now_iso = datetime.now(timezone.utc).isoformat()
        rows = await self._db.execute_fetchall(
            "SELECT file_path, GROUP_CONCAT(locked_by) as agents, COUNT(*) as lock_count "
            "FROM file_locks WHERE expires_at > ? "
            "GROUP BY file_path HAVING lock_count > 1",
            (now_iso,),
        )
        conflicts: list[dict] = []
        for row in rows:
            conflicts.append({
                "file_path": row["file_path"],
                "agents": row["agents"].split(",") if row["agents"] else [],
                "lock_count": row["lock_count"],
            })
        return conflicts

    # ------------------------------------------------------------------
    # Feature 22: Knowledge Sharing Digest
    # ------------------------------------------------------------------

    async def create_digest(
        self, digest_type: str, content: str, target_roles: list[str] | None = None
    ) -> dict:
        """Create a knowledge sharing digest."""
        now = datetime.now(timezone.utc).isoformat()
        did = f"DIG-{uuid.uuid4().hex[:8]}"
        roles_json = json.dumps(target_roles) if target_roles else None

        await self._db.execute(
            "INSERT INTO knowledge_digests (id, digest_type, content, target_roles, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (did, digest_type, content, roles_json, now),
        )

        return {
            "id": did,
            "digest_type": digest_type,
            "content": content,
            "target_roles": target_roles,
            "created_at": now,
        }

    async def get_digests(self, role: str | None = None, limit: int = 10) -> list[dict]:
        """Retrieve digests, optionally filtered by target role."""
        if role:
            rows = await self._db.execute_fetchall(
                "SELECT * FROM knowledge_digests "
                "WHERE target_roles IS NULL OR target_roles LIKE ? "
                "ORDER BY created_at DESC LIMIT ?",
                (f"%{role}%", limit),
            )
        else:
            rows = await self._db.execute_fetchall(
                "SELECT * FROM knowledge_digests ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
        for row in rows:
            if isinstance(row.get("target_roles"), str):
                try:
                    row["target_roles"] = json.loads(row["target_roles"])
                except (json.JSONDecodeError, TypeError) as exc:
                    logger.warning("Failed to parse digest target_roles JSON: %s", exc)
        return rows

    # ------------------------------------------------------------------
    # Feature 23: Mentor-Mentee Pairing
    # ------------------------------------------------------------------

    async def create_pair(self, mentor_role: str, mentee_role: str, skill_area: str) -> dict:
        """Create a mentor-mentee pairing."""
        now = datetime.now(timezone.utc).isoformat()
        pid = f"MP-{uuid.uuid4().hex[:8]}"

        await self._db.execute(
            "INSERT INTO mentor_pairs (id, mentor_role, mentee_role, skill_area, status, created_at) "
            "VALUES (?, ?, ?, ?, 'active', ?)",
            (pid, mentor_role, mentee_role, skill_area, now),
        )

        return {
            "id": pid,
            "mentor_role": mentor_role,
            "mentee_role": mentee_role,
            "skill_area": skill_area,
            "status": "active",
            "created_at": now,
        }

    async def get_pairs(self, role: str | None = None) -> list[dict]:
        """Get mentor-mentee pairs, optionally filtered by role."""
        if role:
            return await self._db.execute_fetchall(
                "SELECT * FROM mentor_pairs WHERE mentor_role = ? OR mentee_role = ? "
                "ORDER BY created_at DESC",
                (role, role),
            )
        return await self._db.execute_fetchall(
            "SELECT * FROM mentor_pairs ORDER BY created_at DESC"
        )

    # ------------------------------------------------------------------
    # Feature 24: Multi-Agent Consensus Protocol
    # ------------------------------------------------------------------

    async def _ensure_consensus_tables(self) -> None:
        """Create the consensus_proposals table if it does not exist."""
        await self._db.execute(
            """CREATE TABLE IF NOT EXISTS consensus_proposals (
                id TEXT PRIMARY KEY,
                description TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                created_at TEXT NOT NULL
            )"""
        )

    async def create_proposal(self, proposal_id: str, description: str) -> dict:
        """Create a proposal and persist it to the database."""
        await self._ensure_consensus_tables()
        now = datetime.now(timezone.utc).isoformat()

        await self._db.execute(
            "INSERT INTO consensus_proposals (id, description, status, created_at) "
            "VALUES (?, ?, 'open', ?)",
            (proposal_id, description, now),
        )

        return {
            "proposal_id": proposal_id,
            "description": description,
            "status": "open",
            "created_at": now,
        }

    async def cast_vote(
        self, proposal_id: str, voter_id: str, vote: str, reasoning: str | None = None
    ) -> dict:
        """Cast a vote on a proposal. Each voter may only vote once per proposal."""
        now = datetime.now(timezone.utc).isoformat()

        # Check if voter already voted on this proposal
        existing = await self._db.execute_fetchone(
            "SELECT id FROM consensus_votes WHERE proposal_id = ? AND voter_id = ?",
            (proposal_id, voter_id),
        )
        if existing:
            return {
                "error": "duplicate_vote",
                "proposal_id": proposal_id,
                "voter_id": voter_id,
                "message": f"Voter '{voter_id}' has already voted on proposal '{proposal_id}'",
            }

        vid = f"V-{uuid.uuid4().hex[:8]}"

        await self._db.execute(
            "INSERT INTO consensus_votes (id, proposal_id, voter_id, vote, reasoning, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (vid, proposal_id, voter_id, vote, reasoning, now),
        )

        return {
            "id": vid,
            "proposal_id": proposal_id,
            "voter_id": voter_id,
            "vote": vote,
            "reasoning": reasoning,
            "created_at": now,
        }

    async def tally_votes(self, proposal_id: str) -> dict:
        """Tally votes for a proposal and determine the result."""
        rows = await self._db.execute_fetchall(
            "SELECT vote FROM consensus_votes WHERE proposal_id = ?",
            (proposal_id,),
        )

        approve = sum(1 for r in rows if r["vote"] == "approve")
        reject = sum(1 for r in rows if r["vote"] == "reject")
        abstain = sum(1 for r in rows if r["vote"] == "abstain")
        total = len(rows)

        if total == 0:
            result = "no_quorum"
        elif approve > total / 2:
            result = "approved"
        else:
            result = "rejected"

        return {
            "proposal_id": proposal_id,
            "approve": approve,
            "reject": reject,
            "abstain": abstain,
            "total": total,
            "result": result,
        }

    # ------------------------------------------------------------------
    # Feature 25: Work Stealing
    # ------------------------------------------------------------------

    async def find_stealable_tasks(self, agent_id: str) -> list[dict]:
        """Find pending tasks from overloaded agents that could be stolen."""
        rows = await self._db.execute_fetchall(
            "SELECT t.* FROM tasks t "
            "WHERE t.status = 'pending' AND t.assigned_to != ? "
            "AND t.assigned_to IN ("
            "  SELECT assigned_to FROM tasks WHERE status = 'pending' "
            "  GROUP BY assigned_to HAVING COUNT(*) > 3"
            ") "
            "ORDER BY t.created_at ASC",
            (agent_id,),
        )
        return rows

    async def steal_task(self, task_id: str, agent_id: str) -> dict:
        """Attempt to claim a pending task for this agent."""
        row = await self._db.execute_fetchone(
            "SELECT id, status FROM tasks WHERE id = ? AND status = 'pending'",
            (task_id,),
        )
        if not row:
            return {"success": False, "reason": "Task not found or not pending"}

        await self._db.execute(
            "UPDATE tasks SET claimed_by = ? WHERE id = ? AND status = 'pending'",
            (agent_id, task_id),
        )

        return {"success": True, "task_id": task_id, "claimed_by": agent_id}

    # ------------------------------------------------------------------
    # Feature 26: Progress Heartbeats
    # ------------------------------------------------------------------

    async def record_heartbeat(
        self, task_id: str, agent_id: str, progress_pct: float, status_message: str
    ) -> dict:
        """Record a progress heartbeat for a running task."""
        now = datetime.now(timezone.utc).isoformat()
        hid = f"HB-{uuid.uuid4().hex[:8]}"

        await self._db.execute(
            "INSERT INTO progress_heartbeats "
            "(id, task_id, agent_id, progress_pct, status_message, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (hid, task_id, agent_id, progress_pct, status_message, now),
        )

        return {
            "id": hid,
            "task_id": task_id,
            "agent_id": agent_id,
            "progress_pct": progress_pct,
            "status_message": status_message,
            "created_at": now,
        }

    async def get_heartbeats(self, task_id: str, limit: int = 20) -> list[dict]:
        """Get heartbeats for a task, most recent first."""
        return await self._db.execute_fetchall(
            "SELECT * FROM progress_heartbeats WHERE task_id = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (task_id, limit),
        )
