"""Task board: group and task CRUD with dependency management."""

from __future__ import annotations

import json
import logging
import subprocess
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from taskbrew.orchestrator.database import Database

logger = logging.getLogger(__name__)


def _utcnow() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


# Priority ordering used by the claim query (lower number = higher priority).
_PRIORITY_ORDER = {
    "critical": 0,
    "high": 1,
    "medium": 2,
    "low": 3,
}

# Visible Kanban column order. Existing cancelled tasks remain terminal,
# but are not represented as a separate visible board column.
BOARD_STATUSES = (
    "backlog",
    "pending",
    "in_progress",
    "review",
    "blocked",
    "completed",
    "rejected",
    "failed",
)

TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled", "rejected"})
CLAIMABLE_STATUS = "pending"
BACKLOG_STATUS = "backlog"
REVIEW_STATUS = "review"
DEFAULT_MAX_REVIEW_ROUNDS = 3
DEFAULT_PACKAGE_REVIEW_SCOPE = "work_package"
NO_REVIEW_SCOPE = "none"
TASK_REVIEW_SCOPE = "task"
PACKAGE_GATE_STALE_PENDING_SECONDS = 30 * 60
PACKAGE_GATE_STALE_RUNNING_SECONDS = 20 * 60
PACKAGE_STATUSES = (
    "backlog",
    "pending",
    "in_progress",
    "blocked",
    "review",
    "waiting_revision",
    "integrating",
    "completed",
    "rejected",
    "failed",
)
PACKAGE_INTEGRATING_STATUS = "integrating"
PACKAGE_INTEGRATION_TASK_TYPE = "package_integration"
PACKAGE_INTEGRATION_TASK_TYPES = frozenset({"implementation", "bug_fix", "revision"})
DEFAULT_PACKAGE_DESCRIPTION = "Automatically created to keep existing tasks grouped."
MERGE_QUEUE_OPEN_STATUSES = (
    "queued",
    "running",
    "retry_pending",
    "blocked",
    "conflict",
    "root_refresh_blocked",
    "failed",
)
MERGE_QUEUE_SUCCESS_STATUSES = ("merged", "already_merged")


def normalize_max_review_rounds(
    value: int | str | None,
    default: int = DEFAULT_MAX_REVIEW_ROUNDS,
) -> int:
    """Return a non-negative review round limit; 0 means unlimited."""
    if value is None:
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(0, parsed)


def review_round_limit_reached(record: dict) -> bool:
    """Return True when a task/package has exhausted fixed review rounds."""
    max_rounds = normalize_max_review_rounds(record.get("max_review_rounds"))
    review_round = int(record.get("review_round") or 0)
    return max_rounds > 0 and review_round >= max_rounds


class TaskBoard:
    """High-level CRUD interface for groups, tasks, and dependencies.

    Parameters
    ----------
    db:
        An initialised :class:`Database` instance.
    group_prefixes:
        Optional mapping of ``role -> prefix`` used when creating groups
        (e.g. ``{"pm": "FEAT", "architect": "DEBT"}``).
    """

    def __init__(
        self,
        db: Database,
        group_prefixes: dict[str, str] | None = None,
        event_bus=None,
        default_package_review_rounds: int | None = None,
    ) -> None:
        self._db = db
        self._group_prefixes: dict[str, str] = dict(group_prefixes or {})
        self._default_package_review_rounds = normalize_max_review_rounds(
            default_package_review_rounds
        )
        # Mapping from role name to task-ID prefix (e.g. "coder" -> "CD").
        self._role_to_prefix: dict[str, str] = {}
        # Optional event bus -- used to emit ``task.available`` when a
        # task becomes claimable (pending or just-unblocked). Agents
        # subscribe to this event so they can wake up immediately
        # instead of waiting for the next poll tick. Event bus is
        # optional for backwards compatibility with test fixtures
        # that construct TaskBoard without one.
        self._event_bus = event_bus
        self._package_merge_queue = None
        self._package_repo_dir: Path | None = None

    def set_default_package_review_rounds(self, value: int | str | None) -> None:
        """Set the default review/revision round limit for new packages."""
        self._default_package_review_rounds = normalize_max_review_rounds(value)

    async def apply_default_package_review_rounds_to_active_packages(
        self,
        value: int | str | None,
    ) -> int:
        """Apply the project default to packages that are not terminal."""
        max_rounds = normalize_max_review_rounds(value)
        self._default_package_review_rounds = max_rounds
        rows = await self._db.execute_returning(
            "UPDATE work_packages SET max_review_rounds = ?, updated_at = ? "
            "WHERE status NOT IN ('completed', 'rejected', 'failed') "
            "RETURNING id",
            (max_rounds, _utcnow()),
        )
        if rows:
            ids = [row["id"] for row in rows]
            placeholders = ",".join("?" for _ in ids)
            await self._db.execute(
                "UPDATE review_gates SET max_review_rounds = ?, updated_at = ? "
                f"WHERE entity_type = 'work_package' "
                f"AND entity_id IN ({placeholders})",
                (max_rounds, _utcnow(), *ids),
            )
        return len(rows)

    def configure_package_integration(self, *, merge_queue=None, repo_dir: str | None = None) -> None:
        """Wire package approval to the durable merge queue.

        Tests and legacy callers may construct ``TaskBoard`` without a repo
        or queue. In that mode package approval keeps the old in-memory
        semantics. Production orchestrators call this during startup so
        approved package deliverables must land on the intended branch before
        the package/group can close.
        """
        self._package_merge_queue = merge_queue
        self._package_repo_dir = Path(repo_dir).resolve() if repo_dir else None

    # ------------------------------------------------------------------
    # Prefix helpers
    # ------------------------------------------------------------------

    def set_group_prefixes(self, prefixes: dict[str, str]) -> None:
        """Replace the group prefix mapping."""
        self._group_prefixes = dict(prefixes)

    async def register_prefixes(self, role_prefixes: dict[str, str]) -> None:
        """Register all role prefixes in the database and cache the mapping.

        Parameters
        ----------
        role_prefixes:
            Mapping of ``role_name -> prefix`` (e.g. ``{"coder": "CD"}``).
        """
        self._role_to_prefix = dict(role_prefixes)
        for prefix in role_prefixes.values():
            await self._db.register_prefix(prefix)
        # Also register group prefixes.
        for prefix in self._group_prefixes.values():
            await self._db.register_prefix(prefix)

    # ------------------------------------------------------------------
    # Groups
    # ------------------------------------------------------------------

    async def create_group(
        self,
        title: str,
        origin: str | None = None,
        created_by: str | None = None,
    ) -> dict:
        """Create a new group with an auto-generated ID.

        The prefix is determined by *created_by* via ``_group_prefixes``.
        Falls back to ``"GRP"`` if the role has no group prefix configured.
        """
        prefix = self._group_prefixes.get(created_by or "", "GRP")
        # Make sure the prefix is registered (idempotent).
        await self._db.register_prefix(prefix)
        group_id = await self._db.generate_task_id(prefix)
        now = _utcnow()
        await self._db.execute(
            "INSERT INTO groups (id, title, origin, status, created_by, created_at) "
            "VALUES (?, ?, ?, 'active', ?, ?)",
            (group_id, title, origin, created_by, now),
        )
        return {
            "id": group_id,
            "title": title,
            "origin": origin,
            "status": "active",
            "created_by": created_by,
            "created_at": now,
            "completed_at": None,
        }

    async def get_groups(self, status: str | None = None) -> list[dict]:
        """Return all groups, optionally filtered by status."""
        if status is not None:
            return await self._db.execute_fetchall(
                "SELECT * FROM groups WHERE status = ? ORDER BY created_at",
                (status,),
            )
        return await self._db.execute_fetchall(
            "SELECT * FROM groups ORDER BY created_at"
        )

    # ------------------------------------------------------------------
    # Work Packages
    # ------------------------------------------------------------------

    async def create_work_package(
        self,
        group_id: str,
        title: str,
        description: str | None = None,
        *,
        milestone_id: str | None = None,
        created_by: str | None = None,
        risk_level: str = "medium",
        review_scope: str = DEFAULT_PACKAGE_REVIEW_SCOPE,
        max_review_rounds: int | None = None,
    ) -> dict:
        """Create a work package for a group."""
        group = await self._db.execute_fetchone(
            "SELECT 1 FROM groups WHERE id = ?", (group_id,)
        )
        if group is None:
            raise ValueError(f"Group not found: {group_id}")

        await self._db.register_prefix("WP")
        package_id = await self._db.generate_task_id("WP")
        now = _utcnow()
        await self._db.execute(
            "INSERT INTO work_packages "
            "(id, group_id, milestone_id, title, description, status, risk_level, "
            "review_scope, max_review_rounds, created_by, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?)",
            (
                package_id,
                group_id,
                milestone_id,
                title,
                description,
                risk_level,
                review_scope,
                normalize_max_review_rounds(
                    max_review_rounds,
                    self._default_package_review_rounds,
                ),
                created_by,
                now,
                now,
            ),
        )
        package = await self.get_work_package(package_id)
        if package is None:
            raise ValueError(f"Work package not found after create: {package_id}")
        return package

    async def get_work_package(self, package_id: str) -> dict | None:
        """Return a single work package by ID, or None."""
        return await self._db.execute_fetchone(
            "SELECT * FROM work_packages WHERE id = ?", (package_id,)
        )

    async def update_work_package_metadata(
        self,
        package_id: str,
        *,
        title: str | None = None,
        description: str | None = None,
        only_if_default: bool = False,
    ) -> dict | None:
        """Update a package title/description from PM or Architect design output."""
        package = await self.get_work_package(package_id)
        if package is None:
            return None
        if only_if_default and not self._is_default_work_package(package):
            return package

        clean_title = title.strip() if isinstance(title, str) else None
        clean_description = (
            description.strip() if isinstance(description, str) else None
        )
        if not clean_title and not clean_description:
            return package

        next_title = clean_title or package["title"]
        next_description = clean_description or package.get("description")
        rows = await self._db.execute_returning(
            "UPDATE work_packages SET title = ?, description = ?, updated_at = ? "
            "WHERE id = ? RETURNING *",
            (next_title, next_description, _utcnow(), package_id),
        )
        return rows[0] if rows else await self.get_work_package(package_id)

    def _is_default_work_package(self, package: dict) -> bool:
        title = str(package.get("title") or "")
        return (
            package.get("created_by") == "system"
            and title.startswith("Default package for ")
            and (package.get("description") in (None, "", DEFAULT_PACKAGE_DESCRIPTION))
        )

    async def get_group_work_packages(self, group_id: str) -> list[dict]:
        """Return all work packages belonging to a group."""
        return await self._db.execute_fetchall(
            "SELECT * FROM work_packages WHERE group_id = ? ORDER BY created_at",
            (group_id,),
        )

    async def get_work_package_board(self, group_id: str | None = None) -> dict:
        """Return work packages grouped by package status with dashboard metadata."""
        clauses: list[str] = []
        params: list[str] = []
        if group_id is not None:
            clauses.append("group_id = ?")
            params.append(group_id)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        package_rows = await self._db.execute_fetchall(
            f"SELECT * FROM work_packages{where} ORDER BY created_at",
            tuple(params),
        )

        package_ids = [package["id"] for package in package_rows]
        tasks_by_package: dict[str, list[dict]] = {package_id: [] for package_id in package_ids}
        gates_by_package: dict[str, dict] = {}
        integration_by_package: dict[str, list[dict]] = {
            package_id: [] for package_id in package_ids
        }
        if package_ids:
            placeholders = ",".join("?" * len(package_ids))
            task_rows = await self._db.execute_fetchall(
                "SELECT * "
                "FROM tasks "
                f"WHERE work_package_id IN ({placeholders}) "
                "ORDER BY created_at",
                tuple(package_ids),
            )
            for row in task_rows:
                tasks_by_package.setdefault(row["work_package_id"], []).append(row)

            gate_rows = await self._db.execute_fetchall(
                "SELECT * FROM review_gates "
                "WHERE entity_type = 'work_package' "
                f"AND entity_id IN ({placeholders})",
                tuple(package_ids),
            )
            gates_by_package = {row["entity_id"]: row for row in gate_rows}

            integration_rows = await self._db.execute_fetchall(
                "SELECT * FROM merge_queue "
                f"WHERE work_package_id IN ({placeholders}) "
                "ORDER BY created_at",
                tuple(package_ids),
            )
            for row in integration_rows:
                integration_by_package.setdefault(row["work_package_id"], []).append(row)

        columns: dict[str, list[dict]] = {status: [] for status in PACKAGE_STATUSES}
        packages: list[dict] = []
        now = datetime.now(timezone.utc)
        for package in package_rows:
            card = self._enrich_work_package_card(
                package,
                tasks_by_package.get(package["id"], []),
                gates_by_package.get(package["id"]),
                integration_by_package.get(package["id"], []),
                now,
            )
            columns.setdefault(card["status"], []).append(card)
            packages.append(card)
        return {"columns": columns, "packages": packages}

    async def get_work_package_detail(self, package_id: str) -> dict | None:
        """Return package detail data for dashboard drilldown surfaces."""
        package = await self.get_work_package(package_id)
        if package is None:
            return None

        tasks = await self._db.execute_fetchall(
            "SELECT * FROM tasks WHERE work_package_id = ? ORDER BY created_at",
            (package_id,),
        )
        gate = await self._db.execute_fetchone(
            "SELECT * FROM review_gates "
            "WHERE entity_type = 'work_package' AND entity_id = ?",
            (package_id,),
        )
        integration_rows = await self._db.execute_fetchall(
            "SELECT * FROM merge_queue WHERE work_package_id = ? ORDER BY created_at",
            (package_id,),
        )

        now = datetime.now(timezone.utc)
        detail = self._enrich_work_package_card(package, tasks, gate, integration_rows, now)
        normalized_gate = detail.get("review_gate")
        normalized_tasks = [self._normalize_task_for_detail(task) for task in tasks]
        task_ids = [task["id"] for task in normalized_tasks]
        detail["tasks"] = normalized_tasks
        detail["review_runs"] = (
            normalized_gate.get("system_gate_runs", []) if normalized_gate else []
        )
        detail["revision_tasks"] = [
            task
            for task in normalized_tasks
            if task.get("revision_of")
            or task.get("review_parent_task_id")
            or task.get("task_type") == "revision"
        ]
        detail["artifacts"] = self._package_artifact_summaries(normalized_tasks)
        detail["dependencies"] = await self._package_dependencies(task_ids)
        detail["timeline"] = self._package_timeline(
            package,
            normalized_tasks,
            normalized_gate,
        )
        detail["recent_agent_activity"] = []
        return detail

    async def get_operations_summary(self, group_id: str | None = None) -> dict:
        """Return command-center counts and action queues."""
        board = await self.get_work_package_board(group_id=group_id)
        packages = board["packages"]

        def queue_item(package: dict) -> dict:
            return {
                "id": package["id"],
                "title": package.get("title"),
                "group_id": package.get("group_id"),
                "status": package.get("status"),
                "review_status": package.get("review_status"),
                "risk_level": package.get("risk_level"),
                "review_gate": package.get("review_gate"),
                "attention_reasons": package.get("attention_reasons", []),
                "waiting_age_seconds": package.get("waiting_age_seconds"),
            }

        review_queue = [
            queue_item(package)
            for package in packages
            if package.get("status") == "review"
            or (package.get("review_gate") or {}).get("status")
            in {"pending", "running", "failed"}
        ]
        blocked_queue = [
            queue_item(package) for package in packages if package.get("status") == "blocked"
        ]
        revision_queue = [
            queue_item(package)
            for package in packages
            if package.get("status") == "waiting_revision"
            or package.get("review_status") == "waiting_revision"
        ]
        stale_queue = [
            queue_item(package) for package in packages if package.get("stale") is True
        ]
        attention_queue = [
            queue_item(package)
            for package in packages
            if package.get("needs_attention") is True
        ]

        return {
            "counts": {
                "packages_total": len(packages),
                "packages_active": sum(
                    1
                    for package in packages
                    if package.get("status")
                    in {"backlog", "pending", "in_progress", PACKAGE_INTEGRATING_STATUS}
                ),
                "packages_blocked": len(blocked_queue),
                "packages_review": sum(
                    1 for package in packages if package.get("status") == "review"
                ),
                "packages_integrating": sum(
                    1
                    for package in packages
                    if package.get("status") == PACKAGE_INTEGRATING_STATUS
                ),
                "packages_waiting_revision": len(revision_queue),
                "packages_attention": len(attention_queue),
                "stale_gates": len(stale_queue),
            },
            "queues": {
                "review": review_queue,
                "blocked": blocked_queue,
                "revision": revision_queue,
                "stale": stale_queue,
                "attention": attention_queue,
            },
            "system_agent": self._system_agent_summary(packages),
            "recent_decisions": self._recent_package_review_decisions(packages),
        }

    def _enrich_work_package_card(
        self,
        package: dict,
        tasks: list[dict],
        gate: dict | None,
        integration_rows: list[dict] | None,
        now: datetime,
    ) -> dict:
        status_counts: dict[str, int] = {}
        for task in tasks:
            status = task.get("status") or "unknown"
            status_counts[status] = status_counts.get(status, 0) + 1
        task_counts = dict(status_counts)
        task_counts["total"] = sum(status_counts.values())

        card = dict(package)
        normalized_gate = self._normalize_review_gate(gate, now)
        normalized_integration = [dict(row) for row in (integration_rows or [])]
        attention_reasons = self._package_attention_reasons(
            card,
            tasks,
            normalized_gate,
            normalized_integration,
        )
        card["task_counts"] = task_counts
        card["review_gate"] = normalized_gate
        card["integration_queue"] = normalized_integration
        card["latest_integration"] = (
            normalized_integration[-1] if normalized_integration else None
        )
        card["needs_attention"] = bool(attention_reasons)
        card["attention_reasons"] = attention_reasons
        card["stale"] = any(
            reason.get("type") == "stale_gate" for reason in attention_reasons
        )
        card["active_gate"] = (
            "package_review"
            if normalized_gate and normalized_gate.get("status") == "running"
            else None
        )
        card["latest_review_reason_summary"] = self._summary_text(
            card.get("review_reason")
            or (normalized_gate or {}).get("reason")
            or self._latest_run_reason(normalized_gate)
        )
        card["waiting_age_seconds"] = self._age_seconds(
            card.get("updated_at") or card.get("created_at"),
            now,
        )
        return card

    def _normalize_review_gate(self, gate: dict | None, now: datetime) -> dict | None:
        if gate is None:
            return None
        normalized = dict(gate)
        runs = self._json_list(normalized.get("system_gate_runs"))
        age_seconds = self._age_seconds(
            normalized.get("updated_at") or normalized.get("created_at"),
            now,
        )
        status = normalized.get("status") or "pending"
        threshold = (
            PACKAGE_GATE_STALE_RUNNING_SECONDS
            if status == "running"
            else PACKAGE_GATE_STALE_PENDING_SECONDS
        )
        normalized["system_gate_runs"] = runs
        normalized["latest_run"] = runs[-1] if runs else None
        normalized["age_seconds"] = age_seconds
        normalized["stale"] = bool(
            status in {"pending", "running"} and age_seconds is not None
            and age_seconds > threshold
        )
        return normalized

    def _package_attention_reasons(
        self,
        package: dict,
        tasks: list[dict],
        gate: dict | None,
        integration_rows: list[dict] | None = None,
    ) -> list[dict]:
        reasons: list[dict] = []
        status = package.get("status")
        review_status = package.get("review_status")

        if status == "blocked":
            reasons.append({
                "type": "blocked",
                "severity": "high",
                "message": "Package is blocked by child task state.",
            })
        if status == "waiting_revision" or review_status == "waiting_revision":
            reasons.append({
                "type": "revision_needed",
                "severity": "high",
                "message": "System review requested revision work.",
            })
        if status == "review":
            gate_status = (gate or {}).get("status") or review_status or "pending"
            reasons.append({
                "type": "review_gate",
                "severity": "medium",
                "message": f"Package review gate is {gate_status}.",
            })
        if status == PACKAGE_INTEGRATING_STATUS:
            latest = (integration_rows or [])[-1] if integration_rows else {}
            integration_status = latest.get("status") or "pending"
            reasons.append({
                "type": "integration_pending",
                "severity": "medium",
                "message": (
                    "Approved package integration is "
                    f"{integration_status}."
                ),
            })
        for row in integration_rows or []:
            if row.get("status") in {"conflict", "blocked", "failed", "root_refresh_blocked"}:
                reasons.append({
                    "type": "integration_blocked",
                    "severity": "high",
                    "message": (
                        f"Integration {row.get('id')} is {row.get('status')}: "
                        f"{self._summary_text(row.get('last_error'))}"
                    ),
                })
                break
        if gate and gate.get("status") == "failed":
            reasons.append({
                "type": "review_failed",
                "severity": "high",
                "message": "System review attempt failed and needs retry.",
            })
        if gate and gate.get("stale"):
            reasons.append({
                "type": "stale_gate",
                "severity": "high",
                "message": "System review gate has been waiting too long.",
            })

        failed_statuses = {"failed", "rejected", "cancelled"}
        failed_tasks = [task for task in tasks if task.get("status") in failed_statuses]
        if failed_tasks:
            reasons.append({
                "type": "failed_child_task",
                "severity": "high",
                "message": f"{len(failed_tasks)} child task(s) are terminal failures.",
            })
        return reasons

    def _normalize_task_for_detail(self, task: dict) -> dict:
        normalized = dict(task)
        normalized["revision_task_ids"] = self._json_list(
            normalized.get("revision_task_ids")
        )
        normalized["system_gate_runs"] = self._json_list(
            normalized.get("system_gate_runs")
        )
        normalized["completion_checks"] = self._json_dict(
            normalized.get("completion_checks")
        )
        return normalized

    def _json_dict(self, value) -> dict:
        if value in (None, ""):
            return {}
        if isinstance(value, dict):
            return value
        try:
            parsed = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def _parse_timestamp(self, value) -> datetime | None:
        if not value:
            return None
        if isinstance(value, datetime):
            parsed = value
        else:
            raw = str(value)
            if raw.endswith("Z"):
                raw = raw[:-1] + "+00:00"
            try:
                parsed = datetime.fromisoformat(raw)
            except ValueError:
                return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    def _age_seconds(self, value, now: datetime) -> int | None:
        parsed = self._parse_timestamp(value)
        if parsed is None:
            return None
        return max(0, int((now - parsed).total_seconds()))

    def _summary_text(self, value, limit: int = 180) -> str:
        if not value:
            return ""
        text = " ".join(str(value).split())
        if len(text) <= limit:
            return text
        return text[: limit - 1].rstrip() + "…"

    def _latest_run_reason(self, gate: dict | None) -> str:
        if not gate:
            return ""
        latest = gate.get("latest_run") or {}
        return str(latest.get("reason") or "")

    def _package_artifact_summaries(self, tasks: list[dict]) -> list[dict]:
        artifacts: list[dict] = []
        for task in tasks:
            artifacts.append({
                "task_id": task.get("id"),
                "title": task.get("title"),
                "status": task.get("status"),
                "branch_name": task.get("branch_name"),
                "merge_status": task.get("merge_status"),
                "has_output": bool(task.get("output_text")),
                "completion_checks": task.get("completion_checks") or {},
            })
        return artifacts

    async def _package_dependencies(self, task_ids: list[str]) -> list[dict]:
        if not task_ids:
            return []
        placeholders = ",".join("?" * len(task_ids))
        rows = await self._db.execute_fetchall(
            "SELECT task_id, blocked_by, resolved, resolved_at "
            "FROM task_dependencies "
            f"WHERE task_id IN ({placeholders}) "
            "ORDER BY task_id, blocked_by",
            tuple(task_ids),
        )
        return [dict(row) for row in rows]

    def _package_timeline(
        self,
        package: dict,
        tasks: list[dict],
        gate: dict | None,
    ) -> list[dict]:
        events: list[dict] = [
            {
                "type": "package.created",
                "at": package.get("created_at"),
                "label": "Package created",
            }
        ]
        if package.get("updated_at") and package.get("updated_at") != package.get("created_at"):
            events.append({
                "type": "package.updated",
                "at": package.get("updated_at"),
                "label": f"Package moved to {package.get('status')}",
            })
        if gate:
            events.append({
                "type": "review_gate.status",
                "at": gate.get("updated_at") or gate.get("created_at"),
                "label": f"Review gate {gate.get('status')}",
            })
            for run in gate.get("system_gate_runs", []):
                events.append({
                    "type": f"review_gate.{run.get('outcome', 'run')}",
                    "at": run.get("finished_at") or run.get("started_at"),
                    "label": str(run.get("outcome") or "Review gate run"),
                })
        for task in tasks:
            events.append({
                "type": "task.created",
                "at": task.get("created_at"),
                "task_id": task.get("id"),
                "label": f"{task.get('id')} created",
            })
            events.append({
                "type": "task.status",
                "at": task.get("completed_at") or task.get("started_at") or task.get("created_at"),
                "task_id": task.get("id"),
                "label": f"{task.get('id')} is {task.get('status')}",
            })
        return sorted(events, key=lambda event: event.get("at") or "")

    def _system_agent_summary(self, packages: list[dict]) -> dict:
        for package in packages:
            gate = package.get("review_gate") or {}
            if gate.get("status") == "running":
                return {
                    "status": "working",
                    "current_gate": "package_review",
                    "current_entity_id": package.get("id"),
                    "current_task": None,
                    "status_detail": "reviewing package",
                    "label": f"Reviewing {package.get('id')}",
                }
        return {
            "status": "idle",
            "current_gate": None,
            "current_entity_id": None,
            "current_task": None,
            "status_detail": "idle",
            "label": "System agent idle",
        }

    def _recent_package_review_decisions(self, packages: list[dict]) -> list[dict]:
        decisions: list[dict] = []
        for package in packages:
            gate = package.get("review_gate") or {}
            for run in gate.get("system_gate_runs", []):
                at = run.get("finished_at") or run.get("started_at")
                decisions.append({
                    "package_id": package.get("id"),
                    "title": package.get("title"),
                    "outcome": run.get("outcome"),
                    "reason": run.get("reason"),
                    "at": at,
                })
        return sorted(
            decisions,
            key=lambda decision: decision.get("at") or "",
            reverse=True,
        )[:8]

    async def ensure_review_gate(
        self,
        *,
        entity_type: str,
        entity_id: str,
        group_id: str,
        max_rounds: int | None = None,
    ) -> dict:
        """Return or create the review gate for an entity."""
        existing = await self._db.execute_fetchone(
            "SELECT * FROM review_gates WHERE entity_type = ? AND entity_id = ?",
            (entity_type, entity_id),
        )
        if existing is not None:
            return existing

        await self._db.register_prefix("RG")
        gate_id = await self._db.generate_task_id("RG")
        now = _utcnow()
        await self._db.execute(
            "INSERT INTO review_gates "
            "(id, entity_type, entity_id, group_id, status, review_round, "
            "max_review_rounds, system_gate_runs, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 'pending', 0, ?, '[]', ?, ?)",
            (
                gate_id,
                entity_type,
                entity_id,
                group_id,
                normalize_max_review_rounds(max_rounds),
                now,
                now,
            ),
        )
        gate = await self._db.execute_fetchone(
            "SELECT * FROM review_gates WHERE id = ?", (gate_id,)
        )
        if gate is None:
            raise ValueError(f"Review gate not found after create: {gate_id}")
        return gate

    async def reopen_ready_work_package_review_gate(
        self,
        package_id: str,
    ) -> dict | None:
        """Reopen a package review gate after package revisions are integrated."""
        package = await self.get_work_package(package_id)
        if package is None:
            return None
        if package["status"] != "review" or package.get("review_status") != "pending":
            return None

        gate = await self.ensure_review_gate(
            entity_type="work_package",
            entity_id=package_id,
            group_id=package["group_id"],
            max_rounds=normalize_max_review_rounds(package.get("max_review_rounds")),
        )
        if gate.get("status") == "pending":
            return gate

        now = _utcnow()
        rows = await self._db.execute_returning(
            "UPDATE review_gates SET status = 'pending', outcome = NULL, "
            "reason = NULL, completed_at = NULL, review_round = ?, "
            "max_review_rounds = ?, updated_at = ? "
            "WHERE id = ? AND entity_type = 'work_package' "
            "AND status IN ('waiting_revision', 'failed') RETURNING *",
            (
                int(package.get("review_round") or 0),
                normalize_max_review_rounds(package.get("max_review_rounds")),
                now,
                gate["id"],
            ),
        )
        return rows[0] if rows else gate

    async def reconcile_work_package_status(self, package_id: str) -> dict | None:
        """Recompute a work package status from its child task statuses."""
        package = await self.get_work_package(package_id)
        if package is None:
            return None
        if package["status"] in ("review", "waiting_revision"):
            return package

        tasks = await self._db.execute_fetchall(
            "SELECT id, title, status, task_type, completion_checks, branch_name, "
            "parent_branch, created_at, completed_at FROM tasks "
            "WHERE work_package_id = ? ORDER BY created_at",
            (package_id,),
        )

        next_status = package["status"]
        next_review_status = package.get("review_status")
        task_statuses = [task["status"] for task in tasks]
        active_integration_task = any(
            task.get("task_type") == PACKAGE_INTEGRATION_TASK_TYPE
            and task.get("status") not in TERMINAL_STATUSES
            for task in tasks
        )
        waiting_package_revision = package.get("review_status") == "waiting_revision" and any(
            task.get("task_type") == "revision"
            and task.get("status") not in TERMINAL_STATUSES
            for task in tasks
        )

        if not tasks:
            next_status = "pending"
            next_review_status = None
        elif all(status == "rejected" for status in task_statuses):
            next_status = "rejected"
            next_review_status = "rejected"
        elif active_integration_task:
            next_status = PACKAGE_INTEGRATING_STATUS
            next_review_status = "integration_pending"
        elif any(status in ("failed", "rejected", "cancelled") for status in task_statuses):
            next_status = "blocked"
            next_review_status = None
        elif "blocked" in task_statuses:
            next_status = "blocked"
            next_review_status = None
        elif "in_progress" in task_statuses:
            next_status = "in_progress"
            next_review_status = "waiting_revision" if waiting_package_revision else None
        elif any(status in ("backlog", "pending") for status in task_statuses):
            next_status = "pending"
            next_review_status = "waiting_revision" if waiting_package_revision else None
        elif all(status == "completed" for status in task_statuses):
            if package.get("review_scope") == NO_REVIEW_SCOPE:
                next_status = "completed"
                next_review_status = "skipped"
            else:
                source_tasks = await self._package_source_tasks_for_integration(
                    package_id
                )
                if await self._package_needs_pre_review_integration(
                    package_id,
                    source_tasks,
                ):
                    await self._ensure_package_integration_task(package, source_tasks)
                    return await self.get_work_package(package_id)
                next_status = "review"
                next_review_status = "pending"

        now = _utcnow()
        rows = await self._db.execute_returning(
            "UPDATE work_packages SET status = ?, review_status = ?, updated_at = ? "
            "WHERE id = ? RETURNING *",
            (next_status, next_review_status, now, package_id),
        )
        updated = rows[0] if rows else await self.get_work_package(package_id)
        if updated and updated["status"] == "review":
            await self.ensure_review_gate(
                entity_type="work_package",
                entity_id=package_id,
                group_id=updated["group_id"],
                max_rounds=normalize_max_review_rounds(
                    updated.get("max_review_rounds")
                ),
            )
            await self.reopen_ready_work_package_review_gate(package_id)
        return updated

    async def reconcile_task_package(self, task_id: str) -> None:
        """Reconcile the package containing a task, if any."""
        task = await self._db.execute_fetchone(
            "SELECT work_package_id FROM tasks WHERE id = ?", (task_id,)
        )
        if task and task.get("work_package_id"):
            await self.reconcile_work_package_status(task["work_package_id"])

    async def ensure_default_work_package(self, group_id: str) -> dict:
        """Return or create the group's default work package."""
        group = await self._db.execute_fetchone(
            "SELECT * FROM groups WHERE id = ?", (group_id,)
        )
        if group is None:
            raise ValueError(f"Group not found: {group_id}")

        default_title = f"Default package for {group['title']}"
        default_description = DEFAULT_PACKAGE_DESCRIPTION
        package = await self._db.execute_fetchone(
            "SELECT * FROM work_packages "
            "WHERE group_id = ? AND milestone_id IS NULL "
            "AND created_by = 'system' AND title = ? AND description = ? "
            "ORDER BY created_at LIMIT 1",
            (group_id, default_title, default_description),
        )
        if package is not None:
            return package

        return await self.create_work_package(
            group_id=group_id,
            title=default_title,
            description=default_description,
            created_by="system",
            risk_level="medium",
            review_scope=DEFAULT_PACKAGE_REVIEW_SCOPE,
        )

    # ------------------------------------------------------------------
    # Tasks
    # ------------------------------------------------------------------

    async def create_task(
        self,
        group_id: str,
        title: str,
        task_type: str,
        assigned_to: str,
        created_by: str | None = None,
        description: str | None = None,
        priority: str = "medium",
        parent_id: str | None = None,
        revision_of: str | None = None,
        review_parent_task_id: str | None = None,
        blocked_by: list[str] | None = None,
        requires_fanout: bool | None = None,
        branch_name: str | None = None,
        parent_branch: str | None = None,
        work_package_id: str | None = None,
        work_package_title: str | None = None,
        work_package_description: str | None = None,
        milestone_id: str | None = None,
        review_scope: str | None = None,
    ) -> dict:
        """Create a new task with an auto-generated ID.

        The ID prefix is derived from the *assigned_to* role using the
        ``_role_to_prefix`` mapping populated by :meth:`register_prefixes`.

        Branch fields (``branch_name`` / ``parent_branch``) are
        minted here rather than reconstructed in the agent_loop:
        - ``branch_name`` defaults to ``feat/<task_id.lower()>`` so
          worker code has a single typed handle to the branch.
        - ``parent_branch`` defaults to ``main`` for a fresh task;
          for a revision (``revision_of`` set) it inherits the
          original task's ``branch_name`` so the fixer builds on
          the existing work instead of diverging from main.
        Callers can override either by passing an explicit value --
        the integrator role will want to pass ``parent_branch`` set
        to ``integration/<group_id>`` once that layer exists.
        """
        if work_package_id is None:
            package = await self.ensure_default_work_package(group_id)
            work_package_id = package["id"]
            milestone_id = milestone_id or package.get("milestone_id")
        else:
            package = await self.get_work_package(work_package_id)
            if package is None:
                raise ValueError(f"Work package not found: {work_package_id}")
            if package["group_id"] != group_id:
                raise ValueError(
                    f"Work package {work_package_id} does not belong to group {group_id}"
                )
            if work_package_title or work_package_description:
                package = await self.update_work_package_metadata(
                    work_package_id,
                    title=work_package_title,
                    description=work_package_description,
                ) or package
            milestone_id = milestone_id or package.get("milestone_id")

        prefix = self._role_to_prefix.get(assigned_to, assigned_to.upper()[:2])
        # Ensure the prefix is registered.
        await self._db.register_prefix(prefix)

        task_id = await self._db.generate_task_id(prefix)
        now = _utcnow()
        intended_status = "blocked" if blocked_by else "pending"
        status = BACKLOG_STATUS

        rf_stored: int | None
        if requires_fanout is None:
            rf_stored = None
        else:
            rf_stored = 1 if requires_fanout else 0

        # Mint branch metadata. The default name matches what
        # agent_loop used to compute inline, so existing deployments
        # see no behaviour change; the field just becomes
        # authoritative instead of reconstructed.
        if branch_name is None:
            branch_name = f"feat/{task_id.lower()}"
        if parent_branch is None and revision_of is not None:
            orig = await self._db.execute_fetchone(
                "SELECT branch_name FROM tasks WHERE id = ?",
                (revision_of,),
            )
            if orig and orig.get("branch_name") and self._branch_ref_exists(orig["branch_name"]):
                parent_branch = orig["branch_name"]
        if parent_branch is None:
            parent_branch = "main"

        await self._db.execute(
            "INSERT INTO tasks "
            "(id, group_id, parent_id, title, description, task_type, "
            " priority, assigned_to, status, created_by, created_at, "
            " revision_of, requires_fanout, branch_name, parent_branch, "
            " intended_status, backlog_intake_status, max_review_rounds, "
            " review_parent_task_id, work_package_id, milestone_id, review_scope) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                task_id,
                group_id,
                parent_id,
                title,
                description,
                task_type,
                priority,
                assigned_to,
                status,
                created_by,
                now,
                revision_of,
                rf_stored,
                branch_name,
                parent_branch,
                intended_status,
                "pending",
                DEFAULT_MAX_REVIEW_ROUNDS,
                review_parent_task_id,
                work_package_id,
                milestone_id,
                review_scope,
            ),
        )

        # Create dependency rows (with cycle detection).
        if blocked_by:
            for dep_id in blocked_by:
                if await self.has_cycle(task_id, dep_id):
                    raise ValueError(
                        f"Dependency {task_id} -> {dep_id} would create a cycle"
                    )
                blocker = await self._db.execute_fetchone(
                    "SELECT status FROM tasks WHERE id = ?",
                    (dep_id,),
                )
                dep_resolved = 1 if blocker and blocker["status"] == "completed" else 0
                dep_resolved_at = now if dep_resolved else None
                await self._db.execute(
                    "INSERT INTO task_dependencies "
                    "(task_id, blocked_by, resolved, resolved_at) "
                    "VALUES (?, ?, ?, ?)",
                    (task_id, dep_id, dep_resolved, dep_resolved_at),
                )

        task = {
            "id": task_id,
            "group_id": group_id,
            "work_package_id": work_package_id,
            "milestone_id": milestone_id,
            "parent_id": parent_id,
            "title": title,
            "description": description,
            "task_type": task_type,
            "priority": priority,
            "assigned_to": assigned_to,
            "claimed_by": None,
            "status": status,
            "intended_status": intended_status,
            "created_by": created_by,
            "created_at": now,
            "started_at": None,
            "completed_at": None,
            "rejection_reason": None,
            "revision_of": revision_of,
            "needs_review": None,
            "needs_review_reason": None,
            "needs_review_decision": None,
            "backlog_intake_status": "pending",
            "backlog_intake_processed_at": None,
            "review_status": None,
            "review_scope": review_scope,
            "review_round": 0,
            "max_review_rounds": DEFAULT_MAX_REVIEW_ROUNDS,
            "review_parent_task_id": review_parent_task_id,
            "revision_task_ids": [],
            "system_gate_runs": [],
            "requires_fanout": rf_stored,
            "fanout_retries": 0,
        }
        if blocked_by:
            reconciled = await self._reconcile_dependencies_after_create(task_id)
            fresh = reconciled or await self.get_task(task_id)
            if fresh:
                task.update(fresh)
        if task.get("work_package_id"):
            await self.reconcile_work_package_status(task["work_package_id"])
        return self._normalize_task_return(task)

    def _normalize_task_return(self, task: dict) -> dict:
        task["revision_task_ids"] = self._json_list(task.get("revision_task_ids"))
        task["system_gate_runs"] = self._json_list(task.get("system_gate_runs"))
        return task

    def _json_list(self, value) -> list:
        if value in (None, ""):
            return []
        if isinstance(value, list):
            return value
        try:
            parsed = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return []
        return parsed if isinstance(parsed, list) else []

    def _branch_ref_exists(self, branch_name: str) -> bool:
        if self._package_repo_dir is None:
            return True
        return self._git("rev-parse", "--verify", branch_name) == 0

    async def _has_unresolved_dependencies(self, task_id: str) -> bool:
        row = await self._db.execute_fetchone(
            "SELECT 1 FROM task_dependencies "
            "WHERE task_id = ? AND resolved = 0 LIMIT 1",
            (task_id,),
        )
        return row is not None

    async def _has_dependency_rows(self, task_id: str) -> bool:
        row = await self._db.execute_fetchone(
            "SELECT 1 FROM task_dependencies WHERE task_id = ? LIMIT 1",
            (task_id,),
        )
        return row is not None

    async def _target_status_after_intake(
        self, task_id: str, intended_status: str
    ) -> str:
        if await self._has_unresolved_dependencies(task_id):
            return "blocked"
        if intended_status == "blocked" and not await self._has_dependency_rows(task_id):
            return "blocked"
        return "pending" if intended_status in ("pending", "blocked") else intended_status

    async def _reconcile_dependencies_after_create(self, task_id: str) -> dict | None:
        now = _utcnow()
        await self._db.execute(
            "UPDATE task_dependencies SET resolved = 1, resolved_at = ? "
            "WHERE task_id = ? AND resolved = 0 "
            "AND blocked_by IN (SELECT id FROM tasks WHERE status = 'completed')",
            (now, task_id),
        )

        failed_blocker = await self._db.execute_fetchone(
            "SELECT 1 FROM task_dependencies d "
            "JOIN tasks blocker ON blocker.id = d.blocked_by "
            "WHERE d.task_id = ? AND d.resolved = 0 "
            "AND blocker.status IN ('failed', 'rejected') LIMIT 1",
            (task_id,),
        )
        if failed_blocker:
            failed_rows = await self._db.execute_returning(
                "UPDATE tasks SET status = 'failed' "
                "WHERE id = ? AND status IN ('backlog', 'blocked', 'pending') "
                "RETURNING *",
                (task_id,),
            )
            return failed_rows[0] if failed_rows else await self.get_task(task_id)

        if not await self._has_unresolved_dependencies(task_id):
            pending_rows = await self._db.execute_returning(
                "UPDATE tasks SET status = 'pending' "
                "WHERE id = ? AND status = 'blocked' RETURNING *",
                (task_id,),
            )
            if pending_rows:
                task = pending_rows[0]
                if self._event_bus is not None:
                    await self._event_bus.emit(
                        "task.available",
                        {
                            "task_id": task["id"],
                            "role": task["assigned_to"],
                            "group_id": task["group_id"],
                        },
                    )
                return task
        return None

    async def apply_backlog_intake_decision(
        self,
        task_id: str,
        *,
        needs_review: bool,
        reason: str,
        signals: list[str] | None = None,
        confidence: str = "medium",
    ) -> dict:
        """Store the one-time backlog intake decision and move to intended state."""
        task = await self.get_task(task_id)
        if task is None:
            raise ValueError(f"Task not found: {task_id}")
        if task["status"] != BACKLOG_STATUS:
            return task
        if task.get("backlog_intake_status") == "completed":
            return task

        now = _utcnow()
        decision = {
            "decision": bool(needs_review),
            "reason": reason,
            "signals": signals or [],
            "confidence": confidence,
            "decided_by": "system_agent",
            "decided_at": now,
        }
        intended_status = task.get("intended_status") or "pending"
        target_status = await self._target_status_after_intake(task_id, intended_status)
        rows = await self._db.execute_returning(
            "UPDATE tasks SET status = ?, needs_review = ?, needs_review_reason = ?, "
            "needs_review_decision = ?, backlog_intake_status = 'completed', "
            "backlog_intake_processed_at = ? "
            "WHERE id = ? AND status = 'backlog' "
            "AND COALESCE(backlog_intake_status, 'pending') != 'completed' RETURNING *",
            (
                target_status,
                1 if needs_review else 0,
                reason,
                json.dumps(decision),
                now,
                task_id,
            ),
        )
        updated = rows[0] if rows else await self.get_task(task_id)
        did_transition = bool(rows)
        emit_available = bool(
            did_transition and updated and updated["status"] == CLAIMABLE_STATUS
        )
        if (
            did_transition
            and updated
            and updated["status"] == "blocked"
            and await self._has_dependency_rows(task_id)
            and not await self._has_unresolved_dependencies(task_id)
        ):
            pending_rows = await self._db.execute_returning(
                "UPDATE tasks SET status = 'pending' "
                "WHERE id = ? AND status = 'blocked' RETURNING *",
                (task_id,),
            )
            if pending_rows:
                updated = pending_rows[0]
                emit_available = True
            else:
                updated = await self.get_task(task_id) or updated
                emit_available = False
        if (
            emit_available
            and updated
            and self._event_bus is not None
        ):
            await self._event_bus.emit(
                "task.available",
                {
                    "task_id": updated["id"],
                    "role": updated["assigned_to"],
                    "group_id": updated["group_id"],
                },
            )
        if updated and updated.get("work_package_id"):
            await self.reconcile_work_package_status(updated["work_package_id"])
        return updated

    async def mark_backlog_intake_failed(self, task_id: str, error: str) -> dict:
        now = _utcnow()
        rows = await self._db.execute_returning(
            "UPDATE tasks SET backlog_intake_status = 'failed', "
            "needs_review_reason = ? "
            "WHERE id = ? AND status = 'backlog' RETURNING *",
            (f"System backlog intake failed at {now}: {error[:500]}", task_id),
        )
        if not rows:
            task = await self.get_task(task_id)
            if task is None:
                raise ValueError(f"Task not found: {task_id}")
            return task
        return rows[0]

    async def get_task(self, task_id: str) -> dict | None:
        """Return a single task by ID, or None."""
        return await self._db.execute_fetchone(
            "SELECT * FROM tasks WHERE id = ?", (task_id,)
        )

    async def get_group_tasks(self, group_id: str) -> list[dict]:
        """Return all tasks belonging to a group."""
        return await self._db.execute_fetchall(
            "SELECT * FROM tasks WHERE group_id = ? ORDER BY created_at",
            (group_id,),
        )

    # ------------------------------------------------------------------
    # Usage tracking (public delegation)
    # ------------------------------------------------------------------

    async def record_task_usage(self, task_id: str, agent_id: str, **kwargs) -> None:
        """Record usage metrics for a task execution."""
        await self._db.record_task_usage(task_id=task_id, agent_id=agent_id, **kwargs)

    # ------------------------------------------------------------------
    # Claim / Complete / Reject / Fail
    # ------------------------------------------------------------------

    async def claim_task(
        self, role: str, instance_id: str
    ) -> dict | None:
        """Atomically claim the highest-priority pending task for *role*.

        Implemented as a single ``UPDATE ... WHERE id = (SELECT ... LIMIT 1)
        AND status='pending' AND claimed_by IS NULL RETURNING *`` statement.
        Atomicity comes from SQLite's statement-level write lock: the entire
        UPDATE (including its nested SELECT and the belt-and-suspenders
        predicate re-check on the outer WHERE) runs inside one implicit
        write transaction, so two concurrent claimers cannot both win the
        same row.

        This is the audit 03 F#1 fix. The previous SELECT-then-UPDATE pair
        ran on an autocommit aiosqlite connection (``isolation_level=None``
        in Database._create_connection) where the wrapping
        ``transaction()`` context opened a real BEGIN/COMMIT but, because
        the SELECT and UPDATE were two separate statements, another writer
        could interleave between them. A single-statement UPDATE avoids
        that class of race entirely; the outer AND-predicate guards the
        narrow window where the row could transition between the nested
        SELECT and the apply phase on the same statement under WAL.

        Scope note: the broader autocommit refactor flagged in audit 03 F#5
        is deferred. See Database._create_connection and Database.transaction
        for related hazards.

        Returns the claimed task dict, or ``None`` when the queue is empty.
        """
        # Build the priority CASE expression from hardcoded module state.
        # _PRIORITY_ORDER contains only trusted literal keys/values; if this
        # ever becomes user-configurable, precompute a constant (audit 03 F#2).
        priority_case = (
            "CASE priority "
            + " ".join(f"WHEN '{p}' THEN {v}" for p, v in _PRIORITY_ORDER.items())
            + " ELSE 99 END"
        )
        now = _utcnow()

        sql = (
            "UPDATE tasks SET claimed_by = ?, status = 'in_progress', started_at = ? "
            "WHERE id = ("
            "    SELECT id FROM tasks "
            "    WHERE assigned_to = ? AND status = 'pending' AND claimed_by IS NULL "
            "    AND NOT EXISTS ("
            "        SELECT 1 FROM task_dependencies d "
            "        WHERE d.task_id = tasks.id AND d.resolved = 0"
            "    ) "
            f"    ORDER BY {priority_case}, created_at "
            "    LIMIT 1"
            ") "
            "AND status = 'pending' AND claimed_by IS NULL "
            "AND NOT EXISTS ("
            "    SELECT 1 FROM task_dependencies d "
            "    WHERE d.task_id = tasks.id AND d.resolved = 0"
            ") "
            "RETURNING *"
        )
        rows = await self._db.execute_returning(sql, (instance_id, now, role))
        if not rows:
            return None
        result = rows[0]
        if result.get("work_package_id"):
            await self.reconcile_work_package_status(result["work_package_id"])
        logger.info("Task %s claimed by %s", result["id"], instance_id)
        return result

    def _completion_status_for(self, task: dict) -> tuple[str, str | None]:
        needs_review = task.get("needs_review")
        review_parent = task.get("review_parent_task_id")
        if needs_review in (1, True) and not review_parent:
            return REVIEW_STATUS, "pending"
        return "completed", None

    async def complete_task(self, task_id: str) -> dict:
        """Mark a task as completed and resolve downstream dependencies."""
        task = await self.get_task(task_id)
        if task is None:
            raise ValueError(f"Task not found: {task_id}")
        if task["status"] != "in_progress":
            logger.warning(
                "complete_task(%s) skipped: task is in status '%s', "
                "expected 'in_progress'",
                task_id,
                task["status"],
            )
            if task["status"] in ("completed", REVIEW_STATUS):
                await self.reconcile_task_package(task_id)
            return task

        now = _utcnow()
        target_status, review_status = self._completion_status_for(task)
        rows = await self._db.execute_returning(
            "UPDATE tasks SET status = ?, completed_at = ?, review_status = ? "
            "WHERE id = ? AND status = 'in_progress' RETURNING *",
            (target_status, now, review_status, task_id),
        )
        if not rows:
            existing = await self.get_task(task_id)
            if existing is None:
                raise ValueError(f"Task not found after completion race: {task_id}")
            logger.warning(
                "complete_task(%s) skipped: task is in status '%s', "
                "expected 'in_progress'",
                task_id,
                existing["status"],
            )
            if existing["status"] in ("completed", REVIEW_STATUS):
                await self.reconcile_task_package(task_id)
            return existing
        if target_status == "completed":
            await self.reconcile_task_package(task_id)
            await self._resolve_dependencies(task_id)
            await self._check_group_completion(task_id)
        else:
            await self.reconcile_task_package(task_id)
        logger.info("Task %s completed", task_id)
        return rows[0]

    async def complete_task_with_output(self, task_id: str, output: str) -> dict:
        """Mark task as completed and store the agent output.

        Agents may call the MCP ``complete_task`` tool before the
        orchestrator's own completion path runs. In that case the row is
        already ``completed`` by the time this method is called, but the
        final output still needs to be persisted for the dashboard artifact
        viewer. Treat completion as idempotent and attach output to completed
        rows instead of dropping it.
        """
        now = _utcnow()
        persisted_output = output or ""
        task = await self.get_task(task_id)
        if task is None:
            raise ValueError(f"Task not found: {task_id}")
        target_status, review_status = self._completion_status_for(task)
        rows = await self._db.execute_returning(
            "UPDATE tasks SET status = ?, completed_at = ?, output_text = ?, "
            "review_status = ? "
            "WHERE id = ? AND status = 'in_progress' RETURNING *",
            (target_status, now, persisted_output, review_status, task_id),
        )
        if not rows:
            existing = await self.get_task(task_id)
            if existing is None:
                raise ValueError(f"Task not found after completion race: {task_id}")
            if existing["status"] in ("completed", "review"):
                next_output = persisted_output or existing.get("output_text") or ""
                completed_rows = await self._db.execute_returning(
                    "UPDATE tasks SET output_text = ? "
                    "WHERE id = ? RETURNING *",
                    (next_output, task_id),
                )
                await self.reconcile_task_package(task_id)
                return completed_rows[0] if completed_rows else existing
            logger.warning(
                "complete_task_with_output(%s) skipped: task is in status '%s', "
                "expected 'in_progress'",
                task_id,
                existing["status"],
            )
            return existing
        if target_status == "completed":
            await self.reconcile_task_package(task_id)
            await self._resolve_dependencies(task_id)
            await self._check_group_completion(task_id)
        else:
            await self.reconcile_task_package(task_id)
        return rows[0]

    async def approve_review_gate(self, task_id: str, *, reason: str) -> dict:
        """Approve a task waiting at the system review gate."""
        task = await self.get_task(task_id)
        if task is None:
            raise ValueError(f"Task not found: {task_id}")
        if task["status"] != REVIEW_STATUS:
            return task
        if task.get("review_status") != "pending":
            return task
        if await self._has_unresolved_dependencies(task_id):
            return task

        now = _utcnow()
        rows = await self._db.execute_returning(
            "UPDATE tasks SET status = 'completed', review_status = 'approved', "
            "rejection_reason = NULL "
            "WHERE id = ? AND status = 'review' AND review_status = 'pending' "
            "AND NOT EXISTS ("
            "  SELECT 1 FROM task_dependencies "
            "  WHERE task_id = ? AND resolved = 0"
            ") RETURNING *",
            (task_id, task_id),
        )
        if not rows:
            task = await self.get_task(task_id)
            if task is None:
                raise ValueError(f"Task not found: {task_id}")
            return task
        await self._append_system_gate_run(
            task_id,
            {
                "gate": "review",
                "outcome": "approved",
                "reason": reason,
                "finished_at": now,
            },
        )
        await self.reconcile_task_package(task_id)
        await self._resolve_dependencies(task_id)
        await self._check_group_completion(task_id)
        fresh = await self.get_task(task_id)
        if fresh is None:
            raise ValueError(f"Task not found after review approval: {task_id}")
        return fresh

    async def reject_review_gate(self, task_id: str, *, reason: str) -> dict:
        """Reject a task waiting at the system review gate."""
        task = await self.get_task(task_id)
        if task is None:
            raise ValueError(f"Task not found: {task_id}")
        if task["status"] != REVIEW_STATUS:
            return task
        if task.get("review_status") not in ("pending", "running"):
            return task
        if await self._has_unresolved_dependencies(task_id):
            return task

        now = _utcnow()
        rows = await self._db.execute_returning(
            "UPDATE tasks SET status = 'rejected', review_status = 'rejected', "
            "rejection_reason = ? WHERE id = ? AND status = 'review' "
            "AND review_status IN ('pending', 'running') "
            "AND NOT EXISTS ("
            "  SELECT 1 FROM task_dependencies "
            "  WHERE task_id = ? AND resolved = 0"
            ") RETURNING *",
            (reason, task_id, task_id),
        )
        if not rows:
            task = await self.get_task(task_id)
            return task
        await self._append_system_gate_run(
            task_id,
            {
                "gate": "review",
                "outcome": "rejected",
                "reason": reason,
                "finished_at": now,
            },
        )
        await self._cascade_failure(task_id)
        await self.reconcile_task_package(task_id)
        await self._check_group_completion(task_id)
        fresh = await self.get_task(task_id)
        if fresh is None:
            raise ValueError(f"Task not found after review rejection: {task_id}")
        return fresh

    async def create_review_revision_tasks(
        self, original_task_id: str, revisions: list[dict]
    ) -> list[dict]:
        """Create revision tasks and block a review task until they complete."""
        original = await self.get_task(original_task_id)
        if original is None:
            raise ValueError(f"Task not found: {original_task_id}")
        if original["status"] != REVIEW_STATUS:
            raise ValueError(
                f"Task {original_task_id} is in status '{original['status']}', "
                f"expected '{REVIEW_STATUS}'"
            )
        if not revisions:
            raise ValueError("At least one revision is required")
        original_review_status = original.get("review_status") or "pending"
        original_review_round = int(original.get("review_round") or 0)
        max_review_rounds = normalize_max_review_rounds(
            original.get("max_review_rounds")
        )
        if max_review_rounds > 0 and original_review_round >= max_review_rounds:
            return []

        rows = await self._db.execute_returning(
            "UPDATE tasks SET review_status = 'waiting_revision', "
            "review_round = COALESCE(review_round, 0) + 1 "
            "WHERE id = ? AND status = 'review' "
            "AND review_status IN ('pending', 'running') "
            "AND NOT EXISTS ("
            "  SELECT 1 FROM task_dependencies "
            "  WHERE task_id = ? AND resolved = 0"
            ") "
            "AND (COALESCE(max_review_rounds, ?) <= 0 "
            "OR COALESCE(review_round, 0) < COALESCE(max_review_rounds, ?)) "
            "RETURNING *",
            (
                original_task_id,
                original_task_id,
                DEFAULT_MAX_REVIEW_ROUNDS,
                DEFAULT_MAX_REVIEW_ROUNDS,
            ),
        )
        if not rows:
            return []
        original = rows[0]

        created: list[dict] = []
        try:
            for index, revision in enumerate(revisions, start=1):
                revision_task = await self.create_task(
                    group_id=original["group_id"],
                    title=revision.get("title")
                    or f"Revision {index} for {original_task_id}",
                    task_type=revision.get("task_type") or "revision",
                    assigned_to=revision.get("assigned_to")
                    or original.get("assigned_to")
                    or "coder",
                    created_by="system",
                    description=revision.get("description")
                    or "Address the system review finding.",
                    priority=revision.get("priority")
                    or original.get("priority")
                    or "medium",
                    parent_id=original_task_id,
                    revision_of=original_task_id,
                    review_parent_task_id=original_task_id,
                )
                await self.add_dependency(original_task_id, revision_task["id"])
                created.append(revision_task)
        except BaseException:
            await self._rollback_empty_revision_transition(
                original_task_id,
                original_review_status,
                original_review_round,
            )
            raise

        revision_task_ids = self._json_list(original.get("revision_task_ids"))
        revision_task_ids.extend(task["id"] for task in created)
        await self._db.execute(
            "UPDATE tasks SET revision_task_ids = ? WHERE id = ?",
            (json.dumps(revision_task_ids), original_task_id),
        )
        await self._append_system_gate_run(
            original_task_id,
            {
                "gate": "review",
                "outcome": "needs_revision",
                "revision_task_ids": [task["id"] for task in created],
                "review_round": original["review_round"],
                "finished_at": _utcnow(),
            },
        )
        return created

    async def _rollback_empty_revision_transition(
        self, original_task_id: str, review_status: str, review_round: int
    ) -> None:
        task = await self.get_task(original_task_id)
        if task is None or task["status"] != REVIEW_STATUS:
            return
        if task.get("review_status") != "waiting_revision":
            return
        if self._json_list(task.get("revision_task_ids")):
            return
        dep = await self._db.execute_fetchone(
            "SELECT 1 FROM task_dependencies WHERE task_id = ? LIMIT 1",
            (original_task_id,),
        )
        if dep is not None:
            return
        await self._db.execute(
            "UPDATE tasks SET review_status = ?, review_round = ? "
            "WHERE id = ? AND status = 'review' AND review_status = 'waiting_revision'",
            (review_status, review_round, original_task_id),
        )

    async def mark_review_ready_if_unblocked(self, task_id: str) -> dict:
        """Move a waiting review task back to pending review when unblocked."""
        task = await self.get_task(task_id)
        if task is None:
            raise ValueError(f"Task not found: {task_id}")
        if task["status"] != REVIEW_STATUS:
            return task
        if await self._has_unresolved_dependencies(task_id):
            return task
        if task.get("review_status") != "waiting_revision":
            return task

        rows = await self._db.execute_returning(
            "UPDATE tasks SET review_status = 'pending' "
            "WHERE id = ? AND status = 'review' "
            "AND review_status = 'waiting_revision' RETURNING *",
            (task_id,),
        )
        return rows[0] if rows else await self.get_task(task_id)

    async def mark_review_failed(self, task_id: str, reason: str) -> dict:
        """Mark a review gate attempt as failed without rejecting the task."""
        task = await self.get_task(task_id)
        if task is None:
            raise ValueError(f"Task not found: {task_id}")
        if task["status"] != REVIEW_STATUS:
            return task
        if task.get("review_status") not in ("pending", "running", "failed"):
            return task
        if await self._has_unresolved_dependencies(task_id):
            return task

        now = _utcnow()
        runs = self._json_list(task.get("system_gate_runs"))
        runs.append(
            {
                "gate": "review",
                "outcome": "failed_review",
                "reason": reason[:1000],
                "finished_at": now,
            }
        )
        rows = await self._db.execute_returning(
            "UPDATE tasks SET review_status = 'failed', system_gate_runs = ? "
            "WHERE id = ? AND status = 'review' "
            "AND review_status IN ('pending', 'running', 'failed') "
            "AND NOT EXISTS ("
            "  SELECT 1 FROM task_dependencies "
            "  WHERE task_id = ? AND resolved = 0"
            ") RETURNING *",
            (json.dumps(runs), task_id, task_id),
        )
        if not rows:
            fresh = await self.get_task(task_id)
            if fresh is None:
                raise ValueError(f"Task not found: {task_id}")
            return fresh
        await self.reconcile_task_package(task_id)
        return rows[0]

    async def create_package_revision_tasks(
        self,
        package_id: str,
        revisions: list[dict],
        *,
        reason: str,
    ) -> list[dict]:
        """Create package-level revision tasks after a package review finding."""
        package = await self.get_work_package(package_id)
        if package is None:
            raise ValueError(f"Work package not found: {package_id}")
        if package["status"] not in ("review", "waiting_revision"):
            return []
        if not revisions:
            raise ValueError("At least one package revision is required")

        review_round = int(package.get("review_round") or 0)
        max_review_rounds = normalize_max_review_rounds(
            package.get("max_review_rounds")
        )
        if max_review_rounds > 0 and review_round >= max_review_rounds:
            return []

        now = _utcnow()
        rows = await self._db.execute_returning(
            "UPDATE work_packages SET status = 'pending', "
            "review_status = 'waiting_revision', review_reason = ?, "
            "review_round = COALESCE(review_round, 0) + 1, updated_at = ? "
            "WHERE id = ? AND status IN ('review', 'waiting_revision') "
            "AND (COALESCE(max_review_rounds, ?) <= 0 "
            "OR COALESCE(review_round, 0) < COALESCE(max_review_rounds, ?)) "
            "RETURNING *",
            (
                reason,
                now,
                package_id,
                DEFAULT_MAX_REVIEW_ROUNDS,
                DEFAULT_MAX_REVIEW_ROUNDS,
            ),
        )
        if not rows:
            return []
        updated = rows[0]

        created: list[dict] = []
        try:
            for index, revision in enumerate(revisions, start=1):
                revision_task = await self.create_task(
                    group_id=updated["group_id"],
                    title=revision.get("title")
                    or f"Package revision {index} for {package_id}",
                    task_type=revision.get("task_type") or "revision",
                    assigned_to=revision.get("assigned_to") or "coder",
                    created_by="system",
                    description=revision.get("description")
                    or reason
                    or "Address the package review finding.",
                    priority=revision.get("priority") or "high",
                    work_package_id=package_id,
                    milestone_id=updated.get("milestone_id"),
                    review_scope=NO_REVIEW_SCOPE,
                )
                created.append(revision_task)
        except BaseException:
            await self._db.execute(
                "UPDATE work_packages SET status = 'review', "
                "review_status = 'running', "
                "review_round = ?, updated_at = ? WHERE id = ?",
                (review_round, _utcnow(), package_id),
            )
            raise

        await self.reconcile_work_package_status(package_id)
        return created

    async def _package_needs_pre_review_integration(
        self,
        package_id: str,
        source_tasks: list[dict],
    ) -> bool:
        if self._package_repo_dir is None or not source_tasks:
            return False
        if await self._package_open_integration_task(package_id) is not None:
            return False

        latest_integration = await self._latest_completed_package_integration_task(
            package_id
        )
        if latest_integration is None:
            return True

        integration_done_at = self._task_finished_or_created_at(latest_integration)
        return any(
            self._task_finished_or_created_at(task) > integration_done_at
            for task in source_tasks
        )

    async def _ensure_package_integration_task(
        self,
        package: dict,
        source_tasks: list[dict],
    ) -> dict | None:
        package_id = package["id"]
        existing = await self._package_open_integration_task(package_id)
        if existing is not None:
            return existing
        if not source_tasks:
            return None

        package_branch = package.get("branch_name") or self._package_branch_name(
            package_id
        )
        parent_branch = self._package_parent_branch_for_sources(source_tasks)
        description = self._package_integration_task_description(
            package,
            source_tasks,
            package_branch=package_branch,
            parent_branch=parent_branch,
        )

        now = _utcnow()
        await self._db.execute(
            "UPDATE work_packages SET status = ?, review_status = ?, "
            "branch_name = ?, updated_at = ? WHERE id = ?",
            (
                PACKAGE_INTEGRATING_STATUS,
                "integration_pending",
                package_branch,
                now,
                package_id,
            ),
        )
        return await self.create_task(
            group_id=package["group_id"],
            title=f"Integrate package {package_id} for review",
            task_type=PACKAGE_INTEGRATION_TASK_TYPE,
            assigned_to="coder",
            created_by="system",
            description=description,
            priority="high",
            branch_name=package_branch,
            parent_branch=parent_branch,
            work_package_id=package_id,
            milestone_id=package.get("milestone_id"),
            review_scope=NO_REVIEW_SCOPE,
        )

    def _package_branch_name(self, package_id: str) -> str:
        return f"package/{package_id.lower()}"

    def _package_parent_branch_for_sources(self, source_tasks: list[dict]) -> str:
        targets = {
            self._package_target_branch(task)
            for task in source_tasks
            if task.get("branch_name")
        }
        if len(targets) == 1:
            return next(iter(targets))
        return "main"

    def _package_integration_task_description(
        self,
        package: dict,
        source_tasks: list[dict],
        *,
        package_branch: str,
        parent_branch: str,
    ) -> str:
        source_lines = []
        for task in source_tasks:
            source_lines.append(
                f"- {task['id']}: {task.get('branch_name')} ({task.get('title')})"
            )
        sources = "\n".join(source_lines) or "- No source branches found."
        return (
            f"Integrate Work Package {package['id']} before package review.\n\n"
            f"Work package: {package.get('title') or package['id']}\n"
            f"Target package branch: {package_branch}\n"
            f"Base branch: {parent_branch}\n\n"
            f"Source task branches:\n{sources}\n\n"
            "Instructions:\n"
            "1. Create or update the package branch from the base branch.\n"
            "2. Merge or replay each source task branch into the package branch.\n"
            "3. Resolve conflicts, run relevant tests, and commit the integrated result.\n"
            "4. Complete this task only after the package branch contains all "
            "source deliverables."
        )

    def _task_finished_or_created_at(self, task: dict) -> str:
        return task.get("completed_at") or task.get("created_at") or ""

    async def _package_open_integration_task(self, package_id: str) -> dict | None:
        return await self._db.execute_fetchone(
            "SELECT id, group_id, title, task_type, status, branch_name, "
            "parent_branch, created_at, completed_at "
            "FROM tasks WHERE work_package_id = ? AND task_type = ? "
            "AND status NOT IN ('completed', 'failed', 'cancelled', 'rejected') "
            "ORDER BY created_at DESC LIMIT 1",
            (package_id, PACKAGE_INTEGRATION_TASK_TYPE),
        )

    async def _latest_completed_package_integration_task(
        self,
        package_id: str,
    ) -> dict | None:
        return await self._db.execute_fetchone(
            "SELECT id, group_id, title, task_type, status, branch_name, "
            "parent_branch, created_at, completed_at "
            "FROM tasks WHERE work_package_id = ? AND task_type = ? "
            "AND status = 'completed' AND branch_name IS NOT NULL "
            "AND branch_name != '' "
            "ORDER BY completed_at DESC, created_at DESC LIMIT 1",
            (package_id, PACKAGE_INTEGRATION_TASK_TYPE),
        )

    async def _package_source_tasks_for_integration(
        self,
        package_id: str,
    ) -> list[dict]:
        if self._package_repo_dir is None:
            return []
        task_type_placeholders = ",".join("?" for _ in PACKAGE_INTEGRATION_TASK_TYPES)
        return await self._db.execute_fetchall(
            "SELECT id, group_id, title, task_type, status, branch_name, "
            "parent_branch, created_at, completed_at "
            "FROM tasks WHERE work_package_id = ? AND status = 'completed' "
            "AND branch_name IS NOT NULL AND branch_name != '' "
            f"AND task_type IN ({task_type_placeholders}) "
            "ORDER BY created_at",
            (package_id, *sorted(PACKAGE_INTEGRATION_TASK_TYPES)),
        )

    async def _queue_work_package_integration(self, package_id: str) -> list[dict]:
        if self._package_merge_queue is None:
            return []
        rows: list[dict] = []
        for task in await self._package_integration_tasks(package_id):
            source_branch = task.get("branch_name")
            if not source_branch:
                continue
            target_branch = self._package_target_branch(task)
            if source_branch == target_branch:
                continue
            if await self._branch_is_integrated(
                source_branch,
                target_branch,
                task_id=task["id"],
            ):
                continue
            rows.append(
                await self._package_merge_queue.enqueue_package_integration(
                    group_id=task["group_id"],
                    work_package_id=package_id,
                    parent_task_id=task["id"],
                    source_branch=source_branch,
                    target_branch=target_branch,
                )
            )
        return rows

    async def _package_integration_ready(self, package_id: str) -> bool:
        tasks = await self._package_integration_tasks(package_id)
        if not tasks:
            return True

        task_ids = [task["id"] for task in tasks]
        placeholders = ",".join("?" for _ in task_ids)
        open_statuses = ",".join("?" for _ in MERGE_QUEUE_OPEN_STATUSES)
        open_row = await self._db.execute_fetchone(
            "SELECT 1 FROM merge_queue "
            f"WHERE parent_task_id IN ({placeholders}) "
            f"AND status IN ({open_statuses}) LIMIT 1",
            (*task_ids, *MERGE_QUEUE_OPEN_STATUSES),
        )
        if open_row is not None:
            return False

        for task in tasks:
            source_branch = task.get("branch_name")
            if not source_branch:
                continue
            target_branch = self._package_target_branch(task)
            if source_branch == target_branch:
                continue
            if not await self._branch_is_integrated(
                source_branch,
                target_branch,
                task_id=task["id"],
            ):
                return False
        return True

    async def _package_integration_tasks(self, package_id: str) -> list[dict]:
        if self._package_repo_dir is None:
            return []
        package_branch_tasks = await self._db.execute_fetchall(
            "SELECT id, group_id, task_type, status, branch_name, parent_branch "
            "FROM tasks WHERE work_package_id = ? AND task_type = ? "
            "AND status = 'completed' AND branch_name IS NOT NULL "
            "AND branch_name != '' ORDER BY completed_at DESC, created_at DESC",
            (package_id, PACKAGE_INTEGRATION_TASK_TYPE),
        )
        if package_branch_tasks:
            return [package_branch_tasks[0]]
        return await self._package_source_tasks_for_integration(package_id)

    def _package_target_branch(self, task: dict) -> str:
        parent_branch = task.get("parent_branch") or "main"
        if parent_branch.startswith(("feat/", "fix/", "bugfix/")):
            return "main"
        return parent_branch

    async def _branch_is_integrated(
        self,
        source_branch: str,
        target_branch: str,
        *,
        task_id: str,
    ) -> bool:
        if self._package_repo_dir is None:
            return True

        if self._git("merge-base", "--is-ancestor", source_branch, target_branch) == 0:
            return True

        if self._git("rev-parse", "--verify", source_branch) == 0:
            return False

        success_statuses = ",".join("?" for _ in MERGE_QUEUE_SUCCESS_STATUSES)
        row = await self._db.execute_fetchone(
            "SELECT 1 FROM merge_queue "
            "WHERE parent_task_id = ? AND source_branch = ? AND target_branch = ? "
            f"AND status IN ({success_statuses}) LIMIT 1",
            (task_id, source_branch, target_branch, *MERGE_QUEUE_SUCCESS_STATUSES),
        )
        return row is not None

    def _git(self, *args: str) -> int:
        if self._package_repo_dir is None:
            return 1
        proc = subprocess.run(
            ["git", *args],
            cwd=self._package_repo_dir,
            text=True,
            capture_output=True,
            check=False,
        )
        return proc.returncode

    async def approve_work_package_review(
        self, package_id: str, *, reason: str
    ) -> dict:
        """Approve a work package waiting at the system review gate."""
        package = await self.get_work_package(package_id)
        if package is None:
            raise ValueError(f"Work package not found: {package_id}")
        if package["status"] != "review":
            return package

        await self._queue_work_package_integration(package_id)
        integration_ready = await self._package_integration_ready(package_id)
        next_status = "completed" if integration_ready else PACKAGE_INTEGRATING_STATUS
        now = _utcnow()
        completed_at = now if integration_ready else None
        rows = await self._db.execute_returning(
            "UPDATE work_packages SET status = ?, "
            "review_status = 'approved', review_reason = ?, completed_at = ?, "
            "updated_at = ? WHERE id = ? AND status = 'review' RETURNING *",
            (next_status, reason, completed_at, now, package_id),
        )
        if not rows:
            fresh = await self.get_work_package(package_id)
            if fresh is None:
                raise ValueError(f"Work package not found: {package_id}")
            return fresh

        gate = await self._db.execute_fetchone(
            "SELECT id FROM review_gates "
            "WHERE entity_type = 'work_package' AND entity_id = ?",
            (package_id,),
        )
        if gate is not None:
            await self._db.execute(
                "UPDATE review_gates SET status = 'completed', outcome = 'approved', "
                "reason = ?, completed_at = ?, updated_at = ? WHERE id = ?",
                (reason, now, now, gate["id"]),
            )
            await self._append_review_gate_run(
                gate["id"],
                {
                    "gate": "package_review",
                    "outcome": "approved",
                    "reason": reason,
                    "finished_at": now,
                },
            )
        await self._check_group_completion_for_group(rows[0]["group_id"])
        fresh = await self.get_work_package(package_id)
        if fresh is None:
            raise ValueError(f"Work package not found after review approval: {package_id}")
        return fresh

    async def reject_work_package_review(
        self, package_id: str, *, reason: str
    ) -> dict:
        """Reject a work package waiting at the system review gate."""
        package = await self.get_work_package(package_id)
        if package is None:
            raise ValueError(f"Work package not found: {package_id}")
        if package["status"] not in ("review", "waiting_revision"):
            return package

        now = _utcnow()
        rows = await self._db.execute_returning(
            "UPDATE work_packages SET status = 'rejected', "
            "review_status = 'rejected', review_reason = ?, updated_at = ? "
            "WHERE id = ? AND status IN ('review', 'waiting_revision') RETURNING *",
            (reason, now, package_id),
        )
        if not rows:
            fresh = await self.get_work_package(package_id)
            if fresh is None:
                raise ValueError(f"Work package not found: {package_id}")
            return fresh

        gate = await self._db.execute_fetchone(
            "SELECT id FROM review_gates "
            "WHERE entity_type = 'work_package' AND entity_id = ?",
            (package_id,),
        )
        if gate is not None:
            await self._db.execute(
                "UPDATE review_gates SET status = 'completed', outcome = 'rejected', "
                "reason = ?, completed_at = ?, updated_at = ? WHERE id = ?",
                (reason, now, now, gate["id"]),
            )
            await self._append_review_gate_run(
                gate["id"],
                {
                    "gate": "package_review",
                    "outcome": "rejected",
                    "reason": reason,
                    "finished_at": now,
                },
            )
        await self._check_group_completion_for_group(rows[0]["group_id"])
        fresh = await self.get_work_package(package_id)
        if fresh is None:
            raise ValueError(f"Work package not found after review rejection: {package_id}")
        return fresh

    async def _append_system_gate_run(self, task_id: str, entry: dict) -> None:
        task = await self.get_task(task_id)
        if task is None:
            raise ValueError(f"Task not found: {task_id}")
        runs = self._json_list(task.get("system_gate_runs"))
        runs.append(entry)
        await self._db.execute(
            "UPDATE tasks SET system_gate_runs = ? WHERE id = ?",
            (json.dumps(runs), task_id),
        )

    async def _append_review_gate_run(self, gate_id: str, run: dict) -> None:
        gate = await self._db.execute_fetchone(
            "SELECT system_gate_runs FROM review_gates WHERE id = ?", (gate_id,)
        )
        if gate is None:
            raise ValueError(f"Review gate not found: {gate_id}")
        runs = self._json_list(gate.get("system_gate_runs"))
        runs.append(run)
        await self._db.execute(
            "UPDATE review_gates SET system_gate_runs = ?, updated_at = ? WHERE id = ?",
            (json.dumps(runs), _utcnow(), gate_id),
        )

    async def reject_task(self, task_id: str, reason: str) -> dict:
        """Mark a task as rejected with a reason."""
        rows = await self._db.execute_returning(
            "UPDATE tasks SET status = 'rejected', rejection_reason = ? "
            "WHERE id = ? RETURNING *",
            (reason, task_id),
        )
        if not rows:
            raise ValueError(f"Task not found: {task_id}")
        await self._cascade_failure(task_id)
        await self.reconcile_task_package(task_id)
        await self._check_group_completion(task_id)
        return rows[0]

    async def fail_task(self, task_id: str, reason: str | None = None) -> dict:
        """Mark a task as failed and cascade failure to blocked dependents."""
        rows = await self._db.execute_returning(
            "UPDATE tasks SET status = 'failed', rejection_reason = ? "
            "WHERE id = ? AND status = 'in_progress' RETURNING *",
            (reason, task_id),
        )
        if not rows:
            existing = await self._db.execute_fetchone(
                "SELECT * FROM tasks WHERE id = ?", (task_id,)
            )
            if existing is None:
                raise ValueError(f"Task not found: {task_id}")
            logger.warning(
                "fail_task(%s) skipped: task is in status '%s', "
                "expected 'in_progress'",
                task_id,
                existing["status"],
            )
            await self.reconcile_task_package(task_id)
            return existing
        await self._cascade_failure(task_id)
        # audit 03 F#16: previously this only cancelled 'pending' children,
        # leaving blocked children orphaned when the parent fails. A child
        # linked purely by parent_id (no task_dependencies row) can still
        # be blocked -- cascade to those too.
        await self._db.execute(
            "UPDATE tasks SET status = 'cancelled' "
            "WHERE parent_id = ? AND status IN ('backlog', 'pending', 'blocked')",
            (task_id,),
        )
        await self.reconcile_task_package(task_id)
        await self._check_group_completion(task_id)
        logger.info("Task %s failed", task_id)
        return rows[0]

    async def _cascade_failure(self, task_id: str) -> None:
        """When a task fails, fail all blocked tasks that depend on it.

        This prevents downstream tasks from being stuck in 'blocked' forever.
        Uses iterative BFS to cascade through the entire dependency chain.
        """
        queue: deque[str] = deque([task_id])
        while queue:
            current = queue.popleft()
            dependents = await self._db.execute_fetchall(
                "SELECT task_id FROM task_dependencies WHERE blocked_by = ? AND resolved = 0",
                (current,),
            )
            for dep in dependents:
                review_parent = await self._db.execute_fetchone(
                    "SELECT * FROM tasks "
                    "WHERE id = ? AND status = 'review' "
                    "AND review_status = 'waiting_revision'",
                    (dep["task_id"],),
                )
                if review_parent:
                    rejected = await self._reject_waiting_revision_parent(
                        review_parent["id"], current
                    )
                    if rejected:
                        queue.append(review_parent["id"])
                    continue

                dep_task = await self._db.execute_fetchone(
                    "SELECT * FROM tasks "
                    "WHERE id = ? AND status IN ('backlog', 'pending', 'blocked')",
                    (dep["task_id"],),
                )
                if dep_task:
                    await self._db.execute(
                        "UPDATE tasks SET status = 'failed' WHERE id = ?",
                        (dep_task["id"],),
                    )
                    queue.append(dep_task["id"])

    async def _reject_waiting_revision_parent(
        self, parent_task_id: str, failed_revision_task_id: str
    ) -> dict | None:
        """Reject a review parent whose required revision failed or was rejected."""
        now = _utcnow()
        reason = (
            f"Required revision task {failed_revision_task_id} failed or was rejected; "
            "the review cannot continue."
        )
        rows = await self._db.execute_returning(
            "UPDATE tasks SET status = 'rejected', review_status = 'rejected', "
            "rejection_reason = ? "
            "WHERE id = ? AND status = 'review' "
            "AND review_status = 'waiting_revision' RETURNING *",
            (reason, parent_task_id),
        )
        if not rows:
            return None
        await self._append_system_gate_run(
            parent_task_id,
            {
                "gate": "review",
                "outcome": "rejected",
                "reason": reason,
                "failed_revision_task_id": failed_revision_task_id,
                "finished_at": now,
            },
        )
        return rows[0]

    async def _reject_review_for_terminal_dependency(
        self, review_task_id: str, failed_dependency_task_id: str
    ) -> dict | None:
        """Reject a review task blocked by an already failed or rejected dependency."""
        now = _utcnow()
        reason = (
            f"Terminal dependency task {failed_dependency_task_id} failed or was "
            "rejected; the review cannot proceed."
        )
        rows = await self._db.execute_returning(
            "UPDATE tasks SET status = 'rejected', review_status = 'rejected', "
            "rejection_reason = ? "
            "WHERE id = ? AND status = 'review' "
            "AND review_status IN ('pending', 'waiting_revision') RETURNING *",
            (reason, review_task_id),
        )
        if not rows:
            return None
        await self._append_system_gate_run(
            review_task_id,
            {
                "gate": "review",
                "outcome": "rejected",
                "reason": reason,
                "failed_dependency_task_id": failed_dependency_task_id,
                "finished_at": now,
            },
        )
        return rows[0]

    # ------------------------------------------------------------------
    # Group completion check
    # ------------------------------------------------------------------

    async def _check_group_completion(self, task_id: str) -> None:
        """Check if all tasks in the group are in terminal states.

        If every task in the group has status ``completed``, ``failed``, or
        ``cancelled``, the group is marked as ``completed`` with the current
        timestamp. Also triggers Stage-1 Fix #4 (PM goal-verification) before
        the group is sealed.
        """
        # Look up the group_id for this task.
        task = await self._db.execute_fetchone(
            "SELECT group_id FROM tasks WHERE id = ?", (task_id,)
        )
        if not task or not task["group_id"]:
            return

        group_id = task["group_id"]

        await self._check_group_completion_for_group(group_id)

    async def _check_group_completion_for_group(self, group_id: str) -> None:
        """Check if all tasks and packages in a group are terminal."""

        # Check whether any task in this group is NOT in a terminal state.
        non_terminal = await self._db.execute_fetchone(
            "SELECT 1 FROM tasks WHERE group_id = ? "
            "AND status NOT IN ('completed', 'failed', 'cancelled', 'rejected') LIMIT 1",
            (group_id,),
        )
        if non_terminal:
            return

        await self._finalize_integrated_work_packages_for_group(group_id)

        open_package = await self._db.execute_fetchone(
            "SELECT 1 FROM work_packages WHERE group_id = ? "
            "AND status NOT IN ('completed', 'rejected') LIMIT 1",
            (group_id,),
        )
        if open_package:
            return

        # Durable merge queue gate. A task can be terminal while its
        # approved branch is still waiting to land on the target branch
        # or waiting for the visible checkout to refresh. Do not seal
        # the group until integration is resolved.
        open_merge = await self._db.execute_fetchone(
            "SELECT 1 FROM merge_queue WHERE group_id = ? "
            "AND status IN ("
            "'queued', 'running', 'retry_pending', 'blocked', "
            "'conflict', 'root_refresh_blocked', 'failed'"
            ") LIMIT 1",
            (group_id,),
        )
        if open_merge:
            return

        # --- Stage-1 Fix #4: PM goal-verification trigger ---
        # Before we seal the group, spawn a final PM task that re-reads every
        # child output and confirms the original goal was actually met.
        # confido shipped FEAT-001 "complete" despite AR-006 never creating the
        # CLI coder tasks — this gate would have caught that.
        if await self._maybe_spawn_goal_verification(group_id):
            # A new pending task was just created, so the group is no longer
            # fully terminal; bail out and let the next completion re-check.
            return

        # All tasks are terminal -- mark the group as completed.
        now = _utcnow()
        await self._db.execute(
            "UPDATE groups SET status = 'completed', completed_at = ? "
            "WHERE id = ? AND status = 'active'",
            (now, group_id),
        )

    async def _finalize_integrated_work_packages_for_group(self, group_id: str) -> None:
        packages = await self._db.execute_fetchall(
            "SELECT id FROM work_packages WHERE group_id = ? AND status = ?",
            (group_id, PACKAGE_INTEGRATING_STATUS),
        )
        for package in packages:
            if not await self._package_integration_ready(package["id"]):
                continue
            now = _utcnow()
            await self._db.execute(
                "UPDATE work_packages SET status = 'completed', completed_at = ?, "
                "updated_at = ? WHERE id = ? AND status = ?",
                (now, now, package["id"], PACKAGE_INTEGRATING_STATUS),
            )

    async def _maybe_spawn_goal_verification(self, group_id: str) -> bool:
        """Create a PM ``goal_verification`` task if the conditions are met.

        Returns True if a new task was spawned (caller should treat the group
        as still active). Returns False when skipped — no verification needed
        or already performed.
        """
        # Package-backed groups are verified by Work Package review gates.
        # Keep the older PM goal-verification fallback only for legacy groups
        # that do not have package records.
        has_package = await self._db.execute_fetchone(
            "SELECT 1 FROM work_packages WHERE group_id = ? LIMIT 1",
            (group_id,),
        )
        if has_package:
            return False

        # Dedup: never spawn more than one goal_verification per group.
        already = await self._db.execute_fetchone(
            "SELECT id FROM tasks WHERE group_id = ? "
            "AND task_type = 'goal_verification' LIMIT 1",
            (group_id,),
        )
        if already:
            return False

        # Need a PM goal task to verify against.
        pm_goal = await self._db.execute_fetchone(
            "SELECT id, title, description FROM tasks "
            "WHERE group_id = ? AND task_type = 'goal' LIMIT 1",
            (group_id,),
        )
        if not pm_goal:
            return False

        # Skip trivial groups — a docs-only goal like FEAT-002 ("create README")
        # has 4-5 tasks and doesn't need a second PM pass.
        group_size_row = await self._db.execute_fetchone(
            "SELECT COUNT(*) AS n FROM tasks WHERE group_id = ?",
            (group_id,),
        )
        if not group_size_row or (group_size_row["n"] or 0) < 5:
            return False

        await self.create_task(
            group_id=group_id,
            title=f"Goal verification for {pm_goal['id']}",
            task_type="goal_verification",
            assigned_to="pm",
            created_by="system",
            parent_id=pm_goal["id"],
            priority="high",
            description=(
                f"Every task in group {group_id} has reached a terminal state.\n\n"
                f"Re-read the original PRD (task {pm_goal['id']}), then walk the "
                f"child task outputs and on-disk artifacts. Confirm that each "
                f"deliverable named in the original goal actually exists and is "
                f"wired up end-to-end. For every gap, create a revision task "
                f"routed to the correct role.\n\n"
                f"This verification task itself does NOT need to fan out further "
                f"if everything checks out — complete with a short summary."
            ),
            # Goal-verification is itself a design/review task; no sub-fan-out.
            requires_fanout=False,
        )
        return True

    # ------------------------------------------------------------------
    # Dependency resolution
    # ------------------------------------------------------------------

    async def _resolve_dependencies(self, completed_task_id: str) -> None:
        """Resolve dependencies after a task completes.

        1. Mark all ``task_dependencies`` rows where ``blocked_by`` equals the
           completed task as ``resolved = 1``.
        2. Find any blocked tasks that now have *zero* unresolved dependencies
           and transition them to ``'pending'``.
        """
        now = _utcnow()
        await self._db.execute(
            "UPDATE task_dependencies SET resolved = 1, resolved_at = ? "
            "WHERE blocked_by = ? AND resolved = 0",
            (now, completed_task_id),
        )

        review_dependents = await self._db.execute_fetchall(
            "SELECT DISTINCT t.id FROM tasks t "
            "JOIN task_dependencies d ON d.task_id = t.id "
            "WHERE d.blocked_by = ? "
            "AND t.status = 'review' "
            "AND t.review_status = 'waiting_revision'",
            (completed_task_id,),
        )
        for row in review_dependents:
            await self.mark_review_ready_if_unblocked(row["id"])

        # Find tasks that were blocked and now have no remaining unresolved deps.
        newly_free = await self._db.execute_fetchall(
            "SELECT t.id, t.assigned_to, t.group_id FROM tasks t "
            "WHERE t.status = 'blocked' "
            "  AND NOT EXISTS ("
            "    SELECT 1 FROM task_dependencies d "
            "    WHERE d.task_id = t.id AND d.resolved = 0"
            "  )",
        )
        for row in newly_free:
            transitioned = await self._db.execute_returning(
                "UPDATE tasks SET status = 'pending' "
                "WHERE id = ? AND status = 'blocked' RETURNING *",
                (row["id"],),
            )
            # Wake any idle agent for this role so it doesn't wait
            # out the poll_interval before picking up work that is
            # now claimable.
            if transitioned:
                task = transitioned[0]
                await self.reconcile_task_package(task["id"])
                if self._event_bus is not None:
                    await self._event_bus.emit(
                        "task.available",
                        {
                            "task_id": task["id"],
                            "role": task["assigned_to"],
                            "group_id": task["group_id"],
                        },
                    )

    # ------------------------------------------------------------------
    # Board view
    # ------------------------------------------------------------------

    async def get_board(
        self,
        group_id: str | None = None,
        assigned_to: str | None = None,
        claimed_by: str | None = None,
        task_type: str | None = None,
        priority: str | None = None,
    ) -> dict[str, list[dict]]:
        """Return tasks grouped by status, with optional filters.

        Returns a dict like ``{"pending": [...], "in_progress": [...], ...}``.
        """
        clauses: list[str] = []
        params: list[str] = []

        if group_id is not None:
            clauses.append("group_id = ?")
            params.append(group_id)
        if assigned_to is not None:
            clauses.append("assigned_to = ?")
            params.append(assigned_to)
        if claimed_by is not None:
            clauses.append("claimed_by = ?")
            params.append(claimed_by)
        if task_type is not None:
            clauses.append("task_type = ?")
            params.append(task_type)
        if priority is not None:
            clauses.append("priority = ?")
            params.append(priority)

        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = f"SELECT * FROM tasks{where} ORDER BY created_at"
        rows = await self._db.execute_fetchall(sql, tuple(params))

        board: dict[str, list[dict]] = {}
        for row in rows:
            status = row["status"]
            board.setdefault(status, []).append(row)
        return board

    # ------------------------------------------------------------------
    # Cycle detection
    # ------------------------------------------------------------------

    async def has_cycle(self, task_id: str, blocked_by_id: str) -> bool:
        """Return True if adding ``task_id`` blocked-by ``blocked_by_id``
        would create a cycle in the dependency graph.

        Uses BFS starting from *blocked_by_id*, following the
        ``task_dependencies`` edges (unresolved) in reverse (i.e. "who is
        *blocked_by_id* blocked by?").  If we reach *task_id* there is a
        cycle.

        Additionally, a direct identity check is performed: if *task_id*
        equals *blocked_by_id*, that is a trivial cycle.
        """
        if task_id == blocked_by_id:
            return True

        # BFS: starting from blocked_by_id, walk "upstream" through
        # unresolved dependencies.  If we ever reach task_id there would be
        # a cycle.
        visited: set[str] = set()
        queue: deque[str] = deque([blocked_by_id])

        while queue:
            current = queue.popleft()
            if current in visited:
                continue
            visited.add(current)

            # Find what *current* is blocked by.
            rows = await self._db.execute_fetchall(
                "SELECT blocked_by FROM task_dependencies "
                "WHERE task_id = ? AND resolved = 0",
                (current,),
            )
            for row in rows:
                upstream = row["blocked_by"]
                if upstream == task_id:
                    return True
                if upstream not in visited:
                    queue.append(upstream)

        return False

    async def add_dependency(self, task_id: str, blocked_by_id: str) -> None:
        """Add a dependency edge after checking that it will not create a cycle."""
        if await self.has_cycle(task_id, blocked_by_id):
            raise ValueError(
                f"Dependency {task_id} -> {blocked_by_id} would create a cycle"
            )
        now = _utcnow()
        blocker = await self._db.execute_fetchone(
            "SELECT status FROM tasks WHERE id = ?",
            (blocked_by_id,),
        )
        dep_resolved = 1 if blocker and blocker["status"] == "completed" else 0
        dep_resolved_at = now if dep_resolved else None
        await self._db.execute(
            "INSERT OR IGNORE INTO task_dependencies "
            "(task_id, blocked_by, resolved, resolved_at) VALUES (?, ?, ?, ?)",
            (task_id, blocked_by_id, dep_resolved, dep_resolved_at),
        )
        if dep_resolved:
            await self._db.execute(
                "UPDATE task_dependencies SET resolved = 1, "
                "resolved_at = COALESCE(resolved_at, ?) "
                "WHERE task_id = ? AND blocked_by = ?",
                (now, task_id, blocked_by_id),
            )
        elif not blocker or blocker["status"] not in ("failed", "rejected"):
            await self._db.execute(
                "UPDATE tasks SET status = 'blocked' "
                "WHERE id = ? AND status = 'pending'",
                (task_id,),
            )
        if blocker and blocker["status"] in ("failed", "rejected"):
            target = await self.get_task(task_id)
            if target and target["status"] == REVIEW_STATUS:
                rejected = await self._reject_review_for_terminal_dependency(
                    task_id, blocked_by_id
                )
                if rejected:
                    await self._cascade_failure(task_id)
                    await self._check_group_completion(task_id)
                    return
        reconciled = await self._reconcile_dependencies_after_create(task_id)
        if blocker and blocker["status"] in ("failed", "rejected"):
            target = reconciled or await self.get_task(task_id)
            if target and target["status"] == "failed":
                await self._cascade_failure(task_id)
                await self._check_group_completion(task_id)

    # ------------------------------------------------------------------
    # Resilience / Recovery
    # ------------------------------------------------------------------

    async def recover_orphaned_tasks(
        self, *, heartbeat_timeout_seconds: int = 60
    ) -> list[dict]:
        """Reset in_progress tasks to pending when their claiming instance
        is no longer alive.

        audit 03 F#18: the previous implementation reset EVERY in_progress
        row unconditionally. That is safe for a single-process daemon but
        catastrophic in any HA scenario (multiple workers sharing a SQLite
        file under WAL, rolling restart, sidecar process) -- worker B
        starting up would rip live work out from under worker A's
        actively-heartbeating agents.

        New contract: only reset a task when its ``claimed_by`` is either
        absent from ``agent_instances`` or has a ``last_heartbeat`` older
        than *heartbeat_timeout_seconds*. Tasks owned by a sibling process
        whose agents are still heartbeating are left alone. A task whose
        ``claimed_by`` is NULL is treated as orphaned (bug bail-out) and
        also reset.

        Heartbeats are sent every 15 s per the orchestrator contract; the
        default 60 s timeout tolerates ~3 missed heartbeats before
        reclaiming.
        """
        from datetime import datetime, timezone, timedelta

        cutoff = (
            datetime.now(timezone.utc) - timedelta(seconds=heartbeat_timeout_seconds)
        ).isoformat()

        # LEFT JOIN so rows with an unknown claimed_by (no agent_instances
        # row) are included in the reset via the IS NULL branch. The ``OR
        # tasks.claimed_by IS NULL`` branch covers bug paths where a row
        # is in_progress but no owner was recorded.
        return await self._db.execute_returning(
            "UPDATE tasks SET status = 'pending', claimed_by = NULL, started_at = NULL "
            "WHERE id IN ("
            "   SELECT t.id FROM tasks t "
            "   LEFT JOIN agent_instances ai ON ai.instance_id = t.claimed_by "
            "   WHERE t.status = 'in_progress' "
            "     AND ("
            "        t.claimed_by IS NULL "
            "     OR ai.instance_id IS NULL "
            "     OR ai.last_heartbeat IS NULL "
            "     OR ai.last_heartbeat < ?"
            "   )"
            ") RETURNING *",
            (cutoff,),
        )

    async def recover_stale_in_progress_tasks(
        self, stale_instance_ids: list[str]
    ) -> list[dict]:
        """Reset in_progress tasks claimed by stale (dead) instances.

        Unlike :meth:`recover_orphaned_tasks` which resets *all* in_progress
        tasks (suitable only for server restart), this method targets tasks
        held by specific instances whose heartbeats have gone stale -- safe to
        call during normal operation.

        Recovered tasks are pending again, not completed, so this method does
        not resolve dependency rows for tasks that were waiting on them.
        """
        if not stale_instance_ids:
            return []
        placeholders = ", ".join("?" for _ in stale_instance_ids)
        return await self._db.execute_returning(
            f"UPDATE tasks SET status = 'pending', claimed_by = NULL, started_at = NULL "
            f"WHERE status = 'in_progress' AND claimed_by IN ({placeholders}) "
            f"RETURNING *",
            tuple(stale_instance_ids),
        )

    async def recover_stuck_blocked_tasks(self) -> list[dict]:
        """Recover blocked tasks whose dependencies are all in terminal states.

        A blocked task should be failed if any of its unresolved dependencies
        failed or was rejected, or moved to pending if all dependencies completed
        but the resolution was missed (e.g. crash).
        """
        # Find blocked tasks with unresolved deps pointing to terminal tasks
        stuck = await self._db.execute_fetchall(
            "SELECT DISTINCT d.task_id, d.blocked_by, t2.status AS blocker_status "
            "FROM task_dependencies d "
            "JOIN tasks t ON t.id = d.task_id AND t.status = 'blocked' "
            "JOIN tasks t2 ON t2.id = d.blocked_by "
            "WHERE d.resolved = 0 "
            "  AND t2.status IN ('completed', 'failed', 'rejected')"
        )
        repaired: list[dict] = []
        seen: set[str] = set()

        for row in stuck:
            tid = row["task_id"]
            blocker_status = row["blocker_status"]

            # Resolve this dependency
            await self._db.execute(
                "UPDATE task_dependencies SET resolved = 1 "
                "WHERE task_id = ? AND blocked_by = ?",
                (tid, row["blocked_by"]),
            )

            # If blocker failed, cascade failure to this task
            if blocker_status in ("failed", "rejected") and tid not in seen:
                await self._db.execute(
                    "UPDATE tasks SET status = 'failed' WHERE id = ? AND status = 'blocked'",
                    (tid,),
                )
                seen.add(tid)
                task = await self._db.execute_fetchone(
                    "SELECT * FROM tasks WHERE id = ?", (tid,)
                )
                if task:
                    repaired.append(task)
                    # Cascade further
                    await self._cascade_failure(tid)

        stuck_review = await self._db.execute_fetchall(
            "SELECT DISTINCT d.task_id, d.blocked_by, t2.status AS blocker_status "
            "FROM task_dependencies d "
            "JOIN tasks t ON t.id = d.task_id "
            "AND t.status = 'review' "
            "AND t.review_status = 'waiting_revision' "
            "JOIN tasks t2 ON t2.id = d.blocked_by "
            "WHERE d.resolved = 0 "
            "AND t2.status IN ('completed', 'failed', 'rejected')"
        )
        for row in stuck_review:
            tid = row["task_id"]
            blocker_status = row["blocker_status"]
            if blocker_status == "completed":
                await self._db.execute(
                    "UPDATE task_dependencies SET resolved = 1 "
                    "WHERE task_id = ? AND blocked_by = ?",
                    (tid, row["blocked_by"]),
                )
                updated = await self.mark_review_ready_if_unblocked(tid)
                if updated.get("review_status") == "pending":
                    repaired.append(updated)
                continue

            rejected = await self._reject_waiting_revision_parent(
                tid, row["blocked_by"]
            )
            if rejected:
                repaired.append(rejected)
                await self._cascade_failure(tid)
                await self._check_group_completion(tid)

        # Check for tasks now fully unblocked (all deps resolved successfully)
        newly_free = await self._db.execute_fetchall(
            "SELECT t.id FROM tasks t "
            "WHERE t.status = 'blocked' "
            "  AND NOT EXISTS ("
            "    SELECT 1 FROM task_dependencies d "
            "    WHERE d.task_id = t.id AND d.resolved = 0"
            "  )",
        )
        for row in newly_free:
            await self._db.execute(
                "UPDATE tasks SET status = 'pending' WHERE id = ?",
                (row["id"],),
            )
            task = await self._db.execute_fetchone(
                "SELECT * FROM tasks WHERE id = ?", (row["id"],)
            )
            if task:
                repaired.append(task)

        return repaired

    # ------------------------------------------------------------------
    # Task Cancellation
    # ------------------------------------------------------------------

    async def cancel_task(self, task_id: str, reason: str | None = None) -> dict:
        """Cancel a task and cascade failure to blocked dependents.

        Sets the task status to ``'cancelled'``, records the cancellation
        reason in ``rejection_reason``, and cascades failure to any tasks
        that are blocked by this one (reuses :meth:`_cascade_failure`).

        Returns the cancelled task dict.
        """
        now = _utcnow()
        rows = await self._db.execute_returning(
            "UPDATE tasks SET status = 'cancelled', completed_at = ?, "
            "rejection_reason = ? WHERE id = ? RETURNING *",
            (now, reason, task_id),
        )
        if not rows:
            raise ValueError(f"Task not found: {task_id}")
        await self._cascade_failure(task_id)
        await self.reconcile_task_package(task_id)
        await self._check_group_completion(task_id)
        return rows[0]

    # ------------------------------------------------------------------
    # Task Search
    # ------------------------------------------------------------------

    async def search_tasks(
        self,
        query: str,
        group_id: str | None = None,
        status: str | None = None,
        assigned_to: str | None = None,
        task_type: str | None = None,
        priority: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        """Full-text search on task title and description with optional filters.

        Returns a pagination-aware dict::

            {"tasks": [...], "total": int, "limit": int, "offset": int}
        """
        clauses: list[str] = []
        params: list = []

        # Full-text search on title and description.
        clauses.append("(title LIKE ? OR description LIKE ?)")
        like_pattern = f"%{query}%"
        params.extend([like_pattern, like_pattern])

        if group_id is not None:
            clauses.append("group_id = ?")
            params.append(group_id)
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        if assigned_to is not None:
            clauses.append("assigned_to = ?")
            params.append(assigned_to)
        if task_type is not None:
            clauses.append("task_type = ?")
            params.append(task_type)
        if priority is not None:
            clauses.append("priority = ?")
            params.append(priority)

        where = " WHERE " + " AND ".join(clauses)

        # Count total matching rows.
        count_row = await self._db.execute_fetchone(
            f"SELECT COUNT(*) AS total FROM tasks{where}",
            tuple(params),
        )
        total = count_row["total"] if count_row else 0

        # Fetch the page.
        tasks = await self._db.execute_fetchall(
            f"SELECT * FROM tasks{where} ORDER BY created_at LIMIT ? OFFSET ?",
            tuple(params) + (limit, offset),
        )

        return {"tasks": tasks, "total": total, "limit": limit, "offset": offset}

    # ------------------------------------------------------------------
    # Task Retry
    # ------------------------------------------------------------------

    async def retry_task(self, task_id: str) -> dict:
        """Reset a failed, rejected, or cancelled task back to pending.

        Clears ``claimed_by`` and ``completed_at``, sets status to
        ``'pending'``.

        Returns the reset task dict.
        """
        task = await self.get_task(task_id)
        if task is None:
            raise ValueError(f"Task not found: {task_id}")
        if task["status"] not in ("failed", "rejected", "cancelled"):
            raise ValueError(
                f"Can only retry failed/rejected/cancelled tasks, "
                f"got status={task['status']!r}"
            )

        rows = await self._db.execute_returning(
            "UPDATE tasks SET status = 'pending', claimed_by = NULL, "
            "completed_at = NULL WHERE id = ? RETURNING *",
            (task_id,),
        )
        if not rows:
            raise ValueError(f"Task not found: {task_id}")

        return rows[0]

    # ------------------------------------------------------------------
    # Task Reassignment
    # ------------------------------------------------------------------

    async def reassign_task(self, task_id: str, new_assignee: str) -> dict:
        """Reassign a pending or blocked task to a new agent/role.

        Returns the updated task dict.
        """
        task = await self.get_task(task_id)
        if task is None:
            raise ValueError(f"Task not found: {task_id}")
        if task["status"] not in ("pending", "blocked"):
            raise ValueError(
                f"Can only reassign pending/blocked tasks, "
                f"got status={task['status']!r}"
            )

        rows = await self._db.execute_returning(
            "UPDATE tasks SET assigned_to = ? WHERE id = ? RETURNING *",
            (new_assignee, task_id),
        )
        if not rows:
            raise ValueError(f"Task not found: {task_id}")
        return rows[0]

    # ------------------------------------------------------------------
    # Batch Operations
    # ------------------------------------------------------------------

    async def batch_update_tasks(
        self,
        task_ids: list[str],
        action: str,
        params: dict | None = None,
    ) -> dict:
        """Apply a batch action to multiple tasks.

        Supported actions:

        - ``"cancel"``: Cancel all specified tasks.
        - ``"reassign"``: Set ``assigned_to`` for pending/blocked tasks.
          Requires ``params["assigned_to"]``.
        - ``"change_priority"``: Update priority for specified tasks.
          Requires ``params["priority"]``.
        - ``"retry"``: Reset failed/rejected/cancelled tasks to pending.

        Returns ``{"updated": int, "task_ids": [str]}``.
        """
        params = params or {}
        updated_ids: list[str] = []

        if action == "cancel":
            for tid in task_ids:
                try:
                    await self.cancel_task(tid, reason=params.get("reason"))
                    updated_ids.append(tid)
                except ValueError:
                    continue

        elif action == "reassign":
            new_assignee = params.get("assigned_to", "")
            for tid in task_ids:
                try:
                    await self.reassign_task(tid, new_assignee)
                    updated_ids.append(tid)
                except ValueError:
                    continue

        elif action == "change_priority":
            new_priority = params.get("priority", "medium")
            for tid in task_ids:
                rows = await self._db.execute_returning(
                    "UPDATE tasks SET priority = ? WHERE id = ? RETURNING *",
                    (new_priority, tid),
                )
                if rows:
                    updated_ids.append(tid)

        elif action == "retry":
            for tid in task_ids:
                try:
                    await self.retry_task(tid)
                    updated_ids.append(tid)
                except ValueError:
                    continue

        else:
            raise ValueError(f"Unknown batch action: {action!r}")

        return {"updated": len(updated_ids), "task_ids": updated_ids}

    # ------------------------------------------------------------------
    # Task Templates
    # ------------------------------------------------------------------

    async def create_template(
        self,
        name: str,
        title_template: str,
        description_template: str,
        task_type: str,
        assigned_to: str,
        priority: str = "medium",
    ) -> dict:
        """Insert a new task template. Returns the template dict."""
        await self._db.register_prefix("TPL")
        template_id = await self._db.generate_task_id("TPL")
        now = _utcnow()

        await self._db.execute(
            "INSERT INTO task_templates "
            "(id, name, title_template, description_template, task_type, "
            "assigned_to, priority, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (template_id, name, title_template, description_template,
             task_type, assigned_to, priority, now),
        )

        return {
            "id": template_id,
            "name": name,
            "title_template": title_template,
            "description_template": description_template,
            "task_type": task_type,
            "assigned_to": assigned_to,
            "priority": priority,
            "created_at": now,
        }

    async def get_templates(self) -> list[dict]:
        """Return all task templates."""
        return await self._db.execute_fetchall(
            "SELECT * FROM task_templates ORDER BY created_at"
        )

    async def create_from_template(
        self,
        template_name: str,
        group_id: str,
        variables: dict[str, str] | None = None,
    ) -> dict:
        """Create a task from a named template with variable substitution.

        Placeholders like ``{variable}`` in the title and description
        templates are replaced with values from *variables*.

        Returns the created task dict.
        """
        template = await self._db.execute_fetchone(
            "SELECT * FROM task_templates WHERE name = ?",
            (template_name,),
        )
        if template is None:
            raise ValueError(f"Template not found: {template_name!r}")

        variables = variables or {}

        title = template["title_template"]
        description = template["description_template"] or ""
        for key, value in variables.items():
            title = title.replace(f"{{{key}}}", value)
            description = description.replace(f"{{{key}}}", value)

        return await self.create_task(
            group_id=group_id,
            title=title,
            description=description or None,
            task_type=template["task_type"],
            assigned_to=template["assigned_to"],
            priority=template["priority"],
        )

    # ------------------------------------------------------------------
    # Custom Workflow Execution
    # ------------------------------------------------------------------

    async def start_workflow(
        self, workflow_id: str, group_id: str
    ) -> list[dict]:
        """Load a workflow definition and create tasks with dependencies.

        The workflow ``steps`` field is a JSON array of objects, each with
        at least ``title``, ``task_type``, and ``assigned_to``.  Steps are
        chained sequentially: each step is blocked by the previous one.

        Returns the list of created tasks.
        """
        workflow = await self._db.execute_fetchone(
            "SELECT * FROM workflow_definitions WHERE id = ?",
            (workflow_id,),
        )
        if workflow is None:
            raise ValueError(f"Workflow not found: {workflow_id}")

        steps = json.loads(workflow["steps"])
        created_tasks: list[dict] = []
        prev_task_id: str | None = None

        for step in steps:
            blocked_by = [prev_task_id] if prev_task_id else None
            task = await self.create_task(
                group_id=group_id,
                title=step["title"],
                task_type=step.get("task_type", "workflow_step"),
                assigned_to=step.get("assigned_to", "coder"),
                description=step.get("description"),
                priority=step.get("priority", "medium"),
                blocked_by=blocked_by,
            )
            created_tasks.append(task)
            prev_task_id = task["id"]

        return created_tasks

    # ------------------------------------------------------------------
    # Retry Classification
    # ------------------------------------------------------------------

    async def classify_failure(self, task_id: str) -> str:
        """Classify a task failure as transient, logic, or permanent.

        Uses simple keyword matching on the ``rejection_reason`` field:

        - **transient**: network errors, timeouts, rate limits (auto-retry).
        - **logic**: code bugs, assertion errors (needs fix).
        - **permanent**: missing resources, not found (skip).

        Returns one of ``"transient"``, ``"logic"``, or ``"permanent"``.
        """
        task = await self.get_task(task_id)
        if task is None:
            raise ValueError(f"Task not found: {task_id}")

        reason = (task.get("rejection_reason") or "").lower()

        transient_keywords = [
            "timeout", "timed out", "network", "connection",
            "rate limit", "ratelimit", "retry", "503", "502",
            "504", "temporary", "unavailable",
        ]
        permanent_keywords = [
            "not found", "missing", "does not exist", "404",
            "forbidden", "403", "deleted", "gone", "no such",
            "permission denied",
        ]

        for keyword in transient_keywords:
            if keyword in reason:
                return "transient"

        for keyword in permanent_keywords:
            if keyword in reason:
                return "permanent"

        # Default: if there is a reason but no match, assume logic error.
        return "logic"
