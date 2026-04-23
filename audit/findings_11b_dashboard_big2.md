# Audit Findings — Dashboard (system/pipelines)

**Files reviewed:** routers/system.py, routers/pipeline_editor.py, routers/pipelines.py
**Reviewer:** audit-agent-11b

## Finding 1 — Admin auth dep declared but never applied
- **Severity:** CRITICAL
- **Category:** security
- **Location:** system.py:37-43 (and every mutating route)
- **Finding:** `_verify_admin` stored via `set_auth_deps` but no route uses `Depends(_verify_admin)`. Create/delete project, activate, role CRUD, team settings, budgets, webhooks, A/B tests all open.
- **Impact:** Any network-reachable caller mutates config, deletes roles (cancelling agents), registers webhooks.
- **Fix:** Add `dependencies=[Depends(_verify_admin)]` on all mutating routes; fail-closed.

## Finding 2 — Path traversal via role_name in YAML write/delete
- **Severity:** CRITICAL
- **Category:** security
- **Location:** system.py:441, 571, 590
- **Finding:** `create_role` validates `^[a-z][a-z0-9_]*$`; `update_role_settings` and `delete_role` interpolate `role_name` directly into `Path(...)/f"{role_name}.yaml"` with no validation.
- **Impact:** Arbitrary YAML write/delete inside project dir tree (e.g. overwrite `team.yaml`).
- **Fix:** Re-apply regex; resolve+verify path stays inside roles dir.

## Finding 3 — /api/browse-directory shells osascript unauthed
- **Severity:** HIGH
- **Category:** security
- **Location:** system.py:107-142
- **Finding:** Any caller pops native folder picker on operator's desktop; no auth, no rate limit.
- **Fix:** Admin auth + loopback-only + rate limit.

## Finding 4 — activate_project swaps global orch and spawns agents unauthed
- **Severity:** HIGH
- **Category:** security
- **Location:** system.py:156-180
- **Finding:** Unauth POST swaps global orchestrator, resubscribes broadcast, calls `start_agents(orch)`.
- **Fix:** Admin auth + activation lock; await prior deactivate drain before swap.

## Finding 5 — YAML writes are non-atomic and unlocked
- **Severity:** HIGH
- **Category:** concurrency
- **Location:** system.py:259-290, 443-469, 550-551, 592-599; pipeline_editor.py:47-53
- **Finding:** Every write is `open(path, "w")` (truncate+write); no temp+rename, no lock.
- **Impact:** Lost updates; crash mid-write leaves truncated YAML preventing project load.
- **Fix:** Write-to-temp + `os.replace`, guarded by asyncio lock per file/project.

## Finding 6 — delete_role cancels tasks without awaiting; mutates orch lists unlocked
- **Severity:** HIGH
- **Category:** concurrency
- **Location:** system.py:614-628
- **Finding:** Pops `orch._agent_tasks_by_id`, calls `stop()`/`cancel()` but never awaits; mutates `_agent_loops` and `agent_tasks` with no lock.
- **Fix:** Move to orch API that awaits cancellation under a lock.

## Finding 7 — Preset merge can inject unknown fields
- **Severity:** MEDIUM
- **Category:** security
- **Location:** system.py:492-512
- **Finding:** Preset dict merged into `body` with only a few metadata keys removed; no allow-list of role fields before merged dict is consumed.
- **Fix:** Whitelist merged keys.

## Finding 8 — create_role persists YAML before `_parse_role` validates
- **Severity:** MEDIUM
- **Category:** correctness-bug
- **Location:** system.py:546-556
- **Finding:** YAML written then parsed; if parse raises, disk file remains but role not registered.
- **Fix:** Parse first, then write + register atomically.

## Finding 9 — create_role skips validate_routing
- **Severity:** MEDIUM
- **Category:** correctness-bug
- **Location:** system.py:474-558
- **Finding:** `update_role_settings` runs `validate_routing` and rolls back; `create_role` does not.
- **Fix:** Run validate_routing after register; roll back YAML+memory if invalid.

## Finding 10 — update_role_settings has no rollback on YAML write failure
- **Severity:** MEDIUM
- **Category:** correctness-bug
- **Location:** system.py:340-471
- **Finding:** Memory rollback covers validate only; IOError during `yaml.dump` leaves memory updated but disk stale.
- **Fix:** Try/except around YAML write; restore snapshot on failure.

## Finding 11 — Webhook URL accepted without SSRF checks
- **Severity:** MEDIUM
- **Category:** security
- **Location:** system.py:756-770
- **Finding:** `create_webhook` stores any `url`; no scheme allow-list, no host validation (loopback, 169.254.169.254, file://).
- **Fix:** Restrict http/https; reject private/loopback/metadata.

## Finding 12 — Budget period math tz/DST-naive; scope unvalidated
- **Severity:** LOW
- **Category:** correctness-bug
- **Location:** system.py:713-735
- **Finding:** Daily resets by `replace(hour=0)` on UTC now; weekly resets as "now + 7 days".
- **Fix:** Align to UTC day boundary; enum-check scope.

## Finding 13 — A/B test variants not schema-validated
- **Severity:** LOW
- **Category:** validation
- **Location:** system.py:791-801
- **Finding:** `variant_a/b` JSON-dumped verbatim.
- **Fix:** Pydantic sub-model.

## Finding 14 — Settings GETs leak internals unauthed
- **Severity:** MEDIUM
- **Category:** security
- **Location:** system.py:205-225, 295-337
- **Finding:** `db_path`, dashboard host/port, system_prompts, tool lists returned to unauth callers.
- **Fix:** Admin auth; redact db_path when auth_enabled.

## Finding 15 — Pipeline editor mutations unauthed; write team.yaml
- **Severity:** HIGH
- **Category:** security
- **Location:** pipeline_editor.py:117-274
- **Finding:** All PUT/POST/DELETE mutate `_pipeline` and persist via `save_pipeline` with no auth dep.
- **Fix:** Apply admin dep.

## Finding 16 — GET /api/pipeline has disk side effect
- **Severity:** MEDIUM
- **Category:** api-contract
- **Location:** pipeline_editor.py:76-90
- **Finding:** Reads trigger `migrate_routes_to_pipeline` + `_save_pipeline` when edges empty but routes exist.
- **Fix:** Do migration once at startup.

## Finding 17 — update_pipeline_full skips on_failure/task_types validation
- **Severity:** MEDIUM
- **Category:** validation
- **Location:** pipeline_editor.py:129-145
- **Finding:** Validates from/to roles but not `on_failure` enum or `task_types` shape, unlike `add_pipeline_edge`.
- **Fix:** Same enum check.

## Finding 18 — pipeline validate endpoint unauthed; leaks topology
- **Severity:** LOW
- **Category:** security
- **Location:** pipeline_editor.py:282-375
- **Fix:** Require auth.

## Finding 19 — pipelines.py GETs unauthed + N+1 queries
- **Severity:** MEDIUM
- **Category:** security / perf
- **Location:** pipelines.py:20-191
- **Finding:** GET routes expose group/task/cost data without auth; per-row stats queries.
- **Fix:** Gate with auth; aggregate JOINs.

## Finding 20 — group_id echoed into error message
- **Severity:** LOW
- **Category:** security
- **Location:** pipelines.py:111-120
- **Finding:** Unsanitized `group_id` returned in `HTTPException(404, ...)`.
- **Fix:** Limit/escape echoed input.

## Finding 21 — `status` query string not enum-checked
- **Severity:** LOW
- **Category:** validation
- **Location:** pipelines.py:51-65
- **Fix:** Allow-list valid statuses.

## Finding 22 — `_pipeline` global has no lock
- **Severity:** MEDIUM
- **Category:** concurrency
- **Location:** pipeline_editor.py:30-44
- **Fix:** Module-level `asyncio.Lock`.

## Finding 23 — No size caps on persisted payloads
- **Severity:** LOW
- **Category:** resource-leak
- **Location:** system.py:668-801
- **Finding:** `message`, `data`, webhook `url`, A/B variant JSON stored with no length limits.
- **Fix:** Length constraints in pydantic.

## Systemic issues
- **Auth façade not wired** — `_verify_admin` injected but never used as a dependency; every destructive endpoint open by default. Dominant risk.
- **Non-atomic, unlocked YAML writes** — no temp+rename, no asyncio lock; concurrent edits corrupt configs and crashes truncate them.
- **Path-traversal guards applied inconsistently** — role_name regex protects create but not update/delete.
- **Validation asymmetry** between sibling endpoints (create_role vs update_role_settings; update_pipeline_full vs add_pipeline_edge).
- **Side effects in GETs** (`/api/pipeline` auto-migrates to disk, `/api/browse-directory` spawns GUI) violate REST semantics.
- **N+1 DB patterns** in pipelines.py.

**Counts:** CRITICAL 2, HIGH 5, MEDIUM 10, LOW 6 (total 23)
