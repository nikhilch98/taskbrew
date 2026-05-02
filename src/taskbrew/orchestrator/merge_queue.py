"""Durable queue for provider-neutral branch integration."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone


MERGE_QUEUE_OPEN_STATUSES = frozenset({
    "queued",
    "running",
    "retry_pending",
    "blocked",
    "conflict",
    "root_refresh_blocked",
    "failed",
})

MERGE_QUEUE_SUCCESS_STATUSES = frozenset({"merged", "already_merged"})


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _future(seconds: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


class MergeQueue:
    """Persistence API for approved-branch integration requests."""

    def __init__(self, db) -> None:
        self._db = db

    async def enqueue(
        self,
        *,
        group_id: str,
        parent_task_id: str,
        verifier_task_id: str,
        source_branch: str,
        target_branch: str = "main",
    ) -> dict:
        """Insert or return the merge row for *verifier_task_id*."""
        existing = await self._db.execute_fetchone(
            "SELECT * FROM merge_queue WHERE verifier_task_id = ?",
            (verifier_task_id,),
        )
        if existing:
            return existing

        now = _utcnow()
        row_id = f"MQ-{uuid.uuid4().hex[:12]}"
        rows = await self._db.execute_returning(
            "INSERT INTO merge_queue ("
            "id, group_id, parent_task_id, verifier_task_id, source_branch, "
            "target_branch, status, attempts, created_at, updated_at"
            ") VALUES (?, ?, ?, ?, ?, ?, 'queued', 0, ?, ?) "
            "RETURNING *",
            (
                row_id,
                group_id,
                parent_task_id,
                verifier_task_id,
                source_branch,
                target_branch or "main",
                now,
                now,
            ),
        )
        return rows[0]

    async def claim_ready(
        self,
        *,
        worker_id: str,
        lease_seconds: float = 120.0,
    ) -> dict | None:
        """Lease the oldest ready row, returning it or ``None``."""
        now = _utcnow()
        lease_until = _future(lease_seconds)
        rows = await self._db.execute_returning(
            "UPDATE merge_queue SET status = 'running', leased_by = ?, "
            "leased_until = ?, attempts = attempts + 1, updated_at = ? "
            "WHERE id = ("
            "  SELECT id FROM merge_queue "
            "  WHERE status = 'queued' "
            "     OR (status = 'retry_pending' "
            "         AND (next_attempt_at IS NULL OR next_attempt_at <= ?)) "
            "  ORDER BY created_at LIMIT 1"
            ") "
            "AND (status = 'queued' "
            "     OR (status = 'retry_pending' "
            "         AND (next_attempt_at IS NULL OR next_attempt_at <= ?))) "
            "RETURNING *",
            (worker_id, lease_until, now, now, now),
        )
        return rows[0] if rows else None

    async def release_expired_leases(self) -> int:
        """Return stale running rows to retry_pending."""
        now = _utcnow()
        rows = await self._db.execute_returning(
            "UPDATE merge_queue SET status = 'retry_pending', leased_by = NULL, "
            "leased_until = NULL, next_attempt_at = ?, "
            "last_error = COALESCE(last_error, 'Merge broker lease expired'), "
            "updated_at = ? "
            "WHERE status = 'running' AND leased_until IS NOT NULL "
            "AND leased_until < ? RETURNING id",
            (now, now, now),
        )
        return len(rows)

    async def retry(
        self,
        row_id: str,
        *,
        details: str,
        delay_seconds: float,
    ) -> dict | None:
        now = _utcnow()
        rows = await self._db.execute_returning(
            "UPDATE merge_queue SET status = 'retry_pending', leased_by = NULL, "
            "leased_until = NULL, next_attempt_at = ?, last_error = ?, "
            "updated_at = ? WHERE id = ? RETURNING *",
            (_future(delay_seconds), details, now, row_id),
        )
        return rows[0] if rows else None

    async def complete(
        self,
        row_id: str,
        *,
        status: str,
        details: str | None = None,
        target_sha_before: str | None = None,
        target_sha_after: str | None = None,
        root_refresh_status: str | None = None,
    ) -> dict | None:
        now = _utcnow()
        rows = await self._db.execute_returning(
            "UPDATE merge_queue SET status = ?, leased_by = NULL, "
            "leased_until = NULL, next_attempt_at = NULL, last_error = ?, "
            "target_sha_before = COALESCE(?, target_sha_before), "
            "target_sha_after = COALESCE(?, target_sha_after), "
            "root_refresh_status = COALESCE(?, root_refresh_status), "
            "updated_at = ?, completed_at = ? WHERE id = ? RETURNING *",
            (
                status,
                details,
                target_sha_before,
                target_sha_after,
                root_refresh_status,
                now,
                now,
                row_id,
            ),
        )
        return rows[0] if rows else None

    async def supersede_open_for_parent(
        self,
        *,
        parent_task_id: str,
        except_row_id: str,
        details: str,
    ) -> int:
        """Close older unresolved rows once a later merge for the parent lands."""
        placeholders = ",".join("?" for _ in MERGE_QUEUE_OPEN_STATUSES)
        now = _utcnow()
        rows = await self._db.execute_returning(
            "UPDATE merge_queue SET status = 'superseded', leased_by = NULL, "
            "leased_until = NULL, next_attempt_at = NULL, last_error = ?, "
            "updated_at = ?, completed_at = COALESCE(completed_at, ?) "
            "WHERE parent_task_id = ? AND id != ? "
            f"AND status IN ({placeholders}) RETURNING id",
            (
                details,
                now,
                now,
                parent_task_id,
                except_row_id,
                *MERGE_QUEUE_OPEN_STATUSES,
            ),
        )
        return len(rows)

    async def update_result_metadata(
        self,
        row_id: str,
        *,
        target_sha_before: str | None = None,
        target_sha_after: str | None = None,
        root_refresh_status: str | None = None,
    ) -> None:
        await self._db.execute(
            "UPDATE merge_queue SET "
            "target_sha_before = COALESCE(?, target_sha_before), "
            "target_sha_after = COALESCE(?, target_sha_after), "
            "root_refresh_status = COALESCE(?, root_refresh_status), "
            "updated_at = ? WHERE id = ?",
            (
                target_sha_before,
                target_sha_after,
                root_refresh_status,
                _utcnow(),
                row_id,
            ),
        )

    async def has_open_group_merges(self, group_id: str) -> bool:
        placeholders = ",".join("?" for _ in MERGE_QUEUE_OPEN_STATUSES)
        row = await self._db.execute_fetchone(
            "SELECT 1 FROM merge_queue WHERE group_id = ? "
            f"AND status IN ({placeholders}) LIMIT 1",
            (group_id, *MERGE_QUEUE_OPEN_STATUSES),
        )
        return row is not None

    async def counts_for_group(self, group_id: str) -> dict[str, int]:
        rows = await self._db.execute_fetchall(
            "SELECT status, COUNT(*) AS n FROM merge_queue "
            "WHERE group_id = ? GROUP BY status",
            (group_id,),
        )
        return {row["status"]: int(row["n"] or 0) for row in rows}

    async def list_non_running_integration_ids(self) -> list[str]:
        rows = await self._db.execute_fetchall(
            "SELECT id FROM merge_queue WHERE status != 'running'",
        )
        return [row["id"] for row in rows]
