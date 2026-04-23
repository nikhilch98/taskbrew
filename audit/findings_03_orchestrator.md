# Audit Findings — Orchestrator Core

**Files reviewed:** `__init__.py`, `artifact_store.py`, `cost_manager.py`, `database.py`, `event_bus.py`, `interactions.py`, `notification_service.py`, `system_prompt_builder.py`, `task_board.py`, `webhook_manager.py`
**Reviewer:** audit-agent-03

## Finding 1 — `claim_task` not atomic: autocommit defeats transaction()
- **Severity:** CRITICAL
- **Category:** concurrency
- **Location:** database.py:321 (`isolation_level=None`) + database.py:467-478 + task_board.py:254-278
- **Finding:** `_create_connection` sets autocommit; `transaction()` issues literal `"BEGIN"` + `commit()` on a shared autocommit connection, so the SELECT-then-UPDATE in `claim_task` is not one unit. Other methods (`execute*`) bypass `_tx_lock` and touch the same connection.
- **Impact:** Two agents can double-claim the same task; the README SELECT-FOR-UPDATE guarantee is false.
- **Fix:** Drop `isolation_level=None` (or serialise differently), and implement claim as a single `UPDATE ... WHERE id = (SELECT ... LIMIT 1) RETURNING *`.

## Finding 2 — `claim_task` priority CASE built via f-string (latent SQLi)
- **Severity:** HIGH
- **Category:** security
- **Location:** task_board.py:247-252
- **Finding:** `CASE priority WHEN '{p}' THEN {v}` interpolates dict keys/values into SQL. Hardcoded today, but an obvious footgun if priorities/roles ever come from config or user input.
- **Impact:** Latent first-order SQL injection on a hot path.
- **Fix:** Precompute as a module constant; never f-string SQL fragments.

## Finding 3 — `create_task` INSERTs column missing from baseline schema
- **Severity:** HIGH
- **Category:** correctness-bug
- **Location:** task_board.py:156-175 vs database.py `_SCHEMA_SQL` (tasks table, ~line 25-45)
- **Finding:** `create_task` writes `requires_fanout`, but that column is only added by `MigrationManager` (migration.py:1299), not in `_SCHEMA_SQL`. If migrations fail (swallowed by `except Exception: pass` at database.py:383) or run after first insert, every `create_task` raises `no such column`.
- **Impact:** Core write path bricks silently on any migration regression.
- **Fix:** Add `requires_fanout INTEGER` and `fanout_retries INTEGER DEFAULT 0` to baseline `tasks` DDL.

## Finding 4 — Group completion race vs. goal-verification spawn
- **Severity:** HIGH
- **Category:** correctness-bug
- **Location:** task_board.py:417-447 + 454-509
- **Finding:** Non-terminal probe, goal-verification spawn, and group UPDATE are separate statements on the autocommit shared connection. A concurrent complete/cancel can observe an empty non-terminal set between spawn and commit and seal the group first.
- **Impact:** Group flips to `completed` despite verification gate intent; the very bug the gate exists to prevent.
- **Fix:** Single transaction on a non-autocommit connection, or CAS-style UPDATE guarded by "no non-terminal AND no goal_verification pending".

## Finding 5 — All query helpers share ONE connection; cursor cross-talk
- **Severity:** HIGH
- **Category:** concurrency
- **Location:** database.py:552-576 vs `acquire()` at 421-446
- **Finding:** Pool exists but `execute`, `execute_fetchall`, `execute_fetchone`, `execute_returning` all use `self._conn`. aiosqlite Connection.execute is not coroutine-reentrant — concurrent calls can return another coroutine's cursor rows.
- **Impact:** Wrong rows returned, "cursor closed" crashes under load.
- **Fix:** Route every helper through `async with self.acquire() as conn`.

## Finding 6 — Migration/bootstrap errors swallowed with bare `except Exception: pass`
- **Severity:** HIGH
- **Category:** error-handling
- **Location:** database.py:366-385
- **Finding:** ALTER TABLE loop and `_DEFERRED_INDEX_SQL` catch all exceptions to hide "column exists" but mask disk-full, syntax errors, etc.
- **Impact:** Silent partial migrations → cryptic failures downstream (see Finding 3).
- **Fix:** Match the specific `duplicate column name` error string only; fail loudly otherwise.

## Finding 7 — SSRF filter doesn't resolve DNS; trivial bypass / DNS rebinding
- **Severity:** HIGH
- **Category:** security
- **Location:** webhook_manager.py:55-77
- **Finding:** `_validate_url` only checks literal IPs and a tiny hostname blocklist. A DNS A record pointing at `169.254.169.254` (AWS IMDS) or `127.0.0.1` sails through. Also vulnerable to DNS rebinding between validate and fetch.
- **Impact:** SSRF → cloud metadata creds theft, internal net scanning.
- **Fix:** `getaddrinfo` the hostname at validate time AND pin the IP at request time; reject any private/loopback/link-local/reserved answer.

## Finding 8 — Webhook signatures: no timestamp binding, no algorithm version
- **Severity:** MEDIUM
- **Category:** security
- **Location:** webhook_manager.py:164-167
- **Finding:** HMAC covers only payload bytes. No signed timestamp → infinite replay; no `v1=` versioning → no algo rotation path.
- **Impact:** Captured deliveries replay forever; cannot upgrade hash.
- **Fix:** `X-Webhook-Timestamp` bound into HMAC input; Stripe-style `t=..,v1=..` header.

## Finding 9 — Webhook payload size unbounded; retries multiply impact
- **Severity:** MEDIUM
- **Category:** resource-leak
- **Location:** webhook_manager.py:145-152, 182-184
- **Finding:** `fire()` serialises arbitrary `data` dicts into both DB row and HTTP body; retries re-send.
- **Impact:** One fat event balloons SQLite row size, RAM, egress.
- **Fix:** Cap payload (e.g. 64 KiB) at `fire()`; truncate or drop with log.

## Finding 10 — Budget check-then-act race; 8-char UUID collision risk
- **Severity:** MEDIUM
- **Category:** concurrency
- **Location:** cost_manager.py:20-79 + 81-119
- **Finding:** `check_budget` then `record_spend` is non-atomic; parallel agents can both pass at 99/100 and overshoot. Dedup uses `data LIKE '%{id}%'` with 8-hex id (birthday collision ~77k budgets).
- **Impact:** Budget advisory-only; notification mis-dedup.
- **Fix:** Single UPDATE with `WHERE spent_usd + ? <= budget_usd`, check rowcount; use full UUIDs.

## Finding 11 — Event handlers fire-and-forgotten; cancelled on shutdown
- **Severity:** MEDIUM
- **Category:** concurrency
- **Location:** event_bus.py:40, 60
- **Finding:** `asyncio.create_task(...)` result is discarded. `NotificationService` re-emits, spawning more. Loop shutdown cancels in-flight DB writes.
- **Impact:** Mid-write cancellation; no drain path.
- **Fix:** Track pending tasks in a set, `add_done_callback(discard)`, await on shutdown.

## Finding 12 — Interaction endpoints have NO auth
- **Severity:** HIGH
- **Category:** security
- **Location:** dashboard/routers/interactions.py (all POSTs) + dashboard/app.py:309 (router include)
- **Finding:** `/api/interactions/{id}/approve|reject|respond|skip` are registered without `dependencies=[Depends(verify_auth)]`, unlike `/api/server/restart`. With default `AUTH_ENABLED=false` this is open to the network; first-run approvals gate destructive actions.
- **Impact:** Unauth network attacker approves/rejects any pending HITL decision.
- **Fix:** Add `dependencies=[Depends(verify_auth)]` to the interactions router (admin variant preferred).

## Finding 13 — Artifact listing skips traversal check
- **Severity:** MEDIUM
- **Category:** security
- **Location:** artifact_store.py:73-91
- **Finding:** `save_artifact`/`load_artifact` realpath-check, but `get_task_artifacts` / `get_group_artifacts` do not — a `group_id` with `..` segments or a planted symlink under `base_dir` leaks arbitrary directory listings via `os.listdir`.
- **Impact:** Directory enumeration outside sandbox.
- **Fix:** Validate `group_id`/`task_id` to `^[A-Za-z0-9_-]+$`; realpath-check before listdir.

## Finding 14 — `generate_task_id` races → duplicate PK under load
- **Severity:** HIGH
- **Category:** concurrency
- **Location:** database.py:486-500
- **Finding:** The UPDATE...RETURNING commits immediately (autocommit), runs on the shared non-reentrant connection. Two concurrent callers can read the same `val` and then INSERT two tasks with identical IDs.
- **Impact:** PK violation mid-`create_task`, leaving partially written rows (dependencies / usage) behind.
- **Fix:** Serialise via transaction on a non-autocommit connection, or move to UUID task IDs.

## Finding 15 — `classify_failure` keyword scan: substring false positives, first-match wins
- **Severity:** LOW
- **Category:** correctness-bug
- **Location:** task_board.py:1088-1126
- **Finding:** "retry count exceeded" → transient; "504 test file not found" hits both lists but returns transient because it's checked first.
- **Impact:** Wrong retry policy on mixed-keyword reasons.
- **Fix:** Word-boundary regex; prefer permanent on mixed hits.

## Finding 16 — Parent-failure cascade skips `blocked` children
- **Severity:** MEDIUM
- **Category:** correctness-bug
- **Location:** task_board.py:369-374
- **Finding:** `UPDATE tasks SET status='cancelled' WHERE parent_id=? AND status='pending'` — `blocked` excluded. Children linked by parent_id only (no `task_dependencies` row) never cascade.
- **Impact:** Orphaned `blocked` children; group never completes.
- **Fix:** Include `'blocked'` in the status filter.

## Finding 17 — Interaction payload: no size cap, silent idempotency
- **Severity:** LOW
- **Category:** security / api-contract
- **Location:** interactions.py:32-50
- **Finding:** `request_data` serialised verbatim into DB (could include echoed secrets). On `request_key` collision, the new request is silently discarded and the existing row returned — no warning.
- **Impact:** Secrets persistence risk; silent duplicate masking.
- **Fix:** Truncate payload; log on idempotency hit.

## Finding 18 — `recover_orphaned_tasks` at startup is HA-unsafe
- **Severity:** MEDIUM
- **Category:** correctness-bug
- **Location:** task_board.py:653-663
- **Finding:** Resets *every* `in_progress` row unconditionally. With WAL-mode SQLite shared between two workers, worker B boot rips tasks from worker A's live agents.
- **Impact:** Double-execution and output corruption in any HA / rolling-restart setup.
- **Fix:** Gate on single-instance proof (pidfile/advisory lock), or always use `recover_stale_in_progress_tasks` keyed on heartbeat.

## Finding 19 — `_validate_url` runs only at creation, not on fire
- **Severity:** LOW
- **Category:** security
- **Location:** webhook_manager.py:123-135, 145-152
- **Finding:** Rows already in the DB (pre-validator or seeded) bypass SSRF checks at fire time.
- **Impact:** Stale malicious URLs keep firing after the validator is tightened.
- **Fix:** Re-run `_validate_url` on each row in `fire()`; migrate/disable violators.

## Finding 20 — NotificationService: no unsubscribe, unbounded handler leak
- **Severity:** LOW
- **Category:** resource-leak
- **Location:** notification_service.py:43-47
- **Finding:** `subscribe()` adds handlers but no inverse. Repeated service creation (tests, hot-reload) leaks handlers pointing at dead DB connections.
- **Impact:** Test pollution; log spam from dead handlers in long-lived processes.
- **Fix:** Add `unsubscribe()` that removes `_handle_event` for each watched event.

---

## Systemic issues observed across this slice

- **Autocommit + shared connection mismatch**: `isolation_level=None` combined with all helpers using `self._conn` makes every "transactional" method silently non-atomic (Findings 1, 4, 5, 14). The connection pool exists but isn't used on hot paths.
- **Silent bootstrap error swallowing**: Bare `except Exception: pass` masks real migration failures (6), and baseline schema is out of sync with write paths (3).
- **Auth coverage gaps on mutating dashboard routers**: `/api/interactions/*` is unauthenticated by default (12); `verify_auth` is applied inconsistently.
- **Webhook hardening is checkbox-grade**: SSRF filter skips DNS (7), no payload cap (9), no timestamp/algo versioning on HMAC (8), no revalidation of stored URLs (19) — each a real exploit primitive.
- **State-machine transitions reinvented per method**: `fail_task` cascade misses `blocked` children (16); `recover_orphaned_tasks` is an HA-unsafe hammer (18); group completion races the verification spawn (4). No single source of truth for valid task transitions.

**Counts:** CRITICAL 1, HIGH 8, MEDIUM 7, LOW 4 (total 20)
