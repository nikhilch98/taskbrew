"""Dashboard endpoints for human interaction management -- approve, reject, respond.

Audit 03 F#12 / 10 F#33: the four POST endpoints (approve/reject/respond/skip)
authorise destructive HITL decisions and MUST require admin auth. A blanket
``dependencies=[Depends(verify_admin)]`` at include_router time was reviewed
and rejected because it would also gate the two GET polling endpoints used
by the dashboard UI, so the admin dep is attached per-endpoint below.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from starlette.requests import Request

from taskbrew.dashboard.models import (
    ApproveInteractionBody, RejectInteractionBody, RespondInteractionBody,
)

router = APIRouter()

_interaction_mgr = None
_verify_admin = None


def set_interaction_deps(interaction_mgr, verify_admin=None):
    """Called by app.py to inject deps.

    *verify_admin* is the admin-auth callable (same one passed to
    system_router.set_auth_deps). When omitted (legacy/test shim), the
    per-endpoint admin check becomes a no-op and the middleware is the
    only gate.
    """
    global _interaction_mgr, _verify_admin
    _interaction_mgr = interaction_mgr
    _verify_admin = verify_admin


async def _verify_admin_dep(request: Request):
    """Indirection so ``Depends(_verify_admin_dep)`` can reference the
    injected callable at request time instead of module-load time.
    """
    if _verify_admin is None:
        return  # legacy/test path
    await _verify_admin(request)


@router.get("/api/interactions/pending")
async def get_pending():
    if not _interaction_mgr:
        return {"interactions": [], "count": 0}
    pending = await _interaction_mgr.get_pending()
    return {"interactions": pending, "count": len(pending)}


@router.get("/api/interactions/history")
async def get_history(limit: int = Query(50)):
    if not _interaction_mgr:
        return {"interactions": [], "count": 0}
    history = await _interaction_mgr.get_history(limit)
    return {"interactions": history, "count": len(history)}


@router.post(
    "/api/interactions/{request_id}/approve",
    dependencies=[Depends(_verify_admin_dep)],
)
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


@router.post(
    "/api/interactions/{request_id}/reject",
    dependencies=[Depends(_verify_admin_dep)],
)
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


@router.post(
    "/api/interactions/{request_id}/respond",
    dependencies=[Depends(_verify_admin_dep)],
)
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


@router.post(
    "/api/interactions/{request_id}/skip",
    dependencies=[Depends(_verify_admin_dep)],
)
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
