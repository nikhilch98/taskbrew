"""Intelligence V3 API endpoints — 50 new agent intelligence features."""
from __future__ import annotations

import asyncio
import os
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ._deps import get_orch

# ===================================================================
# Pydantic request body models
# ===================================================================

# -- Self-Improvement --
class PromptVersionBody(BaseModel):
    agent_role: str
    prompt_text: str
    version_tag: str | None = None

class PromptOutcomeBody(BaseModel):
    version_id: str
    task_id: str
    success: bool
    quality_score: float | None = None

class RegisterStrategyBody(BaseModel):
    agent_role: str
    strategy_name: str
    strategy_type: str
    description: str | None = None

class StrategyUseBody(BaseModel):
    strategy_id: str
    task_id: str
    success: bool
    duration_ms: int | None = None

class CreateTransferBody(BaseModel):
    source_role: str
    target_role: str
    skill_area: str
    knowledge_content: str

class CognitiveLoadBody(BaseModel):
    agent_id: str
    context_tokens: int
    max_tokens: int
    active_files: int
    task_id: str | None = None

class CreateReflectionBody(BaseModel):
    task_id: str
    agent_id: str
    what_worked: str
    what_failed: str
    lessons: str
    approach_rating: float

class SearchReflectionsBody(BaseModel):
    task_description: str
    limit: int = 10

class ClassifyFailureBody(BaseModel):
    task_id: str
    category: str
    subcategory: str
    description: str
    severity: str

class UpdateProfileBody(BaseModel):
    agent_role: str
    trait: str
    value: float
    evidence_task_id: str | None = None

class MatchTaskBody(BaseModel):
    task_type: str
    required_traits: dict[str, float]

class RecordConfidenceBody(BaseModel):
    agent_id: str
    task_id: str
    predicted_confidence: float
    actual_success: bool | None = None

# -- Social Intelligence --
class OpenArgumentBody(BaseModel):
    topic: str
    participants: list[str]
    context: str | None = None

class SubmitEvidenceBody(BaseModel):
    agent_id: str
    position: str
    evidence: str
    confidence: float

class ResolveArgumentBody(BaseModel):
    pass  # resolve_argument only needs session_id from URL path

class UpdateTrustBody(BaseModel):
    from_agent: str
    to_agent: str
    interaction_type: str
    outcome_quality: float

class RecordPreferenceBody(BaseModel):
    agent_role: str
    preference_key: str
    preference_value: str

class AssertFactBody(BaseModel):
    key: str
    value: str
    source_agent: str
    confidence: float = 1.0

class RetractFactBody(BaseModel):
    key: str
    agent_id: str

class ReportWorkAreaBody(BaseModel):
    agent_id: str
    file_paths: list[str]
    task_id: str | None = None

class ShareContextBody(BaseModel):
    from_agent: str
    to_agent: str
    context_key: str
    context_value: str
    relevance_score: float = 1.0

class RecordCollaborationBody(BaseModel):
    agent_a: str
    agent_b: str
    task_id: str
    effectiveness: float
    notes: str | None = None

class PredictConsensusBody(BaseModel):
    proposal_description: str
    participants: list[str]

class RecordPredictionOutcomeBody(BaseModel):
    actual_outcome: str

# -- Code Reasoning --
class IndexIntentBody(BaseModel):
    file_path: str
    function_name: str
    intent_description: str
    keywords: list[str] | str

class RecordDependencyBody(BaseModel):
    source_file: str
    target_file: str
    dep_type: str = "import"

class RecordPatternBody(BaseModel):
    pattern_name: str
    category: str
    example: str
    file_path: str | None = None

class CheckConformanceBody(BaseModel):
    file_path: str
    content: str

class DetectOpportunitiesBody(BaseModel):
    file_path: str
    content: str

class AddDebtBody(BaseModel):
    file_path: str
    category: str
    description: str
    effort_estimate: int
    business_impact: int

class RecordApiVersionBody(BaseModel):
    endpoint: str
    method: str
    version: str
    schema_hash: str
    breaking_change: bool = False

class GenerateNarrativeBody(BaseModel):
    file_path: str
    function_name: str
    code_snippet: str
    narrative_text: str

class RecordInvariantBody(BaseModel):
    file_path: str
    function_name: str
    invariant_expression: str
    invariant_type: str

# -- Task Intelligence --
class EstimateComplexityBody(BaseModel):
    task_id: str
    title: str
    description: str | None = None

class CalibrateBody(BaseModel):
    actual_complexity: int

class DetectPrerequisitesBody(BaseModel):
    task_id: str
    description: str
    files_involved: list[str] | None = None

class ConfirmPrerequisiteBody(BaseModel):
    confirmed: bool = True

class RecordDecompositionBody(BaseModel):
    parent_task_id: str
    subtask_count: int
    avg_subtask_duration_ms: float
    success_rate: float
    task_type: str | None = None

class FindParallelTasksBody(BaseModel):
    group_id: str

class PlanBudgetBody(BaseModel):
    task_id: str
    estimated_files: int
    estimated_tokens_per_file: int = 500

class RecordActualBudgetBody(BaseModel):
    actual_tokens_used: int

class PredictOutcomeBody(BaseModel):
    task_id: str
    complexity_score: int
    agent_role: str
    historical_success_rate: float | None = None

class RecordActualOutcomeBody(BaseModel):
    success: bool

class FingerprintTaskBody(BaseModel):
    task_id: str
    title: str
    description: str | None = None
    task_type: str | None = None

class FindSimilarTasksBody(BaseModel):
    title: str
    description: str | None = None
    limit: int = 10

class StartTrackingBody(BaseModel):
    task_id: str
    estimated_duration_ms: int

class CompleteTrackingBody(BaseModel):
    pass  # complete_tracking only needs task_id from URL path

# -- Verification --
class FingerprintRegressionBody(BaseModel):
    test_name: str
    error_message: str
    failing_commit: str
    last_passing_commit: str | None = None

class RecordMappingBody(BaseModel):
    source_file: str
    test_file: str
    confidence: float | None = None

class AutoMapBody(BaseModel):
    test_dir: str
    source_dir: str

class RecordRunBody(BaseModel):
    test_name: str
    passed: bool
    duration_ms: int | None = None
    run_id: str | None = None

class MineSpecBody(BaseModel):
    test_file: str
    test_name: str
    asserted_behavior: str

class AnnotateBody(BaseModel):
    file_path: str
    line_number: int
    annotation_type: str
    message: str
    severity: str = "info"

class AutoAnnotateBody(BaseModel):
    file_path: str
    content: str

class DefineGateBody(BaseModel):
    gate_name: str
    conditions: dict
    risk_level: str = "standard"

class EvaluateGateBody(BaseModel):
    metrics: dict

# -- Process Intelligence --
class RecordVelocityBody(BaseModel):
    sprint_id: str
    tasks_completed: int
    story_points: float | None = None
    duration_days: float | None = None

class ForecastBody(BaseModel):
    remaining_points: int
    num_simulations: int = 1000

class ScoreFileBody(BaseModel):
    file_path: str
    change_frequency: int
    complexity_score: float
    test_coverage_pct: float

class RecordPhaseDurationBody(BaseModel):
    task_id: str
    phase: str
    duration_ms: int

class AssessReadinessBody(BaseModel):
    release_id: str
    metrics: dict

class RecordImpactBody(BaseModel):
    change_id: str
    stakeholder_group: str
    impact_level: str
    description: str

class GenerateRetroBody(BaseModel):
    sprint_id: str
    tasks_data: list[dict] | None = None

# -- Knowledge Management --
class TrackKnowledgeBody(BaseModel):
    key: str
    content: str
    source_file: str | None = None
    source_agent: str | None = None

class ScanForGapsBody(BaseModel):
    code_files: list[str]
    doc_files: list[str]

class ExtractFromCommitBody(BaseModel):
    commit_hash: str
    commit_message: str
    author: str
    files_changed: list[str]

class ExtractFromCommentBody(BaseModel):
    file_path: str
    line_number: int
    comment_text: str

class CompressContextBody(BaseModel):
    context_items: list[dict]
    max_tokens: int
    strategy: str | None = None

class RecordCompressionBody(BaseModel):
    task_id: str
    original_tokens: int
    compressed_tokens: int
    items_kept: int
    items_dropped: int

class SetSalienceWeightsBody(BaseModel):
    recency_weight: float = 0.3
    relevance_weight: float = 0.5
    frequency_weight: float = 0.2

# -- Compliance --
class CreateModelBody(BaseModel):
    feature_name: str
    description: str
    data_flows: list[str] | None = None

class AddThreatBody(BaseModel):
    threat_type: str
    description: str
    risk_level: str
    mitigation: str | None = None

class AddRuleBody(BaseModel):
    rule_id: str
    framework: str
    category: str
    description: str
    check_pattern: str
    severity: str = "medium"

class CheckFileBody(BaseModel):
    file_path: str
    content: str

class RecordCheckBody(BaseModel):
    file_path: str
    violations_found: int
    rules_checked: int

class AddExemptionBody(BaseModel):
    rule_id: str
    file_path: str
    reason: str
    approved_by: str


# ===================================================================
# Router setup
# ===================================================================

router = APIRouter(prefix="/api/v3", tags=["Intelligence V3"])


def _validate_path(path: str) -> str:
    """Validate path parameter to prevent directory traversal."""
    normalized = os.path.normpath(path)
    if ".." in normalized.split(os.sep):
        raise HTTPException(400, "Path traversal not allowed")
    return normalized


# ---------------------------------------------------------------------------
# Lazy table-bootstrapping flags (double-check locking pattern)
# ---------------------------------------------------------------------------

_init_lock = asyncio.Lock()
_self_improvement_init = False
_social_intel_init = False
_code_reasoning_init = False
_task_intel_init = False
_verification_init = False
_process_intel_init = False
_knowledge_init = False
_compliance_init = False


async def _ensure_self_improvement():
    global _self_improvement_init
    orch = get_orch()
    if not orch.self_improvement_manager:
        raise HTTPException(503, "Self-improvement manager not initialized")
    if not _self_improvement_init:
        async with _init_lock:
            if not _self_improvement_init:
                await orch.self_improvement_manager.ensure_tables()
                _self_improvement_init = True
    return orch.self_improvement_manager


async def _ensure_social_intel():
    global _social_intel_init
    orch = get_orch()
    if not orch.social_intelligence_manager:
        raise HTTPException(503, "Social intelligence manager not initialized")
    if not _social_intel_init:
        async with _init_lock:
            if not _social_intel_init:
                await orch.social_intelligence_manager.ensure_tables()
                _social_intel_init = True
    return orch.social_intelligence_manager


async def _ensure_code_reasoning():
    global _code_reasoning_init
    orch = get_orch()
    if not orch.code_reasoning_manager:
        raise HTTPException(503, "Code reasoning manager not initialized")
    if not _code_reasoning_init:
        async with _init_lock:
            if not _code_reasoning_init:
                await orch.code_reasoning_manager.ensure_tables()
                _code_reasoning_init = True
    return orch.code_reasoning_manager


async def _ensure_task_intel():
    global _task_intel_init
    orch = get_orch()
    if not orch.task_intelligence_manager:
        raise HTTPException(503, "Task intelligence manager not initialized")
    if not _task_intel_init:
        async with _init_lock:
            if not _task_intel_init:
                await orch.task_intelligence_manager.ensure_tables()
                _task_intel_init = True
    return orch.task_intelligence_manager


async def _ensure_verification():
    global _verification_init
    orch = get_orch()
    if not orch.verification_manager:
        raise HTTPException(503, "Verification manager not initialized")
    if not _verification_init:
        async with _init_lock:
            if not _verification_init:
                await orch.verification_manager.ensure_tables()
                _verification_init = True
    return orch.verification_manager


async def _ensure_process_intel():
    global _process_intel_init
    orch = get_orch()
    if not orch.process_intelligence_manager:
        raise HTTPException(503, "Process intelligence manager not initialized")
    if not _process_intel_init:
        async with _init_lock:
            if not _process_intel_init:
                await orch.process_intelligence_manager.ensure_tables()
                _process_intel_init = True
    return orch.process_intelligence_manager


async def _ensure_knowledge():
    global _knowledge_init
    orch = get_orch()
    if not orch.knowledge_manager:
        raise HTTPException(503, "Knowledge management manager not initialized")
    if not _knowledge_init:
        async with _init_lock:
            if not _knowledge_init:
                await orch.knowledge_manager.ensure_tables()
                _knowledge_init = True
    return orch.knowledge_manager


async def _ensure_compliance():
    global _compliance_init
    orch = get_orch()
    if not orch.compliance_manager:
        raise HTTPException(503, "Compliance manager not initialized")
    if not _compliance_init:
        async with _init_lock:
            if not _compliance_init:
                await orch.compliance_manager.ensure_tables()
                _compliance_init = True
    return orch.compliance_manager


# ===================================================================
# Self-Improvement (Features 1-8)
# ===================================================================


@router.post("/self-improvement/prompt-versions")
async def store_prompt_version(body: PromptVersionBody):
    mgr = await _ensure_self_improvement()
    return await mgr.store_prompt_version(
        agent_role=body.agent_role,
        prompt_text=body.prompt_text,
        version_tag=body.version_tag,
    )


@router.post("/self-improvement/prompt-outcomes")
async def record_prompt_outcome(body: PromptOutcomeBody):
    mgr = await _ensure_self_improvement()
    return await mgr.record_prompt_outcome(
        version_id=body.version_id,
        task_id=body.task_id,
        success=body.success,
        quality_score=body.quality_score,
    )


@router.get("/self-improvement/best-prompt")
async def get_best_prompt(agent_role: str):
    mgr = await _ensure_self_improvement()
    return await mgr.get_best_prompt(agent_role=agent_role)


@router.get("/self-improvement/prompt-history")
async def get_prompt_history(agent_role: str, limit: int = 20):
    mgr = await _ensure_self_improvement()
    return await mgr.get_prompt_history(agent_role=agent_role, limit=limit)


@router.post("/self-improvement/strategies")
async def register_strategy(body: RegisterStrategyBody):
    mgr = await _ensure_self_improvement()
    return await mgr.register_strategy(
        agent_role=body.agent_role,
        strategy_name=body.strategy_name,
        strategy_type=body.strategy_type,
        description=body.description,
    )


@router.post("/self-improvement/strategy-uses")
async def record_strategy_use(body: StrategyUseBody):
    mgr = await _ensure_self_improvement()
    return await mgr.record_strategy_use(
        strategy_id=body.strategy_id,
        task_id=body.task_id,
        success=body.success,
        duration_ms=body.duration_ms,
    )


@router.get("/self-improvement/best-strategy")
async def select_strategy(agent_role: str, task_type: str):
    mgr = await _ensure_self_improvement()
    return await mgr.select_strategy(
        agent_role=agent_role, task_type=task_type,
    )


@router.get("/self-improvement/portfolio")
async def get_portfolio(agent_role: str):
    mgr = await _ensure_self_improvement()
    return await mgr.get_portfolio(agent_role=agent_role)


@router.post("/self-improvement/transfers")
async def create_transfer(body: CreateTransferBody):
    mgr = await _ensure_self_improvement()
    return await mgr.create_transfer(
        source_role=body.source_role,
        target_role=body.target_role,
        skill_area=body.skill_area,
        knowledge_content=body.knowledge_content,
    )


@router.get("/self-improvement/transfers/pending")
async def get_pending_transfers(target_role: str):
    mgr = await _ensure_self_improvement()
    return await mgr.get_pending_transfers(target_role=target_role)


@router.post("/self-improvement/transfers/{transfer_id}/acknowledge")
async def acknowledge_transfer(transfer_id: str):
    mgr = await _ensure_self_improvement()
    return await mgr.acknowledge_transfer(transfer_id=transfer_id)


@router.post("/self-improvement/cognitive-load")
async def record_load(body: CognitiveLoadBody):
    mgr = await _ensure_self_improvement()
    return await mgr.record_load(
        agent_id=body.agent_id,
        context_tokens=body.context_tokens,
        max_tokens=body.max_tokens,
        active_files=body.active_files,
        task_id=body.task_id,
    )


@router.get("/self-improvement/cognitive-load")
async def get_load_history(agent_id: str, limit: int = 20):
    mgr = await _ensure_self_improvement()
    return await mgr.get_load_history(agent_id=agent_id, limit=limit)


@router.get("/self-improvement/cognitive-load/eviction")
async def recommend_eviction(agent_id: str):
    mgr = await _ensure_self_improvement()
    return await mgr.recommend_eviction(agent_id=agent_id)


@router.post("/self-improvement/reflections")
async def create_reflection(body: CreateReflectionBody):
    mgr = await _ensure_self_improvement()
    return await mgr.create_reflection(
        task_id=body.task_id,
        agent_id=body.agent_id,
        what_worked=body.what_worked,
        what_failed=body.what_failed,
        lessons=body.lessons,
        approach_rating=body.approach_rating,
    )


@router.get("/self-improvement/reflections")
async def get_reflections(agent_id: str, limit: int = 20):
    mgr = await _ensure_self_improvement()
    return await mgr.get_reflections(agent_id=agent_id, limit=limit)


@router.post("/self-improvement/reflections/search")
async def find_relevant_reflections(body: SearchReflectionsBody):
    mgr = await _ensure_self_improvement()
    return await mgr.find_relevant_reflections(
        task_description=body.task_description, limit=body.limit,
    )


@router.post("/self-improvement/failure-modes")
async def classify_failure(body: ClassifyFailureBody):
    mgr = await _ensure_self_improvement()
    return await mgr.classify_failure(
        task_id=body.task_id,
        category=body.category,
        subcategory=body.subcategory,
        description=body.description,
        severity=body.severity,
    )


@router.get("/self-improvement/failure-modes")
async def get_taxonomy(category: Optional[str] = None):
    mgr = await _ensure_self_improvement()
    return await mgr.get_taxonomy(category=category)


@router.get("/self-improvement/failure-modes/playbook")
async def get_recovery_playbook(category: str, subcategory: Optional[str] = None):
    mgr = await _ensure_self_improvement()
    return await mgr.get_recovery_playbook(
        category=category, subcategory=subcategory,
    )


@router.post("/self-improvement/profiles")
async def update_profile(body: UpdateProfileBody):
    mgr = await _ensure_self_improvement()
    return await mgr.update_profile(
        agent_role=body.agent_role,
        trait=body.trait,
        value=body.value,
        evidence_task_id=body.evidence_task_id,
    )


@router.get("/self-improvement/profiles")
async def get_profile(agent_role: str):
    mgr = await _ensure_self_improvement()
    return await mgr.get_profile(agent_role=agent_role)


@router.post("/self-improvement/profiles/match")
async def match_task_to_agent(body: MatchTaskBody):
    mgr = await _ensure_self_improvement()
    return await mgr.match_task_to_agent(
        task_type=body.task_type, required_traits=body.required_traits,
    )


@router.post("/self-improvement/confidence")
async def record_confidence(body: RecordConfidenceBody):
    mgr = await _ensure_self_improvement()
    return await mgr.record_confidence(
        agent_id=body.agent_id,
        task_id=body.task_id,
        predicted_confidence=body.predicted_confidence,
        actual_success=body.actual_success,
    )


@router.get("/self-improvement/confidence/calibration")
async def get_calibration_score(agent_id: str):
    mgr = await _ensure_self_improvement()
    return await mgr.get_calibration_score(agent_id=agent_id)


@router.get("/self-improvement/confidence/history")
async def get_calibration_history(agent_id: str, limit: int = 50):
    mgr = await _ensure_self_improvement()
    return await mgr.get_calibration_history(agent_id=agent_id, limit=limit)


# ===================================================================
# Social Intelligence (Features 9-16)
# ===================================================================


@router.post("/social/arguments")
async def open_argument(body: OpenArgumentBody):
    mgr = await _ensure_social_intel()
    return await mgr.open_argument(
        topic=body.topic,
        participants=body.participants,
        context=body.context,
    )


@router.post("/social/arguments/{session_id}/evidence")
async def submit_evidence(session_id: str, body: SubmitEvidenceBody):
    mgr = await _ensure_social_intel()
    return await mgr.submit_evidence(
        session_id=session_id,
        agent_id=body.agent_id,
        position=body.position,
        evidence=body.evidence,
        confidence=body.confidence,
    )


@router.post("/social/arguments/{session_id}/resolve")
async def resolve_argument(session_id: str, body: ResolveArgumentBody):
    mgr = await _ensure_social_intel()
    return await mgr.resolve_argument(
        session_id=session_id,
    )


@router.get("/social/arguments")
async def get_argument_history(limit: int = 20):
    mgr = await _ensure_social_intel()
    return await mgr.get_argument_history(limit=limit)


@router.post("/social/trust")
async def update_trust(body: UpdateTrustBody):
    mgr = await _ensure_social_intel()
    return await mgr.update_trust(
        from_agent=body.from_agent,
        to_agent=body.to_agent,
        interaction_type=body.interaction_type,
        outcome_quality=body.outcome_quality,
    )


@router.get("/social/trust")
async def get_trust(from_agent: str, to_agent: str):
    mgr = await _ensure_social_intel()
    return await mgr.get_trust(from_agent=from_agent, to_agent=to_agent)


@router.get("/social/trust/network")
async def get_trust_network():
    mgr = await _ensure_social_intel()
    return await mgr.get_trust_network()


@router.get("/social/trust/top")
async def get_most_trusted(for_agent: str, limit: int = 5):
    mgr = await _ensure_social_intel()
    return await mgr.get_most_trusted(for_agent=for_agent, limit=limit)


@router.post("/social/communication-style")
async def record_preference(body: RecordPreferenceBody):
    mgr = await _ensure_social_intel()
    return await mgr.record_preference(
        agent_role=body.agent_role,
        preference_key=body.preference_key,
        preference_value=body.preference_value,
    )


@router.get("/social/communication-style")
async def get_style(agent_role: str):
    mgr = await _ensure_social_intel()
    return await mgr.get_style(agent_role=agent_role)


@router.get("/social/communication-style/adapt")
async def adapt_message(target_role: str, message_type: str):
    mgr = await _ensure_social_intel()
    return await mgr.adapt_message(
        target_role=target_role, message_type=message_type,
    )


@router.post("/social/mental-model")
async def assert_fact(body: AssertFactBody):
    mgr = await _ensure_social_intel()
    return await mgr.assert_fact(
        key=body.key,
        value=body.value,
        source_agent=body.source_agent,
        confidence=body.confidence,
    )


@router.get("/social/mental-model")
async def get_model(prefix: Optional[str] = None):
    mgr = await _ensure_social_intel()
    return await mgr.get_model(prefix=prefix)


@router.delete("/social/mental-model")
async def retract_fact(body: RetractFactBody):
    mgr = await _ensure_social_intel()
    return await mgr.retract_fact(key=body.key, agent_id=body.agent_id)


@router.get("/social/mental-model/conflicts")
async def get_conflicts():
    mgr = await _ensure_social_intel()
    return await mgr.get_conflicts()


@router.post("/social/work-areas")
async def report_work_area(body: ReportWorkAreaBody):
    mgr = await _ensure_social_intel()
    return await mgr.report_work_area(
        agent_id=body.agent_id,
        file_paths=body.file_paths,
        task_id=body.task_id,
    )


@router.get("/social/overlaps")
async def detect_overlaps():
    mgr = await _ensure_social_intel()
    return await mgr.detect_overlaps()


@router.get("/social/alerts")
async def get_alerts(resolved: bool = False, limit: int = 20):
    mgr = await _ensure_social_intel()
    return await mgr.get_alerts(resolved=resolved, limit=limit)


@router.post("/social/alerts/{alert_id}/resolve")
async def resolve_alert(alert_id: str):
    mgr = await _ensure_social_intel()
    return await mgr.resolve_alert(alert_id=alert_id)


@router.post("/social/context-shares")
async def share_context(body: ShareContextBody):
    mgr = await _ensure_social_intel()
    return await mgr.share_context(
        from_agent=body.from_agent,
        to_agent=body.to_agent,
        context_key=body.context_key,
        context_value=body.context_value,
        relevance_score=body.relevance_score,
    )


@router.get("/social/context-shares")
async def get_shared_context(agent_id: str, limit: int = 20):
    mgr = await _ensure_social_intel()
    return await mgr.get_shared_context(agent_id=agent_id, limit=limit)


@router.post("/social/context-shares/{share_id}/acknowledge")
async def acknowledge_context(share_id: str):
    mgr = await _ensure_social_intel()
    return await mgr.acknowledge_context(share_id=share_id)


@router.post("/social/collaborations")
async def record_collaboration(body: RecordCollaborationBody):
    mgr = await _ensure_social_intel()
    return await mgr.record_collaboration(
        agent_a=body.agent_a,
        agent_b=body.agent_b,
        task_id=body.task_id,
        effectiveness=body.effectiveness,
        notes=body.notes,
    )


@router.get("/social/collaborations/pair")
async def get_pair_score(agent_a: str, agent_b: str):
    mgr = await _ensure_social_intel()
    return await mgr.get_pair_score(agent_a=agent_a, agent_b=agent_b)


@router.get("/social/collaborations/best")
async def get_best_pairs(limit: int = 10):
    mgr = await _ensure_social_intel()
    return await mgr.get_best_pairs(limit=limit)


@router.get("/social/collaborations/worst")
async def get_worst_pairs(limit: int = 10):
    mgr = await _ensure_social_intel()
    return await mgr.get_worst_pairs(limit=limit)


@router.post("/social/consensus-predictions")
async def predict_consensus(body: PredictConsensusBody):
    mgr = await _ensure_social_intel()
    return await mgr.predict_consensus(
        proposal_description=body.proposal_description,
        participants=body.participants,
    )


@router.post("/social/consensus-predictions/{prediction_id}/outcome")
async def record_prediction_outcome(
    prediction_id: str, body: RecordPredictionOutcomeBody,
):
    mgr = await _ensure_social_intel()
    return await mgr.record_prediction_outcome(
        prediction_id=prediction_id, actual_outcome=body.actual_outcome,
    )


@router.get("/social/consensus-predictions/accuracy")
async def get_prediction_accuracy():
    mgr = await _ensure_social_intel()
    return await mgr.get_prediction_accuracy()


# ===================================================================
# Code Reasoning (Features 17-24)
# ===================================================================


@router.post("/code-reasoning/semantic-index")
async def index_intent(body: IndexIntentBody):
    body.file_path = _validate_path(body.file_path)
    mgr = await _ensure_code_reasoning()
    return await mgr.index_intent(
        file_path=body.file_path,
        function_name=body.function_name,
        intent_description=body.intent_description,
        keywords=body.keywords,
    )


@router.get("/code-reasoning/semantic-search")
async def search_by_intent(query: str, limit: int = 10):
    mgr = await _ensure_code_reasoning()
    return await mgr.search_by_intent(query=query, limit=limit)


@router.get("/code-reasoning/semantic-index/stats")
async def get_index_stats():
    mgr = await _ensure_code_reasoning()
    return await mgr.get_index_stats()


@router.post("/code-reasoning/dependencies")
async def record_dependency(body: RecordDependencyBody):
    body.source_file = _validate_path(body.source_file)
    body.target_file = _validate_path(body.target_file)
    mgr = await _ensure_code_reasoning()
    return await mgr.record_dependency(
        source_file=body.source_file,
        target_file=body.target_file,
        dep_type=body.dep_type,
    )


@router.get("/code-reasoning/impact")
async def predict_impact(changed_file: str):
    changed_file = _validate_path(changed_file)
    mgr = await _ensure_code_reasoning()
    return await mgr.predict_impact(changed_file=changed_file)


@router.get("/code-reasoning/impact/history")
async def get_impact_history(limit: int = 20):
    mgr = await _ensure_code_reasoning()
    return await mgr.get_impact_history(limit=limit)


@router.post("/code-reasoning/style-patterns")
async def record_pattern(body: RecordPatternBody):
    mgr = await _ensure_code_reasoning()
    return await mgr.record_pattern(
        pattern_name=body.pattern_name,
        category=body.category,
        example=body.example,
        file_path=body.file_path,
    )


@router.get("/code-reasoning/style-patterns")
async def get_patterns(category: Optional[str] = None):
    mgr = await _ensure_code_reasoning()
    return await mgr.get_patterns(category=category)


@router.post("/code-reasoning/style-check")
async def check_conformance(body: CheckConformanceBody):
    body.file_path = _validate_path(body.file_path)
    mgr = await _ensure_code_reasoning()
    return await mgr.check_conformance(
        file_path=body.file_path, content=body.content,
    )


@router.post("/code-reasoning/refactoring/detect")
async def detect_opportunities(body: DetectOpportunitiesBody):
    body.file_path = _validate_path(body.file_path)
    mgr = await _ensure_code_reasoning()
    return await mgr.detect_opportunities(
        file_path=body.file_path, content=body.content,
    )


@router.get("/code-reasoning/refactoring")
async def get_opportunities(
    file_path: Optional[str] = None,
    priority: Optional[str] = None,
    limit: int = 20,
):
    if file_path is not None:
        file_path = _validate_path(file_path)
    mgr = await _ensure_code_reasoning()
    return await mgr.get_opportunities(
        file_path=file_path, priority=priority, limit=limit,
    )


@router.post("/code-reasoning/refactoring/{opportunity_id}/dismiss")
async def dismiss_opportunity(opportunity_id: str):
    mgr = await _ensure_code_reasoning()
    return await mgr.dismiss_opportunity(opportunity_id=opportunity_id)


@router.post("/code-reasoning/debt")
async def add_debt(body: AddDebtBody):
    body.file_path = _validate_path(body.file_path)
    mgr = await _ensure_code_reasoning()
    return await mgr.add_debt(
        file_path=body.file_path,
        category=body.category,
        description=body.description,
        effort_estimate=body.effort_estimate,
        business_impact=body.business_impact,
    )


@router.get("/code-reasoning/debt/prioritized")
async def get_prioritized_debt(limit: int = 20):
    mgr = await _ensure_code_reasoning()
    return await mgr.get_prioritized_debt(limit=limit)


@router.post("/code-reasoning/debt/{debt_id}/resolve")
async def resolve_debt(debt_id: str):
    mgr = await _ensure_code_reasoning()
    return await mgr.resolve_debt(debt_id=debt_id)


@router.post("/code-reasoning/api-versions")
async def record_api_version(body: RecordApiVersionBody):
    mgr = await _ensure_code_reasoning()
    return await mgr.record_api_version(
        endpoint=body.endpoint,
        method=body.method,
        version=body.version,
        schema_hash=body.schema_hash,
        breaking_change=body.breaking_change,
    )


@router.get("/code-reasoning/api-versions/breaking")
async def detect_breaking_changes(endpoint: Optional[str] = None):
    mgr = await _ensure_code_reasoning()
    return await mgr.detect_breaking_changes(endpoint=endpoint)


@router.get("/code-reasoning/api-versions/changelog")
async def get_api_changelog(limit: int = 20):
    mgr = await _ensure_code_reasoning()
    return await mgr.get_api_changelog(limit=limit)


@router.post("/code-reasoning/narratives")
async def generate_narrative(body: GenerateNarrativeBody):
    body.file_path = _validate_path(body.file_path)
    mgr = await _ensure_code_reasoning()
    return await mgr.generate_narrative(
        file_path=body.file_path,
        function_name=body.function_name,
        code_snippet=body.code_snippet,
        narrative_text=body.narrative_text,
    )


@router.get("/code-reasoning/narratives")
async def get_narrative(
    file_path: Optional[str] = None,
    function_name: Optional[str] = None,
):
    if file_path is not None:
        file_path = _validate_path(file_path)
    mgr = await _ensure_code_reasoning()
    return await mgr.get_narrative(
        file_path=file_path, function_name=function_name,
    )


@router.get("/code-reasoning/narratives/search")
async def search_narratives(query: str, limit: int = 10):
    mgr = await _ensure_code_reasoning()
    return await mgr.search_narratives(query=query, limit=limit)


@router.post("/code-reasoning/invariants")
async def record_invariant(body: RecordInvariantBody):
    body.file_path = _validate_path(body.file_path)
    mgr = await _ensure_code_reasoning()
    return await mgr.record_invariant(
        file_path=body.file_path,
        function_name=body.function_name,
        invariant_expression=body.invariant_expression,
        invariant_type=body.invariant_type,
    )


@router.get("/code-reasoning/invariants")
async def get_invariants(
    file_path: Optional[str] = None,
    function_name: Optional[str] = None,
):
    if file_path is not None:
        file_path = _validate_path(file_path)
    mgr = await _ensure_code_reasoning()
    return await mgr.get_invariants(
        file_path=file_path, function_name=function_name,
    )


@router.get("/code-reasoning/invariants/violations")
async def check_invariant_violations(file_path: Optional[str] = None):
    if file_path is not None:
        file_path = _validate_path(file_path)
    mgr = await _ensure_code_reasoning()
    return await mgr.check_invariant_violations(file_path=file_path)


# ===================================================================
# Task Intelligence (Features 25-32)
# ===================================================================


@router.post("/task-intel/complexity")
async def estimate_complexity(body: EstimateComplexityBody):
    mgr = await _ensure_task_intel()
    return await mgr.estimate_complexity(
        task_id=body.task_id,
        title=body.title,
        description=body.description,
    )


@router.get("/task-intel/complexity")
async def get_estimate(task_id: str):
    mgr = await _ensure_task_intel()
    return await mgr.get_estimate(task_id=task_id)


@router.post("/task-intel/complexity/{task_id}/calibrate")
async def calibrate(task_id: str, body: CalibrateBody):
    mgr = await _ensure_task_intel()
    return await mgr.calibrate(
        task_id=task_id,
        actual_complexity=body.actual_complexity,
    )


@router.post("/task-intel/prerequisites/detect")
async def detect_prerequisites(body: DetectPrerequisitesBody):
    mgr = await _ensure_task_intel()
    return await mgr.detect_prerequisites(
        task_id=body.task_id,
        description=body.description,
        files_involved=body.files_involved,
    )


@router.get("/task-intel/prerequisites")
async def get_prerequisites(task_id: str):
    mgr = await _ensure_task_intel()
    return await mgr.get_prerequisites(task_id=task_id)


@router.post("/task-intel/prerequisites/{prereq_id}/confirm")
async def confirm_prerequisite(prereq_id: str, body: ConfirmPrerequisiteBody):
    mgr = await _ensure_task_intel()
    return await mgr.confirm_prerequisite(
        prereq_id=prereq_id, confirmed=body.confirmed,
    )


@router.post("/task-intel/decomposition-metrics")
async def record_decomposition(body: RecordDecompositionBody):
    mgr = await _ensure_task_intel()
    return await mgr.record_decomposition(
        parent_task_id=body.parent_task_id,
        subtask_count=body.subtask_count,
        avg_subtask_duration_ms=body.avg_subtask_duration_ms,
        success_rate=body.success_rate,
        task_type=body.task_type,
    )


@router.get("/task-intel/decomposition/optimal")
async def get_optimal_granularity(task_type: str):
    mgr = await _ensure_task_intel()
    return await mgr.get_optimal_granularity(task_type=task_type)


@router.get("/task-intel/decomposition/metrics")
async def get_metrics(limit: int = 20):
    mgr = await _ensure_task_intel()
    return await mgr.get_metrics(limit=limit)


@router.post("/task-intel/parallel/find")
async def find_parallel_tasks(body: FindParallelTasksBody):
    mgr = await _ensure_task_intel()
    return await mgr.find_parallel_tasks(group_id=body.group_id)


@router.get("/task-intel/parallel")
async def get_parallel_opportunities(group_id: Optional[str] = None, limit: int = 20):
    mgr = await _ensure_task_intel()
    return await mgr.get_opportunities(group_id=group_id, limit=limit)


@router.post("/task-intel/parallel/{opportunity_id}/exploit")
async def mark_exploited(opportunity_id: str):
    mgr = await _ensure_task_intel()
    return await mgr.mark_exploited(opportunity_id=opportunity_id)


@router.post("/task-intel/context-budget")
async def plan_budget(body: PlanBudgetBody):
    mgr = await _ensure_task_intel()
    return await mgr.plan_budget(
        task_id=body.task_id,
        estimated_files=body.estimated_files,
        estimated_tokens_per_file=body.estimated_tokens_per_file,
    )


@router.get("/task-intel/context-budget")
async def get_budget(task_id: str):
    mgr = await _ensure_task_intel()
    return await mgr.get_budget(task_id=task_id)


@router.post("/task-intel/context-budget/{task_id}/actual")
async def record_actual(task_id: str, body: RecordActualBudgetBody):
    mgr = await _ensure_task_intel()
    return await mgr.record_actual(
        task_id=task_id, actual_tokens_used=body.actual_tokens_used,
    )


@router.post("/task-intel/predictions")
async def predict_outcome(body: PredictOutcomeBody):
    mgr = await _ensure_task_intel()
    return await mgr.predict_outcome(
        task_id=body.task_id,
        complexity_score=body.complexity_score,
        agent_role=body.agent_role,
        historical_success_rate=body.historical_success_rate,
    )


@router.post("/task-intel/predictions/{prediction_id}/outcome")
async def record_actual_outcome(
    prediction_id: str, body: RecordActualOutcomeBody,
):
    mgr = await _ensure_task_intel()
    return await mgr.record_actual_outcome(
        prediction_id=prediction_id,
        success=body.success,
    )


@router.get("/task-intel/predictions/accuracy")
async def get_task_prediction_accuracy(agent_role: Optional[str] = None):
    mgr = await _ensure_task_intel()
    return await mgr.get_prediction_accuracy(agent_role=agent_role)


@router.post("/task-intel/fingerprints")
async def fingerprint_task(body: FingerprintTaskBody):
    mgr = await _ensure_task_intel()
    return await mgr.fingerprint_task(
        task_id=body.task_id,
        title=body.title,
        description=body.description,
        task_type=body.task_type,
    )


@router.post("/task-intel/fingerprints/similar")
async def find_similar(body: FindSimilarTasksBody):
    mgr = await _ensure_task_intel()
    return await mgr.find_similar(
        title=body.title,
        description=body.description,
        limit=body.limit,
    )


@router.get("/task-intel/fingerprints")
async def get_fingerprint(task_id: str):
    mgr = await _ensure_task_intel()
    return await mgr.get_fingerprint(task_id=task_id)


@router.post("/task-intel/effort-tracking/start")
async def start_tracking(body: StartTrackingBody):
    mgr = await _ensure_task_intel()
    return await mgr.start_tracking(
        task_id=body.task_id,
        estimated_duration_ms=body.estimated_duration_ms,
    )


@router.get("/task-intel/effort-tracking/drift")
async def check_drift(task_id: str):
    mgr = await _ensure_task_intel()
    return await mgr.check_drift(task_id=task_id)


@router.post("/task-intel/effort-tracking/{task_id}/complete")
async def complete_tracking(task_id: str, body: CompleteTrackingBody):
    mgr = await _ensure_task_intel()
    return await mgr.complete_tracking(
        task_id=task_id,
    )


@router.get("/task-intel/effort-tracking/history")
async def get_drift_history(limit: int = 20):
    mgr = await _ensure_task_intel()
    return await mgr.get_drift_history(limit=limit)


# ===================================================================
# Verification (Features 33-38)
# ===================================================================


@router.post("/verification/regressions")
async def fingerprint_regression(body: FingerprintRegressionBody):
    mgr = await _ensure_verification()
    return await mgr.fingerprint_regression(
        test_name=body.test_name,
        error_message=body.error_message,
        failing_commit=body.failing_commit,
        last_passing_commit=body.last_passing_commit,
    )


@router.get("/verification/regressions/similar")
async def find_similar_regressions(error_message: str, limit: int = 5):
    mgr = await _ensure_verification()
    return await mgr.find_similar_regressions(
        error_message=error_message, limit=limit,
    )


@router.get("/verification/regressions")
async def get_fingerprints(
    test_name: Optional[str] = None, limit: int = 20,
):
    mgr = await _ensure_verification()
    return await mgr.get_fingerprints(test_name=test_name, limit=limit)


@router.post("/verification/test-mappings")
async def record_mapping(body: RecordMappingBody):
    body.source_file = _validate_path(body.source_file)
    body.test_file = _validate_path(body.test_file)
    mgr = await _ensure_verification()
    return await mgr.record_mapping(
        source_file=body.source_file,
        test_file=body.test_file,
        confidence=body.confidence,
    )


@router.get("/verification/test-mappings/affected")
async def get_affected_tests(changed_files: str):
    file_list = [_validate_path(f.strip()) for f in changed_files.split(",")]
    mgr = await _ensure_verification()
    return await mgr.get_affected_tests(changed_files=file_list)


@router.get("/verification/test-mappings")
async def get_mappings(
    source_file: Optional[str] = None,
    test_file: Optional[str] = None,
):
    if source_file is not None:
        source_file = _validate_path(source_file)
    if test_file is not None:
        test_file = _validate_path(test_file)
    mgr = await _ensure_verification()
    return await mgr.get_mappings(
        source_file=source_file, test_file=test_file,
    )


@router.post("/verification/test-mappings/auto-map")
async def auto_map(body: AutoMapBody):
    body.test_dir = _validate_path(body.test_dir)
    body.source_dir = _validate_path(body.source_dir)
    mgr = await _ensure_verification()
    return await mgr.auto_map(
        test_dir=body.test_dir, source_dir=body.source_dir,
    )


@router.post("/verification/test-runs")
async def record_run(body: RecordRunBody):
    mgr = await _ensure_verification()
    return await mgr.record_run(
        test_name=body.test_name,
        passed=body.passed,
        duration_ms=body.duration_ms,
        run_id=body.run_id,
    )


@router.get("/verification/flaky-tests")
async def detect_flaky(
    min_runs: int = 5, threshold: float = 0.1, limit: int = 20,
):
    mgr = await _ensure_verification()
    return await mgr.detect_flaky(
        min_runs=min_runs, flaky_threshold=threshold,
    )


@router.get("/verification/flaky-tests/list")
async def get_flaky_tests(limit: int = 20):
    mgr = await _ensure_verification()
    return await mgr.get_flaky_tests(limit=limit)


@router.post("/verification/flaky-tests/{test_name:path}/quarantine")
async def quarantine_test(test_name: str):
    mgr = await _ensure_verification()
    return await mgr.quarantine_test(test_name=test_name)


@router.post("/verification/behavioral-specs")
async def mine_spec(body: MineSpecBody):
    body.test_file = _validate_path(body.test_file)
    mgr = await _ensure_verification()
    return await mgr.mine_spec(
        test_file=body.test_file,
        test_name=body.test_name,
        asserted_behavior=body.asserted_behavior,
    )


@router.get("/verification/behavioral-specs")
async def get_specs(source_file: Optional[str] = None, limit: int = 20):
    if source_file is not None:
        source_file = _validate_path(source_file)
    mgr = await _ensure_verification()
    return await mgr.get_specs(source_file=source_file, limit=limit)


@router.get("/verification/behavioral-specs/undocumented")
async def detect_undocumented():
    mgr = await _ensure_verification()
    return await mgr.detect_undocumented()


@router.post("/verification/annotations")
async def annotate(body: AnnotateBody):
    body.file_path = _validate_path(body.file_path)
    mgr = await _ensure_verification()
    return await mgr.annotate(
        file_path=body.file_path,
        line_number=body.line_number,
        annotation_type=body.annotation_type,
        message=body.message,
        severity=body.severity,
    )


@router.get("/verification/annotations")
async def get_annotations(
    file_path: Optional[str] = None,
    annotation_type: Optional[str] = None,
    limit: int = 50,
):
    if file_path is not None:
        file_path = _validate_path(file_path)
    mgr = await _ensure_verification()
    return await mgr.get_annotations(
        file_path=file_path, annotation_type=annotation_type, limit=limit,
    )


@router.post("/verification/annotations/auto")
async def auto_annotate(body: AutoAnnotateBody):
    body.file_path = _validate_path(body.file_path)
    mgr = await _ensure_verification()
    return await mgr.auto_annotate(
        file_path=body.file_path, content=body.content,
    )


@router.delete("/verification/annotations")
async def clear_annotations(file_path: str):
    file_path = _validate_path(file_path)
    mgr = await _ensure_verification()
    return await mgr.clear_annotations(file_path=file_path)


@router.post("/verification/quality-gates")
async def define_gate(body: DefineGateBody):
    mgr = await _ensure_verification()
    return await mgr.define_gate(
        gate_name=body.gate_name,
        conditions=body.conditions,
        risk_level=body.risk_level,
    )


@router.post("/verification/quality-gates/{gate_name}/evaluate")
async def evaluate_gate(gate_name: str, body: EvaluateGateBody):
    mgr = await _ensure_verification()
    return await mgr.evaluate_gate(
        gate_name=gate_name, metrics=body.metrics,
    )


@router.get("/verification/quality-gates")
async def get_gates():
    mgr = await _ensure_verification()
    return await mgr.get_gates()


@router.get("/verification/quality-gates/history")
async def get_gate_history(gate_name: Optional[str] = None, limit: int = 20):
    mgr = await _ensure_verification()
    return await mgr.get_gate_history(gate_name=gate_name, limit=limit)


# ===================================================================
# Process Intelligence (Features 39-44)
# ===================================================================


@router.post("/process/velocity")
async def record_velocity(body: RecordVelocityBody):
    mgr = await _ensure_process_intel()
    return await mgr.record_velocity(
        sprint_id=body.sprint_id,
        tasks_completed=body.tasks_completed,
        story_points=body.story_points,
        duration_days=body.duration_days,
    )


@router.post("/process/velocity/forecast")
async def forecast(body: ForecastBody):
    mgr = await _ensure_process_intel()
    return await mgr.forecast(
        remaining_points=body.remaining_points,
        num_simulations=body.num_simulations,
    )


@router.get("/process/velocity/history")
async def get_velocity_history(limit: int = 20):
    mgr = await _ensure_process_intel()
    return await mgr.get_velocity_history(limit=limit)


@router.post("/process/risk-scores")
async def score_file(body: ScoreFileBody):
    body.file_path = _validate_path(body.file_path)
    mgr = await _ensure_process_intel()
    return await mgr.score_file(
        file_path=body.file_path,
        change_frequency=body.change_frequency,
        complexity_score=body.complexity_score,
        test_coverage_pct=body.test_coverage_pct,
    )


@router.get("/process/risk-scores/heat-map")
async def get_heat_map(min_risk: float = 0.0, limit: int = 50):
    mgr = await _ensure_process_intel()
    return await mgr.get_heat_map(min_risk=min_risk, limit=limit)


@router.post("/process/risk-scores/refresh")
async def refresh_scores():
    mgr = await _ensure_process_intel()
    return await mgr.refresh_scores()


@router.post("/process/phase-durations")
async def record_phase_duration(body: RecordPhaseDurationBody):
    mgr = await _ensure_process_intel()
    return await mgr.record_phase_duration(
        task_id=body.task_id,
        phase=body.phase,
        duration_ms=body.duration_ms,
    )


@router.get("/process/bottlenecks")
async def find_bottlenecks():
    mgr = await _ensure_process_intel()
    return await mgr.find_bottlenecks()


@router.get("/process/phase-stats")
async def get_phase_stats(phase: Optional[str] = None):
    mgr = await _ensure_process_intel()
    return await mgr.get_phase_stats(phase=phase)


@router.post("/process/readiness")
async def assess_readiness(body: AssessReadinessBody):
    mgr = await _ensure_process_intel()
    return await mgr.assess_readiness(
        release_id=body.release_id, metrics=body.metrics,
    )


@router.get("/process/readiness")
async def get_assessment(release_id: str):
    mgr = await _ensure_process_intel()
    return await mgr.get_assessment(release_id=release_id)


@router.get("/process/readiness/history")
async def get_history(limit: int = 10):
    mgr = await _ensure_process_intel()
    return await mgr.get_history(limit=limit)


@router.post("/process/stakeholder-impacts")
async def record_impact(body: RecordImpactBody):
    mgr = await _ensure_process_intel()
    return await mgr.record_impact(
        change_id=body.change_id,
        stakeholder_group=body.stakeholder_group,
        impact_level=body.impact_level,
        description=body.description,
    )


@router.get("/process/stakeholder-impacts")
async def get_impacts(
    change_id: Optional[str] = None,
    stakeholder_group: Optional[str] = None,
    limit: int = 20,
):
    mgr = await _ensure_process_intel()
    return await mgr.get_impacts(
        change_id=change_id,
        stakeholder_group=stakeholder_group,
        limit=limit,
    )


@router.get("/process/stakeholder-impacts/most-impacted")
async def get_most_impacted(limit: int = 10):
    mgr = await _ensure_process_intel()
    return await mgr.get_most_impacted(limit=limit)


@router.post("/process/retrospectives")
async def generate_retro(body: GenerateRetroBody):
    mgr = await _ensure_process_intel()
    return await mgr.generate_retro(
        sprint_id=body.sprint_id, tasks_data=body.tasks_data,
    )


@router.get("/process/retrospectives")
async def get_retro(sprint_id: str):
    mgr = await _ensure_process_intel()
    return await mgr.get_retro(sprint_id=sprint_id)


@router.get("/process/retrospectives/list")
async def get_retros(limit: int = 10):
    mgr = await _ensure_process_intel()
    return await mgr.get_retros(limit=limit)


# ===================================================================
# Knowledge Management (Features 45-48)
# ===================================================================


@router.post("/knowledge/entries")
async def track_knowledge(body: TrackKnowledgeBody):
    mgr = await _ensure_knowledge()
    return await mgr.track_knowledge(
        key=body.key,
        content=body.content,
        source_file=body.source_file,
        source_agent=body.source_agent,
    )


@router.get("/knowledge/staleness")
async def check_staleness(max_age_days: int = 30):
    mgr = await _ensure_knowledge()
    return await mgr.check_staleness(max_age_days=max_age_days)


@router.post("/knowledge/entries/{entry_id}/refresh")
async def refresh_knowledge(entry_id: str):
    mgr = await _ensure_knowledge()
    return await mgr.refresh_knowledge(entry_id=entry_id)


@router.get("/knowledge/stale")
async def get_stale_entries(limit: int = 20):
    mgr = await _ensure_knowledge()
    return await mgr.get_stale_entries(limit=limit)


@router.post("/knowledge/doc-gaps/scan")
async def scan_for_gaps(body: ScanForGapsBody):
    mgr = await _ensure_knowledge()
    return await mgr.scan_for_gaps(
        code_files=body.code_files, doc_files=body.doc_files,
    )


@router.get("/knowledge/doc-gaps")
async def get_gaps(
    file_path: Optional[str] = None,
    severity: Optional[str] = None,
    limit: int = 20,
):
    if file_path is not None:
        file_path = _validate_path(file_path)
    mgr = await _ensure_knowledge()
    return await mgr.get_gaps(
        file_path=file_path, severity=severity, limit=limit,
    )


@router.post("/knowledge/doc-gaps/{gap_id}/resolve")
async def resolve_gap(gap_id: str):
    mgr = await _ensure_knowledge()
    return await mgr.resolve_gap(gap_id=gap_id)


@router.get("/knowledge/doc-gaps/coverage")
async def get_coverage_stats():
    mgr = await _ensure_knowledge()
    return await mgr.get_coverage_stats()


@router.post("/knowledge/institutional/commit")
async def extract_from_commit(body: ExtractFromCommitBody):
    mgr = await _ensure_knowledge()
    return await mgr.extract_from_commit(
        commit_hash=body.commit_hash,
        commit_message=body.commit_message,
        author=body.author,
        files_changed=body.files_changed,
    )


@router.post("/knowledge/institutional/comment")
async def extract_from_comment(body: ExtractFromCommentBody):
    body.file_path = _validate_path(body.file_path)
    mgr = await _ensure_knowledge()
    return await mgr.extract_from_comment(
        file_path=body.file_path,
        line_number=body.line_number,
        comment_text=body.comment_text,
    )


@router.get("/knowledge/institutional/search")
async def search_knowledge(query: str, limit: int = 10):
    mgr = await _ensure_knowledge()
    return await mgr.search_knowledge(query=query, limit=limit)


@router.get("/knowledge/institutional")
async def get_knowledge(source_type: Optional[str] = None, limit: int = 20):
    mgr = await _ensure_knowledge()
    return await mgr.get_knowledge(source_type=source_type, limit=limit)


@router.post("/knowledge/compression")
async def compress_context(body: CompressContextBody):
    mgr = await _ensure_knowledge()
    return await mgr.compress_context(
        context_items=body.context_items,
        max_tokens=body.max_tokens,
        strategy=body.strategy,
    )


@router.post("/knowledge/compression/record")
async def record_compression(body: RecordCompressionBody):
    mgr = await _ensure_knowledge()
    return await mgr.record_compression(
        task_id=body.task_id,
        original_tokens=body.original_tokens,
        compressed_tokens=body.compressed_tokens,
        items_kept=body.items_kept,
        items_dropped=body.items_dropped,
    )


@router.get("/knowledge/compression/stats")
async def get_compression_stats():
    mgr = await _ensure_knowledge()
    return await mgr.get_compression_stats()


@router.post("/knowledge/compression/weights")
async def set_salience_weights(body: SetSalienceWeightsBody):
    mgr = await _ensure_knowledge()
    return await mgr.set_salience_weights(
        recency_weight=body.recency_weight,
        relevance_weight=body.relevance_weight,
        frequency_weight=body.frequency_weight,
    )


# ===================================================================
# Compliance (Features 49-50)
# ===================================================================


@router.post("/compliance/threat-models")
async def create_model(body: CreateModelBody):
    mgr = await _ensure_compliance()
    return await mgr.create_model(
        feature_name=body.feature_name,
        description=body.description,
        data_flows=body.data_flows,
    )


@router.post("/compliance/threat-models/{model_id}/threats")
async def add_threat(model_id: str, body: AddThreatBody):
    mgr = await _ensure_compliance()
    return await mgr.add_threat(
        model_id=model_id,
        threat_type=body.threat_type,
        description=body.description,
        risk_level=body.risk_level,
        mitigation=body.mitigation,
    )


@router.get("/compliance/threat-models/{model_id}")
async def get_threat_model(model_id: str):
    mgr = await _ensure_compliance()
    return await mgr.get_model(model_id=model_id)


@router.get("/compliance/threat-models")
async def get_models(limit: int = 20):
    mgr = await _ensure_compliance()
    return await mgr.get_models(limit=limit)


@router.get("/compliance/threat-models/{model_id}/risk")
async def assess_risk(model_id: str):
    mgr = await _ensure_compliance()
    return await mgr.assess_risk(model_id=model_id)


@router.get("/compliance/threat-models/unmitigated")
async def get_unmitigated_threats(model_id: Optional[str] = None):
    mgr = await _ensure_compliance()
    return await mgr.get_unmitigated_threats(model_id=model_id)


@router.post("/compliance/rules")
async def add_rule(body: AddRuleBody):
    mgr = await _ensure_compliance()
    return await mgr.add_rule(
        rule_id=body.rule_id,
        framework=body.framework,
        category=body.category,
        description=body.description,
        check_pattern=body.check_pattern,
        severity=body.severity,
    )


@router.get("/compliance/rules")
async def get_rules(
    framework: Optional[str] = None, category: Optional[str] = None,
):
    mgr = await _ensure_compliance()
    return await mgr.get_rules(framework=framework, category=category)


@router.post("/compliance/rules/check")
async def check_file(body: CheckFileBody):
    body.file_path = _validate_path(body.file_path)
    mgr = await _ensure_compliance()
    return await mgr.check_file(
        file_path=body.file_path, content=body.content,
    )


@router.post("/compliance/checks")
async def record_check(body: RecordCheckBody):
    body.file_path = _validate_path(body.file_path)
    mgr = await _ensure_compliance()
    return await mgr.record_check(
        file_path=body.file_path,
        violations_found=body.violations_found,
        rules_checked=body.rules_checked,
    )


@router.get("/compliance/status")
async def get_compliance_status(framework: Optional[str] = None):
    mgr = await _ensure_compliance()
    return await mgr.get_compliance_status(framework=framework)


@router.post("/compliance/exemptions")
async def add_exemption(body: AddExemptionBody):
    body.file_path = _validate_path(body.file_path)
    mgr = await _ensure_compliance()
    return await mgr.add_exemption(
        rule_id=body.rule_id,
        file_path=body.file_path,
        reason=body.reason,
        approved_by=body.approved_by,
    )
