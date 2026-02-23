"""Code reasoning: semantic search, dependency impact, style harmonization, refactoring detection, tech debt prioritization, API evolution, code narratives, invariant discovery."""

from __future__ import annotations

import json
import logging
import re
from collections import deque

from taskbrew.intelligence._utils import utcnow, new_id, validate_path, clamp

logger = logging.getLogger(__name__)


class CodeReasoningManager:
    """Analyze code structure, dependencies, style, and technical debt."""

    def __init__(self, db, project_dir: str = ".") -> None:
        self._db = db
        self._project_dir = project_dir

    # ------------------------------------------------------------------
    # Schema bootstrap
    # ------------------------------------------------------------------

    async def ensure_tables(self) -> None:
        await self._db.executescript("""
            CREATE TABLE IF NOT EXISTS semantic_index (
                id TEXT PRIMARY KEY,
                file_path TEXT NOT NULL,
                function_name TEXT NOT NULL,
                intent_description TEXT NOT NULL,
                keywords TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS dependency_graph (
                id TEXT PRIMARY KEY,
                source_file TEXT NOT NULL,
                target_file TEXT NOT NULL,
                dep_type TEXT NOT NULL DEFAULT 'import',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS impact_predictions (
                id TEXT PRIMARY KEY,
                changed_file TEXT NOT NULL,
                affected_files TEXT NOT NULL,
                max_depth INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS style_patterns (
                id TEXT PRIMARY KEY,
                pattern_name TEXT NOT NULL,
                category TEXT NOT NULL,
                example TEXT NOT NULL,
                file_path TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS refactoring_opportunities (
                id TEXT PRIMARY KEY,
                file_path TEXT NOT NULL,
                opportunity_type TEXT NOT NULL,
                description TEXT NOT NULL,
                priority TEXT NOT NULL DEFAULT 'medium',
                dismissed INTEGER NOT NULL DEFAULT 0,
                dismiss_reason TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS debt_items (
                id TEXT PRIMARY KEY,
                file_path TEXT NOT NULL,
                category TEXT NOT NULL,
                description TEXT NOT NULL,
                effort_estimate INTEGER NOT NULL,
                business_impact INTEGER NOT NULL,
                resolved INTEGER NOT NULL DEFAULT 0,
                resolution_notes TEXT,
                created_at TEXT NOT NULL,
                resolved_at TEXT
            );
            CREATE TABLE IF NOT EXISTS api_versions (
                id TEXT PRIMARY KEY,
                endpoint TEXT NOT NULL,
                method TEXT NOT NULL,
                version TEXT NOT NULL,
                schema_hash TEXT NOT NULL,
                breaking_change INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS code_narratives (
                id TEXT PRIMARY KEY,
                file_path TEXT NOT NULL,
                function_name TEXT NOT NULL,
                code_snippet TEXT,
                narrative_text TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS code_invariants (
                id TEXT PRIMARY KEY,
                file_path TEXT NOT NULL,
                function_name TEXT NOT NULL,
                invariant_expression TEXT NOT NULL,
                invariant_type TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
        """)

    # ------------------------------------------------------------------
    # Feature 17: Semantic Code Search
    # ------------------------------------------------------------------

    async def index_intent(
        self,
        file_path: str,
        function_name: str,
        intent_description: str,
        keywords: list[str] | str,
    ) -> dict:
        """Store a semantic description for a function."""
        file_path = validate_path(file_path)
        if isinstance(keywords, list):
            keywords_str = ",".join(keywords)
        else:
            keywords_str = keywords

        now = utcnow()
        item_id = f"SI-{new_id(8)}"
        await self._db.execute(
            "INSERT INTO semantic_index "
            "(id, file_path, function_name, intent_description, keywords, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (item_id, file_path, function_name, intent_description, keywords_str, now),
        )
        return {
            "id": item_id,
            "file_path": file_path,
            "function_name": function_name,
            "intent_description": intent_description,
            "keywords": keywords_str,
            "created_at": now,
        }

    async def search_by_intent(self, query: str, limit: int = 10) -> list[dict]:
        """Keyword match on intent_description and keywords."""
        tokens = [t.strip().lower() for t in query.split() if t.strip()]
        if not tokens:
            return []

        conditions = []
        params: list = []
        for token in tokens:
            conditions.append(
                "(LOWER(intent_description) LIKE ? OR LOWER(keywords) LIKE ?)"
            )
            params.extend([f"%{token}%", f"%{token}%"])

        where = " OR ".join(conditions)
        params.append(limit)
        return await self._db.execute_fetchall(
            f"SELECT * FROM semantic_index WHERE {where} "
            "ORDER BY created_at DESC LIMIT ?",
            tuple(params),
        )

    async def get_index_stats(self) -> list[dict]:
        """Count of indexed items grouped by file."""
        return await self._db.execute_fetchall(
            "SELECT file_path, COUNT(*) AS count FROM semantic_index "
            "GROUP BY file_path ORDER BY count DESC",
        )

    # ------------------------------------------------------------------
    # Feature 18: Dependency Impact Predictor
    # ------------------------------------------------------------------

    async def record_dependency(
        self, source_file: str, target_file: str, dep_type: str = "import"
    ) -> dict:
        """Store a dependency edge between two files."""
        source_file = validate_path(source_file)
        target_file = validate_path(target_file)
        now = utcnow()
        dep_id = f"DEP-{new_id(8)}"
        await self._db.execute(
            "INSERT INTO dependency_graph "
            "(id, source_file, target_file, dep_type, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (dep_id, source_file, target_file, dep_type, now),
        )
        return {
            "id": dep_id,
            "source_file": source_file,
            "target_file": target_file,
            "dep_type": dep_type,
            "created_at": now,
        }

    async def predict_impact(self, changed_file: str) -> dict:
        """BFS through dependency_graph to find affected files with depth."""
        changed_file = validate_path(changed_file)

        # Build adjacency list: source imports target means if target changes,
        # source is affected. So edges go target -> source.
        all_edges = await self._db.execute_fetchall(
            "SELECT source_file, target_file FROM dependency_graph",
        )
        reverse_adj: dict[str, list[str]] = {}
        for edge in all_edges:
            target = edge["target_file"]
            source = edge["source_file"]
            reverse_adj.setdefault(target, []).append(source)

        # BFS
        visited: dict[str, int] = {}
        queue: deque[tuple[str, int]] = deque()
        queue.append((changed_file, 0))
        visited[changed_file] = 0

        while queue:
            current, depth = queue.popleft()
            for neighbor in reverse_adj.get(current, []):
                if neighbor not in visited:
                    visited[neighbor] = depth + 1
                    queue.append((neighbor, depth + 1))

        # Remove the changed file itself from the results
        affected = [
            {"file": f, "depth": d}
            for f, d in visited.items()
            if f != changed_file
        ]
        affected.sort(key=lambda x: x["depth"])

        max_depth = max((a["depth"] for a in affected), default=0)

        # Persist prediction
        now = utcnow()
        pred_id = f"IP-{new_id(8)}"
        await self._db.execute(
            "INSERT INTO impact_predictions "
            "(id, changed_file, affected_files, max_depth, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (pred_id, changed_file, json.dumps(affected), max_depth, now),
        )

        return {
            "id": pred_id,
            "changed_file": changed_file,
            "affected": affected,
            "max_depth": max_depth,
            "created_at": now,
        }

    async def get_impact_history(self, limit: int = 20) -> list[dict]:
        """Return past impact predictions."""
        rows = await self._db.execute_fetchall(
            "SELECT * FROM impact_predictions ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
        for row in rows:
            if isinstance(row.get("affected_files"), str):
                try:
                    row["affected_files"] = json.loads(row["affected_files"])
                except (json.JSONDecodeError, TypeError):
                    pass
        return rows

    # ------------------------------------------------------------------
    # Feature 19: Code Style Harmonizer
    # ------------------------------------------------------------------

    async def record_pattern(
        self,
        pattern_name: str,
        category: str,
        example: str,
        file_path: str | None = None,
    ) -> dict:
        """Record an observed code style pattern."""
        now = utcnow()
        pat_id = f"SP-{new_id(8)}"
        await self._db.execute(
            "INSERT INTO style_patterns "
            "(id, pattern_name, category, example, file_path, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (pat_id, pattern_name, category, example, file_path, now),
        )
        return {
            "id": pat_id,
            "pattern_name": pattern_name,
            "category": category,
            "example": example,
            "file_path": file_path,
            "created_at": now,
        }

    async def get_patterns(self, category: str | None = None) -> list[dict]:
        """List recorded style patterns, optionally filtered by category."""
        if category:
            return await self._db.execute_fetchall(
                "SELECT * FROM style_patterns WHERE category = ? "
                "ORDER BY created_at DESC",
                (category,),
            )
        return await self._db.execute_fetchall(
            "SELECT * FROM style_patterns ORDER BY created_at DESC",
        )

    async def check_conformance(self, file_path: str, content: str) -> list[dict]:
        """Check content against recorded patterns and return violations."""
        file_path = validate_path(file_path)
        patterns = await self._db.execute_fetchall(
            "SELECT * FROM style_patterns",
        )
        violations: list[dict] = []
        for pat in patterns:
            example = pat["example"]
            pattern_name = pat["pattern_name"]
            category = pat["category"]

            # Check if the pattern's example represents a naming convention
            if category == "naming":
                # For naming patterns, check if the content follows the convention
                # e.g., pattern example "snake_case" - check for camelCase violations
                if example == "snake_case":
                    # Find camelCase identifiers (simple heuristic)
                    camel_matches = re.findall(r'\b[a-z]+[A-Z][a-zA-Z]*\b', content)
                    if camel_matches:
                        violations.append({
                            "pattern_name": pattern_name,
                            "category": category,
                            "description": f"Found camelCase identifiers: {', '.join(camel_matches[:5])}",
                            "file_path": file_path,
                        })
            elif category == "formatting":
                if example == "trailing_comma" and content.strip():
                    # Check for multi-line lists/dicts without trailing commas
                    lines = content.splitlines()
                    for i, line in enumerate(lines):
                        stripped = line.rstrip()
                        if stripped.endswith((",", "[", "(", "{", ":")):
                            continue
                        # Check if next line starts a closing bracket
                        if i + 1 < len(lines):
                            next_stripped = lines[i + 1].strip()
                            if next_stripped in (")", "]", "}") and stripped and not stripped.endswith(","):
                                if not stripped.endswith(("{", "[", "(", ":")):
                                    violations.append({
                                        "pattern_name": pattern_name,
                                        "category": category,
                                        "description": f"Missing trailing comma at line {i + 1}",
                                        "file_path": file_path,
                                    })
            elif category == "imports":
                if example == "absolute_imports":
                    # Check for relative imports
                    relative_imports = re.findall(r'^\s*from\s+\.', content, re.MULTILINE)
                    if relative_imports:
                        violations.append({
                            "pattern_name": pattern_name,
                            "category": category,
                            "description": f"Found {len(relative_imports)} relative import(s)",
                            "file_path": file_path,
                        })

        return violations

    # ------------------------------------------------------------------
    # Feature 20: Refactoring Opportunity Detector
    # ------------------------------------------------------------------

    async def detect_opportunities(
        self, file_path: str, content: str | None = None
    ) -> list[dict]:
        """Scan for refactoring opportunities in the given file content.

        Detects:
        - Long methods (>50 lines)
        - Deep nesting (>4 levels)
        - Large classes (>500 lines)
        - Duplicate blocks (repeated lines)
        """
        file_path = validate_path(file_path)
        if content is None:
            return []

        now = utcnow()
        opportunities: list[dict] = []
        lines = content.splitlines()

        # Detect long methods: simple heuristic for def/async def blocks
        current_func: str | None = None
        func_lines: int = 0

        for i, line in enumerate(lines):
            stripped = line.strip()
            if re.match(r'^(async\s+)?def\s+\w+', stripped):
                # Close previous function if any
                if current_func and func_lines > 50:
                    opp = self._make_opportunity(
                        file_path, "long_method",
                        f"Function '{current_func}' is {func_lines} lines (>50)",
                        "high", now,
                    )
                    opportunities.append(opp)
                current_func = re.match(r'^(?:async\s+)?def\s+(\w+)', stripped).group(1)
                func_lines = 0
            if current_func is not None:
                func_lines += 1

        # Handle last function
        if current_func and func_lines > 50:
            opp = self._make_opportunity(
                file_path, "long_method",
                f"Function '{current_func}' is {func_lines} lines (>50)",
                "high", now,
            )
            opportunities.append(opp)

        # Detect deep nesting (>4 levels, assuming 4-space indent)
        for i, line in enumerate(lines, 1):
            if line.strip():
                indent = len(line) - len(line.lstrip())
                nesting_level = indent // 4
                if nesting_level > 4:
                    opp = self._make_opportunity(
                        file_path, "deep_nesting",
                        f"Line {i} has nesting level {nesting_level} (>4)",
                        "medium", now,
                    )
                    opportunities.append(opp)
                    break  # Report once per file

        # Detect large classes: track class blocks
        class_name: str | None = None
        class_lines: int = 0
        for i, line in enumerate(lines):
            stripped = line.strip()
            if re.match(r'^class\s+\w+', stripped):
                if class_name and class_lines > 500:
                    opp = self._make_opportunity(
                        file_path, "large_class",
                        f"Class '{class_name}' is {class_lines} lines (>500)",
                        "high", now,
                    )
                    opportunities.append(opp)
                class_name = re.match(r'^class\s+(\w+)', stripped).group(1)
                class_lines = 0
            if class_name is not None:
                class_lines += 1

        if class_name and class_lines > 500:
            opp = self._make_opportunity(
                file_path, "large_class",
                f"Class '{class_name}' is {class_lines} lines (>500)",
                "high", now,
            )
            opportunities.append(opp)

        # Detect duplicate blocks (3+ consecutive identical lines appearing more than once)
        block_size = 3
        seen_blocks: dict[str, int] = {}
        for i in range(len(lines) - block_size + 1):
            block = "\n".join(line.strip() for line in lines[i:i + block_size])
            if block.strip():
                seen_blocks[block] = seen_blocks.get(block, 0) + 1

        for block, count in seen_blocks.items():
            if count > 1:
                first_line = block.split("\n")[0][:60]
                opp = self._make_opportunity(
                    file_path, "duplicate_block",
                    f"Block starting with '{first_line}...' appears {count} times",
                    "medium", now,
                )
                opportunities.append(opp)
                break  # Report once

        # Persist opportunities
        for opp in opportunities:
            await self._db.execute(
                "INSERT INTO refactoring_opportunities "
                "(id, file_path, opportunity_type, description, priority, dismissed, created_at) "
                "VALUES (?, ?, ?, ?, ?, 0, ?)",
                (opp["id"], opp["file_path"], opp["opportunity_type"],
                 opp["description"], opp["priority"], opp["created_at"]),
            )

        return opportunities

    def _make_opportunity(
        self, file_path: str, opp_type: str, description: str,
        priority: str, now: str,
    ) -> dict:
        return {
            "id": f"RO-{new_id(8)}",
            "file_path": file_path,
            "opportunity_type": opp_type,
            "description": description,
            "priority": priority,
            "dismissed": 0,
            "created_at": now,
        }

    async def get_opportunities(
        self,
        file_path: str | None = None,
        priority: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        """List refactoring opportunities, optionally filtered."""
        conditions = ["dismissed = 0"]
        params: list = []

        if file_path:
            conditions.append("file_path = ?")
            params.append(file_path)
        if priority:
            conditions.append("priority = ?")
            params.append(priority)

        where = " AND ".join(conditions)
        params.append(limit)
        return await self._db.execute_fetchall(
            f"SELECT * FROM refactoring_opportunities WHERE {where} "
            "ORDER BY created_at DESC LIMIT ?",
            tuple(params),
        )

    async def dismiss_opportunity(
        self, opportunity_id: str, reason: str | None = None
    ) -> dict:
        """Mark a refactoring opportunity as dismissed."""
        await self._db.execute(
            "UPDATE refactoring_opportunities SET dismissed = 1, dismiss_reason = ? "
            "WHERE id = ?",
            (reason, opportunity_id),
        )
        return {"id": opportunity_id, "dismissed": True, "reason": reason}

    # ------------------------------------------------------------------
    # Feature 21: Technical Debt Prioritizer
    # ------------------------------------------------------------------

    async def add_debt(
        self,
        file_path: str,
        category: str,
        description: str,
        effort_estimate: int,
        business_impact: int,
    ) -> dict:
        """Add a technical debt item with effort (1-5) and business impact (1-5)."""
        file_path = validate_path(file_path)
        effort_estimate = int(clamp(float(effort_estimate), 1.0, 5.0))
        business_impact = int(clamp(float(business_impact), 1.0, 5.0))

        now = utcnow()
        debt_id = f"DI-{new_id(8)}"
        await self._db.execute(
            "INSERT INTO debt_items "
            "(id, file_path, category, description, effort_estimate, business_impact, "
            "resolved, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 0, ?)",
            (debt_id, file_path, category, description, effort_estimate, business_impact, now),
        )
        return {
            "id": debt_id,
            "file_path": file_path,
            "category": category,
            "description": description,
            "effort_estimate": effort_estimate,
            "business_impact": business_impact,
            "resolved": 0,
            "created_at": now,
        }

    async def get_prioritized_debt(self, limit: int = 20) -> list[dict]:
        """Return unresolved debt sorted by impact/effort ratio (highest first)."""
        return await self._db.execute_fetchall(
            "SELECT *, "
            "CAST(business_impact AS REAL) / CAST(effort_estimate AS REAL) AS priority_ratio "
            "FROM debt_items WHERE resolved = 0 "
            "ORDER BY priority_ratio DESC LIMIT ?",
            (limit,),
        )

    async def resolve_debt(
        self, debt_id: str, resolution_notes: str | None = None
    ) -> dict:
        """Mark a debt item as resolved."""
        now = utcnow()
        await self._db.execute(
            "UPDATE debt_items SET resolved = 1, resolution_notes = ?, resolved_at = ? "
            "WHERE id = ?",
            (resolution_notes, now, debt_id),
        )
        return {"id": debt_id, "resolved": True, "resolution_notes": resolution_notes, "resolved_at": now}

    # ------------------------------------------------------------------
    # Feature 22: API Evolution Tracker
    # ------------------------------------------------------------------

    async def record_api_version(
        self,
        endpoint: str,
        method: str,
        version: str,
        schema_hash: str,
        breaking_change: bool = False,
    ) -> dict:
        """Track a new API version."""
        now = utcnow()
        ver_id = f"AV-{new_id(8)}"
        await self._db.execute(
            "INSERT INTO api_versions "
            "(id, endpoint, method, version, schema_hash, breaking_change, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (ver_id, endpoint, method, version, schema_hash, int(breaking_change), now),
        )
        return {
            "id": ver_id,
            "endpoint": endpoint,
            "method": method,
            "version": version,
            "schema_hash": schema_hash,
            "breaking_change": breaking_change,
            "created_at": now,
        }

    async def detect_breaking_changes(self, endpoint: str) -> list[dict]:
        """List breaking changes for a given endpoint."""
        return await self._db.execute_fetchall(
            "SELECT * FROM api_versions WHERE endpoint = ? AND breaking_change = 1 "
            "ORDER BY created_at DESC",
            (endpoint,),
        )

    async def get_api_changelog(self, limit: int = 20) -> list[dict]:
        """Return recent API version changes."""
        return await self._db.execute_fetchall(
            "SELECT * FROM api_versions ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )

    # ------------------------------------------------------------------
    # Feature 23: Code Narrative Generator
    # ------------------------------------------------------------------

    async def generate_narrative(
        self,
        file_path: str,
        function_name: str,
        code_snippet: str,
        narrative_text: str,
    ) -> dict:
        """Store a human-readable explanation for a code snippet."""
        file_path = validate_path(file_path)
        now = utcnow()
        narr_id = f"CN-{new_id(8)}"
        await self._db.execute(
            "INSERT INTO code_narratives "
            "(id, file_path, function_name, code_snippet, narrative_text, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (narr_id, file_path, function_name, code_snippet, narrative_text, now),
        )
        return {
            "id": narr_id,
            "file_path": file_path,
            "function_name": function_name,
            "code_snippet": code_snippet,
            "narrative_text": narrative_text,
            "created_at": now,
        }

    async def get_narrative(
        self, file_path: str, function_name: str | None = None
    ) -> list[dict]:
        """Retrieve narratives for a file, optionally filtered by function."""
        file_path = validate_path(file_path)
        if function_name:
            return await self._db.execute_fetchall(
                "SELECT * FROM code_narratives "
                "WHERE file_path = ? AND function_name = ? "
                "ORDER BY created_at DESC",
                (file_path, function_name),
            )
        return await self._db.execute_fetchall(
            "SELECT * FROM code_narratives WHERE file_path = ? "
            "ORDER BY created_at DESC",
            (file_path,),
        )

    async def search_narratives(self, query: str, limit: int = 10) -> list[dict]:
        """Keyword search across narrative text."""
        tokens = [t.strip().lower() for t in query.split() if t.strip()]
        if not tokens:
            return []

        conditions = []
        params: list = []
        for token in tokens:
            conditions.append("LOWER(narrative_text) LIKE ?")
            params.extend([f"%{token}%"])

        where = " OR ".join(conditions)
        params.append(limit)
        return await self._db.execute_fetchall(
            f"SELECT * FROM code_narratives WHERE {where} "
            "ORDER BY created_at DESC LIMIT ?",
            tuple(params),
        )

    # ------------------------------------------------------------------
    # Feature 24: Invariant Discoverer
    # ------------------------------------------------------------------

    async def record_invariant(
        self,
        file_path: str,
        function_name: str,
        invariant_expression: str,
        invariant_type: str,
    ) -> dict:
        """Store a code invariant (precondition, postcondition, loop_invariant)."""
        file_path = validate_path(file_path)
        valid_types = ("precondition", "postcondition", "loop_invariant")
        if invariant_type not in valid_types:
            raise ValueError(
                f"invariant_type must be one of {valid_types}, got {invariant_type!r}"
            )

        now = utcnow()
        inv_id = f"INV-{new_id(8)}"
        await self._db.execute(
            "INSERT INTO code_invariants "
            "(id, file_path, function_name, invariant_expression, invariant_type, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (inv_id, file_path, function_name, invariant_expression, invariant_type, now),
        )
        return {
            "id": inv_id,
            "file_path": file_path,
            "function_name": function_name,
            "invariant_expression": invariant_expression,
            "invariant_type": invariant_type,
            "created_at": now,
        }

    async def get_invariants(
        self, file_path: str | None = None, function_name: str | None = None
    ) -> list[dict]:
        """List invariants, optionally filtered by file and/or function."""
        conditions = []
        params: list = []

        if file_path:
            conditions.append("file_path = ?")
            params.append(validate_path(file_path))
        if function_name:
            conditions.append("function_name = ?")
            params.append(function_name)

        if conditions:
            where = "WHERE " + " AND ".join(conditions)
        else:
            where = ""

        return await self._db.execute_fetchall(
            f"SELECT * FROM code_invariants {where} ORDER BY created_at DESC",
            tuple(params),
        )

    async def check_invariant_violations(self, file_path: str) -> list[dict]:
        """Find invariants that may be violated based on recent changes.

        A simple heuristic: checks if the invariant expression references
        variables or functions that no longer appear in the file content.
        Falls back to returning the invariant as potentially violated if
        the file cannot be read.
        """
        file_path = validate_path(file_path)
        invariants = await self._db.execute_fetchall(
            "SELECT * FROM code_invariants WHERE file_path = ?",
            (file_path,),
        )

        if not invariants:
            return []

        # Try to read the file
        from pathlib import Path
        full_path = Path(self._project_dir) / file_path
        try:
            content = full_path.read_text(errors="replace")
        except OSError:
            # Cannot read file: all invariants are potentially violated
            return [
                {
                    "invariant_id": inv["id"],
                    "file_path": file_path,
                    "function_name": inv["function_name"],
                    "invariant_expression": inv["invariant_expression"],
                    "reason": "File not readable; invariant cannot be verified",
                }
                for inv in invariants
            ]

        violations: list[dict] = []
        for inv in invariants:
            expr = inv["invariant_expression"]
            # Extract identifiers from the expression
            identifiers = re.findall(r'[a-zA-Z_]\w*', expr)
            # Check if any key identifier is missing from the file
            for ident in identifiers:
                if len(ident) > 2 and ident not in content:
                    violations.append({
                        "invariant_id": inv["id"],
                        "file_path": file_path,
                        "function_name": inv["function_name"],
                        "invariant_expression": expr,
                        "reason": f"Identifier '{ident}' not found in file",
                    })
                    break

        return violations
