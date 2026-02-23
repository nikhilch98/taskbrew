"""Integration tests for Intelligence V2 API endpoints.

Tests the full API stack (HTTP -> Router -> Manager -> DB) for all 50
intelligence features across 8 manager groups: autonomous, code-intel,
learning, coordination, testing-quality, security, observability, and
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


async def _build_v2_env(tmp_path: Path):
    """Build a test environment with all v1 + v2 intelligence managers."""
    db = Database(str(tmp_path / "test_v2.db"))
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
async def v2_client(tmp_path):
    """AsyncClient with all v2 intelligence managers wired up."""
    orch, db = await _build_v2_env(tmp_path)

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
# Autonomous endpoints (Features 1-5)
# ===================================================================


class TestAutonomousEndpoints:
    """Features 1-5: Task decomposition, work discovery, bids, retry, self-healing."""

    async def test_get_discoveries_empty(self, v2_client):
        resp = await v2_client.get("/api/v2/autonomous/discoveries")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    async def test_submit_bid_and_resolve(self, v2_client):
        goal = await _create_task(v2_client, "Bid test")
        task_id = goal["task_id"]

        resp = await v2_client.post(
            "/api/v2/autonomous/bids",
            json={
                "task_id": task_id,
                "agent_id": "coder-1",
                "workload": 0.3,
                "skill_match": 0.9,
                "urgency": 0.5,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "bid_id" in data or "id" in data

        # Resolve bids
        resolve = await v2_client.post(
            f"/api/v2/autonomous/bids/{task_id}/resolve"
        )
        assert resolve.status_code == 200
        assert resolve.json()["winner"] == "coder-1"

    async def test_retry_outcome_and_strategy(self, v2_client):
        resp = await v2_client.post(
            "/api/v2/autonomous/retry-outcomes",
            json={
                "failure_type": "timeout",
                "strategy": "exponential_backoff",
                "success": True,
                "recovery_time_ms": 5000,
            },
        )
        assert resp.status_code == 200

        best = await v2_client.get("/api/v2/autonomous/retry-strategies/timeout")
        assert best.status_code == 200

    async def test_record_and_find_fix(self, v2_client):
        resp = await v2_client.post(
            "/api/v2/autonomous/fixes",
            json={
                "failure_signature": "ModuleNotFoundError: no module named foo",
                "fix_applied": "pip install foo",
                "success": True,
            },
        )
        assert resp.status_code == 200

        find = await v2_client.get(
            "/api/v2/autonomous/similar-fixes/ModuleNotFoundError"
        )
        assert find.status_code == 200

    async def test_decompose_task(self, v2_client):
        goal = await _create_task(v2_client, "Decompose this large feature")
        task_id = goal["task_id"]

        resp = await v2_client.post(
            "/api/v2/autonomous/decompose",
            json={"task_id": task_id},
        )
        assert resp.status_code == 200
        assert "task_id" in resp.json()


# ===================================================================
# Code Intelligence endpoints (Features 6-12)
# ===================================================================


class TestCodeIntelEndpoints:
    """Features 6-12: Semantic search, patterns, smells, debt, test gaps."""

    async def test_get_patterns_empty(self, v2_client):
        resp = await v2_client.get("/api/v2/code-intel/patterns")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    async def test_get_debt_report_empty(self, v2_client):
        resp = await v2_client.get("/api/v2/code-intel/debt")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    async def test_search_by_intent(self, v2_client):
        resp = await v2_client.post(
            "/api/v2/code-intel/search",
            json={"query": "database initialization", "limit": 5},
        )
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    async def test_dead_code_detection(self, v2_client):
        resp = await v2_client.post(
            "/api/v2/code-intel/dead-code", params={"directory": "src/"}
        )
        assert resp.status_code == 200


# ===================================================================
# Learning endpoints (Features 13-19)
# ===================================================================


class TestLearningEndpoints:
    """Features 13-19: A/B experiments, benchmarks, conventions, cross-project."""

    async def test_create_experiment_and_record_trial(self, v2_client):
        resp = await v2_client.post(
            "/api/v2/learning/experiments",
            json={
                "name": "prompt_test",
                "role": "coder",
                "variant_a": "You are a developer",
                "variant_b": "You are an expert developer",
            },
        )
        assert resp.status_code == 200
        exp = resp.json()
        exp_id = exp["experiment_id"]

        trial = await v2_client.post(
            f"/api/v2/learning/experiments/{exp_id}/trials",
            json={
                "experiment_id": exp_id,
                "variant_key": "A",
                "success": True,
                "quality_score": 0.9,
            },
        )
        assert trial.status_code == 200

        winner = await v2_client.get(
            f"/api/v2/learning/experiments/{exp_id}/winner"
        )
        assert winner.status_code == 200

    async def test_get_conventions_empty(self, v2_client):
        resp = await v2_client.get("/api/v2/learning/conventions")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    async def test_benchmark_and_compare(self, v2_client):
        resp = await v2_client.post(
            "/api/v2/learning/benchmarks",
            json={
                "agent_role": "coder",
                "metric": "task_completion_time",
                "value": 120.5,
                "period": "daily",
            },
        )
        assert resp.status_code == 200

        compare = await v2_client.get(
            "/api/v2/learning/benchmarks/compare",
            params={"metric": "task_completion_time", "period": "daily"},
        )
        assert compare.status_code == 200

    async def test_cross_project_knowledge_flow(self, v2_client):
        store = await v2_client.post(
            "/api/v2/learning/cross-project",
            json={
                "source_project": "project-alpha",
                "knowledge_type": "best_practice",
                "title": "Use dependency injection",
                "content": "Always inject dependencies for testability.",
            },
        )
        assert store.status_code == 200

        find = await v2_client.get(
            "/api/v2/learning/cross-project",
            params={"knowledge_type": "best_practice"},
        )
        assert find.status_code == 200
        assert len(find.json()) >= 1

    async def test_cluster_errors(self, v2_client):
        resp = await v2_client.post(
            "/api/v2/learning/errors/cluster",
            params={"lookback_limit": 50},
        )
        assert resp.status_code == 200

    async def test_suggest_adjustments(self, v2_client):
        resp = await v2_client.get("/api/v2/learning/adjustments/coder")
        assert resp.status_code == 200

    async def test_track_corrections(self, v2_client):
        resp = await v2_client.get("/api/v2/learning/corrections/coder")
        assert resp.status_code == 200


# ===================================================================
# Coordination endpoints (Features 20-26)
# ===================================================================


class TestCoordinationEndpoints:
    """Features 20-26: Standups, locks, digests, pairs, voting, heartbeats."""

    async def test_get_standups_empty(self, v2_client):
        resp = await v2_client.get("/api/v2/coordination/standups")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    async def test_file_lock_conflict_flow(self, v2_client):
        lock1 = await v2_client.post(
            "/api/v2/coordination/locks",
            json={"file_path": "src/main.py", "agent_id": "coder-1"},
        )
        assert lock1.status_code == 200
        assert lock1.json()["conflict"] is False

        lock2 = await v2_client.post(
            "/api/v2/coordination/locks",
            json={"file_path": "src/main.py", "agent_id": "coder-2"},
        )
        assert lock2.status_code == 200
        assert lock2.json()["conflict"] is True

    async def test_release_lock(self, v2_client):
        await v2_client.post(
            "/api/v2/coordination/locks",
            json={"file_path": "src/utils.py", "agent_id": "coder-1"},
        )
        release = await v2_client.request(
            "DELETE",
            "/api/v2/coordination/locks",
            params={"file_path": "src/utils.py", "agent_id": "coder-1"},
        )
        assert release.status_code == 200

    async def test_digest_create_and_list(self, v2_client):
        resp = await v2_client.post(
            "/api/v2/coordination/digests",
            json={
                "digest_type": "daily",
                "content": "Today's progress summary",
                "target_roles": ["coder", "reviewer"],
            },
        )
        assert resp.status_code == 200
        assert "id" in resp.json()

        digests = await v2_client.get("/api/v2/coordination/digests")
        assert digests.status_code == 200
        assert len(digests.json()) >= 1

    async def test_heartbeat_flow(self, v2_client):
        goal = await _create_task(v2_client, "Heartbeat test")
        task_id = goal["task_id"]

        resp = await v2_client.post(
            "/api/v2/coordination/heartbeats",
            json={
                "task_id": task_id,
                "agent_id": "coder-1",
                "progress_pct": 50.0,
                "status_message": "Halfway done",
            },
        )
        assert resp.status_code == 200

        hbs = await v2_client.get(
            f"/api/v2/coordination/heartbeats/{task_id}"
        )
        assert hbs.status_code == 200
        assert len(hbs.json()) >= 1

    async def test_pair_programming_flow(self, v2_client):
        create = await v2_client.post(
            "/api/v2/coordination/pairs",
            json={
                "mentor_role": "architect",
                "mentee_role": "coder",
                "skill_area": "system_design",
            },
        )
        assert create.status_code == 200

        pairs = await v2_client.get("/api/v2/coordination/pairs")
        assert pairs.status_code == 200
        assert len(pairs.json()) >= 1

    async def test_proposal_and_voting_flow(self, v2_client):
        create = await v2_client.post(
            "/api/v2/coordination/proposals/PROP-001",
            params={"description": "Switch to async DB driver"},
        )
        assert create.status_code == 200

        vote = await v2_client.post(
            "/api/v2/coordination/votes",
            json={
                "proposal_id": "PROP-001",
                "voter_id": "coder-1",
                "vote": "approve",
                "reasoning": "Better performance",
            },
        )
        assert vote.status_code == 200

        tally = await v2_client.get(
            "/api/v2/coordination/votes/PROP-001/tally"
        )
        assert tally.status_code == 200

    async def test_detect_conflicts(self, v2_client):
        resp = await v2_client.get("/api/v2/coordination/conflicts")
        assert resp.status_code == 200

    async def test_generate_standup(self, v2_client):
        resp = await v2_client.post(
            "/api/v2/coordination/standups/coder-1"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "agent_id" in data


# ===================================================================
# Testing & Quality endpoints (Features 27-33)
# ===================================================================


class TestTestingQualityEndpoints:
    """Features 27-33: Test gen, mutations, property tests, regression risk."""

    async def test_get_mutation_scores_empty(self, v2_client):
        resp = await v2_client.get("/api/v2/testing/mutations")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    async def test_detect_perf_regressions_empty(self, v2_client):
        resp = await v2_client.get("/api/v2/testing/regressions")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    async def test_record_test_timing(self, v2_client):
        resp = await v2_client.post(
            "/api/v2/testing/timings",
            json={"test_name": "test_login_flow", "duration_ms": 245.3},
        )
        assert resp.status_code == 200

    async def test_predict_regression_risk(self, v2_client):
        resp = await v2_client.post(
            "/api/v2/testing/regression-risk",
            json={
                "files_changed": ["src/auth.py", "src/db.py"],
                "pr_identifier": "PR-123",
            },
        )
        assert resp.status_code == 200

    async def test_generate_checklist(self, v2_client):
        goal = await _create_task(v2_client, "Checklist test")
        task_id = goal["task_id"]

        resp = await v2_client.post(
            f"/api/v2/testing/checklists/{task_id}"
        )
        assert resp.status_code == 200


# ===================================================================
# Security endpoints (Features 34-38)
# ===================================================================


class TestSecurityEndpoints:
    """Features 34-38: Dependency scan, secrets, SAST, licenses, flags."""

    async def test_get_vulnerabilities_empty(self, v2_client):
        resp = await v2_client.get("/api/v2/security/vulnerabilities")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    async def test_get_sast_findings_empty(self, v2_client):
        resp = await v2_client.get("/api/v2/security/sast")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    async def test_get_security_flags_empty(self, v2_client):
        resp = await v2_client.get("/api/v2/security/flags")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    async def test_flag_security_changes(self, v2_client):
        goal = await _create_task(v2_client, "Security flag test")
        task_id = goal["task_id"]

        resp = await v2_client.post(
            "/api/v2/security/flags",
            json={
                "task_id": task_id,
                "files_changed": ["src/auth.py", "config/secrets.yaml"],
            },
        )
        assert resp.status_code == 200

        flags = await v2_client.get(
            "/api/v2/security/flags", params={"task_id": task_id}
        )
        assert flags.status_code == 200


# ===================================================================
# Observability endpoints (Features 39-44)
# ===================================================================


class TestObservabilityEndpoints:
    """Features 39-44: Audit trail, behavior, costs, bottlenecks, trends."""

    async def test_log_and_get_decisions(self, v2_client):
        resp = await v2_client.post(
            "/api/v2/observability/decisions",
            json={
                "agent_id": "coder-1",
                "decision_type": "task_selection",
                "decision": "Selected TSK-001",
                "reasoning": "Highest priority task",
            },
        )
        assert resp.status_code == 200

        trail = await v2_client.get(
            "/api/v2/observability/decisions",
            params={"agent_id": "coder-1"},
        )
        assert trail.status_code == 200
        assert len(trail.json()) >= 1

    async def test_cost_attribution_flow(self, v2_client):
        resp = await v2_client.post(
            "/api/v2/observability/costs",
            json={
                "agent_id": "coder-1",
                "cost_usd": 0.05,
                "input_tokens": 1000,
                "output_tokens": 500,
                "feature_tag": "code_review",
            },
        )
        assert resp.status_code == 200

        by_feature = await v2_client.get(
            "/api/v2/observability/costs/by-feature"
        )
        assert by_feature.status_code == 200

        by_agent = await v2_client.get(
            "/api/v2/observability/costs/by-agent"
        )
        assert by_agent.status_code == 200

    async def test_record_and_get_trends(self, v2_client):
        resp = await v2_client.post(
            "/api/v2/observability/trends",
            json={
                "metric_name": "test_pass_rate",
                "metric_value": 0.95,
                "period": "daily",
            },
        )
        assert resp.status_code == 200

        trends = await v2_client.get(
            "/api/v2/observability/trends/test_pass_rate"
        )
        assert trends.status_code == 200
        assert len(trends.json()) >= 1

    async def test_detect_bottlenecks(self, v2_client):
        resp = await v2_client.post("/api/v2/observability/bottlenecks")
        assert resp.status_code == 200

    async def test_record_behavior_metric(self, v2_client):
        resp = await v2_client.post(
            "/api/v2/observability/behavior",
            json={
                "agent_role": "coder",
                "metric_type": "response_time",
                "value": 3.5,
                "period_start": "2026-02-25T00:00:00Z",
                "period_end": "2026-02-26T00:00:00Z",
            },
        )
        assert resp.status_code == 200

        analytics = await v2_client.get(
            "/api/v2/observability/behavior/coder"
        )
        assert analytics.status_code == 200

    async def test_get_anomalies_empty(self, v2_client):
        resp = await v2_client.get("/api/v2/observability/anomalies")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)


# ===================================================================
# Advanced Planning endpoints (Features 45-50)
# ===================================================================


class TestAdvancedPlanningEndpoints:
    """Features 45-50: Scheduling, resources, deadlines, scope creep, increments, post-mortems."""

    async def test_get_schedule_empty(self, v2_client):
        goal = await _create_task(v2_client, "Schedule test")
        group_id = goal["group_id"]

        # GET schedule works against the scheduling_graph table directly
        schedule = await v2_client.get(
            f"/api/v2/planning/schedule/{group_id}"
        )
        assert schedule.status_code == 200
        assert isinstance(schedule.json(), list)

    async def test_snapshot_resources(self, v2_client):
        resp = await v2_client.get("/api/v2/planning/resources")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    async def test_estimate_deadline(self, v2_client):
        goal = await _create_task(v2_client, "Deadline test")
        task_id = goal["task_id"]

        resp = await v2_client.post(
            f"/api/v2/planning/deadline/{task_id}"
        )
        assert resp.status_code == 200

    async def test_scope_creep_detection(self, v2_client):
        # Create a task with a description for comparison
        goal = await _create_task(v2_client, "Scope test")
        group_id = goal["group_id"]

        # Create a task with a description
        task_resp = await v2_client.post(
            "/api/tasks",
            json={
                "group_id": group_id,
                "title": "Fix login",
                "assigned_to": "coder",
                "assigned_by": "human",
                "task_type": "bugfix",
                "description": "Fix the login button",
            },
        )
        task_id = task_resp.json()["id"]

        resp = await v2_client.post(
            "/api/v2/planning/scope-creep",
            json={
                "task_id": task_id,
                "current_description": (
                    "Fix the login button. Also refactor auth. "
                    "Add OAuth2. Add rate limiting. Deploy to staging. "
                    "Update schema. Add monitoring."
                ),
            },
        )
        assert resp.status_code == 200

    async def test_get_scope_flags(self, v2_client):
        resp = await v2_client.get("/api/v2/planning/scope-flags")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    async def test_plan_increments(self, v2_client):
        resp = await v2_client.post(
            "/api/v2/planning/increments",
            json={
                "feature_id": "FEAT-001",
                "title": "User Dashboard",
                "description": (
                    "Build user profile and add analytics and add notifications"
                ),
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) >= 2

    async def test_get_increments(self, v2_client):
        # First create increments
        await v2_client.post(
            "/api/v2/planning/increments",
            json={
                "feature_id": "FEAT-002",
                "title": "Search Feature",
                "description": "Build search index and add query parser and add filters",
            },
        )

        resp = await v2_client.get("/api/v2/planning/increments/FEAT-002")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)
        assert len(resp.json()) >= 2

    async def test_get_post_mortems_empty(self, v2_client):
        resp = await v2_client.get("/api/v2/planning/post-mortems")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    async def test_generate_post_mortem(self, v2_client):
        goal = await _create_task(v2_client, "Post-mortem test")
        group_id = goal["group_id"]

        resp = await v2_client.post(
            "/api/v2/planning/post-mortems",
            json={"group_id": group_id},
        )
        assert resp.status_code == 200

    async def test_plan_with_resources(self, v2_client):
        goal = await _create_task(v2_client, "Resource plan test")
        group_id = goal["group_id"]

        resp = await v2_client.post(
            f"/api/v2/planning/resources/{group_id}"
        )
        assert resp.status_code == 200
