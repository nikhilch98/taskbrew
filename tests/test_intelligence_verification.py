"""Tests for the VerificationManager."""

from __future__ import annotations


import pytest

from taskbrew.orchestrator.database import Database
from taskbrew.intelligence.verification import VerificationManager


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture
async def manager(tmp_path):
    db = Database(":memory:")
    await db.initialize()
    mgr = VerificationManager(db, project_dir=str(tmp_path))
    await mgr.ensure_tables()
    yield mgr
    await db.close()


# ------------------------------------------------------------------
# Feature 33: Regression Fingerprinter
# ------------------------------------------------------------------


async def test_fingerprint_regression_stores_record(manager):
    """fingerprint_regression persists a regression fingerprint."""
    result = await manager.fingerprint_regression(
        "test_login", "AssertionError: expected 200 got 401", "abc123", "def456"
    )
    assert result["id"].startswith("RF-")
    assert result["test_name"] == "test_login"
    assert result["error_message"] == "AssertionError: expected 200 got 401"
    assert result["failing_commit"] == "abc123"
    assert result["last_passing_commit"] == "def456"


async def test_find_similar_regressions_keyword_match(manager):
    """find_similar_regressions returns fingerprints matching error keywords."""
    await manager.fingerprint_regression(
        "test_auth", "AssertionError: token expired", "aaa111"
    )
    await manager.fingerprint_regression(
        "test_api", "ConnectionError: timeout", "bbb222"
    )

    results = await manager.find_similar_regressions("token expired")
    assert len(results) >= 1
    assert any(r["test_name"] == "test_auth" for r in results)


async def test_get_fingerprints_all(manager):
    """get_fingerprints returns all fingerprints when no filter is given."""
    await manager.fingerprint_regression("test_a", "Error A", "c1")
    await manager.fingerprint_regression("test_b", "Error B", "c2")

    results = await manager.get_fingerprints()
    assert len(results) == 2


async def test_get_fingerprints_by_test_name(manager):
    """get_fingerprints filters by test_name."""
    await manager.fingerprint_regression("test_a", "Error A", "c1")
    await manager.fingerprint_regression("test_b", "Error B", "c2")

    results = await manager.get_fingerprints(test_name="test_a")
    assert len(results) == 1
    assert results[0]["test_name"] == "test_a"


# ------------------------------------------------------------------
# Feature 34: Test Impact Analyzer
# ------------------------------------------------------------------


async def test_record_mapping_and_get_affected_tests(manager):
    """record_mapping stores a mapping; get_affected_tests retrieves it."""
    await manager.record_mapping("test_utils.py", "utils.py", confidence=0.9)

    affected = await manager.get_affected_tests(["utils.py"])
    assert len(affected) == 1
    assert affected[0]["test_file"] == "test_utils.py"
    assert affected[0]["confidence"] == 0.9


async def test_get_affected_tests_empty_changeset(manager):
    """get_affected_tests returns empty list for no changed files."""
    result = await manager.get_affected_tests([])
    assert result == []


async def test_auto_map_naming_convention(manager, tmp_path):
    """auto_map maps test_foo.py to foo.py by naming convention."""
    test_dir = tmp_path / "tests"
    src_dir = tmp_path / "src"
    test_dir.mkdir()
    src_dir.mkdir()

    (src_dir / "utils.py").write_text("def helper(): pass\n")
    (src_dir / "models.py").write_text("class Model: pass\n")
    (test_dir / "test_utils.py").write_text("def test_helper(): pass\n")
    (test_dir / "test_models.py").write_text("def test_model(): pass\n")
    (test_dir / "test_orphan.py").write_text("def test_nothing(): pass\n")

    mappings = await manager.auto_map(str(test_dir), str(src_dir))
    assert len(mappings) == 2
    mapped_sources = {m["source_file"] for m in mappings}
    assert str(src_dir / "utils.py") in mapped_sources
    assert str(src_dir / "models.py") in mapped_sources


async def test_get_mappings_filters(manager):
    """get_mappings filters by source_file and test_file."""
    await manager.record_mapping("test_a.py", "a.py")
    await manager.record_mapping("test_b.py", "b.py")

    by_source = await manager.get_mappings(source_file="a.py")
    assert len(by_source) == 1

    by_test = await manager.get_mappings(test_file="test_b.py")
    assert len(by_test) == 1

    all_mappings = await manager.get_mappings()
    assert len(all_mappings) == 2


# ------------------------------------------------------------------
# Feature 35: Flaky Test Detector
# ------------------------------------------------------------------


async def test_record_run_and_detect_flaky(manager):
    """detect_flaky identifies tests that fail intermittently."""
    # Flaky test: fails 2 out of 10 runs = 20% failure rate
    for i in range(10):
        await manager.record_run("test_flaky", passed=(i >= 2))

    # Stable test: always passes
    for _ in range(10):
        await manager.record_run("test_stable", passed=True)

    flaky = await manager.detect_flaky(min_runs=5, flaky_threshold=0.1)
    assert len(flaky) == 1
    assert flaky[0]["test_name"] == "test_flaky"
    assert flaky[0]["failure_rate"] == 0.2


async def test_get_flaky_tests(manager):
    """get_flaky_tests returns tests with mixed pass/fail results."""
    for i in range(6):
        await manager.record_run("test_sometimes_fails", passed=(i != 0))
    for _ in range(6):
        await manager.record_run("test_always_passes", passed=True)

    flaky = await manager.get_flaky_tests()
    assert len(flaky) == 1
    assert flaky[0]["test_name"] == "test_sometimes_fails"


async def test_quarantine_test(manager):
    """quarantine_test marks test runs as quarantined."""
    for i in range(6):
        await manager.record_run("test_broken", passed=(i % 2 == 0))

    result = await manager.quarantine_test("test_broken", reason="Too flaky")
    assert result["quarantined"] is True
    assert result["reason"] == "Too flaky"

    # Quarantined tests should not appear in flaky detection
    flaky = await manager.detect_flaky(min_runs=2, flaky_threshold=0.1)
    assert not any(f["test_name"] == "test_broken" for f in flaky)


# ------------------------------------------------------------------
# Feature 36: Behavioral Spec Miner
# ------------------------------------------------------------------


async def test_mine_spec_and_get_specs(manager):
    """mine_spec stores a behavioral spec; get_specs retrieves it."""
    result = await manager.mine_spec(
        "test_auth.py", "test_login_success", "Login returns 200 with valid credentials"
    )
    assert result["id"].startswith("BS-")
    assert result["asserted_behavior"] == "Login returns 200 with valid credentials"

    specs = await manager.get_specs()
    assert len(specs) == 1


async def test_detect_undocumented(manager):
    """detect_undocumented returns specs not marked as documented."""
    await manager.mine_spec("test_a.py", "test_a", "Behavior A")
    await manager.mine_spec("test_b.py", "test_b", "Behavior B")

    undoc = await manager.detect_undocumented()
    assert len(undoc) == 2  # Both are undocumented by default


# ------------------------------------------------------------------
# Feature 37: Code Review Auto-Annotator
# ------------------------------------------------------------------


async def test_annotate_and_get_annotations(manager):
    """annotate stores an annotation; get_annotations retrieves it."""
    ann = await manager.annotate(
        "src/app.py", 42, "style", "Missing docstring", severity="warning"
    )
    assert ann["id"].startswith("RA-")
    assert ann["line_number"] == 42
    assert ann["severity"] == "warning"

    annotations = await manager.get_annotations(file_path="src/app.py")
    assert len(annotations) == 1


async def test_auto_annotate_detects_markers(manager, tmp_path):
    """auto_annotate detects TODO/FIXME/HACK comments."""
    code = "def foo():\n    # TODO: fix this\n    # HACK: workaround\n    pass\n"
    code_file = tmp_path / "module.py"
    code_file.write_text(code)

    annotations = await manager.auto_annotate("module.py", content=code)
    types = [a["annotation_type"] for a in annotations]
    assert "comment_marker" in types
    assert len(annotations) >= 2  # TODO and HACK


async def test_auto_annotate_detects_deep_nesting(manager, tmp_path):
    """auto_annotate detects deeply nested code."""
    # 4 levels of nesting = 16 spaces
    code = "def foo():\n    if True:\n        if True:\n            if True:\n                if True:\n                    pass\n"
    annotations = await manager.auto_annotate("deep.py", content=code)
    complexity_anns = [a for a in annotations if a["annotation_type"] == "complexity"]
    assert len(complexity_anns) >= 1


async def test_clear_annotations(manager):
    """clear_annotations removes all annotations for a file."""
    await manager.annotate("src/app.py", 1, "style", "Issue 1")
    await manager.annotate("src/app.py", 2, "style", "Issue 2")
    await manager.annotate("src/other.py", 1, "style", "Other issue")

    result = await manager.clear_annotations("src/app.py")
    assert result["removed"] == 2

    remaining = await manager.get_annotations(file_path="src/app.py")
    assert len(remaining) == 0

    other = await manager.get_annotations(file_path="src/other.py")
    assert len(other) == 1


# ------------------------------------------------------------------
# Feature 38: Quality Gate Composer
# ------------------------------------------------------------------


async def test_define_gate_and_evaluate_pass(manager):
    """define_gate creates a gate; evaluate_gate passes when metrics meet conditions."""
    await manager.define_gate(
        "release-gate",
        {"min_test_coverage": 80, "max_complexity": 10},
        risk_level="high",
    )

    result = await manager.evaluate_gate(
        "release-gate", {"min_test_coverage": 90, "max_complexity": 5}
    )
    assert result["passed"] is True
    assert len(result["details"]) == 2


async def test_evaluate_gate_fails(manager):
    """evaluate_gate returns failure when metrics do not meet conditions."""
    await manager.define_gate(
        "strict-gate",
        {"min_test_coverage": 95, "max_complexity": 5},
    )

    result = await manager.evaluate_gate(
        "strict-gate", {"min_test_coverage": 70, "max_complexity": 12}
    )
    assert result["passed"] is False
    failed = [d for d in result["details"] if not d["passed"]]
    assert len(failed) == 2


async def test_evaluate_gate_missing_metric(manager):
    """evaluate_gate fails for missing metrics."""
    await manager.define_gate("basic-gate", {"min_test_coverage": 80})

    result = await manager.evaluate_gate("basic-gate", {})
    assert result["passed"] is False
    assert result["details"][0]["reason"] == "Metric not provided"


async def test_get_gates_and_history(manager):
    """get_gates lists gates; get_gate_history lists past evaluations."""
    await manager.define_gate("gate-a", {"min_test_coverage": 80})
    await manager.define_gate("gate-b", {"max_complexity": 10})

    gates = await manager.get_gates()
    assert len(gates) == 2

    await manager.evaluate_gate("gate-a", {"min_test_coverage": 90})
    await manager.evaluate_gate("gate-a", {"min_test_coverage": 50})

    history = await manager.get_gate_history(gate_name="gate-a")
    assert len(history) == 2
