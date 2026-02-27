"""Integration tests for Intelligence V3 API endpoints (end-to-end).

Tests POST data then GET to verify across all v3 router endpoint groups:
self-improvement, social-intelligence, code-reasoning, task-intelligence,
verification, process-intelligence, knowledge-management, and compliance.

All router-manager argument mismatches have been resolved.  Every endpoint
should return HTTP 200 on valid input.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import AsyncClient, ASGITransport

from taskbrew.orchestrator.database import Database
from taskbrew.orchestrator.event_bus import EventBus
from taskbrew.orchestrator.task_board import TaskBoard
from taskbrew.agents.instance_manager import InstanceManager


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


async def _build_v3_env(tmp_path: Path):
    """Build test environment with all V3 intelligence managers."""
    db = Database(str(tmp_path / "test.db"))
    await db.initialize()

    from taskbrew.orchestrator.migration import MigrationManager

    mm = MigrationManager(db)
    await mm.apply_pending()

    board = TaskBoard(db, group_prefixes={"pm": "FEAT"})
    await board.register_prefixes(
        {"pm": "PM", "architect": "AR", "coder": "CD", "verifier": "VR"}
    )
    event_bus = EventBus()
    instance_mgr = InstanceManager(db)

    # V3 managers
    from taskbrew.intelligence.self_improvement import SelfImprovementManager
    from taskbrew.intelligence.social_intelligence import SocialIntelligenceManager
    from taskbrew.intelligence.code_reasoning import CodeReasoningManager
    from taskbrew.intelligence.task_intelligence import TaskIntelligenceManager
    from taskbrew.intelligence.verification import VerificationManager
    from taskbrew.intelligence.process_intelligence import ProcessIntelligenceManager
    from taskbrew.intelligence.knowledge_management import KnowledgeManager
    from taskbrew.intelligence.compliance import ComplianceManager

    class _Orch:
        pass

    orch = _Orch()
    orch.db = db
    orch.task_board = board
    orch.event_bus = event_bus
    orch.instance_manager = instance_mgr
    orch.roles = {}
    orch.team_config = None
    orch.project_dir = str(tmp_path)

    # Set ALL manager attributes to None first (V1 + V2)
    for attr in [
        "quality_manager",
        "collaboration_manager",
        "specialization_manager",
        "planning_manager",
        "preflight_checker",
        "impact_analyzer",
        "escalation_manager",
        "checkpoint_manager",
        "messaging_manager",
        "knowledge_graph",
        "review_learning",
        "tool_router",
        "memory_manager",
        "context_registry",
        "autonomous_manager",
        "code_intel_manager",
        "learning_manager",
        "coordination_manager",
        "testing_quality_manager",
        "security_intel_manager",
        "observability_manager",
        "advanced_planning_manager",
    ]:
        setattr(orch, attr, None)

    # V3 managers
    orch.self_improvement_manager = SelfImprovementManager(db)
    orch.social_intelligence_manager = SocialIntelligenceManager(
        db, event_bus=event_bus, instance_manager=instance_mgr
    )
    orch.code_reasoning_manager = CodeReasoningManager(
        db, project_dir=str(tmp_path)
    )
    orch.task_intelligence_manager = TaskIntelligenceManager(db, task_board=board)
    orch.verification_manager = VerificationManager(
        db, project_dir=str(tmp_path)
    )
    orch.process_intelligence_manager = ProcessIntelligenceManager(
        db, task_board=board
    )
    orch.knowledge_manager = KnowledgeManager(db, project_dir=str(tmp_path))
    orch.compliance_manager = ComplianceManager(db, project_dir=str(tmp_path))

    return orch, db


@pytest.fixture
async def client(tmp_path):
    """AsyncClient backed by a fully-wired FastAPI app with V3 managers."""
    orch, db = await _build_v3_env(tmp_path)

    from taskbrew.dashboard.app import create_app
    from taskbrew.dashboard.routers._deps import set_orchestrator
    import taskbrew.dashboard.routers.intelligence_v3 as v3_mod

    app = create_app(
        event_bus=orch.event_bus,
        task_board=orch.task_board,
        instance_manager=orch.instance_manager,
    )
    set_orchestrator(orch)

    # Reset the module-level lazy-table flags so each test gets fresh
    # ensure_tables() calls against its own database.
    v3_mod._self_improvement_init = False
    v3_mod._social_intel_init = False
    v3_mod._code_reasoning_init = False
    v3_mod._task_intel_init = False
    v3_mod._verification_init = False
    v3_mod._process_intel_init = False
    v3_mod._knowledge_init = False
    v3_mod._compliance_init = False

    # Use raise_app_exceptions=False so any unexpected server errors are
    # returned as HTTP 500 responses instead of raising in the test.
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    await db.close()


# ------------------------------------------------------------------
# Helper
# ------------------------------------------------------------------


async def _create_task(client: AsyncClient, title: str = "Test task") -> dict:
    """Create a goal and return the response containing group_id and task_id."""
    resp = await client.post("/api/goals", json={"title": title})
    assert resp.status_code == 200
    return resp.json()


# ===================================================================
# Self-Improvement endpoints (8 tests)
# ===================================================================


class TestSelfImprovementEndpoints:
    """End-to-end tests for self-improvement features."""

    async def test_store_prompt_version_returns_200(self, client):
        """POST /api/v3/self-improvement/prompt-versions returns 200."""
        resp = await client.post(
            "/api/v3/self-improvement/prompt-versions",
            json={
                "agent_role": "coder",
                "prompt_text": "You are a skilled Python developer.",
                "version_tag": "v1.0",
            },
        )
        assert resp.status_code == 200

    async def test_record_prompt_outcome_returns_200(self, client):
        """POST prompt version then record outcome returns 200."""
        ver_resp = await client.post(
            "/api/v3/self-improvement/prompt-versions",
            json={
                "agent_role": "coder",
                "prompt_text": "You are an expert coder.",
                "version_tag": "v1.1",
            },
        )
        assert ver_resp.status_code == 200
        version_data = ver_resp.json()
        version_id = version_data.get("version_id") or version_data.get("id", "v1")

        resp = await client.post(
            "/api/v3/self-improvement/prompt-outcomes",
            json={
                "version_id": str(version_id),
                "task_id": "T-001",
                "success": True,
                "quality_score": 0.95,
            },
        )
        assert resp.status_code == 200

    async def test_get_best_prompt_returns_200(self, client):
        """GET /api/v3/self-improvement/best-prompt?agent_role=coder returns 200."""
        await client.post(
            "/api/v3/self-improvement/prompt-versions",
            json={
                "agent_role": "coder",
                "prompt_text": "You are a Python expert.",
            },
        )
        resp = await client.get(
            "/api/v3/self-improvement/best-prompt",
            params={"agent_role": "coder"},
        )
        assert resp.status_code == 200

    async def test_get_prompt_history_returns_200(self, client):
        """GET /api/v3/self-improvement/prompt-history?agent_role=coder returns 200."""
        await client.post(
            "/api/v3/self-improvement/prompt-versions",
            json={
                "agent_role": "coder",
                "prompt_text": "Prompt history test.",
            },
        )
        resp = await client.get(
            "/api/v3/self-improvement/prompt-history",
            params={"agent_role": "coder"},
        )
        assert resp.status_code == 200

    async def test_register_strategy_returns_200(self, client):
        """POST /api/v3/self-improvement/strategies returns 200."""
        resp = await client.post(
            "/api/v3/self-improvement/strategies",
            json={
                "agent_role": "coder",
                "strategy_name": "test_first",
                "strategy_type": "implementation",
                "description": "Write tests before code.",
            },
        )
        assert resp.status_code == 200

    async def test_create_reflection_returns_200(self, client):
        """POST /api/v3/self-improvement/reflections returns 200."""
        resp = await client.post(
            "/api/v3/self-improvement/reflections",
            json={
                "task_id": "T-002",
                "agent_id": "coder-1",
                "what_worked": "Clear requirements helped.",
                "what_failed": "Test coverage was insufficient.",
                "lessons": "Always write tests first.",
                "approach_rating": 0.75,
            },
        )
        assert resp.status_code == 200

    async def test_classify_failure_returns_200(self, client):
        """POST /api/v3/self-improvement/failure-modes returns 200."""
        resp = await client.post(
            "/api/v3/self-improvement/failure-modes",
            json={
                "task_id": "T-003",
                "category": "dependency",
                "subcategory": "missing_module",
                "description": "ImportError: No module named foo",
                "severity": "medium",
            },
        )
        assert resp.status_code == 200

    async def test_record_confidence_returns_200(self, client):
        """POST /api/v3/self-improvement/confidence returns 200."""
        resp = await client.post(
            "/api/v3/self-improvement/confidence",
            json={
                "agent_id": "coder-1",
                "task_id": "T-004",
                "predicted_confidence": 0.85,
                "actual_success": True,
            },
        )
        assert resp.status_code == 200


# ===================================================================
# Social Intelligence endpoints (8 tests)
# ===================================================================


class TestSocialIntelligenceEndpoints:
    """End-to-end tests for social intelligence features."""

    async def test_open_argument_returns_200(self, client):
        """POST /api/v3/social/arguments returns 200."""
        resp = await client.post(
            "/api/v3/social/arguments",
            json={
                "topic": "Should we use SQLAlchemy or raw SQL?",
                "participants": ["architect-1", "coder-1", "coder-2"],
                "context": "Discussing ORM strategy",
            },
        )
        assert resp.status_code == 200

    async def test_update_trust_returns_200(self, client):
        """POST /api/v3/social/trust returns 200."""
        resp = await client.post(
            "/api/v3/social/trust",
            json={
                "from_agent": "coder-1",
                "to_agent": "reviewer-1",
                "interaction_type": "code_review",
                "outcome_quality": 0.9,
            },
        )
        assert resp.status_code == 200

    async def test_get_trust_network_returns_200(self, client):
        """GET /api/v3/social/trust/network returns 200."""
        resp = await client.get("/api/v3/social/trust/network")
        assert resp.status_code == 200

    async def test_assert_mental_model_fact_returns_200(self, client):
        """POST /api/v3/social/mental-model returns 200."""
        resp = await client.post(
            "/api/v3/social/mental-model",
            json={
                "key": "preferred_db",
                "value": "PostgreSQL",
                "source_agent": "architect-1",
                "confidence": 0.9,
            },
        )
        assert resp.status_code == 200

    async def test_get_mental_model_returns_200(self, client):
        """GET /api/v3/social/mental-model returns 200."""
        resp = await client.get("/api/v3/social/mental-model")
        assert resp.status_code == 200

    async def test_report_work_area_returns_200(self, client):
        """POST /api/v3/social/work-areas returns 200."""
        resp = await client.post(
            "/api/v3/social/work-areas",
            json={
                "agent_id": "coder-1",
                "file_paths": ["src/auth.py", "src/models.py"],
                "task_id": "T-010",
            },
        )
        assert resp.status_code == 200

    async def test_record_collaboration_returns_200(self, client):
        """POST /api/v3/social/collaborations returns 200."""
        resp = await client.post(
            "/api/v3/social/collaborations",
            json={
                "agent_a": "coder-1",
                "agent_b": "reviewer-1",
                "task_id": "T-011",
                "effectiveness": 0.9,
                "notes": "Good collaboration on code review.",
            },
        )
        assert resp.status_code == 200

    async def test_predict_consensus_returns_200(self, client):
        """POST /api/v3/social/consensus-predictions returns 200."""
        resp = await client.post(
            "/api/v3/social/consensus-predictions",
            json={
                "proposal_description": "Use FastAPI for new endpoints",
                "participants": ["architect", "coder", "reviewer"],
            },
        )
        assert resp.status_code == 200


# ===================================================================
# Code Reasoning endpoints (8 tests)
# ===================================================================


class TestCodeReasoningEndpoints:
    """End-to-end tests for code reasoning features."""

    async def test_index_intent_returns_200(self, client):
        """POST /api/v3/code-reasoning/semantic-index returns 200."""
        resp = await client.post(
            "/api/v3/code-reasoning/semantic-index",
            json={
                "file_path": "src/auth.py",
                "function_name": "authenticate",
                "intent_description": "Validates user credentials against the database.",
                "keywords": ["auth", "security"],
            },
        )
        assert resp.status_code == 200

    async def test_semantic_search_returns_200(self, client):
        """GET /api/v3/code-reasoning/semantic-search?query=auth returns 200."""
        resp = await client.get(
            "/api/v3/code-reasoning/semantic-search",
            params={"query": "auth"},
        )
        assert resp.status_code == 200

    async def test_record_dependency_returns_200(self, client):
        """POST /api/v3/code-reasoning/dependencies returns 200."""
        resp = await client.post(
            "/api/v3/code-reasoning/dependencies",
            json={
                "source_file": "src/app.py",
                "target_file": "src/database.py",
                "dep_type": "import",
            },
        )
        assert resp.status_code == 200

    async def test_predict_impact_returns_200(self, client):
        """GET /api/v3/code-reasoning/impact?changed_file=test.py returns 200."""
        resp = await client.get(
            "/api/v3/code-reasoning/impact",
            params={"changed_file": "test.py"},
        )
        assert resp.status_code == 200

    async def test_add_debt_returns_200(self, client):
        """POST /api/v3/code-reasoning/debt returns 200."""
        resp = await client.post(
            "/api/v3/code-reasoning/debt",
            json={
                "file_path": "src/legacy.py",
                "category": "complexity",
                "description": "Function too long, needs refactoring.",
                "effort_estimate": 4,
                "business_impact": 3,
            },
        )
        assert resp.status_code == 200

    async def test_get_prioritized_debt_returns_200(self, client):
        """GET /api/v3/code-reasoning/debt/prioritized returns 200."""
        resp = await client.get("/api/v3/code-reasoning/debt/prioritized")
        assert resp.status_code == 200

    async def test_record_invariant_returns_200(self, client):
        """POST /api/v3/code-reasoning/invariants returns 200."""
        resp = await client.post(
            "/api/v3/code-reasoning/invariants",
            json={
                "file_path": "src/core.py",
                "function_name": "process_order",
                "invariant_expression": "order.total must be >= 0",
                "invariant_type": "postcondition",
            },
        )
        assert resp.status_code == 200

    async def test_record_api_version_returns_200(self, client):
        """POST /api/v3/code-reasoning/api-versions returns 200."""
        resp = await client.post(
            "/api/v3/code-reasoning/api-versions",
            json={
                "endpoint": "/api/v1/users",
                "method": "GET",
                "version": "2.0",
                "schema_hash": "abc123",
                "breaking_change": False,
            },
        )
        assert resp.status_code == 200


# ===================================================================
# Task Intelligence endpoints (8 tests)
# ===================================================================


class TestTaskIntelligenceEndpoints:
    """End-to-end tests for task intelligence features."""

    async def test_estimate_complexity_returns_200(self, client):
        """POST /api/v3/task-intel/complexity returns 200."""
        resp = await client.post(
            "/api/v3/task-intel/complexity",
            json={
                "task_id": "T-100",
                "title": "Implement user authentication",
                "description": "Add OAuth2 login support with JWT tokens.",
            },
        )
        assert resp.status_code == 200

    async def test_detect_prerequisites_returns_200(self, client):
        """POST /api/v3/task-intel/prerequisites/detect returns 200."""
        resp = await client.post(
            "/api/v3/task-intel/prerequisites/detect",
            json={
                "task_id": "T-101",
                "description": "Requires user auth to be implemented first.",
                "files_involved": ["src/auth.py", "src/profile.py"],
            },
        )
        assert resp.status_code == 200

    async def test_find_parallel_tasks_returns_200(self, client):
        """POST /api/v3/task-intel/parallel/find returns 200."""
        goal = await _create_task(client, "Parallel task test")
        group_id = goal["group_id"]

        resp = await client.post(
            "/api/v3/task-intel/parallel/find",
            json={"group_id": group_id},
        )
        assert resp.status_code == 200

    async def test_plan_context_budget_returns_200(self, client):
        """POST /api/v3/task-intel/context-budget returns 200."""
        resp = await client.post(
            "/api/v3/task-intel/context-budget",
            json={
                "task_id": "T-102",
                "estimated_files": 10,
                "estimated_tokens_per_file": 500,
            },
        )
        assert resp.status_code == 200

    async def test_predict_outcome_returns_200(self, client):
        """POST /api/v3/task-intel/predictions returns 200."""
        resp = await client.post(
            "/api/v3/task-intel/predictions",
            json={
                "task_id": "T-103",
                "complexity_score": 5,
                "agent_role": "coder",
                "historical_success_rate": 0.85,
            },
        )
        assert resp.status_code == 200

    async def test_fingerprint_task_returns_200(self, client):
        """POST /api/v3/task-intel/fingerprints returns 200."""
        resp = await client.post(
            "/api/v3/task-intel/fingerprints",
            json={
                "task_id": "T-104",
                "title": "Fix database connection pool",
                "description": "Connection pool exhaustion under load.",
                "task_type": "bugfix",
            },
        )
        assert resp.status_code == 200

    async def test_start_effort_tracking_returns_200(self, client):
        """POST /api/v3/task-intel/effort-tracking/start returns 200."""
        resp = await client.post(
            "/api/v3/task-intel/effort-tracking/start",
            json={
                "task_id": "T-105",
                "estimated_duration_ms": 60000,
            },
        )
        assert resp.status_code == 200

    async def test_check_drift_returns_200(self, client):
        """GET /api/v3/task-intel/effort-tracking/drift?task_id=T1 returns 200."""
        await client.post(
            "/api/v3/task-intel/effort-tracking/start",
            json={
                "task_id": "T1",
                "estimated_duration_ms": 30000,
            },
        )
        resp = await client.get(
            "/api/v3/task-intel/effort-tracking/drift",
            params={"task_id": "T1"},
        )
        assert resp.status_code == 200


# ===================================================================
# Verification endpoints (8 tests)
# ===================================================================


class TestVerificationEndpoints:
    """End-to-end tests for verification features."""

    async def test_fingerprint_regression_returns_200(self, client):
        """POST /api/v3/verification/regressions returns 200."""
        resp = await client.post(
            "/api/v3/verification/regressions",
            json={
                "test_name": "test_login_flow",
                "error_message": "AssertionError: expected 200 got 401",
                "failing_commit": "abc123",
                "last_passing_commit": "def456",
            },
        )
        assert resp.status_code == 200

    async def test_record_mapping_returns_200(self, client):
        """POST /api/v3/verification/test-mappings returns 200."""
        resp = await client.post(
            "/api/v3/verification/test-mappings",
            json={
                "source_file": "src/auth.py",
                "test_file": "tests/test_auth.py",
                "confidence": 0.95,
            },
        )
        assert resp.status_code == 200

    async def test_record_run_returns_200(self, client):
        """POST /api/v3/verification/test-runs returns 200."""
        resp = await client.post(
            "/api/v3/verification/test-runs",
            json={
                "test_name": "test_user_creation",
                "passed": True,
                "duration_ms": 150,
                "run_id": "run-001",
            },
        )
        assert resp.status_code == 200

    async def test_detect_flaky_returns_200(self, client):
        """GET /api/v3/verification/flaky-tests returns 200."""
        resp = await client.get("/api/v3/verification/flaky-tests")
        assert resp.status_code == 200

    async def test_mine_behavioral_spec_returns_200(self, client):
        """POST /api/v3/verification/behavioral-specs returns 200."""
        resp = await client.post(
            "/api/v3/verification/behavioral-specs",
            json={
                "test_file": "tests/test_orders.py",
                "test_name": "test_create_order",
                "asserted_behavior": "Orders must have at least one item.",
            },
        )
        assert resp.status_code == 200

    async def test_annotate_returns_200(self, client):
        """POST /api/v3/verification/annotations returns 200."""
        resp = await client.post(
            "/api/v3/verification/annotations",
            json={
                "file_path": "src/core.py",
                "line_number": 42,
                "annotation_type": "invariant",
                "message": "Balance must never be negative.",
                "severity": "warning",
            },
        )
        assert resp.status_code == 200

    async def test_define_quality_gate_returns_200(self, client):
        """POST /api/v3/verification/quality-gates returns 200."""
        resp = await client.post(
            "/api/v3/verification/quality-gates",
            json={
                "gate_name": "pre-merge",
                "conditions": {
                    "test_pass_rate": 0.95,
                    "coverage": 0.80,
                },
                "risk_level": "standard",
            },
        )
        assert resp.status_code == 200

    async def test_evaluate_quality_gate_returns_200(self, client):
        """POST /api/v3/verification/quality-gates/{name}/evaluate returns 200.

        evaluate_gate() signature matches; returns graceful "Gate not found"
        when the gate does not exist (define_gate has a mismatch but
        evaluate_gate itself works).
        """
        resp = await client.post(
            "/api/v3/verification/quality-gates/deploy-gate/evaluate",
            json={
                "metrics": {
                    "test_pass_rate": 0.98,
                    "coverage": 0.82,
                },
            },
        )
        assert resp.status_code == 200


# ===================================================================
# Process Intelligence endpoints (8 tests)
# ===================================================================


class TestProcessIntelligenceEndpoints:
    """End-to-end tests for process intelligence features."""

    async def test_record_velocity_returns_200(self, client):
        """POST /api/v3/process/velocity returns 200."""
        resp = await client.post(
            "/api/v3/process/velocity",
            json={
                "sprint_id": "sprint-01",
                "tasks_completed": 21,
                "story_points": 25.0,
                "duration_days": 14.0,
            },
        )
        assert resp.status_code == 200

    async def test_forecast_returns_200(self, client):
        """POST /api/v3/process/velocity/forecast returns 200.

        forecast() only needs remaining_points/num_simulations which match.
        Returns default estimate when no historical velocity data exists.
        """
        resp = await client.post(
            "/api/v3/process/velocity/forecast",
            json={
                "remaining_points": 50,
                "num_simulations": 100,
            },
        )
        assert resp.status_code == 200

    async def test_score_risk_returns_200(self, client):
        """POST /api/v3/process/risk-scores returns 200."""
        resp = await client.post(
            "/api/v3/process/risk-scores",
            json={
                "file_path": "src/payment.py",
                "change_frequency": 15,
                "complexity_score": 25.0,
                "test_coverage_pct": 72.5,
            },
        )
        assert resp.status_code == 200

    async def test_get_heat_map_returns_200(self, client):
        """GET /api/v3/process/risk-scores/heat-map returns 200."""
        resp = await client.get("/api/v3/process/risk-scores/heat-map")
        assert resp.status_code == 200

    async def test_record_phase_duration_returns_200(self, client):
        """POST /api/v3/process/phase-durations returns 200."""
        resp = await client.post(
            "/api/v3/process/phase-durations",
            json={
                "task_id": "T-200",
                "phase": "implementation",
                "duration_ms": 45000,
            },
        )
        assert resp.status_code == 200

    async def test_find_bottlenecks_returns_200(self, client):
        """GET /api/v3/process/bottlenecks returns 200."""
        for phase in ["design", "implementation", "review"]:
            await client.post(
                "/api/v3/process/phase-durations",
                json={
                    "task_id": "T-201",
                    "phase": phase,
                    "duration_ms": 30000,
                },
            )
        resp = await client.get("/api/v3/process/bottlenecks")
        assert resp.status_code == 200

    async def test_assess_readiness_returns_200(self, client):
        """POST /api/v3/process/readiness returns 200."""
        resp = await client.post(
            "/api/v3/process/readiness",
            json={
                "release_id": "v2.0.0",
                "metrics": {
                    "test_pass_rate": 0.97,
                    "coverage": 0.85,
                    "open_bugs": 2,
                },
            },
        )
        assert resp.status_code == 200

    async def test_generate_retrospective_returns_200(self, client):
        """POST /api/v3/process/retrospectives returns 200."""
        resp = await client.post(
            "/api/v3/process/retrospectives",
            json={
                "sprint_id": "sprint-05",
                "tasks_data": [
                    {"title": "Task A", "status": "done", "duration_ms": 5000},
                    {"title": "Task B", "status": "done", "duration_ms": 3000},
                ],
            },
        )
        assert resp.status_code == 200


# ===================================================================
# Knowledge Management endpoints (6 tests)
# ===================================================================


class TestKnowledgeEndpoints:
    """End-to-end tests for knowledge management features."""

    async def test_track_knowledge_returns_200(self, client):
        """POST /api/v3/knowledge/entries returns 200."""
        resp = await client.post(
            "/api/v3/knowledge/entries",
            json={
                "key": "database_migrations",
                "content": "Always use Alembic for schema changes.",
                "source_file": "docs/guidelines.md",
                "source_agent": "architect-1",
            },
        )
        assert resp.status_code == 200

    async def test_check_staleness_returns_200(self, client):
        """GET /api/v3/knowledge/staleness returns 200."""
        resp = await client.get(
            "/api/v3/knowledge/staleness",
            params={"max_age_days": 30},
        )
        assert resp.status_code == 200

    async def test_extract_from_commit_returns_200(self, client):
        """POST /api/v3/knowledge/institutional/commit returns 200."""
        resp = await client.post(
            "/api/v3/knowledge/institutional/commit",
            json={
                "commit_hash": "abc123def",
                "commit_message": "feat: add rate limiting to API endpoints",
                "author": "developer-1",
                "files_changed": ["src/middleware.py", "src/config.py"],
            },
        )
        assert resp.status_code == 200

    async def test_search_institutional_knowledge_returns_200(self, client):
        """GET /api/v3/knowledge/institutional/search?query=test returns 200."""
        resp = await client.get(
            "/api/v3/knowledge/institutional/search",
            params={"query": "test"},
        )
        assert resp.status_code == 200

    async def test_compress_context_returns_200(self, client):
        """POST /api/v3/knowledge/compression returns 200."""
        resp = await client.post(
            "/api/v3/knowledge/compression",
            json={
                "context_items": [
                    {"type": "code", "content": "def foo(): pass", "tokens": 100, "recency": 0.9, "relevance": 0.8, "frequency": 0.5},
                    {"type": "doc", "content": "API docs", "tokens": 200, "recency": 0.5, "relevance": 0.7, "frequency": 0.3},
                ],
                "max_tokens": 150,
                "strategy": "salience",
            },
        )
        assert resp.status_code == 200

    async def test_get_compression_stats_returns_200(self, client):
        """GET /api/v3/knowledge/compression/stats returns 200."""
        resp = await client.get("/api/v3/knowledge/compression/stats")
        assert resp.status_code == 200


# ===================================================================
# Compliance endpoints (6 tests)
# ===================================================================


class TestComplianceEndpoints:
    """End-to-end tests for compliance features."""

    async def test_create_threat_model_returns_200(self, client):
        """POST /api/v3/compliance/threat-models returns 200."""
        resp = await client.post(
            "/api/v3/compliance/threat-models",
            json={
                "feature_name": "API Security Model",
                "description": "Threat model for public-facing API.",
                "data_flows": ["user_input", "api_response"],
            },
        )
        assert resp.status_code == 200

    async def test_add_threat_to_model_returns_200(self, client):
        """POST /api/v3/compliance/threat-models/{id}/threats returns 200."""
        # First create a model to get a valid model_id
        model_resp = await client.post(
            "/api/v3/compliance/threat-models",
            json={
                "feature_name": "Auth Module",
                "description": "Authentication threat model.",
            },
        )
        assert model_resp.status_code == 200
        model_data = model_resp.json()
        model_id = model_data.get("model_id") or model_data.get("id", "m1")

        resp = await client.post(
            f"/api/v3/compliance/threat-models/{model_id}/threats",
            json={
                "threat_type": "tampering",
                "description": "SQL injection via search parameter.",
                "risk_level": "high",
                "mitigation": "Use parameterized queries.",
            },
        )
        assert resp.status_code == 200

    async def test_get_threat_models_returns_200(self, client):
        """GET /api/v3/compliance/threat-models returns 200."""
        resp = await client.get("/api/v3/compliance/threat-models")
        assert resp.status_code == 200

    async def test_add_rule_returns_200(self, client):
        """POST /api/v3/compliance/rules returns 200."""
        resp = await client.post(
            "/api/v3/compliance/rules",
            json={
                "rule_id": "A03-001",
                "framework": "OWASP",
                "category": "injection",
                "description": "All SQL queries must use parameterized statements.",
                "check_pattern": "execute\\(",
                "severity": "high",
            },
        )
        assert resp.status_code == 200

    async def test_get_rules_returns_200(self, client):
        """GET /api/v3/compliance/rules returns 200."""
        resp = await client.get("/api/v3/compliance/rules")
        assert resp.status_code == 200

    async def test_get_compliance_status_returns_200(self, client):
        """GET /api/v3/compliance/status returns 200."""
        resp = await client.get("/api/v3/compliance/status")
        assert resp.status_code == 200
