# Audit Findings — Cross-cutting Synthesis

**Reviewer:** audit-agent-18
**Inputs:** audit/findings_01..17 + own spot checks
**Scope:** patterns only visible across modules; factual inventory; OSS-release hygiene; roll-up.

---

## 1. Version / API landscape (factual inventory)

- **API endpoint count (actual, across all `src/taskbrew/dashboard/routers/*.py` + `app.py`): 426**
  - `intelligence_v3.py`: 172 `@router.*` decorators
  - `intelligence_v2.py`: 89
  - `intelligence.py` (v1): 34
  - `tasks.py`: 31, `system.py`: 28, `usage.py`: 2, `collaboration.py`: 9, `costs.py`: 4, `exports.py`: 6, `git.py`: 7, `analytics.py`: 4, `pipeline_editor.py`: 8, `pipelines.py`: 4, `comparison.py`: 2, `mcp_tools.py`: 5, `interactions.py`: 6, `presets.py`: 2, `search.py`: 1, `agents.py`: 4, `ws.py`: 3, `app.py`: 5
  - **README claims "170+ endpoints" (`README.md:12`, `README.md:110`, `README.md:204`, `README.md:409`). Actual is ~2.5× that.**
  - **`app.py:76` OpenAPI `description="...89+ API endpoints..."` while `version="2.0.0"`. The docstring is 4× low.**

- **Intelligence module count (actual): 35** `.py` files under `src/taskbrew/intelligence/` (including `__init__.py` and `_utils.py`). Content-bearing managers = 33.
  - README claims "33 built-in intelligence modules" (`README.md:109`) — matches if you exclude `__init__`+`_utils`. Acceptable.
  - `__init__.py` is a 49-byte docstring-only file; **no re-exports** (so grep for `from taskbrew.intelligence import X` won't hit — every caller imports the submodule directly).

- **v1 / v2 / v3 endpoint path prefixes:**
  - v1 (`intelligence.py`): paths literally hardcoded `/api/...` (NOT `/api/v1/...`) — `router = APIRouter()` with **no** prefix. 34 routes share the root `/api/*` namespace with several other routers.
  - v2 (`intelligence_v2.py`): `router = APIRouter()` with **no** prefix; paths spell `/api/v2/...` inline. 89 routes.
  - v3 (`intelligence_v3.py`): `router = APIRouter(prefix="/api/v3", tags=["Intelligence V3"])`. 172 routes.
  - App additionally registers `APIRouter(prefix="/api/v1")` in `app.py:355-410` with 6 thin delegate endpoints (`/api/v1/{health,board,tasks/search,agents,usage,project}`). These **re-export** existing v0/unversioned endpoints; they are NOT related to intelligence.
  - **All three intelligence routers are included on the app at `app.py:295-297` with no deprecation headers, no `Sunset:` header, no `Deprecated: true` flag on the FastAPI decorator.**
  - **Path-level duplication between v1 and v2/v3: zero** — v1 lives at `/api/{feature}` while v2 lives at `/api/v2/{feature}`, so there is no *exact* path collision, but there is heavy *conceptual* duplication (e.g. v1 `/api/knowledge-graph/stats`, v1 `/api/model-routing`, v1 `/api/messages` all have richer v2/v3 equivalents). Slice 12a Finding 13 flagged this directly.

- **Version-string mismatches (5 independent sources):**
  - `pyproject.toml:7` → `version = "1.0.6"`
  - `src/taskbrew/__init__.py:3` → `__version__ = "0.1.0"`
  - `src/taskbrew/dashboard/app.py:77` → `version="2.0.0"` (FastAPI OpenAPI)
  - `src/taskbrew/dashboard/app.py:76` → `description="...89+ API endpoints..."` hand-maintained and stale
  - `CHANGELOG.md:8` → `## [1.0.0] - 2026-02-27` (never updated for 1.0.1..1.0.6)
  - `README.md:387` (architecture-diagram snippet) → `version="1.0.0"`
  - **5 different versions in 5 different places. No single source of truth.** (Already raised by slice 01 F#10 and slice 17 F#2 — but only cross-cuttingly visible that app.py, README, and CHANGELOG each add a *different* fourth/fifth value.)

- **Root-level markdown file count: 8 user-facing + 5 internal-review artifacts = 13 total.**
  Legit: `README.md`, `CHANGELOG.md`, `CONTRIBUTING.md`, `LICENSE`.
  **Should NOT ship on PyPI / public repo:** `ARCH-COMPLIANCE-CD153.md`, `CD-164-VERIFICATION-CD-145-READY-TO-MERGE.md`, `CODE_REVIEW_RV-179.md`, `CODE_REVIEW_RV-253.md`, `REVIEW-RV-261-REJECTION.md`, `TEST_VERIFICATION_TS-203.md`. These are internal Jira/review-ticket artifacts and leak internal ticket IDs.

- **`.env.example` (150 B total):** declares only `LOG_LEVEL`, `LOG_FORMAT`, `AUTH_ENABLED`, `CORS_ORIGINS`. Missing the many env vars actually read in the code base (e.g. `TASKBREW_CONFIG`, `TASKBREW_HOME`, provider-key vars). Confirmed by slice 17 F#14.

- **CI (`.github/workflows/ci.yml`):** matrix is `["3.10", "3.12"]`; `pyproject.toml` claims 3.10+ support but 3.11 is skipped. Actions pinned by **floating tag** (`actions/checkout@v4`, `actions/setup-python@v5`, `actions/cache@v4`) not SHA. `pytest tests/ -x -q --no-header` is invoked — suite does run in CI. README claim “1300+ passing” is unverified; actual test file count = 90 test_*.py under `tests/`; conftest is effectively empty (215 B). Fixtures are duplicated, per slice 16.

---

## 2. Systemic patterns across the audit (top 5)

### Pattern 1 — Intelligence endpoints uniformly lack authentication
- **Slices affected:** 10 (F#1,2,3,7,15,16,28,29,31,33), 11a (F#1,10,15,16), 11b (F#3,4,6,15,18,19), 12a (F#1,17), 12b (F#1 "CRITICAL — no auth on ~112 endpoints").
- **Evidence:** `dashboard/app.py:108-130` — `auth_middleware` is bypassed when `_tc` (team_config) is absent (10 F#3) AND when `AUTH_ENABLED!=true` (default false). Both `FastAPI.Depends(verify_auth)` decorators and `team_config.auth_enabled` are opt-in with the safe default being **off**. Slice 11b F#1 flagged that `verify_admin` is *declared* on `system.py` imports but never applied at the router level. Slice 10 F#2 — MCP endpoints accept any Bearer token.
- **Why cross-cutting:** This is a single root cause (default-off AUTH) producing 30+ per-slice findings. If you fix AUTH_ENABLED default to True + require `verify_admin` on every mutating endpoint, ~40 distinct findings collapse.

### Pattern 2 — Four independent broken path-traversal validators
- **Slices affected:** 05 F#1, 07a F#3, 07b F#1-3, 08a F#4-5, 08b F#3, 11a F#8, 11b F#2, 12a F#4-5, 12b F#2.
- **Evidence:** At least **four** local `_validate_path` / `validate_path` helpers, each independently broken (absolute paths pass through; symlink-traverse; `{file_path:path}` FastAPI converter bypasses the helper entirely). Slice 12a F#5 and 12b F#2 explicitly cite same class of bug in different routers. No shared helper.
- **Why cross-cutting:** Same bug reinvented 4+ times. Centralise `taskbrew.util.safe_path(root, candidate)` returning `Path` resolved under root; delete all copies.

### Pattern 3 — “Intelligence” is a pile of heuristics, not LLM reasoning
- **Slices affected:** 06a F#1-2,5,10, 06b F#4,9,12, 07a F#6,10-13, 07b F#8-16, 08a F#2,12, 08b F#1-2,4,8, 09 F#11-15.
- **Evidence:** 06a F#1 — *zero* LLM calls in planning modules; 08a F#2 — "SAST" is regex grep; 08b F#1 — "verification never actually verifies anything"; 08b F#2 — mutation analysis counts AST nodes, does not mutate; 07b F#16 — coverage-stats is not a coverage metric; 09 F#15 — prediction uses uncalibrated logistic with magic coefficients.
- **Why cross-cutting:** Product-marketing risk. README promises 33 intelligence modules enhancing agent behaviour; the *functional* truth is 33 heuristic scorers persisting meaningless numbers. Any Google-class reviewer who opens two of these files in a row will see it.

### Pattern 4 — Writes to SQLite without transactions or atomicity
- **Slices affected:** 03 F#1,3,5, 04 F#1,2,9, 06a F#9, 06b F#5,7, 07a F#12, 08a F#13, 08b F#12, 09 F#7,13,14,20, 10 F#17, 11b F#5,10.
- **Evidence:** 03 F#1 — `claim_task` not atomic because aiosqlite autocommit defeats `transaction()`; 04 F#1 — migrations 4 and 29 non-idempotent on crash-retry; 04 F#2 — no tx wraps DDL + version insert; 06b F#5 — `steal_task` non-atomic; 09 F#20 — `record_preference` classic lost-update race; 11b F#5 — YAML writes non-atomic and unlocked.
- **Why cross-cutting:** Data-corruption risk across the whole persistence layer. Each slice agent saw “this one function”; cross-cutting reviewer sees it’s **the prevailing idiom**.

### Pattern 5 — Subprocess calls with no timeout, no cwd validation, no stderr drain
- **Slices affected:** 01 F#7, 02 F#1-2, 05 F#3-7, 11a F#16-17, 11b F#3, 17 F#3.
- **Evidence:** 02 F#1 — Gemini subprocess never enforces wall-clock timeout; 02 F#2 — Gemini stderr never drained (pipe deadlock risk); 05 F#5 — no timeout on any subprocess `communicate()`, `GIT_TERMINAL_PROMPT` not set; 05 F#7 — `get_diff_summary` / `create_feature_branch` run without `cwd=`; 11a F#16 — `usage.py` spawns host CLIs via PATH resolution and returns OAuth profile; 11b F#3 — `/api/browse-directory` shells `osascript` unauthed.
- **Why cross-cutting:** Any hang in a child process becomes an unresponsive agent. Every subprocess site needs: timeout + stderr drain + fixed PATH + explicit cwd. Wrap once in `taskbrew.util.run_subprocess(...)`.

---

## Finding 1 — README and code disagree on endpoint count by 2.5×
- **Severity:** MEDIUM
- **Category:** docs-mismatch
- **Location:** cross-file — `README.md:12,110,204,409`, `src/taskbrew/dashboard/app.py:76-77`, counted decorators across `routers/*.py`.
- **Finding:** README repeatedly advertises "170+ API endpoints"; `app.py` OpenAPI description says "89+"; actual endpoint count across all routers is **426** (172 v3 + 89 v2 + 34 v1 + ~131 other). No single number is correct.
- **Impact:** Readers of the repo distrust other stated numbers; “audit surface” (the thing security teams size) is far bigger than advertised.
- **Fix:** Compute endpoint count at build time (ruff pre-commit or `scripts/count_routes.py`) and substitute into README + OpenAPI description.

## Finding 2 — Five disagreeing version strings, no single source of truth
- **Severity:** MEDIUM
- **Category:** oss-release-hygiene
- **Location:** cross-file — `pyproject.toml:7` (`1.0.6`), `src/taskbrew/__init__.py:3` (`0.1.0`), `src/taskbrew/dashboard/app.py:77` (`2.0.0`), `README.md:387` (`1.0.0`), `CHANGELOG.md:8` (`[1.0.0] - 2026-02-27`).
- **Finding:** Already noted in 01 F#10 and 17 F#2, but those only see two values. Five different values live in five files; `CHANGELOG.md` never advanced past 1.0.0 despite six pyproject bumps.
- **Impact:** Users cannot tell what they are running; `taskbrew --version` (if based on `__version__`) returns `0.1.0` for 1.0.6 installs.
- **Fix:** Have `__init__.py:__version__` read via `importlib.metadata.version("taskbrew")`; delete the hand-maintained string in `app.py`; add a `release.md` checklist step for CHANGELOG.

## Finding 3 — Internal review artifacts shipped in repo root
- **Severity:** LOW (but blocking for a Google-class OSS release)
- **Category:** oss-release-hygiene
- **Location:** repo root — `ARCH-COMPLIANCE-CD153.md`, `CD-164-VERIFICATION-CD-145-READY-TO-MERGE.md`, `CODE_REVIEW_RV-179.md`, `CODE_REVIEW_RV-253.md`, `REVIEW-RV-261-REJECTION.md`, `TEST_VERIFICATION_TS-203.md`.
- **Finding:** Six markdown files with internal ticket IDs (CD-153, CD-145, RV-179, RV-253, RV-261, TS-203) are checked into the repo root alongside README/LICENSE. They appear to be code-review rebuttals and compliance-check reports.
- **Impact:** Leaks internal project-management taxonomy; implies a gated review process that OSS contributors can’t participate in; clutters root for first-time visitors.
- **Fix:** Move to `docs/internal/` and add to `.gitignore`, or delete. MANIFEST/sdist exclusion should also be verified.

## Finding 4 — `intelligence/__init__.py` is a doc-string-only 49-byte file; every caller reaches into submodules
- **Severity:** LOW
- **Category:** api-contract
- **Location:** `src/taskbrew/intelligence/__init__.py` (49 B).
- **Finding:** The package provides no public facade. Every consumer (`src/taskbrew/main.py`, `src/taskbrew/tools/intelligence_tools.py`, `src/taskbrew/agents/agent_loop.py`, 30+ tests) imports specific submodules: `from taskbrew.intelligence.autonomous import ...`, etc. My grep (24 modules probed) confirms NONE go through `__init__`.
- **Impact:** Hard refactors; no deprecation story; a submodule rename is a breaking change for every test. Also means the "33 modules" number (README :109) is a PyPI/Google-review red flag — the surface isn’t a well-considered API, it’s a filesystem dump.
- **Fix:** Either (a) define a curated public facade in `__init__.py` and route callers through it, or (b) move cross-cutting managers under `taskbrew.intelligence.public` and explicitly mark the rest private.

## Finding 5 — v1 intelligence router uses bare `/api/*` paths (namespace collision with future features)
- **Severity:** MEDIUM
- **Category:** api-contract
- **Location:** `src/taskbrew/dashboard/routers/intelligence.py:28` (`router = APIRouter()` — no `prefix=`), 34 paths at `/api/memories`, `/api/skills`, `/api/messages`, etc.
- **Finding:** Unlike v2 (paths spelled `/api/v2/...` inline) and v3 (uses `prefix="/api/v3"`), v1 claims the root `/api/*` namespace. Any new router that introduces `/api/messages` under a different domain will collide. This is a latent bug of scale, not a current one.
- **Impact:** Refactor hazard; `/api/memories` could stop working when another router adds the same path; removing v1 is a public-API break for anyone who adopted it.
- **Fix:** Either prefix v1 with `/api/v1/...` and add redirects, OR emit `Deprecated: true` + `Sunset` headers on every v1 route and schedule removal.

## Finding 6 — Three intelligence API versions coexist with zero deprecation machinery
- **Severity:** MEDIUM
- **Category:** api-contract
- **Location:** `src/taskbrew/dashboard/app.py:295-297`.
- **Finding:** Already raised by 12a F#13 as “v1+v2 coexist” but now v3 exists too. All three are `app.include_router(...)` with no deprecation decorator, no `response.headers["Deprecation"]`, no CHANGELOG entry. 34 + 89 + 172 = **295 intelligence endpoints** simultaneously live.
- **Impact:** Security audit surface is 3× what it should be. Every bug class (path traversal, no auth, no rate limit) has to be fixed in three places.
- **Fix:** Pick one version as canonical; mark others deprecated with `Deprecation` + `Sunset` headers and a removal date; enforce removal in CI.

## Finding 7 — Test files exist for modules that are only imported from `__init__` aggregation — suggests dead “faux-API surface”
- **Severity:** LOW
- **Category:** dead-code
- **Location:** cross-file. Grep showed modules like `intelligence/social_intelligence.py` and `intelligence/self_improvement.py` are imported by `src/taskbrew/main.py` + a single test file + their v3 router — but nowhere else in the agent/orchestrator paths.
- **Finding:** These managers are registered on the “fake orch” scaffold in `app.py:180-216` (26 `fake.X = None`), but no non-router production code path *uses* them. They exist to be exposed by the v3 REST router and tested in isolation; no agent invokes them.
- **Impact:** 26 managers each ~20KB = ~500KB of code path that never runs in the happy path but still carries the full audit surface (auth, path traversal, SQL, DDL side-effects). Huge cost-to-value ratio.
- **Fix:** Decide per-manager: either wire into an agent/orchestrator decision, or remove the router surface.

## Finding 8 — `app.py:76-77` hand-maintained stale metadata
- **Severity:** LOW
- **Category:** docs-mismatch
- **Location:** `src/taskbrew/dashboard/app.py:76-77`.
- **Finding:** `description="Multi-agent AI team orchestrator with 89+ API endpoints ..."` and `version="2.0.0"`. Both values are hand-maintained and both are wrong (actual ~426 endpoints; pyproject 1.0.6). Slice 10 F#32 flagged the description string; the version duplicates slice 17 F#2 but triples the mismatch.
- **Impact:** OpenAPI served at `/openapi.json` lies to API clients.
- **Fix:** Derive both from `importlib.metadata` + a build-time endpoint counter.

## Finding 9 — CI ci.yml runs ruff + pytest + bandit but tolerates floating action pins and skips Py 3.11
- **Severity:** MEDIUM
- **Category:** oss-release-hygiene
- **Location:** `.github/workflows/ci.yml` (whole file).
- **Finding:** (a) `actions/checkout@v4`, `actions/setup-python@v5`, `actions/cache@v4` are floating tags — supply-chain risk (slice 17 F#4). (b) Matrix `["3.10", "3.12"]` skips 3.11 even though `pyproject.toml` declares `3.10+`. (c) `pytest tests/ -x -q` stops at first failure — flakes truncate all subsequent signal; CI will under-report failures.
- **Impact:** A compromised GitHub Action tag compromises all future CI runs; Py 3.11 users get no compatibility signal.
- **Fix:** Pin actions by SHA; add 3.11 to matrix; drop `-x` so all failures are surfaced.

## Finding 10 — `.env.example` is dangerously minimal (under-documented envvars is a foot-gun pattern)
- **Severity:** LOW
- **Category:** oss-release-hygiene
- **Location:** `.env.example` (150 B).
- **Finding:** Declares only `LOG_LEVEL`, `LOG_FORMAT`, `AUTH_ENABLED`, `CORS_ORIGINS`. But code reads many more env vars (provider API keys, `TASKBREW_HOME`, `DATABASE_URL`-style overrides, etc., per slice 17 F#14). Critically, `AUTH_ENABLED=false` is shipped as the example default — so running `cp .env.example .env` reproduces the insecure default.
- **Impact:** Newcomers copy the example, disable auth, expose the dashboard; the example endorses Pattern 1.
- **Fix:** Switch default to `AUTH_ENABLED=true` with a commented rationale; document every env var the code actually reads.

## Finding 11 — CHANGELOG.md never advanced past 1.0.0 despite 6+ releases
- **Severity:** LOW
- **Category:** docs-mismatch
- **Location:** `CHANGELOG.md` (30 lines total; one entry `[1.0.0] - 2026-02-27`).
- **Finding:** pyproject is 1.0.6 but CHANGELOG has a single 1.0.0 entry. Recent git log (e.g. `e4414c6`, `d478045`, `176b059`, `b983e11`, `8202e09`) shows real feature/fix work unreferenced in the changelog.
- **Impact:** Users upgrading 1.0.4 → 1.0.6 have no way to see what changed; breaks SemVer contract.
- **Fix:** Add Unreleased + per-release sections; enforce with a CI check that pyproject bump requires matching CHANGELOG diff.

---

## Consolidated severity roll-up across ALL 18 findings files

| Slice | C | H | M | L | Total |
|---|---|---|---|---|---|
| 01 core | 0 | 5 | 7 | 9 | 21 |
| 02 agents | 0 | 5 | 9 | 5 | 19 |
| 03 orchestrator | 1 | 8 | 7 | 4 | 20 |
| 04 migration | 2 | 4 | 4 | 4 | 14 |
| 05 tools | 2 | 6 | 4 | 3 | 15 |
| 06a intel planning | 0 | 3 | 9 | 6 | 18 |
| 06b intel autonomy | 0 | 4 | 7 | 9 | 20 |
| 07a intel code | 0 | 3 | 6 | 6 | 15 |
| 07b intel knowledge | 0 | 0 | 6 | 10 | 16 |
| 08a intel security | 0 | 5 | 7 | 2 | 14 + 1 Info |
| 08b intel QA | 1 | 3 | 6 | 7 | 17 |
| 09 intel collab | 1 | 4 | 9 | 6 | 20 |
| 10 dashboard small | 3 | 12 | 12 | 8 | 35 |
| 11a dashboard big1 | 1 | 10 | 11 | 3 | 25 |
| 11b dashboard big2 | 2 | 5 | 10 | 6 | 23 |
| 12a dash intel v1/v2 | 0 | 4 | 8 | 8 | 20 |
| 12b dash intel v3 | 1 | 2 | 5 | 11 | 19 |
| 13 frontend JS | 0 | 2 | 3 | 3 | 8 |
| 14 templates | 0 | 0 | 2 | 3 | 5 |
| 15 CSS | 0 | 0 | 0 | 1 | 1 |
| 16 tests | 0 | 2 | 7 | 4 | 13 |
| 17 infra config | 1 | 4 | 6 | 4 | 15 |
| **18 cross-cutting** | 0 | 0 | 5 | 6 | 11 |
| **TOTAL** | **15** | **91** | **150** | **128** | **384 + 1 Info** |

- Total CRITICAL: **15**
- Total HIGH: **91**
- Total MEDIUM: **150**
- Total LOW: **128**
- Informational: 1
- Grand total: **385**

---

## Top 10 most serious findings repo-wide (rank across all 18 files)

1. **[10] F#1/F#3 + [11b] F#1 + [12b] F#1 — Dashboard defaults to no authentication across ~400 endpoints.** Root cause is `AUTH_ENABLED=false` default + opt-in `team_config.auth_enabled` + `verify_admin` declared-but-not-applied. Any deployed instance on the open internet leaks tasks, artifacts, git repo contents, and accepts write/restart. This is the single largest attack surface.
2. **[05] F#1 — `agent_name` path-traversal in worktree paths (CRITICAL).** Agent-controllable string used to build a worktree path; combined with [05] F#2 (`prune_stale` rm -rf’s every dir in `worktree_base` not in the in-process dict), a hostile/crashy agent can corrupt the entire worktree base or escape to arbitrary dirs.
3. **[03] F#1 — `claim_task` not atomic (CRITICAL).** aiosqlite autocommit defeats `transaction()`, so two agents can win the same task under load. Breaks the orchestrator’s core invariant.
4. **[04] F#1 + F#2 — Migrations 4 and 29 non-idempotent, no tx around DDL + version-record insert (2× CRITICAL).** Crash during upgrade → DB permanently in-between versions with no rollback path; slice 17 F#1 compounds this with a broken Dockerfile that guarantees every rebuild re-runs migrations.
5. **[11b] F#1 + F#2 — Admin auth declared but never applied; path traversal via `role_name` in YAML write/delete (2× CRITICAL).** Unauth attacker can write arbitrary YAML paths via `/api/roles/...` and trigger server restart via admin endpoint (F#1 from slice 10 too).
6. **[09] F#1 — ToolRouter is advisory-only; no allowlist enforcement (CRITICAL).** The “restricted” tool policy is a suggestion to the LLM, not an enforced barrier — prompt-injection on agent output trivially bypasses.
7. **[17] F#1 — Dockerfile references files that do not exist; the container image will not build.** Any “ship via Docker” story is dead on arrival, and the Docker path silently paints over several other findings.
8. **[08b] F#1 — Verification module never verifies anything.** The feature that gates “is this task done” is a no-op — which means every gate that delegates to it passes by accident.
9. **[02] F#1 + F#2 — Gemini subprocess has no wall-clock timeout and never drains stderr.** Guaranteed agent hang under common failure modes; hangs a worker permanently, which interacts badly with the [02] F#7 “ERROR status cannot be persisted” bug.
10. **[10] F#4 + F#5 + F#6 — WebSocket endpoints have no auth, no Origin check (CSWSH), no message-size limit, and event bus broadcasts leak cross-tenant events to all clients.** Anyone who can open a WS sees every other tenant’s activity; cross-site WebSocket hijacking gives an attacker the same view via a victim’s browser.

Honourable mentions: [11a] F#5 (CSV formula injection in exports), [11a] F#2 (PATCH builds SQL with f-string from dict keys), [03] F#7 (SSRF filter ignores DNS — DNS rebinding trivial), [01] F#4 (plugin_system executes arbitrary Python), [10] F#2 (MCP accepts any Bearer token), [17] F#3 (`.githooks/pre-commit` is a no-op).
