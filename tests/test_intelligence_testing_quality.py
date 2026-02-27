"""Tests for the TestingQualityManager."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from taskbrew.orchestrator.database import Database
from taskbrew.intelligence.testing_quality import TestingQualityManager


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture
async def db(tmp_path):
    """Create and initialise a temp database with the tasks table."""
    database = Database(str(tmp_path / "test.db"))
    await database.initialize()
    await database.execute("""CREATE TABLE IF NOT EXISTS tasks (
        id TEXT PRIMARY KEY, title TEXT, description TEXT, task_type TEXT,
        priority TEXT, status TEXT DEFAULT 'pending', assigned_to TEXT,
        claimed_by TEXT, group_id TEXT, parent_id TEXT, created_by TEXT,
        created_at TEXT, started_at TEXT, completed_at TEXT,
        output_text TEXT, rejection_reason TEXT, rejection_count INTEGER DEFAULT 0
    )""")
    yield database
    await database.close()


@pytest.fixture
def project_dir(tmp_path):
    """Create a temporary project directory with sample files."""
    src = tmp_path / "src"
    src.mkdir()
    docs = tmp_path / "docs"
    docs.mkdir()
    return tmp_path


@pytest.fixture
async def tqm(db, project_dir):
    """Create a TestingQualityManager backed by the temp database."""
    return TestingQualityManager(db, project_dir=str(project_dir))


async def _create_task(db: Database, task_type: str = "implementation") -> str:
    """Insert a minimal task row."""
    now = datetime.now(timezone.utc).isoformat()
    task_id = f"TSK-{uuid.uuid4().hex[:6]}"
    await db.execute(
        "INSERT INTO tasks (id, title, task_type, priority, status, created_by, created_at) "
        "VALUES (?, 'Test Task', ?, 'medium', 'pending', 'test', ?)",
        (task_id, task_type, now),
    )
    return task_id


# ------------------------------------------------------------------
# Feature 27: Test Case Generation
# ------------------------------------------------------------------


async def test_generate_test_skeletons_basic(tqm: TestingQualityManager, project_dir):
    """Generate skeletons for functions in a simple Python file."""
    source = project_dir / "src" / "sample.py"
    source.write_text(
        "def add(a, b):\n    return a + b\n\n"
        "def greet(name: str) -> str:\n    return f'Hello {name}'\n"
    )
    results = await tqm.generate_test_skeletons("src/sample.py")
    assert len(results) == 2
    assert results[0]["function_name"] == "add"
    assert "def test_add():" in results[0]["test_skeleton"]
    assert "add(a, b)" in results[0]["test_skeleton"]
    assert results[1]["function_name"] == "greet"
    assert "greet(name)" in results[1]["test_skeleton"]


async def test_generate_test_skeletons_skips_self(tqm: TestingQualityManager, project_dir):
    """Methods with 'self' should have self stripped from args."""
    source = project_dir / "src" / "cls.py"
    source.write_text("class Foo:\n    def bar(self, x):\n        return x\n")
    results = await tqm.generate_test_skeletons("src/cls.py")
    assert len(results) == 1
    assert "self" not in results[0]["test_skeleton"]
    assert "bar(x)" in results[0]["test_skeleton"]


async def test_generate_test_skeletons_persists(tqm: TestingQualityManager, db, project_dir):
    """Generated tests are stored in the database."""
    source = project_dir / "src" / "mod.py"
    source.write_text("def compute(x):\n    return x * 2\n")
    await tqm.generate_test_skeletons("src/mod.py")
    rows = await db.execute_fetchall("SELECT * FROM generated_tests")
    assert len(rows) == 1
    assert rows[0]["function_name"] == "compute"


# ------------------------------------------------------------------
# Feature 28: Mutation Testing Integration
# ------------------------------------------------------------------


async def test_mutation_analysis(tqm: TestingQualityManager, project_dir):
    """Mutation analysis counts operators and computes a score."""
    source = project_dir / "src" / "ops.py"
    source.write_text(
        "def check(a, b):\n"
        "    if a > b and a < 100:\n"
        "        return a + b\n"
        "    return a - b\n"
    )
    result = await tqm.run_mutation_analysis("src/ops.py")
    assert "score" in result
    assert result["mutation_points"] > 0
    assert 0 <= result["score"] <= 1
    details = result["details"]
    assert details["comparison_ops"] >= 2  # >, <
    assert details["arithmetic_ops"] >= 2  # +, -
    assert details["boolean_ops"] >= 1     # and


async def test_get_mutation_scores(tqm: TestingQualityManager, project_dir):
    """get_mutation_scores returns stored results."""
    source = project_dir / "src" / "simple.py"
    source.write_text("x = 1\n")
    await tqm.run_mutation_analysis("src/simple.py")
    scores = await tqm.get_mutation_scores(file_path="src/simple.py")
    assert len(scores) == 1
    all_scores = await tqm.get_mutation_scores()
    assert len(all_scores) >= 1


# ------------------------------------------------------------------
# Feature 29: Property-Based Test Suggestions
# ------------------------------------------------------------------


async def test_suggest_property_tests(tqm: TestingQualityManager, project_dir):
    """Identifies pure functions and suggests property tests."""
    source = project_dir / "src" / "pure.py"
    source.write_text(
        "def double(x):\n    return x * 2\n\n"
        "def impure(x):\n    print(x)\n    return x\n\n"
        "class Foo:\n    def method(self, x):\n        return x\n"
    )
    suggestions = await tqm.suggest_property_tests("src/pure.py")
    func_names = [s["function_name"] for s in suggestions]
    assert "double" in func_names
    assert "impure" not in func_names  # has print (side effect)
    assert "method" not in func_names  # has self


# ------------------------------------------------------------------
# Feature 30: Regression Risk Prediction
# ------------------------------------------------------------------


async def test_predict_regression_risk_low(tqm: TestingQualityManager):
    """Few short files produce low risk."""
    result = await tqm.predict_regression_risk(["src/a.py", "src/b.py"])
    assert result["risk_score"] == 0.0
    assert result["risk_factors"] == []


async def test_predict_regression_risk_many_files(tqm: TestingQualityManager):
    """>5 files changed adds 0.3 to risk."""
    files = [f"src/f{i}.py" for i in range(7)]
    result = await tqm.predict_regression_risk(files, pr_identifier="PR-42")
    assert result["risk_score"] >= 0.3
    assert result["pr_identifier"] == "PR-42"
    assert any(">5" in f for f in result["risk_factors"])


async def test_predict_regression_risk_init_and_test(tqm: TestingQualityManager, project_dir):
    """__init__.py and test files add risk."""
    # Create real files so line count can be read
    (project_dir / "src").mkdir(exist_ok=True)
    init = project_dir / "src" / "__init__.py"
    init.write_text("# init\n")
    test_file = project_dir / "src" / "test_foo.py"
    test_file.write_text("# test\n")

    result = await tqm.predict_regression_risk(["src/__init__.py", "src/test_foo.py"])
    assert result["risk_score"] >= 0.3  # 0.2 for init + 0.1 for test


# ------------------------------------------------------------------
# Feature 31: Review Checklist Generation
# ------------------------------------------------------------------


async def test_generate_checklist_implementation(tqm: TestingQualityManager, db):
    """Implementation tasks produce the correct checklist items."""
    task_id = await _create_task(db, task_type="implementation")
    checklist = await tqm.generate_checklist(task_id)
    assert checklist["task_type"] == "implementation"
    assert "Tests added?" in checklist["checklist_items"]
    assert "Type hints?" in checklist["checklist_items"]


async def test_generate_checklist_bug_fix(tqm: TestingQualityManager, db):
    """Bug fix tasks produce bug-fix-specific checklist items."""
    task_id = await _create_task(db, task_type="bug_fix")
    checklist = await tqm.generate_checklist(task_id)
    assert checklist["task_type"] == "bug_fix"
    assert "Root cause identified?" in checklist["checklist_items"]
    assert "Regression test added?" in checklist["checklist_items"]


async def test_generate_checklist_not_found(tqm: TestingQualityManager):
    """Non-existent task returns an error dict."""
    result = await tqm.generate_checklist("NONEXISTENT")
    assert "error" in result


# ------------------------------------------------------------------
# Feature 32: Documentation Drift Detection
# ------------------------------------------------------------------


async def test_detect_doc_drift_missing_file(tqm: TestingQualityManager, project_dir):
    """Drift is detected when a doc references a file that does not exist."""
    docs = project_dir / "docs"
    docs.mkdir(exist_ok=True)
    md = docs / "guide.md"
    md.write_text('See `src/nonexistent.py` for details.\n')

    drifts = await tqm.detect_doc_drift(doc_dir="docs/", code_dir="src/")
    assert len(drifts) >= 1
    assert drifts[0]["drift_type"] == "missing_file"
    assert "nonexistent.py" in drifts[0]["details"]


async def test_detect_doc_drift_missing_function(tqm: TestingQualityManager, project_dir):
    """Drift is detected when a doc references a function that does not exist."""
    docs = project_dir / "docs"
    docs.mkdir(exist_ok=True)
    md = docs / "api.md"
    md.write_text('Use `some_missing_func()` to do things.\n')

    src = project_dir / "src"
    src.mkdir(exist_ok=True)
    (src / "module.py").write_text("def existing_func():\n    pass\n")

    drifts = await tqm.detect_doc_drift(doc_dir="docs/", code_dir="src/")
    func_drifts = [d for d in drifts if d["drift_type"] == "missing_function"]
    assert len(func_drifts) >= 1
    assert "some_missing_func" in func_drifts[0]["details"]


# ------------------------------------------------------------------
# Feature 33: Performance Regression Detection
# ------------------------------------------------------------------


async def test_record_test_timing_initial(tqm: TestingQualityManager):
    """First timing record creates a baseline."""
    result = await tqm.record_test_timing("test_foo", 100.0)
    assert result["test_name"] == "test_foo"
    assert result["avg_duration_ms"] == 100.0
    assert result["sample_count"] == 1
    assert result["std_deviation_ms"] == 0.0


async def test_record_test_timing_running_avg(tqm: TestingQualityManager):
    """Subsequent timings update the running average."""
    await tqm.record_test_timing("test_bar", 100.0)
    result = await tqm.record_test_timing("test_bar", 200.0)
    assert result["sample_count"] == 2
    assert result["avg_duration_ms"] == 150.0  # (100 + 200) / 2


async def test_detect_perf_regressions(tqm: TestingQualityManager):
    """High variance relative to mean triggers a regression flag."""
    # Record a stable test
    for _ in range(5):
        await tqm.record_test_timing("test_stable", 100.0)

    # Record an unstable test with large jumps
    await tqm.record_test_timing("test_unstable", 100.0)
    await tqm.record_test_timing("test_unstable", 500.0)

    regressions = await tqm.detect_perf_regressions(threshold_pct=20.0)
    names = [r["test_name"] for r in regressions]
    assert "test_unstable" in names
