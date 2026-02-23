"""Knowledge graph builder using Python AST analysis."""

from __future__ import annotations

import ast
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

MAX_FILE_SIZE = 1_048_576  # 1 MB


class KnowledgeGraphBuilder:
    """Build and query a knowledge graph of code dependencies.

    Uses the ``knowledge_graph_nodes`` and ``knowledge_graph_edges`` tables.
    Node types: module, class, function, file
    Edge types: imports, contains, calls, inherits
    """

    def __init__(self, db, project_dir: str | None = None) -> None:
        self._db = db
        self._project_dir: str | None = project_dir

    def _safe_read(self, file_path: str) -> str | None:
        """Read a file safely -- restrict to project directory and enforce size limit."""
        if self._project_dir:
            project_root = Path(self._project_dir).resolve()
            full_path = (project_root / file_path).resolve()
            # Prevent path traversal
            try:
                full_path.relative_to(project_root)
            except ValueError:
                logger.warning("Path traversal attempt blocked: %s", file_path)
                return None
        else:
            full_path = Path(file_path).resolve()

        if not full_path.exists() or not full_path.is_file():
            return None

        if full_path.stat().st_size > MAX_FILE_SIZE:
            logger.warning(
                "File too large, skipping: %s (%d bytes)",
                file_path,
                full_path.stat().st_size,
            )
            return None

        return full_path.read_text()

    async def _upsert_node(
        self,
        node_type: str,
        name: str,
        file_path: str | None = None,
        description: str | None = None,
        metadata: dict | None = None,
    ) -> int:
        """Insert or update a node, return its ID."""
        now = datetime.now(timezone.utc).isoformat()
        meta_json = json.dumps(metadata) if metadata else None

        existing = await self._db.execute_fetchone(
            "SELECT id FROM knowledge_graph_nodes "
            "WHERE node_type = ? AND name = ? AND (file_path = ? OR (file_path IS NULL AND ? IS NULL))",
            (node_type, name, file_path, file_path),
        )

        if existing:
            await self._db.execute(
                "UPDATE knowledge_graph_nodes SET description = ?, metadata = ?, last_updated = ? WHERE id = ?",
                (description, meta_json, now, existing["id"]),
            )
            return existing["id"]
        else:
            rows = await self._db.execute_returning(
                "INSERT INTO knowledge_graph_nodes (node_type, name, file_path, description, metadata, last_updated) "
                "VALUES (?, ?, ?, ?, ?, ?) RETURNING id",
                (node_type, name, file_path, description, meta_json, now),
            )
            return rows[0]["id"]

    async def _add_edge(
        self, source_id: int, target_id: int, edge_type: str, weight: float = 1.0
    ) -> None:
        """Add an edge between two nodes (skip if duplicate)."""
        now = datetime.now(timezone.utc).isoformat()
        try:
            await self._db.execute(
                "INSERT INTO knowledge_graph_edges (source_id, target_id, edge_type, weight, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (source_id, target_id, edge_type, weight, now),
            )
        except Exception:
            pass  # Duplicate edge

    async def analyze_file(self, file_path: str, source_code: str | None = None) -> dict:
        """Analyze a Python file and add its nodes/edges to the graph.

        If *source_code* is not provided, reads from disk.
        Returns a summary of what was found.
        """
        if source_code is None:
            content = self._safe_read(file_path)
            if content is None:
                return {"error": f"File not found or inaccessible: {file_path}", "nodes": 0, "edges": 0}
            source_code = content

        try:
            tree = ast.parse(source_code)
        except SyntaxError as e:
            return {"error": f"Syntax error: {e}", "nodes": 0, "edges": 0}

        nodes_created = 0
        edges_created = 0

        # Create file node
        file_node_id = await self._upsert_node("file", os.path.basename(file_path), file_path)
        nodes_created += 1

        # Extract imports
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    mod_id = await self._upsert_node("module", alias.name)
                    await self._add_edge(file_node_id, mod_id, "imports")
                    nodes_created += 1
                    edges_created += 1
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    mod_id = await self._upsert_node("module", node.module)
                    await self._add_edge(file_node_id, mod_id, "imports")
                    nodes_created += 1
                    edges_created += 1

        # Extract classes and functions at module level
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.ClassDef):
                class_id = await self._upsert_node(
                    "class",
                    node.name,
                    file_path,
                    description=ast.get_docstring(node),
                    metadata={"lineno": node.lineno},
                )
                await self._add_edge(file_node_id, class_id, "contains")
                nodes_created += 1
                edges_created += 1

                # Check inheritance
                for base in node.bases:
                    if isinstance(base, ast.Name):
                        base_id = await self._upsert_node("class", base.id)
                        await self._add_edge(class_id, base_id, "inherits")
                        nodes_created += 1
                        edges_created += 1

                # Extract methods
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        func_id = await self._upsert_node(
                            "function",
                            f"{node.name}.{item.name}",
                            file_path,
                            description=ast.get_docstring(item),
                            metadata={"lineno": item.lineno, "class": node.name},
                        )
                        await self._add_edge(class_id, func_id, "contains")
                        nodes_created += 1
                        edges_created += 1

            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                func_id = await self._upsert_node(
                    "function",
                    node.name,
                    file_path,
                    description=ast.get_docstring(node),
                    metadata={"lineno": node.lineno},
                )
                await self._add_edge(file_node_id, func_id, "contains")
                nodes_created += 1
                edges_created += 1

        return {"file": file_path, "nodes": nodes_created, "edges": edges_created}

    async def build_from_directory(self, directory: str) -> dict:
        """Scan a directory for .py files and analyze each one."""
        total_nodes = 0
        total_edges = 0
        files_analyzed = 0

        for root, _dirs, files in os.walk(directory):
            # Skip common non-source directories
            if any(skip in root for skip in ["__pycache__", ".venv", "node_modules", ".git"]):
                continue
            for fname in files:
                if fname.endswith(".py"):
                    fpath = os.path.join(root, fname)
                    result = await self.analyze_file(fpath)
                    total_nodes += result.get("nodes", 0)
                    total_edges += result.get("edges", 0)
                    files_analyzed += 1

        return {
            "files_analyzed": files_analyzed,
            "total_nodes": total_nodes,
            "total_edges": total_edges,
        }

    async def query_dependencies(self, name: str) -> list[dict]:
        """Find what a node depends on (outgoing 'imports' edges)."""
        rows = await self._db.execute_fetchall(
            "SELECT n2.node_type, n2.name, n2.file_path, e.edge_type "
            "FROM knowledge_graph_nodes n1 "
            "JOIN knowledge_graph_edges e ON e.source_id = n1.id "
            "JOIN knowledge_graph_nodes n2 ON e.target_id = n2.id "
            "WHERE n1.name = ? AND e.edge_type = 'imports'",
            (name,),
        )
        return rows

    async def query_dependents(self, name: str) -> list[dict]:
        """Find what depends on a node (incoming 'imports' edges)."""
        rows = await self._db.execute_fetchall(
            "SELECT n1.node_type, n1.name, n1.file_path, e.edge_type "
            "FROM knowledge_graph_nodes n2 "
            "JOIN knowledge_graph_edges e ON e.target_id = n2.id "
            "JOIN knowledge_graph_nodes n1 ON e.source_id = n1.id "
            "WHERE n2.name = ? AND e.edge_type = 'imports'",
            (name,),
        )
        return rows

    async def get_module_summary(self, file_path: str) -> dict:
        """Get a summary of classes and functions in a file."""
        nodes = await self._db.execute_fetchall(
            "SELECT node_type, name, description FROM knowledge_graph_nodes "
            "WHERE file_path = ? ORDER BY node_type, name",
            (file_path,),
        )
        classes = [n for n in nodes if n["node_type"] == "class"]
        functions = [n for n in nodes if n["node_type"] == "function"]
        return {
            "file_path": file_path,
            "classes": classes,
            "functions": functions,
            "total_symbols": len(nodes),
        }

    async def get_graph_stats(self) -> dict:
        """Get overall graph statistics."""
        node_counts = await self._db.execute_fetchall(
            "SELECT node_type, COUNT(*) as count FROM knowledge_graph_nodes GROUP BY node_type"
        )
        edge_counts = await self._db.execute_fetchall(
            "SELECT edge_type, COUNT(*) as count FROM knowledge_graph_edges GROUP BY edge_type"
        )
        return {
            "nodes": {r["node_type"]: r["count"] for r in node_counts},
            "edges": {r["edge_type"]: r["count"] for r in edge_counts},
        }
