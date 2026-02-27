"""Intelligence-layer routes: quality, knowledge graph, skills, model routing,
memories, review patterns, planning, preflight, collaboration, messaging,
escalations, checkpoints, and tools."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException

from taskbrew.dashboard.models import (
    AssessRiskBody,
    CreateEscalationBody,
    DecideCheckpointBody,
    PairSessionBody,
    PeerReviewBody,
    RebuildKGRequest,
    ResolveEscalationBody,
    SelectToolsBody,
    SendMessageBody,
    SetModelRoutingBody,
    StartDebateBody,
    StoreMemoryBody,
)
from taskbrew.dashboard.routers._deps import get_orch

router = APIRouter()


# ------------------------------------------------------------------
# Agent Memory
# ------------------------------------------------------------------


@router.get("/api/memories")
async def get_memories(role: str = "", memory_type: str = "", limit: int = 50):
    orch = get_orch()
    if not orch.memory_manager:
        raise HTTPException(status_code=503, detail="Memory manager not initialized")
    return await orch.memory_manager.get_memories(
        agent_role=role or None,
        memory_type=memory_type or None,
        limit=limit,
    )


@router.post("/api/memories")
async def store_memory(body: StoreMemoryBody):
    orch = get_orch()
    if not orch.memory_manager:
        raise HTTPException(status_code=503, detail="Memory manager not initialized")
    return await orch.memory_manager.store_memory(
        agent_role=body.agent_role,
        memory_type=body.memory_type,
        title=body.title,
        content=body.content,
        source_task_id=body.source_task_id,
        tags=body.tags,
        project_id=body.project_id,
    )


@router.delete("/api/memories/{memory_id}")
async def delete_memory(memory_id: int):
    orch = get_orch()
    if not orch.memory_manager:
        raise HTTPException(status_code=503, detail="Memory manager not initialized")
    await orch.memory_manager.delete_memory(memory_id)
    return {"status": "deleted"}


# ------------------------------------------------------------------
# Planning & Pre-flight
# ------------------------------------------------------------------


@router.get("/api/tasks/{task_id}/plans")
async def get_task_plans(task_id: str, plan_type: str = ""):
    orch = get_orch()
    _db = orch.task_board._db
    if plan_type:
        return await _db.execute_fetchall(
            "SELECT * FROM task_plans WHERE task_id = ? AND plan_type = ? ORDER BY created_at DESC",
            (task_id, plan_type),
        )
    return await _db.execute_fetchall(
        "SELECT * FROM task_plans WHERE task_id = ? ORDER BY created_at DESC",
        (task_id,),
    )


@router.post("/api/tasks/{task_id}/estimate")
async def estimate_task(task_id: str):
    orch = get_orch()
    if not orch.planning_manager:
        raise HTTPException(status_code=503, detail="Planning manager not initialized")
    return await orch.planning_manager.estimate_effort(task_id)


@router.post("/api/tasks/{task_id}/risk")
async def assess_risk(task_id: str, body: Optional[AssessRiskBody] = None):
    orch = get_orch()
    if not orch.planning_manager:
        raise HTTPException(status_code=503, detail="Planning manager not initialized")
    files = body.files if body else []
    return await orch.planning_manager.assess_risk(task_id, files_to_change=files)


@router.post("/api/tasks/{task_id}/preflight")
async def run_preflight(task_id: str):
    orch = get_orch()
    if not orch.preflight_checker:
        raise HTTPException(status_code=503, detail="Preflight checker not initialized")
    _db = orch.task_board._db
    task = await _db.execute_fetchone("SELECT * FROM tasks WHERE id = ?", (task_id,))
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return await orch.preflight_checker.run_checks(task, role=task.get("assigned_to", ""))


# ------------------------------------------------------------------
# Collaboration endpoints
# ------------------------------------------------------------------


@router.post("/api/tasks/{task_id}/peer-review")
async def request_peer_review(task_id: str, body: Optional[PeerReviewBody] = None):
    orch = get_orch()
    if not orch.collaboration_manager:
        raise HTTPException(status_code=503, detail="Collaboration manager not initialized")
    reviewer_role = body.reviewer_role if body else "coder"
    return await orch.collaboration_manager.request_peer_review(task_id, reviewer_role)


@router.post("/api/tasks/{task_id}/pair")
async def start_pair_session(task_id: str, body: PairSessionBody):
    orch = get_orch()
    if not orch.collaboration_manager:
        raise HTTPException(status_code=503, detail="Collaboration manager not initialized")
    return await orch.collaboration_manager.start_pair_session(task_id, body.agent1, body.agent2)


@router.post("/api/tasks/{task_id}/debate")
async def start_debate(task_id: str, body: Optional[StartDebateBody] = None):
    orch = get_orch()
    if not orch.collaboration_manager:
        raise HTTPException(status_code=503, detail="Collaboration manager not initialized")
    debater_role = body.debater_role if body else "coder"
    judge_role = body.judge_role if body else "architect"
    return await orch.collaboration_manager.start_debate(task_id, debater_role, judge_role)


@router.get("/api/collaborations")
async def get_collaborations(limit: int = 10):
    orch = get_orch()
    if not orch.collaboration_manager:
        raise HTTPException(status_code=503, detail="Collaboration manager not initialized")
    return await orch.collaboration_manager.get_active_collaborations(limit=limit)


# ------------------------------------------------------------------
# Quality Scores
# ------------------------------------------------------------------


@router.get("/api/quality/scores")
async def get_quality_scores(task_id: str = "", score_type: str = "", limit: int = 50):
    orch = get_orch()
    if not orch.quality_manager:
        raise HTTPException(status_code=503, detail="Quality manager not initialized")
    return await orch.quality_manager.get_scores(
        task_id=task_id or None,
        score_type=score_type or None,
        limit=limit,
    )


@router.get("/api/tasks/{task_id}/quality")
async def get_task_quality(task_id: str):
    orch = get_orch()
    if not orch.quality_manager:
        raise HTTPException(status_code=503, detail="Quality manager not initialized")
    return await orch.quality_manager.get_task_quality_summary(task_id)


# ------------------------------------------------------------------
# Execution Tools
# ------------------------------------------------------------------


@router.get("/api/tools/profiles")
async def get_tool_profiles():
    from taskbrew.intelligence.tool_router import TOOL_PROFILES, ROLE_TOOLS
    return {"task_profiles": TOOL_PROFILES, "role_tools": ROLE_TOOLS}


@router.post("/api/tools/select")
async def select_tools(body: SelectToolsBody):
    orch = get_orch()
    if not orch.tool_router:
        raise HTTPException(status_code=503, detail="Tool router not initialized")
    tools = await orch.tool_router.select_tools(
        task_type=body.task_type,
        role=body.role,
        complexity=body.complexity,
    )
    return {"tools": tools}


# ------------------------------------------------------------------
# Knowledge Graph
# ------------------------------------------------------------------


@router.get("/api/knowledge-graph/stats")
async def get_kg_stats():
    orch = get_orch()
    if not orch.knowledge_graph:
        raise HTTPException(status_code=503, detail="Knowledge graph not initialized")
    return await orch.knowledge_graph.get_graph_stats()


@router.get("/api/knowledge-graph/dependencies")
async def get_kg_dependencies(name: str):
    orch = get_orch()
    if not orch.knowledge_graph:
        raise HTTPException(status_code=503, detail="Knowledge graph not initialized")
    return await orch.knowledge_graph.query_dependencies(name)


@router.get("/api/knowledge-graph/dependents")
async def get_kg_dependents(name: str):
    orch = get_orch()
    if not orch.knowledge_graph:
        raise HTTPException(status_code=503, detail="Knowledge graph not initialized")
    return await orch.knowledge_graph.query_dependents(name)


@router.post("/api/knowledge-graph/rebuild")
async def rebuild_kg(body: Optional[RebuildKGRequest] = None):
    orch = get_orch()
    if not orch.knowledge_graph:
        raise HTTPException(status_code=503, detail="Knowledge graph not initialized")
    directory = body.directory if body else "src/"
    return await orch.knowledge_graph.build_from_directory(directory)


# ------------------------------------------------------------------
# Review Patterns
# ------------------------------------------------------------------


@router.get("/api/review-patterns")
async def get_review_patterns(reviewer: str = "", limit: int = 10):
    orch = get_orch()
    if not orch.review_learning:
        raise HTTPException(status_code=503, detail="Review learning manager not initialized")
    return await orch.review_learning.get_top_patterns(
        reviewer=reviewer or None,
        limit=limit,
    )


@router.get("/api/review-patterns/{reviewer}/stats")
async def get_reviewer_stats(reviewer: str):
    orch = get_orch()
    if not orch.review_learning:
        raise HTTPException(status_code=503, detail="Review learning manager not initialized")
    return await orch.review_learning.get_reviewer_stats(reviewer)


# ------------------------------------------------------------------
# Skills & Specialization
# ------------------------------------------------------------------


@router.get("/api/skills")
async def get_skills(agent_role: str = ""):
    orch = get_orch()
    if not orch.specialization_manager:
        raise HTTPException(status_code=503, detail="Specialization manager not initialized")
    if agent_role:
        return await orch.specialization_manager.get_agent_skills(agent_role)
    # Return all skill badges
    return await orch.task_board._db.execute_fetchall(
        "SELECT * FROM skill_badges ORDER BY agent_role, proficiency DESC"
    )


@router.get("/api/skills/best")
async def get_best_agent(skill_type: str):
    orch = get_orch()
    if not orch.specialization_manager:
        raise HTTPException(status_code=503, detail="Specialization manager not initialized")
    return await orch.specialization_manager.get_best_agent_for_task(skill_type) or {"message": "No agent found"}


@router.get("/api/model-routing")
async def get_model_routing(role: str = ""):
    orch = get_orch()
    if not orch.specialization_manager:
        raise HTTPException(status_code=503, detail="Specialization manager not initialized")
    return await orch.specialization_manager.get_routing_rules(role=role or None)


@router.post("/api/model-routing")
async def set_model_routing(body: SetModelRoutingBody):
    orch = get_orch()
    if not orch.specialization_manager:
        raise HTTPException(status_code=503, detail="Specialization manager not initialized")
    rule_id = await orch.specialization_manager.set_routing_rule(
        role=body.role,
        complexity=body.complexity,
        model=body.model,
        criteria=body.criteria,
    )
    return {"id": rule_id}


@router.get("/api/role-gaps")
async def get_role_gaps():
    orch = get_orch()
    if not orch.specialization_manager:
        raise HTTPException(status_code=503, detail="Specialization manager not initialized")
    return await orch.specialization_manager.detect_role_gaps()


# ------------------------------------------------------------------
# Agent Messaging
# ------------------------------------------------------------------


@router.get("/api/messages/{agent_id}")
async def get_agent_messages(agent_id: str, unread_only: bool = True):
    orch = get_orch()
    if unread_only:
        return await orch.task_board._db.execute_fetchall(
            "SELECT * FROM agent_messages WHERE to_agent = ? AND read = 0 ORDER BY created_at DESC",
            (agent_id,)
        )
    return await orch.task_board._db.execute_fetchall(
        "SELECT * FROM agent_messages WHERE to_agent = ? OR from_agent = ? ORDER BY created_at DESC LIMIT 50",
        (agent_id, agent_id)
    )


@router.get("/api/messages")
async def get_messages(agent_id: str = "", unread_only: bool = True, limit: int = 20):
    orch = get_orch()
    if not agent_id:
        return await orch.task_board._db.execute_fetchall(
            "SELECT * FROM agent_messages ORDER BY created_at DESC LIMIT ?", (limit,)
        )
    return await orch.task_board._db.execute_fetchall(
        "SELECT * FROM agent_messages WHERE to_agent = ? AND (? = 0 OR read = 0) ORDER BY created_at DESC LIMIT ?",
        (agent_id, 0 if not unread_only else 1, limit),
    )


@router.post("/api/messages")
async def send_agent_message(body: SendMessageBody):
    orch = get_orch()
    now = datetime.now(timezone.utc).isoformat()
    await orch.task_board._db.execute(
        "INSERT INTO agent_messages (from_agent, to_agent, content, message_type, priority, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (body.from_agent, body.to_agent, body.content, body.message_type, body.priority, now),
    )
    await orch.event_bus.emit("agent.message", {"from": body.from_agent, "to": body.to_agent})
    return {"status": "sent", "from": body.from_agent, "to": body.to_agent}


# ------------------------------------------------------------------
# Escalations
# ------------------------------------------------------------------


@router.get("/api/escalations")
async def get_escalations(status: str = "open", limit: int = 20):
    orch = get_orch()
    if status:
        return await orch.task_board._db.execute_fetchall(
            "SELECT * FROM escalations WHERE status = ? ORDER BY created_at DESC LIMIT ?",
            (status, limit),
        )
    return await orch.task_board._db.execute_fetchall(
        "SELECT * FROM escalations ORDER BY created_at DESC LIMIT ?", (limit,)
    )


@router.post("/api/escalations")
async def create_escalation(body: CreateEscalationBody):
    orch = get_orch()
    now = datetime.now(timezone.utc).isoformat()
    await orch.task_board._db.execute(
        "INSERT INTO escalations (task_id, from_agent, to_agent, reason, severity, status, created_at) "
        "VALUES (?, ?, ?, ?, ?, 'open', ?)",
        (body.task_id, body.from_agent, body.to_agent, body.reason, body.severity, now),
    )
    return {"status": "escalated", "task_id": body.task_id}


@router.post("/api/escalations/{escalation_id}/resolve")
async def resolve_escalation(escalation_id: int, body: ResolveEscalationBody):
    orch = get_orch()
    now = datetime.now(timezone.utc).isoformat()
    await orch.task_board._db.execute(
        "UPDATE escalations SET status = 'resolved', resolution = ?, resolved_at = ? WHERE id = ?",
        (body.resolution, now, escalation_id),
    )
    return {"status": "resolved", "id": escalation_id}


# ------------------------------------------------------------------
# Checkpoints
# ------------------------------------------------------------------


@router.get("/api/checkpoints")
async def get_checkpoints(status: str = "pending", limit: int = 20):
    orch = get_orch()
    return await orch.task_board._db.execute_fetchall(
        "SELECT * FROM checkpoints WHERE status = ? ORDER BY created_at DESC LIMIT ?",
        (status, limit),
    )


@router.post("/api/checkpoints/{checkpoint_id}/decide")
async def decide_checkpoint(checkpoint_id: int, body: DecideCheckpointBody):
    orch = get_orch()
    now = datetime.now(timezone.utc).isoformat()
    status = "approved" if body.approved else "rejected"
    await orch.task_board._db.execute(
        "UPDATE checkpoints SET status = ?, decided_by = ?, decided_at = ? WHERE id = ?",
        (status, body.decided_by, now, checkpoint_id),
    )
    return {"status": status, "id": checkpoint_id}
