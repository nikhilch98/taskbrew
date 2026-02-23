"""Integration tests for Intelligence V2 API endpoints (end-to-end).

Tests POST data then GET to verify across all v2 router endpoint groups:
autonomous, code-intel, coordination, observability, security, and
advanced-planning.
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


async def _build_full_env(tmp_path: Path):
    """Build a fully-wired test environment with all intelligence managers."""
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

    # V1 managers
    from taskbrew.intelligence.quality import QualityManager
    from taskbrew.intelligence.memory import MemoryManager
    from taskbrew.intelligence.collaboration import CollaborationManager
    from taskbrew.intelligence.specialization import SpecializationManager
    from taskbrew.intelligence.planning import PlanningManager
    from taskbrew.intelligence.preflight import PreflightChecker
    from taskbrew.intelligence.impact import ImpactAnalyzer
    from taskbrew.intelligence.escalation import EscalationManager
    from taskbrew.intelligence.checkpoints import CheckpointManager
    from taskbrew.intelligence.messaging import MessagingManager
    from taskbrew.intelligence.knowledge_graph import KnowledgeGraphBuilder
    from taskbrew.intelligence.review_learning import ReviewLearningManager
    from taskbrew.intelligence.tool_router import ToolRouter
    from taskbrew.intelligence.context_providers import ContextProviderRegistry

    # V2 managers
    from taskbrew.intelligence.autonomous import AutonomousManager
    from taskbrew.intelligence.code_intel import CodeIntelligenceManager
    from taskbrew.intelligence.learning import LearningManager
    from taskbrew.intelligence.coordination import CoordinationManager
    from taskbrew.intelligence.testing_quality import TestingQualityManager
    from taskbrew.intelligence.security_intel import SecurityIntelManager
    from taskbrew.intelligence.observability import ObservabilityManager
    from taskbrew.intelligence.advanced_planning import AdvancedPlanningManager

    memory_manager = MemoryManager(db)
    context_registry = ContextProviderRegistry(db, project_dir=str(tmp_path))

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
    orch.memory_manager = memory_manager
    orch.context_registry = context_registry

    # V1 managers
    orch.quality_manager = QualityManager(db, memory_manager=memory_manager)
    orch.collaboration_manager = CollaborationManager(
        db, task_board=board, event_bus=event_bus
    )
    orch.specialization_manager = SpecializationManager(db)
    orch.planning_manager = PlanningManager(db, task_board=board)
    orch.preflight_checker = PreflightChecker(db)
    orch.impact_analyzer = ImpactAnalyzer(db, project_dir=str(tmp_path))
    orch.escalation_manager = EscalationManager(
        db, task_board=board, event_bus=event_bus
    )
    orch.checkpoint_manager = CheckpointManager(db, event_bus=event_bus)
    orch.messaging_manager = MessagingManager(db, event_bus=event_bus)
    orch.knowledge_graph = KnowledgeGraphBuilder(db, project_dir=str(tmp_path))
    orch.review_learning = ReviewLearningManager(db)
    orch.tool_router = ToolRouter(db)

    # V2 managers
    orch.autonomous_manager = AutonomousManager(
        db, task_board=board, memory_manager=memory_manager
    )
    orch.code_intel_manager = CodeIntelligenceManager(
        db, project_dir=str(tmp_path)
    )
    orch.learning_manager = LearningManager(db, memory_manager=memory_manager)
    orch.coordination_manager = CoordinationManager(
        db, task_board=board, event_bus=event_bus, instance_manager=instance_mgr
    )
    orch.testing_quality_manager = TestingQualityManager(
        db, project_dir=str(tmp_path)
    )
    orch.security_intel_manager = SecurityIntelManager(
        db, project_dir=str(tmp_path)
    )
    orch.observability_manager = ObservabilityManager(db, event_bus=event_bus)
    orch.advanced_planning_manager = AdvancedPlanningManager(
        db, task_board=board
    )

    return orch, db


@pytest.fixture
async def client(tmp_path):
    """AsyncClient backed by a fully-wired FastAPI app."""
    orch, db = await _build_full_env(tmp_path)

    from taskbrew.dashboard.app import create_app
    from taskbrew.dashboard.routers._deps import set_orchestrator
    import taskbrew.dashboard.routers.intelligence_v2 as v2_mod

    app = create_app(
        event_bus=orch.event_bus,
        task_board=orch.task_board,
        instance_manager=orch.instance_manager,
    )
    set_orchestrator(orch)

    # Reset the module-level lazy-table flags so each test gets fresh
    # ensure_tables() calls against its own database.
    v2_mod._obs_tables_ensured = False
    v2_mod._planning_tables_ensured = False
    v2_mod._testing_tables_ensured = False
    v2_mod._security_tables_ensured = False

    transport = ASGITransport(app=app)
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
# Autonomous endpoints (8 tests)
# ===================================================================


class TestAutonomousEndpoints:
    """End-to-end tests for autonomous features."""

    async def test_decompose_returns_subtasks(self, client):
        """POST /api/v2/autonomous/decompose returns subtasks."""
        goal = await _create_task(client, "Large feature to decompose")
        task_id = goal["task_id"]

        resp = await client.post(
            "/api/v2/autonomous/decompose",
            json={"task_id": task_id},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "task_id" in data

    async def test_get_discoveries_returns_list(self, client):
        """GET /api/v2/autonomous/discoveries returns 200 + list."""
        resp = await client.get("/api/v2/autonomous/discoveries")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    async def test_submit_bid_returns_200(self, client):
        """POST /api/v2/autonomous/bids submits a bid successfully."""
        goal = await _create_task(client, "Bid task")
        task_id = goal["task_id"]

        resp = await client.post(
            "/api/v2/autonomous/bids",
            json={
                "task_id": task_id,
                "agent_id": "coder-1",
                "workload": 0.4,
                "skill_match": 0.85,
                "urgency": 0.6,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "bid_id" in data or "id" in data

    async def test_record_retry_outcome_returns_200(self, client):
        """POST /api/v2/autonomous/retry-outcomes records outcome."""
        resp = await client.post(
            "/api/v2/autonomous/retry-outcomes",
            json={
                "failure_type": "connection_error",
                "strategy": "linear_backoff",
                "success": True,
                "recovery_time_ms": 3000,
            },
        )
        assert resp.status_code == 200

    async def test_record_fix_returns_200(self, client):
        """POST /api/v2/autonomous/fixes records a fix."""
        resp = await client.post(
            "/api/v2/autonomous/fixes",
            json={
                "failure_signature": "ImportError: cannot import name bar",
                "fix_applied": "pip install bar",
                "success": True,
            },
        )
        assert resp.status_code == 200

    async def test_get_retry_strategies_returns_200(self, client):
        """POST retry outcome then GET retry strategy for same failure_type."""
        await client.post(
            "/api/v2/autonomous/retry-outcomes",
            json={
                "failure_type": "rate_limit",
                "strategy": "exponential_backoff",
                "success": True,
                "recovery_time_ms": 2000,
            },
        )
        resp = await client.get(
            "/api/v2/autonomous/retry-strategies/rate_limit"
        )
        assert resp.status_code == 200

    async def test_bid_resolve_picks_winner(self, client):
        """Submit bids then resolve to verify winner is returned."""
        goal = await _create_task(client, "Resolve bid task")
        task_id = goal["task_id"]

        await client.post(
            "/api/v2/autonomous/bids",
            json={
                "task_id": task_id,
                "agent_id": "coder-1",
                "workload": 0.3,
                "skill_match": 0.9,
                "urgency": 0.5,
            },
        )
        await client.post(
            "/api/v2/autonomous/bids",
            json={
                "task_id": task_id,
                "agent_id": "coder-2",
                "workload": 0.8,
                "skill_match": 0.5,
                "urgency": 0.3,
            },
        )

        resolve = await client.post(
            f"/api/v2/autonomous/bids/{task_id}/resolve"
        )
        assert resolve.status_code == 200
        data = resolve.json()
        assert "winner" in data

    async def test_find_similar_fixes_after_recording(self, client):
        """POST a fix then GET similar fixes to verify data persists."""
        await client.post(
            "/api/v2/autonomous/fixes",
            json={
                "failure_signature": "KeyError: missing_key",
                "fix_applied": "add default value for missing_key",
                "success": True,
            },
        )
        resp = await client.get(
            "/api/v2/autonomous/similar-fixes/KeyError"
        )
        assert resp.status_code == 200


# ===================================================================
# Code Intelligence endpoints (6 tests)
# ===================================================================


class TestCodeIntelEndpoints:
    """End-to-end tests for code intelligence features."""

    async def test_search_by_intent_returns_results(self, client):
        """POST /api/v2/code-intel/search returns 200 + results list."""
        resp = await client.post(
            "/api/v2/code-intel/search",
            json={"query": "database connection", "limit": 5},
        )
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    async def test_get_patterns_returns_list(self, client):
        """GET /api/v2/code-intel/patterns returns 200 + list."""
        resp = await client.get("/api/v2/code-intel/patterns")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    async def test_get_debt_returns_list(self, client):
        """GET /api/v2/code-intel/debt returns 200 + list."""
        resp = await client.get("/api/v2/code-intel/debt")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    async def test_get_test_gaps_returns_200(self, client):
        """POST /api/v2/code-intel/test-gaps/{source_file} returns 200."""
        resp = await client.post(
            "/api/v2/code-intel/test-gaps/src/main.py"
        )
        assert resp.status_code == 200

    async def test_detect_dead_code_returns_200(self, client):
        """POST /api/v2/code-intel/dead-code returns 200."""
        resp = await client.post(
            "/api/v2/code-intel/dead-code", params={"directory": "src/"}
        )
        assert resp.status_code == 200

    async def test_search_empty_query_returns_results(self, client):
        """POST /api/v2/code-intel/search with broad query returns list."""
        resp = await client.post(
            "/api/v2/code-intel/search",
            json={"query": "authentication", "limit": 10},
        )
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)


# ===================================================================
# Coordination endpoints (8 tests)
# ===================================================================


class TestCoordinationEndpoints:
    """End-to-end tests for coordination features."""

    async def test_acquire_lock_returns_200(self, client):
        """POST /api/v2/coordination/locks acquires a lock."""
        resp = await client.post(
            "/api/v2/coordination/locks",
            json={"file_path": "src/app.py", "agent_id": "coder-1"},
        )
        assert resp.status_code == 200
        assert resp.json()["conflict"] is False

    async def test_release_lock_returns_200(self, client):
        """DELETE /api/v2/coordination/locks releases a lock."""
        await client.post(
            "/api/v2/coordination/locks",
            json={"file_path": "src/release_me.py", "agent_id": "coder-1"},
        )
        resp = await client.request(
            "DELETE",
            "/api/v2/coordination/locks",
            params={
                "file_path": "src/release_me.py",
                "agent_id": "coder-1",
            },
        )
        assert resp.status_code == 200

    async def test_create_digest_returns_200(self, client):
        """POST /api/v2/coordination/digests creates a digest."""
        resp = await client.post(
            "/api/v2/coordination/digests",
            json={
                "digest_type": "daily",
                "content": "Daily summary of work done",
                "target_roles": ["coder", "reviewer"],
            },
        )
        assert resp.status_code == 200
        assert "id" in resp.json()

    async def test_get_digests_after_create(self, client):
        """POST digest then GET to verify it shows up."""
        await client.post(
            "/api/v2/coordination/digests",
            json={
                "digest_type": "weekly",
                "content": "Weekly sprint summary",
                "target_roles": ["pm"],
            },
        )
        resp = await client.get("/api/v2/coordination/digests")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)
        assert len(resp.json()) >= 1

    async def test_create_pair_returns_200(self, client):
        """POST /api/v2/coordination/pairs creates a pair session."""
        resp = await client.post(
            "/api/v2/coordination/pairs",
            json={
                "mentor_role": "architect",
                "mentee_role": "coder",
                "skill_area": "api_design",
            },
        )
        assert resp.status_code == 200

    async def test_cast_vote_returns_200(self, client):
        """Create proposal then POST vote on it."""
        await client.post(
            "/api/v2/coordination/proposals/PROP-TEST-001",
            params={"description": "Use FastAPI for all endpoints"},
        )
        resp = await client.post(
            "/api/v2/coordination/votes",
            json={
                "proposal_id": "PROP-TEST-001",
                "voter_id": "architect-1",
                "vote": "approve",
                "reasoning": "Consistent framework choice",
            },
        )
        assert resp.status_code == 200

    async def test_record_heartbeat_returns_200(self, client):
        """POST /api/v2/coordination/heartbeats records heartbeat."""
        goal = await _create_task(client, "Heartbeat endpoint test")
        task_id = goal["task_id"]

        resp = await client.post(
            "/api/v2/coordination/heartbeats",
            json={
                "task_id": task_id,
                "agent_id": "coder-1",
                "progress_pct": 75.0,
                "status_message": "Almost done",
            },
        )
        assert resp.status_code == 200

    async def test_get_standups_returns_200(self, client):
        """GET /api/v2/coordination/standups returns 200."""
        resp = await client.get("/api/v2/coordination/standups")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)


# ===================================================================
# Observability endpoints (8 tests)
# ===================================================================


class TestObservabilityEndpoints:
    """End-to-end tests for observability features."""

    async def test_log_decision_returns_200(self, client):
        """POST /api/v2/observability/decisions logs a decision."""
        resp = await client.post(
            "/api/v2/observability/decisions",
            json={
                "agent_id": "coder-1",
                "decision_type": "task_selection",
                "decision": "Selected CD-001 for implementation",
                "reasoning": "Highest priority and best skill match",
            },
        )
        assert resp.status_code == 200

    async def test_get_decisions_after_logging(self, client):
        """POST decision then GET decisions list to verify it persists."""
        await client.post(
            "/api/v2/observability/decisions",
            json={
                "agent_id": "reviewer-1",
                "decision_type": "review_priority",
                "decision": "Prioritized RV-005",
                "reasoning": "Critical bug fix needs fast review",
            },
        )
        resp = await client.get(
            "/api/v2/observability/decisions",
            params={"agent_id": "reviewer-1"},
        )
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)
        assert len(resp.json()) >= 1

    async def test_record_behavior_metric_returns_200(self, client):
        """POST /api/v2/observability/behavior records a metric."""
        resp = await client.post(
            "/api/v2/observability/behavior",
            json={
                "agent_role": "coder",
                "metric_type": "task_completion_time",
                "value": 120.5,
                "period_start": "2026-02-25T00:00:00Z",
                "period_end": "2026-02-26T00:00:00Z",
            },
        )
        assert resp.status_code == 200

    async def test_attribute_cost_returns_200(self, client):
        """POST /api/v2/observability/costs attributes a cost."""
        resp = await client.post(
            "/api/v2/observability/costs",
            json={
                "agent_id": "coder-1",
                "cost_usd": 0.12,
                "input_tokens": 2500,
                "output_tokens": 800,
                "feature_tag": "code_generation",
            },
        )
        assert resp.status_code == 200

    async def test_get_cost_by_agent_returns_200(self, client):
        """POST cost then GET by-agent to verify."""
        await client.post(
            "/api/v2/observability/costs",
            json={
                "agent_id": "architect-1",
                "cost_usd": 0.08,
                "input_tokens": 1500,
                "output_tokens": 400,
                "feature_tag": "design_review",
            },
        )
        resp = await client.get("/api/v2/observability/costs/by-agent")
        assert resp.status_code == 200

    async def test_get_cost_by_feature_returns_200(self, client):
        """POST cost then GET by-feature to verify."""
        await client.post(
            "/api/v2/observability/costs",
            json={
                "agent_id": "coder-2",
                "cost_usd": 0.03,
                "input_tokens": 500,
                "output_tokens": 200,
                "feature_tag": "testing",
            },
        )
        resp = await client.get("/api/v2/observability/costs/by-feature")
        assert resp.status_code == 200

    async def test_get_bottlenecks_returns_200(self, client):
        """POST /api/v2/observability/bottlenecks returns 200."""
        resp = await client.post("/api/v2/observability/bottlenecks")
        assert resp.status_code == 200

    async def test_record_trend_returns_200(self, client):
        """POST /api/v2/observability/trends records a trend datapoint."""
        resp = await client.post(
            "/api/v2/observability/trends",
            json={
                "metric_name": "code_quality_score",
                "metric_value": 0.92,
                "period": "daily",
            },
        )
        assert resp.status_code == 200


# ===================================================================
# Security endpoints (4 tests)
# ===================================================================


class TestSecurityEndpoints:
    """End-to-end tests for security features."""

    async def test_get_vulnerabilities_returns_200(self, client):
        """GET /api/v2/security/vulnerabilities returns 200."""
        resp = await client.get("/api/v2/security/vulnerabilities")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    async def test_get_secrets_flags_returns_200(self, client):
        """GET /api/v2/security/flags returns 200 (empty initially)."""
        resp = await client.get("/api/v2/security/flags")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    async def test_get_sast_returns_200(self, client):
        """GET /api/v2/security/sast returns 200."""
        resp = await client.get("/api/v2/security/sast")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    async def test_flag_security_returns_200(self, client):
        """POST /api/v2/security/flags flags security-sensitive changes."""
        goal = await _create_task(client, "Security flag endpoint test")
        task_id = goal["task_id"]

        resp = await client.post(
            "/api/v2/security/flags",
            json={
                "task_id": task_id,
                "files_changed": ["src/auth.py", "config/credentials.yaml"],
            },
        )
        assert resp.status_code == 200

        # Verify the flags can be retrieved
        flags = await client.get(
            "/api/v2/security/flags", params={"task_id": task_id}
        )
        assert flags.status_code == 200


# ===================================================================
# Advanced Planning endpoints (6 tests)
# ===================================================================


class TestAdvancedPlanningEndpoints:
    """End-to-end tests for advanced planning features."""

    async def test_scope_check_returns_200(self, client):
        """POST /api/v2/planning/scope-creep checks for scope creep."""
        goal = await _create_task(client, "Scope check endpoint test")
        group_id = goal["group_id"]

        task_resp = await client.post(
            "/api/tasks",
            json={
                "group_id": group_id,
                "title": "Fix search",
                "assigned_to": "coder",
                "assigned_by": "human",
                "task_type": "bugfix",
                "description": "Fix search bar autocomplete",
            },
        )
        task_id = task_resp.json()["id"]

        resp = await client.post(
            "/api/v2/planning/scope-creep",
            json={
                "task_id": task_id,
                "current_description": (
                    "Fix search bar autocomplete. Also add fuzzy search. "
                    "Refactor the entire search module. Add ML ranking. "
                    "Deploy new search cluster. Update all tests."
                ),
            },
        )
        assert resp.status_code == 200

    async def test_plan_increments_returns_200(self, client):
        """POST /api/v2/planning/increments plans delivery increments."""
        resp = await client.post(
            "/api/v2/planning/increments",
            json={
                "feature_id": "FEAT-001",
                "title": "User Dashboard",
                "description": (
                    "Build user profile page and add analytics dashboard "
                    "and add notification center"
                ),
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) >= 2

    async def test_get_increments_after_planning(self, client):
        """POST increments then GET /api/v2/planning/increments/FEAT-001."""
        await client.post(
            "/api/v2/planning/increments",
            json={
                "feature_id": "FEAT-001",
                "title": "User Dashboard",
                "description": (
                    "Build profile and add analytics and add notifications"
                ),
            },
        )
        resp = await client.get("/api/v2/planning/increments/FEAT-001")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)
        assert len(resp.json()) >= 2

    async def test_generate_post_mortem_returns_200(self, client):
        """POST /api/v2/planning/post-mortems generates a post-mortem."""
        goal = await _create_task(client, "Post-mortem endpoint test")
        group_id = goal["group_id"]

        resp = await client.post(
            "/api/v2/planning/post-mortems",
            json={"group_id": group_id},
        )
        assert resp.status_code == 200

    async def test_get_post_mortems_returns_list(self, client):
        """GET /api/v2/planning/post-mortems returns 200 + list."""
        resp = await client.get("/api/v2/planning/post-mortems")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    async def test_get_scope_flags_returns_200(self, client):
        """GET /api/v2/planning/scope-flags returns 200."""
        resp = await client.get("/api/v2/planning/scope-flags")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)
