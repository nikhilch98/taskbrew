"""Compliance management: threat modelling (STRIDE) and compliance rule engine."""

from __future__ import annotations

import json
import logging
import re

from taskbrew.intelligence._utils import utcnow, new_id

logger = logging.getLogger(__name__)

# Valid STRIDE threat types
_STRIDE_TYPES = frozenset({
    "spoofing",
    "tampering",
    "repudiation",
    "info_disclosure",
    "dos",
    "elevation_of_privilege",
})

# Recognised compliance frameworks
_FRAMEWORKS = frozenset({"GDPR", "SOC2", "HIPAA", "PCI", "OWASP"})

# Risk-level weights for scoring
_RISK_WEIGHTS = {
    "critical": 10,
    "high": 7,
    "medium": 4,
    "low": 1,
}


class ComplianceManager:
    """Manage threat models and compliance rule checks."""

    def __init__(self, db, project_dir: str = ".") -> None:
        self._db = db
        self._project_dir = project_dir

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    async def ensure_tables(self) -> None:
        await self._db.executescript("""
            CREATE TABLE IF NOT EXISTS threat_models (
                id TEXT PRIMARY KEY,
                feature_name TEXT NOT NULL,
                description TEXT,
                data_flows TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS threat_entries (
                id TEXT PRIMARY KEY,
                model_id TEXT NOT NULL REFERENCES threat_models(id),
                threat_type TEXT NOT NULL,
                description TEXT NOT NULL,
                risk_level TEXT NOT NULL DEFAULT 'medium',
                mitigation TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS compliance_rules (
                id TEXT PRIMARY KEY,
                rule_id TEXT NOT NULL UNIQUE,
                framework TEXT NOT NULL,
                category TEXT NOT NULL,
                description TEXT NOT NULL,
                check_pattern TEXT NOT NULL,
                severity TEXT NOT NULL DEFAULT 'medium',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS compliance_checks (
                id TEXT PRIMARY KEY,
                file_path TEXT NOT NULL,
                violations_found INTEGER NOT NULL DEFAULT 0,
                rules_checked INTEGER NOT NULL DEFAULT 0,
                details TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS compliance_exemptions (
                id TEXT PRIMARY KEY,
                rule_id TEXT NOT NULL,
                file_path TEXT NOT NULL,
                reason TEXT NOT NULL,
                approved_by TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(rule_id, file_path)
            );
        """)

    # ------------------------------------------------------------------
    # Feature 49: Threat Model Generator
    # ------------------------------------------------------------------

    async def create_model(
        self,
        feature_name: str,
        description: str,
        data_flows: list[str] | None = None,
    ) -> dict:
        """Create a new threat model for a feature."""
        now = utcnow()
        model_id = f"TM-{new_id(8)}"
        flows_json = json.dumps(data_flows) if data_flows else None

        await self._db.execute(
            "INSERT INTO threat_models (id, feature_name, description, data_flows, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (model_id, feature_name, description, flows_json, now),
        )
        return {
            "id": model_id,
            "feature_name": feature_name,
            "description": description,
            "data_flows": data_flows,
            "created_at": now,
        }

    async def add_threat(
        self,
        model_id: str,
        threat_type: str,
        description: str,
        risk_level: str,
        mitigation: str | None = None,
    ) -> dict:
        """Add a threat entry to a model.

        *threat_type* must be one of the STRIDE categories: spoofing,
        tampering, repudiation, info_disclosure, dos, elevation_of_privilege.
        """
        if threat_type not in _STRIDE_TYPES:
            raise ValueError(
                f"Invalid threat_type {threat_type!r}; must be one of {sorted(_STRIDE_TYPES)}"
            )
        if risk_level not in _RISK_WEIGHTS:
            raise ValueError(
                f"Invalid risk_level {risk_level!r}; must be one of {sorted(_RISK_WEIGHTS)}"
            )

        now = utcnow()
        threat_id = f"TH-{new_id(8)}"
        await self._db.execute(
            "INSERT INTO threat_entries "
            "(id, model_id, threat_type, description, risk_level, mitigation, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (threat_id, model_id, threat_type, description, risk_level, mitigation, now),
        )
        return {
            "id": threat_id,
            "model_id": model_id,
            "threat_type": threat_type,
            "description": description,
            "risk_level": risk_level,
            "mitigation": mitigation,
            "created_at": now,
        }

    async def get_model(self, model_id: str) -> dict | None:
        """Return a threat model with all its threats."""
        model = await self._db.execute_fetchone(
            "SELECT * FROM threat_models WHERE id = ?", (model_id,)
        )
        if not model:
            return None

        threats = await self._db.execute_fetchall(
            "SELECT * FROM threat_entries WHERE model_id = ? ORDER BY created_at",
            (model_id,),
        )
        result = dict(model)
        if result.get("data_flows"):
            try:
                result["data_flows"] = json.loads(result["data_flows"])
            except (json.JSONDecodeError, TypeError):
                pass
        result["threats"] = [dict(t) for t in threats]
        return result

    async def get_models(self, limit: int = 20) -> list[dict]:
        """List threat models."""
        return await self._db.execute_fetchall(
            "SELECT * FROM threat_models ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )

    async def assess_risk(self, model_id: str) -> dict:
        """Compute an overall risk score for a threat model.

        The score is a weighted sum where each threat contributes its
        ``_RISK_WEIGHTS[risk_level]`` value.  The result also includes
        a breakdown by severity.
        """
        threats = await self._db.execute_fetchall(
            "SELECT * FROM threat_entries WHERE model_id = ?", (model_id,)
        )
        breakdown: dict[str, int] = {level: 0 for level in _RISK_WEIGHTS}
        total_score = 0

        for t in threats:
            level = t["risk_level"]
            breakdown[level] = breakdown.get(level, 0) + 1
            total_score += _RISK_WEIGHTS.get(level, 0)

        return {
            "model_id": model_id,
            "total_threats": len(threats),
            "risk_score": total_score,
            "breakdown": breakdown,
        }

    async def get_unmitigated_threats(
        self, model_id: str | None = None
    ) -> list[dict]:
        """Return threats that lack mitigations."""
        if model_id:
            return await self._db.execute_fetchall(
                "SELECT * FROM threat_entries "
                "WHERE model_id = ? AND (mitigation IS NULL OR mitigation = '') "
                "ORDER BY created_at",
                (model_id,),
            )
        return await self._db.execute_fetchall(
            "SELECT * FROM threat_entries "
            "WHERE mitigation IS NULL OR mitigation = '' "
            "ORDER BY created_at",
        )

    # ------------------------------------------------------------------
    # Feature 50: Compliance Rule Engine
    # ------------------------------------------------------------------

    async def add_rule(
        self,
        rule_id: str,
        framework: str,
        category: str,
        description: str,
        check_pattern: str,
        severity: str = "medium",
    ) -> dict:
        """Add a compliance rule with a regex check pattern."""
        if framework not in _FRAMEWORKS:
            raise ValueError(
                f"Invalid framework {framework!r}; must be one of {sorted(_FRAMEWORKS)}"
            )
        if severity not in _RISK_WEIGHTS:
            raise ValueError(
                f"Invalid severity {severity!r}; must be one of {sorted(_RISK_WEIGHTS)}"
            )
        # Validate regex
        try:
            re.compile(check_pattern)
        except re.error as exc:
            raise ValueError(f"Invalid regex pattern: {exc}") from exc

        now = utcnow()
        row_id = f"CR-{new_id(8)}"
        await self._db.execute(
            "INSERT INTO compliance_rules "
            "(id, rule_id, framework, category, description, check_pattern, severity, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (row_id, rule_id, framework, category, description, check_pattern, severity, now),
        )
        return {
            "id": row_id,
            "rule_id": rule_id,
            "framework": framework,
            "category": category,
            "description": description,
            "check_pattern": check_pattern,
            "severity": severity,
            "created_at": now,
        }

    async def get_rules(
        self, framework: str | None = None, category: str | None = None
    ) -> list[dict]:
        """List compliance rules, optionally filtered."""
        query = "SELECT * FROM compliance_rules WHERE 1=1"
        params: list = []
        if framework:
            query += " AND framework = ?"
            params.append(framework)
        if category:
            query += " AND category = ?"
            params.append(category)
        query += " ORDER BY framework, rule_id"
        return await self._db.execute_fetchall(query, tuple(params))

    async def check_file(self, file_path: str, content: str) -> list[dict]:
        """Scan *content* against all compliance rules.

        Returns a list of violation dicts.  Rules with an active exemption
        for *file_path* are skipped.
        """
        rules = await self._db.execute_fetchall(
            "SELECT * FROM compliance_rules ORDER BY rule_id"
        )
        violations: list[dict] = []

        for rule in rules:
            # Check exemption
            exemption = await self._db.execute_fetchone(
                "SELECT id FROM compliance_exemptions "
                "WHERE rule_id = ? AND file_path = ?",
                (rule["rule_id"], file_path),
            )
            if exemption:
                continue

            try:
                pattern = re.compile(rule["check_pattern"])
            except re.error:
                continue

            matches = list(pattern.finditer(content))
            if matches:
                for m in matches:
                    line_num = content[: m.start()].count("\n") + 1
                    violations.append({
                        "rule_id": rule["rule_id"],
                        "framework": rule["framework"],
                        "severity": rule["severity"],
                        "description": rule["description"],
                        "file_path": file_path,
                        "line": line_num,
                        "match": m.group(0)[:200],
                    })

        return violations

    async def record_check(
        self,
        file_path: str,
        violations_found: int,
        rules_checked: int,
    ) -> dict:
        """Record a compliance check run."""
        now = utcnow()
        check_id = f"CC-{new_id(8)}"
        await self._db.execute(
            "INSERT INTO compliance_checks "
            "(id, file_path, violations_found, rules_checked, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (check_id, file_path, violations_found, rules_checked, now),
        )
        return {
            "id": check_id,
            "file_path": file_path,
            "violations_found": violations_found,
            "rules_checked": rules_checked,
            "created_at": now,
        }

    async def get_compliance_status(
        self, framework: str | None = None
    ) -> dict:
        """Return a compliance summary."""
        if framework:
            rules = await self._db.execute_fetchone(
                "SELECT COUNT(*) AS cnt FROM compliance_rules WHERE framework = ?",
                (framework,),
            )
        else:
            rules = await self._db.execute_fetchone(
                "SELECT COUNT(*) AS cnt FROM compliance_rules"
            )

        checks = await self._db.execute_fetchone(
            "SELECT COUNT(*) AS files_checked, "
            "COALESCE(SUM(violations_found), 0) AS total_violations, "
            "COALESCE(SUM(rules_checked), 0) AS total_rules_checked "
            "FROM compliance_checks"
        )

        total_rules = rules["cnt"] if rules else 0
        files_checked = checks["files_checked"] if checks else 0
        total_violations = checks["total_violations"] if checks else 0
        total_rules_checked = checks["total_rules_checked"] if checks else 0

        compliance_pct = (
            round((1 - total_violations / total_rules_checked) * 100, 1)
            if total_rules_checked > 0
            else 100.0
        )
        # Clamp to 0-100
        compliance_pct = max(0.0, min(100.0, compliance_pct))

        return {
            "total_rules": total_rules,
            "files_checked": files_checked,
            "total_violations": total_violations,
            "compliance_percentage": compliance_pct,
        }

    async def add_exemption(
        self,
        rule_id: str,
        file_path: str,
        reason: str,
        approved_by: str,
    ) -> dict:
        """Exempt a file from a specific compliance rule."""
        now = utcnow()
        ex_id = f"EX-{new_id(8)}"
        await self._db.execute(
            "INSERT INTO compliance_exemptions "
            "(id, rule_id, file_path, reason, approved_by, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (ex_id, rule_id, file_path, reason, approved_by, now),
        )
        return {
            "id": ex_id,
            "rule_id": rule_id,
            "file_path": file_path,
            "reason": reason,
            "approved_by": approved_by,
            "created_at": now,
        }
