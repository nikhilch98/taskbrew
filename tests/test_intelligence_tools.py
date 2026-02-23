"""Tests for intelligence MCP tools (validates underlying modules used by tools)."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import pytest

from taskbrew.orchestrator.database import Database
from taskbrew.intelligence.memory import MemoryManager
from taskbrew.intelligence.quality import QualityManager
from taskbrew.intelligence.tool_router import ToolRouter, TOOL_PROFILES, ROLE_TOOLS


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture
async def db():
    """Create and initialise an in-memory database."""
    database = Database(":memory:")
    await database.initialize()
    yield database
    await database.close()


async def _create_task(db: Database) -> str:
    """Insert a minimal task + group so foreign key constraints are satisfied."""
    now = datetime.now(timezone.utc).isoformat()
    task_id = f"TST-{uuid.uuid4().hex[:4]}"
    group_id = f"GRP-{task_id}"
    await db.execute(
        "INSERT INTO groups (id, title, status, created_at) VALUES (?, 'Test', 'active', ?)",
        (group_id, now),
    )
    await db.execute(
        "INSERT INTO tasks (id, group_id, title, status, priority, created_at) VALUES (?, ?, 'Test', 'in_progress', 'medium', ?)",
        (task_id, group_id, now),
    )
    return task_id


# ------------------------------------------------------------------
# Memory tools tests
# ------------------------------------------------------------------


async def test_memory_store_and_recall(db: Database):
    """Memory manager can store and recall memories (used by MCP tools)."""
    mm = MemoryManager(db)
    result = await mm.store_lesson(
        role="coder",
        title="Use pytest fixtures",
        content="Fixtures provide reusable test setup",
        source_task_id=None,
    )
    assert isinstance(result, dict)
    assert result["title"] == "Use pytest fixtures"

    results = await mm.recall(agent_role="coder", query="pytest")
    assert len(results) >= 1
    assert any("pytest" in r.get("title", "").lower() for r in results)


async def test_memory_recall_empty(db: Database):
    """Recall with no matching data returns empty list."""
    mm = MemoryManager(db)
    results = await mm.recall(agent_role="coder", query="nonexistent_xyz_topic")
    assert results == []


# ------------------------------------------------------------------
# Quality tools tests
# ------------------------------------------------------------------


async def test_quality_confidence_scoring(db: Database):
    """Quality manager scores confidence (used by MCP tools)."""
    task_id = await _create_task(db)
    qm = QualityManager(db)
    score = await qm.score_confidence(task_id, "coder", "All tests pass and verified.")
    assert 0.0 <= score <= 1.0
    assert score >= 0.7  # high-confidence output


async def test_quality_confidence_low(db: Database):
    """Low-confidence output scores lower."""
    task_id = await _create_task(db)
    qm = QualityManager(db)
    score = await qm.score_confidence(
        task_id, "coder", "I'm not sure, maybe this could be right, perhaps."
    )
    assert score < 0.7


# ------------------------------------------------------------------
# Impact tools tests
# ------------------------------------------------------------------


async def test_impact_trace(db: Database):
    """Impact analyzer traces dependencies (used by MCP tools)."""
    from taskbrew.intelligence.impact import ImpactAnalyzer
    analyzer = ImpactAnalyzer(db)
    deps = await analyzer.trace_dependencies("nonexistent_file.py")
    assert isinstance(deps, dict)
    assert "file" in deps
    assert "imports" in deps
    assert "imported_by" in deps


# ------------------------------------------------------------------
# Project context tests
# ------------------------------------------------------------------


async def test_project_context_empty(db: Database):
    """Project context returns empty string when no knowledge stored."""
    mm = MemoryManager(db)
    ctx = await mm.get_project_context(role="coder", query="anything")
    assert isinstance(ctx, str)
    assert ctx == ""


async def test_project_context_with_data(db: Database):
    """Project context returns stored knowledge."""
    mm = MemoryManager(db)
    await mm.add_project_knowledge(
        role="coder",
        title="Project uses FastAPI",
        content="The dashboard is built with FastAPI and Jinja2",
        project_id="proj-1",
    )
    ctx = await mm.get_project_context(role="coder", query="FastAPI dashboard", project_id="proj-1")
    assert isinstance(ctx, str)
    assert "FastAPI" in ctx


# ------------------------------------------------------------------
# ToolRouter tests
# ------------------------------------------------------------------


async def test_tool_router_by_task_type(db: Database):
    """ToolRouter selects tools based on task type."""
    router = ToolRouter(db)
    tools = await router.select_tools(task_type="implementation")
    assert "read_file" in tools
    assert "write_file" in tools
    assert "run_tests" in tools


async def test_tool_router_by_role(db: Database):
    """ToolRouter selects tools based on role."""
    router = ToolRouter(db)
    tools = await router.select_tools(role="coder")
    assert "write_file" in tools
    assert "git_commit" in tools


async def test_tool_router_combined(db: Database):
    """ToolRouter merges task type and role tools."""
    router = ToolRouter(db)
    tools = await router.select_tools(task_type="code_review", role="reviewer")
    assert "read_file" in tools
    assert "search_code" in tools
    assert "run_tests" in tools


async def test_tool_router_high_complexity(db: Database):
    """High complexity adds extra tools."""
    router = ToolRouter(db)
    tools = await router.select_tools(task_type="documentation", complexity="high")
    assert "git_diff" in tools
    assert "git_log" in tools
    assert "run_tests" in tools


async def test_tool_router_default_fallback(db: Database):
    """Unknown task type and role returns default tools."""
    router = ToolRouter(db)
    tools = await router.select_tools(task_type="unknown", role="unknown")
    assert "read_file" in tools
    assert "write_file" in tools
    assert "search_code" in tools


async def test_tool_router_sync_methods():
    """Sync helper methods work without DB."""
    router = ToolRouter()
    profile = router.get_profile("implementation")
    assert "read_file" in profile
    assert "write_file" in profile

    role_tools = router.get_role_tools("coder")
    assert "write_file" in role_tools
    assert "git_commit" in role_tools

    # Unknown returns empty
    assert router.get_profile("unknown") == []
    assert router.get_role_tools("unknown") == []


async def test_tool_router_custom_db_rules(db: Database):
    """ToolRouter picks up custom rules from model_routing_rules table."""
    now = datetime.now(timezone.utc).isoformat()
    criteria = json.dumps({"extra_tools": ["custom_tool_a", "custom_tool_b"]})
    await db.execute(
        "INSERT INTO model_routing_rules (role, complexity_threshold, model, criteria, active, created_at) "
        "VALUES (?, 'medium', 'gpt-4', ?, 1, ?)",
        ("coder", criteria, now),
    )
    router = ToolRouter(db)
    tools = await router.select_tools(role="coder")
    assert "custom_tool_a" in tools
    assert "custom_tool_b" in tools


async def test_tool_profiles_constants():
    """Verify TOOL_PROFILES and ROLE_TOOLS are well-formed."""
    for task_type, tools in TOOL_PROFILES.items():
        assert isinstance(task_type, str)
        assert isinstance(tools, list)
        assert len(tools) > 0

    for role, tools in ROLE_TOOLS.items():
        assert isinstance(role, str)
        assert isinstance(tools, list)
        assert len(tools) > 0
