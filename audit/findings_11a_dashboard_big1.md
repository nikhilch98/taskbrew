# Audit Findings — Dashboard (tasks/usage/git/exports)
**Files reviewed:** src/taskbrew/dashboard/routers/tasks.py, src/taskbrew/dashboard/routers/usage.py, src/taskbrew/dashboard/routers/git.py, src/taskbrew/dashboard/routers/exports.py, src/taskbrew/dashboard/routers/_deps.py
**Reviewer:** audit-agent-11a

## Finding 1 — No authentication / authorization on any endpoint
- **Severity:** C
- **Category:** security
- **Location:** tasks.py, usage.py, git.py, exports.py (all routes)
- **Finding:** `_deps.get_orch()` only checks that an orchestrator exists; there is no user principal, no session, and no per-user scoping. Every endpoint is effectively unauthenticated, including destructive ones (cancel, reassign, PATCH, batch, template/workflow creation).
- **Impact:** Anyone reaching the dashboard port can read/modify all tasks, dump the entire DB via `/api/export/full`, exec git, and scrape host usage/profile tokens.
- **Fix:** Add an auth dependency (bearer/session) and scope queries by principal; bind the dashboard to loopback by default if auth is absent.

## Finding 2 — PATCH `/api/tasks/{task_id}` builds SQL with f-string from dict keys
- **Severity:** H
- **Category:** security
- **Location:** tasks.py:334-344
- **Finding:** `set_clauses = ", ".join(f"{k} = ?" for k in updates)` then f-string into `UPDATE tasks SET {set_clauses}...`. Today the keys are hardcoded but the pattern is a SQL-identifier-injection footgun for any future edit that sources keys from the body.
- **Impact:** Regression-prone SQLi vector; also no `updated_at` bump and no group_id authz.
- **Fix:** Use a strict hardcoded field mapping, not f-string over keys.

## Finding 3 — Arbitrary state transitions via PATCH and `/complete`
- **Severity:** H
- **Category:** correctness-bug
- **Location:** tasks.py:316-344 (PATCH), tasks.py:298-308 (`/complete`)
- **Finding:** PATCH accepts any status in VALID_STATUSES with no transition rules (e.g., completed→pending, cancelled→in_progress). `/complete` never checks current status.
- **Impact:** Corrupts completion metrics; re-triggers downstream events; breaks rejection-cycle/guardrail invariants.
- **Fix:** Enforce an allowed_transitions state machine; reject illegal transitions with 409.

## Finding 4 — Batch ops unbounded (`task_ids` size not capped)
- **Severity:** H
- **Category:** resource-leak
- **Location:** tasks.py:355-364
- **Finding:** `body.task_ids` has no length cap; downstream likely builds an IN-clause with N placeholders. SQLite default SQLITE_MAX_VARIABLE_NUMBER=32766.
- **Impact:** DoS; potential SQLite errors or silent truncation.
- **Fix:** `max_items=500` on pydantic model; server-side chunking.

## Finding 5 — CSV export vulnerable to formula injection
- **Severity:** H
- **Category:** security
- **Location:** tasks.py:575-585 (`/api/export`), exports.py:22-41 (`_csv_response`)
- **Finding:** Task titles/descriptions starting with `=`, `+`, `-`, `@`, tab, or CR are written raw; Excel/Sheets executes them as formulas (DDE/URL exfil).
- **Impact:** Admin opening an export can leak data to attacker-controlled URLs.
- **Fix:** Prefix dangerous leading chars with `'`; quote-all.

## Finding 6 — `_csv_response` fieldnames come from `rows[0].keys()`
- **Severity:** M
- **Category:** correctness-bug
- **Location:** exports.py:27-41
- **Finding:** DictWriter fieldnames blindly trust DB column names; future schema additions (e.g. a JSON blob column) could bloat exports or include internal-only fields.
- **Impact:** Latent data leakage and CSV shape drift.
- **Fix:** Explicit whitelist per endpoint.

## Finding 7 — `/api/export` and `/api/export/full` pull entire tables into memory
- **Severity:** H
- **Category:** perf / resource-leak
- **Location:** tasks.py:565-585, exports.py:55-86
- **Finding:** `SELECT * FROM tasks` / `task_usage` / `artifacts` with no LIMIT, no streaming; JSON/CSV serialization is sync on the full list.
- **Impact:** OOM / event-loop stalls on large projects.
- **Fix:** Paginate; use `StreamingResponse`; cap rows.

## Finding 8 — `get_artifact_content` path-traversal hole in structured branch
- **Severity:** H
- **Category:** security
- **Location:** tasks.py:464-498
- **Finding:** The flat-file branch does `resolve()`+`relative_to()` containment; the structured branch `base / group_id / task_id / filename` does NOT. `filename`, `group_id`, `task_id` arrive unsanitized from the URL.
- **Impact:** Potential arbitrary file read inside (or possibly outside) the project dir depending on `store.load_artifact` internals.
- **Fix:** Apply the same resolve+relative_to containment to the structured path; reject `..`/`/`/NUL in any segment.

## Finding 9 — Artifact content read with no size cap; XSS risk if rendered inline
- **Severity:** M
- **Category:** security / resource-leak
- **Location:** tasks.py:459-502
- **Finding:** `flat_path.read_text()` and `output_text` pulled with no max size; agent-authored content can contain `<script>` if any UI preview renders as HTML.
- **Impact:** OOM on huge artifacts; stored XSS in preview surface.
- **Fix:** Cap read size (e.g. 2 MiB); sanitize in renderer; CSP on dashboard shell.

## Finding 10 — Destructive task endpoints have no authz / group scoping
- **Severity:** H
- **Category:** security
- **Location:** tasks.py:275-364 (cancel, retry, reassign, complete, PATCH, batch)
- **Finding:** Any task_id is reachable; no group-membership or creator check.
- **Impact:** Cross-group tampering; DoS on in-flight agent work.
- **Fix:** Add RBAC; verify caller's scope vs task group.

## Finding 11 — `/api/metrics/timeseries` unknown-range fallback is brittle
- **Severity:** M
- **Category:** correctness-bug
- **Location:** tasks.py:493-508
- **Finding:** Unknown `time_range` falls through to the `today` delta, but `since` recomputation depends on the string comparison `== "today"` — mismatched branches between delta selection and since-branch on unknown inputs.
- **Impact:** Silently-wrong metrics; future-edit hazard.
- **Fix:** Explicit match; 400 on unknown `time_range`.

## Finding 12 — `/api/metrics/timeseries` unbounded time range + bucket cardinality
- **Severity:** M
- **Category:** perf
- **Location:** tasks.py:510-528
- **Finding:** No cap on number of buckets or models returned; long-lived projects can return huge JSON.
- **Impact:** Slow dashboard; large payloads.
- **Fix:** Max bucket count; downsample above threshold.

## Finding 13 — `/api/metrics/roles` uses `SUBSTR(..., INSTR(..., '-') - 1)` — empty role for rows without hyphen
- **Severity:** M
- **Category:** correctness-bug
- **Location:** tasks.py:533-545
- **Finding:** `INSTR=0` → `SUBSTR length=-1` → SQLite returns ''. Rows without a hyphen in `agent_id` collapse into a bogus empty role.
- **Impact:** Wrong cost attribution.
- **Fix:** Store role as its own column, or `CASE WHEN INSTR>0 THEN ... END`.

## Finding 14 — `git_file_diff` accepts `:path` param; `cwd=None` when no project active
- **Severity:** M
- **Category:** security
- **Location:** git.py:134-142, 30-41 (`_get_project_dir`)
- **Finding:** `create_subprocess_exec` avoids shell-injection and `--` is present, so arg-injection is blocked. But `_get_project_dir()` can return None, leaving `cwd=None` which runs `git` in the dashboard's actual CWD — may be an unrelated repo and leaks its log/diff.
- **Impact:** Leak of unrelated repo contents when no project is active.
- **Fix:** 400 when project_dir is None; reject NUL/`..` in file_path.

## Finding 15 — All git endpoints are unauth'd — full repo log/diff leak
- **Severity:** H
- **Category:** security
- **Location:** git.py (all routes)
- **Finding:** No auth; full commit log, working-tree diff, and staged diff are readable by any caller.
- **Impact:** Source-code + in-progress secret exfiltration.
- **Fix:** Auth + loopback binding.

## Finding 16 — `usage.py` spawns host CLIs via PATH resolution; returns OAuth profile
- **Severity:** H
- **Category:** security
- **Location:** usage.py:108-173, 259-318, 365-385
- **Finding:** `shutil.which("claude")` / `"gemini"` is invoked on a GET request (no auth), with fallback to hardcoded paths. `_fetch_profile` sends the session OAuth token to Anthropic and caches the response; the full profile is then returned through `/api/usage/summary`.
- **Impact:** PATH-hijack to arbitrary binary; PII/plan leak; DoS by triggering 50-second pexpect timeouts.
- **Fix:** Absolute binary paths; feature-flag + auth gate; do not return full profile; concurrency cap.

## Finding 17 — `_fetch_usage_via_cli` has no concurrency lock
- **Severity:** H
- **Category:** resource-leak / perf
- **Location:** usage.py:176-213, 321-357
- **Finding:** Under cache-miss, N concurrent callers spawn N `claude` processes (up to 50s each). No `asyncio.Lock`.
- **Impact:** Fork-bomb; dashboard stall.
- **Fix:** Serialize cache-miss fills with `asyncio.Lock`.

## Finding 18 — `_parse_session` does unbounded sync file read in async handler
- **Severity:** M
- **Category:** resource-leak
- **Location:** usage.py:414-462
- **Finding:** `open(path)` in an async route; JSONL files grow to hundreds of MB; blocks event loop.
- **Impact:** Slow `/api/usage/summary` response.
- **Fix:** Cap bytes read; use a thread or aiofiles.

## Finding 19 — `/api/export/tasks` `since` is string-compared without validation
- **Severity:** M
- **Category:** correctness-bug
- **Location:** exports.py:94-129
- **Finding:** `since` is str|None; SQLite compares lexicographically. `since=banana` silently returns 0 rows; `since=2026` matches everything ≥ "2026".
- **Impact:** Silent mis-filtering.
- **Fix:** `datetime.fromisoformat` validate; 400 on failure.

## Finding 20 — Template/workflow bodies have no size or schema caps
- **Severity:** M
- **Category:** resource-leak / security
- **Location:** tasks.py:433-450, 529-546
- **Finding:** `body.steps` is `json.dumps`'d into a TEXT column; templates contain title/description strings later interpolated into agent prompts — unbounded input enables stored-size attacks and potential prompt-injection.
- **Impact:** Storage exhaustion; agent prompt-injection via templates.
- **Fix:** Size caps; schema validation for `steps`.

## Finding 21 — `get_board_filters` leaks all groups/roles
- **Severity:** L
- **Category:** security
- **Location:** tasks.py:462-472
- **Finding:** Returns every group title and role name with no scoping.
- **Impact:** Enumeration aid.
- **Fix:** Scope by user.

## Finding 22 — `get_group_graph` has no group-ownership check
- **Severity:** M
- **Category:** security
- **Location:** tasks.py:79-107
- **Finding:** Placeholders are bound safely (no SQLi), but any caller can enumerate any group's task graph by id.
- **Impact:** Cross-group enumeration.
- **Fix:** Authz check.

## Finding 23 — `/api/health` leaks DB exception text
- **Severity:** L
- **Category:** security / api-contract
- **Location:** tasks.py:33-48
- **Finding:** 503 body includes `str(e)` — file paths / schema hints; also reaches private `_db`.
- **Impact:** Minor info leak.
- **Fix:** Return opaque error; add a public ping().

## Finding 24 — Guardrail depth walks use hardcoded caps (100 / 10)
- **Severity:** L
- **Category:** correctness-bug
- **Location:** tasks.py:227-263
- **Finding:** Walk terminates silently at the cap instead of erroring, allowing deliberately deep chains to bypass the depth limit.
- **Impact:** Guardrail escape at edge cases.
- **Fix:** Error when cap hit; one shared helper.

## Finding 25 — `search_tasks` accepts unbounded `limit`/`offset`
- **Severity:** M
- **Category:** perf / resource-leak
- **Location:** tasks.py:261-279
- **Finding:** `limit: int = 50` has no `le`; caller can ask for 10M rows.
- **Impact:** DoS.
- **Fix:** `Query(50, ge=1, le=500)`.

## Systemic issues
- **No authentication layer at all.** Any caller reaching the dashboard port has full read/write to tasks, DB-wide export, git log/diff, and can trigger subprocess spawns — in combination this is a dashboard-compromise = host-compromise scenario.
- **Direct SQL and private `_db` reach-through in routers** (~20 sites), with f-string SQL-identifier patterns and no repository layer — SQL regressions are one edit away.
- **No pagination/size caps** on list, batch, export, search, artifact read — uniform DoS/OOM surface.
- **Blocking operations in async handlers:** `pexpect.spawn` (50s), `Path.read_text()` on unbounded artifacts, sync `open()` for huge JSONL — event-loop hazards.
- **Weak state-machine + no CSV-injection protection + no lifecycle invariants** — classic LOB-app footguns repeated consistently across routers.
