"""Human interaction request management — approvals and clarifications."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from taskbrew.orchestrator.database import Database


class InteractionManager:
    """CRUD layer for human_interaction_requests, task_chains, first_run_approvals."""

    def __init__(self, db: Database):
        self._db = db

    async def create_request(
        self,
        task_id: str,
        group_id: str,
        agent_role: str,
        instance_token: str,
        req_type: str,
        request_data: dict,
        request_key: str | None = None,
    ) -> dict:
        req_id = f"hir-{uuid.uuid4().hex[:12]}"
        if request_key is None:
            request_key = f"{task_id}:{req_type}:{uuid.uuid4().hex[:8]}"
        now = datetime.now(timezone.utc).isoformat()

        # Idempotency — check if request_key already exists
        existing = await self._db.execute_fetchone(
            "SELECT * FROM human_interaction_requests WHERE request_key = ?",
            (request_key,),
        )
        if existing:
            return self._row_to_dict(existing)

        # Store group_id and agent_role inside the payload JSON so they
        # survive the round-trip (the table has no dedicated columns for them).
        enriched_payload = {**request_data, "_group_id": group_id, "_agent_role": agent_role}
        await self._db.execute(
            "INSERT INTO human_interaction_requests "
            "(id, request_key, task_id, instance_token, request_type, status, payload, created_at) "
            "VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)",
            (req_id, request_key, task_id, instance_token, req_type, json.dumps(enriched_payload), now),
        )
        return {
            "id": req_id,
            "request_key": request_key,
            "task_id": task_id,
            "group_id": group_id,
            "agent_role": agent_role,
            "instance_token": instance_token,
            "type": req_type,
            "status": "pending",
            "request_data": request_data,
            "response_data": None,
            "created_at": now,
            "resolved_at": None,
        }

    async def get_pending(self) -> list[dict]:
        rows = await self._db.execute_fetchall(
            "SELECT * FROM human_interaction_requests WHERE status = 'pending' ORDER BY created_at",
        )
        return [self._row_to_dict(r) for r in rows]

    async def get_history(self, limit: int = 50) -> list[dict]:
        rows = await self._db.execute_fetchall(
            "SELECT * FROM human_interaction_requests WHERE status != 'pending' ORDER BY resolved_at DESC LIMIT ?",
            (limit,),
        )
        return [self._row_to_dict(r) for r in rows]

    async def resolve(self, request_id: str, status: str, response_data: dict | None = None) -> dict | None:
        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            "UPDATE human_interaction_requests SET status = ?, response_payload = ?, resolved_at = ? WHERE id = ?",
            (status, json.dumps(response_data) if response_data is not None else None, now, request_id),
        )
        row = await self._db.execute_fetchone(
            "SELECT * FROM human_interaction_requests WHERE id = ?", (request_id,),
        )
        return self._row_to_dict(row) if row else None

    async def check_status(self, request_id: str) -> dict | None:
        row = await self._db.execute_fetchone(
            "SELECT * FROM human_interaction_requests WHERE id = ?", (request_id,),
        )
        return self._row_to_dict(row) if row else None

    async def check_first_run(self, group_id: str, agent_role: str) -> bool:
        row = await self._db.execute_fetchone(
            "SELECT 1 FROM first_run_approvals WHERE group_id = ? AND agent_role = ?",
            (group_id, agent_role),
        )
        return row is not None

    async def record_first_run(self, group_id: str, agent_role: str) -> None:
        fra_id = f"fra-{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            "INSERT OR IGNORE INTO first_run_approvals (id, group_id, agent_role, approved_at) VALUES (?, ?, ?, ?)",
            (fra_id, group_id, agent_role, now),
        )

    def _row_to_dict(self, row) -> dict:
        d = dict(row)
        # Normalize column names to the public interface
        if "request_type" in d:
            d["type"] = d.pop("request_type")
        if "payload" in d:
            val = d.pop("payload")
            if isinstance(val, str):
                try:
                    val = json.loads(val)
                except (json.JSONDecodeError, TypeError):
                    pass
            # Extract group_id/agent_role stored inside the payload
            if isinstance(val, dict):
                if "_group_id" in val:
                    d["group_id"] = val.pop("_group_id")
                if "_agent_role" in val:
                    d["agent_role"] = val.pop("_agent_role")
            d["request_data"] = val
        if "response_payload" in d:
            val = d.pop("response_payload")
            if isinstance(val, str):
                try:
                    val = json.loads(val)
                except (json.JSONDecodeError, TypeError):
                    pass
            d["response_data"] = val
        return d
