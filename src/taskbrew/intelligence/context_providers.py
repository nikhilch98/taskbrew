"""Context providers: pluggable sources of agent context with caching."""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Protocol

logger = logging.getLogger(__name__)


class ContextProvider(Protocol):
    """Protocol for context providers."""
    name: str
    ttl_seconds: int

    async def gather(self, scope: str | None = None) -> str: ...


class ContextProviderRegistry:
    """Registry of context providers with caching via context_snapshots table."""

    def __init__(self, db, project_dir: str = ".") -> None:
        self._db = db
        self._project_dir = project_dir
        self._providers: dict[str, ContextProvider] = {}

    def register(self, provider: ContextProvider) -> None:
        self._providers[provider.name] = provider

    async def get_context(self, provider_names: list[str], scope: str | None = None) -> str:
        """Gather context from multiple providers, using cache when available."""
        parts = []
        for name in provider_names:
            provider = self._providers.get(name)
            if not provider:
                continue

            # Check cache
            cached = await self._db.execute_fetchone(
                "SELECT data, expires_at FROM context_snapshots "
                "WHERE context_type = ? AND (scope = ? OR (scope IS NULL AND ? IS NULL)) "
                "ORDER BY created_at DESC LIMIT 1",
                (name, scope, scope),
            )

            if cached and cached["expires_at"]:
                now = datetime.now(timezone.utc).isoformat()
                if cached["expires_at"] > now:
                    parts.append(cached["data"])
                    continue

            # Gather fresh context
            try:
                data = await provider.gather(scope)
                if data:
                    # Cache it
                    now = datetime.now(timezone.utc)
                    expires = (now + timedelta(seconds=provider.ttl_seconds)).isoformat()
                    await self._db.execute(
                        "INSERT INTO context_snapshots (context_type, scope, data, expires_at, created_at) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (name, scope, data, expires, now.isoformat()),
                    )
                    parts.append(data)
            except Exception:
                logger.warning("Context provider %s failed", name, exc_info=True)

        return "\n\n".join(parts) if parts else ""

    def get_available_providers(self) -> list[str]:
        return list(self._providers.keys())


class GitHistoryProvider:
    """Feature 25: Recent git history context."""
    name = "git_history"
    ttl_seconds = 300  # 5 minutes

    def __init__(self, project_dir: str) -> None:
        self._project_dir = project_dir

    async def gather(self, scope: str | None = None) -> str:
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                ["git", "log", "--oneline", "-20"],
                cwd=self._project_dir,
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode != 0:
                return ""

            branch_result = await asyncio.to_thread(
                subprocess.run,
                ["git", "branch", "--show-current"],
                cwd=self._project_dir,
                capture_output=True, text=True, timeout=5,
            )
            branch = branch_result.stdout.strip() if branch_result.returncode == 0 else "unknown"

            return f"## Git Context\nBranch: {branch}\n\nRecent commits:\n{result.stdout.strip()}"
        except Exception:
            return ""


class CoverageContextProvider:
    """Feature 26: Test coverage information."""
    name = "test_coverage"
    ttl_seconds = 600  # 10 minutes

    def __init__(self, project_dir: str) -> None:
        self._project_dir = project_dir

    async def gather(self, scope: str | None = None) -> str:
        # Look for coverage data files
        coverage_file = Path(self._project_dir) / ".coverage"
        coverage_xml = Path(self._project_dir) / "coverage.xml"
        htmlcov = Path(self._project_dir) / "htmlcov"

        parts = ["## Test Coverage"]
        if coverage_file.exists():
            parts.append(
                f"Coverage data exists (last modified: "
                f"{datetime.fromtimestamp(coverage_file.stat().st_mtime).isoformat()})"
            )
        elif coverage_xml.exists():
            parts.append("Coverage XML report available")
        elif htmlcov.exists():
            parts.append("HTML coverage report available")
        else:
            parts.append("No coverage data found. Run: pytest --cov to generate.")

        return "\n".join(parts)


class DependencyGraphProvider:
    """Feature 27: Project dependency information."""
    name = "dependency_graph"
    ttl_seconds = 3600  # 1 hour

    def __init__(self, project_dir: str) -> None:
        self._project_dir = project_dir

    async def gather(self, scope: str | None = None) -> str:
        parts = ["## Project Dependencies"]

        # Check pyproject.toml
        pyproject = Path(self._project_dir) / "pyproject.toml"
        if pyproject.exists():
            content = pyproject.read_text()
            # Extract dependencies section
            in_deps = False
            deps = []
            for line in content.split("\n"):
                if "dependencies" in line and "=" in line:
                    in_deps = True
                    continue
                if in_deps:
                    if line.strip().startswith("]"):
                        in_deps = False
                    elif line.strip().startswith('"'):
                        dep = line.strip().strip('",')
                        deps.append(dep)
            if deps:
                parts.append("Python dependencies: " + ", ".join(deps[:15]))

        # Check package.json
        pkg_json = Path(self._project_dir) / "package.json"
        if pkg_json.exists():
            try:
                data = json.loads(pkg_json.read_text())
                js_deps = list(data.get("dependencies", {}).keys())[:10]
                if js_deps:
                    parts.append("JS dependencies: " + ", ".join(js_deps))
            except Exception:
                pass

        return "\n".join(parts) if len(parts) > 1 else ""


class CrossTaskProvider:
    """Feature 28: Awareness of other in-progress tasks."""
    name = "cross_task"
    ttl_seconds = 60  # 1 minute

    def __init__(self, db) -> None:
        self._db = db

    async def gather(self, scope: str | None = None) -> str:
        tasks = await self._db.execute_fetchall(
            "SELECT id, title, assigned_to, claimed_by FROM tasks "
            "WHERE status = 'in_progress' ORDER BY started_at DESC LIMIT 10"
        )
        if not tasks:
            return ""

        parts = ["## Other Active Tasks"]
        for t in tasks:
            parts.append(f"- {t['id']}: {t['title']} (by {t.get('claimed_by') or t.get('assigned_to', '?')})")
        return "\n".join(parts)


class CICDProvider:
    """Feature 29: CI/CD configuration context."""
    name = "ci_cd"
    ttl_seconds = 3600  # 1 hour

    def __init__(self, project_dir: str) -> None:
        self._project_dir = project_dir

    async def gather(self, scope: str | None = None) -> str:
        parts = ["## CI/CD Configuration"]
        found = False

        # Check GitHub Actions
        workflows_dir = Path(self._project_dir) / ".github" / "workflows"
        if workflows_dir.exists():
            workflows = list(workflows_dir.glob("*.yml")) + list(workflows_dir.glob("*.yaml"))
            if workflows:
                parts.append("GitHub Actions workflows: " + ", ".join(w.name for w in workflows))
                found = True

        # Check Makefile
        makefile = Path(self._project_dir) / "Makefile"
        if makefile.exists():
            parts.append("Makefile present")
            found = True

        return "\n".join(parts) if found else ""


class DocumentationProvider:
    """Feature 30: Project documentation context."""
    name = "documentation"
    ttl_seconds = 1800  # 30 minutes

    def __init__(self, project_dir: str) -> None:
        self._project_dir = project_dir

    async def gather(self, scope: str | None = None) -> str:
        parts = ["## Project Documentation"]
        found = False

        readme = Path(self._project_dir) / "README.md"
        if readme.exists():
            content = readme.read_text()[:500]
            parts.append(f"README.md ({len(content)} chars preview): {content[:200]}...")
            found = True

        docs_dir = Path(self._project_dir) / "docs"
        if docs_dir.exists():
            doc_files = list(docs_dir.rglob("*.md"))[:10]
            if doc_files:
                parts.append(
                    f"Documentation files ({len(doc_files)}): "
                    + ", ".join(f.name for f in doc_files[:5])
                )
                found = True

        return "\n".join(parts) if found else ""


class IssueTrackerProvider:
    """Feature 31: Issue tracker context (reads from local task board)."""
    name = "issue_tracker"
    ttl_seconds = 300  # 5 minutes

    def __init__(self, db) -> None:
        self._db = db

    async def gather(self, scope: str | None = None) -> str:
        # Use the task board as the issue tracker
        pending = await self._db.execute_fetchall(
            "SELECT id, title, priority, assigned_to FROM tasks "
            "WHERE status = 'pending' ORDER BY priority DESC, created_at ASC LIMIT 10"
        )
        if not pending:
            return ""

        parts = ["## Pending Issues"]
        for t in pending:
            parts.append(f"- [{t['priority']}] {t['id']}: {t['title']} (-> {t.get('assigned_to', '?')})")
        return "\n".join(parts)


class RuntimeContextProvider:
    """Feature 32: Runtime context (recent errors, system status)."""
    name = "runtime"
    ttl_seconds = 120  # 2 minutes

    def __init__(self, db) -> None:
        self._db = db

    async def gather(self, scope: str | None = None) -> str:
        # Recent failed tasks
        failures = await self._db.execute_fetchall(
            "SELECT id, title, rejection_reason FROM tasks "
            "WHERE status = 'failed' ORDER BY completed_at DESC LIMIT 5"
        )
        # Recent escalations
        escalations = await self._db.execute_fetchall(
            "SELECT task_id, reason, severity FROM escalations "
            "WHERE status = 'open' ORDER BY created_at DESC LIMIT 5"
        )

        if not failures and not escalations:
            return ""

        parts = ["## Runtime Context"]
        if failures:
            parts.append("Recent failures:")
            for f in failures:
                reason = f.get("rejection_reason") or "unknown"
                parts.append(f"  - {f['id']}: {f['title']} ({reason[:80]})")
        if escalations:
            parts.append("Open escalations:")
            for e in escalations:
                parts.append(f"  - Task {e['task_id']}: {e['reason'][:80]} [{e['severity']}]")

        return "\n".join(parts)
