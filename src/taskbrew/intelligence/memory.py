"""Persistent agent memory: lessons, patterns, post-mortems, and style rules."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class MemoryManager:
    """Store and recall agent memories for learning across tasks.

    Manages five memory types:
    - lesson: General learnings from task execution
    - pattern: Reusable code/workflow patterns
    - failure: Post-mortem analysis of failures
    - style: Coding style preferences
    - preference: Project-specific knowledge
    """

    def __init__(self, db) -> None:
        self._db = db

    async def store_memory(
        self,
        agent_role: str,
        memory_type: str,
        title: str,
        content: str,
        source_task_id: str | None = None,
        tags: list[str] | None = None,
        project_id: str | None = None,
    ) -> dict:
        """Store a new memory. Returns the created memory dict."""
        now = datetime.now(timezone.utc).isoformat()
        tags_json = json.dumps(tags) if tags else None
        await self._db.execute(
            "INSERT INTO agent_memories "
            "(agent_role, memory_type, title, content, source_task_id, tags, project_id, created_at, last_accessed) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (agent_role, memory_type, title, content, source_task_id, tags_json, project_id, now, now),
        )
        return {
            "agent_role": agent_role,
            "memory_type": memory_type,
            "title": title,
            "content": content,
            "tags": tags,
            "created_at": now,
        }

    async def recall(
        self,
        agent_role: str,
        query: str,
        memory_type: str | None = None,
        limit: int = 5,
    ) -> list[dict]:
        """Recall memories matching a query. Uses LIKE search + relevance scoring.

        Updates access_count and last_accessed for returned memories.
        Results ordered by relevance_score * recency.
        """
        conditions = ["agent_role = ?"]
        params: list = [agent_role]

        if memory_type:
            conditions.append("memory_type = ?")
            params.append(memory_type)

        # Keyword-based search across title and content
        keywords = [w.strip() for w in query.split() if len(w.strip()) > 2]
        if keywords:
            keyword_clauses = []
            for kw in keywords[:5]:  # cap at 5 keywords
                keyword_clauses.append("(title LIKE ? OR content LIKE ? OR tags LIKE ?)")
                params.extend([f"%{kw}%", f"%{kw}%", f"%{kw}%"])
            conditions.append(f"({' OR '.join(keyword_clauses)})")

        where = " AND ".join(conditions)
        params.append(limit)

        memories = await self._db.execute_fetchall(
            f"SELECT * FROM agent_memories WHERE {where} "
            "ORDER BY relevance_score DESC, created_at DESC "
            "LIMIT ?",
            tuple(params),
        )

        # Update access tracking (batch update to avoid N+1)
        if memories:
            now = datetime.now(timezone.utc).isoformat()
            ids = [mem["id"] for mem in memories]
            placeholders = ",".join(["?" for _ in ids])
            await self._db.execute(
                f"UPDATE agent_memories SET access_count = access_count + 1, last_accessed = ? WHERE id IN ({placeholders})",
                (now, *ids),
            )

        return memories

    async def store_lesson(
        self, role: str, title: str, content: str, source_task_id: str | None = None, tags: list[str] | None = None
    ) -> dict:
        """Store a lesson learned from task execution."""
        return await self.store_memory(role, "lesson", title, content, source_task_id, tags)

    async def store_pattern(
        self, role: str, title: str, content: str, tags: list[str] | None = None
    ) -> dict:
        """Store a reusable pattern."""
        return await self.store_memory(role, "pattern", title, content, tags=tags)

    async def find_patterns(self, role: str, tags: list[str] | None = None, limit: int = 10) -> list[dict]:
        """Find patterns by role and optional tags."""
        if tags:
            tag_clauses = " OR ".join(["tags LIKE ?" for _ in tags])
            params = [role] + [f"%{t}%" for t in tags] + [limit]
            return await self._db.execute_fetchall(
                f"SELECT * FROM agent_memories WHERE agent_role = ? AND memory_type = 'pattern' "
                f"AND ({tag_clauses}) ORDER BY relevance_score DESC LIMIT ?",
                tuple(params),
            )
        return await self._db.execute_fetchall(
            "SELECT * FROM agent_memories WHERE agent_role = ? AND memory_type = 'pattern' "
            "ORDER BY relevance_score DESC LIMIT ?",
            (role, limit),
        )

    async def store_postmortem(
        self, task_id: str, role: str, analysis: str, root_cause: str, prevention: str
    ) -> dict:
        """Store a failure post-mortem."""
        content = json.dumps({
            "analysis": analysis,
            "root_cause": root_cause,
            "prevention": prevention,
        })
        return await self.store_memory(role, "failure", f"Post-mortem: {task_id}", content, source_task_id=task_id, tags=["failure", "post-mortem"])

    async def get_similar_failures(self, role: str, description: str, limit: int = 3) -> list[dict]:
        """Find similar past failures based on description keywords."""
        return await self.recall(role, description, memory_type="failure", limit=limit)

    async def store_style_rule(self, role: str, rule: str, source_file: str | None = None) -> dict:
        """Store a coding style rule."""
        tags = ["style"]
        if source_file:
            ext = source_file.rsplit(".", 1)[-1] if "." in source_file else ""
            if ext:
                tags.append(ext)
        return await self.store_memory(role, "style", f"Style: {rule[:80]}", rule, tags=tags)

    async def get_style_guide(self, role: str, file_extension: str | None = None, limit: int = 10) -> list[dict]:
        """Get style rules, optionally filtered by file extension."""
        if file_extension:
            return await self._db.execute_fetchall(
                "SELECT * FROM agent_memories WHERE agent_role = ? AND memory_type = 'style' "
                "AND tags LIKE ? ORDER BY relevance_score DESC LIMIT ?",
                (role, f"%{file_extension}%", limit),
            )
        return await self._db.execute_fetchall(
            "SELECT * FROM agent_memories WHERE agent_role = ? AND memory_type = 'style' "
            "ORDER BY relevance_score DESC LIMIT ?",
            (role, limit),
        )

    async def add_project_knowledge(self, role: str, title: str, content: str, tags: list[str] | None = None, project_id: str | None = None) -> dict:
        """Store project-specific knowledge."""
        return await self.store_memory(role, "preference", title, content, tags=tags, project_id=project_id)

    async def get_project_context(self, role: str, query: str, project_id: str | None = None, limit: int = 5) -> str:
        """Build a context string from relevant project knowledge."""
        memories = await self.recall(role, query, memory_type="preference", limit=limit)
        if project_id:
            # Also search for project-specific memories
            project_memories = await self._db.execute_fetchall(
                "SELECT * FROM agent_memories WHERE project_id = ? AND agent_role = ? "
                "ORDER BY relevance_score DESC LIMIT ?",
                (project_id, role, limit),
            )
            # Merge, deduplicate by id
            seen_ids = {m["id"] for m in memories}
            for pm in project_memories:
                if pm["id"] not in seen_ids:
                    memories.append(pm)

        if not memories:
            return ""

        parts = ["## Relevant Knowledge"]
        for m in memories[:limit]:
            parts.append(f"- **{m['title']}**: {m['content'][:200]}")
        return "\n".join(parts)

    async def decay_scores(self, age_days: int = 30) -> int:
        """Decay relevance scores for memories older than age_days. Returns count updated."""
        cutoff = datetime.now(timezone.utc).isoformat()
        result = await self._db.execute_fetchall(
            "SELECT id, relevance_score FROM agent_memories WHERE last_accessed < date(?, '-' || ? || ' days')",
            (cutoff, str(age_days)),
        )
        if not result:
            return 0
        # Batch update: group by new_score to minimize queries
        score_updates: dict[float, list[int]] = {}
        for row in result:
            new_score = max(0.1, round(row["relevance_score"] * 0.9, 10))
            score_updates.setdefault(new_score, []).append(row["id"])
        for new_score, ids in score_updates.items():
            placeholders = ",".join(["?" for _ in ids])
            await self._db.execute(
                f"UPDATE agent_memories SET relevance_score = ? WHERE id IN ({placeholders})",
                (new_score, *ids),
            )
        return len(result)

    async def get_memories(self, agent_role: str | None = None, memory_type: str | None = None, limit: int = 50) -> list[dict]:
        """List memories with optional filters."""
        conditions = []
        params: list = []
        if agent_role:
            conditions.append("agent_role = ?")
            params.append(agent_role)
        if memory_type:
            conditions.append("memory_type = ?")
            params.append(memory_type)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        params.append(limit)
        return await self._db.execute_fetchall(
            f"SELECT * FROM agent_memories {where} ORDER BY created_at DESC LIMIT ?",
            tuple(params),
        )

    async def delete_memory(self, memory_id: int) -> None:
        """Delete a memory by ID."""
        await self._db.execute("DELETE FROM agent_memories WHERE id = ?", (memory_id,))
