"""MCP tools for agent intelligence features.

Usage (as subprocess):
    python -m taskbrew.tools.intelligence_tools

Environment:
    TASKBREW_DB_PATH  Path to the SQLite database (default: data/tasks.db)
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)


def register_intelligence_tools(mcp_server, get_db):
    """Register intelligence MCP tools on the given FastMCP server.

    Parameters
    ----------
    mcp_server : FastMCP
        The MCP server instance to register tools on.
    get_db : callable
        A callable that returns the Database instance.
    """

    @mcp_server.tool()
    async def recall_memory(agent_role: str, query: str, memory_type: str = "", limit: int = 5) -> str:
        """Recall relevant memories for the current task context."""
        from taskbrew.intelligence.memory import MemoryManager
        db = get_db()
        mm = MemoryManager(db)
        memories = await mm.recall(
            agent_role=agent_role,
            query=query,
            memory_type=memory_type or None,
            limit=limit,
        )
        return json.dumps(memories, indent=2)

    @mcp_server.tool()
    async def store_lesson(agent_role: str, title: str, content: str, source_task_id: str = "") -> str:
        """Store a lesson learned from the current task."""
        from taskbrew.intelligence.memory import MemoryManager
        db = get_db()
        mm = MemoryManager(db)
        result = await mm.store_lesson(
            role=agent_role,
            title=title,
            content=content,
            source_task_id=source_task_id or None,
        )
        return json.dumps({"stored": True, "memory": result})

    @mcp_server.tool()
    async def check_impact(file_paths: str) -> str:
        """Check the impact/blast radius of modifying the given files.

        file_paths: comma-separated list of file paths
        """
        from taskbrew.intelligence.impact import ImpactAnalyzer
        db = get_db()
        analyzer = ImpactAnalyzer(db)
        paths = [p.strip() for p in file_paths.split(",") if p.strip()]
        results = {}
        for path in paths:
            deps = await analyzer.trace_dependencies(path)
            results[path] = deps
        return json.dumps(results, indent=2)

    @mcp_server.tool()
    async def get_project_context(agent_role: str, query: str = "", project_id: str = "") -> str:
        """Get accumulated project knowledge for the current context."""
        from taskbrew.intelligence.memory import MemoryManager
        db = get_db()
        mm = MemoryManager(db)
        context = await mm.get_project_context(
            role=agent_role,
            query=query or "project",
            project_id=project_id or None,
        )
        return json.dumps({"context": context})

    @mcp_server.tool()
    async def report_confidence(task_id: str, agent_role: str, output_text: str) -> str:
        """Analyze and report confidence level for the given output."""
        from taskbrew.intelligence.quality import QualityManager
        db = get_db()
        qm = QualityManager(db)
        confidence = await qm.score_confidence(task_id, agent_role, output_text)
        return json.dumps({"task_id": task_id, "confidence": confidence})


def build_intelligence_tools_server(db_path: str = "data/tasks.db"):
    """Build a FastMCP server with intelligence tools registered.

    Parameters
    ----------
    db_path : str
        Path to the SQLite database file.

    Returns
    -------
    FastMCP
        A ready-to-run MCP server with intelligence tools.
    """
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("intelligence-tools")
    _db_instance = None

    async def _get_db():
        nonlocal _db_instance
        if _db_instance is None:
            from taskbrew.orchestrator.database import Database
            _db_instance = Database(db_path)
            await _db_instance.initialize()
        return _db_instance

    # Wrap get_db as a sync callable that returns a coroutine-compatible
    # object.  The tool functions are async, so they can await the DB
    # initialization on first use.  However register_intelligence_tools
    # expects get_db to return the db directly (not a coroutine), so we
    # use a lazy wrapper that initialises synchronously via the event loop
    # on first call.

    class _LazyDB:
        """Lazy DB accessor that initializes on first use within an async context."""

        def __init__(self):
            self._db = None

        async def ensure_initialized(self):
            if self._db is None:
                from taskbrew.orchestrator.database import Database
                self._db = Database(db_path)
                await self._db.initialize()
            return self._db

    _lazy = _LazyDB()

    # Re-register tools directly on this server with async DB init
    @mcp.tool()
    async def recall_memory(agent_role: str, query: str, memory_type: str = "", limit: int = 5) -> str:
        """Recall relevant memories for the current task context."""
        from taskbrew.intelligence.memory import MemoryManager
        db = await _lazy.ensure_initialized()
        mm = MemoryManager(db)
        memories = await mm.recall(
            agent_role=agent_role,
            query=query,
            memory_type=memory_type or None,
            limit=limit,
        )
        return json.dumps(memories, indent=2)

    @mcp.tool()
    async def store_lesson(agent_role: str, title: str, content: str, source_task_id: str = "") -> str:
        """Store a lesson learned from the current task."""
        from taskbrew.intelligence.memory import MemoryManager
        db = await _lazy.ensure_initialized()
        mm = MemoryManager(db)
        result = await mm.store_lesson(
            role=agent_role,
            title=title,
            content=content,
            source_task_id=source_task_id or None,
        )
        return json.dumps({"stored": True, "memory": result})

    @mcp.tool()
    async def check_impact(file_paths: str) -> str:
        """Check the impact/blast radius of modifying the given files.

        file_paths: comma-separated list of file paths
        """
        from taskbrew.intelligence.impact import ImpactAnalyzer
        db = await _lazy.ensure_initialized()
        analyzer = ImpactAnalyzer(db)
        paths = [p.strip() for p in file_paths.split(",") if p.strip()]
        results = {}
        for path in paths:
            deps = await analyzer.trace_dependencies(path)
            results[path] = deps
        return json.dumps(results, indent=2)

    @mcp.tool()
    async def get_project_context(agent_role: str, query: str = "", project_id: str = "") -> str:
        """Get accumulated project knowledge for the current context."""
        from taskbrew.intelligence.memory import MemoryManager
        db = await _lazy.ensure_initialized()
        mm = MemoryManager(db)
        context = await mm.get_project_context(
            role=agent_role,
            query=query or "project",
            project_id=project_id or None,
        )
        return json.dumps({"context": context})

    @mcp.tool()
    async def report_confidence(task_id: str, agent_role: str, output_text: str) -> str:
        """Analyze and report confidence level for the given output."""
        from taskbrew.intelligence.quality import QualityManager
        db = await _lazy.ensure_initialized()
        qm = QualityManager(db)
        confidence = await qm.score_confidence(task_id, agent_role, output_text)
        return json.dumps({"task_id": task_id, "confidence": confidence})

    return mcp


if __name__ == "__main__":
    import os
    db_path = os.environ.get("TASKBREW_DB_PATH", "data/tasks.db")
    server = build_intelligence_tools_server(db_path=db_path)
    server.run(transport="stdio")
