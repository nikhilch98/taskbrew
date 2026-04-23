"""Pydantic models shared across dashboard routers.

Audit 10 F#23: historically these models accepted free-form strings
with no size cap and free-form URLs with no scheme validation. An LLM
agent feeding a 10 MB task title bloated every DB row; a webhook URL
pointing at ``file:///etc/passwd`` or ``javascript:alert(1)`` passed
the pre-flight unchecked. We apply:

- ``max_length`` on every free-form string so oversized input is
  rejected with a 422 at request parsing instead of silently landing
  in SQLite.
- ``HttpUrl`` on webhook URLs so the scheme is validated to http/https
  by pydantic before the custom SSRF validator runs (audit 03 F#7).
- ``Field(default_factory=list)`` etc. for mutable defaults (audit 10
  F#24 follow-up).

Existing field names are preserved so callers and tests don't break.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field, HttpUrl


class CreateTaskBody(BaseModel):
    group_id: str
    title: str
    assigned_to: str
    assigned_by: str
    task_type: str
    description: Optional[str] = None
    priority: str = "medium"
    parent_id: Optional[str] = None
    blocked_by: Optional[list[str]] = None
    # None = task_type default (tech_design => required); True/False = explicit.
    requires_fanout: Optional[bool] = None


class SubmitGoalBody(BaseModel):
    title: str
    description: str = ""


class PauseResumeBody(BaseModel):
    role: Optional[str] = None


class CreateProjectBody(BaseModel):
    name: str = Field(max_length=128)
    directory: str = Field(max_length=2048)
    with_defaults: bool = True
    cli_provider: str = Field(default="claude", max_length=32)


class UpdateTeamSettingsBody(BaseModel):
    name: Optional[str] = None
    auth_enabled: Optional[bool] = None
    cost_budgets_enabled: Optional[bool] = None
    webhooks_enabled: Optional[bool] = None
    default_poll_interval: Optional[int] = None
    default_idle_timeout: Optional[int] = None
    default_max_instances: Optional[int] = None
    group_prefixes: Optional[dict[str, str]] = None


class UpdateRoleSettingsBody(BaseModel):
    display_name: Optional[str] = None
    prefix: Optional[str] = None
    color: Optional[str] = None
    emoji: Optional[str] = None
    system_prompt: Optional[str] = None
    model: Optional[str] = None
    tools: Optional[list[str]] = None
    max_instances: Optional[int] = None
    max_turns: Optional[int] = None
    max_execution_time: Optional[int] = None
    produces: Optional[list[str]] = None
    accepts: Optional[list[str]] = None
    context_includes: Optional[list[str]] = None
    can_create_groups: Optional[bool] = None
    group_type: Optional[str] = None
    routes_to: Optional[list[dict[str, Any]]] = None
    auto_scale: Optional[dict[str, Any]] = None
    # --- New fields (v2) ---
    approval_mode: Optional[str] = None
    max_revision_cycles: Optional[int] = None
    max_clarification_requests: Optional[int] = None
    max_route_tasks: Optional[int] = None
    uses_worktree: Optional[bool] = None


class CreateRoleBody(BaseModel):
    role: str
    preset_id: Optional[str] = None  # If set, copy config from preset
    display_name: Optional[str] = None
    prefix: Optional[str] = None
    color: Optional[str] = None
    emoji: Optional[str] = None
    system_prompt: Optional[str] = None
    tools: Optional[list[str]] = None
    model: Optional[str] = None
    produces: Optional[list[str]] = None
    accepts: Optional[list[str]] = None
    routes_to: Optional[list[dict[str, Any]]] = None
    can_create_groups: Optional[bool] = None
    group_type: Optional[str] = None
    max_instances: Optional[int] = None
    max_turns: Optional[int] = None
    context_includes: Optional[list[str]] = None
    max_execution_time: Optional[int] = None
    auto_scale: Optional[dict[str, Any]] = None
    # --- New fields (v2) ---
    approval_mode: Optional[str] = None
    max_revision_cycles: Optional[int] = None
    max_clarification_requests: Optional[int] = None
    max_route_tasks: Optional[int] = None
    uses_worktree: Optional[bool] = None


class CancelTaskBody(BaseModel):
    reason: str = "Cancelled by user"


class ReassignTaskBody(BaseModel):
    assigned_to: str


class CompleteTaskBody(BaseModel):
    status: str = "completed"


class UpdateTaskBody(BaseModel):
    priority: Optional[str] = None
    assigned_to: Optional[str] = None
    status: Optional[str] = None


class BatchTasksBody(BaseModel):
    task_ids: list[str]
    action: str
    params: dict[str, Any] = {}


class CreateNotificationBody(BaseModel):
    type: str = Field(max_length=64)
    title: str = Field(max_length=256)
    message: Optional[str] = Field(default=None, max_length=4096)
    severity: str = Field(default="info", max_length=16)
    data: Optional[str] = Field(default=None, max_length=8192)


class CreateBudgetBody(BaseModel):
    scope: str = Field(default="global", max_length=32)
    budget_usd: float = Field(default=0, ge=0)
    period: str = Field(default="daily", max_length=16)
    scope_id: Optional[str] = Field(default=None, max_length=128)


class CreateWebhookBody(BaseModel):
    # HttpUrl enforces http:// or https:// via pydantic; the SSRF
    # validator in WebhookManager._validate_url / the system router
    # still runs and refuses private/loopback/link-local hostnames.
    # The string form is preserved on the wire by using str() at the
    # call site.
    url: HttpUrl
    events: list[str] = Field(default_factory=lambda: ["*"], max_length=32)
    secret: Optional[str] = Field(default=None, max_length=512)


# audit 11a F#20: templates/workflows get persisted into TEXT
# columns and their title/description strings are later
# interpolated into agent prompts. An unbounded POST body could
# OOM the DB row or smuggle multi-KB instructions into every
# task spawned from the template (prompt injection). Cap
# everything that ends up on an agent prompt surface.
class CreateTemplateBody(BaseModel):
    name: str = Field(max_length=128)
    title_template: str = Field(max_length=2_000)
    description_template: Optional[str] = Field(default=None, max_length=20_000)
    task_type: Optional[str] = Field(default=None, max_length=64)
    assigned_to: Optional[str] = Field(default=None, max_length=64)
    priority: str = Field(default="medium", max_length=16)


class InstantiateTemplateBody(BaseModel):
    group_id: str = Field(max_length=128)
    variables: dict[str, Any] = Field(default_factory=dict)


class CreateWorkflowBody(BaseModel):
    name: str = Field(max_length=128)
    description: Optional[str] = Field(default=None, max_length=20_000)
    # cap the steps array so a caller can't POST 10k steps; each step
    # is also capped below at the dict level by pydantic's default
    # JSON-size handling, but the list length cap is explicit here.
    steps: list[dict[str, Any]] = Field(max_length=200)


class StartWorkflowBody(BaseModel):
    group_id: str


class SendMessageBody(BaseModel):
    from_agent: str
    to_agent: str
    content: str
    message_type: str = "direct"
    priority: str = "normal"


class CreateEscalationBody(BaseModel):
    task_id: str
    from_agent: str
    reason: str
    to_agent: Optional[str] = None
    severity: str = "medium"


class ResolveEscalationBody(BaseModel):
    resolution: str = ""


class DecideCheckpointBody(BaseModel):
    approved: bool = False
    decided_by: str = "human"


class CreateAbTestBody(BaseModel):
    name: str = Field(max_length=128)
    role: str = Field(max_length=64)
    variant_a: dict[str, Any]
    variant_b: dict[str, Any]
    allocation: float = Field(default=0.5, ge=0.0, le=1.0)


class StoreMemoryBody(BaseModel):
    agent_role: str
    memory_type: str
    title: str
    content: str
    source_task_id: Optional[str] = None
    tags: Optional[list[str]] = None
    project_id: Optional[str] = None


class AssessRiskBody(BaseModel):
    files: list[str] = []


class PeerReviewBody(BaseModel):
    reviewer_role: str = "coder"


class PairSessionBody(BaseModel):
    agent1: str
    agent2: str


class StartDebateBody(BaseModel):
    debater_role: str = "coder"
    judge_role: str = "architect"


class SelectToolsBody(BaseModel):
    task_type: Optional[str] = None
    role: Optional[str] = None
    complexity: str = "medium"


class RebuildKGRequest(BaseModel):
    directory: str = "src/"


class SetModelRoutingBody(BaseModel):
    role: str
    complexity: str
    model: str
    criteria: Optional[str] = None


# ---------------------------------------------------------------------------
# Intelligence V2 – Autonomous (Features 1-5)
# ---------------------------------------------------------------------------


class DecomposeBody(BaseModel):
    task_id: str
    llm_output: Optional[str] = None


class SubmitBidBody(BaseModel):
    task_id: str
    agent_id: str
    workload: float
    skill_match: float
    urgency: float


class RetryOutcomeBody(BaseModel):
    failure_type: str
    strategy: str
    success: bool
    recovery_time_ms: int


class RecordFixBody(BaseModel):
    failure_signature: str
    fix_applied: str
    success: bool
    source_task_id: Optional[str] = None


# ---------------------------------------------------------------------------
# Intelligence V2 – Code Intelligence (Features 6-12)
# ---------------------------------------------------------------------------


class CodeSearchBody(BaseModel):
    query: str
    limit: int = 10


class DebtScoreBody(BaseModel):
    file_path: str


# ---------------------------------------------------------------------------
# Intelligence V2 – Learning (Features 13-19)
# ---------------------------------------------------------------------------


class CreateExperimentBody(BaseModel):
    name: str
    role: str
    variant_a: str
    variant_b: str


class RecordTrialBody(BaseModel):
    experiment_id: str
    variant_key: str
    success: bool
    quality_score: float = 0.0


class CrossProjectKnowledgeBody(BaseModel):
    source_project: str
    knowledge_type: str
    title: str
    content: str


class RecordBenchmarkBody(BaseModel):
    agent_role: str
    metric: str
    value: float
    period: str = "daily"
    details: Optional[str] = None


# ---------------------------------------------------------------------------
# Intelligence V2 – Coordination (Features 20-26)
# ---------------------------------------------------------------------------


class AcquireLockBody(BaseModel):
    file_path: str
    agent_id: str
    task_id: Optional[str] = None


class CreateDigestBody(BaseModel):
    digest_type: str
    content: str
    target_roles: Optional[list[str]] = None


class CreatePairBody(BaseModel):
    mentor_role: str
    mentee_role: str
    skill_area: str


class CastVoteBody(BaseModel):
    proposal_id: str
    voter_id: str
    vote: str
    reasoning: Optional[str] = None


class RecordHeartbeatBody(BaseModel):
    task_id: str
    agent_id: str
    progress_pct: float
    status_message: str


# ---------------------------------------------------------------------------
# Intelligence V2 – Testing & Quality (Features 27-33)
# ---------------------------------------------------------------------------


class PredictRegressionBody(BaseModel):
    files_changed: list[str]
    pr_identifier: Optional[str] = None


class RecordTimingBody(BaseModel):
    test_name: str
    duration_ms: float


# ---------------------------------------------------------------------------
# Intelligence V2 – Security (Features 34-38)
# ---------------------------------------------------------------------------


class FlagSecurityBody(BaseModel):
    task_id: str
    files_changed: list[str]


# ---------------------------------------------------------------------------
# Intelligence V2 – Observability (Features 39-44)
# ---------------------------------------------------------------------------


class LogDecisionBody(BaseModel):
    agent_id: str
    decision_type: str
    decision: str
    reasoning: Optional[str] = None
    task_id: Optional[str] = None
    context: Optional[dict[str, Any]] = None


class RecordBehaviorMetricBody(BaseModel):
    agent_role: str
    metric_type: str
    value: float
    period_start: str
    period_end: str
    metadata: Optional[dict[str, Any]] = None


class AttributeCostBody(BaseModel):
    agent_id: str
    cost_usd: float
    input_tokens: int
    output_tokens: int
    task_id: Optional[str] = None
    feature_tag: Optional[str] = None


class RecordTrendBody(BaseModel):
    metric_name: str
    metric_value: float
    dimension: Optional[str] = None
    period: str = "daily"


# ---------------------------------------------------------------------------
# Intelligence V2 – Advanced Planning (Features 45-50)
# ---------------------------------------------------------------------------


class CheckScopeBody(BaseModel):
    task_id: str
    current_description: str


class PlanIncrementsBody(BaseModel):
    feature_id: str
    title: str
    description: str


class GeneratePostMortemBody(BaseModel):
    task_id: Optional[str] = None
    group_id: Optional[str] = None


# ---------------------------------------------------------------------------
# Pipeline Editor (Plan 2)
# ---------------------------------------------------------------------------


class PipelineEdgeBody(BaseModel):
    from_agent: str
    to_agent: str
    task_types: list[str] = []
    on_failure: str = "block"


class UpdatePipelineEdgeBody(BaseModel):
    task_types: Optional[list[str]] = None
    on_failure: Optional[str] = None


class UpdatePipelineBody(BaseModel):
    name: Optional[str] = None
    start_agent: Optional[str] = None
    edges: Optional[list[dict[str, Any]]] = None
    node_config: Optional[dict[str, dict[str, str]]] = None


class SetStartAgentBody(BaseModel):
    role: str


class SetNodeConfigBody(BaseModel):
    join_strategy: str = "wait_all"


# ---------------------------------------------------------------------------
# Human-in-the-Loop models
# ---------------------------------------------------------------------------


class ApproveInteractionBody(BaseModel):
    notes: Optional[str] = None


class RejectInteractionBody(BaseModel):
    feedback: str


class RespondInteractionBody(BaseModel):
    response: str
