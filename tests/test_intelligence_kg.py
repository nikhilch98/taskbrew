"""Tests for the KnowledgeGraphBuilder."""

from __future__ import annotations

import textwrap

import pytest

from taskbrew.orchestrator.database import Database
from taskbrew.intelligence.knowledge_graph import KnowledgeGraphBuilder


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
async def kg(db: Database) -> KnowledgeGraphBuilder:
    """Create a KnowledgeGraphBuilder backed by the in-memory database."""
    return KnowledgeGraphBuilder(db)


SAMPLE_SOURCE = textwrap.dedent("""\
    import os
    from datetime import datetime

    class Animal:
        \"\"\"Base animal class.\"\"\"

        def speak(self):
            \"\"\"Make a sound.\"\"\"
            pass

    class Dog(Animal):
        \"\"\"A dog.\"\"\"

        def speak(self):
            return "woof"

        async def fetch(self, item: str):
            \"\"\"Fetch an item.\"\"\"
            return item

    def helper():
        \"\"\"A module-level helper.\"\"\"
        return 42
""")


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------


async def test_analyze_file_creates_nodes_and_edges(kg: KnowledgeGraphBuilder):
    """analyze_file should create file, module, class, and function nodes."""
    result = await kg.analyze_file("/fake/sample.py", source_code=SAMPLE_SOURCE)

    assert result["file"] == "/fake/sample.py"
    assert result["nodes"] > 0
    assert result["edges"] > 0


async def test_analyze_file_imports(kg: KnowledgeGraphBuilder, db: Database):
    """Imports should produce module nodes with 'imports' edges."""
    await kg.analyze_file("/fake/sample.py", source_code=SAMPLE_SOURCE)

    deps = await kg.query_dependencies("sample.py")
    dep_names = [d["name"] for d in deps]
    assert "os" in dep_names
    assert "datetime" in dep_names


async def test_analyze_file_classes(kg: KnowledgeGraphBuilder, db: Database):
    """Classes should appear in the module summary."""
    await kg.analyze_file("/fake/sample.py", source_code=SAMPLE_SOURCE)

    summary = await kg.get_module_summary("/fake/sample.py")
    class_names = [c["name"] for c in summary["classes"]]
    assert "Animal" in class_names
    assert "Dog" in class_names


async def test_analyze_file_functions(kg: KnowledgeGraphBuilder, db: Database):
    """Functions (module-level and methods) should appear in summary."""
    await kg.analyze_file("/fake/sample.py", source_code=SAMPLE_SOURCE)

    summary = await kg.get_module_summary("/fake/sample.py")
    func_names = [f["name"] for f in summary["functions"]]
    assert "helper" in func_names
    assert "Animal.speak" in func_names
    assert "Dog.speak" in func_names
    assert "Dog.fetch" in func_names


async def test_analyze_file_inheritance(kg: KnowledgeGraphBuilder, db: Database):
    """Dog inheriting from Animal should create an 'inherits' edge."""
    await kg.analyze_file("/fake/sample.py", source_code=SAMPLE_SOURCE)

    stats = await kg.get_graph_stats()
    assert "inherits" in stats["edges"]
    assert stats["edges"]["inherits"] >= 1


async def test_analyze_file_syntax_error(kg: KnowledgeGraphBuilder):
    """A file with a syntax error should return an error result."""
    bad_source = "def broken(:\n    pass"
    result = await kg.analyze_file("/fake/broken.py", source_code=bad_source)

    assert "error" in result
    assert "Syntax error" in result["error"]
    assert result["nodes"] == 0
    assert result["edges"] == 0


async def test_analyze_file_not_found(kg: KnowledgeGraphBuilder):
    """Analyzing a non-existent file (without source_code) returns error."""
    result = await kg.analyze_file("/does/not/exist.py")

    assert "error" in result
    assert "File not found" in result["error"]
    assert result["nodes"] == 0


async def test_query_dependencies(kg: KnowledgeGraphBuilder):
    """query_dependencies returns modules imported by a file."""
    await kg.analyze_file("/fake/sample.py", source_code=SAMPLE_SOURCE)

    deps = await kg.query_dependencies("sample.py")
    assert len(deps) >= 2
    for dep in deps:
        assert dep["edge_type"] == "imports"
        assert dep["node_type"] == "module"


async def test_query_dependents(kg: KnowledgeGraphBuilder):
    """query_dependents returns files that import a given module."""
    await kg.analyze_file("/fake/sample.py", source_code=SAMPLE_SOURCE)

    dependents = await kg.query_dependents("os")
    assert len(dependents) >= 1
    assert dependents[0]["name"] == "sample.py"


async def test_get_module_summary(kg: KnowledgeGraphBuilder):
    """get_module_summary returns structured info about a file."""
    await kg.analyze_file("/fake/sample.py", source_code=SAMPLE_SOURCE)

    summary = await kg.get_module_summary("/fake/sample.py")
    assert summary["file_path"] == "/fake/sample.py"
    assert len(summary["classes"]) == 2
    assert len(summary["functions"]) >= 4  # Animal.speak, Dog.speak, Dog.fetch, helper
    assert summary["total_symbols"] >= 7  # file + 2 classes + 4 functions


async def test_get_graph_stats(kg: KnowledgeGraphBuilder):
    """get_graph_stats returns node and edge counts grouped by type."""
    await kg.analyze_file("/fake/sample.py", source_code=SAMPLE_SOURCE)

    stats = await kg.get_graph_stats()
    assert "file" in stats["nodes"]
    assert "module" in stats["nodes"]
    assert "class" in stats["nodes"]
    assert "function" in stats["nodes"]
    assert "imports" in stats["edges"]
    assert "contains" in stats["edges"]


async def test_upsert_node_idempotent(kg: KnowledgeGraphBuilder):
    """Analyzing the same file twice should not duplicate nodes."""
    await kg.analyze_file("/fake/sample.py", source_code=SAMPLE_SOURCE)
    stats1 = await kg.get_graph_stats()

    await kg.analyze_file("/fake/sample.py", source_code=SAMPLE_SOURCE)
    stats2 = await kg.get_graph_stats()

    assert stats1["nodes"] == stats2["nodes"]


async def test_graph_stats_empty(kg: KnowledgeGraphBuilder):
    """Empty graph returns empty dicts."""
    stats = await kg.get_graph_stats()
    assert stats["nodes"] == {}
    assert stats["edges"] == {}
