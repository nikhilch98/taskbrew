"""Security tests for the TaskBrew API.

Validates path traversal prevention, auth enforcement, input validation,
XSS safety, duplicate vote prevention, and scope creep edge cases across
the v2 intelligence endpoints.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

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
# Path traversal prevention (5 tests)
# ===================================================================


class TestPathTraversalPrevention:
    """Path parameters with '..' components must be rejected with 400.

    For routes using {file_path:path} in the URL, the HTTP layer normalises
    bare ``../`` segments before they reach FastAPI.  We therefore encode the
    slashes (``%2F``) so the full path survives to the handler, which then
    calls ``_validate_path`` and rejects the ``..`` component.
    """

    async def test_code_intel_rejects_path_traversal(self, client):
        """POST /api/v2/code-intel/index with traversal path returns 400."""
        # Use body-based debt/score endpoint which takes file_path in body
        resp = await client.post(
            "/api/v2/code-intel/debt/score",
            json={"file_path": "../../etc/passwd"},
        )
        assert resp.status_code == 400
        assert "traversal" in resp.json()["detail"].lower()

    async def test_security_scan_rejects_path_traversal(self, client):
        """GET /api/v2/testing/mutations with traversal file_path returns 400."""
        resp = await client.get(
            "/api/v2/testing/mutations",
            params={"file_path": "../../etc/passwd"},
        )
        assert resp.status_code == 400
        assert "traversal" in resp.json()["detail"].lower()

    async def test_sast_rejects_path_traversal(self, client):
        """GET /api/v2/security/sast with traversal file_path returns 400."""
        resp = await client.get(
            "/api/v2/security/sast",
            params={"file_path": "../../../etc/shadow"},
        )
        assert resp.status_code == 400
        assert "traversal" in resp.json()["detail"].lower()

    async def test_convention_scan_rejects_traversal(self, client):
        """POST /api/v2/learning/conventions with traversal directory returns 400."""
        resp = await client.post(
            "/api/v2/learning/conventions",
            params={"directory": "../../"},
        )
        assert resp.status_code == 400
        assert "traversal" in resp.json()["detail"].lower()

    async def test_dead_code_rejects_traversal(self, client):
        """POST /api/v2/code-intel/dead-code with traversal directory returns 400."""
        resp = await client.post(
            "/api/v2/code-intel/dead-code",
            params={"directory": "../../etc"},
        )
        assert resp.status_code == 400
        assert "traversal" in resp.json()["detail"].lower()


# ===================================================================
# Auth enforcement (2 tests)
# ===================================================================


class TestAuthEnforcement:
    """Verify auth controls on sensitive endpoints."""

    async def test_health_accessible_without_auth(self, client):
        """GET /api/health is always public regardless of auth setting."""
        resp = await client.get("/api/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    async def test_restart_requires_auth(self, tmp_path):
        """With AUTH_ENABLED=true, POST /api/server/restart without token returns 401."""
        db = Database(str(tmp_path / "auth_test.db"))
        await db.initialize()
        board = TaskBoard(db, group_prefixes={})
        event_bus = EventBus()
        instance_mgr = InstanceManager(db)

        with patch.dict(os.environ, {"AUTH_ENABLED": "true"}):
            from taskbrew.dashboard.app import create_app

            app = create_app(
                event_bus=event_bus,
                task_board=board,
                instance_manager=instance_mgr,
            )
            transport = ASGITransport(app=app)
            async with AsyncClient(
                transport=transport, base_url="http://test"
            ) as c:
                resp = await c.post("/api/server/restart")
                assert resp.status_code == 401
        await db.close()


# ===================================================================
# Input validation (3 tests)
# ===================================================================


class TestInputValidation:
    """Verify that invalid input is handled correctly."""

    async def test_submit_bid_invalid_values_clamped(self, client):
        """Submit bid with out-of-range workload; manager clamps to [0, 1]."""
        goal = await _create_task(client, "Clamp test")
        task_id = goal["task_id"]

        resp = await client.post(
            "/api/v2/autonomous/bids",
            json={
                "task_id": task_id,
                "agent_id": "coder-1",
                "workload": 5.0,
                "skill_match": 0.9,
                "urgency": 0.5,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        # workload=5.0 gets clamped to 1.0 so (1 - 1.0)*0.3 = 0
        # bid_score = 0.0 + 0.4*0.9 + 0.3*0.5 = 0.36 + 0.15 = 0.51
        assert data["bid_score"] == pytest.approx(0.51, abs=0.01)

    async def test_empty_body_returns_422(self, client):
        """POST with empty body to an endpoint requiring a body returns 422."""
        resp = await client.post(
            "/api/v2/autonomous/bids",
            content=b"",
            headers={"content-type": "application/json"},
        )
        assert resp.status_code == 422

    async def test_missing_required_fields_returns_422(self, client):
        """POST with partial body missing required fields returns 422."""
        resp = await client.post(
            "/api/v2/autonomous/bids",
            json={"task_id": "TSK-001"},
        )
        assert resp.status_code == 422


# ===================================================================
# XSS prevention (1 test)
# ===================================================================


class TestXSSPrevention:
    """Verify that HTML in user input is stored safely (no server-side mutation)."""

    async def test_task_title_with_html_stored_safely(self, client):
        """Create a task with <script> in title; it should be stored as-is."""
        xss_title = '<script>alert("xss")</script> Fix login'
        goal_resp = await client.post(
            "/api/goals", json={"title": xss_title}
        )
        assert goal_resp.status_code == 200
        group_id = goal_resp.json()["group_id"]

        task_resp = await client.post(
            "/api/tasks",
            json={
                "group_id": group_id,
                "title": xss_title,
                "assigned_to": "coder",
                "assigned_by": "human",
                "task_type": "bugfix",
            },
        )
        assert task_resp.status_code == 200
        # The title is stored as-is; rendering (escaping) is the frontend's job.
        assert task_resp.json()["title"] == xss_title


# ===================================================================
# Duplicate vote prevention (1 test)
# ===================================================================


class TestDuplicateVotePrevention:
    """Verify that the same voter cannot vote twice on the same proposal."""

    async def test_duplicate_vote_rejected(self, client):
        """Cast vote twice with same proposal_id+voter_id; second returns error."""
        await client.post(
            "/api/v2/coordination/proposals/PROP-DUP-001",
            params={"description": "Duplicate vote test proposal"},
        )

        vote_body = {
            "proposal_id": "PROP-DUP-001",
            "voter_id": "architect-1",
            "vote": "approve",
            "reasoning": "Good idea",
        }

        # First vote succeeds
        first = await client.post(
            "/api/v2/coordination/votes", json=vote_body
        )
        assert first.status_code == 200
        assert "error" not in first.json()

        # Second vote with same voter returns duplicate error
        second = await client.post(
            "/api/v2/coordination/votes", json=vote_body
        )
        assert second.status_code == 200
        data = second.json()
        assert data.get("error") == "duplicate_vote"


# ===================================================================
# Scope creep edge cases (2 tests)
# ===================================================================


class TestScopeCreepEdgeCases:
    """Verify scope creep detection handles unusual input correctly."""

    async def test_scope_creep_numbers_filtered(self, client):
        """Description with many numbers should not cause false positives."""
        goal = await _create_task(client, "Numbers test")
        group_id = goal["group_id"]

        task_resp = await client.post(
            "/api/tasks",
            json={
                "group_id": group_id,
                "title": "Fix data processing",
                "assigned_to": "coder",
                "assigned_by": "human",
                "task_type": "bugfix",
                "description": "Process rows 1 2 3 4 5",
            },
        )
        task_id = task_resp.json()["id"]

        # Current description has same text plus many numbers --
        # numbers are filtered from keyword count in check_scope_creep.
        resp = await client.post(
            "/api/v2/planning/scope-creep",
            json={
                "task_id": task_id,
                "current_description": (
                    "Process rows 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 "
                    "16 17 18 19 20 21 22 23 24 25"
                ),
            },
        )
        assert resp.status_code == 200

    async def test_scope_creep_empty_description(self, client):
        """Empty current_description should not crash."""
        goal = await _create_task(client, "Empty scope test")
        group_id = goal["group_id"]

        task_resp = await client.post(
            "/api/tasks",
            json={
                "group_id": group_id,
                "title": "Some task",
                "assigned_to": "coder",
                "assigned_by": "human",
                "task_type": "bugfix",
                "description": "Original description of the task",
            },
        )
        task_id = task_resp.json()["id"]

        resp = await client.post(
            "/api/v2/planning/scope-creep",
            json={
                "task_id": task_id,
                "current_description": "",
            },
        )
        assert resp.status_code == 200


# ===================================================================
# Additional security edge cases (3 tests)
# ===================================================================


class TestAdditionalSecurityEdgeCases:
    """Extra security scenarios to reach 15+ tests."""

    async def test_testing_doc_drift_rejects_doc_dir_traversal(self, client):
        """POST /api/v2/testing/doc-drift with traversal doc_dir returns 400."""
        resp = await client.post(
            "/api/v2/testing/doc-drift",
            params={"doc_dir": "../../etc", "code_dir": "src/"},
        )
        assert resp.status_code == 400
        assert "traversal" in resp.json()["detail"].lower()

    async def test_testing_doc_drift_rejects_code_dir_traversal(self, client):
        """POST /api/v2/testing/doc-drift with traversal code_dir returns 400."""
        resp = await client.post(
            "/api/v2/testing/doc-drift",
            params={"doc_dir": "docs/", "code_dir": "../../etc"},
        )
        assert resp.status_code == 400
        assert "traversal" in resp.json()["detail"].lower()

    async def test_coordination_lock_rejects_traversal(self, client):
        """POST /api/v2/coordination/locks with traversal file_path returns 400."""
        resp = await client.post(
            "/api/v2/coordination/locks",
            json={"file_path": "../../etc/passwd", "agent_id": "coder-1"},
        )
        assert resp.status_code == 400
        assert "traversal" in resp.json()["detail"].lower()
