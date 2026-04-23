# Audit Findings — Dashboard Intelligence v3

**Files reviewed:** dashboard/routers/intelligence_v3.py (2036 lines, 59KB)
**Reviewer:** audit-agent-12b

## Finding 1 — No auth/authz on any of ~112 endpoints
- **Severity:** CRITICAL
- **Category:** security
- **Location:** intelligence_v3.py:426 (router), all `@router.*` decorators
- **Finding:** `APIRouter(prefix="/api/v3")` has no `dependencies=[Depends(auth)]`; no per-endpoint auth on any of ~112 handlers.
- **Impact:** Unauthenticated tampering and info disclosure across self-improvement, social, compliance, knowledge (e.g. `POST /compliance/exemptions`, `DELETE /social/mental-model`, `POST /verification/flaky-tests/{test_name}/quarantine`).
- **Fix:** Shared auth dep at router level + per-role checks on mutating endpoints.

## Finding 2 — `_validate_path` bypassable; absolute paths pass through
- **Severity:** HIGH
- **Category:** security
- **Location:** intelligence_v3.py:429-434
- **Finding:** Checks only literal `..` component after normpath; doesn't reject absolute paths (`/etc/passwd`), symlinks, or forward-slash edge cases on Windows. Validated paths flow into manager reader code (check_conformance, detect_opportunities, auto_annotate, check_file, extract_from_comment).
- **Impact:** Arbitrary file read through any content-based endpoint.
- **Fix:** Reject absolutes, resolve against project-root, `is_relative_to(root)`, reject symlinks.

## Finding 3 — `changed_files` CSV splitter unbounded
- **Severity:** MEDIUM
- **Category:** edge-case / perf
- **Location:** intelligence_v3.py:1478-1482 (get_affected_tests)
- **Finding:** `changed_files.split(",")` no cap, no empty-string guard (trailing comma → `""` → normpath returns `.`).
- **Fix:** Cap length; drop empties; reject bare `.`.

## Finding 4 — Unbounded/unvalidated limit and numeric params pervasively
- **Severity:** MEDIUM
- **Category:** perf / resource-leak
- **Location:** intelligence_v3.py: 24+ sites listed; bodies at 320 (num_simulations=1000), 64, 256, 371.
- **Finding:** No `Query(..., ge=1, le=...)` on any pagination/size; `forecast` Monte-Carlo default 1000 sims per call, no ceiling.
- **Fix:** `Annotated[int, Query(ge=1, le=500)]` aliases.

## Finding 5 — `detect_flaky` silently discards `limit`
- **Severity:** LOW
- **Category:** api-contract
- **Location:** intelligence_v3.py:1521-1528
- **Finding:** Signature has `limit: int = 20` but only `min_runs` and `flaky_threshold` forwarded.
- **Fix:** Forward or drop.

## Finding 6 — `retract_fact` uses DELETE with a body
- **Severity:** LOW
- **Category:** api-contract
- **Location:** intelligence_v3.py:889-892
- **Finding:** `@router.delete(...)` with `RetractFactBody`. Many proxies strip DELETE bodies.
- **Fix:** Move to query params or `POST .../retract`.

## Finding 7 — Module-level init flags don't reset on orchestrator swap
- **Severity:** MEDIUM
- **Category:** correctness-bug / concurrency
- **Location:** intelligence_v3.py:441-553
- **Finding:** Eight module-level booleans gate `ensure_tables()`. `_deps.set_orchestrator` can be called multiple times; flags never reset, code assumes tables exist in new orchestrator's DB.
- **Impact:** First-hit "table does not exist" after project activation change; tests share cached state.
- **Fix:** Store init state on orchestrator (keyed by id(orch)); reset in set_orchestrator.

## Finding 8 — DCL boilerplate duplicated 8× with pre-lock read
- **Severity:** LOW
- **Category:** concurrency / maintainability
- **Location:** intelligence_v3.py:452-553
- **Fix:** One generic `_ensure(attr, flag_key)` helper.

## Finding 9 — Unsigned-unchecked ints across bodies
- **Severity:** LOW
- **Category:** input-validation
- **Location:** intelligence_v3.py:230-236, 258-260, 328-330, 376-378
- **Fix:** `Field(ge=0)` / `PositiveInt`.

## Finding 10 — Salience weights unvalidated
- **Severity:** LOW
- **Category:** input-validation
- **Location:** intelligence_v3.py:381-385, 1919-1926
- **Fix:** Validate 0≤w≤1, require sum ≈1 or renormalise.

## Finding 11 — No rate-limiting on LLM-reachable / expensive endpoints
- **Severity:** HIGH
- **Category:** security / perf (cost-DoS)
- **Location:** intelligence_v3.py: 14 sites (/refactoring/detect, /narratives, /velocity/forecast, /doc-gaps/scan, etc.)
- **Finding:** No slowapi/Depends limiter; combined with Finding 1, unauthenticated loops on LLM-backed endpoints are unmetered.
- **Fix:** Router-level rate limit + stricter caps on LLM-backed endpoints.

## Finding 12 — Inconsistent response shape; no response_model
- **Severity:** LOW
- **Category:** api-contract
- **Location:** throughout
- **Finding:** No `response_model`; action endpoints return None → null JSON; siblings return objects; unknown IDs don't 404.
- **Fix:** Add response_model; translate None → 404.

## Finding 13 — Mutating pydantic body attributes in place
- **Severity:** LOW
- **Category:** correctness-bug / maintainability
- **Location:** intelligence_v3.py: 17 sites
- **Finding:** `body.file_path = ...` overwrites fields. Future `model_config(frozen=True)` breaks all.
- **Fix:** Local var.

## Finding 14 — Empty-body pydantic models required on action endpoints
- **Severity:** LOW
- **Category:** dead-code / api-contract
- **Location:** intelligence_v3.py:101-102, 262-263, 805-810, 1420-1425
- **Finding:** `ResolveArgumentBody`/`CompleteTrackingBody` are `pass` stubs; FastAPI requires `{}` JSON.
- **Fix:** Drop body param.

## Finding 15 — IndexIntentBody.keywords union with no coercion
- **Severity:** LOW
- **Category:** api-contract
- **Location:** intelligence_v3.py:152-156, 1013-1022
- **Fix:** `@field_validator` to normalize.

## Finding 16 — quarantine_test uses greedy {test_name:path}
- **Severity:** MEDIUM
- **Category:** correctness-bug
- **Location:** intelligence_v3.py:1537-1540
- **Finding:** `:path` converter is greedy and unvalidated; slash-containing name propagates to downstream SQL/LIKE without escaping.
- **Fix:** Regex-validate test_name; require URL-encoding.

## Finding 17 — Unbounded `content: str` on code/text upload endpoints
- **Severity:** MEDIUM
- **Category:** resource-leak
- **Location:** intelligence_v3.py: 9 sites
- **Finding:** `content` no length cap; uvicorn accepts multi-GB payloads by default.
- **Fix:** `Field(max_length=1_000_000)` + request-size middleware.

## Finding 18 — Free-form dict bodies
- **Severity:** LOW
- **Category:** input-validation
- **Location:** intelligence_v3.py:79-81, 303-306, 308-309, 333-335, 747-752, 1611-1626, 1718-1723
- **Finding:** Untyped dict blobs; NaN floats / huge keys break downstream serialization.
- **Fix:** Typed sub-models.

## Finding 19 — set_salience_weights has no agent/tenant scope
- **Severity:** LOW
- **Category:** missed-impl
- **Location:** intelligence_v3.py:381-385, 1919-1926
- **Finding:** Global weights; any caller mutates behavior for every agent.
- **Fix:** Per-agent/tenant scope or admin role.

## Systemic issues
- **Endpoint count:** ~112 HTTP handlers in one 2036-line file — god-router.
- **Stub-endpoint count:** 0. Every endpoint delegates to manager methods; no hardcoded JSON stubs.
- **Zero auth + zero rate-limit + zero quota** across all ~112 endpoints — compounds into the most critical finding.
- **Unbounded numeric query params repeated 40+ times** — one `Annotated[int, Query(ge=1, le=500)]` alias module would fix most of them.
- **Massive boilerplate**: 90% of the file is `mgr = await _ensure_X(); return await mgr.y(**body)`. A declarative registration table would collapse it and make cross-cutting fixes one-line changes.
- **Path validation is a single weak helper** (Finding 2) guarding 17 call sites. Replace with project-root-anchored pydantic `FilePath` alias.

**Counts:** CRITICAL 1, HIGH 2, MEDIUM 5, LOW 11 (total 19)
