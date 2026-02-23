"""Tests for the MemoryManager."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from taskbrew.orchestrator.database import Database
from taskbrew.intelligence.memory import MemoryManager


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


@pytest.fixture
async def memory(db: Database) -> MemoryManager:
    """Create a MemoryManager backed by the in-memory database."""
    return MemoryManager(db)


async def _create_dummy_task(db: Database, task_id: str) -> None:
    """Insert a minimal task row so foreign key constraints are satisfied."""
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "INSERT INTO groups (id, title, status, created_at) VALUES (?, ?, ?, ?)",
        (f"GRP-{task_id}", "Test Group", "active", now),
    )
    await db.execute(
        "INSERT INTO tasks (id, group_id, title, status, created_at) VALUES (?, ?, ?, ?, ?)",
        (task_id, f"GRP-{task_id}", "Test Task", "pending", now),
    )


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------


async def test_store_and_recall_lesson(memory: MemoryManager):
    """Store a lesson, recall it by keyword."""
    await memory.store_lesson(
        role="coder",
        title="Always validate input parameters",
        content="When handling API endpoints, always validate input params before processing.",
        tags=["validation", "api"],
    )

    results = await memory.recall("coder", "validate input")
    assert len(results) >= 1
    assert results[0]["title"] == "Always validate input parameters"
    assert results[0]["memory_type"] == "lesson"


async def test_recall_no_results(memory: MemoryManager):
    """Recall with no matching query returns empty."""
    results = await memory.recall("coder", "nonexistent topic xyz")
    assert results == []


async def test_store_pattern_and_find(memory: MemoryManager):
    """Store pattern with tags, find by tags."""
    await memory.store_pattern(
        role="coder",
        title="Retry with exponential backoff",
        content="Use exponential backoff for transient failures: delay = base * (2 ** attempt)",
        tags=["retry", "resilience"],
    )

    results = await memory.find_patterns("coder", tags=["retry"])
    assert len(results) >= 1
    assert "backoff" in results[0]["content"].lower()

    # Also test find_patterns without tags
    all_patterns = await memory.find_patterns("coder")
    assert len(all_patterns) >= 1


async def test_store_postmortem(memory: MemoryManager, db: Database):
    """Store a post-mortem, retrieve similar failures."""
    await _create_dummy_task(db, "CD-042")
    await memory.store_postmortem(
        task_id="CD-042",
        role="coder",
        analysis="Timeout occurred during API call to external service",
        root_cause="No timeout configured on HTTP client",
        prevention="Always set timeouts on external HTTP calls",
    )

    results = await memory.get_similar_failures("coder", "timeout API call")
    assert len(results) >= 1
    assert results[0]["memory_type"] == "failure"
    content = json.loads(results[0]["content"])
    assert "timeout" in content["root_cause"].lower()


async def test_style_rules(memory: MemoryManager):
    """Store style rules, retrieve by file extension."""
    await memory.store_style_rule(
        role="coder",
        rule="Use 4-space indentation for Python files",
        source_file="main.py",
    )
    await memory.store_style_rule(
        role="coder",
        rule="Use 2-space indentation for JavaScript files",
        source_file="app.js",
    )

    py_rules = await memory.get_style_guide("coder", file_extension="py")
    assert len(py_rules) >= 1
    assert "Python" in py_rules[0]["content"]

    js_rules = await memory.get_style_guide("coder", file_extension="js")
    assert len(js_rules) >= 1
    assert "JavaScript" in js_rules[0]["content"]

    # Get all style rules without filter
    all_rules = await memory.get_style_guide("coder")
    assert len(all_rules) >= 2


async def test_project_knowledge(memory: MemoryManager):
    """Store project knowledge, get context string."""
    await memory.add_project_knowledge(
        role="architect",
        title="Database uses SQLite",
        content="The project uses SQLite with aiosqlite for async access. WAL mode is enabled.",
        tags=["database", "sqlite"],
        project_id="proj-1",
    )

    context = await memory.get_project_context("architect", "database access", project_id="proj-1")
    assert "## Relevant Knowledge" in context
    assert "SQLite" in context

    # Empty context for non-matching query
    empty_context = await memory.get_project_context("architect", "xyz nonexistent")
    assert empty_context == ""


async def test_access_count_increments(memory: MemoryManager, db: Database):
    """Verify recall increments access_count."""
    await memory.store_lesson(
        role="reviewer",
        title="Check for SQL injection",
        content="Always use parameterized queries to prevent SQL injection.",
        tags=["security"],
    )

    # First recall
    results = await memory.recall("reviewer", "SQL injection")
    assert len(results) >= 1
    mem_id = results[0]["id"]

    # Check access_count after first recall
    row = await db.execute_fetchone(
        "SELECT access_count FROM agent_memories WHERE id = ?", (mem_id,)
    )
    assert row["access_count"] == 1

    # Second recall
    await memory.recall("reviewer", "SQL injection")

    row = await db.execute_fetchone(
        "SELECT access_count FROM agent_memories WHERE id = ?", (mem_id,)
    )
    assert row["access_count"] == 2


async def test_get_memories_filtered(memory: MemoryManager):
    """List with role and type filters."""
    await memory.store_lesson(
        role="coder", title="Lesson 1", content="Content 1"
    )
    await memory.store_pattern(
        role="coder", title="Pattern 1", content="Content 2"
    )
    await memory.store_lesson(
        role="reviewer", title="Lesson 2", content="Content 3"
    )

    # Filter by role
    coder_memories = await memory.get_memories(agent_role="coder")
    assert len(coder_memories) == 2

    # Filter by type
    lessons = await memory.get_memories(memory_type="lesson")
    assert len(lessons) == 2

    # Filter by both
    coder_lessons = await memory.get_memories(agent_role="coder", memory_type="lesson")
    assert len(coder_lessons) == 1
    assert coder_lessons[0]["title"] == "Lesson 1"

    # No filter returns all
    all_memories = await memory.get_memories()
    assert len(all_memories) == 3


async def test_delete_memory(memory: MemoryManager):
    """Delete a memory by ID."""
    await memory.store_lesson(
        role="coder", title="Temporary lesson", content="Will be deleted"
    )

    memories = await memory.get_memories(agent_role="coder")
    assert len(memories) == 1
    mem_id = memories[0]["id"]

    await memory.delete_memory(mem_id)

    memories = await memory.get_memories(agent_role="coder")
    assert len(memories) == 0


async def test_recall_batch_updates_access_count(memory: MemoryManager, db: Database):
    """Regression: recall() batch-updates access_count for all returned memories.

    Previously used N+1 individual UPDATEs; now uses a single batch UPDATE.
    This test verifies multiple memories all get their access_count incremented.
    """
    await memory.store_lesson(role="coder", title="Lesson about validation", content="Always validate input params")
    await memory.store_lesson(role="coder", title="Lesson about validation errors", content="Return clear validation error messages")

    results = await memory.recall("coder", "validation")
    assert len(results) == 2

    # Both memories should have access_count = 1
    for mem in results:
        row = await db.execute_fetchone(
            "SELECT access_count FROM agent_memories WHERE id = ?", (mem["id"],)
        )
        assert row["access_count"] == 1

    # Recall again
    await memory.recall("coder", "validation")

    # Both memories should now have access_count = 2
    for mem in results:
        row = await db.execute_fetchone(
            "SELECT access_count FROM agent_memories WHERE id = ?", (mem["id"],)
        )
        assert row["access_count"] == 2


async def test_recall_empty_does_not_error(memory: MemoryManager):
    """Regression: recall() with no results should not error on batch update."""
    results = await memory.recall("coder", "nonexistent xyz abc")
    assert results == []
