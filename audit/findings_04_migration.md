# Audit Findings — Database Migrations

**Files reviewed:**
- `src/taskbrew/orchestrator/migration.py` (1352 lines, all 29 migrations, full read)
- `src/taskbrew/orchestrator/database.py` (init / schema-version section, lines 1-400)

**Reviewer:** audit-agent-04

---

## Finding 1 — Migrations 4 and 29 are NOT idempotent on crash-retry
- **Severity:** CRITICAL
- **Category:** correctness-bug
- **Location:** `src/taskbrew/orchestrator/migration.py:69-72` (migration 4) and `:1299-1300` (migration 29); applied by `:1344` (`executescript`) / `:1345-1349` (INSERT)
- **Finding:** Migrations 4 (`enhance_agent_messages`) and 29 (`add_stage1_completion_gate_columns`) use raw `ALTER TABLE ... ADD COLUMN` with no pre-check. If the process crashes, the connection dies, or the subsequent `INSERT INTO schema_migrations` on `migration.py:1345-1349` fails AFTER `executescript` has already committed (aiosqlite auto-commit with `isolation_level=None` — see `database.py:340`), the next run will re-execute the ALTERs and SQLite will raise `OperationalError: duplicate column name: message_type` (or `requires_fanout`). `apply_pending` has no try/except around this, so ALL further migrations halt forever.
- **Impact:** Any user whose process is interrupted (Ctrl-C, OOM, disk-full, power loss) between the DDL apply and the version-row insert has a permanently wedged database on next startup. Production upgrade blocker.
- **Fix:** Wrap each migration in an explicit BEGIN / COMMIT so the DDL and the `schema_migrations` INSERT are atomic, AND rewrite ALTER-based migrations to check `PRAGMA table_info(tasks)` before adding (the pattern `database.py:362-381` uses with try/except — which is what the main schema uses — proves the team knows the idiom but abandoned it here).

## Finding 2 — No transaction wraps DDL + version-record insert
- **Severity:** CRITICAL
- **Category:** correctness-bug
- **Location:** `src/taskbrew/orchestrator/migration.py:1341-1352` (`apply_pending`)
- **Finding:** The loop body runs `await self._db.executescript(sql)` (which commits, see `database.py:482-487`), then separately runs `await self._db.execute("INSERT INTO schema_migrations ...")` (also commits, `database.py:560-565`). There is a window where the schema has been mutated but the version row has not been persisted. Worse, the `Database` wrapper uses `isolation_level=None` (autocommit, `database.py:340`) and `executescript` in aiosqlite explicitly commits any outstanding transaction, so even wrapping with `transaction()` would not help without restructuring.
- **Impact:** A crash between DDL and version-insert silently half-applies the migration; next startup either reruns (breaking on non-idempotent ALTERs — Finding 1) or silently skips a real upgrade that partially happened, causing query errors in app code. No way to tell apart.
- **Fix:** Use a single `BEGIN IMMEDIATE` / COMMIT around both statements per migration; for SQLite, issue the DDL statements individually (not via `executescript`, which ends transactions) and the INSERT inside the same txn. If the txn rolls back, DDL rolls back too (SQLite supports transactional DDL).

## Finding 3 — No process-level lock — concurrent startups race
- **Severity:** HIGH
- **Category:** concurrency
- **Location:** `src/taskbrew/orchestrator/migration.py:1338-1352` and `database.py:389-394` (called unconditionally at `initialize()`)
- **Finding:** Every call to `Database.initialize()` invokes `MigrationManager.apply_pending()` without any cross-process coordination. Two simultaneously starting workers (gunicorn/uvicorn with `--workers 2`, systemd Restart, double-deploy) both read `current_version=N`, both try to apply N+1. For `CREATE TABLE IF NOT EXISTS` migrations, the loser just no-ops (OK). For migrations 4 and 29 (ALTER), the loser hits duplicate-column and crashes. Worse, both may insert `(version, name, applied_at)` rows — but `schema_migrations.version` is `PRIMARY KEY` in `database.py:201-205`, so the loser's INSERT raises IntegrityError and the migration is reported "applied" by one process while the other crashes.
- **Impact:** Multi-worker FastAPI deployments (explicitly documented as the target topology) will experience random worker crashes on upgrade days. Silent half-apply possible.
- **Fix:** Acquire `BEGIN EXCLUSIVE` on the main connection before reading `MAX(version)`, hold through the whole loop, commit at the end. Combined with WAL mode (already on, `database.py:342`) this serializes migrators across processes.

## Finding 4 — `get_current_version` swallows all exceptions and returns 0
- **Severity:** HIGH
- **Category:** error-handling
- **Location:** `src/taskbrew/orchestrator/migration.py:1320-1328`
- **Finding:** `except Exception: return 0` masks any error reading `schema_migrations` — locked DB, corrupted file, permission error, SQL syntax error, a user-typo'd alias. Returning 0 then causes `apply_pending` to re-run EVERY migration from version 1. Migrations 1-3, 5-28 are idempotent due to `IF NOT EXISTS`, but 4 and 29 will crash with duplicate-column. Even without crash, the `INSERT INTO schema_migrations (version, ...)` at `:1345-1349` will raise PRIMARY KEY violation on re-insert because rows for those versions already exist.
- **Impact:** Any transient read error on the migration table makes the database unrecoverable without manual intervention, and the root-cause exception is lost.
- **Fix:** Distinguish "table does not exist" (return 0) from any other exception (re-raise with context). Check table existence explicitly via `sqlite_master`.

## Finding 5 — Migration 28 redundantly creates tables already in base schema
- **Severity:** HIGH
- **Category:** api-contract
- **Location:** `src/taskbrew/orchestrator/migration.py:1240-1290` vs `database.py:207-239`
- **Finding:** Migration 28 creates `human_interaction_requests`, `task_chains`, `first_run_approvals` — but `database.py` `_SCHEMA_SQL` (lines 207-239) ALSO creates the exact same three tables during every `initialize()` call before migrations run. Because both use `CREATE TABLE IF NOT EXISTS`, the result depends on order: on fresh DB, base schema wins and migration 28 is a no-op (but is still recorded as "applied"). If the column definitions ever drift between the two locations, the discrepancy is silent — whoever runs first wins, and you have two sources of truth for the same schema.
- **Impact:** Schema drift between files is undetectable. Changing a column type in one file and forgetting the other produces divergent databases depending on migration history. The indexes on `:1275-1289` duplicate `database.py:276-291` with identical definitions.
- **Fix:** Either remove these tables from `_SCHEMA_SQL` (so migration 28 is the sole source), or mark migration 28 as historical/no-op and keep base schema as source. Pick one and delete the other.

## Finding 6 — `MAX(version)` falsy-check treats 0 as missing
- **Severity:** LOW
- **Category:** edge-case
- **Location:** `src/taskbrew/orchestrator/migration.py:1326`
- **Finding:** `return row["version"] if row and row["version"] else 0` — `row["version"]` of `0` (falsy) would be treated as "no migrations applied". Currently harmless because migration versions start at 1, but if a future developer adds a version-0 sentinel or a NULL-returning query, the check silently does the wrong thing.
- **Impact:** Latent footgun if schema conventions change.
- **Fix:** Explicit `None` check: `row["version"] if row and row["version"] is not None else 0`.

## Finding 7 — No schema-drift / tamper detection
- **Severity:** MEDIUM
- **Category:** missed-impl
- **Location:** `src/taskbrew/orchestrator/migration.py:13-1302` (MIGRATIONS list)
- **Finding:** Only `version` is used for decisions; `name` and `sql` are not hashed. If migration 15's SQL is edited after release (e.g. a typo fix), already-upgraded DBs silently retain the old schema while fresh installs get the new. No warning, no drift flag.
- **Impact:** Upgrades that "fix" a prior migration in-place never run on machines that already applied v15, producing silent schema divergence between old and new installs.
- **Fix:** Store `sha256(sql)` in `schema_migrations.checksum` and warn/error on mismatch at startup. Or document "never edit a released migration" and forbid via a lint.

## Finding 8 — ALTER TABLE migrations are not reversible; no rollback story
- **Severity:** MEDIUM
- **Category:** missed-impl
- **Location:** Migration 4 `:68-73` adds 4 columns to `agent_messages`; Migration 29 `:1291-1301` adds 2 columns to `tasks`
- **Finding:** There is no `down` migration. `ALTER TABLE DROP COLUMN` was only added in SQLite 3.35 (2021); many older production SQLite builds (Debian stable, RHEL 8 with system sqlite3) cannot drop columns without the 12-step table-rebuild workaround. Downgrading a binary after a botched deploy is impossible without a manual DB restore.
- **Impact:** Cannot roll back a bad release. Data-loss risk if DBAs attempt manual column drop without the workaround.
- **Fix:** Document that downgrade requires DB restore from backup. Optionally provide paired `down_sql` for each migration with explicit opt-in.

## Finding 9 — `aiosqlite` `executescript` commits and cannot be rolled back
- **Severity:** MEDIUM
- **Category:** correctness-bug
- **Location:** `src/taskbrew/orchestrator/migration.py:1344` → `database.py:482-487`
- **Finding:** `Database.executescript` calls `conn.executescript(sql)` then `conn.commit()`. `aiosqlite`/`sqlite3` `executescript` always performs an implicit `COMMIT` before running the script. Any BEGIN surrounding this is cancelled. Combined with `isolation_level=None` (autocommit, `database.py:340`), each `CREATE TABLE`/`CREATE INDEX` statement is its own transaction. Partial script failure (e.g. migration 20 creates 10 tables + 9 indexes — if CREATE TABLE #6 fails for any reason) leaves the first 5 tables persisted and the 6th+ unapplied, yet `schema_migrations` will NOT record the version (good), so next start re-runs — and the first 5 CREATE TABLEs no-op (OK, they used IF NOT EXISTS), but any extra state that leaked is stuck.
- **Impact:** Multi-statement migrations are not atomic across statements. For current migrations this is mostly hidden by `IF NOT EXISTS`, but a future destructive statement (DELETE/UPDATE) inserted mid-script would half-apply irreversibly.
- **Fix:** Split the SQL into statements, run each inside a single explicit BEGIN/COMMIT on the connection, NOT via executescript.

## Finding 10 — Migration 29 ALTER runs even though Database.initialize already tries to add chain_id/instance_token via try-except
- **Severity:** LOW
- **Category:** dead-code
- **Location:** `database.py:367-381` vs `migration.py:1291-1301`
- **Finding:** `database.py:367-381` adds `chain_id`, `approval_mode`, `instance_token`, `config_snapshot` via try/except ALTER. This is a parallel ad-hoc migration mechanism that runs every startup BEFORE the proper migration manager. It overlaps conceptually with migration 29 (which adds `requires_fanout`, `fanout_retries` to the same table). The two mechanisms have different idempotency guarantees (try/except vs. no guard) and can easily diverge.
- **Impact:** Two places define "patch the tasks table on startup" with different semantics. Future maintainers will add a column in the wrong place.
- **Fix:** Delete `database.py:360-387` (the ad-hoc try/except ALTERs and the deferred index block) and move those columns/indexes into proper numbered migrations preceding migration 29.

## Finding 11 — Foreign keys reference tables assumed to pre-exist; order is implicit
- **Severity:** LOW
- **Category:** edge-case
- **Location:** Migrations 1 (`:28` → tasks), 3 (`:64`), 5 (`:86`), 6 (`:102`), 7 (`:116`), 9 (`:140`), 20 prompt_performance (`:639`), 21 argument_evidence (`:739`), 26 knowledge_staleness (`:1143`), 27 threat_entries (`:1199`), 28 (`:1243`, `:1257-1258`, `:1269`)
- **Finding:** Every FK uses `REFERENCES tasks(id)` or `REFERENCES groups(id)` etc., which only works because `_SCHEMA_SQL` creates these first. If a future migration is accidentally numbered before a future base-table migration, or if the base schema is ever removed in favor of pure migrations, FK parsing still succeeds (SQLite defers FK validation to row insert), but logic will silently break. With `PRAGMA foreign_keys=ON` (`database.py:344`), FK violations at insert time would surface.
- **Impact:** Fragile ordering assumption, not a live bug.
- **Fix:** Document the invariant that `_SCHEMA_SQL` creates "core" tables and migrations extend. Or fold `_SCHEMA_SQL` into migration 0.

## Finding 12 — Newer binary vs older DB: forward migration OK, but downgrade silently corrupts
- **Severity:** MEDIUM
- **Category:** api-contract
- **Location:** `src/taskbrew/orchestrator/migration.py:1341-1352`
- **Finding:** The loop only applies versions `> current`. There is NO check for `current > max(MIGRATIONS)` — i.e., if the user downgrades the binary (rolled back deploy) and the DB has been upgraded past what the code knows about, the old binary happily opens the DB, records nothing, and starts issuing queries that reference columns/tables its code expects but relying on a schema shape it wasn't tested against. Some queries may fail; others may silently read wrong data.
- **Impact:** Rolling back the package after a successful upgrade can produce undefined behavior with no warning logged.
- **Fix:** At startup, compare `MAX(schema_migrations.version)` to the highest version in the in-memory MIGRATIONS list; if DB is newer, log a prominent warning or refuse to start.

## Finding 13 — No per-statement error handling inside apply_pending loop
- **Severity:** HIGH
- **Category:** error-handling
- **Location:** `src/taskbrew/orchestrator/migration.py:1341-1352`
- **Finding:** If any migration's `executescript` raises (syntax error introduced in a patch release, FK-constraint failure on pre-existing bad data, disk-full mid-way), the exception propagates up and `Database.initialize()` fails. No logging tells the operator WHICH migration failed beyond the generic `logger.info` printed BEFORE the attempt. No recovery hook. No "mark failed" flag.
- **Impact:** Troubleshooting a failed upgrade requires reproducing locally; the schema may be half-applied (Finding 9) and the version row is absent, so the next attempt retries the same broken SQL with no visibility that it was tried before.
- **Fix:** `try/except` around each migration, log the version+name on failure, re-raise with context. Consider recording a `failed_at` column in `schema_migrations`.

## Finding 14 — `datetime.now(timezone.utc).isoformat()` differs slightly from elsewhere
- **Severity:** LOW
- **Category:** api-contract
- **Location:** `src/taskbrew/orchestrator/migration.py:1348` vs `database.py:305-307` (`_utcnow`)
- **Finding:** Migration records `applied_at` via `datetime.now(timezone.utc).isoformat()` directly. Elsewhere the codebase uses a dedicated `_utcnow()` helper (`database.py:305-307`). Duplicates logic; if the helper ever changes format (e.g. to strip microseconds), `schema_migrations.applied_at` diverges from every other timestamp in the DB.
- **Impact:** Timestamp format drift; cosmetic.
- **Fix:** Import `_utcnow` or add `from .database import _utcnow`.

---

## Systemic issues observed across this slice

- **No transactional boundary around the DDL+version-row pair** — the single most important invariant of any migration system ("either the schema change AND the version record commit together, or neither does") is not enforced anywhere in this file. Every finding on atomicity cascades from this.
- **Two parallel migration mechanisms** coexist: the proper `MigrationManager` in this file AND ad-hoc try/except ALTERs in `Database.initialize()` (`database.py:360-387`). They have different idempotency semantics and different failure modes. Maintainers must know both.
- **Duplicate table definitions** between `database.py:_SCHEMA_SQL` and migration 28 for `human_interaction_requests`/`task_chains`/`first_run_approvals` — no single source of truth. The same issue applies to `_INDEX_SQL` vs the indexes inside migration 28.
- **No concurrency control** for multi-process deployments, despite WAL being enabled (which is a concurrency-readiness signal). Multi-worker uvicorn/gunicorn startups are a normal target topology.
- **No post-upgrade validation** — no `PRAGMA integrity_check`, no `PRAGMA foreign_key_check`, no assertion that expected tables exist. Silent partial applies go undetected until queries fail.
- **No rollback or checksum/drift detection** — once released, a migration's SQL is effectively the source of truth for DBs that have already run it; editing it silently produces divergent schemas across the user base.
- **All 29 migrations are additive (CREATE TABLE/INDEX + 6 ALTER ADD COLUMN)** — good. No destructive SQL (DROP, DELETE, TRUNCATE, UPDATE-without-WHERE) exists yet. The sole destructive-ish statements are the ALTER ADDs in migrations 4 and 29. But because there is no pattern or guardrail for a future destructive migration, whoever writes migration 30 (e.g. "drop deprecated agent_benchmarks") inherits all the above gaps.
