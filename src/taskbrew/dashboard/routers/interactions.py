"""Dashboard endpoints for human interaction management -- approve, reject, respond."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from taskbrew.dashboard.models import (
    ApproveInteractionBody, RejectInteractionBody, RespondInteractionBody,
)

router = APIRouter()

_interaction_mgr = None


def set_interaction_deps(interaction_mgr):
    global _interaction_mgr
    _interaction_mgr = interaction_mgr


@router.get("/api/interactions/pending")
async def get_pending(group_id: str = Query(None)):
    if not _interaction_mgr:
        return {"interactions": [], "count": 0}
    pending = await _interaction_mgr.get_pending(group_id)
    return {"interactions": pending, "count": len(pending)}


@router.get("/api/interactions/history")
async def get_history(group_id: str = Query(None), limit: int = Query(50)):
    if not _interaction_mgr:
        return {"interactions": [], "count": 0}
    history = await _interaction_mgr.get_history(group_id, limit)
    return {"interactions": history, "count": len(history)}


@router.post("/api/interactions/{request_id}/approve")
async def approve_interaction(request_id: str, body: ApproveInteractionBody = None):
    if not _interaction_mgr:
        raise HTTPException(500, "Not configured")
    req = await _interaction_mgr.check_status(request_id)
    if not req:
        raise HTTPException(404, f"Interaction not found: {request_id}")
    if req["status"] != "pending":
        raise HTTPException(400, f"Interaction already resolved: {req['status']}")

    response_data = {"notes": body.notes} if body and body.notes else {}
    result = await _interaction_mgr.resolve(request_id, "approved", response_data)

    # If this is a first_run approval, record it
    if req.get("type") == "approval":
        group_id = req.get("group_id")
        agent_role = req.get("agent_role")
        if group_id and agent_role:
            await _interaction_mgr.record_first_run(group_id, agent_role)

    return result


@router.post("/api/interactions/{request_id}/reject")
async def reject_interaction(request_id: str, body: RejectInteractionBody):
    if not _interaction_mgr:
        raise HTTPException(500, "Not configured")
    req = await _interaction_mgr.check_status(request_id)
    if not req:
        raise HTTPException(404, f"Interaction not found: {request_id}")
    if req["status"] != "pending":
        raise HTTPException(400, f"Interaction already resolved: {req['status']}")

    result = await _interaction_mgr.resolve(request_id, "rejected", {"feedback": body.feedback})
    return result


@router.post("/api/interactions/{request_id}/respond")
async def respond_interaction(request_id: str, body: RespondInteractionBody):
    if not _interaction_mgr:
        raise HTTPException(500, "Not configured")
    req = await _interaction_mgr.check_status(request_id)
    if not req:
        raise HTTPException(404, f"Interaction not found: {request_id}")
    if req["status"] != "pending":
        raise HTTPException(400, f"Interaction already resolved: {req['status']}")

    result = await _interaction_mgr.resolve(request_id, "responded", {"response": body.response})
    return result


@router.post("/api/interactions/{request_id}/skip")
async def skip_interaction(request_id: str):
    if not _interaction_mgr:
        raise HTTPException(500, "Not configured")
    req = await _interaction_mgr.check_status(request_id)
    if not req:
        raise HTTPException(404, f"Interaction not found: {request_id}")
    if req["status"] != "pending":
        raise HTTPException(400, f"Interaction already resolved: {req['status']}")

    result = await _interaction_mgr.resolve(request_id, "skipped", {"message": "User chose to skip. Use your best judgment."})
    return result
