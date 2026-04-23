# Audit Findings — Test Suite

**Files scanned:** ~90 test files (~850 KB)
**Files read in depth:** test_dashboard_api.py (full), test_security.py (full), test_security_v2.py (full), test_integration_intelligence_v3_endpoints.py (sampled + assert inventory), test_integration_intelligence_v2_endpoints.py (sampled), test_performance.py (full), test_agent_loop.py (sampled), test_task_board.py (signatures), test_edge_cases.py (full), test_webhook_manager.py (sampled), test_graceful_shutdown.py (full), test_chat_manager.py (full), test_intelligence_self_improvement.py (full), test_agent_base.py, test_agent_roles.py, test_logging_config.py, test_tools.py, conftest.py
**Reviewer:** audit-agent-16

## Test-coverage gap summary

- **Total source files:** 94 (incl. `__init__.py` stubs)
- **Total test files:** 90
- **Source files with NO same-named test file:**
  - `src/taskbrew/main.py` (42.7 KB orchestrator entrypoint — covered only indirectly via mocks in test_graceful_shutdown.py)
  - `src/taskbrew/dashboard/models.py` (11.7 KB)
  - `src/taskbrew/dashboard/routers/agents.py`
  - `src/taskbrew/dashboard/routers/collaboration.py`
  - `src/taskbrew/dashboard/routers/costs.py` (9.2 KB)
  - `src/taskbrew/dashboard/routers/interactions.py`
  - `src/taskbrew/dashboard/routers/mcp_tools.py`
  - `src/taskbrew/dashboard/routers/system.py` (28.4 KB)
  - `src/taskbrew/dashboard/routers/tasks.py` (31.3 KB)
  - `src/taskbrew/dashboard/routers/ws.py` (6.6 KB WS handler)
  - `src/taskbrew/dashboard/routers/search.py`
  - `src/taskbrew/dashboard/routers/intelligence.py`, `intelligence_v2.py`, `intelligence_v3.py` (only integration-tested)
  - `src/taskbrew/intelligence/clarification.py`
  - `src/taskbrew/intelligence/monitors.py`
  - `src/taskbrew/intelligence/impact.py`
  - `src/taskbrew/intelligence/tool_router.py`
  - `src/taskbrew/intelligence/context_providers.py` (11.1 KB, used heavily by fixtures but no unit tests)
  - `src/taskbrew/intelligence/_utils.py`
  - `src/taskbrew/orchestrator/interactions.py`
  - `src/taskbrew/orchestrator/system_prompt_builder.py`
- **Under-tested modules:** ~60 KB of dashboard router code (tasks.py + system.py) has no dedicated unit test; 42.7 KB `main.py` orchestrator path only exercised via MagicMocks.

## Finding 1 — v3 intelligence endpoint tests assert only HTTP 200, never payloads
- **Severity:** HIGH
- **Category:** test-quality
- **Location:** tests/test_integration_intelligence_v3_endpoints.py (lines 171-291, 302-385, 396-480, 490-588, 598-699, 709-809, 819-880, 890-956)
- **Finding:** Regex-confirmed: file has 63 `assert` statements total, of which only 2 check anything other than `resp.status_code == 200`. Dozens of tests (e.g. `test_store_prompt_version_returns_200`, `test_record_prompt_outcome_returns_200`, `test_update_trust_returns_200`, `test_index_intent_returns_200`, `test_add_debt_returns_200`) never read `resp.json()` or verify persistence.
- **Impact:** These tests would pass if handlers silently dropped payloads, wrote wrong rows, or returned `{}` — they pad the test count without providing behavioral guarantees on ~50 v3 endpoints.
- **Fix:** Add at least one downstream assertion per endpoint (read-back GET, or inspect primary-key field in response).

## Finding 2 — v2 intelligence endpoint tests have the same shallow-assert pattern
- **Severity:** MEDIUM
- **Category:** test-quality
- **Location:** tests/test_integration_intelligence_v2_endpoints.py (66 asserts, ~40 are bare status-code checks)
- **Finding:** ~60% of assertions are `assert resp.status_code == 200` with no follow-up.
- **Impact:** Allows logic bugs in handler side-effects to merge silently.
- **Fix:** Supplement each POST with a read-back GET.

## Finding 3 — v2 endpoint fixtures mutate router module-level state (blocks parallel tests)
- **Severity:** MEDIUM
- **Category:** test-pollution
- **Location:** tests/test_integration_intelligence_v2_endpoints.py:145-150; tests/test_integration_intelligence_v3_endpoints.py (client fixture); tests/test_security_v2.py:148; tests/test_edge_cases.py (client fixture); tests/test_performance.py API-perf fixture
- **Finding:** The `client` fixture resets `intelligence_v2._obs_tables_ensured`, `_planning_tables_ensured`, `_testing_tables_ensured`, `_security_tables_ensured` to False because production caches "tables ensured" as module-level booleans.
- **Impact:** New `_X_tables_ensured` flags silently cause stale-schema state; parallel execution unsafe.
- **Fix:** Move the table-ensure cache onto manager instances.

## Finding 4 — Fire-and-forget asserts gated on hardcoded `asyncio.sleep(0.1)`
- **Severity:** MEDIUM
- **Category:** flakiness
- **Location:** tests/test_webhook_manager.py (12 occurrences: lines 155, 193, 213, 243, 272, 302, 335, 414, 447, 470, 494, 515); tests/test_notification_service.py (7 occurrences); tests/test_hooks.py:101, 130, 187, 207
- **Finding:** After `wh_mgr.fire(...)` spawns `asyncio.create_task`, tests sleep exactly 100 ms before asserting mock call counts. Under CI load the send may not have run.
- **Impact:** Intermittent `assert_called_once()` failures with no code change.
- **Fix:** Have `fire()` expose the background task so tests `await` it.

## Finding 5 — Busy-wait polling with 0.01 s sleeps and no timeout guard
- **Severity:** MEDIUM
- **Category:** flakiness
- **Location:** tests/test_escalation_monitor.py:56-79, 83-110, 114-166
- **Finding:** `while call_count < 2: await asyncio.sleep(0.01)` with no outer `asyncio.wait_for` — a regression that stops the monitor hangs the test indefinitely.
- **Impact:** Genuine regressions show as CI hangs rather than failures.
- **Fix:** Wrap in `asyncio.wait_for(..., timeout=2.0)`.

## Finding 6 — test_auth navigates private closures to fetch AuthManager
- **Severity:** MEDIUM
- **Category:** test-quality
- **Location:** tests/test_security.py:168-208
- **Finding:** Walks `route.dependencies[*].dependency.__closure__` cell contents to locate the AuthManager. Any refactor breaks the test even when auth behavior is unchanged.
- **Impact:** Brittle; couples test to production internals.
- **Fix:** Expose AuthManager via `app.state.auth_manager`.

## Finding 7 — test_security_v2 XSS test only asserts round-trip, no sanitization check
- **Severity:** MEDIUM
- **Category:** security-test-quality
- **Location:** tests/test_security_v2.py:302-322
- **Finding:** The only XSS test stores `<script>alert("xss")</script>` and asserts it is returned verbatim, with a comment "rendering (escaping) is the frontend's job." No check that responses use `Content-Type: application/json`, no check that any HTML rendering path escapes the string.
- **Impact:** "XSS prevention" section does not prevent anything — merely documents the backend is permissive.
- **Fix:** Assert `Content-Type: application/json` on API responses carrying user strings, add render-path test against dashboard HTML.

## Finding 8 — v3 endpoint tests: 2 of 63 asserts validate business behavior
- **Severity:** HIGH
- **Category:** test-quality
- **Location:** tests/test_integration_intelligence_v3_endpoints.py
- **Finding:** Regex-verified `\bassert\b` count = 63, `assert\s+(?!resp\.status_code)` = 2. Claims full endpoint coverage but provides near-zero differential testing.
- **Impact:** Inflates the "1300+ tests" headline without raising the bug-catch floor.
- **Fix:** Pair each POST with a GET/list round-trip assertion.

## Finding 9 — Fixtures call `set_orchestrator(orch)` without a teardown reset
- **Severity:** MEDIUM
- **Category:** test-pollution
- **Location:** tests/test_integration_intelligence_v2_endpoints.py:143; tests/test_integration_intelligence_v3_endpoints.py; tests/test_security_v2.py:147; tests/test_edge_cases.py; tests/test_performance.py:505
- **Finding:** `set_orchestrator(orch)` writes to a module-level slot in `taskbrew.dashboard.routers._deps` but no finalizer calls `set_orchestrator(None)`. Previous test's orchestrator leaks.
- **Impact:** Tests like `test_quality_scores_returns_503_when_manager_none` pass only by fixture-ordering luck.
- **Fix:** Add `set_orchestrator(None)` in teardown after `yield c`.

## Finding 10 — test_graceful_shutdown uses bare MagicMock for every orchestrator field
- **Severity:** LOW
- **Category:** test-quality
- **Location:** tests/test_graceful_shutdown.py:16-48
- **Finding:** Every field is a bare `MagicMock()` with no `spec=`. Tests verify call order on mocks, not real interaction.
- **Impact:** Signature changes don't break the test; validates shutdown choreography, not real-component correctness.
- **Fix:** Use real in-memory DB + real TaskBoard/EventBus for happy-path.

## Finding 11 — Tests use `asyncio.sleep(60)` / `sleep(300)` in test bodies
- **Severity:** LOW
- **Category:** flakiness
- **Location:** tests/test_project_manager.py:460-462; tests/test_graceful_shutdown.py:100, 243
- **Finding:** "Slow" mock coroutines sleep 60/300 s and rely on shutdown cancellation to terminate.
- **Impact:** Regressions surface as CI timeouts not assertion failures.
- **Fix:** Short sleeps inside `asyncio.wait_for(test_body, timeout=2)`.

## Finding 12 — `test_concurrent_task_claims` is not actually concurrent
- **Severity:** LOW
- **Category:** test-quality
- **Location:** tests/test_performance.py:178-234
- **Finding:** Test name says "concurrent"; body is a sequential for-loop of awaits — no `asyncio.gather`.
- **Impact:** Gives appearance of concurrency testing; provides zero defense against double-claim races.
- **Fix:** Rename to `test_sequential_rapid_claims`, add a separate `asyncio.gather`-based test.

## Finding 13 — Conftest is nearly empty; ~100-line orchestrator fixture duplicated 5+ times
- **Severity:** LOW
- **Category:** test-quality
- **Location:** tests/conftest.py (215 B); duplicated in 5-6 files
- **Finding:** Only one fixture (`tmp_project`) exists globally. The full `_build_full_env` wiring 25 managers is copy-pasted across 5-6 files with arity drift already visible.
- **Impact:** New managers require N fixture updates; silent arity drift between copies.
- **Fix:** Move `_build_full_env` into conftest.py.

## Systemic issues observed across the test suite

- **Shallow status-code-only asserts dominate v2/v3 endpoint tests** — v3 has 2/63 meaningful asserts, v2 has ~26/66. Inflates test count without proportionate bug-catch power.
- **Zero skip/xfail debt.** No `@pytest.mark.skip` or `@pytest.mark.xfail` anywhere; one inline `pytest.skip` with reason. Healthy.
- **Zero cwd-pollution.** Every file write uses `tmp_path`.
- **Pervasive `asyncio.sleep(0.01–0.5)` flake surface.** ~40 sleep calls across webhook/notification/escalation/hooks/event_bus tests.
- **Heavy fixture duplication + module-level state resets.** 5-6 copies of `_build_full_env`. Blocks safe parallel execution.
- **Security tests reasonable in breadth but thin in depth.** XSS is a no-op; duplicate-vote/scope-creep tests are happy-path only.
- **Coverage gaps concentrate in dashboard routers.** tasks.py, system.py, intelligence_v3.py have no dedicated unit test.

**Counts:** HIGH 2, MEDIUM 7, LOW 4 (total 13)

**Verdict:** Breadth is impressive; unit-level tests for intelligence managers, task_board, webhooks, and agent_loop are genuinely good — but the flagship v2/v3 endpoint integration suites are mostly smoke tests padding the count, and fixture pollution would burn anyone trying pytest-xdist.
