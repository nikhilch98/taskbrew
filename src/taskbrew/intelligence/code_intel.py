"""Advanced code intelligence: semantic search, pattern detection, smells, debt, test gaps, contracts, dead code."""

from __future__ import annotations

import ast
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


class CodeIntelligenceManager:
    """Analyze code quality, patterns, and structure."""

    def __init__(self, db, project_dir: str = ".") -> None:
        self._db = db
        self._project_dir = project_dir

    # --- Feature 6: Semantic Code Search ---

    async def index_file(self, file_path: str) -> int:
        """Parse a Python file and index all functions and classes.

        Returns the number of symbols indexed.
        """
        try:
            source = Path(file_path).read_text(errors="replace")
            tree = ast.parse(source, filename=file_path)
        except (SyntaxError, OSError) as exc:
            logger.warning("Cannot parse %s for indexing: %s", file_path, exc)
            return 0

        now = datetime.now(timezone.utc).isoformat()
        count = 0

        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                docstring = ast.get_docstring(node) or f"Class {node.name}"
                await self._upsert_embedding(
                    file_path, node.name, "class", docstring, now
                )
                count += 1
                # Index methods inside the class
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        method_doc = (
                            ast.get_docstring(item)
                            or f"Method {item.name} of {node.name}"
                        )
                        await self._upsert_embedding(
                            file_path,
                            f"{node.name}.{item.name}",
                            "method",
                            method_doc,
                            now,
                        )
                        count += 1
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # Top-level functions only (methods handled above)
                if not any(
                    isinstance(p, ast.ClassDef)
                    for p in _iter_parents(tree, node)
                ):
                    docstring = ast.get_docstring(node) or f"Function {node.name}"
                    await self._upsert_embedding(
                        file_path, node.name, "function", docstring, now
                    )
                    count += 1

        return count

    async def _upsert_embedding(
        self,
        file_path: str,
        symbol_name: str,
        symbol_type: str,
        description: str,
        now: str,
    ) -> None:
        """Insert or update a code_embeddings row."""
        existing = await self._db.execute_fetchone(
            "SELECT id FROM code_embeddings WHERE file_path = ? AND symbol_name = ?",
            (file_path, symbol_name),
        )
        if existing:
            await self._db.execute(
                "UPDATE code_embeddings SET symbol_type = ?, description = ?, "
                "last_updated = ? WHERE id = ?",
                (symbol_type, description, now, existing["id"]),
            )
        else:
            emb_id = f"CE-{uuid.uuid4().hex[:6]}"
            await self._db.execute(
                "INSERT INTO code_embeddings "
                "(id, file_path, symbol_name, symbol_type, embedding, description, last_updated) "
                "VALUES (?, ?, ?, ?, NULL, ?, ?)",
                (emb_id, file_path, symbol_name, symbol_type, description, now),
            )

    async def search_by_intent(self, query: str, limit: int = 10) -> list[dict]:
        """Search indexed symbols by keyword matching on description and name."""
        keywords = [k.strip() for k in query.lower().split() if k.strip()]
        if not keywords:
            return []

        conditions = []
        params: list = []
        for kw in keywords:
            conditions.append("(LOWER(description) LIKE ? OR LOWER(symbol_name) LIKE ?)")
            params.extend([f"%{kw}%", f"%{kw}%"])

        where = " OR ".join(conditions)
        params.append(limit)
        return await self._db.execute_fetchall(
            f"SELECT * FROM code_embeddings WHERE {where} "
            "ORDER BY last_updated DESC LIMIT ?",
            tuple(params),
        )

    # --- Feature 7: Architecture Pattern Detection ---

    async def detect_patterns(self, file_path: str) -> list[dict]:
        """Detect architecture patterns in a Python file via AST analysis.

        Patterns detected:
        - Singleton (class with ``_instance`` attribute)
        - Factory (method returning different types based on input)
        - Observer (classes with ``subscribe`` / ``notify`` methods)
        - Registry (class with a dict of registered items)
        """
        try:
            source = Path(file_path).read_text(errors="replace")
            tree = ast.parse(source, filename=file_path)
        except (SyntaxError, OSError) as exc:
            logger.warning("Cannot parse %s for pattern detection: %s", file_path, exc)
            return []

        now = datetime.now(timezone.utc).isoformat()
        patterns: list[dict] = []

        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue

            method_names = {
                item.name
                for item in node.body
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
            attr_names = set()
            for item in ast.walk(node):
                if isinstance(item, ast.Attribute):
                    attr_names.add(item.attr)
                if isinstance(item, ast.Name):
                    attr_names.add(item.id)

            # Singleton: has _instance attribute
            if "_instance" in attr_names:
                patterns.append(
                    self._make_pattern("singleton", file_path, node.name, now)
                )

            # Factory: method with conditional returns (if/match returning different things)
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    has_conditional_return = False
                    return_count = 0
                    for child in ast.walk(item):
                        if isinstance(child, ast.Return):
                            return_count += 1
                    if return_count >= 2:
                        for child in ast.walk(item):
                            if isinstance(child, (ast.If, ast.Match)):
                                has_conditional_return = True
                                break
                    if has_conditional_return:
                        patterns.append(
                            self._make_pattern(
                                "factory",
                                file_path,
                                f"{node.name}.{item.name}",
                                now,
                            )
                        )
                        break  # one per class

            # Observer: has subscribe and notify methods
            if "subscribe" in method_names and "notify" in method_names:
                patterns.append(
                    self._make_pattern("observer", file_path, node.name, now)
                )

            # Registry: class with register method and a dict attribute
            if "register" in method_names:
                has_dict = False
                for item in node.body:
                    if isinstance(item, ast.FunctionDef) and item.name == "__init__":
                        for child in ast.walk(item):
                            if isinstance(child, ast.Call):
                                func = child.func
                                if isinstance(func, ast.Name) and func.id == "dict":
                                    has_dict = True
                                if isinstance(func, ast.Attribute) and func.attr == "dict":
                                    has_dict = True
                            if isinstance(child, ast.Dict):
                                has_dict = True
                if has_dict:
                    patterns.append(
                        self._make_pattern("registry", file_path, node.name, now)
                    )

        # Persist patterns
        for p in patterns:
            await self._db.execute(
                "INSERT INTO architecture_patterns "
                "(id, pattern_type, file_path, details, severity, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    p["id"],
                    p["pattern_type"],
                    p["file_path"],
                    p["details"],
                    p["severity"],
                    p["created_at"],
                ),
            )

        return patterns

    def _make_pattern(
        self, pattern_type: str, file_path: str, detail: str, now: str
    ) -> dict:
        return {
            "id": f"AP-{uuid.uuid4().hex[:6]}",
            "pattern_type": pattern_type,
            "file_path": file_path,
            "details": detail,
            "severity": "info",
            "created_at": now,
        }

    async def get_patterns(
        self, pattern_type: str | None = None, limit: int = 50
    ) -> list[dict]:
        """Query detected architecture patterns, optionally filtered by type."""
        if pattern_type:
            return await self._db.execute_fetchall(
                "SELECT * FROM architecture_patterns WHERE pattern_type = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (pattern_type, limit),
            )
        return await self._db.execute_fetchall(
            "SELECT * FROM architecture_patterns ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )

    # --- Feature 8: Code Smell Detection ---

    async def detect_smells(self, file_path: str) -> list[dict]:
        """Detect code smells in a Python file.

        Smells detected:
        - God Class (class with > 10 methods)
        - Long Method (function with > 50 lines)
        - Too Many Parameters (function with > 5 params)
        - Deep Nesting (> 4 indent levels)
        """
        try:
            source = Path(file_path).read_text(errors="replace")
            tree = ast.parse(source, filename=file_path)
        except (SyntaxError, OSError) as exc:
            logger.warning("Cannot parse %s for smell detection: %s", file_path, exc)
            return []

        smells: list[dict] = []
        lines = source.splitlines()

        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                method_count = sum(
                    1
                    for item in node.body
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                )
                if method_count > 10:
                    smells.append(
                        {
                            "type": "god_class",
                            "location": f"{file_path}:{node.lineno}",
                            "severity": "high",
                            "detail": f"Class '{node.name}' has {method_count} methods",
                        }
                    )

            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # Long Method
                end_lineno = getattr(node, "end_lineno", None)
                if end_lineno:
                    func_lines = end_lineno - node.lineno + 1
                    if func_lines > 50:
                        smells.append(
                            {
                                "type": "long_method",
                                "location": f"{file_path}:{node.lineno}",
                                "severity": "medium",
                                "detail": f"Function '{node.name}' is {func_lines} lines",
                            }
                        )

                # Too Many Parameters
                params = node.args
                param_count = (
                    len(params.args)
                    + len(params.posonlyargs)
                    + len(params.kwonlyargs)
                )
                # Exclude 'self' and 'cls'
                if params.args and params.args[0].arg in ("self", "cls"):
                    param_count -= 1
                if param_count > 5:
                    smells.append(
                        {
                            "type": "too_many_parameters",
                            "location": f"{file_path}:{node.lineno}",
                            "severity": "medium",
                            "detail": (
                                f"Function '{node.name}' has {param_count} parameters"
                            ),
                        }
                    )

        # Deep nesting check (indent-based)
        for i, line in enumerate(lines, 1):
            if line.strip():
                indent = len(line) - len(line.lstrip())
                # Assume 4-space indent; >4 levels = >16 spaces
                if indent > 16:
                    smells.append(
                        {
                            "type": "deep_nesting",
                            "location": f"{file_path}:{i}",
                            "severity": "low",
                            "detail": f"Line {i} has {indent // 4} indent levels",
                        }
                    )

        return smells

    # --- Feature 9: Technical Debt Scoring ---

    async def score_debt(self, file_path: str) -> dict:
        """Calculate a technical debt score for a Python file.

        Score is normalized to [0, 1] where 1 = high debt.
        """
        try:
            source = Path(file_path).read_text(errors="replace")
            tree = ast.parse(source, filename=file_path)
        except (SyntaxError, OSError) as exc:
            logger.warning("Cannot parse %s for debt scoring: %s", file_path, exc)
            return {"file_path": file_path, "score": 0.0, "details": {}}

        lines = source.splitlines()
        total_lines = len(lines)

        # Cyclomatic complexity: count branching statements
        complexity = 0
        for node in ast.walk(tree):
            if isinstance(node, (ast.If, ast.For, ast.While, ast.Try)):
                complexity += 1

        # Function count and max function length
        func_count = 0
        max_func_length = 0
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                func_count += 1
                end_lineno = getattr(node, "end_lineno", None)
                if end_lineno:
                    length = end_lineno - node.lineno + 1
                    max_func_length = max(max_func_length, length)

        # Normalize components to [0, 1]
        complexity_score = min(complexity / 20.0, 1.0)
        length_score = min(total_lines / 500.0, 1.0)
        func_count_score = min(func_count / 20.0, 1.0)
        max_func_score = min(max_func_length / 100.0, 1.0)

        # Weighted sum
        score = round(
            0.3 * complexity_score
            + 0.2 * length_score
            + 0.2 * func_count_score
            + 0.3 * max_func_score,
            4,
        )

        details = {
            "cyclomatic_complexity": complexity,
            "total_lines": total_lines,
            "function_count": func_count,
            "max_function_length": max_func_length,
            "complexity_score": round(complexity_score, 4),
            "length_score": round(length_score, 4),
            "func_count_score": round(func_count_score, 4),
            "max_func_score": round(max_func_score, 4),
        }

        now = datetime.now(timezone.utc).isoformat()
        debt_id = f"TD-{uuid.uuid4().hex[:6]}"
        await self._db.execute(
            "INSERT INTO technical_debt "
            "(id, file_path, debt_type, score, details, trend, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (debt_id, file_path, "composite", score, json.dumps(details), "stable", now),
        )

        return {"file_path": file_path, "score": score, "details": details}

    async def get_debt_report(self, limit: int = 50) -> list[dict]:
        """Return technical debt records ordered by score descending."""
        rows = await self._db.execute_fetchall(
            "SELECT * FROM technical_debt ORDER BY score DESC LIMIT ?",
            (limit,),
        )
        for row in rows:
            if isinstance(row.get("details"), str):
                try:
                    row["details"] = json.loads(row["details"])
                except (json.JSONDecodeError, TypeError) as exc:
                    logger.warning("Failed to parse debt details JSON for %s: %s", row.get("file_path"), exc)
        return rows

    # --- Feature 10: Test Gap Analysis ---

    async def analyze_test_gaps(self, source_file: str) -> list[dict]:
        """Find functions in *source_file* that lack corresponding tests."""
        try:
            source = Path(source_file).read_text(errors="replace")
            tree = ast.parse(source, filename=source_file)
        except (SyntaxError, OSError) as exc:
            logger.warning("Cannot parse %s for test gap analysis: %s", source_file, exc)
            return []

        # Gather source function names
        source_funcs: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if not node.name.startswith("_"):
                    source_funcs.append(node.name)

        # Find corresponding test file
        src_path = Path(source_file)
        test_name = f"test_{src_path.name}"
        test_path = src_path.parent / test_name
        # Also check a tests/ sibling directory
        tests_dir = src_path.parent.parent / "tests" / test_name
        tested_funcs: set[str] = set()

        for tp in [test_path, tests_dir]:
            if tp.is_file():
                try:
                    test_source = tp.read_text(errors="replace")
                    test_tree = ast.parse(test_source, filename=str(tp))
                    for node in ast.walk(test_tree):
                        if isinstance(
                            node, (ast.FunctionDef, ast.AsyncFunctionDef)
                        ):
                            tested_funcs.add(node.name)
                except (SyntaxError, OSError) as exc:
                    logger.warning("Cannot parse test file %s: %s", tp, exc)

        now = datetime.now(timezone.utc).isoformat()
        gaps: list[dict] = []

        for func in source_funcs:
            # Check if any test name references this function
            has_test = any(func in tname for tname in tested_funcs)
            if not has_test:
                gap_id = f"TG-{uuid.uuid4().hex[:6]}"
                gap = {
                    "id": gap_id,
                    "file_path": source_file,
                    "function_name": func,
                    "gap_type": "untested_function",
                    "suggested_test": f"test_{func}",
                    "created_at": now,
                }
                await self._db.execute(
                    "INSERT INTO test_gaps "
                    "(id, file_path, function_name, gap_type, suggested_test, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (gap_id, source_file, func, "untested_function", f"test_{func}", now),
                )
                gaps.append(gap)

        return gaps

    # --- Feature 11: API Contract Validation ---

    async def validate_contracts(self, router_file: str) -> list[dict]:
        """Validate API contracts in a router file.

        Checks for route decorators and reports handler functions that
        use raw dict params or are missing type hints.
        """
        try:
            source = Path(router_file).read_text(errors="replace")
            tree = ast.parse(source, filename=router_file)
        except (SyntaxError, OSError) as exc:
            logger.warning("Cannot parse %s for contract validation: %s", router_file, exc)
            return []

        issues: list[dict] = []

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue

            # Check if this function has route decorators
            has_route = False
            for dec in node.decorator_list:
                dec_name = ""
                if isinstance(dec, ast.Call):
                    if isinstance(dec.func, ast.Attribute):
                        dec_name = dec.func.attr
                    elif isinstance(dec.func, ast.Name):
                        dec_name = dec.func.id
                elif isinstance(dec, ast.Attribute):
                    dec_name = dec.attr
                elif isinstance(dec, ast.Name):
                    dec_name = dec.id

                if dec_name in ("get", "post", "put", "delete", "patch", "route"):
                    has_route = True
                    break

            if not has_route:
                continue

            # Check parameters (skip 'self', 'cls', 'request')
            for arg in node.args.args:
                if arg.arg in ("self", "cls", "request"):
                    continue
                if arg.annotation is None:
                    issues.append(
                        {
                            "function": node.name,
                            "param": arg.arg,
                            "issue": "missing_type_hint",
                            "file": router_file,
                            "line": node.lineno,
                        }
                    )
                elif isinstance(arg.annotation, ast.Name) and arg.annotation.id == "dict":
                    issues.append(
                        {
                            "function": node.name,
                            "param": arg.arg,
                            "issue": "raw_dict_param",
                            "file": router_file,
                            "line": node.lineno,
                        }
                    )

            # Check return type annotation
            if node.returns is None:
                issues.append(
                    {
                        "function": node.name,
                        "param": None,
                        "issue": "missing_return_type",
                        "file": router_file,
                        "line": node.lineno,
                    }
                )

        return issues

    # --- Feature 12: Dead Code Detection ---

    async def detect_dead_code(self, directory: str = "src/") -> list[dict]:
        """Find functions that are never called from any other function.

        Excludes ``__init__``, ``__main__``, test files, and decorated functions
        (e.g. ``@app.get``).
        """
        dir_path = Path(directory)
        if not dir_path.is_dir():
            return []

        # Phase 1: collect all defined functions and all called names
        all_defs: list[dict] = []  # {"name", "file_path", "lineno", "decorated"}
        all_calls: set[str] = set()

        for py_file in dir_path.rglob("*.py"):
            if py_file.name.startswith("test_"):
                continue
            try:
                source = py_file.read_text(errors="replace")
                tree = ast.parse(source, filename=str(py_file))
            except (SyntaxError, OSError) as exc:
                logger.warning("Cannot parse %s for dead code analysis: %s", py_file, exc)
                continue

            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if node.name.startswith("__"):
                        continue
                    # Skip methods defined inside classes (called via self.method())
                    parents = list(_iter_parents(tree, node))
                    is_inside_class = any(isinstance(p, ast.ClassDef) for p in parents)
                    if is_inside_class:
                        continue
                    # Skip nested/inner functions (closures/helpers)
                    is_inside_function = any(
                        isinstance(p, (ast.FunctionDef, ast.AsyncFunctionDef))
                        for p in parents
                    )
                    if is_inside_function:
                        continue
                    decorated = len(node.decorator_list) > 0
                    all_defs.append(
                        {
                            "name": node.name,
                            "file_path": str(py_file),
                            "lineno": node.lineno,
                            "decorated": decorated,
                        }
                    )

                # Collect call targets
                if isinstance(node, ast.Call):
                    if isinstance(node.func, ast.Name):
                        all_calls.add(node.func.id)
                    elif isinstance(node.func, ast.Attribute):
                        all_calls.add(node.func.attr)

        # Phase 2: find unreferenced functions
        dead: list[dict] = []
        for defn in all_defs:
            if defn["decorated"]:
                continue
            if defn["name"] not in all_calls:
                dead.append(
                    {
                        "function_name": defn["name"],
                        "file_path": defn["file_path"],
                        "lineno": defn["lineno"],
                    }
                )

        return dead


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _iter_parents(tree: ast.AST, target: ast.AST):
    """Yield ancestor nodes of *target* in *tree*."""
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            if child is target:
                yield node
