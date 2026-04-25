"""Task board, groups, goals, artifacts, templates, workflows, and export routes."""

from __future__ import annotations

import csv
import io
import json
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Annotated
from fastapi import APIRouter, HTTPException, Query
from starlette.responses import Response

# audit 11a F#4 / F#6: shared clamps for task endpoint pagination and
# batch operations. 500 is generous for UI pagination; 200 tasks per
# batch is plenty and stops a single request from walking the whole
# board.
SafeLimit = Annotated[int, Query(ge=1, le=500)]
MAX_BATCH_SIZE = 200

from taskbrew.dashboard.models import (
    BatchTasksBody,
    CancelTaskBody,
    CompleteTaskBody,
    CreateTaskBody,
    CreateTemplateBody,
    CreateWorkflowBody,
    InstantiateTemplateBody,
    ReassignTaskBody,
    StartWorkflowBody,
    SubmitGoalBody,
    UpdateTaskBody,
)
from taskbrew.dashboard.routers._deps import get_orch, get_orch_optional

router = APIRouter()


# ------------------------------------------------------------------
# Health
# ------------------------------------------------------------------


@router.get("/api/health")
async def health():
    """Liveness probe.

    audit 11a F#23: the previous implementation returned the raw
    DB exception text in the 503 body, leaking file paths and
    schema hints to any caller. Keep the payload opaque and only
    log the detail server-side.
    """
    orch = get_orch_optional()
    if orch is None:
        return {"status": "ok", "db": "no_project"}
    try:
        await orch.task_board._db.execute_fetchone("SELECT 1")
        return {"status": "ok", "db": "connected"}
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning(
            "health check failed: %s", exc,
        )
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=503,
            content={"status": "degraded", "db": "unavailable"},
        )


# ------------------------------------------------------------------
# Task board endpoints
# ------------------------------------------------------------------


@router.get("/api/board")
async def get_board(
    group_id: str | None = None,
    assigned_to: str | None = None,
    claimed_by: str | None = None,
    task_type: str | None = None,
    priority: str | None = None,
):
    orch = get_orch()
    return await orch.task_board.get_board(
        group_id=group_id,
        assigned_to=assigned_to,
        claimed_by=claimed_by,
        task_type=task_type,
        priority=priority,
    )


@router.get("/api/groups")
async def get_groups(status: str | None = None):
    orch = get_orch()
    return await orch.task_board.get_groups(status=status)


@router.get("/api/groups/{group_id}/graph")
async def get_group_graph(group_id: str):
    orch = get_orch()
    tasks = await orch.task_board.get_group_tasks(group_id)
    nodes = []
    edges = []
    for task in tasks:
        nodes.append({
            "id": task["id"],
            "title": task["title"],
            "status": task["status"],
            "assigned_to": task["assigned_to"],
            "claimed_by": task.get("claimed_by"),
            "task_type": task["task_type"],
        })
        if task.get("parent_id"):
            edges.append({
                "from": task["parent_id"],
                "to": task["id"],
                "type": "parent",
            })
    # Also add blocked_by edges from task_dependencies
    task_ids = [t["id"] for t in tasks]
    if task_ids:
        placeholders = ",".join("?" * len(task_ids))
        deps = await orch.task_board._db.execute_fetchall(
            f"SELECT task_id, blocked_by FROM task_dependencies WHERE task_id IN ({placeholders})",
            tuple(task_ids),
        )
        for dep in deps:
            edges.append({
                "from": dep["blocked_by"],
                "to": dep["task_id"],
                "type": "blocked_by",
            })
    return {"nodes": nodes, "edges": edges}


_MAX_TRACE_TASKS = 1000


@router.get("/api/groups/{group_id}/trace")
async def get_group_trace(group_id: str):
    """Execution trace for a feature: per-task timing + cost + status
    + verification, plus group-level aggregates.

    Complements ``/api/groups/{id}/graph`` (which returns the DAG
    shape) with the timing / cost / correctness dimensions we need
    for "where did this feature go" investigations.

    Design:
    docs/superpowers/specs/2026-04-24-execution-tracing-endpoint-design.md
    """
    import json as _json
    from datetime import datetime, timezone
    orch = get_orch()
    db = orch.task_board._db

    group = await db.execute_fetchone(
        "SELECT id, title, status, created_at FROM groups WHERE id = ?",
        (group_id,),
    )
    if group is None:
        raise HTTPException(404, f"Group not found: {group_id}")

    # Fetch tasks (soft-capped) and their usage rows in two queries
    # so the response is O(N) DB work rather than N+1.
    tasks = await db.execute_fetchall(
        "SELECT id, group_id, parent_id, revision_of, title, task_type, "
        "priority, assigned_to, claimed_by, status, merge_status, "
        "requires_fanout, fanout_retries, verification_retries, "
        "completion_checks, branch_name, parent_branch, "
        "created_at, started_at, completed_at "
        "FROM tasks WHERE group_id = ? "
        "ORDER BY created_at "
        f"LIMIT {_MAX_TRACE_TASKS + 1}",
        (group_id,),
    )
    truncated = len(tasks) > _MAX_TRACE_TASKS
    if truncated:
        tasks = tasks[:_MAX_TRACE_TASKS]

    task_ids = [t["id"] for t in tasks]
    usage_by_task: dict[str, dict] = {}
    if task_ids:
        placeholders = ",".join("?" * len(task_ids))
        usage_rows = await db.execute_fetchall(
            "SELECT task_id, SUM(input_tokens) AS input_tokens, "
            "SUM(output_tokens) AS output_tokens, SUM(cost_usd) AS cost_usd, "
            "SUM(num_turns) AS num_turns, SUM(duration_api_ms) AS duration_api_ms "
            f"FROM task_usage WHERE task_id IN ({placeholders}) "
            "GROUP BY task_id",
            tuple(task_ids),
        )
        usage_by_task = {r["task_id"]: r for r in usage_rows}

    # Derive children relationships from the in-memory parent_id
    # chain (no recursive CTE needed at realistic group sizes).
    children_of: dict[str, list[str]] = {}
    for t in tasks:
        pid = t.get("parent_id")
        if pid:
            children_of.setdefault(pid, []).append(t["id"])

    def _parse_iso(s):
        if not s:
            return None
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None

    def _duration_ms(started, completed):
        ts = _parse_iso(started)
        tc = _parse_iso(completed)
        if ts is None or tc is None:
            return None
        return int((tc - ts).total_seconds() * 1000)

    enriched: list[dict] = []
    total_cost = 0.0
    total_in = 0
    total_out = 0
    total_turns = 0
    status_counts: dict[str, int] = {}
    merge_status_counts: dict[str, int] = {}
    verify_retries_total = 0
    first_ts = None
    last_ts = None

    for t in tasks:
        usage = usage_by_task.get(t["id"], {})
        cost = float(usage.get("cost_usd") or 0.0)
        input_tokens = int(usage.get("input_tokens") or 0)
        output_tokens = int(usage.get("output_tokens") or 0)
        num_turns = int(usage.get("num_turns") or 0)

        try:
            checks = _json.loads(t.get("completion_checks") or "{}")
        except _json.JSONDecodeError:
            checks = {}

        entry = {
            "id": t["id"],
            "task_type": t["task_type"],
            "assigned_to": t["assigned_to"],
            "claimed_by": t.get("claimed_by"),
            "title": t["title"],
            "parent_id": t.get("parent_id"),
            "revision_of": t.get("revision_of"),
            "branch_name": t.get("branch_name"),
            "parent_branch": t.get("parent_branch"),
            "status": t["status"],
            "merge_status": t.get("merge_status"),
            "requires_fanout": t.get("requires_fanout"),
            "fanout_retries": t.get("fanout_retries") or 0,
            "verification_retries": t.get("verification_retries") or 0,
            "created_at": t.get("created_at"),
            "started_at": t.get("started_at"),
            "completed_at": t.get("completed_at"),
            "duration_ms": _duration_ms(t.get("started_at"), t.get("completed_at")),
            "cost_usd": round(cost, 6),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "num_turns": num_turns,
            "duration_api_ms": int(usage.get("duration_api_ms") or 0),
            "completion_checks": checks,
            "children": children_of.get(t["id"], []),
        }
        enriched.append(entry)

        total_cost += cost
        total_in += input_tokens
        total_out += output_tokens
        total_turns += num_turns
        verify_retries_total += entry["verification_retries"]
        status_counts[t["status"]] = status_counts.get(t["status"], 0) + 1
        ms_key = t.get("merge_status") or "null"
        merge_status_counts[ms_key] = merge_status_counts.get(ms_key, 0) + 1

        c = _parse_iso(t.get("created_at"))
        f = _parse_iso(t.get("completed_at"))
        if c and (first_ts is None or c < first_ts):
            first_ts = c
        if f and (last_ts is None or f > last_ts):
            last_ts = f

    wall_clock_ms = None
    if first_ts and last_ts and last_ts >= first_ts:
        wall_clock_ms = int((last_ts - first_ts).total_seconds() * 1000)

    return {
        "group_id": group["id"],
        "group_title": group["title"],
        "group_status": group["status"],
        "created_at": group["created_at"],
        "last_activity_at": last_ts.isoformat() if last_ts else None,
        "wall_clock_ms": wall_clock_ms,
        "total_cost_usd": round(total_cost, 6),
        "total_input_tokens": total_in,
        "total_output_tokens": total_out,
        "total_num_turns": total_turns,
        "total_tasks": len(enriched),
        "status_counts": status_counts,
        "merge_status_counts": merge_status_counts,
        "verification_retries_total": verify_retries_total,
        "truncated": truncated,
        "max_tasks": _MAX_TRACE_TASKS,
        "tasks": enriched,
    }


# ------------------------------------------------------------------
# Goals
# ------------------------------------------------------------------


@router.post("/api/goals")
async def submit_goal(body: SubmitGoalBody):
    orch = get_orch()
    title = body.title
    description = body.description
    if not title:
        raise HTTPException(status_code=400, detail="Title is required")
    group = await orch.task_board.create_group(
        title=title, origin="pm", created_by="pm",
    )
    task = await orch.task_board.create_task(
        group_id=group["id"],
        title=f"Create PRD: {title}",
        description=description,
        task_type="goal",
        assigned_to="pm",
        created_by="human",
        priority="high",
    )
    await orch.event_bus.emit("group.created", {"group_id": group["id"], "title": title})
    await orch.event_bus.emit("task.created", {"task_id": task["id"], "group_id": group["id"]})
    return {"group_id": group["id"], "task_id": task["id"]}


@router.post("/api/tasks")
async def create_task(body: CreateTaskBody):
    orch = get_orch()

    # --- C3: Route Validation ---
    # Validate that the creating agent's role is allowed to route to the target.
    # The literal "system" creator is reserved for internal events (e.g., the
    # goal-verification trigger) and bypasses route validation the same way
    # humans do.
    if body.assigned_by not in ("human", "system") and orch.roles:
        # 1. Validate assigned_to is a known role
        if body.assigned_to not in orch.roles:
            raise HTTPException(
                400,
                f"Unknown target role: '{body.assigned_to}'. "
                f"Valid roles: {sorted(orch.roles.keys())}",
            )

        # 2. Validate task_type is accepted by target role
        target_accepts = orch.roles[body.assigned_to].accepts
        if body.task_type not in target_accepts:
            raise HTTPException(
                400,
                f"Role '{body.assigned_to}' does not accept task_type "
                f"'{body.task_type}'. Accepted: {target_accepts}",
            )

        # 3. Validate creator role is allowed to route to target
        m = re.match(r'^(.+)-\d+$', body.assigned_by)
        if m:
            creator_role = m.group(1)
            if creator_role in orch.roles:
                creator_cfg = orch.roles[creator_role]
                routing_mode = getattr(creator_cfg, "routing_mode", "open")
                if routing_mode == "restricted":
                    allowed = any(
                        r.role == body.assigned_to
                        and (not r.task_types or body.task_type in r.task_types)
                        for r in creator_cfg.routes_to
                    )
                    if not allowed:
                        raise HTTPException(
                            403,
                            f"Role '{creator_role}' is not allowed to create "
                            f"'{body.task_type}' tasks for role '{body.assigned_to}' "
                            f"(restricted routing mode)",
                        )
                # If "open", skip route enforcement (Level 1 & 2 still apply)

    # --- Stage-1 Fix #3: Architect-origin coder tasks must link to their design.
    # Without parent_id the coder never receives the tech_design via
    # parent_artifact context and has to re-derive the design from scratch.
    if body.assigned_by not in ("human", "system"):
        m = re.match(r'^(.+)-\d+$', body.assigned_by)
        creator_role = m.group(1) if m else None
        if (
            creator_role == "architect"
            and body.assigned_to == "coder"
            and not body.parent_id
        ):
            raise HTTPException(
                400,
                "Coder tasks created by an architect must include parent_id "
                "referencing your tech_design task. Pass parent_id=<your task id> "
                "so the coder receives your design as context. "
                "(See TaskBrew Stage-1 architect->coder linkage rule.)",
            )

    # --- Stage-1 Fix #12: Reject duplicate verification tasks for the same parent.
    # Two verifier tasks for one CD (e.g. FEAT-002's VR-017 + VR-018) waste tokens
    # and can race on the merge.
    if body.assigned_to == "verifier" and body.parent_id:
        existing_vr = await orch.task_board._db.execute_fetchone(
            "SELECT id FROM tasks "
            "WHERE parent_id = ? AND assigned_to = 'verifier' "
            "AND status != 'cancelled' LIMIT 1",
            (body.parent_id,),
        )
        if existing_vr:
            raise HTTPException(
                409,
                f"A verification task already exists for parent "
                f"'{body.parent_id}' ({existing_vr['id']}). "
                f"Cancel it first if you really need to re-verify.",
            )

    # --- Guardrails ---
    guardrails = getattr(orch.team_config, "guardrails", None)

    # G1: Enforce max_tasks_per_group
    if guardrails and body.group_id:
        group_tasks = await orch.task_board.get_group_tasks(body.group_id)
        if len(group_tasks) >= guardrails.max_tasks_per_group:
            raise HTTPException(
                409,
                f"Group '{body.group_id}' has {len(group_tasks)} tasks, "
                f"exceeding limit of {guardrails.max_tasks_per_group}",
            )

    # G2: Enforce max_task_depth
    if guardrails and body.parent_id:
        depth = 0
        current_id = body.parent_id
        while current_id and depth < 100:
            row = await orch.task_board._db.execute_fetchone(
                "SELECT parent_id FROM tasks WHERE id = ?", (current_id,)
            )
            if not row:
                break
            depth += 1
            current_id = row["parent_id"]
        if depth >= guardrails.max_task_depth:
            raise HTTPException(
                409, f"Task depth {depth} exceeds limit of {guardrails.max_task_depth}"
            )

    # --- C4: Rejection Cycle Limit ---
    # Prevent infinite revision loops by capping at configurable limit
    if body.parent_id and body.task_type in ("revision", "bug_fix"):
        cycle_count = 0
        current_id = body.parent_id
        while current_id and cycle_count < 10:  # safety cap on walk depth
            row = await orch.task_board._db.execute_fetchone(
                "SELECT parent_id, task_type FROM tasks WHERE id = ?",
                (current_id,),
            )
            if not row:
                break
            if row["task_type"] in ("revision", "bug_fix"):
                cycle_count += 1
            current_id = row["parent_id"]
        cycle_limit = guardrails.rejection_cycle_limit if guardrails else 3
        if cycle_count >= cycle_limit:
            raise HTTPException(
                409,
                f"Rejection cycle limit reached ({cycle_count} revision/bug_fix "
                f"tasks in chain). Human intervention required.",
            )

    task = await orch.task_board.create_task(
        group_id=body.group_id,
        title=body.title,
        task_type=body.task_type,
        assigned_to=body.assigned_to,
        created_by=body.assigned_by,
        description=body.description,
        priority=body.priority,
        parent_id=body.parent_id,
        blocked_by=body.blocked_by,
        requires_fanout=body.requires_fanout,
    )
    await orch.event_bus.emit("task.created", {"task_id": task["id"], "group_id": body.group_id})
    return task


# ------------------------------------------------------------------
# Task Search
# ------------------------------------------------------------------


@router.get("/api/tasks/search")
async def search_tasks(
    q: str = "",
    group_id: str | None = None,
    status: str | None = None,
    assigned_to: str | None = None,
    task_type: str | None = None,
    priority: str | None = None,
    limit: SafeLimit = 50,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
):
    orch = get_orch()
    return await orch.task_board.search_tasks(
        query=q, group_id=group_id, status=status,
        assigned_to=assigned_to, task_type=task_type,
        priority=priority, limit=limit, offset=offset,
    )


# ------------------------------------------------------------------
# Task Actions
# ------------------------------------------------------------------


@router.get("/api/tasks/{task_id}")
async def get_task_detail(task_id: str):
    orch = get_orch()
    task = await orch.task_board.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    # Enrich with dependencies
    deps = await orch.task_board._db.execute_fetchall(
        "SELECT blocked_by, resolved FROM task_dependencies WHERE task_id = ?", (task_id,)
    )
    # Enrich with children
    children = await orch.task_board._db.execute_fetchall(
        "SELECT id, title, status, assigned_to FROM tasks WHERE parent_id = ?", (task_id,)
    )
    task["dependencies"] = deps
    task["children"] = children
    return task


@router.post("/api/tasks/{task_id}/cancel")
async def cancel_task(task_id: str, body: CancelTaskBody = CancelTaskBody()):
    orch = get_orch()
    reason = body.reason
    result = await orch.task_board.cancel_task(task_id, reason=reason)
    await orch.event_bus.emit("task.cancelled", {"task_id": task_id, "reason": reason})
    return result


@router.post("/api/tasks/{task_id}/retry")
async def retry_task(task_id: str):
    orch = get_orch()
    result = await orch.task_board.retry_task(task_id)
    await orch.event_bus.emit("task.retried", {"task_id": task_id})
    return result


@router.post("/api/tasks/{task_id}/reassign")
async def reassign_task(task_id: str, body: ReassignTaskBody):
    orch = get_orch()
    new_assignee = body.assigned_to
    if not new_assignee:
        raise HTTPException(status_code=400, detail="assigned_to is required")
    result = await orch.task_board.reassign_task(task_id, new_assignee)
    await orch.event_bus.emit("task.reassigned", {"task_id": task_id, "assigned_to": new_assignee})
    return result


@router.post("/api/tasks/{task_id}/complete")
async def complete_task_endpoint(task_id: str, body: CompleteTaskBody = CompleteTaskBody()):
    orch = get_orch()
    if body.status == "completed":
        result = await orch.task_board.complete_task(task_id)
    elif body.status == "failed":
        result = await orch.task_board.fail_task(task_id)
    else:
        raise HTTPException(status_code=400, detail=f"Invalid status: {body.status}")
    await orch.event_bus.emit("task.completed", {"task_id": task_id, "status": body.status})
    return result


# audit 11a F#2: PATCH /api/tasks/{id} previously built SQL via
# f", ".join(f"{k} = ?" for k in updates)" where ``updates`` was a dict
# populated from Pydantic fields. It was not actually injectable today
# (keys were pulled from named attributes, not from user input), but
# if a future addition ever let a user-controlled string reach those
# keys the column name would be interpolated straight into SQL. Replace
# the free-form join with a hardcoded column → SQL-fragment map so the
# set of possible UPDATE columns is statically visible.
_TASK_UPDATE_COLUMN_SQL: dict[str, str] = {
    "priority": "priority = ?",
    "assigned_to": "assigned_to = ?",
    "status": "status = ?",
}
_VALID_TASK_STATUSES = frozenset({
    "blocked", "pending", "in_progress",
    "completed", "failed", "rejected", "cancelled",
})


@router.patch("/api/tasks/{task_id}")
async def update_task_endpoint(task_id: str, body: UpdateTaskBody):
    orch = get_orch()
    task = await orch.task_board.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    updates: dict[str, object] = {}
    if body.priority is not None:
        updates["priority"] = body.priority
    if body.assigned_to is not None:
        updates["assigned_to"] = body.assigned_to
    if body.status is not None:
        if body.status not in _VALID_TASK_STATUSES:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid status '{body.status}'. Valid: {sorted(_VALID_TASK_STATUSES)}",
            )
        updates["status"] = body.status
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    set_fragments: list[str] = []
    values: list[object] = []
    for field_name, value in updates.items():
        fragment = _TASK_UPDATE_COLUMN_SQL.get(field_name)
        if fragment is None:
            # Hard stop: unknown fields never reach SQL. The current
            # control flow above cannot produce one, but this closes the
            # footgun for future edits.
            raise HTTPException(400, f"Field '{field_name}' cannot be updated")
        set_fragments.append(fragment)
        values.append(value)
    values.append(task_id)

    sql = "UPDATE tasks SET " + ", ".join(set_fragments) + " WHERE id = ? RETURNING *"
    rows = await orch.task_board._db.execute_returning(sql, tuple(values))
    if not rows:
        raise HTTPException(status_code=404, detail="Task not found")
    await orch.event_bus.emit("task.updated", {"task_id": task_id, "updates": updates})
    return rows[0]


# ------------------------------------------------------------------
# Batch Operations
# ------------------------------------------------------------------


@router.post("/api/tasks/batch")
async def batch_tasks(body: BatchTasksBody):
    orch = get_orch()
    task_ids = body.task_ids
    action = body.action
    params = body.params
    if not task_ids or not action:
        raise HTTPException(status_code=400, detail="task_ids and action are required")
    # audit 11a F#4: refuse oversized batches. Tasks are individually
    # touched on SQLite which holds the connection for the duration of
    # the batch; a 10k-id batch would block every other writer.
    if len(task_ids) > MAX_BATCH_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"Batch too large: {len(task_ids)} task_ids (max {MAX_BATCH_SIZE})",
        )
    result = await orch.task_board.batch_update_tasks(task_ids, action, params)
    await orch.event_bus.emit("tasks.batch_updated", {"action": action, "count": result["updated"]})
    return result


# ------------------------------------------------------------------
# Artifacts
# ------------------------------------------------------------------
#
# Agents don't currently call ArtifactStore.save_artifact — they write files
# via the generic Write tool (landing flat in <project>/artifacts/) and their
# final summary is persisted to tasks.output_text. To surface those, the
# handlers below union three sources per task:
#   1. Structured layout: <base_dir>/<group_id>/<task_id>/<filename>
#   2. Flat files in <base_dir>/ whose basename starts with "<task_id>_" or
#      "<task_id>." (e.g., "AR-007_design.md" → task AR-007)
#   3. tasks.output_text surfaced as a synthetic "agent_output.md"

SYNTHETIC_OUTPUT_FILENAME = "agent_output.md"


def _artifact_base_dir(orch) -> Path:
    tc = orch.team_config
    return Path(orch.project_dir) / (tc.artifacts_base_dir if tc else "artifacts")


def _flat_files_for_task(base_dir: Path, task_id: str) -> list[str]:
    """Return basenames of files directly under ``base_dir`` that belong to
    ``task_id`` by filename prefix. Separator-aware so "AR-007" does not
    match "AR-0071".
    """
    if not base_dir.is_dir():
        return []
    results: list[str] = []
    for entry in sorted(base_dir.iterdir()):
        if not entry.is_file():
            continue
        name = entry.name
        if (
            name.startswith(f"{task_id}_")
            or name.startswith(f"{task_id}.")
            or name == task_id
        ):
            results.append(name)
    return results


async def _task_output_text(orch, task_id: str) -> str | None:
    row = await orch.task_board._db.execute_fetchone(
        "SELECT output_text FROM tasks WHERE id = ?", (task_id,)
    )
    if not row:
        return None
    text = row.get("output_text")
    return text or None


@router.get("/api/artifacts")
async def list_artifacts(group_id: str | None = None):
    orch = get_orch()
    from taskbrew.orchestrator.artifact_store import ArtifactStore

    base = _artifact_base_dir(orch)
    store = ArtifactStore(base_dir=str(base))
    results = store.get_all_artifacts(group_id)

    # Index structured results for merging with flat-file / output_text sources.
    index: dict[tuple[str, str], dict] = {
        (entry["group_id"], entry["task_id"]): entry for entry in results
    }

    db = orch.task_board._db
    if group_id is not None:
        rows = await db.execute_fetchall(
            "SELECT id, group_id, output_text FROM tasks WHERE group_id = ?",
            (group_id,),
        )
    else:
        rows = await db.execute_fetchall(
            "SELECT id, group_id, output_text FROM tasks"
        )

    for row in rows:
        tid, gid = row["id"], row["group_id"]
        if not tid or not gid:
            continue
        extras: list[str] = _flat_files_for_task(base, tid)
        if row.get("output_text"):
            extras.append(SYNTHETIC_OUTPUT_FILENAME)
        if not extras:
            continue
        entry = index.get((gid, tid))
        if entry is None:
            entry = {"group_id": gid, "task_id": tid, "files": []}
            index[(gid, tid)] = entry
            results.append(entry)
        existing = set(entry["files"])
        for name in extras:
            if name not in existing:
                entry["files"].append(name)
                existing.add(name)

    return results


@router.get("/api/artifacts/{group_id}/{task_id}")
async def get_task_artifacts(group_id: str, task_id: str):
    orch = get_orch()
    from taskbrew.orchestrator.artifact_store import ArtifactStore

    base = _artifact_base_dir(orch)
    store = ArtifactStore(base_dir=str(base))

    files: list[str] = list(store.get_task_artifacts(group_id, task_id))
    seen = set(files)
    for name in _flat_files_for_task(base, task_id):
        if name not in seen:
            files.append(name)
            seen.add(name)

    if SYNTHETIC_OUTPUT_FILENAME not in seen:
        output_text = await _task_output_text(orch, task_id)
        if output_text:
            files.append(SYNTHETIC_OUTPUT_FILENAME)

    return {"group_id": group_id, "task_id": task_id, "files": files}


@router.get("/api/artifacts/{group_id}/{task_id}/{filename}")
async def get_artifact_content(group_id: str, task_id: str, filename: str):
    """Return one artifact's content.

    Returns 404 when no source matches — previously this returned 200
    with empty content, which masks the "file never existed" case as
    "file is empty" and confuses the viewer UI.
    """
    orch = get_orch()
    from taskbrew.orchestrator.artifact_store import ArtifactStore

    base = _artifact_base_dir(orch)
    store = ArtifactStore(base_dir=str(base))

    if filename == SYNTHETIC_OUTPUT_FILENAME:
        output_text = await _task_output_text(orch, task_id)
        if not output_text:
            raise HTTPException(status_code=404, detail="Artifact not found")
        return {
            "filename": filename,
            "content": output_text,
            "group_id": group_id,
            "task_id": task_id,
        }

    structured_path = base / group_id / task_id / filename
    if structured_path.is_file():
        content = store.load_artifact(group_id, task_id, filename)
        return {
            "filename": filename,
            "content": content,
            "group_id": group_id,
            "task_id": task_id,
        }

    if filename in _flat_files_for_task(base, task_id):
        flat_path = base / filename
        base_resolved = base.resolve()
        flat_resolved = flat_path.resolve()
        try:
            flat_resolved.relative_to(base_resolved)
        except ValueError:
            raise HTTPException(status_code=400, detail="Path traversal detected")
        # Same MAX_LOAD_ARTIFACT_BYTES cap as the structured store path,
        # so a multi-GB flat-file can't OOM the dashboard either.
        from taskbrew.orchestrator.artifact_store import MAX_LOAD_ARTIFACT_BYTES
        try:
            with open(flat_path, encoding="utf-8", errors="replace") as f:
                content = f.read(MAX_LOAD_ARTIFACT_BYTES)
            try:
                size = flat_path.stat().st_size
            except OSError:
                size = 0
            if size > MAX_LOAD_ARTIFACT_BYTES:
                content += (
                    f"\n\n[truncated by TaskBrew: file is {size} bytes; "
                    f"showing first {MAX_LOAD_ARTIFACT_BYTES} bytes]\n"
                )
        except OSError:
            content = ""
        return {
            "filename": filename,
            "content": content,
            "group_id": group_id,
            "task_id": task_id,
        }

    raise HTTPException(status_code=404, detail="Artifact not found")


# ------------------------------------------------------------------
# Task Templates
# ------------------------------------------------------------------


@router.get("/api/templates")
async def get_templates():
    orch = get_orch()
    return await orch.task_board._db.execute_fetchall("SELECT * FROM task_templates ORDER BY name")


@router.post("/api/templates")
async def create_template(body: CreateTemplateBody):
    orch = get_orch()
    import uuid
    template_id = str(uuid.uuid4())[:8]
    now = datetime.now(timezone.utc).isoformat()
    await orch.task_board._db.execute(
        "INSERT INTO task_templates (id, name, title_template, description_template, task_type, assigned_to, priority, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (template_id, body.name, body.title_template, body.description_template, body.task_type, body.assigned_to, body.priority, now)
    )
    return {"id": template_id, "name": body.name}


@router.post("/api/templates/{template_name}/instantiate")
async def instantiate_template(template_name: str, body: InstantiateTemplateBody):
    orch = get_orch()
    group_id = body.group_id
    variables = body.variables
    if not group_id:
        raise HTTPException(status_code=400, detail="group_id is required")
    result = await orch.task_board.create_from_template(template_name, group_id, variables)
    return result


# ------------------------------------------------------------------
# Workflows
# ------------------------------------------------------------------


@router.get("/api/workflows")
async def get_workflows():
    orch = get_orch()
    return await orch.task_board._db.execute_fetchall("SELECT * FROM workflow_definitions WHERE active = 1")


@router.post("/api/workflows")
async def create_workflow(body: CreateWorkflowBody):
    orch = get_orch()
    import uuid
    workflow_id = str(uuid.uuid4())[:8]
    now = datetime.now(timezone.utc).isoformat()
    await orch.task_board._db.execute(
        "INSERT INTO workflow_definitions (id, name, description, steps, created_at) VALUES (?, ?, ?, ?, ?)",
        (workflow_id, body.name, body.description, json.dumps(body.steps), now)
    )
    return {"id": workflow_id, "name": body.name}


@router.post("/api/workflows/{workflow_id}/start")
async def start_workflow(workflow_id: str, body: StartWorkflowBody):
    orch = get_orch()
    group_id = body.group_id
    if not group_id:
        raise HTTPException(status_code=400, detail="group_id is required")
    tasks = await orch.task_board.start_workflow(workflow_id, group_id)
    return {"workflow_id": workflow_id, "tasks_created": len(tasks), "tasks": tasks}


# ------------------------------------------------------------------
# Board filters
# ------------------------------------------------------------------


@router.get("/api/board/filters")
async def get_board_filters():
    orch = get_orch()
    groups = await orch.task_board.get_groups()
    instances = await orch.instance_manager.get_all_instances()
    role_names = list(set(i["role"] for i in instances)) if instances else []
    return {
        "groups": [{"id": g["id"], "title": g["title"]} for g in groups],
        "roles": role_names if role_names else (list(orch.roles.keys()) if orch.roles else []),
        "statuses": ["blocked", "pending", "in_progress", "completed", "failed", "rejected"],
        "priorities": ["critical", "high", "medium", "low"],
    }


# ------------------------------------------------------------------
# Usage
# ------------------------------------------------------------------


@router.get("/api/usage")
async def get_usage():
    orch = get_orch()
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    week_start = (now - timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    ).isoformat()
    daily = await orch.task_board._db.get_usage_summary(today_start)
    weekly = await orch.task_board._db.get_usage_summary(week_start)
    return {
        "daily": daily,
        "weekly": weekly,
        "today": today_start,
        "week_start": week_start,
    }


# ------------------------------------------------------------------
# Metrics
# ------------------------------------------------------------------


_VALID_TIME_RANGES = ("1h", "6h", "today", "7d", "30d", "live")
_VALID_GRANULARITIES = ("minute", "hour", "day")


@router.get("/api/metrics/timeseries")
async def get_metrics_timeseries(
    time_range: str = "today",
    granularity: str = "hour",
):
    """Return cost, tokens, task counts per time bucket.

    audit 11a F#11: an unknown ``time_range`` used to silently fall
    back to ``today``, but the delta / since-computation branches
    disagreed on unknown inputs so the caller got silently-wrong
    metrics. Validate both inputs up front and 400 on unknowns.
    """
    if time_range not in _VALID_TIME_RANGES:
        raise HTTPException(
            400, f"unknown time_range {time_range!r}; expected one of {_VALID_TIME_RANGES}"
        )
    if granularity not in _VALID_GRANULARITIES:
        raise HTTPException(
            400, f"unknown granularity {granularity!r}; expected one of {_VALID_GRANULARITIES}"
        )
    orch = get_orch()
    now = datetime.now(timezone.utc)
    range_map = {
        "1h": timedelta(hours=1),
        "6h": timedelta(hours=6),
        "today": timedelta(hours=now.hour, minutes=now.minute, seconds=now.second),
        "7d": timedelta(days=7),
        "30d": timedelta(days=30),
        "live": timedelta(minutes=30),
    }
    delta = range_map[time_range]
    if time_range == "today":
        since = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    else:
        since = (now - delta).isoformat()

    if granularity == "hour":
        fmt = "%Y-%m-%dT%H:00:00"
    elif granularity == "minute":
        fmt = "%Y-%m-%dT%H:%M:00"
    else:
        fmt = "%Y-%m-%dT00:00:00"

    # audit 11a F#12: cap the number of buckets returned per
    # series so a long-lived project with many distinct models
    # doesn't blow up the JSON response. 5000 is ~7 months of
    # hourly data per model -- generous but bounded.
    _MAX_TIMESERIES_ROWS = 5000
    usage_rows = await orch.task_board._db.execute_fetchall(
        "SELECT strftime(?, recorded_at) AS bucket, "
        "  model, "
        "  SUM(cost_usd) AS cost, "
        "  SUM(input_tokens) AS input_tokens, "
        "  SUM(output_tokens) AS output_tokens, "
        "  COUNT(*) AS task_count "
        "FROM task_usage WHERE recorded_at >= ? "
        "GROUP BY bucket, model ORDER BY bucket "
        f"LIMIT {_MAX_TIMESERIES_ROWS + 1}",
        (fmt, since),
    )
    usage_truncated = len(usage_rows) > _MAX_TIMESERIES_ROWS
    if usage_truncated:
        usage_rows = usage_rows[:_MAX_TIMESERIES_ROWS]

    task_rows = await orch.task_board._db.execute_fetchall(
        "SELECT strftime(?, completed_at) AS bucket, "
        "  status, COUNT(*) AS count "
        "FROM tasks WHERE completed_at IS NOT NULL AND completed_at >= ? "
        "GROUP BY bucket, status ORDER BY bucket "
        f"LIMIT {_MAX_TIMESERIES_ROWS + 1}",
        (fmt, since),
    )
    tasks_truncated = len(task_rows) > _MAX_TIMESERIES_ROWS
    if tasks_truncated:
        task_rows = task_rows[:_MAX_TIMESERIES_ROWS]

    status_totals = await orch.task_board._db.execute_fetchall(
        "SELECT status, COUNT(*) AS count FROM tasks GROUP BY status"
    )

    return {
        "usage": usage_rows,
        "tasks": task_rows,
        "status_totals": {r["status"]: r["count"] for r in status_totals},
        "since": since,
        "granularity": granularity,
        "truncated": {
            "usage": usage_truncated,
            "tasks": tasks_truncated,
        },
        "max_rows": _MAX_TIMESERIES_ROWS,
    }


@router.get("/api/metrics/roles")
async def get_metrics_roles():
    """Per-role success rates, costs, durations."""
    orch = get_orch()
    role_tasks = await orch.task_board._db.execute_fetchall(
        "SELECT assigned_to AS role, "
        "  COUNT(*) AS total, "
        "  SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) AS completed, "
        "  SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS failed "
        "FROM tasks GROUP BY assigned_to ORDER BY total DESC"
    )
    # audit 11a F#13: SUBSTR + INSTR on agent_ids that have no '-'
    # (e.g. a legacy agent_id stored as bare ``"pm"``) returns
    # INSTR()=0, which means SUBSTR(..., 1, -1) = '' -- every such
    # row rolls up into an empty-string role bucket that the UI
    # rendered as a mystery row. Filter out empty roles and fall
    # back to the full agent_id when no '-' separator is present.
    role_costs = await orch.task_board._db.execute_fetchall(
        "SELECT "
        "  CASE WHEN INSTR(agent_id, '-') > 0 "
        "       THEN SUBSTR(agent_id, 1, INSTR(agent_id, '-') - 1) "
        "       ELSE agent_id "
        "  END AS role, "
        "  SUM(cost_usd) AS cost, "
        "  SUM(input_tokens) AS input_tokens, "
        "  SUM(output_tokens) AS output_tokens, "
        "  AVG(duration_api_ms) AS avg_duration_ms, "
        "  SUM(num_turns) AS total_turns "
        "FROM task_usage "
        "WHERE agent_id IS NOT NULL AND agent_id != '' "
        "GROUP BY role ORDER BY cost DESC"
    )
    return {"task_stats": role_tasks, "cost_stats": role_costs}


@router.get("/api/metrics/agents")
async def get_metrics_agents(top: int = 10):
    """Agent leaderboard."""
    orch = get_orch()
    rows = await orch.task_board._db.execute_fetchall(
        "SELECT agent_id, "
        "  COUNT(*) AS tasks_completed, "
        "  SUM(cost_usd) AS total_cost, "
        "  SUM(input_tokens) AS input_tokens, "
        "  SUM(output_tokens) AS output_tokens, "
        "  AVG(duration_api_ms) AS avg_duration_ms, "
        "  SUM(num_turns) AS total_turns "
        "FROM task_usage GROUP BY agent_id "
        "ORDER BY tasks_completed DESC LIMIT ?",
        (top,),
    )
    return rows


@router.get("/api/metrics/failures")
async def get_metrics_failures(limit: int = 20):
    """Recent failed tasks."""
    orch = get_orch()
    rows = await orch.task_board._db.execute_fetchall(
        "SELECT id, title, assigned_to, task_type, group_id, "
        "  created_at, completed_at "
        "FROM tasks WHERE status = 'failed' "
        "ORDER BY created_at DESC LIMIT ?",
        (limit,),
    )
    return rows


# ------------------------------------------------------------------
# Export
# ------------------------------------------------------------------


@router.get("/api/export")
async def export_data(format: str = "json"):
    """Export all tasks and usage data."""
    orch = get_orch()
    tasks = await orch.task_board._db.execute_fetchall("SELECT * FROM tasks ORDER BY created_at")
    groups = await orch.task_board._db.execute_fetchall("SELECT * FROM groups ORDER BY created_at")
    usage = await orch.task_board._db.execute_fetchall("SELECT * FROM task_usage ORDER BY recorded_at")

    data = {"groups": groups, "tasks": tasks, "usage": usage, "exported_at": datetime.now(timezone.utc).isoformat()}

    if format == "csv":
        # audit 11a F#5: guard against CSV formula injection in exported
        # task fields (titles, descriptions, error messages authored by
        # LLM agents). See exports._escape_row for the rationale.
        from taskbrew.dashboard.routers.exports import _escape_row
        output = io.StringIO()
        if tasks:
            writer = csv.DictWriter(output, fieldnames=tasks[0].keys())
            writer.writeheader()
            writer.writerows(_escape_row(t) for t in tasks)
        return Response(content=output.getvalue(), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=tasks.csv"})

    return data


# ------------------------------------------------------------------
# Project Info
# ------------------------------------------------------------------


@router.get("/api/project")
async def get_project_info():
    orch = get_orch_optional()
    pd = orch.project_dir if orch else None
    return {
        "project_dir": pd,
        "project_name": Path(pd).name if pd else "unknown",
    }
