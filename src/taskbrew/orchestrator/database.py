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

CREATE TABLE IF NOT EXISTS milestones (
    id             TEXT PRIMARY KEY,
    group_id       TEXT NOT NULL REFERENCES groups(id),
    title          TEXT NOT NULL,
    description    TEXT,
    status         TEXT NOT NULL DEFAULT 'pending',
    review_scope   TEXT NOT NULL DEFAULT 'milestone',
    review_status  TEXT,
    review_reason  TEXT,
    review_round   INTEGER NOT NULL DEFAULT 0,
    max_review_rounds INTEGER NOT NULL DEFAULT 3,
    created_by     TEXT,
    created_at     TEXT NOT NULL,
    updated_at     TEXT,
    completed_at   TEXT
);

CREATE TABLE IF NOT EXISTS work_packages (
    id             TEXT PRIMARY KEY,
    group_id       TEXT NOT NULL REFERENCES groups(id),
    milestone_id   TEXT REFERENCES milestones(id),
    title          TEXT NOT NULL,
    description    TEXT,
    status         TEXT NOT NULL DEFAULT 'pending',
    risk_level     TEXT NOT NULL DEFAULT 'medium',
    review_scope   TEXT NOT NULL DEFAULT 'work_package',
    review_status  TEXT,
    review_reason  TEXT,
    review_round   INTEGER NOT NULL DEFAULT 0,
    max_review_rounds INTEGER NOT NULL DEFAULT 3,
    branch_name    TEXT,
    created_by     TEXT,
    created_at     TEXT NOT NULL,
    updated_at     TEXT,
    completed_at   TEXT
);

CREATE TABLE IF NOT EXISTS review_gates (
    id             TEXT PRIMARY KEY,
    entity_type    TEXT NOT NULL,
    entity_id      TEXT NOT NULL,
    group_id       TEXT NOT NULL REFERENCES groups(id),
    status         TEXT NOT NULL DEFAULT 'pending',
    outcome        TEXT,
    reason         TEXT,
    review_round   INTEGER NOT NULL DEFAULT 0,
    max_review_rounds INTEGER NOT NULL DEFAULT 3,
    system_gate_runs TEXT DEFAULT '[]',
    created_at     TEXT NOT NULL,
    updated_at     TEXT,
    completed_at   TEXT,
    UNIQUE(entity_type, entity_id)
);

CREATE TABLE IF NOT EXISTS tasks (
    id               TEXT PRIMARY KEY,
    group_id         TEXT REFERENCES groups(id),
    work_package_id  TEXT REFERENCES work_packages(id),
    milestone_id     TEXT REFERENCES milestones(id),
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
    output_text      TEXT,
    chain_id         TEXT,
    approval_mode    TEXT DEFAULT 'auto',
    instance_token   TEXT,
    config_snapshot  TEXT,
    -- audit 03 F#3: include Stage-1 fan-out columns in the baseline
    -- so first-boot writes don't depend on migration 29 having run.
    -- Also mirrored in migration 29 for upgrades from older DBs; the
    -- _column_exists probe in MigrationManager skips the ADD COLUMN
    -- when the baseline already provided it.
    requires_fanout  INTEGER,
    fanout_retries   INTEGER DEFAULT 0,
    -- First-class branch metadata (migration 30). Minted by
    -- TaskBoard.create_task so agent_loop reads the authoritative
    -- branch rather than reconstructing it from task_id.
    branch_name      TEXT,
    parent_branch    TEXT,
    -- Per-task verification fingerprint (migration 31). completion_checks
    -- is a freeform JSON object; the verification gate in
    -- complete_and_handoff reads it and re-queues tasks with any failed
    -- check, emitting merged_unverified events for tasks with no checks.
    completion_checks      TEXT DEFAULT '{}',
    merge_status           TEXT,
    verification_retries   INTEGER DEFAULT 0,
    -- Task-level pause anchor (migration 32). Set when the agent
    -- enters a manual-mode ask_question wait; cleared on resolve
    -- or task cancel. The activity-based idle watchdog skips tasks
    -- while this is non-NULL.
    awaiting_input_since   TEXT,
    -- System gate workflow fields (migration 34).
    intended_status              TEXT DEFAULT 'pending',
    needs_review                 INTEGER,
    needs_review_reason          TEXT,
    needs_review_decision        TEXT,
    review_scope                 TEXT,
    backlog_intake_status        TEXT DEFAULT 'pending',
    backlog_intake_processed_at  TEXT,
    review_status                TEXT,
    review_round                 INTEGER DEFAULT 0,
    max_review_rounds            INTEGER DEFAULT 3,
    review_parent_task_id        TEXT REFERENCES tasks(id),
    revision_task_ids            TEXT DEFAULT '[]',
    system_gate_runs             TEXT DEFAULT '[]'
);

-- Structured agent clarifications (migration 32). Persists every
-- ask_question call the agent makes and the eventual selected
-- answer + selector (agent | user). Indices match the dashboard's
-- pending list and per-task lookup patterns.
CREATE TABLE IF NOT EXISTS agent_questions (
    id                TEXT PRIMARY KEY,
    task_id           TEXT NOT NULL REFERENCES tasks(id),
    group_id          TEXT NOT NULL,
    agent_role        TEXT NOT NULL,
    instance_id       TEXT,
    question          TEXT NOT NULL,
    options           TEXT NOT NULL,
    preferred_answer  TEXT NOT NULL,
    reasoning         TEXT NOT NULL,
    selected_answer   TEXT,
    selected_by       TEXT,
    status            TEXT NOT NULL DEFAULT 'pending',
    created_at        TEXT NOT NULL,
    resolved_at       TEXT
);
CREATE INDEX IF NOT EXISTS idx_agent_questions_task
    ON agent_questions(task_id);
CREATE INDEX IF NOT EXISTS idx_agent_questions_status
    ON agent_questions(status);

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

CREATE TABLE IF NOT EXISTS compression_savings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT,
    agent_id TEXT,
    sink TEXT,
    model TEXT DEFAULT '',
    tokens_before INTEGER DEFAULT 0,
    tokens_after INTEGER DEFAULT 0,
    tokens_saved INTEGER DEFAULT 0,
    transforms TEXT DEFAULT '',
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

CREATE TABLE IF NOT EXISTS human_interaction_requests (
    id              TEXT PRIMARY KEY,
    task_id         TEXT NOT NULL REFERENCES tasks(id),
    instance_token  TEXT NOT NULL,
    request_type    TEXT NOT NULL,
    request_key     TEXT NOT NULL UNIQUE,
    status          TEXT NOT NULL DEFAULT 'pending',
    payload         TEXT,
    response_payload TEXT,
    responded_by    TEXT,
    created_at      TEXT NOT NULL,
    resolved_at     TEXT
);

CREATE TABLE IF NOT EXISTS task_chains (
    id                   TEXT PRIMARY KEY,
    original_task_id     TEXT NOT NULL REFERENCES tasks(id),
    current_task_id      TEXT NOT NULL REFERENCES tasks(id),
    agent_role           TEXT NOT NULL,
    revision_count       INTEGER NOT NULL DEFAULT 0,
    max_revision_cycles  INTEGER NOT NULL DEFAULT 0,
    status               TEXT NOT NULL DEFAULT 'active',
    created_at           TEXT NOT NULL,
    updated_at           TEXT
);

CREATE TABLE IF NOT EXISTS first_run_approvals (
    id          TEXT PRIMARY KEY,
    group_id    TEXT NOT NULL REFERENCES groups(id),
    agent_role  TEXT NOT NULL,
    approved_at TEXT NOT NULL,
    UNIQUE(group_id, agent_role)
);

CREATE TABLE IF NOT EXISTS merge_queue (
    id                  TEXT PRIMARY KEY,
    group_id            TEXT NOT NULL,
    parent_task_id      TEXT NOT NULL REFERENCES tasks(id),
    verifier_task_id    TEXT NOT NULL REFERENCES tasks(id),
    source_type         TEXT NOT NULL DEFAULT 'verifier_approval',
    source_entity_id    TEXT,
    work_package_id     TEXT REFERENCES work_packages(id),
    source_branch       TEXT NOT NULL,
    target_branch       TEXT NOT NULL DEFAULT 'main',
    status              TEXT NOT NULL DEFAULT 'queued',
    attempts            INTEGER NOT NULL DEFAULT 0,
    next_attempt_at     TEXT,
    leased_by           TEXT,
    leased_until        TEXT,
    last_error          TEXT,
    target_sha_before   TEXT,
    target_sha_after    TEXT,
    root_refresh_status TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    completed_at        TEXT
);
"""

_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_milestones_group_status
    ON milestones(group_id, status);

CREATE INDEX IF NOT EXISTS idx_work_packages_group_status
    ON work_packages(group_id, status);

CREATE INDEX IF NOT EXISTS idx_work_packages_milestone
    ON work_packages(milestone_id, status);

CREATE INDEX IF NOT EXISTS idx_review_gates_entity
    ON review_gates(entity_type, entity_id);

CREATE INDEX IF NOT EXISTS idx_review_gates_status
    ON review_gates(status, created_at);

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

CREATE INDEX IF NOT EXISTS idx_hir_pending
    ON human_interaction_requests(status, created_at)
    WHERE status = 'pending';

CREATE INDEX IF NOT EXISTS idx_hir_task
    ON human_interaction_requests(task_id, request_type);

CREATE INDEX IF NOT EXISTS idx_task_chains_current
    ON task_chains(current_task_id);

CREATE INDEX IF NOT EXISTS idx_task_chains_role
    ON task_chains(agent_role, status);

CREATE INDEX IF NOT EXISTS idx_first_run_approvals_group
    ON first_run_approvals(group_id, agent_role);

CREATE UNIQUE INDEX IF NOT EXISTS uq_merge_queue_verifier
    ON merge_queue(verifier_task_id);

CREATE INDEX IF NOT EXISTS idx_merge_queue_group_status
    ON merge_queue(group_id, status);

CREATE INDEX IF NOT EXISTS idx_merge_queue_ready
    ON merge_queue(status, next_attempt_at, created_at);

"""

# Indexes that depend on ALTER TABLE columns. Existing databases may have
# older tasks/merge_queue tables, so these run only after compatibility
# columns and numbered migrations have finished.
_DEFERRED_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_tasks_chain
    ON tasks(chain_id);

CREATE INDEX IF NOT EXISTS idx_tasks_instance_token
    ON tasks(instance_token)
    WHERE instance_token IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_tasks_work_package
    ON tasks(work_package_id, status);

CREATE INDEX IF NOT EXISTS idx_merge_queue_source
    ON merge_queue(source_type, source_entity_id, status);

CREATE INDEX IF NOT EXISTS idx_merge_queue_package
    ON merge_queue(work_package_id, status);
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
        # audit 03 F#14: serialise id generation across coroutines.
        self._id_lock = asyncio.Lock()

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
        # audit 03 F#6: the ALTER TABLE calls below mean to add columns
        # that older databases are missing; re-running them on a newer
        # DB raises "duplicate column name X". That is the ONLY expected
        # failure. The previous bare ``except Exception: pass`` also
        # swallowed disk-full errors, syntax bugs introduced by future
        # edits, and permission errors -- silent half-migrations that
        # then exploded as "no such column" on the next write.
        #
        # We now match the specific SQLite error text ("duplicate column")
        # and re-raise anything else.
        async def _alter_add_column_if_missing(sql: str, col: str) -> None:
            try:
                await self._conn.execute(sql)
                await self._conn.commit()
            except Exception as exc:  # noqa: BLE001 -- narrowed below
                msg = str(exc).lower()
                if "duplicate column" in msg or "already exists" in msg:
                    logger.debug("Column %s already present (benign): %s", col, exc)
                    return
                logger.error(
                    "ALTER TABLE failed for column %s: %s", col, exc,
                )
                raise

        # Add output_text column if missing (backwards compat)
        await _alter_add_column_if_missing(
            "ALTER TABLE tasks ADD COLUMN output_text TEXT",
            "output_text",
        )

        # Add HITL columns to tasks table if missing (backwards compat)
        for col, col_type, default in [
            ("chain_id", "TEXT", None),
            ("approval_mode", "TEXT", "'auto'"),
            ("instance_token", "TEXT", None),
            ("config_snapshot", "TEXT", None),
        ]:
            default_clause = f" DEFAULT {default}" if default else ""
            await _alter_add_column_if_missing(
                f"ALTER TABLE tasks ADD COLUMN {col} {col_type}{default_clause}",
                col,
            )

        # Apply pending schema migrations
        from taskbrew.orchestrator.migration import MigrationManager
        migrator = MigrationManager(self)
        applied = await migrator.apply_pending()
        if applied:
            logger.info("Applied migrations: %s", applied)

        # Create indexes that depend on ALTER TABLE columns after migrations.
        await self._conn.executescript(_DEFERRED_INDEX_SQL)
        await self._conn.commit()

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
        if self._pool is not None:
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

        audit 03 F#14: the ``UPDATE ... RETURNING`` statement is atomic
        at the SQLite engine level, but the subsequent ``cursor.fetchone()``
        is a separate Python await. On a shared aiosqlite connection, two
        concurrent coroutines could (in theory, depending on aiosqlite
        queueing) race between execute and fetch. Serialising id
        generation behind a module-level lock closes that race without
        depending on aiosqlite internals -- id generation is not a hot
        path (once per create_task), so the lock cost is negligible.

        Raises
        ------
        ValueError
            If the prefix has not been registered.
        """
        if self._conn is None:
            raise RuntimeError("Database not initialized. Call initialize() first.")
        async with self._id_lock:
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
    # Context-compression savings (Headroom)
    # ------------------------------------------------------------------

    async def record_compression_saving(
        self, *, task_id: str, agent_id: str, sink: str, model: str = "",
        tokens_before: int = 0, tokens_after: int = 0, tokens_saved: int = 0,
        transforms: str = "",
    ) -> None:
        """Persist a single context-compression event for dashboard reporting."""
        now = datetime.now(timezone.utc).isoformat()
        await self.execute(
            "INSERT INTO compression_savings (task_id, agent_id, sink, model, "
            "tokens_before, tokens_after, tokens_saved, transforms, recorded_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (task_id, agent_id, sink, model, tokens_before, tokens_after,
             tokens_saved, transforms, now),
        )

    async def get_compression_summary(self, since: str) -> dict:
        """Aggregate compression savings recorded at or after *since*."""
        row = await self.execute_fetchone(
            "SELECT COALESCE(SUM(tokens_before), 0) as tokens_before, "
            "COALESCE(SUM(tokens_after), 0) as tokens_after, "
            "COALESCE(SUM(tokens_saved), 0) as tokens_saved, "
            "COUNT(*) as events "
            "FROM compression_savings WHERE recorded_at >= ?",
            (since,),
        )
        summary = dict(row) if row else {
            "tokens_before": 0, "tokens_after": 0, "tokens_saved": 0, "events": 0,
        }
        before = summary.get("tokens_before") or 0
        saved = summary.get("tokens_saved") or 0
        summary["ratio"] = round(saved / before, 4) if before else 0.0
        return summary

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
