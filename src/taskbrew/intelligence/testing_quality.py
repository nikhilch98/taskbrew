"""Testing quality: test generation, mutation analysis, property tests, regression risk, review checklists, doc drift, and performance regression detection."""

from __future__ import annotations

import ast
import json
import logging
import math
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# Checklist templates by task type
_CHECKLIST_TEMPLATES: dict[str, list[str]] = {
    "implementation": [
        "Tests added?",
        "Docs updated?",
        "Error handling?",
        "Type hints?",
    ],
    "bug_fix": [
        "Root cause identified?",
        "Regression test added?",
        "Related code checked?",
    ],
    "code_review": [
        "Style consistent?",
        "No dead code?",
        "Security reviewed?",
    ],
}


class TestingQualityManager:
    """Manage testing quality metrics: skeleton generation, mutation analysis, property tests, regression risk, review checklists, doc drift, and perf regression detection."""

    __test__ = False  # prevent pytest collection

    # Default threshold for performance regression detection (percentage)
    DEFAULT_REGRESSION_THRESHOLD_PCT: float = 20.0

    # Regression risk: file count threshold
    RISK_FILE_COUNT_THRESHOLD: int = 5

    # Regression risk: line count threshold per file
    RISK_LINE_COUNT_THRESHOLD: int = 500

    def __init__(
        self,
        db,
        project_dir: str = ".",
        *,
        regression_threshold_pct: float | None = None,
        risk_file_count_threshold: int | None = None,
        risk_line_count_threshold: int | None = None,
    ) -> None:
        self._db = db
        self._project_dir = project_dir
        if regression_threshold_pct is not None:
            self.DEFAULT_REGRESSION_THRESHOLD_PCT = regression_threshold_pct
        if risk_file_count_threshold is not None:
            self.RISK_FILE_COUNT_THRESHOLD = risk_file_count_threshold
        if risk_line_count_threshold is not None:
            self.RISK_LINE_COUNT_THRESHOLD = risk_line_count_threshold

    # ------------------------------------------------------------------
    # Table bootstrapping
    # ------------------------------------------------------------------

    async def _ensure_tables(self) -> None:
        """Create tables if they do not yet exist."""
        await self._db.execute(
            """CREATE TABLE IF NOT EXISTS generated_tests (
                id TEXT PRIMARY KEY,
                source_file TEXT NOT NULL,
                function_name TEXT NOT NULL,
                test_skeleton TEXT NOT NULL,
                test_type TEXT NOT NULL DEFAULT 'unit',
                status TEXT NOT NULL DEFAULT 'generated',
                created_at TEXT NOT NULL
            )"""
        )
        await self._db.execute(
            """CREATE TABLE IF NOT EXISTS mutation_results (
                id TEXT PRIMARY KEY,
                file_path TEXT NOT NULL,
                mutation_type TEXT NOT NULL,
                survived INTEGER NOT NULL DEFAULT 0,
                killed INTEGER NOT NULL DEFAULT 0,
                score REAL NOT NULL DEFAULT 0,
                details TEXT,
                created_at TEXT NOT NULL
            )"""
        )
        await self._db.execute(
            """CREATE TABLE IF NOT EXISTS regression_predictions (
                id TEXT PRIMARY KEY,
                pr_identifier TEXT,
                risk_score REAL NOT NULL,
                risk_factors TEXT,
                files_changed TEXT,
                predicted_at TEXT NOT NULL
            )"""
        )
        await self._db.execute(
            """CREATE TABLE IF NOT EXISTS review_checklists (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                task_type TEXT,
                checklist_items TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL
            )"""
        )
        await self._db.execute(
            """CREATE TABLE IF NOT EXISTS doc_drift_reports (
                id TEXT PRIMARY KEY,
                doc_file TEXT NOT NULL,
                code_file TEXT,
                drift_type TEXT NOT NULL,
                details TEXT,
                severity TEXT NOT NULL DEFAULT 'medium',
                created_at TEXT NOT NULL
            )"""
        )
        await self._db.execute(
            """CREATE TABLE IF NOT EXISTS perf_baselines (
                id TEXT PRIMARY KEY,
                test_name TEXT UNIQUE NOT NULL,
                avg_duration_ms REAL NOT NULL DEFAULT 0,
                std_deviation_ms REAL NOT NULL DEFAULT 0,
                sample_count INTEGER NOT NULL DEFAULT 0,
                last_updated TEXT NOT NULL
            )"""
        )

    # ------------------------------------------------------------------
    # Feature 27: Test Case Generation
    # ------------------------------------------------------------------

    async def generate_test_skeletons(self, source_file: str) -> list[dict]:
        """Parse a Python file and generate test skeleton strings for each function."""
        await self._ensure_tables()
        full_path = os.path.join(self._project_dir, source_file)

        try:
            with open(full_path) as f:
                tree = ast.parse(f.read())
        except (FileNotFoundError, SyntaxError) as exc:
            logger.warning("Cannot parse %s: %s", source_file, exc)
            return []

        functions: list[dict] = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                args = []
                for arg in node.args.args:
                    if arg.arg == "self":
                        continue
                    args.append(arg.arg)
                ret_annotation = None
                if node.returns:
                    ret_annotation = ast.dump(node.returns)
                functions.append({
                    "name": node.name,
                    "args": args,
                    "return_annotation": ret_annotation,
                })

        now = datetime.now(timezone.utc).isoformat()
        results: list[dict] = []
        for func in functions:
            args_str = ", ".join(func["args"]) if func["args"] else ""
            skeleton = f"def test_{func['name']}():\n    result = {func['name']}({args_str})\n    assert result is not None"
            rec_id = uuid.uuid4().hex[:12]
            await self._db.execute(
                "INSERT INTO generated_tests (id, source_file, function_name, test_skeleton, test_type, status, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (rec_id, source_file, func["name"], skeleton, "unit", "generated", now),
            )
            results.append({
                "id": rec_id,
                "source_file": source_file,
                "function_name": func["name"],
                "test_skeleton": skeleton,
                "test_type": "unit",
                "status": "generated",
                "created_at": now,
            })

        return results

    # ------------------------------------------------------------------
    # Feature 28: Mutation Testing Integration
    # ------------------------------------------------------------------

    async def run_mutation_analysis(self, file_path: str) -> dict:
        """Analyze a Python file for potential mutation points and compute a score."""
        await self._ensure_tables()
        full_path = os.path.join(self._project_dir, file_path)

        try:
            with open(full_path) as f:
                source = f.read()
            tree = ast.parse(source)
        except (FileNotFoundError, SyntaxError) as exc:
            logger.warning("Cannot parse %s for mutation analysis: %s", file_path, exc)
            return {"error": str(exc)}

        total_lines = len(source.splitlines())
        comparison_ops = 0
        arithmetic_ops = 0
        boolean_ops = 0

        for node in ast.walk(tree):
            if isinstance(node, ast.Compare):
                comparison_ops += len(node.ops)
            elif isinstance(node, ast.BinOp):
                arithmetic_ops += 1
            elif isinstance(node, ast.BoolOp):
                boolean_ops += 1

        mutation_points = comparison_ops + arithmetic_ops + boolean_ops
        denominator = total_lines * 2 if total_lines > 0 else 1
        score = max(0.0, 1.0 - (mutation_points / denominator))
        score = round(score, 4)

        details = {
            "total_lines": total_lines,
            "comparison_ops": comparison_ops,
            "arithmetic_ops": arithmetic_ops,
            "boolean_ops": boolean_ops,
            "mutation_points": mutation_points,
        }

        now = datetime.now(timezone.utc).isoformat()
        rec_id = uuid.uuid4().hex[:12]

        await self._db.execute(
            "INSERT INTO mutation_results (id, file_path, mutation_type, survived, killed, score, details, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (rec_id, file_path, "operator", mutation_points, 0, score, json.dumps(details), now),
        )

        return {
            "id": rec_id,
            "file_path": file_path,
            "score": score,
            "mutation_points": mutation_points,
            "details": details,
            "created_at": now,
        }

    async def get_mutation_scores(self, file_path: str | None = None) -> list[dict]:
        """Query mutation results, optionally filtered by file path."""
        await self._ensure_tables()
        if file_path:
            rows = await self._db.execute_fetchall(
                "SELECT * FROM mutation_results WHERE file_path = ? ORDER BY created_at DESC",
                (file_path,),
            )
        else:
            rows = await self._db.execute_fetchall(
                "SELECT * FROM mutation_results ORDER BY created_at DESC"
            )
        for row in rows:
            if isinstance(row.get("details"), str):
                try:
                    row["details"] = json.loads(row["details"])
                except (json.JSONDecodeError, TypeError) as exc:
                    logger.warning("Failed to parse mutation details JSON for %s: %s", row.get("file_path"), exc)
        return rows

    # ------------------------------------------------------------------
    # Feature 29: Property-Based Test Suggestions
    # ------------------------------------------------------------------

    async def suggest_property_tests(self, source_file: str) -> list[dict]:
        """Identify pure functions and suggest property-based tests for them."""
        full_path = os.path.join(self._project_dir, source_file)

        try:
            with open(full_path) as f:
                tree = ast.parse(f.read())
        except (FileNotFoundError, SyntaxError) as exc:
            logger.warning("Cannot parse %s: %s", source_file, exc)
            return []

        suggestions: list[dict] = []
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            # Skip methods (have 'self' as first arg)
            arg_names = [a.arg for a in node.args.args]
            if "self" in arg_names:
                continue
            # Must have a return statement
            has_return = any(isinstance(n, ast.Return) and n.value is not None for n in ast.walk(node))
            if not has_return:
                continue
            # Detect side effects: calls to print, open, os.*, sys.*, write, etc.
            has_side_effects = False
            for child in ast.walk(node):
                if isinstance(child, ast.Call):
                    if isinstance(child.func, ast.Name) and child.func.id in ("print", "open", "input", "exit"):
                        has_side_effects = True
                        break
                    if isinstance(child.func, ast.Attribute) and child.func.attr in ("write", "system", "remove", "mkdir"):
                        has_side_effects = True
                        break
            if has_side_effects:
                continue

            suggestions.append({
                "function_name": node.name,
                "args": [a for a in arg_names],
                "suggestion": "for any valid input, output type should be consistent",
                "source_file": source_file,
            })

        return suggestions

    # ------------------------------------------------------------------
    # Feature 30: Regression Risk Prediction
    # ------------------------------------------------------------------

    async def predict_regression_risk(
        self, files_changed: list[str], pr_identifier: str | None = None
    ) -> dict:
        """Calculate regression risk score based on the changed files."""
        await self._ensure_tables()
        risk_score = 0.0
        risk_factors: list[str] = []

        # +0.3 if files changed exceeds threshold
        if len(files_changed) > self.RISK_FILE_COUNT_THRESHOLD:
            risk_score += 0.3
            risk_factors.append(f"{len(files_changed)} files changed (>{self.RISK_FILE_COUNT_THRESHOLD})")

        for f in files_changed:
            # +0.2 per file with >500 lines
            full_path = os.path.join(self._project_dir, f)
            try:
                with open(full_path) as fh:
                    line_count = len(fh.readlines())
                if line_count > self.RISK_LINE_COUNT_THRESHOLD:
                    risk_score += 0.2
                    risk_factors.append(f"{f} has {line_count} lines (>{self.RISK_LINE_COUNT_THRESHOLD})")
            except (FileNotFoundError, OSError) as exc:
                logger.warning("Cannot read %s for regression risk analysis: %s", f, exc)

            # +0.2 per file touching __init__ or main
            basename = os.path.basename(f)
            if basename in ("__init__.py", "main.py"):
                risk_score += 0.2
                risk_factors.append(f"{f} touches {basename}")

            # +0.1 per test file changed
            if "test" in basename.lower():
                risk_score += 0.1
                risk_factors.append(f"{f} is a test file")

        # Clamp to [0, 1]
        risk_score = max(0.0, min(1.0, risk_score))
        risk_score = round(risk_score, 4)

        now = datetime.now(timezone.utc).isoformat()
        rec_id = uuid.uuid4().hex[:12]

        await self._db.execute(
            "INSERT INTO regression_predictions (id, pr_identifier, risk_score, risk_factors, files_changed, predicted_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (rec_id, pr_identifier, risk_score, json.dumps(risk_factors), json.dumps(files_changed), now),
        )

        return {
            "id": rec_id,
            "pr_identifier": pr_identifier,
            "risk_score": risk_score,
            "risk_factors": risk_factors,
            "files_changed": files_changed,
            "predicted_at": now,
        }

    # ------------------------------------------------------------------
    # Feature 31: Review Checklist Generation
    # ------------------------------------------------------------------

    async def generate_checklist(self, task_id: str) -> dict:
        """Generate a review checklist based on the task type."""
        await self._ensure_tables()
        task = await self._db.execute_fetchone(
            "SELECT * FROM tasks WHERE id = ?", (task_id,)
        )
        if not task:
            return {"error": "Task not found"}

        task_type = task.get("task_type") or "implementation"
        items = _CHECKLIST_TEMPLATES.get(task_type, _CHECKLIST_TEMPLATES["implementation"])

        now = datetime.now(timezone.utc).isoformat()
        rec_id = uuid.uuid4().hex[:12]

        await self._db.execute(
            "INSERT INTO review_checklists (id, task_id, task_type, checklist_items, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (rec_id, task_id, task_type, json.dumps(items), "pending", now),
        )

        return {
            "id": rec_id,
            "task_id": task_id,
            "task_type": task_type,
            "checklist_items": items,
            "status": "pending",
            "created_at": now,
        }

    # ------------------------------------------------------------------
    # Feature 32: Documentation Drift Detection
    # ------------------------------------------------------------------

    async def detect_doc_drift(
        self, doc_dir: str = "docs/", code_dir: str = "src/"
    ) -> list[dict]:
        """Detect documentation that references files or functions no longer present in code."""
        await self._ensure_tables()
        doc_root = Path(self._project_dir) / doc_dir
        code_root = Path(self._project_dir) / code_dir

        if not doc_root.exists():
            return []

        # Collect existing code files and function names
        existing_files: set[str] = set()
        existing_functions: set[str] = set()
        if code_root.exists():
            for py_file in code_root.rglob("*.py"):
                rel = str(py_file.relative_to(self._project_dir))
                existing_files.add(rel)
                try:
                    tree = ast.parse(py_file.read_text())
                    for node in ast.walk(tree):
                        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            existing_functions.add(node.name)
                except (SyntaxError, OSError) as exc:
                    logger.warning("Cannot parse %s for doc drift analysis: %s", py_file, exc)

        now = datetime.now(timezone.utc).isoformat()
        drifts: list[dict] = []

        # Patterns to find file references and function references in markdown
        file_ref_pattern = re.compile(r'(?:`|")([a-zA-Z0-9_/]+\.py)(?:`|")')
        func_ref_pattern = re.compile(r'(?:`|")([a-zA-Z_]\w+)\(\)(?:`|")')

        for md_file in doc_root.rglob("*.md"):
            try:
                content = md_file.read_text()
            except OSError as exc:
                logger.warning("Cannot read doc file %s: %s", md_file, exc)
                continue

            doc_rel = str(md_file.relative_to(self._project_dir))

            # Check file references
            for match in file_ref_pattern.finditer(content):
                ref_path = match.group(1)
                # Check if the referenced file exists anywhere in existing_files
                found = any(ref_path in ef for ef in existing_files)
                if not found:
                    rec_id = uuid.uuid4().hex[:12]
                    drift = {
                        "id": rec_id,
                        "doc_file": doc_rel,
                        "code_file": ref_path,
                        "drift_type": "missing_file",
                        "details": f"Referenced file '{ref_path}' not found in code",
                        "severity": "high",
                        "created_at": now,
                    }
                    await self._db.execute(
                        "INSERT INTO doc_drift_reports (id, doc_file, code_file, drift_type, details, severity, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (rec_id, doc_rel, ref_path, "missing_file", drift["details"], "high", now),
                    )
                    drifts.append(drift)

            # Check function references
            for match in func_ref_pattern.finditer(content):
                func_name = match.group(1)
                if func_name not in existing_functions:
                    rec_id = uuid.uuid4().hex[:12]
                    drift = {
                        "id": rec_id,
                        "doc_file": doc_rel,
                        "code_file": None,
                        "drift_type": "missing_function",
                        "details": f"Referenced function '{func_name}()' not found in code",
                        "severity": "medium",
                        "created_at": now,
                    }
                    await self._db.execute(
                        "INSERT INTO doc_drift_reports (id, doc_file, code_file, drift_type, details, severity, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (rec_id, doc_rel, None, "missing_function", drift["details"], "medium", now),
                    )
                    drifts.append(drift)

        return drifts

    # ------------------------------------------------------------------
    # Feature 33: Performance Regression Detection
    # ------------------------------------------------------------------

    async def record_test_timing(self, test_name: str, duration_ms: float) -> dict:
        """Record a test timing using a running average (UPSERT)."""
        await self._ensure_tables()
        now = datetime.now(timezone.utc).isoformat()

        existing = await self._db.execute_fetchone(
            "SELECT * FROM perf_baselines WHERE test_name = ?", (test_name,)
        )

        if existing:
            old_avg = existing["avg_duration_ms"]
            n = existing["sample_count"]
            new_n = n + 1
            new_avg = (old_avg * n + duration_ms) / new_n
            # Running variance using Welford's online algorithm
            old_std = existing["std_deviation_ms"]
            old_variance = old_std ** 2
            new_variance = (old_variance * n + (duration_ms - new_avg) * (duration_ms - old_avg)) / new_n
            new_std = math.sqrt(max(0.0, new_variance))

            await self._db.execute(
                "UPDATE perf_baselines SET avg_duration_ms = ?, std_deviation_ms = ?, sample_count = ?, last_updated = ? "
                "WHERE test_name = ?",
                (round(new_avg, 4), round(new_std, 4), new_n, now, test_name),
            )
            return {
                "test_name": test_name,
                "avg_duration_ms": round(new_avg, 4),
                "std_deviation_ms": round(new_std, 4),
                "sample_count": new_n,
                "last_updated": now,
            }
        else:
            rec_id = uuid.uuid4().hex[:12]
            await self._db.execute(
                "INSERT INTO perf_baselines (id, test_name, avg_duration_ms, std_deviation_ms, sample_count, last_updated) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (rec_id, test_name, duration_ms, 0.0, 1, now),
            )
            return {
                "test_name": test_name,
                "avg_duration_ms": duration_ms,
                "std_deviation_ms": 0.0,
                "sample_count": 1,
                "last_updated": now,
            }

    async def detect_perf_regressions(self, threshold_pct: float | None = None) -> list[dict]:
        """Return baselines where std_deviation exceeds avg * threshold_pct / 100."""
        if threshold_pct is None:
            threshold_pct = self.DEFAULT_REGRESSION_THRESHOLD_PCT
        await self._ensure_tables()
        rows = await self._db.execute_fetchall(
            "SELECT * FROM perf_baselines WHERE sample_count > 1 ORDER BY test_name"
        )
        regressions: list[dict] = []
        for row in rows:
            avg = row["avg_duration_ms"]
            std = row["std_deviation_ms"]
            if avg > 0 and std > avg * threshold_pct / 100.0:
                regressions.append({
                    "test_name": row["test_name"],
                    "avg_duration_ms": avg,
                    "std_deviation_ms": std,
                    "sample_count": row["sample_count"],
                    "regression_ratio": round(std / avg, 4) if avg > 0 else 0,
                })
        return regressions
