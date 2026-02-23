"""Tests for context providers module."""

from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta

import pytest

from taskbrew.orchestrator.database import Database
from taskbrew.intelligence.context_providers import (
    ContextProviderRegistry,
    CrossTaskProvider,
    DependencyGraphProvider,
    DocumentationProvider,
    IssueTrackerProvider,
    RuntimeContextProvider,
    CoverageContextProvider,
    CICDProvider,
)


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


async def _create_group(db: Database, group_id: str) -> None:
    """Insert a minimal group row."""
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "INSERT INTO groups (id, title, status, created_at) VALUES (?, ?, ?, ?)",
        (group_id, "Test Group", "active", now),
    )


async def _create_task(
    db: Database,
    task_id: str,
    group_id: str = "GRP-001",
    status: str = "pending",
    priority: str = "medium",
    assigned_to: str = "coder",
    title: str = "Test Task",
    rejection_reason: str | None = None,
) -> None:
    """Insert a task row for testing."""
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "INSERT INTO tasks (id, group_id, title, status, priority, assigned_to, "
        "created_at, started_at, completed_at, rejection_reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (task_id, group_id, title, status, priority, assigned_to, now, now, now, rejection_reason),
    )


# ------------------------------------------------------------------
# A simple fake provider for registry tests
# ------------------------------------------------------------------


class FakeProvider:
    """A simple provider for testing the registry."""
    name = "fake"
    ttl_seconds = 60
    call_count = 0

    async def gather(self, scope: str | None = None) -> str:
        self.call_count += 1
        return f"fake context (call {self.call_count})"


class EmptyProvider:
    """A provider that returns empty string."""
    name = "empty"
    ttl_seconds = 60

    async def gather(self, scope: str | None = None) -> str:
        return ""


class FailingProvider:
    """A provider that raises an exception."""
    name = "failing"
    ttl_seconds = 60

    async def gather(self, scope: str | None = None) -> str:
        raise RuntimeError("Provider failure")


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------


async def test_registry_register_and_list(db: Database):
    """Register providers and list available names."""
    registry = ContextProviderRegistry(db)
    fake = FakeProvider()
    empty = EmptyProvider()
    registry.register(fake)
    registry.register(empty)

    available = registry.get_available_providers()
    assert "fake" in available
    assert "empty" in available
    assert len(available) == 2


async def test_get_context_caches_result(db: Database):
    """Verify second call uses cache (provider is only called once)."""
    registry = ContextProviderRegistry(db)
    fake = FakeProvider()
    registry.register(fake)

    # First call: should invoke provider
    result1 = await registry.get_context(["fake"])
    assert "fake context (call 1)" in result1
    assert fake.call_count == 1

    # Second call: should use cache
    result2 = await registry.get_context(["fake"])
    assert "fake context (call 1)" in result2  # same cached data
    assert fake.call_count == 1  # provider NOT called again


async def test_cache_expiry(db: Database):
    """Verify expired cache triggers re-gather."""
    registry = ContextProviderRegistry(db)
    fake = FakeProvider()
    fake.ttl_seconds = 1  # very short TTL
    registry.register(fake)

    # First call
    await registry.get_context(["fake"])
    assert fake.call_count == 1

    # Manually expire the cache by updating the expires_at to the past
    past = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
    await db.execute(
        "UPDATE context_snapshots SET expires_at = ? WHERE context_type = 'fake'",
        (past,),
    )

    # Second call: should re-gather because cache expired
    result2 = await registry.get_context(["fake"])
    assert fake.call_count == 2
    assert "fake context (call 2)" in result2


async def test_cross_task_provider(db: Database):
    """Create in_progress tasks, verify context."""
    await _create_group(db, "GRP-001")
    await _create_task(db, "CD-001", status="in_progress", title="Implement feature X", assigned_to="coder")
    await _create_task(db, "CD-002", status="in_progress", title="Fix bug Y", assigned_to="coder")

    provider = CrossTaskProvider(db)
    result = await provider.gather()

    assert "## Other Active Tasks" in result
    assert "CD-001" in result
    assert "Implement feature X" in result
    assert "CD-002" in result
    assert "Fix bug Y" in result


async def test_cross_task_provider_empty(db: Database):
    """No in-progress tasks returns empty string."""
    provider = CrossTaskProvider(db)
    result = await provider.gather()
    assert result == ""


async def test_issue_tracker_provider(db: Database):
    """Create pending tasks, verify context."""
    await _create_group(db, "GRP-001")
    await _create_task(db, "CD-010", status="pending", priority="high", title="High priority bug")
    await _create_task(db, "CD-011", status="pending", priority="low", title="Low priority enhancement")

    provider = IssueTrackerProvider(db)
    result = await provider.gather()

    assert "## Pending Issues" in result
    assert "CD-010" in result
    assert "High priority bug" in result
    assert "CD-011" in result


async def test_issue_tracker_provider_empty(db: Database):
    """No pending tasks returns empty string."""
    provider = IssueTrackerProvider(db)
    result = await provider.gather()
    assert result == ""


async def test_runtime_context_provider(db: Database):
    """Create failed tasks and escalations, verify context."""
    await _create_group(db, "GRP-001")
    await _create_task(
        db, "CD-020", status="failed", title="Broken deployment",
        rejection_reason="Tests failed on CI",
    )

    # Insert an escalation
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "INSERT INTO escalations (task_id, from_agent, reason, severity, status, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("CD-020", "coder-1", "Cannot resolve merge conflict", "high", "open", now),
    )

    provider = RuntimeContextProvider(db)
    result = await provider.gather()

    assert "## Runtime Context" in result
    assert "Recent failures:" in result
    assert "CD-020" in result
    assert "Tests failed on CI" in result
    assert "Open escalations:" in result
    assert "Cannot resolve merge conflict" in result


async def test_runtime_context_provider_empty(db: Database):
    """No failures or escalations returns empty string."""
    provider = RuntimeContextProvider(db)
    result = await provider.gather()
    assert result == ""


async def test_get_context_unknown_provider(db: Database):
    """Unknown provider name is silently skipped."""
    registry = ContextProviderRegistry(db)
    fake = FakeProvider()
    registry.register(fake)

    result = await registry.get_context(["nonexistent", "fake"])
    assert "fake context" in result
    # No error raised for "nonexistent"


async def test_dependency_graph_provider(tmp_path):
    """Check it reads pyproject.toml."""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        '[project]\n'
        'dependencies = [\n'
        '    "fastapi>=0.115.0",\n'
        '    "aiosqlite>=0.20.0",\n'
        ']\n'
    )

    provider = DependencyGraphProvider(str(tmp_path))
    result = await provider.gather()

    assert "## Project Dependencies" in result
    assert "fastapi>=0.115.0" in result
    assert "aiosqlite>=0.20.0" in result


async def test_dependency_graph_provider_with_package_json(tmp_path):
    """Check it reads package.json."""
    pkg = tmp_path / "package.json"
    pkg.write_text(json.dumps({"dependencies": {"react": "^18.0", "axios": "^1.0"}}))

    provider = DependencyGraphProvider(str(tmp_path))
    result = await provider.gather()

    assert "## Project Dependencies" in result
    assert "react" in result
    assert "axios" in result


async def test_dependency_graph_provider_empty(tmp_path):
    """No pyproject.toml or package.json returns empty string."""
    provider = DependencyGraphProvider(str(tmp_path))
    result = await provider.gather()
    assert result == ""


async def test_documentation_provider_no_docs(tmp_path):
    """Test with no docs returns empty."""
    provider = DocumentationProvider(str(tmp_path))
    result = await provider.gather()
    assert result == ""


async def test_documentation_provider_with_readme(tmp_path):
    """Test it picks up README.md."""
    readme = tmp_path / "README.md"
    readme.write_text("# My Project\nThis is a test project.")

    provider = DocumentationProvider(str(tmp_path))
    result = await provider.gather()

    assert "## Project Documentation" in result
    assert "README.md" in result


async def test_multiple_providers(db: Database):
    """Gather from multiple providers at once."""
    await _create_group(db, "GRP-001")
    await _create_task(db, "CD-030", status="in_progress", title="Active work")
    await _create_task(db, "CD-031", status="pending", title="Queued work", priority="high")

    registry = ContextProviderRegistry(db)
    registry.register(CrossTaskProvider(db))
    registry.register(IssueTrackerProvider(db))

    result = await registry.get_context(["cross_task", "issue_tracker"])

    assert "## Other Active Tasks" in result
    assert "CD-030" in result
    assert "## Pending Issues" in result
    assert "CD-031" in result


async def test_provider_failure_is_swallowed(db: Database):
    """A failing provider does not crash the registry."""
    registry = ContextProviderRegistry(db)
    failing = FailingProvider()
    fake = FakeProvider()
    registry.register(failing)
    registry.register(fake)

    result = await registry.get_context(["failing", "fake"])
    # failing provider's output is skipped, fake still works
    assert "fake context" in result


async def test_empty_provider_not_cached(db: Database):
    """Provider returning empty string is not cached."""
    registry = ContextProviderRegistry(db)
    empty = EmptyProvider()
    registry.register(empty)

    result = await registry.get_context(["empty"])
    assert result == ""

    # Verify nothing was inserted into cache
    row = await db.execute_fetchone(
        "SELECT COUNT(*) as cnt FROM context_snapshots WHERE context_type = 'empty'"
    )
    assert row["cnt"] == 0


async def test_cicd_provider_with_makefile(tmp_path):
    """Test CI/CD provider detects Makefile."""
    makefile = tmp_path / "Makefile"
    makefile.write_text("all:\n\techo hello\n")

    provider = CICDProvider(str(tmp_path))
    result = await provider.gather()

    assert "## CI/CD Configuration" in result
    assert "Makefile present" in result


async def test_cicd_provider_with_github_actions(tmp_path):
    """Test CI/CD provider detects GitHub Actions workflows."""
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "ci.yml").write_text("name: CI\n")
    (workflows / "deploy.yaml").write_text("name: Deploy\n")

    provider = CICDProvider(str(tmp_path))
    result = await provider.gather()

    assert "## CI/CD Configuration" in result
    assert "GitHub Actions workflows:" in result
    assert "ci.yml" in result
    assert "deploy.yaml" in result


async def test_cicd_provider_empty(tmp_path):
    """No CI/CD config returns empty string."""
    provider = CICDProvider(str(tmp_path))
    result = await provider.gather()
    assert result == ""


async def test_test_coverage_provider_no_data(tmp_path):
    """No coverage data returns appropriate message."""
    provider = CoverageContextProvider(str(tmp_path))
    result = await provider.gather()

    assert "## Test Coverage" in result
    assert "No coverage data found" in result


async def test_test_coverage_provider_with_coverage_file(tmp_path):
    """Coverage file is detected."""
    cov = tmp_path / ".coverage"
    cov.write_text("dummy coverage data")

    provider = CoverageContextProvider(str(tmp_path))
    result = await provider.gather()

    assert "## Test Coverage" in result
    assert "Coverage data exists" in result


async def test_cache_with_scope(db: Database):
    """Verify caching respects scope parameter."""
    registry = ContextProviderRegistry(db)
    fake = FakeProvider()
    registry.register(fake)

    # Gather with scope "A"
    await registry.get_context(["fake"], scope="A")
    assert fake.call_count == 1

    # Gather with scope "B" — different scope, should re-gather
    await registry.get_context(["fake"], scope="B")
    assert fake.call_count == 2

    # Gather with scope "A" again — should use cache
    await registry.get_context(["fake"], scope="A")
    assert fake.call_count == 2  # still 2, used cache


# ------------------------------------------------------------------
# Regression: CoverageContextProvider rename (was TestCoverageProvider)
# ------------------------------------------------------------------


async def test_coverage_context_provider_class_name():
    """Verify the class is named CoverageContextProvider, not TestCoverageProvider.

    The old name TestCoverageProvider triggered pytest collection warnings
    because pytest auto-collects classes starting with 'Test'.
    """
    from taskbrew.intelligence.context_providers import CoverageContextProvider

    # Class should exist and be importable
    assert CoverageContextProvider is not None
    assert CoverageContextProvider.name == "test_coverage"

    # Verify the old name is no longer available
    import taskbrew.intelligence.context_providers as cp_module
    assert not hasattr(cp_module, "TestCoverageProvider")


async def test_coverage_context_provider_gather(tmp_path):
    """CoverageContextProvider gathers context correctly after rename."""
    provider = CoverageContextProvider(str(tmp_path))
    result = await provider.gather()

    assert "## Test Coverage" in result
    assert "No coverage data found" in result
