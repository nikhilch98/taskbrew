"""Agent instances, pause/resume routes."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from taskbrew.dashboard.models import PauseResumeBody
from taskbrew.dashboard.routers._deps import get_orch

router = APIRouter()


# ------------------------------------------------------------------
# Agents
# ------------------------------------------------------------------


@router.get("/api/agents")
async def get_agents():
    orch = get_orch()
    agents = await orch.instance_manager.get_all_instances()
    agents.extend(await _system_agent_snapshots(orch))
    return agents


async def _system_agent_snapshots(orch) -> list[dict]:
    """Return virtual agent rows for project-level system gates."""
    max_instances = max(
        1,
        int(getattr(getattr(orch.team_config, "system_agent", None), "max_instances", 1) or 1),
    )
    task_rows = await orch.task_board._db.execute_fetchall(
        "SELECT id, status, backlog_intake_status, review_status, created_at "
        "FROM tasks WHERE "
        "(status = 'review' AND review_status = 'running') "
        "OR (status = 'backlog' AND backlog_intake_status = 'running') "
        "ORDER BY CASE WHEN status = 'review' THEN 0 ELSE 1 END, created_at "
        "LIMIT ?",
        (max_instances,),
    )
    gate_rows = await orch.task_board._db.execute_fetchall(
        "SELECT entity_id AS id, status, created_at "
        "FROM review_gates WHERE entity_type = 'work_package' AND status = 'running' "
        "ORDER BY created_at LIMIT ?",
        (max_instances,),
    )
    work_items = [
        {
            "id": row["id"],
            "gate": "review" if row["status"] == "review" else "backlog",
            "detail": "reviewing" if row["status"] == "review" else "checking",
            "created_at": row.get("created_at"),
        }
        for row in task_rows
    ]
    work_items.extend(
        {
            "id": row["id"],
            "gate": "package_review",
            "detail": "reviewing package",
            "created_at": row.get("created_at"),
        }
        for row in gate_rows
    )
    work_items = sorted(
        work_items,
        key=lambda item: (
            {"review": 0, "package_review": 1, "backlog": 2}.get(item["gate"], 3),
            item.get("created_at") or "",
        ),
    )[:max_instances]
    now = datetime.now(timezone.utc).isoformat()
    snapshots = []
    for index in range(max_instances):
        work_item = work_items[index] if index < len(work_items) else None
        snapshots.append(
            {
                "instance_id": "system-agent" if index == 0 else f"system-agent-{index + 1}",
                "role": "system",
                "status": "working" if work_item else "idle",
                "current_task": work_item["id"] if work_item else None,
                "current_gate": work_item["gate"] if work_item else None,
                "status_detail": work_item["detail"] if work_item else "idle",
                "started_at": None,
                "last_heartbeat": now,
            }
        )
    return snapshots


# ------------------------------------------------------------------
# Pause / Resume
# ------------------------------------------------------------------


@router.post("/api/agents/pause")
async def pause_agents(body: PauseResumeBody):
    orch = get_orch()
    role = body.role
    if role == "all":
        all_roles = list(orch.roles.keys()) if orch.roles else []
        orch.instance_manager.pause_all(all_roles)
        await orch.event_bus.emit("team.paused", {"roles": all_roles})
        return {"status": "ok", "paused": all_roles}
    elif role:
        orch.instance_manager.pause_role(role)
        await orch.event_bus.emit("role.paused", {"role": role})
        return {"status": "ok", "paused": [role]}
    raise HTTPException(status_code=400, detail="role is required")


@router.post("/api/agents/resume")
async def resume_agents(body: PauseResumeBody):
    orch = get_orch()
    role = body.role
    if role == "all":
        orch.instance_manager.resume_all()
        await orch.event_bus.emit("team.resumed", {})
        return {"status": "ok", "resumed": "all"}
    elif role:
        orch.instance_manager.resume_role(role)
        await orch.event_bus.emit("role.resumed", {"role": role})
        return {"status": "ok", "resumed": [role]}
    raise HTTPException(status_code=400, detail="role is required")


@router.get("/api/agents/paused")
async def get_paused():
    orch = get_orch()
    return {"paused_roles": orch.instance_manager.get_paused_roles()}
