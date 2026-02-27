"""Security intelligence: vulnerability scanning, secret detection, SAST, license compliance, security change flagging."""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

from ._utils import utcnow as _utcnow, new_id as _new_id, validate_path

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Hardcoded known vulnerable packages
# ---------------------------------------------------------------------------
_KNOWN_VULNERABILITIES: dict[str, list[dict]] = {
    "pyyaml": [
        {
            "vulnerability": "CVE-2020-14343",
            "severity": "critical",
            "description": "Arbitrary code execution via yaml.load",
            "fix_version": "6.0",
            "max_version": (6, 0),
        },
    ],
    "requests": [
        {
            "vulnerability": "CVE-2018-18074",
            "severity": "high",
            "description": "Session object allows bypass of intended access restrictions",
            "fix_version": "2.25.0",
            "max_version": (2, 25),
        },
    ],
    "urllib3": [
        {
            "vulnerability": "CVE-2021-33503",
            "severity": "medium",
            "description": "ReDoS via URL authority parsing",
            "fix_version": "1.26.0",
            "max_version": (1, 26),
        },
    ],
    "jinja2": [
        {
            "vulnerability": "CVE-2024-22195",
            "severity": "high",
            "description": "XSS via xmlattr filter",
            "fix_version": "3.0",
            "max_version": (3, 0),
        },
    ],
}

# ---------------------------------------------------------------------------
# Hardcoded license mapping
# ---------------------------------------------------------------------------
_KNOWN_LICENSES: dict[str, dict] = {
    "fastapi": {"license_type": "MIT", "license_category": "permissive"},
    "aiosqlite": {"license_type": "MIT", "license_category": "permissive"},
    "pydantic": {"license_type": "MIT", "license_category": "permissive"},
    "requests": {"license_type": "Apache-2.0", "license_category": "permissive"},
    "flask": {"license_type": "BSD-3-Clause", "license_category": "permissive"},
    "django": {"license_type": "BSD-3-Clause", "license_category": "permissive"},
    "sqlalchemy": {"license_type": "MIT", "license_category": "permissive"},
    "pytest": {"license_type": "MIT", "license_category": "permissive"},
    "uvicorn": {"license_type": "BSD-3-Clause", "license_category": "permissive"},
    "jinja2": {"license_type": "BSD-3-Clause", "license_category": "permissive"},
    "pyyaml": {"license_type": "MIT", "license_category": "permissive"},
    "numpy": {"license_type": "BSD-3-Clause", "license_category": "permissive"},
    "pandas": {"license_type": "BSD-3-Clause", "license_category": "permissive"},
    "readline": {"license_type": "GPL-3.0", "license_category": "copyleft"},
    "chardet": {"license_type": "LGPL-2.1", "license_category": "copyleft"},
    "pygments": {"license_type": "BSD-2-Clause", "license_category": "permissive"},
}

# ---------------------------------------------------------------------------
# Secret detection patterns
# ---------------------------------------------------------------------------
_SECRET_PATTERNS = [
    (
        "credential_assignment",
        re.compile(
            r"""(?:api[_-]?key|secret|password|token|auth)\s*[=:]\s*['"]([A-Za-z0-9_\-]{8,})['"]""",
            re.IGNORECASE,
        ),
    ),
    (
        "aws_access_key",
        re.compile(r"AKIA[0-9A-Z]{16}"),
    ),
    (
        "private_key",
        re.compile(r"-----BEGIN\s[\w\s]*PRIVATE KEY-----"),
    ),
    (
        "generic_long_secret",
        re.compile(
            r"""(?:key|token|secret|password)\s*[=:]\s*['"]([A-Za-z0-9]{32,})['"]""",
            re.IGNORECASE,
        ),
    ),
]

# File extensions to scan for secrets
_SCANNABLE_EXTENSIONS = {".py", ".yaml", ".yml", ".json", ".env"}

# Security-sensitive path/content keywords
_SECURITY_KEYWORDS = ["auth", "security", "crypto", "password", "token", "secret", "api_key"]


class SecurityIntelManager:
    """Security intelligence for the AI team pipeline."""

    # Maximum file size in bytes for secret/SAST scanning (default 1MB)
    MAX_SCAN_FILE_SIZE: int = 1_048_576

    def __init__(
        self,
        db,
        project_dir: str = ".",
        *,
        max_scan_file_size: int | None = None,
    ) -> None:
        self._db = db
        self._project_dir = project_dir
        if max_scan_file_size is not None:
            self.MAX_SCAN_FILE_SIZE = max_scan_file_size

    # ------------------------------------------------------------------
    # Schema bootstrap
    # ------------------------------------------------------------------

    async def ensure_tables(self) -> None:
        await self._db.executescript("""
            CREATE TABLE IF NOT EXISTS vulnerability_scans (
                id TEXT PRIMARY KEY,
                package_name TEXT NOT NULL,
                installed_version TEXT,
                vulnerability TEXT NOT NULL,
                severity TEXT NOT NULL DEFAULT 'medium',
                fix_version TEXT,
                scanned_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS secret_detections (
                id TEXT PRIMARY KEY,
                file_path TEXT NOT NULL,
                line_number INTEGER,
                secret_type TEXT NOT NULL,
                pattern_matched TEXT,
                severity TEXT NOT NULL DEFAULT 'critical',
                detected_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sast_findings (
                id TEXT PRIMARY KEY,
                file_path TEXT NOT NULL,
                line_number INTEGER,
                finding_type TEXT NOT NULL,
                description TEXT,
                severity TEXT NOT NULL DEFAULT 'high',
                code_snippet TEXT,
                detected_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS license_checks (
                id TEXT PRIMARY KEY,
                package_name TEXT NOT NULL,
                license_type TEXT,
                license_category TEXT,
                compliant INTEGER NOT NULL DEFAULT 1,
                checked_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS security_flags (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                file_path TEXT NOT NULL,
                flag_reason TEXT NOT NULL,
                severity TEXT NOT NULL DEFAULT 'medium',
                reviewed INTEGER NOT NULL DEFAULT 0,
                flagged_at TEXT NOT NULL
            );
        """)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _parse_requirements(self) -> list[tuple[str, str | None]]:
        """Parse requirements.txt returning (name, version_spec) pairs."""
        req_path = Path(self._project_dir) / "requirements.txt"
        if not req_path.exists():
            return []

        deps: list[tuple[str, str | None]] = []
        for line in req_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            # Split on version specifiers
            match = re.match(r"^([A-Za-z0-9_.-]+)\s*(.*)?$", line)
            if match:
                name = match.group(1).lower()
                version_spec = match.group(2).strip() if match.group(2) else None
                deps.append((name, version_spec))
        return deps

    def _parse_pyproject_deps(self) -> list[tuple[str, str | None]]:
        """Parse dependencies from pyproject.toml."""
        pyproject_path = Path(self._project_dir) / "pyproject.toml"
        if not pyproject_path.exists():
            return []

        deps: list[tuple[str, str | None]] = []
        content = pyproject_path.read_text()
        in_deps = False
        for line in content.splitlines():
            if "dependencies" in line and "[" in line:
                in_deps = True
                continue
            if in_deps:
                if line.strip().startswith("]"):
                    in_deps = False
                    continue
                match = re.search(r'"([^"]+)"', line)
                if match:
                    raw = match.group(1)
                    name_match = re.match(r"^([A-Za-z0-9_.-]+)(.*)?$", raw)
                    if name_match:
                        name = name_match.group(1).lower()
                        version_spec = name_match.group(2).strip() if name_match.group(2) else None
                        deps.append((name, version_spec))
        return deps

    def _get_dependencies(self) -> list[tuple[str, str | None]]:
        """Get project dependencies from requirements.txt or pyproject.toml."""
        deps = self._parse_requirements()
        if deps:
            return deps
        return self._parse_pyproject_deps()

    # ------------------------------------------------------------------
    # Feature 34: Dependency Vulnerability Scanning
    # ------------------------------------------------------------------

    async def scan_dependencies(self) -> list[dict]:
        """Parse requirements.txt/pyproject.toml and check against known vulnerabilities."""
        await self.ensure_tables()
        deps = self._get_dependencies()
        now = _utcnow()
        findings: list[dict] = []

        for dep_name, version_spec in deps:
            vulns = _KNOWN_VULNERABILITIES.get(dep_name, [])
            for vuln in vulns:
                rec_id = _new_id()
                finding = {
                    "id": rec_id,
                    "package_name": dep_name,
                    "installed_version": version_spec,
                    "vulnerability": vuln["vulnerability"],
                    "severity": vuln["severity"],
                    "fix_version": vuln.get("fix_version"),
                    "scanned_at": now,
                }
                await self._db.execute(
                    "INSERT INTO vulnerability_scans "
                    "(id, package_name, installed_version, vulnerability, severity, fix_version, scanned_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        rec_id,
                        dep_name,
                        version_spec,
                        vuln["vulnerability"],
                        vuln["severity"],
                        vuln.get("fix_version"),
                        now,
                    ),
                )
                findings.append(finding)

        return findings

    async def get_vulnerabilities(self, severity: str | None = None) -> list[dict]:
        """Query vulnerability_scans, optionally filtered by severity."""
        await self.ensure_tables()
        if severity:
            return await self._db.execute_fetchall(
                "SELECT * FROM vulnerability_scans WHERE severity = ? ORDER BY scanned_at DESC",
                (severity,),
            )
        return await self._db.execute_fetchall(
            "SELECT * FROM vulnerability_scans ORDER BY scanned_at DESC"
        )

    async def add_vulnerability(self, package: str, version_spec: str, cve: str, severity: str, description: str) -> dict:
        """Add or update a known vulnerability."""
        _KNOWN_VULNERABILITIES.setdefault(package, []).append({
            "vulnerability": cve,
            "severity": severity,
            "description": description,
            "fix_version": version_spec,
            "max_version": None,
        })
        return {"package": package, "cve": cve, "added": True}

    # ------------------------------------------------------------------
    # Feature 35: Secret Detection
    # ------------------------------------------------------------------

    async def scan_for_secrets(self, file_path: str) -> list[dict]:
        """Regex scan a single file for secrets (API keys, AWS keys, private keys)."""
        await self.ensure_tables()
        file_path = validate_path(file_path)
        full_path = os.path.join(self._project_dir, file_path)

        try:
            if os.path.getsize(full_path) > self.MAX_SCAN_FILE_SIZE:
                logger.info("Skipping %s: file exceeds %d byte size limit", file_path, self.MAX_SCAN_FILE_SIZE)
                return []
            with open(full_path) as f:
                lines = f.readlines()
        except (FileNotFoundError, OSError) as exc:
            logger.warning("Cannot read %s: %s", file_path, exc)
            return []

        now = _utcnow()
        detections: list[dict] = []

        for line_no, line in enumerate(lines, start=1):
            for secret_type, pattern in _SECRET_PATTERNS:
                match = pattern.search(line)
                if match:
                    rec_id = _new_id()
                    detection = {
                        "id": rec_id,
                        "file_path": file_path,
                        "line_number": line_no,
                        "secret_type": secret_type,
                        "pattern_matched": match.group(0)[:80],
                        "severity": "critical",
                        "detected_at": now,
                    }
                    await self._db.execute(
                        "INSERT INTO secret_detections "
                        "(id, file_path, line_number, secret_type, pattern_matched, severity, detected_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            rec_id,
                            file_path,
                            line_no,
                            secret_type,
                            match.group(0)[:80],
                            "critical",
                            now,
                        ),
                    )
                    detections.append(detection)

        return detections

    async def scan_directory(self, directory: str = ".") -> list[dict]:
        """Scan all .py, .yaml, .yml, .json, .env files in a directory."""
        await self.ensure_tables()
        directory = validate_path(directory)
        dir_path = Path(self._project_dir) / directory
        all_detections: list[dict] = []

        if not dir_path.exists():
            return []

        for ext in _SCANNABLE_EXTENSIONS:
            for found_file in dir_path.rglob(f"*{ext}"):
                rel = str(found_file.relative_to(self._project_dir))
                detections = await self.scan_for_secrets(rel)
                all_detections.extend(detections)

        return all_detections

    # ------------------------------------------------------------------
    # Feature 36: Static Analysis Security Testing (SAST)
    # ------------------------------------------------------------------

    async def run_sast(self, file_path: str) -> list[dict]:
        """AST-based analysis for SQL injection, XSS, and path traversal."""
        await self.ensure_tables()
        file_path = validate_path(file_path)
        full_path = os.path.join(self._project_dir, file_path)

        try:
            if os.path.getsize(full_path) > self.MAX_SCAN_FILE_SIZE:
                logger.info("Skipping SAST for %s: file exceeds %d byte size limit", file_path, self.MAX_SCAN_FILE_SIZE)
                return []
            with open(full_path) as f:
                content = f.read()
                lines = content.splitlines()
        except (FileNotFoundError, OSError) as exc:
            logger.warning("Cannot read %s: %s", file_path, exc)
            return []

        now = _utcnow()
        findings: list[dict] = []

        for line_no, line in enumerate(lines, start=1):
            # SQL injection: string formatting in execute() calls
            if re.search(
                r"""(?:execute|query|cursor)\s*\(.*(?:f['"]|%s|%d|\.format\(|\+\s*\w)""",
                line,
            ):
                rec_id = _new_id()
                finding = {
                    "id": rec_id,
                    "file_path": file_path,
                    "line_number": line_no,
                    "finding_type": "sql_injection",
                    "description": "Possible SQL injection via string formatting in execute() call",
                    "severity": "high",
                    "code_snippet": line.strip()[:200],
                    "detected_at": now,
                }
                await self._db.execute(
                    "INSERT INTO sast_findings "
                    "(id, file_path, line_number, finding_type, description, severity, code_snippet, detected_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        rec_id,
                        file_path,
                        line_no,
                        "sql_injection",
                        finding["description"],
                        "high",
                        finding["code_snippet"],
                        now,
                    ),
                )
                findings.append(finding)

            # XSS: unescaped user input in HTML (innerHTML, document.write, etc.)
            if file_path.endswith((".html", ".js")) and re.search(
                r"innerHTML\s*=|document\.write\s*\(", line
            ):
                rec_id = _new_id()
                finding = {
                    "id": rec_id,
                    "file_path": file_path,
                    "line_number": line_no,
                    "finding_type": "xss",
                    "description": "Possible XSS via unescaped user input in HTML",
                    "severity": "high",
                    "code_snippet": line.strip()[:200],
                    "detected_at": now,
                }
                await self._db.execute(
                    "INSERT INTO sast_findings "
                    "(id, file_path, line_number, finding_type, description, severity, code_snippet, detected_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        rec_id,
                        file_path,
                        line_no,
                        "xss",
                        finding["description"],
                        "high",
                        finding["code_snippet"],
                        now,
                    ),
                )
                findings.append(finding)

            # Path traversal: os.path.join with user input without sanitization
            if re.search(
                r"os\.path\.join\s*\(.*(?:user_input|request|args|params)", line
            ):
                rec_id = _new_id()
                finding = {
                    "id": rec_id,
                    "file_path": file_path,
                    "line_number": line_no,
                    "finding_type": "path_traversal",
                    "description": "Possible path traversal via os.path.join with user input",
                    "severity": "high",
                    "code_snippet": line.strip()[:200],
                    "detected_at": now,
                }
                await self._db.execute(
                    "INSERT INTO sast_findings "
                    "(id, file_path, line_number, finding_type, description, severity, code_snippet, detected_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        rec_id,
                        file_path,
                        line_no,
                        "path_traversal",
                        finding["description"],
                        "high",
                        finding["code_snippet"],
                        now,
                    ),
                )
                findings.append(finding)

            # Command injection: os.system() or subprocess with shell=True
            if re.search(r"os\.system\s*\(", line) or re.search(
                r"subprocess\.call\s*\(.*shell\s*=\s*True", line
            ):
                rec_id = _new_id()
                finding = {
                    "id": rec_id,
                    "file_path": file_path,
                    "line_number": line_no,
                    "finding_type": "command_injection",
                    "description": "Possible command injection via os.system() or subprocess with shell=True",
                    "severity": "critical",
                    "code_snippet": line.strip()[:200],
                    "detected_at": now,
                }
                await self._db.execute(
                    "INSERT INTO sast_findings "
                    "(id, file_path, line_number, finding_type, description, severity, code_snippet, detected_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        rec_id,
                        file_path,
                        line_no,
                        "command_injection",
                        finding["description"],
                        "critical",
                        finding["code_snippet"],
                        now,
                    ),
                )
                findings.append(finding)

        return findings

    async def get_sast_findings(
        self, file_path: str | None = None, severity: str | None = None
    ) -> list[dict]:
        """Query sast_findings with optional file_path and severity filters."""
        await self.ensure_tables()
        conditions: list[str] = []
        params: list[str] = []

        if file_path:
            conditions.append("file_path = ?")
            params.append(file_path)
        if severity:
            conditions.append("severity = ?")
            params.append(severity)

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        return await self._db.execute_fetchall(
            f"SELECT * FROM sast_findings {where} ORDER BY detected_at DESC",
            tuple(params),
        )

    # ------------------------------------------------------------------
    # Feature 37: License Compliance Checking
    # ------------------------------------------------------------------

    async def check_licenses(self) -> list[dict]:
        """Parse requirements.txt and check against hardcoded license categories."""
        await self.ensure_tables()
        deps = self._get_dependencies()
        now = _utcnow()
        results: list[dict] = []

        for dep_name, _version_spec in deps:
            info = _KNOWN_LICENSES.get(dep_name)
            if info:
                license_type = info["license_type"]
                license_category = info["license_category"]
                compliant = 1 if license_category == "permissive" else 0
            else:
                license_type = "unknown"
                license_category = "unknown"
                compliant = 1  # Assume compliant if unknown

            rec_id = _new_id()
            result = {
                "id": rec_id,
                "package_name": dep_name,
                "license_type": license_type,
                "license_category": license_category,
                "compliant": compliant,
                "checked_at": now,
            }
            await self._db.execute(
                "INSERT INTO license_checks "
                "(id, package_name, license_type, license_category, compliant, checked_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (rec_id, dep_name, license_type, license_category, compliant, now),
            )
            results.append(result)

        return results

    async def get_license_issues(self) -> list[dict]:
        """Return license_checks entries with license_category='copyleft'."""
        await self.ensure_tables()
        return await self._db.execute_fetchall(
            "SELECT * FROM license_checks WHERE license_category = 'copyleft' ORDER BY checked_at DESC"
        )

    # ------------------------------------------------------------------
    # Feature 38: Security-Sensitive Change Detection
    # ------------------------------------------------------------------

    async def flag_security_changes(
        self, task_id: str, files_changed: list[str]
    ) -> list[dict]:
        """Auto-flag files that touch auth, security, crypto, or contain security keywords."""
        await self.ensure_tables()
        now = _utcnow()
        flags: list[dict] = []

        for file_path in files_changed:
            reasons: list[str] = []

            # Check path for security keywords
            path_lower = file_path.lower()
            for keyword in _SECURITY_KEYWORDS:
                if keyword in path_lower:
                    reasons.append(f"Path contains '{keyword}'")

            # Check file content for security keywords
            full_path = os.path.join(self._project_dir, file_path)
            try:
                with open(full_path) as f:
                    content = f.read().lower()
                for keyword in _SECURITY_KEYWORDS:
                    if keyword in content and f"Path contains '{keyword}'" not in reasons:
                        reasons.append(f"Content contains '{keyword}'")
            except (FileNotFoundError, OSError) as exc:
                logger.warning("Cannot read %s for security flag check: %s", file_path, exc)

            if reasons:
                rec_id = _new_id()
                flag_reason = "; ".join(reasons)
                severity = "high" if any(
                    k in path_lower for k in ("auth", "crypto", "security")
                ) else "medium"
                flag = {
                    "id": rec_id,
                    "task_id": task_id,
                    "file_path": file_path,
                    "flag_reason": flag_reason,
                    "severity": severity,
                    "reviewed": 0,
                    "flagged_at": now,
                }
                await self._db.execute(
                    "INSERT INTO security_flags "
                    "(id, task_id, file_path, flag_reason, severity, reviewed, flagged_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (rec_id, task_id, file_path, flag_reason, severity, 0, now),
                )
                flags.append(flag)

        return flags

    async def get_security_flags(
        self, task_id: str | None = None, reviewed: bool | None = None
    ) -> list[dict]:
        """Query security_flags with optional task_id and reviewed filters."""
        await self.ensure_tables()
        conditions: list[str] = []
        params: list = []

        if task_id:
            conditions.append("task_id = ?")
            params.append(task_id)
        if reviewed is not None:
            conditions.append("reviewed = ?")
            params.append(1 if reviewed else 0)

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        return await self._db.execute_fetchall(
            f"SELECT * FROM security_flags {where} ORDER BY flagged_at DESC",
            tuple(params),
        )
