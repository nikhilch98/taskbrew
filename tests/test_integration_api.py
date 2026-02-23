"""Integration tests: API endpoint smoke tests.

Verifies that critical API endpoints return expected status codes when
the orchestrator is wired up with all intelligence managers.
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
    await board.register_prefixes({"pm": "PM", "architect": "AR", "coder": "CD", "verifier": "VR"})
    event_bus = EventBus()
    instance_mgr = InstanceManager(db)

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

    # V2 intelligence managers
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

    # Build a fake orchestrator object matching what the routers expect
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
    orch.quality_manager = QualityManager(db, memory_manager=memory_manager)
    orch.collaboration_manager = CollaborationManager(db, task_board=board, event_bus=event_bus)
    orch.specialization_manager = SpecializationManager(db)
    orch.planning_manager = PlanningManager(db, task_board=board)
    orch.preflight_checker = PreflightChecker(db)
    orch.impact_analyzer = ImpactAnalyzer(db, project_dir=str(tmp_path))
    orch.escalation_manager = EscalationManager(db, task_board=board, event_bus=event_bus)
    orch.checkpoint_manager = CheckpointManager(db, event_bus=event_bus)
    orch.messaging_manager = MessagingManager(db, event_bus=event_bus)
    orch.knowledge_graph = KnowledgeGraphBuilder(db, project_dir=str(tmp_path))
    orch.review_learning = ReviewLearningManager(db)
    orch.tool_router = ToolRouter(db)

    # V2 managers
    orch.autonomous_manager = AutonomousManager(db, task_board=board, memory_manager=memory_manager)
    orch.code_intel_manager = CodeIntelligenceManager(db, project_dir=str(tmp_path))
    orch.learning_manager = LearningManager(db, memory_manager=memory_manager)
    orch.coordination_manager = CoordinationManager(db, task_board=board, event_bus=event_bus, instance_manager=instance_mgr)
    orch.testing_quality_manager = TestingQualityManager(db, project_dir=str(tmp_path))
    orch.security_intel_manager = SecurityIntelManager(db, project_dir=str(tmp_path))
    orch.observability_manager = ObservabilityManager(db, event_bus=event_bus)
    orch.advanced_planning_manager = AdvancedPlanningManager(db)

    return orch, db


@pytest.fixture
async def client(tmp_path):
    """AsyncClient backed by a fully-wired FastAPI app."""
    orch, db = await _build_full_env(tmp_path)

    from taskbrew.dashboard.app import create_app
    from taskbrew.dashboard.routers._deps import set_orchestrator

    app = create_app(
        event_bus=orch.event_bus,
        task_board=orch.task_board,
        instance_manager=orch.instance_manager,
    )
    # Override the orchestrator with our fully-wired version
    set_orchestrator(orch)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    await db.close()


# Also keep a minimal client without intelligence managers for 503 tests
@pytest.fixture
async def minimal_client(tmp_path):
    """AsyncClient backed by a minimal app without intelligence managers."""
    db = Database(str(tmp_path / "minimal.db"))
    await db.initialize()
    board = TaskBoard(db, group_prefixes={"pm": "FEAT"})
    await board.register_prefixes({"pm": "PM", "architect": "AR", "coder": "CD"})
    event_bus = EventBus()
    instance_mgr = InstanceManager(db)

    from taskbrew.dashboard.app import create_app

    app = create_app(
        event_bus=event_bus,
        task_board=board,
        instance_manager=instance_mgr,
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    await db.close()


# ------------------------------------------------------------------
# Smoke tests: endpoints that should always return 200
# ------------------------------------------------------------------


class TestCoreEndpoints:
    """Verify core task/group endpoints work end-to-end."""

    async def test_get_health_returns_200(self, client):
        resp = await client.get("/api/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    async def test_get_board_returns_200(self, client):
        resp = await client.get("/api/board")
        assert resp.status_code == 200
        assert isinstance(resp.json(), dict)

    async def test_get_groups_returns_200(self, client):
        resp = await client.get("/api/groups")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    async def test_create_goal_and_get_tasks(self, client):
        """POST /api/goals should create a group and task."""
        resp = await client.post("/api/goals", json={"title": "Integration test goal"})
        assert resp.status_code == 200
        data = resp.json()
        assert "group_id" in data
        assert "task_id" in data

        # GET /api/board should now have tasks
        board_resp = await client.get("/api/board")
        assert board_resp.status_code == 200
        board_data = board_resp.json()
        total_tasks = sum(len(v) for v in board_data.values())
        assert total_tasks >= 1

    async def test_create_task_returns_200(self, client):
        """POST /api/tasks with valid body returns the created task."""
        # First create a goal to get a group_id
        goal_resp = await client.post("/api/goals", json={"title": "Task creation test"})
        group_id = goal_resp.json()["group_id"]

        resp = await client.post("/api/tasks", json={
            "group_id": group_id,
            "title": "Design API schema",
            "assigned_to": "architect",
            "assigned_by": "human",
            "task_type": "tech_design",
        })
        assert resp.status_code == 200
        assert resp.json()["title"] == "Design API schema"

    async def test_get_agents_returns_200(self, client):
        resp = await client.get("/api/agents")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    async def test_get_notifications_returns_200(self, client):
        resp = await client.get("/api/notifications")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    async def test_invalid_endpoint_returns_404(self, client):
        resp = await client.get("/api/nonexistent")
        assert resp.status_code == 404


# ------------------------------------------------------------------
# Intelligence endpoints: should return 200 when managers are present
# ------------------------------------------------------------------


class TestIntelligenceEndpoints:
    """Verify intelligence endpoints respond correctly when managers are wired."""

    async def test_get_quality_scores_returns_200(self, client):
        resp = await client.get("/api/quality/scores")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    async def test_get_memories_returns_200(self, client):
        resp = await client.get("/api/memories")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    async def test_get_skills_returns_200(self, client):
        resp = await client.get("/api/skills")
        assert resp.status_code == 200

    async def test_get_knowledge_graph_stats_returns_200(self, client):
        resp = await client.get("/api/knowledge-graph/stats")
        assert resp.status_code == 200

    async def test_get_collaborations_returns_200(self, client):
        resp = await client.get("/api/collaborations")
        assert resp.status_code == 200

    async def test_get_escalations_returns_200(self, client):
        resp = await client.get("/api/escalations")
        assert resp.status_code == 200

    async def test_get_checkpoints_returns_200(self, client):
        resp = await client.get("/api/checkpoints")
        assert resp.status_code == 200

    async def test_get_review_patterns_returns_200(self, client):
        resp = await client.get("/api/review-patterns")
        assert resp.status_code == 200

    async def test_get_messages_returns_200(self, client):
        resp = await client.get("/api/messages")
        assert resp.status_code == 200

    async def test_get_tool_profiles_returns_200(self, client):
        resp = await client.get("/api/tools/profiles")
        assert resp.status_code == 200
        data = resp.json()
        assert "task_profiles" in data
        assert "role_tools" in data


# ------------------------------------------------------------------
# 503 tests: endpoints without managers should return 503
# ------------------------------------------------------------------


class TestMissingManagerEndpoints:
    """When intelligence managers are None, endpoints should return 503."""

    async def test_quality_scores_503_without_manager(self, minimal_client):
        resp = await minimal_client.get("/api/quality/scores")
        assert resp.status_code == 503

    async def test_memories_503_without_manager(self, minimal_client):
        resp = await minimal_client.get("/api/memories")
        assert resp.status_code == 503

    async def test_skills_503_without_manager(self, minimal_client):
        resp = await minimal_client.get("/api/skills")
        assert resp.status_code == 503

    async def test_knowledge_graph_503_without_manager(self, minimal_client):
        resp = await minimal_client.get("/api/knowledge-graph/stats")
        assert resp.status_code == 503

    async def test_collaborations_503_without_manager(self, minimal_client):
        resp = await minimal_client.get("/api/collaborations")
        assert resp.status_code == 503


# ------------------------------------------------------------------
# Template pages
# ------------------------------------------------------------------


class TestTemplatePages:
    """Verify HTML template pages are accessible."""

    async def test_index_page_returns_200(self, client):
        resp = await client.get("/")
        assert resp.status_code == 200

    async def test_metrics_page_returns_200(self, client):
        resp = await client.get("/metrics")
        assert resp.status_code == 200

    async def test_settings_page_returns_200(self, client):
        resp = await client.get("/settings")
        assert resp.status_code == 200


# ------------------------------------------------------------------
# Intelligence V2 smoke tests
# ------------------------------------------------------------------


class TestIntelligenceV2Endpoints:
    """Basic smoke tests for v2 intelligence endpoints."""

    async def test_get_discoveries_returns_200(self, client):
        resp = await client.get("/api/v2/autonomous/discoveries")
        assert resp.status_code == 200

    async def test_get_patterns_returns_200(self, client):
        resp = await client.get("/api/v2/code-intel/patterns")
        assert resp.status_code == 200

    async def test_get_conventions_returns_200(self, client):
        resp = await client.get("/api/v2/learning/conventions")
        assert resp.status_code == 200

    async def test_get_standups_returns_200(self, client):
        resp = await client.get("/api/v2/coordination/standups")
        assert resp.status_code == 200

    async def test_get_decisions_returns_200(self, client):
        resp = await client.get("/api/v2/observability/decisions")
        assert resp.status_code == 200

    async def test_get_post_mortems_returns_200(self, client):
        resp = await client.get("/api/v2/planning/post-mortems")
        assert resp.status_code == 200

    async def test_get_vulnerabilities_returns_200(self, client):
        resp = await client.get("/api/v2/security/vulnerabilities")
        assert resp.status_code == 200

    async def test_get_mutations_returns_200(self, client):
        resp = await client.get("/api/v2/testing/mutations")
        assert resp.status_code == 200
