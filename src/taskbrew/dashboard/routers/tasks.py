"""Task board, groups, goals, artifacts, templates, workflows, and export routes."""

from __future__ import annotations

import csv
import io
import json
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from fastapi import APIRouter, HTTPException
from starlette.responses import Response

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
    orch = get_orch_optional()
    if orch is None:
        return {"status": "ok", "db": "no_project"}
    try:
        await orch.task_board._db.execute_fetchone("SELECT 1")
        return {"status": "ok", "db": "connected"}
    except Exception as e:
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=503,
            content={"status": "degraded", "db": str(e)},
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
    # Validate that the creating agent's role is allowed to route to the target
    if body.assigned_by != "human" and orch.roles:
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
    limit: int = 50,
    offset: int = 0,
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


@router.patch("/api/tasks/{task_id}")
async def update_task_endpoint(task_id: str, body: UpdateTaskBody):
    orch = get_orch()
    task = await orch.task_board.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    VALID_STATUSES = {"blocked", "pending", "in_progress", "completed", "failed", "rejected", "cancelled"}
    ALLOWED_UPDATE_FIELDS = {"priority", "assigned_to", "status"}
    updates = {}
    if body.priority is not None:
        updates["priority"] = body.priority
    if body.assigned_to is not None:
        updates["assigned_to"] = body.assigned_to
    if body.status is not None:
        if body.status not in VALID_STATUSES:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid status '{body.status}'. Valid: {sorted(VALID_STATUSES)}",
            )
        updates["status"] = body.status
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")
    for field_name in updates:
        if field_name not in ALLOWED_UPDATE_FIELDS:
            raise HTTPException(400, f"Field '{field_name}' cannot be updated")
    set_clauses = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [task_id]
    rows = await orch.task_board._db.execute_returning(
        f"UPDATE tasks SET {set_clauses} WHERE id = ? RETURNING *",
        tuple(values),
    )
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
    result = await orch.task_board.batch_update_tasks(task_ids, action, params)
    await orch.event_bus.emit("tasks.batch_updated", {"action": action, "count": result["updated"]})
    return result


# ------------------------------------------------------------------
# Artifacts
# ------------------------------------------------------------------


@router.get("/api/artifacts")
async def list_artifacts(group_id: str | None = None):
    orch = get_orch()
    from taskbrew.orchestrator.artifact_store import ArtifactStore
    tc = orch.team_config
    store = ArtifactStore(base_dir=str(Path(orch.project_dir) / (tc.artifacts_base_dir if tc else "artifacts")))
    return store.get_all_artifacts(group_id)


@router.get("/api/artifacts/{group_id}/{task_id}")
async def get_task_artifacts(group_id: str, task_id: str):
    orch = get_orch()
    from taskbrew.orchestrator.artifact_store import ArtifactStore
    tc = orch.team_config
    store = ArtifactStore(base_dir=str(Path(orch.project_dir) / (tc.artifacts_base_dir if tc else "artifacts")))
    files = store.get_task_artifacts(group_id, task_id)
    return {"group_id": group_id, "task_id": task_id, "files": files}


@router.get("/api/artifacts/{group_id}/{task_id}/{filename}")
async def get_artifact_content(group_id: str, task_id: str, filename: str):
    orch = get_orch()
    from taskbrew.orchestrator.artifact_store import ArtifactStore
    tc = orch.team_config
    store = ArtifactStore(base_dir=str(Path(orch.project_dir) / (tc.artifacts_base_dir if tc else "artifacts")))
    content = store.load_artifact(group_id, task_id, filename)
    return {"filename": filename, "content": content, "group_id": group_id, "task_id": task_id}


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


@router.get("/api/metrics/timeseries")
async def get_metrics_timeseries(
    time_range: str = "today",
    granularity: str = "hour",
):
    """Return cost, tokens, task counts per time bucket."""
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
    delta = range_map.get(time_range, range_map["today"])
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

    usage_rows = await orch.task_board._db.execute_fetchall(
        "SELECT strftime(?, recorded_at) AS bucket, "
        "  model, "
        "  SUM(cost_usd) AS cost, "
        "  SUM(input_tokens) AS input_tokens, "
        "  SUM(output_tokens) AS output_tokens, "
        "  COUNT(*) AS task_count "
        "FROM task_usage WHERE recorded_at >= ? "
        "GROUP BY bucket, model ORDER BY bucket",
        (fmt, since),
    )

    task_rows = await orch.task_board._db.execute_fetchall(
        "SELECT strftime(?, completed_at) AS bucket, "
        "  status, COUNT(*) AS count "
        "FROM tasks WHERE completed_at IS NOT NULL AND completed_at >= ? "
        "GROUP BY bucket, status ORDER BY bucket",
        (fmt, since),
    )

    status_totals = await orch.task_board._db.execute_fetchall(
        "SELECT status, COUNT(*) AS count FROM tasks GROUP BY status"
    )

    return {
        "usage": usage_rows,
        "tasks": task_rows,
        "status_totals": {r["status"]: r["count"] for r in status_totals},
        "since": since,
        "granularity": granularity,
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
    role_costs = await orch.task_board._db.execute_fetchall(
        "SELECT "
        "  SUBSTR(agent_id, 1, INSTR(agent_id, '-') - 1) AS role, "
        "  SUM(cost_usd) AS cost, "
        "  SUM(input_tokens) AS input_tokens, "
        "  SUM(output_tokens) AS output_tokens, "
        "  AVG(duration_api_ms) AS avg_duration_ms, "
        "  SUM(num_turns) AS total_turns "
        "FROM task_usage GROUP BY role ORDER BY cost DESC"
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
        output = io.StringIO()
        if tasks:
            writer = csv.DictWriter(output, fieldnames=tasks[0].keys())
            writer.writeheader()
            writer.writerows(tasks)
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
