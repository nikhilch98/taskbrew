"""Tests for the CodeReasoningManager (features 17-24)."""

from __future__ import annotations

import pytest

from taskbrew.orchestrator.database import Database
from taskbrew.intelligence.code_reasoning import CodeReasoningManager


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture
async def manager(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    await db.initialize()
    mgr = CodeReasoningManager(db, project_dir=str(tmp_path))
    await mgr.ensure_tables()
    yield mgr
    await db.close()


@pytest.fixture
async def db(tmp_path):
    database = Database(str(tmp_path / "test.db"))
    await database.initialize()
    mgr = CodeReasoningManager(database, project_dir=str(tmp_path))
    await mgr.ensure_tables()
    yield database
    await database.close()


# ------------------------------------------------------------------
# Feature 17: Semantic Code Search
# ------------------------------------------------------------------


async def test_index_intent_and_search(manager: CodeReasoningManager):
    """Index a function intent and retrieve it via search."""
    result = await manager.index_intent(
        "utils.py", "parse_config", "Parse YAML configuration file", ["yaml", "config", "parse"]
    )
    assert result["id"].startswith("SI-")
    assert result["function_name"] == "parse_config"
    assert "yaml" in result["keywords"]

    hits = await manager.search_by_intent("yaml config")
    assert len(hits) >= 1
    assert hits[0]["function_name"] == "parse_config"


async def test_get_index_stats(manager: CodeReasoningManager):
    """get_index_stats groups counts by file."""
    await manager.index_intent("a.py", "fn1", "first function", "util")
    await manager.index_intent("a.py", "fn2", "second function", "helper")
    await manager.index_intent("b.py", "fn3", "third function", "math")

    stats = await manager.get_index_stats()
    assert len(stats) == 2
    # a.py should have 2 entries
    a_stat = next(s for s in stats if s["file_path"] == "a.py")
    assert a_stat["count"] == 2


# ------------------------------------------------------------------
# Feature 18: Dependency Impact Predictor
# ------------------------------------------------------------------


async def test_record_dependency_and_predict_impact(manager: CodeReasoningManager):
    """Record dependencies and predict impact of a file change."""
    await manager.record_dependency("app.py", "utils.py", "import")
    await manager.record_dependency("tests.py", "app.py", "import")

    result = await manager.predict_impact("utils.py")
    assert result["changed_file"] == "utils.py"
    affected_files = [a["file"] for a in result["affected"]]
    assert "app.py" in affected_files
    assert "tests.py" in affected_files
    # app.py is depth 1, tests.py is depth 2
    app_entry = next(a for a in result["affected"] if a["file"] == "app.py")
    tests_entry = next(a for a in result["affected"] if a["file"] == "tests.py")
    assert app_entry["depth"] == 1
    assert tests_entry["depth"] == 2


async def test_get_impact_history(manager: CodeReasoningManager):
    """Impact predictions are persisted and retrievable."""
    await manager.record_dependency("a.py", "b.py")
    await manager.predict_impact("b.py")

    history = await manager.get_impact_history()
    assert len(history) >= 1
    assert history[0]["changed_file"] == "b.py"


# ------------------------------------------------------------------
# Feature 19: Code Style Harmonizer
# ------------------------------------------------------------------


async def test_record_and_get_patterns(manager: CodeReasoningManager):
    """Record a style pattern and retrieve it by category."""
    result = await manager.record_pattern(
        "snake_case_functions", "naming", "snake_case"
    )
    assert result["id"].startswith("SP-")
    assert result["category"] == "naming"

    patterns = await manager.get_patterns(category="naming")
    assert len(patterns) == 1
    assert patterns[0]["pattern_name"] == "snake_case_functions"


async def test_check_conformance_finds_violations(manager: CodeReasoningManager):
    """check_conformance detects naming violations."""
    await manager.record_pattern("use_snake_case", "naming", "snake_case")

    violations = await manager.check_conformance(
        "module.py",
        "def myFunction():\n    someVar = getValue()\n",
    )
    assert len(violations) >= 1
    assert violations[0]["category"] == "naming"
    assert "camelCase" in violations[0]["description"]


# ------------------------------------------------------------------
# Feature 20: Refactoring Opportunity Detector
# ------------------------------------------------------------------


async def test_detect_long_method(manager: CodeReasoningManager):
    """detect_opportunities flags functions longer than 50 lines."""
    lines = ["def big_function():"] + ["    x = 1"] * 55
    content = "\n".join(lines)

    opps = await manager.detect_opportunities("big.py", content)
    long_methods = [o for o in opps if o["opportunity_type"] == "long_method"]
    assert len(long_methods) >= 1
    assert "big_function" in long_methods[0]["description"]


async def test_dismiss_opportunity(manager: CodeReasoningManager):
    """dismiss_opportunity marks it as dismissed."""
    lines = ["def huge():"] + ["    pass"] * 55
    content = "\n".join(lines)
    opps = await manager.detect_opportunities("huge.py", content)
    assert len(opps) >= 1

    result = await manager.dismiss_opportunity(opps[0]["id"], reason="Intentional")
    assert result["dismissed"] is True

    # Should not appear in non-dismissed list
    remaining = await manager.get_opportunities(file_path="huge.py")
    assert all(o["id"] != opps[0]["id"] for o in remaining)


# ------------------------------------------------------------------
# Feature 21: Technical Debt Prioritizer
# ------------------------------------------------------------------


async def test_add_and_prioritize_debt(manager: CodeReasoningManager):
    """Debt items are sorted by impact/effort ratio."""
    # High value: impact 5, effort 1 -> ratio 5.0
    await manager.add_debt("a.py", "complexity", "Complex logic", 1, 5)
    # Low value: impact 1, effort 5 -> ratio 0.2
    await manager.add_debt("b.py", "duplication", "Duplicated code", 5, 1)

    prioritized = await manager.get_prioritized_debt()
    assert len(prioritized) == 2
    assert prioritized[0]["file_path"] == "a.py"  # Higher ratio first
    assert prioritized[0]["priority_ratio"] > prioritized[1]["priority_ratio"]


async def test_resolve_debt(manager: CodeReasoningManager):
    """resolve_debt marks item as resolved and it disappears from active list."""
    item = await manager.add_debt("c.py", "naming", "Poor names", 2, 3)
    result = await manager.resolve_debt(item["id"], resolution_notes="Renamed all variables")
    assert result["resolved"] is True

    active = await manager.get_prioritized_debt()
    assert all(d["id"] != item["id"] for d in active)


# ------------------------------------------------------------------
# Feature 22: API Evolution Tracker
# ------------------------------------------------------------------


async def test_record_api_version_and_detect_breaking(manager: CodeReasoningManager):
    """Record API versions and detect breaking changes."""
    await manager.record_api_version("/users", "GET", "v1", "abc123", breaking_change=False)
    await manager.record_api_version("/users", "GET", "v2", "def456", breaking_change=True)

    breaking = await manager.detect_breaking_changes("/users")
    assert len(breaking) == 1
    assert breaking[0]["version"] == "v2"


async def test_get_api_changelog(manager: CodeReasoningManager):
    """get_api_changelog returns recent version changes."""
    await manager.record_api_version("/items", "POST", "v1", "hash1")
    await manager.record_api_version("/items", "POST", "v2", "hash2")

    changelog = await manager.get_api_changelog()
    assert len(changelog) == 2
    # Most recent first
    assert changelog[0]["version"] == "v2"


# ------------------------------------------------------------------
# Feature 23: Code Narrative Generator
# ------------------------------------------------------------------


async def test_generate_and_get_narrative(manager: CodeReasoningManager):
    """Store and retrieve a code narrative."""
    result = await manager.generate_narrative(
        "auth.py", "login",
        "def login(user, pw): ...",
        "This function authenticates a user by verifying credentials against the database.",
    )
    assert result["id"].startswith("CN-")

    narratives = await manager.get_narrative("auth.py", "login")
    assert len(narratives) == 1
    assert "authenticates" in narratives[0]["narrative_text"]


async def test_search_narratives(manager: CodeReasoningManager):
    """search_narratives finds matching narrative text."""
    await manager.generate_narrative(
        "db.py", "connect", "def connect(): ...", "Establishes a database connection pool."
    )
    await manager.generate_narrative(
        "cache.py", "invalidate", "def invalidate(): ...", "Removes stale cache entries."
    )

    results = await manager.search_narratives("database connection")
    assert len(results) >= 1
    assert results[0]["file_path"] == "db.py"


# ------------------------------------------------------------------
# Feature 24: Invariant Discoverer
# ------------------------------------------------------------------


async def test_record_and_get_invariants(manager: CodeReasoningManager):
    """Record invariants and retrieve them by file."""
    await manager.record_invariant(
        "sort.py", "quicksort", "len(arr) >= 0", "precondition"
    )
    await manager.record_invariant(
        "sort.py", "quicksort", "result is sorted", "postcondition"
    )

    invariants = await manager.get_invariants(file_path="sort.py")
    assert len(invariants) == 2
    types = {inv["invariant_type"] for inv in invariants}
    assert "precondition" in types
    assert "postcondition" in types


async def test_check_invariant_violations(manager: CodeReasoningManager, tmp_path):
    """check_invariant_violations detects missing identifiers."""
    # Create a file that does NOT contain the identifier 'total_count'
    test_file = tmp_path / "module.py"
    test_file.write_text("def compute():\n    result = 42\n    return result\n")

    await manager.record_invariant(
        "module.py", "compute", "total_count > 0", "precondition"
    )

    violations = await manager.check_invariant_violations("module.py")
    assert len(violations) >= 1
    assert "total_count" in violations[0]["reason"]


async def test_record_invariant_invalid_type(manager: CodeReasoningManager):
    """record_invariant rejects invalid invariant types."""
    with pytest.raises(ValueError, match="invariant_type must be one of"):
        await manager.record_invariant(
            "x.py", "fn", "x > 0", "invalid_type"
        )
