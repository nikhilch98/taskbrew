"""Intelligence V2 routes: autonomous, code-intel, learning, coordination,
testing-quality, security, observability, and advanced-planning (features 1-50)."""

from __future__ import annotations

import asyncio
import os
from typing import Optional

from fastapi import APIRouter, HTTPException

from taskbrew.dashboard.models import (
    # Autonomous
    DecomposeBody,
    SubmitBidBody,
    RetryOutcomeBody,
    RecordFixBody,
    # Code Intelligence
    CodeSearchBody,
    DebtScoreBody,
    # Learning
    CreateExperimentBody,
    RecordTrialBody,
    CrossProjectKnowledgeBody,
    RecordBenchmarkBody,
    # Coordination
    AcquireLockBody,
    CreateDigestBody,
    CreatePairBody,
    CastVoteBody,
    RecordHeartbeatBody,
    # Testing & Quality
    PredictRegressionBody,
    RecordTimingBody,
    # Security
    FlagSecurityBody,
    # Observability
    LogDecisionBody,
    RecordBehaviorMetricBody,
    AttributeCostBody,
    RecordTrendBody,
    # Advanced Planning
    CheckScopeBody,
    PlanIncrementsBody,
    GeneratePostMortemBody,
)
from taskbrew.dashboard.routers._deps import get_orch

router = APIRouter()


def _validate_path(path: str) -> str:
    """Validate path parameter to prevent directory traversal."""
    normalized = os.path.normpath(path)
    if ".." in normalized.split(os.sep):
        raise HTTPException(400, "Path traversal not allowed")
    return normalized


# ---------------------------------------------------------------------------
# Lazy table-bootstrapping flags (for managers that have ensure_tables())
# ---------------------------------------------------------------------------

_init_lock = asyncio.Lock()
_obs_tables_ensured = False
_planning_tables_ensured = False
_testing_tables_ensured = False
_security_tables_ensured = False


async def _ensure_obs():
    global _obs_tables_ensured
    orch = get_orch()
    if not orch.observability_manager:
        raise HTTPException(503, "Observability manager not initialized")
    if not _obs_tables_ensured:
        async with _init_lock:
            if not _obs_tables_ensured:
                await orch.observability_manager.ensure_tables()
                _obs_tables_ensured = True
    return orch.observability_manager


async def _ensure_planning():
    global _planning_tables_ensured
    orch = get_orch()
    if not orch.advanced_planning_manager:
        raise HTTPException(503, "Advanced planning manager not initialized")
    if not _planning_tables_ensured:
        async with _init_lock:
            if not _planning_tables_ensured:
                await orch.advanced_planning_manager.ensure_tables()
                _planning_tables_ensured = True
    return orch.advanced_planning_manager


async def _ensure_testing():
    global _testing_tables_ensured
    orch = get_orch()
    if not orch.testing_quality_manager:
        raise HTTPException(503, "Testing quality manager not initialized")
    if not _testing_tables_ensured:
        async with _init_lock:
            if not _testing_tables_ensured:
                await orch.testing_quality_manager._ensure_tables()
                _testing_tables_ensured = True
    return orch.testing_quality_manager


async def _ensure_security():
    global _security_tables_ensured
    orch = get_orch()
    if not orch.security_intel_manager:
        raise HTTPException(503, "Security intel manager not initialized")
    if not _security_tables_ensured:
        async with _init_lock:
            if not _security_tables_ensured:
                await orch.security_intel_manager.ensure_tables()
                _security_tables_ensured = True
    return orch.security_intel_manager


# ===================================================================
# Autonomous (Features 1-5) -- orch.autonomous_manager
# ===================================================================


@router.post("/api/v2/autonomous/decompose")
async def decompose_task(body: DecomposeBody):
    orch = get_orch()
    if not orch.autonomous_manager:
        raise HTTPException(503, "Autonomous manager not initialized")
    return await orch.autonomous_manager.decompose_with_reasoning(
        task_id=body.task_id, llm_output=body.llm_output,
    )


@router.post("/api/v2/autonomous/discover")
async def discover_work(agent_id: str, project_dir: str):
    project_dir = _validate_path(project_dir)
    orch = get_orch()
    if not orch.autonomous_manager:
        raise HTTPException(503, "Autonomous manager not initialized")
    return await orch.autonomous_manager.discover_work(
        agent_id=agent_id, project_dir=project_dir,
    )


@router.get("/api/v2/autonomous/discoveries")
async def get_discoveries(status: Optional[str] = None, limit: int = 20):
    orch = get_orch()
    if not orch.autonomous_manager:
        raise HTTPException(503, "Autonomous manager not initialized")
    return await orch.autonomous_manager.get_discoveries(
        status=status or "pending", limit=limit,
    )


@router.post("/api/v2/autonomous/bids")
async def submit_bid(body: SubmitBidBody):
    orch = get_orch()
    if not orch.autonomous_manager:
        raise HTTPException(503, "Autonomous manager not initialized")
    return await orch.autonomous_manager.submit_bid(
        task_id=body.task_id,
        agent_id=body.agent_id,
        workload=body.workload,
        skill=body.skill_match,
        urgency=body.urgency,
    )


@router.post("/api/v2/autonomous/bids/{task_id}/resolve")
async def resolve_bids(task_id: str):
    orch = get_orch()
    if not orch.autonomous_manager:
        raise HTTPException(503, "Autonomous manager not initialized")
    return await orch.autonomous_manager.resolve_bids(task_id=task_id)


@router.post("/api/v2/autonomous/retry-outcomes")
async def record_retry_outcome(body: RetryOutcomeBody):
    orch = get_orch()
    if not orch.autonomous_manager:
        raise HTTPException(503, "Autonomous manager not initialized")
    return await orch.autonomous_manager.record_retry_outcome(
        failure_type=body.failure_type,
        strategy=body.strategy,
        success=body.success,
        recovery_time_ms=body.recovery_time_ms,
    )


@router.get("/api/v2/autonomous/retry-strategies/{failure_type}")
async def get_best_retry_strategy(failure_type: str):
    orch = get_orch()
    if not orch.autonomous_manager:
        raise HTTPException(503, "Autonomous manager not initialized")
    return await orch.autonomous_manager.get_best_retry_strategy(
        failure_type=failure_type,
    )


@router.get("/api/v2/autonomous/similar-fixes/{failure_signature}")
async def find_similar_fix(failure_signature: str):
    orch = get_orch()
    if not orch.autonomous_manager:
        raise HTTPException(503, "Autonomous manager not initialized")
    return await orch.autonomous_manager.find_similar_fix(
        failure_signature=failure_signature,
    )


@router.post("/api/v2/autonomous/fixes")
async def record_fix(body: RecordFixBody):
    orch = get_orch()
    if not orch.autonomous_manager:
        raise HTTPException(503, "Autonomous manager not initialized")
    return await orch.autonomous_manager.record_fix(
        failure_signature=body.failure_signature,
        fix_applied=body.fix_applied,
        success=body.success,
        source_task_id=body.source_task_id,
    )


# ===================================================================
# Code Intelligence (Features 6-12) -- orch.code_intel_manager
# ===================================================================


@router.post("/api/v2/code-intel/index/{file_path:path}")
async def index_file(file_path: str):
    file_path = _validate_path(file_path)
    orch = get_orch()
    if not orch.code_intel_manager:
        raise HTTPException(503, "Code intel manager not initialized")
    count = await orch.code_intel_manager.index_file(file_path=file_path)
    return {"file_path": file_path, "symbols_indexed": count}


@router.post("/api/v2/code-intel/search")
async def search_by_intent(body: CodeSearchBody):
    orch = get_orch()
    if not orch.code_intel_manager:
        raise HTTPException(503, "Code intel manager not initialized")
    return await orch.code_intel_manager.search_by_intent(
        query=body.query, limit=body.limit,
    )


@router.post("/api/v2/code-intel/patterns/{file_path:path}")
async def detect_patterns(file_path: str):
    file_path = _validate_path(file_path)
    orch = get_orch()
    if not orch.code_intel_manager:
        raise HTTPException(503, "Code intel manager not initialized")
    return await orch.code_intel_manager.detect_patterns(file_path=file_path)


@router.get("/api/v2/code-intel/patterns")
async def get_patterns(pattern_type: Optional[str] = None, limit: int = 20):
    orch = get_orch()
    if not orch.code_intel_manager:
        raise HTTPException(503, "Code intel manager not initialized")
    return await orch.code_intel_manager.get_patterns(
        pattern_type=pattern_type, limit=limit,
    )


@router.post("/api/v2/code-intel/smells/{file_path:path}")
async def detect_smells(file_path: str):
    file_path = _validate_path(file_path)
    orch = get_orch()
    if not orch.code_intel_manager:
        raise HTTPException(503, "Code intel manager not initialized")
    return await orch.code_intel_manager.detect_smells(file_path=file_path)


@router.post("/api/v2/code-intel/debt/score")
async def score_debt(body: DebtScoreBody):
    validated_path = _validate_path(body.file_path)
    orch = get_orch()
    if not orch.code_intel_manager:
        raise HTTPException(503, "Code intel manager not initialized")
    return await orch.code_intel_manager.score_debt(file_path=validated_path)


@router.get("/api/v2/code-intel/debt")
async def get_debt_report(limit: int = 20):
    orch = get_orch()
    if not orch.code_intel_manager:
        raise HTTPException(503, "Code intel manager not initialized")
    return await orch.code_intel_manager.get_debt_report(limit=limit)


@router.post("/api/v2/code-intel/test-gaps/{source_file:path}")
async def analyze_test_gaps(source_file: str):
    source_file = _validate_path(source_file)
    orch = get_orch()
    if not orch.code_intel_manager:
        raise HTTPException(503, "Code intel manager not initialized")
    return await orch.code_intel_manager.analyze_test_gaps(source_file=source_file)


@router.post("/api/v2/code-intel/contracts/{router_file:path}")
async def validate_contracts(router_file: str):
    router_file = _validate_path(router_file)
    orch = get_orch()
    if not orch.code_intel_manager:
        raise HTTPException(503, "Code intel manager not initialized")
    return await orch.code_intel_manager.validate_contracts(router_file=router_file)


@router.post("/api/v2/code-intel/dead-code")
async def detect_dead_code(directory: str = "src/"):
    directory = _validate_path(directory)
    orch = get_orch()
    if not orch.code_intel_manager:
        raise HTTPException(503, "Code intel manager not initialized")
    return await orch.code_intel_manager.detect_dead_code(directory=directory)


# ===================================================================
# Learning (Features 13-19) -- orch.learning_manager
# ===================================================================


@router.post("/api/v2/learning/experiments")
async def create_experiment(body: CreateExperimentBody):
    orch = get_orch()
    if not orch.learning_manager:
        raise HTTPException(503, "Learning manager not initialized")
    return await orch.learning_manager.create_experiment(
        name=body.name,
        agent_role=body.role,
        variant_a=body.variant_a,
        variant_b=body.variant_b,
    )


@router.post("/api/v2/learning/experiments/{experiment_id}/trials")
async def record_trial(experiment_id: str, body: RecordTrialBody):
    orch = get_orch()
    if not orch.learning_manager:
        raise HTTPException(503, "Learning manager not initialized")
    return await orch.learning_manager.record_trial(
        experiment_id=experiment_id,
        variant_key=body.variant_key,
        success=body.success,
        quality_score=body.quality_score,
    )


@router.get("/api/v2/learning/experiments/{experiment_id}/winner")
async def get_winner(experiment_id: str):
    orch = get_orch()
    if not orch.learning_manager:
        raise HTTPException(503, "Learning manager not initialized")
    return await orch.learning_manager.get_winner(experiment_id=experiment_id)


@router.post("/api/v2/learning/cross-project")
async def store_cross_project(body: CrossProjectKnowledgeBody):
    orch = get_orch()
    if not orch.learning_manager:
        raise HTTPException(503, "Learning manager not initialized")
    return await orch.learning_manager.store_cross_project(
        source_project=body.source_project,
        knowledge_type=body.knowledge_type,
        title=body.title,
        content=body.content,
    )


@router.get("/api/v2/learning/cross-project")
async def find_applicable(knowledge_type: Optional[str] = None, limit: int = 20):
    orch = get_orch()
    if not orch.learning_manager:
        raise HTTPException(503, "Learning manager not initialized")
    if not knowledge_type:
        raise HTTPException(400, "knowledge_type query parameter is required")
    return await orch.learning_manager.find_applicable(
        knowledge_type=knowledge_type, limit=limit,
    )


@router.post("/api/v2/learning/benchmarks")
async def record_benchmark(body: RecordBenchmarkBody):
    orch = get_orch()
    if not orch.learning_manager:
        raise HTTPException(503, "Learning manager not initialized")
    return await orch.learning_manager.record_benchmark(
        agent_role=body.agent_role,
        metric_name=body.metric,
        metric_value=body.value,
        period=body.period,
        details=body.details,
    )


@router.get("/api/v2/learning/benchmarks/compare")
async def compare_agents(metric: str, period: str = "daily"):
    orch = get_orch()
    if not orch.learning_manager:
        raise HTTPException(503, "Learning manager not initialized")
    return await orch.learning_manager.compare_agents(
        metric_name=metric, period=period,
    )


@router.get("/api/v2/learning/adjustments/{agent_role}")
async def suggest_adjustments(agent_role: str):
    orch = get_orch()
    if not orch.learning_manager:
        raise HTTPException(503, "Learning manager not initialized")
    return await orch.learning_manager.suggest_adjustments(agent_role=agent_role)


@router.get("/api/v2/learning/corrections/{agent_role}")
async def track_repeated_corrections(agent_role: str):
    orch = get_orch()
    if not orch.learning_manager:
        raise HTTPException(503, "Learning manager not initialized")
    return await orch.learning_manager.track_repeated_corrections(
        agent_role=agent_role,
    )


@router.post("/api/v2/learning/conventions")
async def learn_conventions(directory: str = "src/"):
    directory = _validate_path(directory)
    orch = get_orch()
    if not orch.learning_manager:
        raise HTTPException(503, "Learning manager not initialized")
    return await orch.learning_manager.learn_conventions(directory=directory)


@router.get("/api/v2/learning/conventions")
async def get_conventions():
    orch = get_orch()
    if not orch.learning_manager:
        raise HTTPException(503, "Learning manager not initialized")
    return await orch.learning_manager.get_conventions()


@router.post("/api/v2/learning/errors/cluster")
async def cluster_errors(lookback_limit: int = 100):
    orch = get_orch()
    if not orch.learning_manager:
        raise HTTPException(503, "Learning manager not initialized")
    return await orch.learning_manager.cluster_errors(
        lookback_limit=lookback_limit,
    )


@router.get("/api/v2/learning/errors/prevention/{error_pattern}")
async def get_prevention_hints(error_pattern: str):
    orch = get_orch()
    if not orch.learning_manager:
        raise HTTPException(503, "Learning manager not initialized")
    return await orch.learning_manager.get_prevention_hints(
        error_pattern=error_pattern,
    )


# ===================================================================
# Coordination (Features 20-26) -- orch.coordination_manager
# ===================================================================


@router.post("/api/v2/coordination/standups/{agent_id}")
async def generate_standup(agent_id: str):
    orch = get_orch()
    if not orch.coordination_manager:
        raise HTTPException(503, "Coordination manager not initialized")
    return await orch.coordination_manager.generate_standup(agent_id=agent_id)


@router.get("/api/v2/coordination/standups")
async def get_standups(agent_id: Optional[str] = None, limit: int = 10):
    orch = get_orch()
    if not orch.coordination_manager:
        raise HTTPException(503, "Coordination manager not initialized")
    return await orch.coordination_manager.get_standups(
        agent_id=agent_id, limit=limit,
    )


@router.post("/api/v2/coordination/locks")
async def acquire_lock(body: AcquireLockBody):
    validated_path = _validate_path(body.file_path)
    orch = get_orch()
    if not orch.coordination_manager:
        raise HTTPException(503, "Coordination manager not initialized")
    return await orch.coordination_manager.acquire_lock(
        file_path=validated_path,
        agent_id=body.agent_id,
        task_id=body.task_id,
    )


@router.delete("/api/v2/coordination/locks")
async def release_lock(file_path: str, agent_id: str):
    file_path = _validate_path(file_path)
    orch = get_orch()
    if not orch.coordination_manager:
        raise HTTPException(503, "Coordination manager not initialized")
    return await orch.coordination_manager.release_lock(
        file_path=file_path, agent_id=agent_id,
    )


@router.get("/api/v2/coordination/conflicts")
async def detect_conflicts():
    orch = get_orch()
    if not orch.coordination_manager:
        raise HTTPException(503, "Coordination manager not initialized")
    return await orch.coordination_manager.detect_conflicts()


@router.post("/api/v2/coordination/digests")
async def create_digest(body: CreateDigestBody):
    orch = get_orch()
    if not orch.coordination_manager:
        raise HTTPException(503, "Coordination manager not initialized")
    return await orch.coordination_manager.create_digest(
        digest_type=body.digest_type,
        content=body.content,
        target_roles=body.target_roles,
    )


@router.get("/api/v2/coordination/digests")
async def get_digests(role: Optional[str] = None, limit: int = 10):
    orch = get_orch()
    if not orch.coordination_manager:
        raise HTTPException(503, "Coordination manager not initialized")
    return await orch.coordination_manager.get_digests(role=role, limit=limit)


@router.post("/api/v2/coordination/pairs")
async def create_pair(body: CreatePairBody):
    orch = get_orch()
    if not orch.coordination_manager:
        raise HTTPException(503, "Coordination manager not initialized")
    return await orch.coordination_manager.create_pair(
        mentor_role=body.mentor_role,
        mentee_role=body.mentee_role,
        skill_area=body.skill_area,
    )


@router.get("/api/v2/coordination/pairs")
async def get_pairs(role: Optional[str] = None):
    orch = get_orch()
    if not orch.coordination_manager:
        raise HTTPException(503, "Coordination manager not initialized")
    return await orch.coordination_manager.get_pairs(role=role)


@router.post("/api/v2/coordination/proposals/{proposal_id}")
async def create_proposal(proposal_id: str, description: str):
    orch = get_orch()
    if not orch.coordination_manager:
        raise HTTPException(503, "Coordination manager not initialized")
    return await orch.coordination_manager.create_proposal(
        proposal_id=proposal_id, description=description,
    )


@router.post("/api/v2/coordination/votes")
async def cast_vote(body: CastVoteBody):
    orch = get_orch()
    if not orch.coordination_manager:
        raise HTTPException(503, "Coordination manager not initialized")
    return await orch.coordination_manager.cast_vote(
        proposal_id=body.proposal_id,
        voter_id=body.voter_id,
        vote=body.vote,
        reasoning=body.reasoning,
    )


@router.get("/api/v2/coordination/votes/{proposal_id}/tally")
async def tally_votes(proposal_id: str):
    orch = get_orch()
    if not orch.coordination_manager:
        raise HTTPException(503, "Coordination manager not initialized")
    return await orch.coordination_manager.tally_votes(proposal_id=proposal_id)


@router.get("/api/v2/coordination/stealable/{agent_id}")
async def find_stealable_tasks(agent_id: str):
    orch = get_orch()
    if not orch.coordination_manager:
        raise HTTPException(503, "Coordination manager not initialized")
    return await orch.coordination_manager.find_stealable_tasks(agent_id=agent_id)


@router.post("/api/v2/coordination/steal/{task_id}")
async def steal_task(task_id: str, agent_id: str):
    orch = get_orch()
    if not orch.coordination_manager:
        raise HTTPException(503, "Coordination manager not initialized")
    return await orch.coordination_manager.steal_task(
        task_id=task_id, agent_id=agent_id,
    )


@router.post("/api/v2/coordination/heartbeats")
async def record_heartbeat(body: RecordHeartbeatBody):
    orch = get_orch()
    if not orch.coordination_manager:
        raise HTTPException(503, "Coordination manager not initialized")
    return await orch.coordination_manager.record_heartbeat(
        task_id=body.task_id,
        agent_id=body.agent_id,
        progress_pct=body.progress_pct,
        status_message=body.status_message,
    )


@router.get("/api/v2/coordination/heartbeats/{task_id}")
async def get_heartbeats(task_id: str, limit: int = 20):
    orch = get_orch()
    if not orch.coordination_manager:
        raise HTTPException(503, "Coordination manager not initialized")
    return await orch.coordination_manager.get_heartbeats(
        task_id=task_id, limit=limit,
    )


# ===================================================================
# Testing & Quality (Features 27-33) -- orch.testing_quality_manager
# ===================================================================


@router.post("/api/v2/testing/skeletons/{source_file:path}")
async def generate_test_skeletons(source_file: str):
    source_file = _validate_path(source_file)
    mgr = await _ensure_testing()
    return await mgr.generate_test_skeletons(source_file=source_file)


@router.post("/api/v2/testing/mutations/{file_path:path}")
async def run_mutation_analysis(file_path: str):
    file_path = _validate_path(file_path)
    mgr = await _ensure_testing()
    return await mgr.run_mutation_analysis(file_path=file_path)


@router.get("/api/v2/testing/mutations")
async def get_mutation_scores(file_path: Optional[str] = None):
    if file_path is not None:
        file_path = _validate_path(file_path)
    mgr = await _ensure_testing()
    return await mgr.get_mutation_scores(file_path=file_path)


@router.post("/api/v2/testing/property-tests/{source_file:path}")
async def suggest_property_tests(source_file: str):
    source_file = _validate_path(source_file)
    mgr = await _ensure_testing()
    return await mgr.suggest_property_tests(source_file=source_file)


@router.post("/api/v2/testing/regression-risk")
async def predict_regression_risk(body: PredictRegressionBody):
    mgr = await _ensure_testing()
    return await mgr.predict_regression_risk(
        files_changed=body.files_changed,
        pr_identifier=body.pr_identifier,
    )


@router.post("/api/v2/testing/checklists/{task_id}")
async def generate_checklist(task_id: str):
    mgr = await _ensure_testing()
    return await mgr.generate_checklist(task_id=task_id)


@router.post("/api/v2/testing/doc-drift")
async def detect_doc_drift(doc_dir: str = "docs/", code_dir: str = "src/"):
    doc_dir = _validate_path(doc_dir)
    code_dir = _validate_path(code_dir)
    mgr = await _ensure_testing()
    return await mgr.detect_doc_drift(doc_dir=doc_dir, code_dir=code_dir)


@router.post("/api/v2/testing/timings")
async def record_test_timing(body: RecordTimingBody):
    mgr = await _ensure_testing()
    return await mgr.record_test_timing(
        test_name=body.test_name, duration_ms=body.duration_ms,
    )


@router.get("/api/v2/testing/regressions")
async def detect_perf_regressions(threshold_pct: float = 20.0):
    mgr = await _ensure_testing()
    return await mgr.detect_perf_regressions(threshold_pct=threshold_pct)


# ===================================================================
# Security (Features 34-38) -- orch.security_intel_manager
# ===================================================================


@router.post("/api/v2/security/dependencies")
async def scan_dependencies():
    mgr = await _ensure_security()
    return await mgr.scan_dependencies()


@router.get("/api/v2/security/vulnerabilities")
async def get_vulnerabilities(severity: Optional[str] = None):
    mgr = await _ensure_security()
    return await mgr.get_vulnerabilities(severity=severity)


@router.post("/api/v2/security/secrets/{file_path:path}")
async def scan_for_secrets(file_path: str):
    file_path = _validate_path(file_path)
    mgr = await _ensure_security()
    return await mgr.scan_for_secrets(file_path=file_path)


@router.post("/api/v2/security/secrets/directory")
async def scan_directory(directory: str = "src/"):
    directory = _validate_path(directory)
    mgr = await _ensure_security()
    return await mgr.scan_directory(directory=directory)


@router.post("/api/v2/security/sast/{file_path:path}")
async def run_sast(file_path: str):
    file_path = _validate_path(file_path)
    mgr = await _ensure_security()
    return await mgr.run_sast(file_path=file_path)


@router.get("/api/v2/security/sast")
async def get_sast_findings(
    file_path: Optional[str] = None,
    severity: Optional[str] = None,
):
    if file_path is not None:
        file_path = _validate_path(file_path)
    mgr = await _ensure_security()
    return await mgr.get_sast_findings(file_path=file_path, severity=severity)


@router.post("/api/v2/security/licenses")
async def check_licenses():
    mgr = await _ensure_security()
    return await mgr.check_licenses()


@router.post("/api/v2/security/flags")
async def flag_security_changes(body: FlagSecurityBody):
    mgr = await _ensure_security()
    return await mgr.flag_security_changes(
        task_id=body.task_id, files_changed=body.files_changed,
    )


@router.get("/api/v2/security/flags")
async def get_security_flags(
    task_id: Optional[str] = None,
    reviewed: Optional[bool] = None,
):
    mgr = await _ensure_security()
    return await mgr.get_security_flags(task_id=task_id, reviewed=reviewed)


# ===================================================================
# Observability (Features 39-44) -- orch.observability_manager
# ===================================================================


@router.post("/api/v2/observability/decisions")
async def log_decision(body: LogDecisionBody):
    mgr = await _ensure_obs()
    return await mgr.log_decision(
        agent_id=body.agent_id,
        decision_type=body.decision_type,
        decision=body.decision,
        reasoning=body.reasoning,
        task_id=body.task_id,
        context=body.context,
    )


@router.get("/api/v2/observability/decisions")
async def get_audit_trail(
    agent_id: Optional[str] = None,
    task_id: Optional[str] = None,
    limit: int = 50,
):
    mgr = await _ensure_obs()
    return await mgr.get_audit_trail(
        agent_id=agent_id, task_id=task_id, limit=limit,
    )


@router.post("/api/v2/observability/behavior")
async def record_behavior_metric(body: RecordBehaviorMetricBody):
    mgr = await _ensure_obs()
    return await mgr.record_behavior_metric(
        agent_role=body.agent_role,
        metric_type=body.metric_type,
        value=body.value,
        period_start=body.period_start,
        period_end=body.period_end,
        metadata=body.metadata,
    )


@router.get("/api/v2/observability/behavior/{agent_role}")
async def get_behavior_analytics(
    agent_role: str,
    metric_type: Optional[str] = None,
):
    mgr = await _ensure_obs()
    return await mgr.get_behavior_analytics(
        agent_role=agent_role, metric_type=metric_type,
    )


@router.post("/api/v2/observability/costs")
async def attribute_cost(body: AttributeCostBody):
    mgr = await _ensure_obs()
    return await mgr.attribute_cost(
        agent_id=body.agent_id,
        cost_usd=body.cost_usd,
        input_tokens=body.input_tokens,
        output_tokens=body.output_tokens,
        task_id=body.task_id,
        feature_tag=body.feature_tag,
    )


@router.get("/api/v2/observability/costs/by-feature")
async def get_cost_by_feature():
    mgr = await _ensure_obs()
    return await mgr.get_cost_by_feature()


@router.get("/api/v2/observability/costs/by-agent")
async def get_cost_by_agent():
    mgr = await _ensure_obs()
    return await mgr.get_cost_by_agent()


@router.post("/api/v2/observability/bottlenecks")
async def detect_bottlenecks():
    mgr = await _ensure_obs()
    return await mgr.detect_bottlenecks()


@router.post("/api/v2/observability/anomalies/{agent_id}")
async def detect_anomalies(agent_id: str):
    mgr = await _ensure_obs()
    return await mgr.detect_anomalies(agent_id=agent_id)


@router.get("/api/v2/observability/anomalies")
async def get_anomalies(agent_id: Optional[str] = None, limit: int = 50):
    mgr = await _ensure_obs()
    return await mgr.get_anomalies(agent_id=agent_id, limit=limit)


@router.post("/api/v2/observability/trends")
async def record_trend(body: RecordTrendBody):
    mgr = await _ensure_obs()
    return await mgr.record_trend(
        metric_name=body.metric_name,
        metric_value=body.metric_value,
        dimension=body.dimension,
        period=body.period,
    )


@router.get("/api/v2/observability/trends/{metric_name}")
async def get_trends(
    metric_name: str,
    period: Optional[str] = None,
    limit: int = 100,
):
    mgr = await _ensure_obs()
    return await mgr.get_trends(
        metric_name=metric_name, period=period, limit=limit,
    )


# ===================================================================
# Advanced Planning (Features 45-50) -- orch.advanced_planning_manager
# ===================================================================


@router.post("/api/v2/planning/schedule/{group_id}")
async def build_schedule(group_id: str):
    mgr = await _ensure_planning()
    return await mgr.build_schedule(group_id=group_id)


@router.get("/api/v2/planning/schedule/{group_id}")
async def get_schedule(group_id: str):
    mgr = await _ensure_planning()
    return await mgr.get_schedule(group_id=group_id)


@router.get("/api/v2/planning/resources")
async def snapshot_resources():
    mgr = await _ensure_planning()
    return await mgr.snapshot_resources()


@router.post("/api/v2/planning/resources/{group_id}")
async def plan_with_resources(group_id: str):
    mgr = await _ensure_planning()
    return await mgr.plan_with_resources(group_id=group_id)


@router.post("/api/v2/planning/deadline/{task_id}")
async def estimate_deadline(task_id: str):
    mgr = await _ensure_planning()
    return await mgr.estimate_deadline(task_id=task_id)


@router.post("/api/v2/planning/scope-creep")
async def check_scope_creep(body: CheckScopeBody):
    mgr = await _ensure_planning()
    return await mgr.check_scope_creep(
        task_id=body.task_id,
        current_description=body.current_description,
    )


@router.get("/api/v2/planning/scope-flags")
async def get_scope_flags(task_id: Optional[str] = None):
    mgr = await _ensure_planning()
    return await mgr.get_scope_flags(task_id=task_id)


@router.post("/api/v2/planning/increments")
async def plan_increments(body: PlanIncrementsBody):
    mgr = await _ensure_planning()
    return await mgr.plan_increments(
        feature_id=body.feature_id,
        title=body.title,
        description=body.description,
    )


@router.get("/api/v2/planning/increments/{feature_id}")
async def get_increments(feature_id: str):
    mgr = await _ensure_planning()
    return await mgr.get_increments(feature_id=feature_id)


@router.post("/api/v2/planning/post-mortems")
async def generate_post_mortem(body: GeneratePostMortemBody):
    mgr = await _ensure_planning()
    return await mgr.generate_post_mortem(
        task_id=body.task_id, group_id=body.group_id,
    )


@router.get("/api/v2/planning/post-mortems")
async def get_post_mortems(limit: int = 20):
    mgr = await _ensure_planning()
    return await mgr.get_post_mortems(limit=limit)
