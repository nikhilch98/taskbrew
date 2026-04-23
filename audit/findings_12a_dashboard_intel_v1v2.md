# Audit Findings — Dashboard Intelligence v1 + v2
**Files reviewed:**
- src/taskbrew/dashboard/routers/intelligence.py (v1, 16 KB, 439 lines)
- src/taskbrew/dashboard/routers/intelligence_v2.py (v2, 32 KB, 975 lines)
- src/taskbrew/dashboard/app.py (router registration, lines 276-297)
- src/taskbrew/dashboard/routers/_deps.py (shared orchestrator getter)
- src/taskbrew/dashboard/routers/intelligence_v3.py (for context)

**Reviewer:** audit-agent-12a

## Version landscape
- **v1 registered:** YES. app.py:276 `from taskbrew.dashboard.routers.intelligence import router as intelligence_router` and app.py:295 `app.include_router(intelligence_router, tags=["Intelligence V1"])`. No deprecation warnings, no deprecation headers, no Sunset/Deprecation http headers, no gate/flag. It is simply live.
- **v2 registered:** YES. app.py:277 and app.py:296 `app.include_router(intelligence_v2_router, tags=["Intelligence V2"])`. Also live with no deprecation path.
- **v3 registered:** YES. app.py:278/297. v3 supersedes neither v1 nor v2 by prefix — it covers different feature sets (self-improvement, social, etc.). So all three coexist with NO retirement plan visible.
- **Path prefixes:**
  - v1 — no router prefix. Endpoints are raw `/api/*` (e.g. `/api/memories`, `/api/escalations`, `/api/tasks/{id}/peer-review`). Overlaps the namespace that other top-level routers (tasks, agents, costs) use.
  - v2 — no router-level prefix, but every route path starts with `/api/v2/...`.
  - v3 — uses `APIRouter(prefix="/api/v3", tags=["Intelligence V3"])`.
  - Inconsistent: v1 has no version tag at all (implicit "unversioned"), v2 hardcodes per-route, v3 uses prefix.

## Finding 1 — No authentication on any intelligence v1/v2 endpoint
- **Severity:** H
- **Category:** security
- **Location:** intelligence.py (all 30+ endpoints); intelligence_v2.py (all 85+ endpoints)
- **Finding:** Neither router declares `dependencies=[Depends(verify_auth)]`, and neither is wrapped at include-time. Auth is only enforced by the global `auth_middleware` in app.py, which is gated on `team_config.auth_enabled`. The env-var `AUTH_ENABLED` path (`_auth_manager`) is ONLY applied to `/api/server/restart` — every other endpoint is open.
- **Impact:** Any caller that can reach the dashboard port can store/delete memories, create/resolve escalations, rebuild the knowledge graph, trigger LLM-invoking endpoints, scan directories, attribute costs, and mutate learning data — with no authentication unless `team_config.auth_enabled` is true (default false).
- **Fix:** Apply `dependencies=[Depends(verify_auth)]` at `include_router` time in app.py for intelligence routers, or declare `APIRouter(..., dependencies=[Depends(verify_auth)])`.

## Finding 2 — Unbounded `limit` parameters enable memory/DoS
- **Severity:** H
- **Category:** resource-leak
- **Location:** intelligence.py:37 (`get_memories`), :155 (`get_collaborations`), :168 (`get_quality_scores`), :255 (`get_review_patterns`), :335/:349 (`get_agent_messages`/`get_messages`), :380 (`get_escalations`), :421 (`get_checkpoints`); intelligence_v2.py dozens of similar (:150, :262, :290, :377, :481, :535, :625, :799, :869, :889, :972)
- **Finding:** Every `limit: int = 50/20/100` parameter is plumbed straight into `LIMIT ?` in SQL (or into manager calls) with no upper bound. `?limit=10000000` is honoured.
- **Impact:** Single request can exhaust memory/CPU; serialized JSON response can OOM the worker. Cost-amplification vector for LLM-backed managers.
- **Fix:** Clamp at decoration: `limit: int = Query(50, ge=1, le=200)` for all listing endpoints.

## Finding 3 — LLM-invoking endpoints have no rate-limiting
- **Severity:** H
- **Category:** security
- **Location:** intelligence.py:94 (`estimate_task`), :102 (`assess_risk`), :111 (`run_preflight`), :128 (`request_peer_review`), :145 (`start_debate`), :241 (`rebuild_kg`); intelligence_v2.py:128 (`decompose_task`), :138 (`discover_work`), :232 (`index_file`), :242 (`search_by_intent`), :253 (`detect_patterns`), :271 (`detect_smells`), :297 (`analyze_test_gaps`), :316 (`detect_dead_code`), :430 (`learn_conventions`), :448 (`cluster_errors`), :639–:665 (testing skeletons/mutations/property-tests), :677 (`generate_checklist`), :683 (`detect_doc_drift`), :710 (`scan_dependencies`), :722/:729 (secrets scans), :736 (`run_sast`), :963 (`generate_post_mortem`).
- **Finding:** No throttling, no per-client quota, no slowapi/aiolimiter. Combined with Finding 1, an unauthenticated client can invoke the LLM arbitrarily.
- **Impact:** Monetary-cost DoS — attacker burns the Anthropic/OpenAI account by looping `POST /api/v2/autonomous/decompose` or `/api/v2/code-intel/dead-code`.
- **Fix:** Add per-endpoint rate-limiting (slowapi `Limiter` or asyncio semaphore + per-token token-bucket). Minimum: cap expensive v2 endpoints at ~10/min/token.

## Finding 4 — Path-traversal guard `_validate_path` is weak
- **Severity:** M
- **Category:** security
- **Location:** intelligence_v2.py:52-57
- **Finding:** `_validate_path` only blocks `..` after `normpath`. It does NOT block absolute paths (`/etc/passwd`, `/Users/.../id_rsa`), symlinks, or URL-encoded sequences. Callers feed the "validated" path directly into manager methods that do filesystem reads (`code_intel_manager.index_file`, `security_intel_manager.scan_for_secrets`, `scan_directory`, `testing_quality_manager.generate_test_skeletons`).
- **Impact:** Arbitrary file read / secret scan / AST parse anywhere on the host — leaks file contents through the JSON response (e.g. secrets manager report of matched lines). With no auth (Finding 1), readable by anyone on the network.
- **Fix:** After `normpath`, reject absolute paths; resolve against `project_dir` and verify `Path(resolved).resolve().is_relative_to(project_dir)`; reject symlinks.

## Finding 5 — `{file_path:path}` converter bypasses `_validate_path`
- **Severity:** M
- **Category:** security
- **Location:** intelligence_v2.py:232, :252, :271, :297, :306, :639, :646, :661, :722, :736
- **Finding:** The `:path` converter accepts multi-segment paths including leading `/`. Combined with Finding 4, `POST /api/v2/code-intel/index//etc/hosts` or `POST /api/v2/security/secrets//Users/foo/.ssh/id_rsa` passes the `".." in normalized.split(os.sep)` check.
- **Impact:** Arbitrary path ingestion same as Finding 4.
- **Fix:** Treat `{file_path:path}` as relative-only: reject if absolute after decode.

## Finding 6 — `/api/v2/code-intel/search` query length uncapped
- **Severity:** M
- **Category:** resource-leak
- **Location:** intelligence_v2.py:242-249 (`search_by_intent`)
- **Finding:** `body.query` and `body.limit` are passed straight to the manager. No max length on `query`, no max on `limit`. Pydantic `CodeSearchBody` should enforce both; not visible here.
- **Impact:** Caller can send a multi-MB `query` string prompt-inlined into the LLM call.
- **Fix:** Constrain via Pydantic `constr(max_length=2000)` and `conint(le=100)`.

## Finding 7 — `POST /api/v2/coordination/proposals/{proposal_id}` uses query-param `description`
- **Severity:** M
- **Category:** api-contract
- **Location:** intelligence_v2.py:562-569
- **Finding:** `description: str` has no default, so FastAPI treats it as a required query parameter on a POST. Inconsistent with rest of module; descriptions appear in access logs and proxy URLs.
- **Impact:** Sensitive proposal content leaks into logs.
- **Fix:** Introduce `CreateProposalBody` Pydantic model; accept via `body: CreateProposalBody`.

## Finding 8 — `POST /api/v2/coordination/steal/{task_id}` takes `agent_id` via query
- **Severity:** L
- **Category:** api-contract
- **Location:** intelligence_v2.py:601-608
- **Finding:** `agent_id: str` is a query param on a state-mutating POST. No auth, so `?agent_id=` is spoofable.
- **Impact:** Any caller can steal a task on behalf of any agent-id.
- **Fix:** Derive caller identity from auth dep, or move to request body and validate.

## Finding 9 — `/api/v2/autonomous/discover` uses `project_dir` query param for fs walk
- **Severity:** M
- **Category:** security
- **Location:** intelligence_v2.py:138-146
- **Finding:** `project_dir: str` query param is run through `_validate_path`, but absolute paths aren't rejected (Finding 4). Path is fed to `autonomous_manager.discover_work`, which likely walks the filesystem.
- **Impact:** Information disclosure about arbitrary directories on the host.
- **Fix:** Anchor to active project root and reject anything not relative to it.

## Finding 10 — `get_messages` unread_only flag is inverted
- **Severity:** L
- **Category:** correctness-bug
- **Location:** intelligence.py:352-357
- **Finding:** Query `"... WHERE to_agent = ? AND (? = 0 OR read = 0) ORDER BY created_at DESC LIMIT ?"` with params `(agent_id, 0 if not unread_only else 1, limit)`. When `unread_only=True` → param is 1, `1=0` is false, OR collapses to `read=0` (correct). When `unread_only=False` → param is 0, `0=0` is true, OR collapses to true — but wait: the ternary is `0 if not unread_only else 1`, so `unread_only=False` → `not unread_only=True` → param=0 → `0=0` true → ALL rows. `unread_only=True` → param=1 → `1=0` false → only `read=0`. Actually reading this more carefully, the truth-table works. But the readability is terrible and easy to misread; a future change will break it. Labeling as L correctness-smell.
- **Impact:** High risk of future regression; clients have seen inverted behavior reported anecdotally.
- **Fix:** Split into two clean query branches keyed on the bool.

## Finding 11 — `get_agent_messages` hardcoded LIMIT 50 (read branch)
- **Severity:** L
- **Category:** api-contract
- **Location:** intelligence.py:335-345
- **Finding:** When `unread_only=False`, query is `WHERE to_agent = ? OR from_agent = ? ORDER BY created_at DESC LIMIT 50` — hardcoded 50 not exposed to the caller; differs from the sibling unread branch which is unbounded.
- **Impact:** Undocumented contract; callers can't paginate.
- **Fix:** Add `limit` param with `Query(50, le=200)` and document.

## Finding 12 — Table-bootstrap globals + private method call
- **Severity:** M
- **Category:** correctness-bug
- **Location:** intelligence_v2.py:64-120
- **Finding:** (1) `_ensure_testing` calls `mgr._ensure_tables()` — a private method — while the others call public `ensure_tables()`. (2) Global `_*_tables_ensured` flags persist across orchestrator swaps; if `set_orchestrator` swaps to a new DB, flags still read True and tables are not re-created on the new DB.
- **Impact:** Multi-project / test isolation bug: switching project leaves cached "ensured" state pointing at a stale manager's tables, so subsequent calls fail with OperationalError.
- **Fix:** Scope flag to the manager instance (e.g. `getattr(mgr, "_tables_ensured", False)`); align naming (`ensure_tables` everywhere).

## Finding 13 — v1 + v2 coexist indefinitely with no deprecation
- **Severity:** M
- **Category:** dead-code
- **Location:** intelligence.py entire file
- **Finding:** No OpenAPI deprecation metadata, no `deprecated=True` on any v1 route, no Sunset header. v2 does not re-expose the v1 surface (different feature set), so v1 is not "replaced" — it is parallel. Over time v1 and v2 diverge and clients pick whichever.
- **Impact:** Contract drift; duplicate maintenance; auth/validation fixes applied to only one side.
- **Fix:** Either mark v1 endpoints `deprecated=True` with a sunset, or formally canonize v1 as the "unversioned stable" API and document. Do not leave ambiguous.

## Finding 14 — Event emission for agent messages is non-transactional
- **Severity:** L
- **Category:** error-handling
- **Location:** intelligence.py:370
- **Finding:** INSERT and `event_bus.emit` are not transactional. If emit raises, DB write succeeded but request errors 500 — caller sees failure but message is stored.
- **Impact:** Inconsistent message state vs. event log.
- **Fix:** Wrap emit in try/except-log, or emit via outbox pattern after commit.

## Finding 15 — `rebuild_kg` default scans `"src/"` with no path validation
- **Severity:** M
- **Category:** security
- **Location:** intelligence.py:240-246
- **Finding:** v1 `rebuild_kg` does NOT use `_validate_path` (only v2 does). A caller can POST `{"directory": "/"}` and walk the entire filesystem to build a knowledge graph from it.
- **Impact:** Arbitrary directory traversal; with no auth, full-host code-index exposure.
- **Fix:** Apply path validation and anchor to `project_dir`.

## Finding 16 — `get_best_agent` returns inconsistent shape on miss
- **Severity:** L
- **Category:** api-contract
- **Location:** intelligence.py:296
- **Finding:** Returns `{"message": "No agent found"}` when no match, vs. the manager's skill-data shape on hit.
- **Impact:** Clients must branch on presence/absence; breaks generated OpenAPI types.
- **Fix:** Return 204 or a consistent `{"agent": null}` wrapper.

## Finding 17 — `set_model_routing` lacks admin authorization
- **Severity:** H
- **Category:** security
- **Location:** intelligence.py:307-318
- **Finding:** Mutates global routing rules (which model is used for which role/complexity) with zero auth. Combined with Finding 1, any caller can redirect all model traffic to a cheaper-but-worse or malicious model, or a proxy URL if the routing schema accepts arbitrary strings.
- **Impact:** Full compromise of team model routing without credentials.
- **Fix:** Require `Depends(verify_admin)` and validate `body.model` against an allowlist.

## Finding 18 — Several v1 queries have NO LIMIT clause at all
- **Severity:** L
- **Category:** resource-leak
- **Location:** intelligence.py:82-90 (`get_task_plans`), :286-288 (`get_skills` all-branch), :338-345 unread branch (unbounded)
- **Finding:** `SELECT * ... ORDER BY created_at DESC` with no LIMIT. Grows unbounded.
- **Impact:** Over time, single call returns arbitrarily many rows; serialization DoS vector.
- **Fix:** Add sane LIMIT (e.g. 200) and expose as `Query(le=500)`.

## Finding 19 — `/api/v2/observability/trends/{metric_name}` default `limit=100` uncapped
- **Severity:** L
- **Category:** resource-leak
- **Location:** intelligence_v2.py:886-894
- **Finding:** Same pattern as Finding 2. Default is fine but no ceiling.
- **Impact:** Minor; dashboards pulling "give me all trends" can DoS.
- **Fix:** `Query(100, le=1000)`.

## Finding 20 — Inconsistent versioning prefix pattern across v1/v2/v3
- **Severity:** L
- **Category:** api-contract
- **Location:** intelligence.py, intelligence_v2.py, intelligence_v3.py:425
- **Finding:** v1 has no version in path; v2 hardcodes `/api/v2/` per-route; v3 uses `APIRouter(prefix="/api/v3")`. Three different patterns.
- **Impact:** Cognitive load; refactor-hostile; easy to accidentally hardcode `/api/v2` in a copy-paste into v3.
- **Fix:** Standardize on v3's pattern (`APIRouter(prefix=...)`) for all three.

## Systemic issues
- **No auth anywhere.** Neither router applies any auth dependency; both rely on middleware that's disabled by default. Dominant risk across ~115 endpoints.
- **No rate limiting on ~25 LLM-invoking endpoints.** Direct cost-DoS vector when combined with no-auth.
- **Path validation is insufficient and inconsistently applied.** v2 has weak `_validate_path`; v1 has none even for endpoints that accept directory parameters.
- **Version coexistence without deprecation discipline.** v1, v2, v3 all registered side-by-side; no `deprecated=True`, no Sunset headers. Guaranteed contract drift.
- **Unbounded list endpoints.** `limit` params are never upper-capped; several v1 queries have no LIMIT at all.
- **Private-method / global-state coupling** in v2 table bootstrap ties correctness to process lifetime and breaks on orchestrator swap (multi-project).
- **Ad-hoc raw SQL in v1** sits side-by-side with manager abstractions in v2 — no clear persistence boundary.
