"""Tests for the SecurityIntelManager."""

from __future__ import annotations

import pytest

from taskbrew.orchestrator.database import Database
from taskbrew.intelligence.security_intel import SecurityIntelManager


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture
async def db():
    database = Database(":memory:")
    await database.initialize()
    yield database
    await database.close()


@pytest.fixture
async def mgr(db: Database, tmp_path) -> SecurityIntelManager:
    mgr = SecurityIntelManager(db, project_dir=str(tmp_path))
    await mgr.ensure_tables()
    return mgr


# ------------------------------------------------------------------
# Feature 35: Secret Detection
# ------------------------------------------------------------------


async def test_scan_for_secrets_detects_api_key(mgr: SecurityIntelManager, tmp_path):
    """Detects API key assignment in a source file."""
    src_file = tmp_path / "config.py"
    src_file.write_text('API_KEY = "sk-abc123def456ghi789jkl012mno345pqr"\n')
    results = await mgr.scan_for_secrets("config.py")
    assert len(results) >= 1
    types = {r["secret_type"] for r in results}
    assert "credential_assignment" in types or "generic_long_secret" in types
    assert results[0]["severity"] == "critical"
    assert results[0]["file_path"] == "config.py"


async def test_scan_for_secrets_detects_aws_key(mgr: SecurityIntelManager, tmp_path):
    """Detects AWS access key pattern."""
    src_file = tmp_path / "aws_config.py"
    src_file.write_text('aws_key = "AKIAIOSFODNN7EXAMPLE"\n')
    results = await mgr.scan_for_secrets("aws_config.py")
    assert len(results) >= 1
    types = {r["secret_type"] for r in results}
    assert "aws_access_key" in types


async def test_scan_for_secrets_detects_private_key(mgr: SecurityIntelManager, tmp_path):
    """Detects private key header pattern."""
    # Write to a .py to be sure it's scannable, or just test via direct path
    src_py = tmp_path / "certs.py"
    src_py.write_text('key = """-----BEGIN RSA PRIVATE KEY-----\nMIIE...\n-----END RSA PRIVATE KEY-----"""\n')
    results = await mgr.scan_for_secrets("certs.py")
    assert len(results) >= 1
    types = {r["secret_type"] for r in results}
    assert "private_key" in types


async def test_scan_for_secrets_no_false_positives(mgr: SecurityIntelManager, tmp_path):
    """Clean file with normal code should return no detections."""
    src_file = tmp_path / "clean.py"
    src_file.write_text("def hello():\n    return 'world'\n\nx = 42\n")
    results = await mgr.scan_for_secrets("clean.py")
    assert results == []


async def test_scan_directory_finds_all(mgr: SecurityIntelManager, tmp_path):
    """scan_directory aggregates findings across multiple files."""
    sub = tmp_path / "src"
    sub.mkdir()
    (sub / "a.py").write_text('API_KEY = "sk-abcdefghijklmnopqrstuvwxyz123456"\n')
    (sub / "b.yaml").write_text('token: "AKIAIOSFODNN7EXAMPLE"\n')
    (sub / "c.py").write_text("x = 1\n")  # clean file

    results = await mgr.scan_directory("src")
    assert len(results) >= 2
    files_found = {r["file_path"] for r in results}
    assert any("a.py" in f for f in files_found)
    assert any("b.yaml" in f for f in files_found)


# ------------------------------------------------------------------
# Feature 36: SAST
# ------------------------------------------------------------------


async def test_run_sast_sql_injection(mgr: SecurityIntelManager, tmp_path):
    """Detects SQL injection via string formatting in execute() call."""
    bad_file = tmp_path / "dao.py"
    bad_file.write_text('cursor.execute(f"SELECT * FROM {table} WHERE id = {uid}")\n')
    results = await mgr.run_sast("dao.py")
    assert len(results) >= 1
    assert results[0]["finding_type"] == "sql_injection"
    assert results[0]["severity"] == "high"
    assert results[0]["code_snippet"] is not None


async def test_run_sast_clean_file(mgr: SecurityIntelManager, tmp_path):
    """No SAST findings for clean code."""
    clean_file = tmp_path / "safe.py"
    clean_file.write_text("def add(a, b):\n    return a + b\n")
    results = await mgr.run_sast("safe.py")
    assert results == []


async def test_get_sast_findings_filters(mgr: SecurityIntelManager, tmp_path):
    """get_sast_findings respects severity and file_path filters."""
    # Create file with SQL injection (high) and command injection (critical)
    vuln_file = tmp_path / "vuln.py"
    vuln_file.write_text(
        'cursor.execute(f"SELECT * FROM {table}")\n'
        'os.system("echo " + user_input)\n'
    )
    await mgr.run_sast("vuln.py")

    # Filter by severity
    high_only = await mgr.get_sast_findings(severity="high")
    assert all(f["severity"] == "high" for f in high_only)

    critical_only = await mgr.get_sast_findings(severity="critical")
    assert all(f["severity"] == "critical" for f in critical_only)

    # Filter by file_path
    by_file = await mgr.get_sast_findings(file_path="vuln.py")
    assert len(by_file) >= 2
    assert all(f["file_path"] == "vuln.py" for f in by_file)

    # Filter by nonexistent file
    empty = await mgr.get_sast_findings(file_path="nonexistent.py")
    assert empty == []


# ------------------------------------------------------------------
# Feature 38: Security-Sensitive Change Detection
# ------------------------------------------------------------------


async def test_flag_security_changes_detects_auth(mgr: SecurityIntelManager, tmp_path):
    """Files touching auth/security patterns are flagged."""
    auth_file = tmp_path / "auth_handler.py"
    auth_file.write_text("def login(): pass\n")
    flags = await mgr.flag_security_changes("TSK-001", ["auth_handler.py"])
    assert len(flags) >= 1
    assert "auth" in flags[0]["flag_reason"].lower()
    assert flags[0]["task_id"] == "TSK-001"
    assert flags[0]["reviewed"] == 0


async def test_flag_security_changes_normal_file(mgr: SecurityIntelManager, tmp_path):
    """Non-security file returns no flags."""
    clean_file = tmp_path / "math_utils.py"
    clean_file.write_text("def add(a, b):\n    return a + b\n")
    flags = await mgr.flag_security_changes("TSK-002", ["math_utils.py"])
    assert flags == []


async def test_get_security_flags_filter_by_task(mgr: SecurityIntelManager, tmp_path):
    """get_security_flags filters by task_id correctly."""
    auth_file = tmp_path / "token_service.py"
    auth_file.write_text("def validate_token(t): pass\n")

    await mgr.flag_security_changes("TSK-010", ["token_service.py"])
    await mgr.flag_security_changes("TSK-020", ["token_service.py"])

    by_task_10 = await mgr.get_security_flags(task_id="TSK-010")
    assert len(by_task_10) >= 1
    assert all(f["task_id"] == "TSK-010" for f in by_task_10)

    by_task_20 = await mgr.get_security_flags(task_id="TSK-020")
    assert len(by_task_20) >= 1
    assert all(f["task_id"] == "TSK-020" for f in by_task_20)

    # Unreviewed filter
    unreviewed = await mgr.get_security_flags(reviewed=False)
    assert all(f["reviewed"] == 0 for f in unreviewed)


# ------------------------------------------------------------------
# Feature 34: Dependency Vulnerability Scanning
# ------------------------------------------------------------------


async def test_scan_dependencies_finds_vulns(mgr: SecurityIntelManager, tmp_path):
    """Finds known vulnerabilities when requirements.txt lists vulnerable packages."""
    req = tmp_path / "requirements.txt"
    req.write_text("requests>=2.0\npyyaml<5.0\nflask\n")
    results = await mgr.scan_dependencies()
    packages_found = {r["package_name"] for r in results}
    assert "requests" in packages_found
    assert "pyyaml" in packages_found
    # flask is not in the vulnerability database
    assert "flask" not in packages_found


async def test_scan_dependencies_no_file(mgr: SecurityIntelManager):
    """Returns empty when no requirements file exists."""
    results = await mgr.scan_dependencies()
    assert results == []


async def test_get_vulnerabilities_severity_filter(mgr: SecurityIntelManager, tmp_path):
    """get_vulnerabilities filters by severity."""
    req = tmp_path / "requirements.txt"
    req.write_text("requests\npyyaml\n")
    await mgr.scan_dependencies()

    high = await mgr.get_vulnerabilities(severity="high")
    assert len(high) >= 1
    assert all(v["severity"] == "high" for v in high)

    critical = await mgr.get_vulnerabilities(severity="critical")
    assert len(critical) >= 1
    assert all(v["severity"] == "critical" for v in critical)

    # All results
    all_vulns = await mgr.get_vulnerabilities()
    assert len(all_vulns) >= 2


# ------------------------------------------------------------------
# Feature 37: License Compliance Checking
# ------------------------------------------------------------------


async def test_check_licenses(mgr: SecurityIntelManager, tmp_path):
    """Check licenses identifies copyleft and permissive licenses."""
    req = tmp_path / "requirements.txt"
    req.write_text("fastapi\nreadline\naiosqlite\n")
    results = await mgr.check_licenses()
    assert len(results) == 3

    by_name = {r["package_name"]: r for r in results}

    # fastapi is MIT (permissive) -> compliant
    assert by_name["fastapi"]["license_category"] == "permissive"
    assert by_name["fastapi"]["compliant"] == 1

    # readline is GPL (copyleft) -> not compliant
    assert by_name["readline"]["license_category"] == "copyleft"
    assert by_name["readline"]["compliant"] == 0

    # get_license_issues should return readline
    issues = await mgr.get_license_issues()
    assert len(issues) >= 1
    issue_names = {i["package_name"] for i in issues}
    assert "readline" in issue_names
