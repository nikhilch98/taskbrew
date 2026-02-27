"""Agent instances, pause/resume routes."""

from __future__ import annotations

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
    return await orch.instance_manager.get_all_instances()


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
