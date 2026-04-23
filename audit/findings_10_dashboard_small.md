# Audit Findings — Dashboard App + Small Routers

**Files reviewed:** app.py, chat_manager.py, models.py, routers/{_deps, agents, analytics, collaboration, comparison, costs, interactions, mcp_tools, presets, search, ws}.py, __init__.py files
**Reviewer:** audit-agent-10

## Finding 1 — Admin restart endpoint unauthenticated by default
- **Severity:** CRITICAL
- **Category:** security
- **Location:** app.py:164-173 + 132-147
- **Finding:** `verify_admin` → `verify_auth` returns when `AUTH_ENABLED` env not "true" (default false). `/api/server/restart` SIGTERMs the process.
- **Impact:** Unauthenticated caller can kill the server at will.
- **Fix:** Fail closed; require AuthManager enabled; real admin check.

## Finding 2 — MCP endpoints accept any Bearer token (no verification)
- **Severity:** CRITICAL
- **Category:** security
- **Location:** routers/mcp_tools.py:26-29
- **Finding:** `_get_token` only checks `"Bearer "` prefix; suffix never validated.
- **Impact:** Unauth attackers auto-complete tasks, route tasks, create approvals impersonating agents.
- **Fix:** Validate against instance-token table tied to agent_role/task_id.

## Finding 3 — REST auth bypassed when team_config absent
- **Severity:** CRITICAL
- **Category:** security
- **Location:** app.py:102-127
- **Finding:** Middleware short-circuits when `_tc` is None or `auth_enabled=false`; no default-deny.
- **Impact:** All REST mutating endpoints unauthenticated in default deployment.
- **Fix:** Default-deny; explicit opt-in for local dev.

## Finding 4 — WebSocket endpoints: no auth, no Origin check (CSWSH)
- **Severity:** HIGH
- **Category:** security
- **Location:** routers/ws.py:25-40, 81-167; app.py:113 skips `/ws*` from auth
- **Finding:** No Origin validation, no token required.
- **Impact:** CSWSH; attacker page drives agent chat sessions and burns API credits.
- **Fix:** Origin allow-list; token subprotocol or query-string bearer.

## Finding 5 — Event bus broadcast leaks to all clients (no tenant filtering)
- **Severity:** HIGH
- **Category:** security
- **Location:** app.py:149-158, ConnectionManager.broadcast 48-60
- **Finding:** Subscribes `*` and blasts every event to every connected socket.
- **Impact:** Any client (or CSWSH) sees raw orchestration events, task content, costs.
- **Fix:** Per-tenant/topic filtering.

## Finding 6 — No WS message-size limit, rate limit, or back-pressure
- **Severity:** HIGH
- **Category:** resource-leak
- **Location:** routers/ws.py:25-40; app.py:48-60
- **Finding:** `receive_text()` unbounded; `send_text` in broadcast no timeout.
- **Fix:** `max_size`, `asyncio.wait_for`, per-connection rate cap.

## Finding 7 — chat_websocket has no identity binding to sessions
- **Severity:** HIGH
- **Category:** security
- **Location:** routers/ws.py:81-167
- **Finding:** `start_session`/`chat_message` act on URL `agent_name` with no authz.
- **Fix:** Require auth; bind session to authenticated user; per-user limits.

## Finding 8 — CORS allow_credentials=True with unvalidated origins env var
- **Severity:** HIGH
- **Category:** security
- **Location:** app.py:83-97
- **Finding:** `CORS_ORIGINS` parsed without validating scheme or rejecting `*`.
- **Fix:** Validate scheme; reject `*`; log resolved origins.

## Finding 9 — Non-constant-time token compare
- **Severity:** MEDIUM
- **Category:** security
- **Location:** app.py:123-125
- **Finding:** `token not in (_tc.auth_tokens or [])` — linear equality, timing side-channel.
- **Fix:** `hmac.compare_digest`.

## Finding 10 — Jinja2 autoescape not explicitly configured
- **Severity:** MEDIUM
- **Category:** security
- **Location:** app.py:329-330
- **Finding:** Relies on Starlette defaults; no `autoescape=select_autoescape(...)`.
- **Fix:** Set explicit autoescape env.

## Finding 11 — /static mount bypasses auth
- **Severity:** MEDIUM
- **Category:** security
- **Location:** app.py:325-327, middleware skip 114
- **Fix:** Build-time scan and policy.

## Finding 12 — No global exception handler; handlers leak str(exc)
- **Severity:** MEDIUM
- **Category:** error-handling
- **Location:** app.py (no exception_handler); ws.py:107-112; interactions.py uses `HTTPException(500, str(e))`
- **Impact:** Info disclosure.
- **Fix:** Global handler, log server-side, generic client message.

## Finding 13 — ConnectionManager list not concurrency-safe
- **Severity:** MEDIUM
- **Category:** concurrency
- **Location:** app.py:34-60
- **Finding:** append/remove/iteration without lock across coroutines.
- **Fix:** `asyncio.Lock` or set.

## Finding 14 — Collaboration list endpoints unbounded
- **Severity:** MEDIUM
- **Category:** resource-leak
- **Location:** collaboration.py:205, 142-153, 249-269
- **Fix:** `Query(..., ge=1, le=200)`; cursor pagination.

## Finding 15 — Collaboration accepts caller-supplied identity fields
- **Severity:** HIGH
- **Category:** security
- **Location:** collaboration.py:21-30, 121-134
- **Finding:** `author`, `mentioned_user`, `user_id` from body/URL with no binding to authenticated user.
- **Fix:** Derive actor from auth context.

## Finding 16 — delete_comment / mark_mention_read have no authz
- **Severity:** HIGH
- **Category:** security
- **Location:** collaboration.py:180-196, 272-282
- **Finding:** Anyone can delete any comment; anyone marks mentions read.
- **Fix:** Owner-or-admin check.

## Finding 17 — /api/comparison opens arbitrary sqlite files named in project config
- **Severity:** MEDIUM
- **Category:** security
- **Location:** comparison.py:33-47, 50-117, 120-187
- **Finding:** `_resolve_db_path` joins project_dir/db_path but no prefix check; absolute paths or `..` accepted.
- **Fix:** Resolve-and-assert prefix within project_dir.

## Finding 18 — Module-level mutable globals for dep injection
- **Severity:** MEDIUM
- **Category:** correctness-bug
- **Location:** _deps.py:8-14, ws.py:14-22, comparison.py:19-25, interactions.py:13-18, mcp_tools.py:13-23, presets.py:14-21
- **Finding:** Module globals prevent multiple app instances; state leaks across tests.
- **Fix:** Attach to `app.state`.

## Finding 19 — Search builds LIKE pattern from unescaped user input
- **Severity:** MEDIUM
- **Category:** perf / edge-case
- **Location:** search.py:22
- **Finding:** `like = f"%{q}%"`; `%` and `_` treated as wildcards; `q` has no max_length.
- **Impact:** `q="%%%%"` → full-scan 5 tables; DoS.
- **Fix:** Escape `%`/`_`; max_length; FTS.

## Finding 20 — Search has no offset/cursor pagination
- **Severity:** LOW
- **Category:** api-contract
- **Location:** search.py:17
- **Fix:** offset or cursor.

## Finding 21 — SQLite datetime comparisons vs ISO8601-with-offset strings
- **Severity:** LOW
- **Category:** correctness-bug
- **Location:** comparison.py:166-172, collaboration.py:115-117
- **Fix:** Normalize to consistent UTC format.

## Finding 22 — PauseResumeBody.role=="all" is undocumented magic
- **Severity:** LOW
- **Category:** api-contract
- **Location:** agents.py:29-57
- **Fix:** Separate pause-all endpoint or explicit flag.

## Finding 23 — Pydantic bodies lack size/regex/URL validators
- **Severity:** MEDIUM
- **Category:** api-contract
- **Location:** models.py
- **Finding:** No `max_length`, `pattern`, URL scheme check (CreateWebhookBody.url entirely unchecked).
- **Impact:** Oversized inputs (DB bloat), SSRF via webhook URL, ReDoS.
- **Fix:** Validators.

## Finding 24 — Mutable default args in pydantic models
- **Severity:** LOW
- **Category:** correctness-bug
- **Location:** models.py:126, 161, 218, 462
- **Fix:** `Field(default_factory=list)`.

## Finding 25 — ChatManager.start_session race: two subprocesses for same agent
- **Severity:** MEDIUM
- **Category:** concurrency
- **Location:** chat_manager.py:61-89
- **Fix:** Per-agent `asyncio.Lock`.

## Finding 26 — send_message appends user turn before success → history poisoning on timeout
- **Severity:** LOW
- **Category:** correctness-bug
- **Location:** chat_manager.py:106-123
- **Fix:** Append only on success or mark errored turns.

## Finding 27 — Attacker can terminate other users' chat sessions
- **Severity:** MEDIUM
- **Category:** security
- **Location:** ws.py:158-167 (finally → stop_session)
- **Finding:** WS disconnect calls `stop_session(agent_name)`; second WS to same agent_name kills the first.
- **Fix:** Scope sessions per connection id.

## Finding 28 — /api/agents/pause{resume} unauthed and unscoped
- **Severity:** HIGH
- **Category:** security
- **Location:** agents.py:29-57
- **Fix:** Auth + actor log + project scoping.

## Finding 29 — mcp_route_task trusts body for group_id, priority, created_by
- **Severity:** HIGH
- **Category:** security
- **Location:** mcp_tools.py:124-142
- **Fix:** Bind agent_role to verified token; validate priority enum; membership check.

## Finding 30 — Presets cached forever at module import
- **Severity:** LOW
- **Category:** correctness-bug
- **Location:** presets.py:14-21
- **Fix:** TTL or reload endpoint.

## Finding 31 — /api/chat/* endpoints unauthed
- **Severity:** HIGH
- **Category:** security
- **Location:** ws.py:53-79 via register_chat_routes
- **Fix:** Require auth; per-user scoping.

## Finding 32 — Stale hand-maintained version/description
- **Severity:** LOW
- **Category:** dead-code
- **Location:** app.py:76-77
- **Fix:** Pull from package metadata.

## Finding 33 — Interactions approve/reject/respond/skip all unauthenticated
- **Severity:** HIGH
- **Category:** security
- **Location:** interactions.py:37-99
- **Fix:** Auth + record approver.

## Finding 34 — /api/v1 duplicates delegate to underlying handlers
- **Severity:** LOW
- **Category:** api-contract
- **Location:** app.py:354-412
- **Fix:** `include_router(prefix=...)`.

## Finding 35 — /docs, /redoc, /openapi.json publicly exposed
- **Severity:** LOW
- **Category:** security
- **Location:** app.py:108 skip_paths
- **Fix:** Disable docs in prod or gate behind auth.

## Systemic issues
- **Auth is a no-op in the default deployment path:** `AUTH_ENABLED` defaults false, and middleware short-circuits when team_config is absent.
- **Count of unauthed-but-should-be-authed mutating endpoints in this slice: ~20.**
- **WebSocket layer entirely unhardened:** no Origin/CSWSH check, no auth, no size/rate limits, unbounded broadcast fan-out to every client.
- **Module-level mutable dependency state** prevents multi-tenant deployment and leaks state across tests.
- **Caller-supplied identity everywhere:** author/mentioned_user/user_id/created_by/agent_role come from body/URL with no binding to authenticated actor.
- **Pydantic models have zero size/regex/URL validators** — oversized inputs, SSRF via webhook URLs, ReDoS on prompts.
- **No global exception handler** — `str(exc)` passed to WS/HTTP clients, leaking internals.

**Counts:** CRITICAL 3, HIGH 12, MEDIUM 12, LOW 8 (total 35)
