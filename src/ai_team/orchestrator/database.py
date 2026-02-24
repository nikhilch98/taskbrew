"""SQLite database layer with async access via aiosqlite."""

from __future__ import annotations

from datetime import datetime, timezone

import aiosqlite


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS groups (
    id            TEXT PRIMARY KEY,
    title         TEXT NOT NULL,
    origin        TEXT,
    status        TEXT NOT NULL DEFAULT 'active',
    created_by    TEXT,
    created_at    TEXT NOT NULL,
    completed_at  TEXT
);

CREATE TABLE IF NOT EXISTS tasks (
    id               TEXT PRIMARY KEY,
    group_id         TEXT REFERENCES groups(id),
    parent_id        TEXT REFERENCES tasks(id),
    title            TEXT NOT NULL,
    description      TEXT,
    task_type        TEXT,
    priority         TEXT NOT NULL DEFAULT 'medium',
    assigned_to      TEXT,
    claimed_by       TEXT,
    status           TEXT NOT NULL DEFAULT 'pending',
    created_by       TEXT,
    created_at       TEXT NOT NULL,
    started_at       TEXT,
    completed_at     TEXT,
    rejection_reason TEXT,
    revision_of      TEXT REFERENCES tasks(id)
);

CREATE TABLE IF NOT EXISTS task_dependencies (
    task_id     TEXT NOT NULL REFERENCES tasks(id),
    blocked_by  TEXT NOT NULL REFERENCES tasks(id),
    resolved    INTEGER NOT NULL DEFAULT 0,
    resolved_at TEXT,
    PRIMARY KEY (task_id, blocked_by),
    CHECK (task_id != blocked_by)
);

CREATE TABLE IF NOT EXISTS artifacts (
    id            TEXT PRIMARY KEY,
    task_id       TEXT REFERENCES tasks(id),
    file_path     TEXT,
    artifact_type TEXT NOT NULL DEFAULT 'output',
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_instances (
    instance_id    TEXT PRIMARY KEY,
    role           TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'idle',
    current_task   TEXT REFERENCES tasks(id),
    started_at     TEXT,
    last_heartbeat TEXT
);

CREATE TABLE IF NOT EXISTS id_sequences (
    prefix   TEXT PRIMARY KEY,
    next_val INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type  TEXT,
    group_id    TEXT,
    task_id     TEXT,
    agent_id    TEXT,
    data        TEXT,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS task_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    cost_usd REAL DEFAULT 0,
    duration_api_ms INTEGER DEFAULT 0,
    num_turns INTEGER DEFAULT 0,
    recorded_at TEXT NOT NULL
);
"""

_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_tasks_assignee_status
    ON tasks(assigned_to, status)
    WHERE status = 'pending' AND claimed_by IS NULL;

CREATE INDEX IF NOT EXISTS idx_tasks_group
    ON tasks(group_id, status);

CREATE INDEX IF NOT EXISTS idx_deps_blocked
    ON task_dependencies(blocked_by)
    WHERE resolved = 0;

CREATE INDEX IF NOT EXISTS idx_tasks_parent
    ON tasks(parent_id);

CREATE INDEX IF NOT EXISTS idx_events_group
    ON events(group_id, created_at);

CREATE INDEX IF NOT EXISTS idx_events_type
    ON events(event_type, created_at);
"""


def _utcnow() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


class Database:
    """Async SQLite database wrapper using aiosqlite.

    Parameters
    ----------
    db_path:
        Path to the SQLite database file.  Use ``":memory:"`` for tests.
    """

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._conn: aiosqlite.Connection | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        """Open the connection, enable WAL mode and foreign keys, create schema."""
        self._conn = await aiosqlite.connect(self.db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._conn.executescript(_SCHEMA_SQL)
        await self._conn.executescript(_INDEX_SQL)
        await self._conn.commit()

    async def close(self) -> None:
        """Close the database connection."""
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    # ------------------------------------------------------------------
    # ID generation
    # ------------------------------------------------------------------

    async def register_prefix(self, prefix: str) -> None:
        """Ensure a prefix row exists in id_sequences.

        If the prefix already exists this is a no-op.
        """
        assert self._conn is not None
        await self._conn.execute(
            "INSERT OR IGNORE INTO id_sequences (prefix, next_val) VALUES (?, 1)",
            (prefix,),
        )
        await self._conn.commit()

    async def generate_task_id(self, prefix: str) -> str:
        """Atomically increment the sequence for *prefix* and return an ID.

        The returned ID has the form ``"PM-001"``.

        Raises
        ------
        ValueError
            If the prefix has not been registered.
        """
        assert self._conn is not None
        cursor = await self._conn.execute(
            "UPDATE id_sequences SET next_val = next_val + 1 "
            "WHERE prefix = ? RETURNING next_val - 1 AS val",
            (prefix,),
        )
        row = await cursor.fetchone()
        if row is None:
            raise ValueError(f"Unregistered prefix: {prefix!r}")
        val: int = row[0]
        await self._conn.commit()
        return f"{prefix}-{val:03d}"

    # ------------------------------------------------------------------
    # Generic query helpers
    # ------------------------------------------------------------------

    async def execute_fetchall(
        self, sql: str, params: tuple = ()
    ) -> list[dict]:
        """Execute a query and return all rows as dicts."""
        assert self._conn is not None
        cursor = await self._conn.execute(sql, params)
        rows = await cursor.fetchall()
        if not rows:
            return []
        keys = [desc[0] for desc in cursor.description]
        return [dict(zip(keys, row)) for row in rows]

    async def execute_fetchone(
        self, sql: str, params: tuple = ()
    ) -> dict | None:
        """Execute a query and return the first row as a dict, or None."""
        assert self._conn is not None
        cursor = await self._conn.execute(sql, params)
        row = await cursor.fetchone()
        if row is None:
            return None
        keys = [desc[0] for desc in cursor.description]
        return dict(zip(keys, row))

    async def execute(self, sql: str, params: tuple = ()) -> None:
        """Execute a statement and commit."""
        assert self._conn is not None
        await self._conn.execute(sql, params)
        await self._conn.commit()

    # ------------------------------------------------------------------
    # Usage tracking
    # ------------------------------------------------------------------

    async def record_task_usage(
        self, task_id: str, agent_id: str, input_tokens: int = 0,
        output_tokens: int = 0, cost_usd: float = 0, duration_api_ms: int = 0,
        num_turns: int = 0,
    ) -> None:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        await self.execute(
            "INSERT INTO task_usage (task_id, agent_id, input_tokens, output_tokens, "
            "cost_usd, duration_api_ms, num_turns, recorded_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (task_id, agent_id, input_tokens, output_tokens, cost_usd, duration_api_ms, num_turns, now),
        )

    async def get_usage_summary(self, since: str) -> dict:
        row = await self.execute_fetchone(
            "SELECT COALESCE(SUM(input_tokens), 0) as input_tokens, "
            "COALESCE(SUM(output_tokens), 0) as output_tokens, "
            "COALESCE(SUM(cost_usd), 0) as cost_usd, "
            "COALESCE(SUM(duration_api_ms), 0) as duration_api_ms, "
            "COALESCE(SUM(num_turns), 0) as num_turns, "
            "COUNT(*) as tasks_completed "
            "FROM task_usage WHERE recorded_at >= ?",
            (since,),
        )
        return dict(row) if row else {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0, "duration_api_ms": 0, "num_turns": 0, "tasks_completed": 0}
