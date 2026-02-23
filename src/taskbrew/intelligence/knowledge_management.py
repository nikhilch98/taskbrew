"""Knowledge management: decay tracking, documentation gap detection,
institutional knowledge extraction, and context compression."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from taskbrew.intelligence._utils import utcnow, new_id, clamp

logger = logging.getLogger(__name__)


class KnowledgeManager:
    """Manage organisational knowledge lifecycle and context compression."""

    def __init__(self, db, project_dir: str = ".") -> None:
        self._db = db
        self._project_dir = project_dir

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    async def ensure_tables(self) -> None:
        await self._db.executescript("""
            CREATE TABLE IF NOT EXISTS knowledge_entries (
                id TEXT PRIMARY KEY,
                key TEXT NOT NULL,
                content TEXT NOT NULL,
                source_file TEXT,
                source_agent TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS knowledge_staleness (
                id TEXT PRIMARY KEY,
                entry_id TEXT NOT NULL REFERENCES knowledge_entries(id),
                flagged_at TEXT NOT NULL,
                reason TEXT,
                resolved INTEGER NOT NULL DEFAULT 0,
                resolved_at TEXT
            );
            CREATE TABLE IF NOT EXISTS doc_gaps (
                id TEXT PRIMARY KEY,
                symbol_name TEXT NOT NULL,
                symbol_type TEXT NOT NULL,
                file_path TEXT NOT NULL,
                severity TEXT NOT NULL DEFAULT 'medium',
                doc_reference TEXT,
                resolved INTEGER NOT NULL DEFAULT 0,
                resolved_at TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS institutional_knowledge (
                id TEXT PRIMARY KEY,
                source_type TEXT NOT NULL,
                source_ref TEXT,
                content TEXT NOT NULL,
                tags TEXT,
                file_path TEXT,
                line_number INTEGER,
                author TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS compression_profiles (
                id TEXT PRIMARY KEY,
                task_id TEXT,
                original_tokens INTEGER NOT NULL,
                compressed_tokens INTEGER NOT NULL,
                items_kept INTEGER NOT NULL,
                items_dropped INTEGER NOT NULL,
                strategy TEXT NOT NULL DEFAULT 'salience',
                created_at TEXT NOT NULL
            );
        """)

    # ------------------------------------------------------------------
    # Feature 45: Knowledge Decay Tracker
    # ------------------------------------------------------------------

    async def track_knowledge(
        self,
        key: str,
        content: str,
        source_file: str | None = None,
        source_agent: str | None = None,
    ) -> dict:
        """Store a knowledge entry with a timestamp."""
        now = utcnow()
        entry_id = f"KE-{new_id(8)}"
        await self._db.execute(
            "INSERT INTO knowledge_entries "
            "(id, key, content, source_file, source_agent, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (entry_id, key, content, source_file, source_agent, now, now),
        )
        return {
            "id": entry_id,
            "key": key,
            "content": content,
            "source_file": source_file,
            "source_agent": source_agent,
            "created_at": now,
            "updated_at": now,
        }

    async def check_staleness(self, max_age_days: int = 30) -> list[dict]:
        """Find entries older than *max_age_days* and flag them as stale.

        Also cross-references with source files that have been modified more
        recently than the knowledge entry.
        """
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=max_age_days)
        ).isoformat()
        now = utcnow()

        old_entries = await self._db.execute_fetchall(
            "SELECT * FROM knowledge_entries WHERE updated_at < ?",
            (cutoff,),
        )

        flagged: list[dict] = []
        for entry in old_entries:
            # Check if already flagged and unresolved
            existing = await self._db.execute_fetchone(
                "SELECT id FROM knowledge_staleness "
                "WHERE entry_id = ? AND resolved = 0",
                (entry["id"],),
            )
            if existing:
                continue

            reason = f"Not updated in over {max_age_days} days"

            # Cross-reference source file modification time
            if entry.get("source_file"):
                src = Path(self._project_dir) / entry["source_file"]
                try:
                    if src.exists():
                        mtime = datetime.fromtimestamp(
                            src.stat().st_mtime, tz=timezone.utc
                        ).isoformat()
                        if mtime > entry["updated_at"]:
                            reason += "; source file modified since last update"
                except OSError:
                    pass

            flag_id = f"KS-{new_id(8)}"
            await self._db.execute(
                "INSERT INTO knowledge_staleness "
                "(id, entry_id, flagged_at, reason, resolved) "
                "VALUES (?, ?, ?, ?, 0)",
                (flag_id, entry["id"], now, reason),
            )
            flagged.append({
                "id": flag_id,
                "entry_id": entry["id"],
                "key": entry["key"],
                "flagged_at": now,
                "reason": reason,
            })

        return flagged

    async def refresh_knowledge(
        self, entry_id: str, new_content: str | None = None
    ) -> dict | None:
        """Update the timestamp (and optionally content) of a knowledge entry.

        Also resolves any outstanding staleness flags for the entry.
        """
        now = utcnow()
        entry = await self._db.execute_fetchone(
            "SELECT * FROM knowledge_entries WHERE id = ?", (entry_id,)
        )
        if not entry:
            return None

        if new_content is not None:
            await self._db.execute(
                "UPDATE knowledge_entries SET content = ?, updated_at = ? WHERE id = ?",
                (new_content, now, entry_id),
            )
        else:
            await self._db.execute(
                "UPDATE knowledge_entries SET updated_at = ? WHERE id = ?",
                (now, entry_id),
            )

        # Resolve open staleness flags
        await self._db.execute(
            "UPDATE knowledge_staleness SET resolved = 1, resolved_at = ? "
            "WHERE entry_id = ? AND resolved = 0",
            (now, entry_id),
        )

        updated = await self._db.execute_fetchone(
            "SELECT * FROM knowledge_entries WHERE id = ?", (entry_id,)
        )
        return updated

    async def get_stale_entries(self, limit: int = 20) -> list[dict]:
        """List knowledge entries flagged as stale (unresolved)."""
        return await self._db.execute_fetchall(
            "SELECT ks.id AS flag_id, ks.entry_id, ks.flagged_at, ks.reason, "
            "ke.key, ke.content, ke.updated_at "
            "FROM knowledge_staleness ks "
            "JOIN knowledge_entries ke ON ks.entry_id = ke.id "
            "WHERE ks.resolved = 0 "
            "ORDER BY ks.flagged_at DESC LIMIT ?",
            (limit,),
        )

    # ------------------------------------------------------------------
    # Feature 46: Documentation Gap Detector
    # ------------------------------------------------------------------

    async def scan_for_gaps(
        self, code_files: list[str], doc_files: list[str]
    ) -> list[dict]:
        """Compare code symbols with documentation references.

        Scans *code_files* for class and function definitions, then checks
        whether any of those names appear in any of the *doc_files*.  Items
        not mentioned in documentation are recorded as gaps.
        """
        now = utcnow()
        # Build set of documented references
        doc_text = ""
        for df in doc_files:
            try:
                doc_text += Path(df).read_text(errors="replace") + "\n"
            except OSError:
                continue

        gaps: list[dict] = []
        symbol_re = re.compile(
            r"^\s*(?:class|def|async\s+def)\s+([A-Za-z_]\w*)", re.MULTILINE
        )

        for cf in code_files:
            try:
                source = Path(cf).read_text(errors="replace")
            except OSError:
                continue

            for m in symbol_re.finditer(source):
                name = m.group(1)
                if name.startswith("_"):
                    continue  # skip private symbols

                if name not in doc_text:
                    severity = "high" if name[0].isupper() else "medium"
                    gap_id = f"DG-{new_id(8)}"
                    await self._db.execute(
                        "INSERT INTO doc_gaps "
                        "(id, symbol_name, symbol_type, file_path, severity, "
                        "resolved, created_at) VALUES (?, ?, ?, ?, ?, 0, ?)",
                        (
                            gap_id,
                            name,
                            "class" if name[0].isupper() else "function",
                            cf,
                            severity,
                            now,
                        ),
                    )
                    gaps.append({
                        "id": gap_id,
                        "symbol_name": name,
                        "symbol_type": "class" if name[0].isupper() else "function",
                        "file_path": cf,
                        "severity": severity,
                        "created_at": now,
                    })

        return gaps

    async def get_gaps(
        self,
        file_path: str | None = None,
        severity: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        """List documentation gaps, optionally filtered."""
        query = "SELECT * FROM doc_gaps WHERE resolved = 0"
        params: list = []

        if file_path is not None:
            query += " AND file_path = ?"
            params.append(file_path)
        if severity is not None:
            query += " AND severity = ?"
            params.append(severity)

        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        return await self._db.execute_fetchall(query, tuple(params))

    async def resolve_gap(
        self, gap_id: str, doc_reference: str | None = None
    ) -> dict | None:
        """Mark a documentation gap as resolved."""
        now = utcnow()
        existing = await self._db.execute_fetchone(
            "SELECT * FROM doc_gaps WHERE id = ?", (gap_id,)
        )
        if not existing:
            return None

        await self._db.execute(
            "UPDATE doc_gaps SET resolved = 1, resolved_at = ?, doc_reference = ? "
            "WHERE id = ?",
            (now, doc_reference, gap_id),
        )
        return {
            **existing,
            "resolved": 1,
            "resolved_at": now,
            "doc_reference": doc_reference,
        }

    async def get_coverage_stats(self) -> dict:
        """Return documentation coverage statistics."""
        total = await self._db.execute_fetchone(
            "SELECT COUNT(*) AS cnt FROM doc_gaps"
        )
        resolved = await self._db.execute_fetchone(
            "SELECT COUNT(*) AS cnt FROM doc_gaps WHERE resolved = 1"
        )
        total_count = total["cnt"] if total else 0
        resolved_count = resolved["cnt"] if resolved else 0
        coverage = (
            round(resolved_count / total_count * 100, 1)
            if total_count > 0
            else 100.0
        )
        return {
            "total_symbols": total_count,
            "documented_symbols": resolved_count,
            "undocumented_symbols": total_count - resolved_count,
            "coverage_percent": coverage,
        }

    # ------------------------------------------------------------------
    # Feature 47: Institutional Knowledge Extractor
    # ------------------------------------------------------------------

    _COMMIT_PATTERNS = re.compile(
        r"\b(because|reason:|workaround|decided|decision|trade-?off|rationale)\b",
        re.IGNORECASE,
    )

    _COMMENT_TAGS = re.compile(
        r"\b(TODO|NOTE|HACK|FIXME|IMPORTANT)\b", re.IGNORECASE
    )

    async def extract_from_commit(
        self,
        commit_hash: str,
        commit_message: str,
        author: str,
        files_changed: list[str],
    ) -> dict | None:
        """Extract institutional knowledge from a commit message.

        Looks for decision-related patterns (because, reason, workaround,
        decision, trade-off, rationale).  Returns ``None`` if no patterns
        matched.
        """
        if not self._COMMIT_PATTERNS.search(commit_message):
            return None

        now = utcnow()
        ik_id = f"IK-{new_id(8)}"
        tags_list = [
            m.group(0).lower()
            for m in self._COMMIT_PATTERNS.finditer(commit_message)
        ]
        tags = json.dumps(sorted(set(tags_list)))

        await self._db.execute(
            "INSERT INTO institutional_knowledge "
            "(id, source_type, source_ref, content, tags, file_path, author, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                ik_id,
                "commit",
                commit_hash,
                commit_message,
                tags,
                json.dumps(files_changed),
                author,
                now,
            ),
        )
        return {
            "id": ik_id,
            "source_type": "commit",
            "source_ref": commit_hash,
            "content": commit_message,
            "tags": tags,
            "author": author,
            "created_at": now,
        }

    async def extract_from_comment(
        self,
        file_path: str,
        line_number: int,
        comment_text: str,
    ) -> dict | None:
        """Extract institutional knowledge from a code comment.

        Looks for TODO, NOTE, HACK, FIXME, IMPORTANT tags.  Returns ``None``
        if no tags matched.
        """
        if not self._COMMENT_TAGS.search(comment_text):
            return None

        now = utcnow()
        ik_id = f"IK-{new_id(8)}"
        tags_list = [
            m.group(0).upper()
            for m in self._COMMENT_TAGS.finditer(comment_text)
        ]
        tags = json.dumps(sorted(set(tags_list)))

        await self._db.execute(
            "INSERT INTO institutional_knowledge "
            "(id, source_type, source_ref, content, tags, file_path, line_number, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                ik_id,
                "comment",
                f"{file_path}:{line_number}",
                comment_text,
                tags,
                file_path,
                line_number,
                now,
            ),
        )
        return {
            "id": ik_id,
            "source_type": "comment",
            "source_ref": f"{file_path}:{line_number}",
            "content": comment_text,
            "tags": tags,
            "file_path": file_path,
            "line_number": line_number,
            "created_at": now,
        }

    async def search_knowledge(self, query: str, limit: int = 10) -> list[dict]:
        """Keyword search across institutional knowledge entries."""
        return await self._db.execute_fetchall(
            "SELECT * FROM institutional_knowledge "
            "WHERE content LIKE ? OR tags LIKE ? "
            "ORDER BY created_at DESC LIMIT ?",
            (f"%{query}%", f"%{query}%", limit),
        )

    async def get_knowledge(
        self, source_type: str | None = None, limit: int = 20
    ) -> list[dict]:
        """List institutional knowledge entries, optionally filtered by source type."""
        if source_type:
            return await self._db.execute_fetchall(
                "SELECT * FROM institutional_knowledge "
                "WHERE source_type = ? ORDER BY created_at DESC LIMIT ?",
                (source_type, limit),
            )
        return await self._db.execute_fetchall(
            "SELECT * FROM institutional_knowledge "
            "ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )

    # ------------------------------------------------------------------
    # Feature 48: Context Compression Engine
    # ------------------------------------------------------------------

    _salience_weights: dict[str, float] = {
        "recency": 0.3,
        "relevance": 0.5,
        "frequency": 0.2,
    }

    async def compress_context(
        self,
        context_items: list[dict],
        max_tokens: int,
        strategy: str = "salience",
    ) -> dict:
        """Score items by salience and return a subset fitting within *max_tokens*.

        Each item in *context_items* must have at minimum:
        - ``tokens`` (int): estimated token count
        - ``recency`` (float 0-1): how recent the item is
        - ``relevance`` (float 0-1): relevance to current task
        - ``frequency`` (float 0-1): how frequently referenced

        Returns a dict with ``kept``, ``dropped``, and ``total_tokens``.
        """
        weights = self._salience_weights

        # Score each item
        scored: list[tuple[float, int, dict]] = []
        for idx, item in enumerate(context_items):
            recency = clamp(item.get("recency", 0.0))
            relevance = clamp(item.get("relevance", 0.0))
            frequency = clamp(item.get("frequency", 0.0))
            score = (
                weights["recency"] * recency
                + weights["relevance"] * relevance
                + weights["frequency"] * frequency
            )
            scored.append((score, idx, item))

        # Sort by score descending
        scored.sort(key=lambda x: x[0], reverse=True)

        kept: list[dict] = []
        dropped: list[dict] = []
        used_tokens = 0

        for score, _idx, item in scored:
            item_tokens = item.get("tokens", 0)
            if used_tokens + item_tokens <= max_tokens:
                kept.append({**item, "salience_score": round(score, 4)})
                used_tokens += item_tokens
            else:
                dropped.append({**item, "salience_score": round(score, 4)})

        return {
            "kept": kept,
            "dropped": dropped,
            "total_tokens": used_tokens,
            "strategy": strategy,
        }

    async def record_compression(
        self,
        task_id: str,
        original_tokens: int,
        compressed_tokens: int,
        items_kept: int,
        items_dropped: int,
    ) -> dict:
        """Record a compression event for later analysis."""
        now = utcnow()
        rec_id = f"CP-{new_id(8)}"
        await self._db.execute(
            "INSERT INTO compression_profiles "
            "(id, task_id, original_tokens, compressed_tokens, items_kept, "
            "items_dropped, strategy, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (rec_id, task_id, original_tokens, compressed_tokens, items_kept, items_dropped, "salience", now),
        )
        return {
            "id": rec_id,
            "task_id": task_id,
            "original_tokens": original_tokens,
            "compressed_tokens": compressed_tokens,
            "items_kept": items_kept,
            "items_dropped": items_dropped,
            "compression_ratio": round(compressed_tokens / original_tokens, 4) if original_tokens > 0 else 0,
            "created_at": now,
        }

    async def get_compression_stats(self) -> dict:
        """Return aggregate compression statistics."""
        row = await self._db.execute_fetchone(
            "SELECT COUNT(*) AS cnt, "
            "COALESCE(AVG(CAST(compressed_tokens AS REAL) / NULLIF(original_tokens, 0)), 0) AS avg_ratio, "
            "COALESCE(SUM(items_dropped), 0) AS total_dropped, "
            "COALESCE(SUM(items_kept), 0) AS total_kept "
            "FROM compression_profiles"
        )
        return {
            "total_compressions": row["cnt"] if row else 0,
            "avg_compression_ratio": round(row["avg_ratio"], 4) if row else 0,
            "total_items_kept": row["total_kept"] if row else 0,
            "total_items_dropped": row["total_dropped"] if row else 0,
        }

    async def set_salience_weights(
        self,
        recency_weight: float = 0.3,
        relevance_weight: float = 0.5,
        frequency_weight: float = 0.2,
    ) -> dict:
        """Configure salience scoring weights."""
        self._salience_weights = {
            "recency": clamp(recency_weight),
            "relevance": clamp(relevance_weight),
            "frequency": clamp(frequency_weight),
        }
        return dict(self._salience_weights)
