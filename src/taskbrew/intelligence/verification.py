"""Verification intelligence: regression fingerprinting, test impact analysis, flaky test detection, behavioral spec mining, code review auto-annotation, quality gate composition."""

from __future__ import annotations

import json
import logging
import os
import re

from taskbrew.intelligence._utils import utcnow, new_id, clamp

logger = logging.getLogger(__name__)


class VerificationManager:
    """Manage verification and quality assurance intelligence capabilities."""

    def __init__(self, db, project_dir: str = ".") -> None:
        self._db = db
        self._project_dir = project_dir

    # ------------------------------------------------------------------
    # Schema bootstrap
    # ------------------------------------------------------------------

    async def ensure_tables(self) -> None:
        await self._db.executescript("""
            CREATE TABLE IF NOT EXISTS regression_fingerprints (
                id TEXT PRIMARY KEY,
                test_name TEXT NOT NULL,
                error_message TEXT NOT NULL,
                failing_commit TEXT NOT NULL,
                last_passing_commit TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS test_file_mappings (
                id TEXT PRIMARY KEY,
                test_file TEXT NOT NULL,
                source_file TEXT NOT NULL,
                confidence REAL NOT NULL DEFAULT 1.0,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS test_runs (
                id TEXT PRIMARY KEY,
                test_name TEXT NOT NULL,
                passed INTEGER NOT NULL,
                duration_ms INTEGER,
                run_id TEXT,
                quarantined INTEGER NOT NULL DEFAULT 0,
                quarantine_reason TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS behavioral_specs (
                id TEXT PRIMARY KEY,
                test_file TEXT NOT NULL,
                test_name TEXT NOT NULL,
                asserted_behavior TEXT NOT NULL,
                source_file TEXT,
                documented INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS review_annotations (
                id TEXT PRIMARY KEY,
                file_path TEXT NOT NULL,
                line_number INTEGER NOT NULL,
                annotation_type TEXT NOT NULL,
                message TEXT NOT NULL,
                severity TEXT NOT NULL DEFAULT 'info',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS quality_gates (
                id TEXT PRIMARY KEY,
                gate_name TEXT NOT NULL UNIQUE,
                conditions TEXT NOT NULL,
                risk_level TEXT NOT NULL DEFAULT 'standard',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS gate_results (
                id TEXT PRIMARY KEY,
                gate_name TEXT NOT NULL,
                passed INTEGER NOT NULL,
                details TEXT,
                metrics TEXT,
                evaluated_at TEXT NOT NULL
            );
        """)

    # ------------------------------------------------------------------
    # Feature 33: Regression Fingerprinter
    # ------------------------------------------------------------------

    async def fingerprint_regression(
        self,
        test_name: str,
        error_message: str,
        failing_commit: str,
        last_passing_commit: str | None = None,
    ) -> dict:
        """Store a regression fingerprint for a failing test."""
        fp_id = f"RF-{new_id(8)}"
        now = utcnow()
        await self._db.execute(
            "INSERT INTO regression_fingerprints "
            "(id, test_name, error_message, failing_commit, last_passing_commit, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (fp_id, test_name, error_message, failing_commit, last_passing_commit, now),
        )
        return {
            "id": fp_id,
            "test_name": test_name,
            "error_message": error_message,
            "failing_commit": failing_commit,
            "last_passing_commit": last_passing_commit,
            "created_at": now,
        }

    async def find_similar_regressions(
        self, error_message: str, limit: int = 5
    ) -> list[dict]:
        """Find regressions with similar error messages using keyword matching."""
        # Extract significant keywords (3+ chars, not common stopwords)
        words = re.findall(r"[A-Za-z_]\w{2,}", error_message)
        if not words:
            return []

        # Build LIKE conditions for each keyword
        conditions = []
        params: list = []
        for word in words[:10]:  # Limit to 10 keywords
            conditions.append("error_message LIKE ?")
            params.append(f"%{word}%")

        where_clause = " OR ".join(conditions)
        params.append(limit)
        return await self._db.execute_fetchall(
            f"SELECT * FROM regression_fingerprints WHERE {where_clause} "
            "ORDER BY created_at DESC LIMIT ?",
            tuple(params),
        )

    async def get_fingerprints(
        self, test_name: str | None = None, limit: int = 20
    ) -> list[dict]:
        """List regression fingerprints, optionally filtered by test name."""
        if test_name:
            return await self._db.execute_fetchall(
                "SELECT * FROM regression_fingerprints WHERE test_name = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (test_name, limit),
            )
        return await self._db.execute_fetchall(
            "SELECT * FROM regression_fingerprints ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )

    # ------------------------------------------------------------------
    # Feature 34: Test Impact Analyzer
    # ------------------------------------------------------------------

    async def record_mapping(
        self, test_file: str, source_file: str, confidence: float = 1.0
    ) -> dict:
        """Map a test file to a source file it covers."""
        confidence = clamp(confidence)
        mapping_id = f"TM-{new_id(8)}"
        now = utcnow()
        await self._db.execute(
            "INSERT INTO test_file_mappings (id, test_file, source_file, confidence, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (mapping_id, test_file, source_file, confidence, now),
        )
        return {
            "id": mapping_id,
            "test_file": test_file,
            "source_file": source_file,
            "confidence": confidence,
            "created_at": now,
        }

    async def get_affected_tests(self, changed_files: list[str]) -> list[dict]:
        """Return test mappings that cover any of the changed files."""
        if not changed_files:
            return []

        placeholders = ", ".join("?" for _ in changed_files)
        return await self._db.execute_fetchall(
            f"SELECT * FROM test_file_mappings WHERE source_file IN ({placeholders}) "
            "ORDER BY confidence DESC",
            tuple(changed_files),
        )

    async def get_mappings(
        self, source_file: str | None = None, test_file: str | None = None
    ) -> list[dict]:
        """List test-to-source mappings with optional filters."""
        if source_file and test_file:
            return await self._db.execute_fetchall(
                "SELECT * FROM test_file_mappings WHERE source_file = ? AND test_file = ? "
                "ORDER BY created_at DESC",
                (source_file, test_file),
            )
        if source_file:
            return await self._db.execute_fetchall(
                "SELECT * FROM test_file_mappings WHERE source_file = ? ORDER BY created_at DESC",
                (source_file,),
            )
        if test_file:
            return await self._db.execute_fetchall(
                "SELECT * FROM test_file_mappings WHERE test_file = ? ORDER BY created_at DESC",
                (test_file,),
            )
        return await self._db.execute_fetchall(
            "SELECT * FROM test_file_mappings ORDER BY created_at DESC",
        )

    async def auto_map(self, test_dir: str, source_dir: str) -> list[dict]:
        """Heuristic mapping by naming convention: test_foo.py maps to foo.py."""
        from pathlib import Path

        test_path = Path(test_dir)
        source_path = Path(source_dir)

        if not test_path.is_dir() or not source_path.is_dir():
            return []

        # Build lookup of source files by name
        source_files = {}
        for sf in source_path.rglob("*.py"):
            if sf.name != "__init__.py":
                source_files[sf.name] = str(sf)

        mappings = []
        for tf in test_path.rglob("*.py"):
            if tf.name.startswith("test_"):
                # test_foo.py -> foo.py
                expected_source = tf.name[5:]  # strip "test_"
                if expected_source in source_files:
                    mapping = await self.record_mapping(
                        str(tf), source_files[expected_source], confidence=0.8
                    )
                    mappings.append(mapping)

        return mappings

    # ------------------------------------------------------------------
    # Feature 35: Flaky Test Detector
    # ------------------------------------------------------------------

    async def record_run(
        self,
        test_name: str,
        passed: bool,
        duration_ms: int | None = None,
        run_id: str | None = None,
    ) -> dict:
        """Record a single test run result."""
        rec_id = f"TR-{new_id(8)}"
        now = utcnow()
        await self._db.execute(
            "INSERT INTO test_runs (id, test_name, passed, duration_ms, run_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (rec_id, test_name, int(passed), duration_ms, run_id, now),
        )
        return {
            "id": rec_id,
            "test_name": test_name,
            "passed": passed,
            "duration_ms": duration_ms,
            "run_id": run_id,
            "created_at": now,
        }

    async def detect_flaky(
        self, min_runs: int = 5, flaky_threshold: float = 0.1
    ) -> list[dict]:
        """Detect tests that fail more than the threshold percentage of runs.

        A test is considered flaky if it has at least *min_runs* total runs
        and its failure rate exceeds *flaky_threshold* but is not 100% (which
        would indicate a genuine broken test, not a flaky one).
        """
        rows = await self._db.execute_fetchall(
            "SELECT test_name, "
            "COUNT(*) as total_runs, "
            "SUM(CASE WHEN passed = 0 THEN 1 ELSE 0 END) as failures "
            "FROM test_runs "
            "WHERE quarantined = 0 "
            "GROUP BY test_name "
            "HAVING total_runs >= ? ",
            (min_runs,),
        )
        flaky = []
        for row in rows:
            total = row["total_runs"]
            failures = row["failures"]
            failure_rate = failures / total if total > 0 else 0.0
            # Flaky = fails sometimes but not always
            if flaky_threshold < failure_rate < 1.0:
                flaky.append({
                    "test_name": row["test_name"],
                    "total_runs": total,
                    "failures": failures,
                    "failure_rate": round(failure_rate, 4),
                })
        return sorted(flaky, key=lambda x: x["failure_rate"], reverse=True)

    async def get_flaky_tests(self, limit: int = 20) -> list[dict]:
        """List flaky tests with their failure rate."""
        rows = await self._db.execute_fetchall(
            "SELECT test_name, "
            "COUNT(*) as total_runs, "
            "SUM(CASE WHEN passed = 0 THEN 1 ELSE 0 END) as failures "
            "FROM test_runs "
            "WHERE quarantined = 0 "
            "GROUP BY test_name "
            "HAVING total_runs >= 2 AND failures > 0 AND failures < total_runs "
            "ORDER BY CAST(failures AS REAL) / total_runs DESC "
            "LIMIT ?",
            (limit,),
        )
        result = []
        for row in rows:
            total = row["total_runs"]
            failures = row["failures"]
            result.append({
                "test_name": row["test_name"],
                "total_runs": total,
                "failures": failures,
                "failure_rate": round(failures / total, 4) if total > 0 else 0.0,
            })
        return result

    async def quarantine_test(
        self, test_name: str, reason: str | None = None
    ) -> dict:
        """Mark all runs for a test as quarantined."""
        now = utcnow()
        await self._db.execute(
            "UPDATE test_runs SET quarantined = 1, quarantine_reason = ? WHERE test_name = ?",
            (reason, test_name),
        )
        return {
            "test_name": test_name,
            "quarantined": True,
            "reason": reason,
            "quarantined_at": now,
        }

    # ------------------------------------------------------------------
    # Feature 36: Behavioral Spec Miner
    # ------------------------------------------------------------------

    async def mine_spec(
        self, test_file: str, test_name: str, asserted_behavior: str
    ) -> dict:
        """Extract a behavioral spec from a test assertion."""
        spec_id = f"BS-{new_id(8)}"
        now = utcnow()
        await self._db.execute(
            "INSERT INTO behavioral_specs "
            "(id, test_file, test_name, asserted_behavior, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (spec_id, test_file, test_name, asserted_behavior, now),
        )
        return {
            "id": spec_id,
            "test_file": test_file,
            "test_name": test_name,
            "asserted_behavior": asserted_behavior,
            "created_at": now,
        }

    async def get_specs(
        self, source_file: str | None = None, limit: int = 20
    ) -> list[dict]:
        """List behavioral specs, optionally filtered by source file."""
        if source_file:
            return await self._db.execute_fetchall(
                "SELECT * FROM behavioral_specs WHERE source_file = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (source_file, limit),
            )
        return await self._db.execute_fetchall(
            "SELECT * FROM behavioral_specs ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )

    async def detect_undocumented(self) -> list[dict]:
        """Return specs that are not marked as documented."""
        return await self._db.execute_fetchall(
            "SELECT * FROM behavioral_specs WHERE documented = 0 "
            "ORDER BY created_at DESC",
        )

    # ------------------------------------------------------------------
    # Feature 37: Code Review Auto-Annotator
    # ------------------------------------------------------------------

    async def annotate(
        self,
        file_path: str,
        line_number: int,
        annotation_type: str,
        message: str,
        severity: str = "info",
    ) -> dict:
        """Add a review annotation to a file at a specific line."""
        ann_id = f"RA-{new_id(8)}"
        now = utcnow()
        await self._db.execute(
            "INSERT INTO review_annotations "
            "(id, file_path, line_number, annotation_type, message, severity, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (ann_id, file_path, line_number, annotation_type, message, severity, now),
        )
        return {
            "id": ann_id,
            "file_path": file_path,
            "line_number": line_number,
            "annotation_type": annotation_type,
            "message": message,
            "severity": severity,
            "created_at": now,
        }

    async def get_annotations(
        self,
        file_path: str | None = None,
        annotation_type: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """List annotations with optional filters."""
        if file_path and annotation_type:
            return await self._db.execute_fetchall(
                "SELECT * FROM review_annotations "
                "WHERE file_path = ? AND annotation_type = ? "
                "ORDER BY line_number LIMIT ?",
                (file_path, annotation_type, limit),
            )
        if file_path:
            return await self._db.execute_fetchall(
                "SELECT * FROM review_annotations WHERE file_path = ? "
                "ORDER BY line_number LIMIT ?",
                (file_path, limit),
            )
        if annotation_type:
            return await self._db.execute_fetchall(
                "SELECT * FROM review_annotations WHERE annotation_type = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (annotation_type, limit),
            )
        return await self._db.execute_fetchall(
            "SELECT * FROM review_annotations ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )

    async def auto_annotate(
        self, file_path: str, content: str | None = None
    ) -> list[dict]:
        """Generate annotations for common code issues in a file.

        Detects:
        - TODO/FIXME/HACK comments
        - Deeply nested code (4+ levels of indentation)
        - Long functions (50+ lines)
        """
        if content is None:
            full_path = os.path.join(self._project_dir, file_path)
            try:
                with open(full_path, "r", errors="replace") as f:
                    content = f.read()
            except OSError as exc:
                logger.warning("Cannot read %s: %s", full_path, exc)
                return []

        annotations = []
        lines = content.splitlines()

        # Detect TODO/FIXME/HACK
        for i, line in enumerate(lines, 1):
            for marker in ("TODO", "FIXME", "HACK"):
                if marker in line:
                    ann = await self.annotate(
                        file_path, i, "comment_marker",
                        f"{marker} found: {line.strip()[:100]}",
                        severity="warning",
                    )
                    annotations.append(ann)

        # Detect deep nesting (4+ levels, assuming 4 spaces per level)
        for i, line in enumerate(lines, 1):
            stripped = line.lstrip()
            if stripped and not stripped.startswith("#"):
                indent = len(line) - len(stripped)
                if indent >= 16:  # 4 levels * 4 spaces
                    ann = await self.annotate(
                        file_path, i, "complexity",
                        f"Deeply nested code (indent level {indent // 4})",
                        severity="warning",
                    )
                    annotations.append(ann)

        # Detect long functions (50+ lines)
        func_start = None
        func_name = None
        for i, line in enumerate(lines, 1):
            m = re.match(r"^(\s*)def\s+(\w+)", line)
            if m:
                if func_start is not None and func_name is not None:
                    length = i - func_start
                    if length > 50:
                        ann = await self.annotate(
                            file_path, func_start, "long_function",
                            f"Function '{func_name}' is {length} lines long",
                            severity="info",
                        )
                        annotations.append(ann)
                func_start = i
                func_name = m.group(2)

        # Check last function
        if func_start is not None and func_name is not None:
            length = len(lines) - func_start + 1
            if length > 50:
                ann = await self.annotate(
                    file_path, func_start, "long_function",
                    f"Function '{func_name}' is {length} lines long",
                    severity="info",
                )
                annotations.append(ann)

        return annotations

    async def clear_annotations(self, file_path: str) -> dict:
        """Remove all annotations for a given file."""
        # Count before deleting
        rows = await self._db.execute_fetchall(
            "SELECT COUNT(*) as cnt FROM review_annotations WHERE file_path = ?",
            (file_path,),
        )
        count = rows[0]["cnt"] if rows else 0
        await self._db.execute(
            "DELETE FROM review_annotations WHERE file_path = ?",
            (file_path,),
        )
        return {"file_path": file_path, "removed": count}

    # ------------------------------------------------------------------
    # Feature 38: Quality Gate Composer
    # ------------------------------------------------------------------

    async def define_gate(
        self,
        gate_name: str,
        conditions: dict,
        risk_level: str = "standard",
    ) -> dict:
        """Define a quality gate with conditions (JSON).

        Conditions can include: min_test_coverage, max_complexity,
        min_test_pass_rate, max_open_bugs, etc.
        """
        gate_id = f"QG-{new_id(8)}"
        now = utcnow()
        conditions_json = json.dumps(conditions)
        await self._db.execute(
            "INSERT OR REPLACE INTO quality_gates "
            "(id, gate_name, conditions, risk_level, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (gate_id, gate_name, conditions_json, risk_level, now),
        )
        return {
            "id": gate_id,
            "gate_name": gate_name,
            "conditions": conditions,
            "risk_level": risk_level,
            "created_at": now,
        }

    async def evaluate_gate(self, gate_name: str, metrics: dict) -> dict:
        """Evaluate metrics against a quality gate's conditions.

        Returns pass/fail with detailed results for each condition.
        """
        gate = await self._db.execute_fetchone(
            "SELECT * FROM quality_gates WHERE gate_name = ?",
            (gate_name,),
        )
        if not gate:
            return {"gate_name": gate_name, "passed": False, "error": "Gate not found"}

        conditions = json.loads(gate["conditions"])
        details = []
        all_passed = True

        for key, threshold in conditions.items():
            actual = metrics.get(key)
            if actual is None:
                details.append({
                    "condition": key,
                    "expected": threshold,
                    "actual": None,
                    "passed": False,
                    "reason": "Metric not provided",
                })
                all_passed = False
                continue

            # Determine pass based on condition prefix
            passed = True
            if key.startswith("min_"):
                passed = actual >= threshold
            elif key.startswith("max_"):
                passed = actual <= threshold
            else:
                # Default: treat as minimum threshold
                passed = actual >= threshold

            if not passed:
                all_passed = False

            details.append({
                "condition": key,
                "expected": threshold,
                "actual": actual,
                "passed": passed,
            })

        # Record result
        result_id = f"GR-{new_id(8)}"
        now = utcnow()
        await self._db.execute(
            "INSERT INTO gate_results "
            "(id, gate_name, passed, details, metrics, evaluated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (result_id, gate_name, int(all_passed), json.dumps(details), json.dumps(metrics), now),
        )

        return {
            "id": result_id,
            "gate_name": gate_name,
            "passed": all_passed,
            "details": details,
            "evaluated_at": now,
        }

    async def get_gates(self) -> list[dict]:
        """List all defined quality gates."""
        rows = await self._db.execute_fetchall(
            "SELECT * FROM quality_gates ORDER BY gate_name",
        )
        for row in rows:
            if isinstance(row.get("conditions"), str):
                row["conditions"] = json.loads(row["conditions"])
        return rows

    async def get_gate_history(
        self, gate_name: str | None = None, limit: int = 20
    ) -> list[dict]:
        """Return past gate evaluations."""
        if gate_name:
            return await self._db.execute_fetchall(
                "SELECT * FROM gate_results WHERE gate_name = ? "
                "ORDER BY evaluated_at DESC LIMIT ?",
                (gate_name, limit),
            )
        return await self._db.execute_fetchall(
            "SELECT * FROM gate_results ORDER BY evaluated_at DESC LIMIT ?",
            (limit,),
        )
