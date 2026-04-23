# Audit Findings — Intelligence (autonomy/coordination/helpers)

**Files reviewed:** __init__.py, _utils.py, autonomous.py, coordination.py, execution.py, checkpoints.py, clarification.py, escalation.py, impact.py, preflight.py, monitors.py
**Reviewer:** audit-agent-06b

## Finding 1 — `discover_work` does unbounded recursive FS scan
- **Severity:** HIGH
- **Category:** perf
- **Location:** autonomous.py:141-204
- **Finding:** `rglob("*.py")` twice + `rglob("*.md")`, blocking `read_text` inside async, no `.gitignore` exclusion (`.venv`, `node_modules`, `__pycache__`), one INSERT per discovery.
- **Impact:** Event-loop stall + `work_discoveries` flooded with vendored-code rows.
- **Fix:** Exclude vendored dirs, cap results, thread executor, batch inserts, dedupe.

## Finding 2 — `missing_test` detector matches by bare filename
- **Severity:** MEDIUM
- **Category:** correctness-bug
- **Location:** autonomous.py:167-179
- **Finding:** Tests detected by `{f.name ...}` — no module-path correlation; projects with `tests/test_foo.py` still emit "missing test" for every `src/foo.py` not adjacent.
- **Impact:** Discovery stream is pure noise.
- **Fix:** Correlate by module path; configurable test layouts.

## Finding 3 — `acquire_lock` never honours `expires_at` → zombie locks forever
- **Severity:** HIGH
- **Category:** concurrency
- **Location:** coordination.py:100-163
- **Finding:** Lock records have 1-hour `expires_at`, but `acquire_lock` relies solely on UNIQUE constraint; expired rows never deleted/overwritten. Only `detect_conflicts` checks `expires_at`.
- **Impact:** Any crash leaves permanent lock; TTL is cosmetic.
- **Fix:** DELETE expired on acquire (or upsert); periodic sweeper.

## Finding 4 — `detect_conflicts` is logically impossible to trigger
- **Severity:** MEDIUM
- **Category:** dead-code
- **Location:** coordination.py:156-170
- **Finding:** `GROUP BY file_path HAVING COUNT(*) > 1` against a table whose `file_path` has a UNIQUE index — the second INSERT already raised in `acquire_lock`.
- **Impact:** Multi-agent conflict detection never fires.
- **Fix:** Track attempts separately or remove function.

## Finding 5 — `steal_task` is non-atomic and never transitions task state
- **Severity:** HIGH
- **Category:** correctness-bug
- **Location:** coordination.py:386-398
- **Finding:** SELECT-then-UPDATE with no transaction; only sets `claimed_by`, not status/`started_at`/`claimed_at`; two concurrent stealers both succeed (last-write-wins).
- **Impact:** Two agents own the same task simultaneously.
- **Fix:** Single UPDATE `WHERE status='pending' AND claimed_by IS NULL` + rowcount==1 check.

## Finding 6 — `_ensure_consensus_tables` runtime DDL bypasses migrations
- **Severity:** MEDIUM
- **Category:** concurrency
- **Location:** coordination.py:263-272
- **Finding:** `consensus_proposals` created on every `create_proposal` call because it's not in migration.py. No indexes, no versioning.
- **Impact:** Environment-dependent schema; migrations can't touch safely.
- **Fix:** Move DDL to migration.py.

## Finding 7 — `cast_vote` has a check-then-insert race
- **Severity:** MEDIUM
- **Category:** concurrency
- **Location:** coordination.py:286-310
- **Finding:** SELECT duplicate-check then INSERT with no transaction; `consensus_votes` has no UNIQUE(proposal_id, voter_id).
- **Impact:** Double-votes skew tallies.
- **Fix:** UNIQUE index + INSERT OR IGNORE + rowcount check.

## Finding 8 — `tally_votes` counts abstentions in majority denominator
- **Severity:** LOW
- **Category:** correctness-bug
- **Location:** coordination.py:325-333
- **Finding:** `approve > total/2` where `total = approve+reject+abstain`. 5/0/6 abstain → "rejected".
- **Fix:** Use `approve+reject` as denominator or document rule.

## Finding 9 — `generate_standup.plan` is static boilerplate
- **Severity:** LOW
- **Category:** missed-impl
- **Location:** coordination.py:53
- **Finding:** `plan = f"Continue work on {N} in-progress task(s)."` — no task context. `blockers` misses tasks blocked on unresolved deps.
- **Fix:** Derive plan from priority/deps.

## Finding 10 — `decompose_with_reasoning` heuristic splits mangle descriptions
- **Severity:** MEDIUM
- **Category:** correctness-bug
- **Location:** autonomous.py:86-109
- **Finding:** Splits on every " and "/" then " regardless of semantics.
- **Impact:** Bogus subtasks persisted.
- **Fix:** Require LLM; restrict fallback to explicit list markers.

## Finding 11 — `resolve_bids` picks a winner but never assigns the task
- **Severity:** MEDIUM
- **Category:** missed-impl
- **Location:** autonomous.py:296-310
- **Finding:** Returns `{"winner": ...}` but doesn't update `tasks.claimed_by` or mark bids accepted/rejected. No caller does it.
- **Impact:** Priority negotiation half-implemented.
- **Fix:** Transactionally assign + mark bid outcomes.

## Finding 12 — `PreflightChecker` exists but is not enforced at task start
- **Severity:** HIGH
- **Category:** missed-impl
- **Location:** preflight.py:17; only caller is dashboard/routers/intelligence.py
- **Finding:** Only one caller of `run_checks` — a dashboard API route. Agent loop never invokes preflight before executing work.
- **Impact:** Budget-exceeded / dep-unresolved tasks execute anyway; preflight is a UI affordance, not a gate.
- **Fix:** Call `run_checks` in claim/start path; abort on failure.

## Finding 13 — Preflight task_completeness check intentionally ignores result
- **Severity:** LOW
- **Category:** edge-case
- **Location:** preflight.py:34-44
- **Finding:** Comment says "Don't fail on missing description, just warn" — `all_passed` unchanged.
- **Fix:** Drop or enforce.

## Finding 14 — escalation_monitor lacks backoff/alerting on repeated DB failure
- **Severity:** LOW
- **Category:** error-handling
- **Location:** monitors.py:27-57
- **Finding:** Bare `except Exception` just logs and retries every 5 min forever.
- **Fix:** Metric/notification after N consecutive failures; exponential backoff.

## Finding 15 — `find_similar_fix` LIKE pattern injection-unsafe
- **Severity:** MEDIUM
- **Category:** security
- **Location:** autonomous.py:416-420
- **Finding:** `failure_signature` interpolated into `LIKE '%{sig}%'` without escaping `%` / `_`. A signature of `"%"` matches every row.
- **Impact:** Wrong/attacker-controlled fix returned and replayed.
- **Fix:** Escape LIKE metachars or match by hash/equality.

## Finding 16 — `trace_dependencies` blocking I/O, no exclusion list, regex recompiled
- **Severity:** MEDIUM
- **Category:** perf
- **Location:** impact.py:22-61
- **Finding:** Synchronous open/read/parse in async function; `rglob("*.py")` with no `.venv` exclusion; regex recompiled in loop.
- **Fix:** Thread executor, compile regex once, exclude vendored dirs, cache.

## Finding 17 — `trace_dependencies` bare-Exception swallows
- **Severity:** LOW
- **Category:** error-handling
- **Location:** impact.py:37, 54-56
- **Fix:** Narrow/log.

## Finding 18 — `CheckpointManager.create_checkpoint` unconditional notification call
- **Severity:** LOW
- **Category:** edge-case
- **Location:** checkpoints.py:40-52
- **Finding:** Always calls `self._db.create_notification(...)`; AttributeError if DB impl lacks the method.
- **Fix:** Guard with hasattr or enforce at wiring.

## Finding 19 — `clarification.detect_ambiguity` uses substring match
- **Severity:** LOW
- **Category:** correctness-bug
- **Location:** clarification.py:32-37
- **Finding:** `if term in full_text` flags "etc" in "etcd", "some" in "something".
- **Fix:** `re.search(rf"\b{term}\b", ...)`.

## Finding 20 — `record_retry_outcome` pollutes avg recovery time with zeros on failure
- **Severity:** LOW
- **Category:** correctness-bug
- **Location:** autonomous.py:335-377
- **Finding:** Callers often pass `recovery_time_ms=0` on failure; running mean updated regardless, dragging average toward 0.
- **Fix:** Update avg only on success.

## Systemic issues
- **SQL concurrency naïveté:** `cast_vote`, `steal_task`, `acquire_lock` all do check-then-write with no transactions or CAS.
- **Schema drift:** Runtime DDL (`_ensure_consensus_tables`) bypasses migration.py.
- **Heuristics masquerade as intelligence:** decomposition, missing-test discovery, standup plan, blast radius — shallow heuristics whose output gets persisted as "findings".
- **Build-once, enforce-never:** `PreflightChecker` wired only into dashboard; `resolve_bids` picks winner but nothing binds.
- **Blocking I/O in async code:** `discover_work`, `trace_dependencies` do synchronous recursive filesystem scans with no exclusion list in the event loop.

**Counts:** HIGH 4, MEDIUM 7, LOW 9 (total 20)
