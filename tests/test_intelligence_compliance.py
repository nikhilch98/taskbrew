"""Tests for the ComplianceManager (features 49-50)."""

from __future__ import annotations

import pytest

from taskbrew.orchestrator.database import Database
from taskbrew.intelligence.compliance import ComplianceManager


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture
async def manager(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    await db.initialize()
    mgr = ComplianceManager(db, project_dir=str(tmp_path))
    await mgr.ensure_tables()
    yield mgr
    await db.close()


# ------------------------------------------------------------------
# Tests: Feature 49 - Threat Model Generator
# ------------------------------------------------------------------


async def test_create_model(manager: ComplianceManager):
    """create_model stores and returns a threat model."""
    result = await manager.create_model(
        "User Authentication",
        "Login flow with OAuth2",
        data_flows=["browser -> API", "API -> DB"],
    )
    assert result["id"].startswith("TM-")
    assert result["feature_name"] == "User Authentication"
    assert result["data_flows"] == ["browser -> API", "API -> DB"]


async def test_add_threat_valid_stride(manager: ComplianceManager):
    """add_threat accepts valid STRIDE types."""
    model = await manager.create_model("File Upload", "User uploads a document")
    threat = await manager.add_threat(
        model["id"],
        "tampering",
        "Uploaded file could be modified in transit",
        "high",
        mitigation="Use checksums and TLS",
    )
    assert threat["id"].startswith("TH-")
    assert threat["threat_type"] == "tampering"
    assert threat["risk_level"] == "high"
    assert threat["mitigation"] == "Use checksums and TLS"


async def test_add_threat_invalid_type(manager: ComplianceManager):
    """add_threat rejects invalid threat types."""
    model = await manager.create_model("Test", "test model")
    with pytest.raises(ValueError, match="Invalid threat_type"):
        await manager.add_threat(model["id"], "hacking", "bad type", "medium")


async def test_get_model_with_threats(manager: ComplianceManager):
    """get_model returns the model with all associated threats."""
    model = await manager.create_model("Payment Processing", "Stripe integration")
    await manager.add_threat(model["id"], "spoofing", "Fake payment callback", "critical")
    await manager.add_threat(model["id"], "info_disclosure", "Leaking card data in logs", "high")

    full = await manager.get_model(model["id"])
    assert full is not None
    assert full["feature_name"] == "Payment Processing"
    assert len(full["threats"]) == 2
    types = {t["threat_type"] for t in full["threats"]}
    assert types == {"spoofing", "info_disclosure"}


async def test_get_model_nonexistent(manager: ComplianceManager):
    """get_model returns None for unknown model_id."""
    assert await manager.get_model("TM-nonexistent") is None


async def test_assess_risk_scoring(manager: ComplianceManager):
    """assess_risk computes weighted risk scores."""
    model = await manager.create_model("API Gateway", "public-facing API")
    await manager.add_threat(model["id"], "dos", "DDoS attack", "critical")       # 10
    await manager.add_threat(model["id"], "tampering", "Header injection", "high") # 7
    await manager.add_threat(model["id"], "repudiation", "Missing audit log", "low")  # 1

    risk = await manager.assess_risk(model["id"])
    assert risk["total_threats"] == 3
    assert risk["risk_score"] == 10 + 7 + 1
    assert risk["breakdown"]["critical"] == 1
    assert risk["breakdown"]["high"] == 1
    assert risk["breakdown"]["low"] == 1
    assert risk["breakdown"]["medium"] == 0


async def test_get_unmitigated_threats(manager: ComplianceManager):
    """get_unmitigated_threats returns only threats without mitigations."""
    model = await manager.create_model("Session Mgmt", "Cookie-based sessions")
    await manager.add_threat(
        model["id"], "spoofing", "Session hijacking", "critical",
        mitigation="Use HttpOnly + Secure cookies",
    )
    await manager.add_threat(
        model["id"], "elevation_of_privilege", "Privilege escalation via token reuse", "high",
    )

    unmitigated = await manager.get_unmitigated_threats(model["id"])
    assert len(unmitigated) == 1
    assert unmitigated[0]["threat_type"] == "elevation_of_privilege"


async def test_get_unmitigated_threats_all_models(manager: ComplianceManager):
    """get_unmitigated_threats without model_id searches all models."""
    m1 = await manager.create_model("Feature A", "desc a")
    m2 = await manager.create_model("Feature B", "desc b")
    await manager.add_threat(m1["id"], "dos", "Flood", "medium")
    await manager.add_threat(m2["id"], "tampering", "Injection", "high")

    all_unmitigated = await manager.get_unmitigated_threats()
    assert len(all_unmitigated) == 2


# ------------------------------------------------------------------
# Tests: Feature 50 - Compliance Rule Engine
# ------------------------------------------------------------------


async def test_add_rule_and_get_rules(manager: ComplianceManager):
    """add_rule stores rules and get_rules retrieves them."""
    await manager.add_rule(
        "GDPR-001", "GDPR", "data_handling",
        "Personal data must be encrypted",
        r"password\s*=\s*['\"]", "high",
    )
    await manager.add_rule(
        "OWASP-SQL", "OWASP", "injection",
        "No string concatenation in SQL",
        r"execute\(.*\+.*\+", "critical",
    )

    all_rules = await manager.get_rules()
    assert len(all_rules) == 2

    gdpr_rules = await manager.get_rules(framework="GDPR")
    assert len(gdpr_rules) == 1
    assert gdpr_rules[0]["rule_id"] == "GDPR-001"


async def test_add_rule_invalid_framework(manager: ComplianceManager):
    """add_rule rejects unknown frameworks."""
    with pytest.raises(ValueError, match="Invalid framework"):
        await manager.add_rule(
            "BAD-001", "INVALID", "misc", "desc", r".*", "medium"
        )


async def test_add_rule_invalid_regex(manager: ComplianceManager):
    """add_rule rejects invalid regex patterns."""
    with pytest.raises(ValueError, match="Invalid regex"):
        await manager.add_rule(
            "BAD-002", "GDPR", "data", "desc", r"[invalid", "medium"
        )


async def test_check_file_detects_violations(manager: ComplianceManager):
    """check_file finds regex matches and returns violations."""
    await manager.add_rule(
        "GDPR-001", "GDPR", "data_handling",
        "Personal data must be encrypted",
        r"password\s*=\s*['\"]", "high",
    )
    await manager.add_rule(
        "OWASP-SQL", "OWASP", "injection",
        "No string concatenation in SQL",
        r"execute\(.*\+.*\+", "critical",
    )

    code = '''
db_password = "hunter2"
cursor.execute("SELECT * FROM users WHERE id=" + user_id + ";")
safe_query = "SELECT 1"
'''
    violations = await manager.check_file("app.py", code)
    assert len(violations) == 2

    rule_ids = {v["rule_id"] for v in violations}
    assert "GDPR-001" in rule_ids
    assert "OWASP-SQL" in rule_ids

    # Check line numbers are sensible
    for v in violations:
        assert v["line"] > 0
        assert v["file_path"] == "app.py"


async def test_check_file_no_violations(manager: ComplianceManager):
    """check_file returns empty list for clean code."""
    await manager.add_rule(
        "GDPR-001", "GDPR", "data_handling",
        "Personal data must be encrypted",
        r"password\s*=\s*['\"]", "high",
    )
    clean_code = "x = 42\ny = x + 1\n"
    violations = await manager.check_file("clean.py", clean_code)
    assert violations == []


async def test_record_check_and_status(manager: ComplianceManager):
    """record_check and get_compliance_status work end-to-end."""
    await manager.add_rule(
        "SOC2-001", "SOC2", "access_control",
        "No hardcoded secrets",
        r"SECRET_KEY\s*=", "high",
    )

    check = await manager.record_check("config.py", violations_found=2, rules_checked=5)
    assert check["id"].startswith("CC-")
    assert check["violations_found"] == 2

    await manager.record_check("utils.py", violations_found=0, rules_checked=5)

    status = await manager.get_compliance_status()
    assert status["total_rules"] == 1
    assert status["files_checked"] == 2
    assert status["total_violations"] == 2
    assert 0 <= status["compliance_percentage"] <= 100


async def test_get_compliance_status_by_framework(manager: ComplianceManager):
    """get_compliance_status can filter by framework."""
    await manager.add_rule(
        "GDPR-001", "GDPR", "data", "desc", r"pii", "medium"
    )
    await manager.add_rule(
        "HIPAA-001", "HIPAA", "health", "desc", r"phi", "high"
    )

    status = await manager.get_compliance_status(framework="GDPR")
    assert status["total_rules"] == 1


async def test_add_exemption_skips_rule(manager: ComplianceManager):
    """Exempted files are not flagged during check_file."""
    await manager.add_rule(
        "PCI-001", "PCI", "card_data",
        "No card numbers in code",
        r"\d{16}", "critical",
    )

    code_with_card = 'card = "4111111111111111"\n'

    # Without exemption: violation found
    violations = await manager.check_file("payment.py", code_with_card)
    assert len(violations) == 1

    # Add exemption
    ex = await manager.add_exemption(
        "PCI-001", "payment.py", "Test data only", "security-lead"
    )
    assert ex["id"].startswith("EX-")
    assert ex["approved_by"] == "security-lead"

    # With exemption: no violation
    violations_after = await manager.check_file("payment.py", code_with_card)
    assert len(violations_after) == 0


async def test_get_models_list(manager: ComplianceManager):
    """get_models returns all threat models."""
    await manager.create_model("Feature A", "desc A")
    await manager.create_model("Feature B", "desc B")

    models = await manager.get_models()
    assert len(models) == 2
