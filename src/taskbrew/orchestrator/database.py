"""SQLite database layer with async access via aiosqlite."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import aiosqlite

logger = logging.getLogger(__name__)


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
    revision_of      TEXT REFERENCES tasks(id),
    output_text      TEXT
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
    model TEXT DEFAULT '',
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    cost_usd REAL DEFAULT 0,
    duration_api_ms INTEGER DEFAULT 0,
    num_turns INTEGER DEFAULT 0,
    recorded_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS approvals (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id),
    artifact_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    requested_at TEXT NOT NULL,
    decided_at TEXT,
    decided_by TEXT,
    reason TEXT
);

CREATE TABLE IF NOT EXISTS cost_budgets (
    id TEXT PRIMARY KEY,
    scope TEXT NOT NULL,
    scope_id TEXT,
    budget_usd REAL NOT NULL,
    spent_usd REAL NOT NULL DEFAULT 0,
    period TEXT NOT NULL DEFAULT 'daily',
    reset_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL,
    title TEXT NOT NULL,
    message TEXT,
    severity TEXT NOT NULL DEFAULT 'info',
    read INTEGER NOT NULL DEFAULT 0,
    data TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS webhooks (
    id TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    events TEXT NOT NULL,
    secret TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    last_triggered_at TEXT
);

CREATE TABLE IF NOT EXISTS webhook_deliveries (
    id TEXT PRIMARY KEY,
    webhook_id TEXT NOT NULL REFERENCES webhooks(id),
    event_type TEXT NOT NULL,
    payload TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    attempt_count INTEGER NOT NULL DEFAULT 0,
    response_code INTEGER,
    error_message TEXT,
    created_at TEXT NOT NULL,
    last_attempted_at TEXT
);

CREATE TABLE IF NOT EXISTS task_templates (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    title_template TEXT NOT NULL,
    description_template TEXT,
    task_type TEXT,
    assigned_to TEXT,
    priority TEXT DEFAULT 'medium',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_agent TEXT NOT NULL,
    to_agent TEXT NOT NULL,
    content TEXT NOT NULL,
    read INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS workflow_definitions (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    description TEXT,
    steps TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ab_test_configs (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    role TEXT NOT NULL,
    variant_a TEXT NOT NULL,
    variant_b TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 0,
    allocation REAL NOT NULL DEFAULT 0.5,
    created_at TEXT NOT NULL,
    results TEXT
);

CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at TEXT NOT NULL
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

CREATE INDEX IF NOT EXISTS idx_approvals_task
    ON approvals(task_id, status);

CREATE INDEX IF NOT EXISTS idx_notifications_unread
    ON notifications(read, created_at)
    WHERE read = 0;

CREATE INDEX IF NOT EXISTS idx_cost_budgets_scope
    ON cost_budgets(scope, scope_id);

CREATE INDEX IF NOT EXISTS idx_agent_messages_to
    ON agent_messages(to_agent, read);
"""


def _utcnow() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


class Database:
    """Async SQLite database wrapper using aiosqlite with connection pooling.

    Parameters
    ----------
    db_path:
        Path to the SQLite database file.  Use ``":memory:"`` for tests.
    pool_size:
        Number of connections to maintain in the pool (default 5).
        The primary connection (``self._conn``) is always available for
        backward compatibility; the pool provides additional connections
        for concurrent reads.
    """

    def __init__(self, db_path: str, pool_size: int = 5) -> None:
        self.db_path = db_path
        self.pool_size = pool_size
        self._conn: aiosqlite.Connection | None = None
        self._pool: asyncio.Queue[aiosqlite.Connection] | None = None
        self._tx_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def _create_connection(self) -> aiosqlite.Connection:
        """Create and configure a single aiosqlite connection."""
        # isolation_level=None enables autocommit mode, preventing implicit
        # transactions that cause "cannot start a transaction within a
        # transaction" when concurrent coroutines share the connection.
        conn = await aiosqlite.connect(self.db_path, isolation_level=None)
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA busy_timeout = 5000")
        await conn.execute("PRAGMA foreign_keys=ON")
        return conn

    async def initialize(self) -> None:
        """Open the connection pool, enable WAL mode and foreign keys, create schema."""
        if self.db_path != ":memory:":
            from pathlib import Path
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)

        # Primary connection (backward compatible)
        self._conn = await self._create_connection()
        await self._conn.executescript(_SCHEMA_SQL)
        await self._conn.executescript(_INDEX_SQL)
        await self._conn.commit()

        # --- Migrations for existing databases ---
        # Add output_text column if missing (backwards compat)
        try:
            await self._conn.execute("ALTER TABLE tasks ADD COLUMN output_text TEXT")
            await self._conn.commit()
        except Exception as exc:
            logger.debug("output_text column already exists: %s", exc)

        # Apply pending schema migrations
        from taskbrew.orchestrator.migration import MigrationManager
        migrator = MigrationManager(self)
        applied = await migrator.apply_pending()
        if applied:
            logger.info("Applied migrations: %s", applied)

        # Initialize connection pool for concurrent access
        self._pool = asyncio.Queue(maxsize=self.pool_size)
        if self.db_path != ":memory:":
            for _ in range(self.pool_size):
                conn = await self._create_connection()
                await self._pool.put(conn)
            logger.debug("Connection pool initialized with %d connections", self.pool_size)

    async def close(self) -> None:
        """Close all connections including the pool."""
        if self._pool is not None:
            while not self._pool.empty():
                try:
                    conn = self._pool.get_nowait()
                    await conn.close()
                except asyncio.QueueEmpty:
                    break
            self._pool = None
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    # ------------------------------------------------------------------
    # Connection pool management
    # ------------------------------------------------------------------

    @asynccontextmanager
    async def acquire(self):
        """Acquire a connection from the pool.

        Usage::

            async with db.acquire() as conn:
                cursor = await conn.execute("SELECT ...")

        Falls back to the primary connection if the pool is not available
        (e.g. in-memory databases where no pool is created).
        """
        if self._pool is not None:
            # Use get() which blocks until a connection is available,
            # avoiding the race condition of checking empty() then get().
            conn = await self._pool.get()
            try:
                yield conn
            finally:
                await self._pool.put(conn)
        else:
            # Fallback to primary connection for in-memory databases
            # (pool is not created for :memory: databases).
            if self._conn is None:
                raise RuntimeError("Database not initialized. Call initialize() first.")
            yield self._conn

    async def release(self, conn: aiosqlite.Connection) -> None:
        """Return a connection to the pool.

        This is provided for callers that manage connections manually
        rather than using the ``acquire()`` context manager.
        """
        if self._pool is not None and not self._pool.full():
            await self._pool.put(conn)
        else:
            await conn.close()

    # ------------------------------------------------------------------
    # Transaction support
    # ------------------------------------------------------------------

    @asynccontextmanager
    async def transaction(self):
        """Async context manager for multi-statement transactions.

        Uses an asyncio lock to prevent concurrent coroutines from
        attempting nested BEGIN on the shared connection.
        """
        if self._conn is None:
            raise RuntimeError("Database not initialized. Call initialize() first.")
        async with self._tx_lock:
            await self._conn.execute("BEGIN")
            try:
                yield self._conn
                await self._conn.commit()
            except Exception:
                await self._conn.rollback()
                raise

    async def executescript(self, sql: str) -> None:
        """Execute a multi-statement SQL script and commit."""
        if self._conn is None:
            raise RuntimeError("Database not initialized. Call initialize() first.")
        await self._conn.executescript(sql)
        await self._conn.commit()

    # ------------------------------------------------------------------
    # ID generation
    # ------------------------------------------------------------------

    async def register_prefix(self, prefix: str) -> None:
        """Ensure a prefix row exists in id_sequences.

        If the prefix already exists this is a no-op.
        """
        if self._conn is None:
            raise RuntimeError("Database not initialized. Call initialize() first.")
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
        if self._conn is None:
            raise RuntimeError("Database not initialized. Call initialize() first.")
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
        if self._conn is None:
            raise RuntimeError("Database not initialized. Call initialize() first.")
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
        if self._conn is None:
            raise RuntimeError("Database not initialized. Call initialize() first.")
        cursor = await self._conn.execute(sql, params)
        row = await cursor.fetchone()
        if row is None:
            return None
        keys = [desc[0] for desc in cursor.description]
        return dict(zip(keys, row))

    async def execute(self, sql: str, params: tuple = ()) -> None:
        """Execute a statement and commit."""
        if self._conn is None:
            raise RuntimeError("Database not initialized. Call initialize() first.")
        await self._conn.execute(sql, params)
        await self._conn.commit()

    async def execute_returning(self, sql: str, params: tuple = ()) -> list[dict]:
        """Execute a mutating query with RETURNING clause, commit, and return rows as dicts."""
        if self._conn is None:
            raise RuntimeError("Database not initialized. Call initialize() first.")
        cursor = await self._conn.execute(sql, params)
        rows = await cursor.fetchall()
        await self._conn.commit()
        if not rows:
            return []
        keys = [desc[0] for desc in cursor.description]
        return [dict(zip(keys, row)) for row in rows]

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

    # ------------------------------------------------------------------
    # Notifications
    # ------------------------------------------------------------------

    async def get_unread_notifications(self, limit: int = 50) -> list[dict]:
        """Fetch unread notifications, most recent first."""
        return await self.execute_fetchall(
            "SELECT * FROM notifications WHERE read = 0 ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )

    async def mark_notification_read(self, notification_id: int) -> None:
        """Mark a single notification as read."""
        await self.execute(
            "UPDATE notifications SET read = 1 WHERE id = ?",
            (notification_id,),
        )

    async def mark_all_notifications_read(self) -> None:
        """Mark all notifications as read."""
        await self.execute("UPDATE notifications SET read = 1 WHERE read = 0")

    async def create_notification(
        self,
        type: str,
        title: str,
        message: str | None = None,
        severity: str = "info",
        data: str | None = None,
    ) -> dict:
        """Create a new notification and return it as a dict."""
        if self._conn is None:
            raise RuntimeError("Database not initialized. Call initialize() first.")
        now = _utcnow()
        cursor = await self._conn.execute(
            "INSERT INTO notifications (type, title, message, severity, data, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (type, title, message, severity, data, now),
        )
        await self._conn.commit()
        return {
            "id": cursor.lastrowid,
            "type": type,
            "title": title,
            "message": message,
            "severity": severity,
            "read": 0,
            "data": data,
            "created_at": now,
        }
