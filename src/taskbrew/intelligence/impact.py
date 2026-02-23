"""Impact analysis for code changes."""

from __future__ import annotations

import ast
import logging
import os
import re
from pathlib import Path

logger = logging.getLogger(__name__)


class ImpactAnalyzer:
    """Analyze impact of code changes by tracing dependencies."""

    def __init__(self, db, project_dir: str = ".") -> None:
        self._db = db
        self._project_dir = project_dir

    async def trace_dependencies(self, file_path: str) -> dict:
        """Trace import dependencies for a Python file."""
        full_path = os.path.join(self._project_dir, file_path)
        imports = []
        importers = []

        # Parse the file for imports
        try:
            with open(full_path) as f:
                tree = ast.parse(f.read())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        imports.append(alias.name)
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        imports.append(node.module)
        except (FileNotFoundError, SyntaxError):
            pass

        # Find files that import this module
        module_name = file_path.replace("/", ".").replace(".py", "")
        src_dir = Path(self._project_dir) / "src"
        if src_dir.exists():
            for py_file in src_dir.rglob("*.py"):
                try:
                    content = py_file.read_text()
                    base_name = file_path.split("/")[-1].replace(".py", "")
                    # Match actual import statements, not just substrings
                    import_pattern = re.compile(
                        rf'\b(?:import\s+(?:[\w.]*\.)?{re.escape(module_name)}\b|from\s+(?:[\w.]*\.)?{re.escape(module_name)}\s+import)'
                        rf'|\b(?:import\s+(?:[\w.]*\.)?{re.escape(base_name)}\b|from\s+(?:[\w.]*\.)?{re.escape(base_name)}\s+import)'
                    )
                    if import_pattern.search(content):
                        rel = str(py_file.relative_to(self._project_dir))
                        if rel != file_path:
                            importers.append(rel)
                except Exception:
                    pass

        return {
            "file": file_path,
            "imports": imports,
            "imported_by": importers,
            "blast_radius": len(importers),
        }

    async def analyze_blast_radius(self, files: list[str]) -> dict:
        """Analyze the blast radius of changing multiple files."""
        all_affected = set()
        file_impacts = []

        for f in files:
            impact = await self.trace_dependencies(f)
            file_impacts.append(impact)
            all_affected.update(impact["imported_by"])

        return {
            "files_changed": files,
            "total_affected": len(all_affected),
            "affected_files": sorted(all_affected),
            "per_file": file_impacts,
            "risk_level": "low" if len(all_affected) < 3 else "medium" if len(all_affected) < 10 else "high",
        }
