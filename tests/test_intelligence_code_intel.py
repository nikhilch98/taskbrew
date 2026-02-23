"""Tests for the CodeIntelligenceManager."""

from __future__ import annotations

import textwrap

import pytest

from taskbrew.orchestrator.database import Database
from taskbrew.intelligence.code_intel import CodeIntelligenceManager


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture
async def db():
    """Create and initialise an in-memory database."""
    database = Database(":memory:")
    await database.initialize()

    # Module-specific tables
    await database.executescript("""
        CREATE TABLE IF NOT EXISTS code_embeddings (
            id TEXT PRIMARY KEY,
            file_path TEXT NOT NULL,
            symbol_name TEXT NOT NULL,
            symbol_type TEXT NOT NULL,
            embedding BLOB,
            description TEXT,
            last_updated TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS architecture_patterns (
            id TEXT PRIMARY KEY,
            pattern_type TEXT NOT NULL,
            file_path TEXT NOT NULL,
            details TEXT,
            severity TEXT DEFAULT 'info',
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS technical_debt (
            id TEXT PRIMARY KEY,
            file_path TEXT NOT NULL,
            debt_type TEXT NOT NULL,
            score REAL NOT NULL,
            details TEXT,
            trend TEXT DEFAULT 'stable',
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS test_gaps (
            id TEXT PRIMARY KEY,
            file_path TEXT NOT NULL,
            function_name TEXT NOT NULL,
            gap_type TEXT NOT NULL,
            suggested_test TEXT,
            created_at TEXT NOT NULL
        );
    """)

    yield database
    await database.close()


@pytest.fixture
async def intel(db: Database) -> CodeIntelligenceManager:
    """Create a CodeIntelligenceManager backed by the in-memory database."""
    return CodeIntelligenceManager(db)


# ------------------------------------------------------------------
# Tests: Feature 6 – Semantic Code Search
# ------------------------------------------------------------------


async def test_index_file(intel: CodeIntelligenceManager, db: Database, tmp_path):
    """index_file extracts functions and classes from a Python file."""
    src = tmp_path / "sample.py"
    src.write_text(textwrap.dedent('''\
        """Sample module."""

        class Calculator:
            """A simple calculator."""

            def add(self, a, b):
                """Add two numbers."""
                return a + b

            def subtract(self, a, b):
                """Subtract b from a."""
                return a - b

        def standalone_helper():
            """A standalone helper function."""
            pass
    '''))

    count = await intel.index_file(str(src))

    # Calculator class + add method + subtract method + standalone_helper
    assert count == 4

    rows = await db.execute_fetchall("SELECT * FROM code_embeddings")
    assert len(rows) == 4
    names = {r["symbol_name"] for r in rows}
    assert "Calculator" in names
    assert "Calculator.add" in names
    assert "Calculator.subtract" in names
    assert "standalone_helper" in names


async def test_index_file_invalid(intel: CodeIntelligenceManager, tmp_path):
    """index_file returns 0 for files with syntax errors."""
    bad = tmp_path / "broken.py"
    bad.write_text("def broken(:\n    pass\n")

    count = await intel.index_file(str(bad))
    assert count == 0


async def test_search_by_intent(intel: CodeIntelligenceManager, tmp_path):
    """search_by_intent finds symbols by keyword."""
    src = tmp_path / "math_ops.py"
    src.write_text(textwrap.dedent('''\
        def calculate_average(values):
            """Calculate the average of a list of numbers."""
            return sum(values) / len(values)

        def find_maximum(values):
            """Find the maximum value in a list."""
            return max(values)
    '''))

    await intel.index_file(str(src))

    results = await intel.search_by_intent("calculate average")
    assert len(results) >= 1
    assert any("calculate_average" in r["symbol_name"] for r in results)


# ------------------------------------------------------------------
# Tests: Feature 7 – Architecture Pattern Detection
# ------------------------------------------------------------------


async def test_detect_patterns_singleton(
    intel: CodeIntelligenceManager, db: Database, tmp_path
):
    """detect_patterns identifies the Singleton pattern."""
    src = tmp_path / "singleton.py"
    src.write_text(textwrap.dedent('''\
        class DatabaseConnection:
            _instance = None

            def __new__(cls):
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                return cls._instance

            def connect(self):
                pass
    '''))

    patterns = await intel.detect_patterns(str(src))

    types = [p["pattern_type"] for p in patterns]
    assert "singleton" in types

    # Verify persisted
    rows = await db.execute_fetchall("SELECT * FROM architecture_patterns")
    assert len(rows) >= 1


async def test_detect_patterns_observer(
    intel: CodeIntelligenceManager, tmp_path
):
    """detect_patterns identifies the Observer pattern."""
    src = tmp_path / "events.py"
    src.write_text(textwrap.dedent('''\
        class EventBus:
            def __init__(self):
                self._listeners = []

            def subscribe(self, listener):
                self._listeners.append(listener)

            def notify(self, event):
                for listener in self._listeners:
                    listener(event)
    '''))

    patterns = await intel.detect_patterns(str(src))

    types = [p["pattern_type"] for p in patterns]
    assert "observer" in types


async def test_get_patterns(intel: CodeIntelligenceManager, db: Database, tmp_path):
    """get_patterns retrieves stored patterns filtered by type."""
    src = tmp_path / "mixed.py"
    src.write_text(textwrap.dedent('''\
        class Singleton:
            _instance = None

        class Bus:
            def subscribe(self, x): pass
            def notify(self, x): pass
    '''))

    await intel.detect_patterns(str(src))

    all_patterns = await intel.get_patterns()
    assert len(all_patterns) >= 2

    singletons = await intel.get_patterns(pattern_type="singleton")
    assert all(p["pattern_type"] == "singleton" for p in singletons)


# ------------------------------------------------------------------
# Tests: Feature 8 – Code Smell Detection
# ------------------------------------------------------------------


async def test_detect_smells_long_method(intel: CodeIntelligenceManager, tmp_path):
    """detect_smells flags functions longer than 50 lines."""
    lines = ["def very_long_function():"]
    lines += [f"    x_{i} = {i}" for i in range(55)]
    lines.append("    return x_0")

    src = tmp_path / "long.py"
    src.write_text("\n".join(lines) + "\n")

    smells = await intel.detect_smells(str(src))

    long_smells = [s for s in smells if s["type"] == "long_method"]
    assert len(long_smells) >= 1
    assert "very_long_function" in long_smells[0]["detail"]


async def test_detect_smells_god_class(intel: CodeIntelligenceManager, tmp_path):
    """detect_smells flags classes with more than 10 methods."""
    methods = "\n".join(
        f"    def method_{i}(self): pass" for i in range(12)
    )
    src = tmp_path / "god.py"
    src.write_text(f"class GodClass:\n{methods}\n")

    smells = await intel.detect_smells(str(src))

    god_smells = [s for s in smells if s["type"] == "god_class"]
    assert len(god_smells) == 1
    assert "12 methods" in god_smells[0]["detail"]


async def test_detect_smells_too_many_params(intel: CodeIntelligenceManager, tmp_path):
    """detect_smells flags functions with more than 5 parameters."""
    src = tmp_path / "params.py"
    src.write_text("def many_params(a, b, c, d, e, f, g): pass\n")

    smells = await intel.detect_smells(str(src))

    param_smells = [s for s in smells if s["type"] == "too_many_parameters"]
    assert len(param_smells) >= 1
    assert "7 parameters" in param_smells[0]["detail"]


# ------------------------------------------------------------------
# Tests: Feature 9 – Technical Debt Scoring
# ------------------------------------------------------------------


async def test_score_debt(intel: CodeIntelligenceManager, db: Database, tmp_path):
    """score_debt returns a score between 0 and 1 with details."""
    src = tmp_path / "complex.py"
    src.write_text(textwrap.dedent('''\
        def process(data):
            if data:
                for item in data:
                    if item > 0:
                        try:
                            return item
                        except Exception:
                            pass
            return None

        def simple():
            return 42
    '''))

    result = await intel.score_debt(str(src))

    assert result["file_path"] == str(src)
    assert 0 <= result["score"] <= 1
    assert "cyclomatic_complexity" in result["details"]
    assert result["details"]["function_count"] == 2

    # Verify persisted
    rows = await db.execute_fetchall("SELECT * FROM technical_debt")
    assert len(rows) == 1


async def test_get_debt_report(intel: CodeIntelligenceManager, tmp_path):
    """get_debt_report returns debt records ordered by score."""
    # Simple file = low debt
    simple = tmp_path / "simple.py"
    simple.write_text("def hello(): return 1\n")
    await intel.score_debt(str(simple))

    # Complex file = higher debt
    lines = ["def complex_func():"]
    lines += [f"    if True: x_{i} = {i}" for i in range(30)]
    lines.append("    return x_0")
    complex_f = tmp_path / "complex.py"
    complex_f.write_text("\n".join(lines) + "\n")
    await intel.score_debt(str(complex_f))

    report = await intel.get_debt_report()
    assert len(report) == 2
    # Highest score first
    assert report[0]["score"] >= report[1]["score"]
    # Details should be parsed as dict
    assert isinstance(report[0]["details"], dict)


# ------------------------------------------------------------------
# Tests: Feature 10 – Test Gap Analysis
# ------------------------------------------------------------------


async def test_analyze_test_gaps(intel: CodeIntelligenceManager, tmp_path):
    """analyze_test_gaps finds untested public functions."""
    # Source file with two public functions
    src = tmp_path / "utils.py"
    src.write_text(textwrap.dedent('''\
        def compute():
            pass

        def transform():
            pass

        def _private():
            pass
    '''))

    # Test file that only tests 'compute'
    test_file = tmp_path / "test_utils.py"
    test_file.write_text(textwrap.dedent('''\
        def test_compute():
            pass
    '''))

    gaps = await intel.analyze_test_gaps(str(src))

    gap_names = [g["function_name"] for g in gaps]
    assert "transform" in gap_names
    # Private functions should not appear
    assert "_private" not in gap_names
    # compute is tested, should not appear
    assert "compute" not in gap_names

    assert gaps[0]["gap_type"] == "untested_function"
    assert gaps[0]["suggested_test"] == "test_transform"


# ------------------------------------------------------------------
# Tests: Feature 11 – API Contract Validation
# ------------------------------------------------------------------


async def test_validate_contracts(intel: CodeIntelligenceManager, tmp_path):
    """validate_contracts reports missing type hints and raw dict params."""
    router = tmp_path / "router.py"
    router.write_text(textwrap.dedent('''\
        from fastapi import APIRouter

        router = APIRouter()

        @router.get("/items")
        def get_items(skip, limit: int = 10):
            return []

        @router.post("/items")
        def create_item(data: dict):
            return data
    '''))

    issues = await intel.validate_contracts(str(router))

    issue_types = {(i["function"], i["issue"]) for i in issues}

    # get_items: 'skip' has no type hint
    assert ("get_items", "missing_type_hint") in issue_types
    # create_item: 'data' is raw dict
    assert ("create_item", "raw_dict_param") in issue_types


async def test_validate_contracts_clean(intel: CodeIntelligenceManager, tmp_path):
    """validate_contracts returns no param issues for well-typed routes."""
    router = tmp_path / "clean_router.py"
    router.write_text(textwrap.dedent('''\
        from fastapi import APIRouter

        router = APIRouter()

        @router.get("/health")
        def health() -> dict:
            return {"status": "ok"}
    '''))

    issues = await intel.validate_contracts(str(router))

    # No param issues (no params besides the decorator check)
    param_issues = [i for i in issues if i["issue"] in ("missing_type_hint", "raw_dict_param")]
    assert len(param_issues) == 0


# ------------------------------------------------------------------
# Tests: Feature 12 – Dead Code Detection
# ------------------------------------------------------------------


async def test_detect_dead_code(intel: CodeIntelligenceManager, tmp_path):
    """detect_dead_code finds functions never called from other code."""
    src_dir = tmp_path / "src"
    src_dir.mkdir()

    # File A: defines used_func and dead_func
    (src_dir / "module_a.py").write_text(textwrap.dedent('''\
        def used_func():
            return 42

        def dead_func():
            return "never called"
    '''))

    # File B: calls used_func but not dead_func
    (src_dir / "module_b.py").write_text(textwrap.dedent('''\
        from module_a import used_func

        def main():
            return used_func()
    '''))

    dead = await intel.detect_dead_code(str(src_dir))

    dead_names = [d["function_name"] for d in dead]
    assert "dead_func" in dead_names
    # used_func is called in module_b, should not be listed
    assert "used_func" not in dead_names


async def test_detect_dead_code_ignores_decorated(intel: CodeIntelligenceManager, tmp_path):
    """detect_dead_code skips decorated functions (e.g. @app.get)."""
    src_dir = tmp_path / "src"
    src_dir.mkdir()

    (src_dir / "routes.py").write_text(textwrap.dedent('''\
        from fastapi import FastAPI
        app = FastAPI()

        @app.get("/")
        def index():
            return {"hello": "world"}

        def truly_dead():
            pass
    '''))

    dead = await intel.detect_dead_code(str(src_dir))

    dead_names = [d["function_name"] for d in dead]
    # Decorated function should be excluded
    assert "index" not in dead_names
    # Undecorated, uncalled function should be found
    assert "truly_dead" in dead_names
