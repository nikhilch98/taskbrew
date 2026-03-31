"""MCP tool endpoints for agent-orchestrator communication."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Header
from typing import Optional

router = APIRouter()

_interaction_mgr = None
_pipeline_getter = None


def set_mcp_deps(interaction_mgr, pipeline_getter):
    """Set dependencies. Called from app.py startup."""
    global _interaction_mgr, _pipeline_getter
    _interaction_mgr = interaction_mgr
    _pipeline_getter = pipeline_getter


def _get_token(authorization: Optional[str] = Header(None)) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing or invalid Authorization header")
    return authorization[7:]


@router.post("/mcp/tools/complete_task")
async def mcp_complete_task(
    body: dict,
    authorization: Optional[str] = Header(None),
):
    """Agent calls this when task is done. Creates approval request if needed."""
    token = _get_token(authorization)
    artifact_paths = body.get("artifact_paths", [])
    summary = body.get("summary", "")
    task_id = body.get("task_id", "")
    group_id = body.get("group_id", "")
    agent_role = body.get("agent_role", "")
    approval_mode = body.get("approval_mode", "auto")

    if approval_mode == "auto":
        return {"status": "approved", "message": "Auto-approved"}

    if approval_mode == "first_run" and _interaction_mgr:
        already_approved = await _interaction_mgr.check_first_run(group_id, agent_role)
        if already_approved:
            return {"status": "approved", "message": "First-run already approved for this group"}

    # Create approval interaction request
    if _interaction_mgr:
        req = await _interaction_mgr.create_request(
            task_id=task_id, group_id=group_id, agent_role=agent_role,
            instance_token=token, req_type="approval",
            request_data={"artifact_paths": artifact_paths, "summary": summary},
            request_key=f"{task_id}:approval:0",
        )
        return {"status": "pending", "request_id": req["id"], "message": "Awaiting human approval"}

    return {"status": "approved", "message": "No interaction manager configured"}


@router.post("/mcp/tools/request_clarification")
async def mcp_request_clarification(
    body: dict,
    authorization: Optional[str] = Header(None),
):
    """Agent asks user a question. Creates clarification request."""
    token = _get_token(authorization)
    question = body.get("question", "")
    context = body.get("context", "")
    suggested_options = body.get("suggested_options", [])
    task_id = body.get("task_id", "")
    group_id = body.get("group_id", "")
    agent_role = body.get("agent_role", "")

    if _interaction_mgr:
        req = await _interaction_mgr.create_request(
            task_id=task_id, group_id=group_id, agent_role=agent_role,
            instance_token=token, req_type="clarification",
            request_data={"question": question, "context": context, "suggested_options": suggested_options},
        )
        return {"status": "pending", "request_id": req["id"]}

    raise HTTPException(500, "Interaction manager not configured")


@router.post("/mcp/tools/route_task")
async def mcp_route_task(
    body: dict,
    authorization: Optional[str] = Header(None),
):
    """Agent routes a task to a connected agent. Validates pipeline edge."""
    token = _get_token(authorization)
    target_agent = body.get("target_agent", "")
    task_type = body.get("task_type", "")
    title = body.get("title", "")
    description = body.get("description", "")
    agent_role = body.get("agent_role", "")

    # Validate pipeline edge exists
    if _pipeline_getter:
        pipeline = _pipeline_getter()
        valid_targets = [e.to_agent for e in pipeline.edges if e.from_agent == agent_role]
        if target_agent not in valid_targets:
            raise HTTPException(400, f"No pipeline edge from '{agent_role}' to '{target_agent}'. Available targets: {valid_targets}")
        # Check task_type is allowed on the edge
        edge = next((e for e in pipeline.edges if e.from_agent == agent_role and e.to_agent == target_agent), None)
        if edge and edge.task_types and task_type not in edge.task_types:
            raise HTTPException(400, f"Task type '{task_type}' not allowed on edge {agent_role}->{target_agent}. Allowed: {edge.task_types}")

    # In a full implementation, this would create the actual task in the task board
    # For now, return success with a placeholder task_id
    import uuid
    return {"status": "ok", "task_id": f"routed-{uuid.uuid4().hex[:8]}", "target": target_agent}


@router.post("/mcp/tools/get_my_connections")
async def mcp_get_connections(
    body: dict,
    authorization: Optional[str] = Header(None),
):
    """Returns outbound pipeline edges for the requesting agent."""
    token = _get_token(authorization)
    agent_role = body.get("agent_role", "")

    if _pipeline_getter:
        pipeline = _pipeline_getter()
        connections = [
            {"target": e.to_agent, "task_types": e.task_types}
            for e in pipeline.edges if e.from_agent == agent_role
        ]
        return {"agent_role": agent_role, "connections": connections}

    return {"agent_role": agent_role, "connections": []}


@router.get("/mcp/tools/poll/{request_id}")
async def mcp_poll(request_id: str, authorization: Optional[str] = Header(None)):
    """Long-poll endpoint. Returns current status of an interaction request."""
    _get_token(authorization)
    if _interaction_mgr:
        req = await _interaction_mgr.check_status(request_id)
        if not req:
            raise HTTPException(404, f"Request not found: {request_id}")
        return {"status": req["status"], "response_data": req.get("response_data")}
    raise HTTPException(500, "Interaction manager not configured")
