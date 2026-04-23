# TaskBrew — End-to-End Audit Report

> **Scope:** Every source file in the repository (Python, YAML configs, JavaScript, HTML templates, CSS, Dockerfile, CI, git hooks, shell scripts, tests, pipelines, plugins docs). Claims of "Google-class open-source-library audit for issues."
> **Date:** 2026-04-22 · **Target:** `taskbrew` v1.0.6 (PyPI)
> **Methodology:** 17 per-slice reviewers + 1 cross-cutting synthesis reviewer, each bound to a standardized finding schema with file:line evidence requirements. Individual slice reports live under [`audit/findings_*.md`](./audit/); this document is the consolidated roll-up.

---

## 1. Executive summary

TaskBrew is a ~1 MB Python project (~130 source files, ~90 tests, ~335 KB of vanilla JS, ~920 KB of Jinja templates) shipping on PyPI as a multi-agent AI orchestrator. Branding (README, OpenAPI description, changelog badges) promises a production-ready library with "1300+ passing tests", "170+ API endpoints", "33 intelligence modules", Docker deployment, auth, HMAC-signed webhooks, guardrails, auto-scaling, and a multi-tenant dashboard.

The reality the audit surfaces:

- **The default deployment path has no authentication.** `AUTH_ENABLED` defaults to `false`, middleware short-circuits when team_config is absent, and `verify_admin` is declared in code but never applied as a FastAPI dependency. **~20+ mutating endpoints** and **all** WebSocket surface are reachable unauthenticated.
- **The orchestrator's core invariant (one task → one agent) is not actually enforced.** `claim_task` is implemented atomically on paper but aiosqlite autocommit defeats the transaction, so two agents can win the same task under load.
- **The "intelligence" surface is 33 modules of heuristics.** Most "intelligence" modules contain zero LLM calls, return hardcoded confidence scores, implement mutation testing by counting AST nodes (no mutation), implement "verification" as a claim-recorder (trusts caller-supplied metrics), and implement "semantic search" as recency-sorted `OR`-of-`LIKE`.
- **Five independent, differently-broken path-traversal validators** exist across the codebase. Each one accepts absolute paths. At least five manager modules read arbitrary files from the host via agent-supplied `file_path`.
- **The DB migration manager is non-idempotent and non-transactional.** A crash mid-upgrade leaves the schema permanently wedged. The Dockerfile as shipped cannot build (`COPY` references missing files), so every Docker deploy would re-run migrations from a clean image anyway.
- **Three API versions (v1/v2/v3) coexist with no deprecation machinery.** 295 intelligence endpoints are live simultaneously; 172 of them are in a single 2036-line god-router.
- **Version strings disagree in five places.** `pyproject.toml=1.0.6`, `__init__.py=0.1.0`, `app.py=2.0.0`, `README.md=1.0.0`, `CHANGELOG.md=1.0.0`. CHANGELOG has not advanced since the initial release.
- **README claims 170+ endpoints; actual decorator count is 426.** The security-audit surface is 2.5× advertised.
- **Six internal review artifacts** (`CODE_REVIEW_RV-179.md`, `ARCH-COMPLIANCE-CD153.md`, etc.) ship in the repo root and leak internal ticket IDs.
- **The frontend has 323 `.innerHTML =` sinks** across 335 KB of vanilla JS. Safety is 100% discipline — one forgotten `escapeHtml()` = stored XSS. Inline `onclick="fn('…')"` + an `escapeHtml` that does not escape single-quotes is the most common real JS bug, present in 7+ files.

### Verdict

TaskBrew is an ambitious prototype marketed as a 1.0.6 production library. The orchestration kernel, migration manager, auth layer, and "intelligence" surface are each one disciplined rewrite away from being solid, but shipped together today they do not meet a Google-class open-source bar. **Do not deploy on a network you don't trust until the default-off auth, atomic task claiming, and migration safety issues are fixed.**

---

## 2. Consolidated severity roll-up

**385 total findings** across 22 slice reports (17 per-slice + 1 cross-cutting + 3 slices split further for scope):

| Severity | Count | Rough definition |
|---|---|---|
| 🔴 **CRITICAL** | **15** | RCE, auth bypass, trivially-reached SQL/data-loss, silent corruption on common path |
| 🟠 **HIGH** | **91** | Security bug with conditions, likely-reached crash, observable race, broken invariant |
| 🟡 **MEDIUM** | **150** | Edge-case crash, missing validation, resource leak under load, likely-dead code, incomplete impl |
| 🟢 **LOW** | **128** | Polish, docs-as-bug, minor perf |
| ℹ️ Informational | 1 | Observations with no direct bug |

### Per-slice roll-up

| # | Slice | C | H | M | L | Total | Full report |
|---|---|--:|--:|--:|--:|--:|---|
| 01 | Core bootstrap + auth | 0 | 5 | 7 | 9 | 21 | [findings_01_core.md](./audit/findings_01_core.md) |
| 02 | Agents subsystem | 0 | 5 | 9 | 5 | 19 | [findings_02_agents.md](./audit/findings_02_agents.md) |
| 03 | Orchestrator core | **1** | 8 | 7 | 4 | 20 | [findings_03_orchestrator.md](./audit/findings_03_orchestrator.md) |
| 04 | DB migrations | **2** | 4 | 4 | 4 | 14 | [findings_04_migration.md](./audit/findings_04_migration.md) |
| 05 | Tools / worktree / MCP | **2** | 6 | 4 | 3 | 15 | [findings_05_tools.md](./audit/findings_05_tools.md) |
| 06a | Intel — planning | 0 | 3 | 9 | 6 | 18 | [findings_06a_intel_planning.md](./audit/findings_06a_intel_planning.md) |
| 06b | Intel — autonomy/coord | 0 | 4 | 7 | 9 | 20 | [findings_06b_intel_autonomy.md](./audit/findings_06b_intel_autonomy.md) |
| 07a | Intel — code analysis | 0 | 3 | 6 | 6 | 15 | [findings_07a_intel_code.md](./audit/findings_07a_intel_code.md) |
| 07b | Intel — knowledge/learning | 0 | 0 | 6 | 10 | 16 | [findings_07b_intel_knowledge.md](./audit/findings_07b_intel_knowledge.md) |
| 08a | Intel — security/compliance | 0 | 5 | 7 | 2 | 14 | [findings_08a_intel_security.md](./audit/findings_08a_intel_security.md) |
| 08b | Intel — QA/verification | **1** | 3 | 6 | 7 | 17 | [findings_08b_intel_qa.md](./audit/findings_08b_intel_qa.md) |
| 09 | Intel — collab/social | **1** | 4 | 9 | 6 | 20 | [findings_09_intel_collab.md](./audit/findings_09_intel_collab.md) |
| 10 | Dashboard app + small routers | **3** | 12 | 12 | 8 | 35 | [findings_10_dashboard_small.md](./audit/findings_10_dashboard_small.md) |
| 11a | Dashboard big routers (tasks/usage/git/exports) | **1** | 10 | 11 | 3 | 25 | [findings_11a_dashboard_big1.md](./audit/findings_11a_dashboard_big1.md) |
| 11b | Dashboard big routers (system/pipelines) | **2** | 5 | 10 | 6 | 23 | [findings_11b_dashboard_big2.md](./audit/findings_11b_dashboard_big2.md) |
| 12a | Intelligence routers v1+v2 | 0 | 4 | 8 | 8 | 20 | [findings_12a_dashboard_intel_v1v2.md](./audit/findings_12a_dashboard_intel_v1v2.md) |
| 12b | Intelligence router v3 | **1** | 2 | 5 | 11 | 19 | [findings_12b_dashboard_intel_v3.md](./audit/findings_12b_dashboard_intel_v3.md) |
| 13 | Frontend JS | 0 | 2 | 3 | 3 | 8 | [findings_13_frontend_js.md](./audit/findings_13_frontend_js.md) |
| 14 | HTML templates | 0 | 0 | 2 | 3 | 5 | [findings_14_templates.md](./audit/findings_14_templates.md) |
| 15 | CSS | 0 | 0 | 0 | 1 | 1 | [findings_15_css.md](./audit/findings_15_css.md) |
| 16 | Test suite quality | 0 | 2 | 7 | 4 | 13 | [findings_16_tests.md](./audit/findings_16_tests.md) |
| 17 | Infra + config + CI | **1** | 4 | 6 | 4 | 15 | [findings_17_infra_config.md](./audit/findings_17_infra_config.md) |
| 18 | Cross-cutting synthesis | 0 | 0 | 5 | 6 | 11 | [findings_18_cross_cutting.md](./audit/findings_18_cross_cutting.md) |
| **Total** | | **15** | **91** | **150** | **128** | **385** | |

---

## 3. Factual inventory (what's actually here vs what's advertised)

| Claim | Where advertised | Reality | Delta |
|---|---|---|---|
| 170+ API endpoints | README.md:12, 110, 204, 409 | **426 decorator-level endpoints** | +150% |
| 89+ API endpoints | app.py:76 (OpenAPI description) | 426 | +379% |
| 33 intelligence modules | README.md:109 | 33 content-bearing `.py` (35 files incl. `__init__`+`_utils`) | ✓ |
| 1300+ tests passing | README.md badge | 90 `test_*.py` files; ~60 KB of the v3 endpoint suite is status-code-only assertions (2 of 63 asserts validate behavior) | under-reported quality |
| Version 1.0.6 | pyproject.toml | `__init__.py`=0.1.0; app.py=2.0.0; README snippet=1.0.0; CHANGELOG stuck at 1.0.0 | 5 different versions in 5 files |
| Docker deployment ready | README.md, docker-compose.yaml | Dockerfile `COPY pyproject.toml setup.cfg setup.py ./` references missing files; `pip install .` before `COPY . .` has no source tree | **container will not build** |
| Intelligence API v1/v2 | routers/intelligence.py, v2.py | Both registered live, no `Deprecation` header, no Sunset date | 295 intel endpoints live simultaneously |
| CI runs on 3.10–3.12 | pyproject classifiers | `.github/workflows/ci.yml` matrix = `[3.10, 3.12]`; **3.11 untested** | silent skip |

---

## 4. Top 15 most serious findings (repo-wide)

Ranked by blast radius / exploitability / likelihood of hitting a real user.

### 1. Dashboard default has no authentication across ~400 endpoints
**Severity: CRITICAL · Slices: 10, 11a, 11b, 12a, 12b**

Root cause: `AUTH_ENABLED=false` is the default env, and middleware also short-circuits when `team_config.auth_enabled=false` (also the default) or when team_config is None (also possible). Additionally, `verify_admin` is declared and imported across system.py, collaboration routers, and interactions router but never attached as a FastAPI dependency on any route (`system.py:37-43` passes it through `set_auth_deps` into oblivion). All destructive admin endpoints (`/api/server/restart`, project activate/deactivate, role create/update/delete, webhook register, budget create, pipeline mutate) and the entire MCP/WebSocket surface are reachable by anyone who can hit the port. `hmac.compare_digest` is not used for the token check even when auth IS enabled.

**Fix direction:** Fail closed. Default `AUTH_ENABLED=true`. Apply a shared `dependencies=[Depends(verify_admin)]` at router-include time for every mutating router. Use `hmac.compare_digest` for token compare.

---

### 2. `claim_task` is not atomic — two agents can win the same task
**Severity: CRITICAL · Slice: 03 (Finding 1) · `orchestrator/database.py:321`, `task_board.py:254-278`**

`Database._create_connection` sets `isolation_level=None` (autocommit), so the explicit `BEGIN`/`COMMIT` in `Database.transaction()` are single autocommit statements, not a transaction. The `claim_task` implementation does `SELECT ... LIMIT 1` then `UPDATE` — with autocommit they are two independent statements on a shared non-reentrant aiosqlite connection, so two concurrent pollers return the same row. The README's promise of `SELECT ... FOR UPDATE` semantics is not delivered.

**Fix direction:** Drop `isolation_level=None`, route hot paths through the pool, and rewrite claim as a single `UPDATE ... WHERE id = (SELECT ... LIMIT 1) RETURNING *`.

---

### 3. `agent_name` path-traversal + `prune_stale` mass-deletes parent dir
**Severity: CRITICAL · Slice: 05 (Findings 1, 2) · `tools/worktree_manager.py:77, 136-142`**

`WorktreeManager` accepts `agent_name` into `os.path.join(self.worktree_base, agent_name)` with no validation. An LLM-generated agent name like `"/etc"` or `"../.."` escapes the base. That path then flows into `shutil.rmtree(..., ignore_errors=True)` on cleanup. Separately, `prune_stale()` deletes every directory inside `worktree_base` that is not in the **in-memory** `_worktrees` dict — after a restart this dict is empty, so every sibling directory gets `rm -rf`'d. If the operator configured `worktree_base` at `~` or the repo root (plausible), prune would delete user data.

**Fix direction:** Validate `agent_name` against `[A-Za-z0-9_-]+`; enforce `os.path.commonpath` containment; source truth for prune from `git worktree list --porcelain`; refuse to rmtree symlinks.

---

### 4. DB migrations are non-idempotent and non-transactional
**Severity: CRITICAL · Slice: 04 (Findings 1, 2) · `orchestrator/migration.py:69-72, 1299-1300, 1344-1349`**

Migrations 4 (`enhance_agent_messages`) and 29 (`add_stage1_completion_gate_columns`) do unguarded `ALTER TABLE ADD COLUMN`. If the process crashes between the DDL and the subsequent `INSERT INTO schema_migrations`, the next boot raises `duplicate column name` on retry and halts all further migrations forever. Separately, `executescript` commits implicitly and the connection is already autocommit, so the DDL ↔ version-record pair is not bounded by a transaction in any migration. No cross-process advisory lock; two workers starting simultaneously both run migrations and one crashes on PK violation while the other half-applies.

**Fix direction:** Wrap each migration+record-insert in a single atomic block; guard each `ALTER TABLE ADD COLUMN` with an `IF NOT EXISTS` check or explicit pragma probe; add a DB-backed advisory lock acquired on boot.

---

### 5. Admin auth declared but never applied + YAML path traversal in role management
**Severity: CRITICAL · Slice: 11b (Findings 1, 2) · `dashboard/routers/system.py:37-43, 441, 571, 590`**

`_verify_admin` is stored in module state via `set_auth_deps()` but **no route uses `Depends(_verify_admin)`**. Every destructive endpoint in system.py is open. `create_role` validates `role_name` against `^[a-z][a-z0-9_]*$`, but `update_role_settings` (line 441) and `delete_role` (lines 571, 590) interpolate `role_name` directly into `Path(...)/f"{role_name}.yaml"` with no validation. A POST to `/api/roles/settings/%2E%2E%2Fteam` can overwrite `team.yaml`.

**Fix direction:** Apply `Depends(_verify_admin)` at router-include time in app.py; re-apply the regex and path-containment check on update/delete.

---

### 6. ToolRouter is advisory-only — role tool allowlists are unenforced
**Severity: CRITICAL · Slice: 09 (Finding 1) · `intelligence/tool_router.py:37-77`**

`ToolRouter.select_tools()` returns a *recommended* tool list merged from task-type + role profiles. No `authorize()`/`is_allowed()` entrypoint exists. `routing_mode: restricted` is enforced only in the dashboard router, not in the MCP `create_task` tool agents actually call (slice 02 Finding 3 confirms same bug from the agent side). The YAML `tools:` allowlist is decorative.

**Fix direction:** Add `is_tool_allowed(role, tool_name) -> bool` and check it on every MCP dispatch and subprocess spawn.

---

### 7. Verification module never actually verifies anything
**Severity: CRITICAL · Slice: 08b (Finding 1) · `intelligence/verification.py:15-670`**

`VerificationManager` only records claims. `evaluate_gate(gate_name, metrics)` blindly trusts `metrics`, which is caller-supplied. An agent reporting `{"tests_pass": true, "coverage": 100, "open_bugs": 0}` passes every quality gate. The only marketing-claimed "verifier" in the agent pipeline is a storage surface.

**Fix direction:** Rename to a ledger manager; add a `_run_pytest()` helper that invokes the real runner and writes objective numbers itself; treat caller-supplied `metrics` as *claims to be verified* not *facts*.

---

### 8. Dockerfile references missing files — container cannot build
**Severity: CRITICAL · Slice: 17 (Finding 1) · `Dockerfile:8`**

`COPY pyproject.toml setup.cfg setup.py ./` references `setup.cfg` and `setup.py` which do not exist in the repo. `pip install .` runs before `COPY . .` so hatchling has no source tree to build against. The published `docker compose up -d` story is dead on arrival from a clean checkout.

**Fix direction:** Remove the missing-file references; `COPY pyproject.toml README.md LICENSE ./` then `COPY src ./src` before `pip install .`.

---

### 9. Gemini subprocess has no timeout and never drains stderr
**Severity: HIGH · Slice: 02 (Findings 1, 2) · `agents/gemini_cli.py:97-109`**

No wall-clock timeout on `communicate()`. Stderr pipe not drained concurrently — deterministic pipe-buffer deadlock in verbose mode. A hung Gemini process stalls the agent indefinitely; interacts badly with `AgentStatus.ERROR` being rejected by `InstanceManager.VALID_STATUSES` (slice 02 Finding 7 — error state literally cannot be persisted).

**Fix direction:** Enforce `asyncio.wait_for` on communicate(); concurrent stdout/stderr drain tasks; add `ERROR` to valid statuses.

---

### 10. WebSocket: no auth, no Origin check, cross-tenant broadcast, no message-size limit
**Severity: HIGH · Slice: 10 (Findings 4, 5, 6, 7) · `dashboard/routers/ws.py:25-40, 81-167`**

All WS endpoints are skipped by the auth middleware (`app.py:113`) and have no Origin validation (CSWSH). The event bus is subscribed to `*` and blasts every event to every connected socket with no per-tenant filter — any connected client sees other tenants' task events and cost attributions. `receive_text()` has no max_size; `send_text` fan-out has no timeout. Chat session is keyed only by URL `agent_name` so a second WS to the same agent name evicts the first user's session.

**Fix direction:** Validate `Origin` allow-list; require token subprotocol or query-string bearer; per-tenant topic filtering before broadcast; `max_size` + rate-limit; scope sessions per-connection-id.

---

### 11. Webhook SSRF filter ignores DNS — IMDS access + DNS rebinding trivial
**Severity: HIGH · Slice: 03 (Finding 7) · `orchestrator/webhook_manager.py:55-77`**

`_validate_url()` only checks literal IPs and a tiny hostname blocklist. A domain resolving to `169.254.169.254` (AWS IMDS) or `127.0.0.1` passes validation; DNS rebinding between validate and fetch is wide open. Webhook URLs are also not re-validated at fire time (F#19) so stored attacker URLs survive validator tightening. HMAC has no signed timestamp and no algorithm version (F#8).

**Fix direction:** Resolve DNS at validate time AND pin the IP at request time; reject any private/loopback/link-local/reserved; add signed timestamp to HMAC input.

---

### 12. `plugin_system` executes arbitrary Python with no symlink/traversal checks
**Severity: HIGH · Slice: 01 (Finding, see plugin_system.py:148-163)**

`PluginRegistry.load_plugins` walks `plugins/` and `exec_module`'s every `*.py`. No symlink-refuse, no path containment. Any attacker who can drop a file (or symlink pointing elsewhere) into `plugins/` gets RCE on next boot. Additionally: when `AUTH_ENABLED=true`, the auto-generated bearer token is `print()`ed to stdout — but `taskbrew start` redirects stdout to `/dev/null`, so the token is lost and the daemon is effectively unauthenticatable (auth.py:63-70 + main.py:735).

**Fix direction:** Refuse symlinks; containment check; optionally require plugin manifests. Log the bearer token to the log file, not stdout.

---

### 13. PATH-hijackable `shutil.which("claude")` on unauthenticated GET
**Severity: HIGH · Slice: 11a (Finding 16) · `dashboard/routers/usage.py` (spawn of `claude`/`gemini` subprocesses via pexpect)**

Usage router spawns host CLIs (`claude`, `gemini`) via PATH resolution, unauthenticated, with no concurrency lock around ~50s pexpect sessions. An attacker who can place a file earlier in PATH on the host (or influence PATH via env injection through a separate endpoint) can execute arbitrary code when the dashboard fetches usage profile. No timeout; pexpect bound to a 50s magic number.

**Fix direction:** Absolute path to binaries; auth; per-host lock; explicit timeout.

---

### 14. CSV export formula injection across every export endpoint
**Severity: HIGH · Slice: 11a (Finding 5) · `dashboard/routers/exports.py`**

All CSV exports write user/LLM content verbatim without prefix-escaping cells starting with `=`, `+`, `-`, `@`, `\t`, `\r`. Opening the exported CSV in Excel/Sheets triggers formula execution — classic CSV-formula-injection (`=HYPERLINK("http://evil",A1)`, `=cmd|'/c calc'!A1` etc.).

**Fix direction:** Prefix any cell beginning with `= + - @` with a single-quote; use `csv.writer` with consistent quoting.

---

### 15. Frontend: 323 `.innerHTML=` sinks + inline `onclick` with broken JS-string escaping
**Severity: HIGH · Slice: 13 (Findings 1, 2) · `static/js/*.js` (5 files, 335 KB)**

Vanilla JS with no template layer: 323 `.innerHTML=` sites across 5 files. Safety is 100% discipline — one forgotten `escapeHtml()` on an LLM-authored field (chat message, agent output, task description, error) = stored XSS in the dashboard origin. The helper `v2RenderTable(intelligence.js:432-443)`, called ~90 times, concatenates cells without escaping. The inline-handler pattern `onclick="fn('"+escapeHtml(x)+"')"` uses an `escapeHtml` that does not escape `'`, so any value containing a single quote breaks out of the JS string argument (7+ occurrences across dashboard-core.js, dashboard-ui.js, features.js). Only intelligence.js:364 handles it correctly. No CSP in any template; CSP hardening is blocked until handlers are refactored.

**Fix direction:** Make `v2RenderTable` escape by default; replace every inline `onclick` with `data-*` + a delegated listener; add a strict CSP now that inline-script is avoidable.

---

### Honorable mentions (still CRITICAL / HIGH, omitted from top 15 for brevity)

- **MCP endpoints accept any Bearer prefix as valid auth** — suffix never verified (slice 10 F#2; `mcp_tools.py:26-29`)
- **tasks.py PATCH builds SQL via f-string from dict keys** — latent SQLi (slice 11a F#2)
- **Migration/bootstrap errors swallowed with bare `except Exception: pass`** — silent half-migrations (slice 03 F#6; `database.py:366-385`)
- **`create_task` writes a column absent from baseline schema** — hot path bricks if migrations fail (slice 03 F#3)
- **`generate_task_id` races under load → duplicate PKs** (slice 03 F#14)
- **`recover_orphaned_tasks` is HA-unsafe** — resets ALL in-progress tasks on any boot (slice 03 F#18)
- **`discover_work` does unbounded blocking FS scan on every call** (slice 06b F#1)
- **`acquire_lock` never honors `expires_at`** — zombie locks forever after any crash (slice 06b F#3)
- **`steal_task` is a non-atomic race that doesn't transition task state** (slice 06b F#5)
- **Four independent broken `validate_path` helpers** across intelligence/*, each accepting absolute paths (slices 07a F#3, 08a F#4-5, 08b F#3, 12a F#5, 12b F#2)
- **Collaboration endpoints accept caller-supplied `author`/`mentioned_user`** — audit trail spoofing (slice 10 F#15)
- **Interaction endpoints (approve/reject/respond/skip) all unauthenticated** (slices 03 F#12, 10 F#33)
- **`activate_project` swaps the global orchestrator and spawns agents, unauthenticated** (slice 11b F#4)
- **`.githooks/pre-commit` is a silent no-op** — reads `$1` expecting commit-message path but pre-commit hooks get no args (slice 17 F#3)

---

## 5. Systemic patterns (what recurs across the audit)

Five patterns appear across multiple slices. Each is a single root-cause refactor that would collapse dozens of individual findings.

### Pattern A — Default-off authentication everywhere
`AUTH_ENABLED=false` default + middleware bypass when `team_config` is absent + `verify_admin` declared-but-not-applied + `/static/*` and `/ws*` middleware skip-lists + MCP endpoints accept any Bearer prefix. **One fix** (fail-closed default + router-level `dependencies=[Depends(verify_admin)]` at `include_router` time + `hmac.compare_digest`) collapses 40+ findings.

### Pattern B — Four independent broken `validate_path` helpers
At least four local `_validate_path` / `validate_path` helpers, each independently broken: absolute paths pass through; symlinks not rejected; FastAPI's `{file_path:path}` converter bypasses the helper entirely. Affects intelligence/_utils.py, intelligence_v3.py, intelligence_v2.py, verification.py, code_intel.py, security_intel.py, testing_quality.py, artifact_store.py, worktree_manager.py. **One fix** (single `taskbrew.util.safe_path(root, candidate)` returning `Path` resolved under root) collapses 10+ findings.

### Pattern C — "Intelligence" is a pile of heuristics, not LLM reasoning
- `planning.py` / `advanced_planning.py`: zero LLM calls; alternatives and rollback are hardcoded templates; confidence is magic numbers (slice 06a F#1-2)
- `security_intel.py`: claims AST-based SAST, is 4 single-line regexes (slice 08a F#2)
- `verification.py`: never verifies anything (slice 08b F#1)
- `testing_quality.py`: mutation analysis counts AST nodes, does not mutate (slice 08b F#2)
- `task_intelligence.py` predict_outcome: uncalibrated logistic with magic coefficients, no feedback loop closes despite schema for it (slice 09 F#15)
- `quality.py`: "confidence score" is substring search for "verified"/"tested" — trivially game-able by LLMs that echo the magic words (slice 08b F#8)
- `code_intel.py` / `code_reasoning.py`: duplicate overlapping feature surfaces; "semantic search" is recency-sorted OR-of-LIKE with no relevance scoring (slice 07a F#5, F#15)

**This is a product-marketing risk as much as a bug.** README promises 33 intelligence modules enhancing agent behavior. The functional truth is a persistence layer for heuristic scores with no calibration loop. Any reviewer who opens two "intelligence" files in a row sees it.

### Pattern D — Writes to SQLite without transactions or atomicity
`claim_task` (03 F#1), migrations (04 F#1-2), `steal_task` (06b F#5), `cast_vote` (06b F#7), `record_preference` (09 F#20), `score_file` upsert (08b F#12), YAML writes system-wide (11b F#5), `plan_with_resources` (06a F#4), `build_schedule` (06a F#9), plus all "ensure tables" DDL paths. The prevailing idiom in the codebase is non-atomic SELECT-then-UPDATE or DELETE-then-INSERT, frequently without even a DB lock.

**One fix** would be a codebase-wide convention: every mutation goes through a single helper that takes a real transaction from a non-autocommit connection, and every upsert uses `INSERT ... ON CONFLICT DO UPDATE`.

### Pattern E — Subprocess calls with no timeout, no cwd, no stderr drain
Gemini CLI (02 F#1-2), every git/worktree subprocess (05 F#5-7), every `claude`/`gemini` spawn in usage.py (11a F#16), `/api/browse-directory` spawning `osascript` (11b F#3), plugin loader execing Python (01). Every one needs: wall-clock timeout, concurrent stderr drain, explicit cwd, fixed PATH, and `GIT_TERMINAL_PROMPT=0` for git.

**One fix:** a shared `taskbrew.util.run_subprocess(...)` wrapper; ban direct `subprocess.run`/`asyncio.create_subprocess_exec` via a ruff rule.

---

## 6. Open-source release hygiene

For a Google-class OSS audit, these items are blockers or near-blockers regardless of the severity-rated findings:

| Item | Status | Action |
|---|---|---|
| Dockerfile builds from clean checkout | ❌ No (refs missing files) | Fix COPY step |
| pyproject version == `__init__.py:__version__` | ❌ 5 different values | `__version__ = importlib.metadata.version("taskbrew")` |
| CHANGELOG reflects releases | ❌ Stuck at 1.0.0 despite 6 pyproject bumps | Per-release sections; CI check |
| Root contains only user-facing docs | ❌ 6 internal review artifacts leak ticket IDs | Move to `docs/internal/` or delete |
| `.env.example` safe defaults | ❌ Ships `AUTH_ENABLED=false` | Flip default; document all env vars actually read |
| CI pins GitHub Actions by SHA | ❌ Floating `@v4`/`@v5` tags | Pin by SHA |
| CI Python matrix matches `pyproject` classifiers | ❌ 3.11 skipped | Add 3.11 |
| CI surfaces all failures | ❌ `pytest -x` stops at first | Drop `-x` |
| v1/v2/v3 deprecation discipline | ❌ No `Deprecation`/`Sunset` headers | Mark v1/v2 deprecated; schedule removal |
| Role presets ship safe defaults | ❌ Bash-capable presets default to `approval_mode: auto` with no Bash-arg allow-list (slice 17) | Require explicit opt-in; allow-list Bash |
| Pipelines reference existing roles | ❌ `researcher`/`tester`/`reviewer` in pipelines exist nowhere in roles (slice 17) | Align YAML |

---

## 7. Per-slice highlights

For each slice: finding counts, 2–3 highlights, link to the full report. Full details live in the per-slice files.

### 01 · Core bootstrap + auth — [full report](./audit/findings_01_core.md)
`main.py`, `auth.py`, `config_loader.py`, `plugin_system.py`, `project_manager.py`. **21 findings** (0C / 5H / 7M / 9L).
- Plugin loader `exec_module`'s every `.py` in `plugins/` — RCE vector on any write to that dir.
- Auto-generated bearer token is `print()`ed but stdout is redirected to `/dev/null` by `taskbrew start` — daemon effectively unauthenticatable.
- Rate limiter keys on raw `request.client.host` with no `X-Forwarded-For` handling — behind any proxy, one attacker locks out every user.

### 02 · Agents subsystem — [full report](./audit/findings_02_agents.md)
`agent_loop.py`, `auto_scaler.py`, `gemini_cli.py`, `instance_manager.py`, `provider.py`, `roles.py`. **19 findings** (0C / 5H / 9M / 5L).
- `cli_path` used as `argv[0]` with no validation — any config-injection sink becomes RCE.
- `build_context` concatenates untrusted task strings directly into system prompts — cross-agent prompt injection.
- `routing_mode: restricted` enforced only by the dashboard router, bypassed by MCP `create_task`.
- `AgentStatus.ERROR` can't be persisted (not in `VALID_STATUSES`).

### 03 · Orchestrator core — [full report](./audit/findings_03_orchestrator.md)
`task_board.py`, `database.py`, `event_bus.py`, `webhook_manager.py`, `cost_manager.py`, `artifact_store.py`, `notification_service.py`, `interactions.py`. **20 findings** (1C / 8H / 7M / 4L).
- `claim_task` not atomic (autocommit defeats `transaction()`). **[Top-15 #2]**
- SSRF webhook filter ignores DNS; DNS rebinding trivial. **[Top-15 #11]**
- All query helpers share ONE aiosqlite connection — cursor cross-talk under load.
- `/api/interactions/*` approve/reject/respond/skip have no auth.
- `generate_task_id` races under load → duplicate PKs.
- `recover_orphaned_tasks` at boot resets ALL in-progress tasks unconditionally (HA-unsafe).

### 04 · DB migrations — [full report](./audit/findings_04_migration.md)
`orchestrator/migration.py` (53 KB). **14 findings** (2C / 4H / 4M / 4L).
- Migrations 4 and 29 use unguarded `ALTER TABLE ADD COLUMN` — crash leaves DB permanently wedged. **[Top-15 #4]**
- DDL + version-record insert not atomic (autocommit defeats the pair).
- No cross-process migration lock; two workers on boot both race.
- Two parallel migration mechanisms (MigrationManager + ad-hoc try/except ALTERs in `database.py:360-387`) coexist.

### 05 · Tools / worktree / MCP — [full report](./audit/findings_05_tools.md)
`worktree_manager.py`, `git_tools.py`, `task_tools.py`, `intelligence_tools.py`. **15 findings** (2C / 6H / 4M / 3L).
- `agent_name` path-traversal + `prune_stale` mass-delete. **[Top-15 #3]**
- Branch-name flag-injection (`git worktree add -b --upload-pack=...`).
- No timeouts on any subprocess; `GIT_TERMINAL_PROMPT` not set; git run without `cwd=` touches main checkout.
- MCP task_tools forwards any LLM-supplied `assigned_by` — role impersonation.

### 06a · Intelligence planning — [full report](./audit/findings_06a_intel_planning.md)
`planning.py`, `advanced_planning.py`. **18 findings** (0C / 3H / 9M / 6L).
- Zero LLM calls; alternatives and rollback are hardcoded templates. Confidence scores are magic numbers.
- Topo-sort silently emits invalid schedule on cycle.
- `plan_with_resources` computes assignments but never persists them.

### 06b · Intelligence autonomy/coordination — [full report](./audit/findings_06b_intel_autonomy.md)
`autonomous.py`, `coordination.py`, `execution.py`, `checkpoints.py`, `clarification.py`, `escalation.py`, `impact.py`, `preflight.py`, `monitors.py`. **20 findings** (0C / 4H / 7M / 9L).
- `discover_work` does unbounded recursive blocking FS scan in async — event-loop stall.
- `acquire_lock` never honors `expires_at` — any crash leaves a permanent lock.
- `steal_task` non-atomic race; never transitions task state.
- `PreflightChecker` exists but agent loop never calls it — only one dashboard route does.
- `find_similar_fix` LIKE pattern injection-unsafe (`%` / `_` not escaped).

### 07a · Intelligence code analysis — [full report](./audit/findings_07a_intel_code.md)
`code_intel.py`, `code_reasoning.py`. **15 findings** (0C / 3H / 6M / 6L).
- Unbounded `read_text` on every analysis method — OOM on large files.
- `validate_path` accepts absolute paths — arbitrary-file read via LLM-supplied `file_path`.
- `rglob` walks without gitignore / symlink / containment checks.
- `detect_dead_code` is fundamentally unsound (bare-name matching, dynamic dispatch invisible).
- Duplicate overlapping feature surface between the two modules.

### 07b · Intelligence knowledge/memory/learning — [full report](./audit/findings_07b_intel_knowledge.md)
`knowledge_graph.py`, `knowledge_management.py`, `memory.py`, `learning.py`, `self_improvement.py`. **16 findings** (0C / 0H / 6M / 10L).
- **Red-flag false alarm:** No `pickle`/`eval`/`exec`/`compile` anywhere. `self_improvement.py` does NOT modify its own code despite the name.
- Unescaped LIKE wildcards at 6 query sites.
- `recall` ignores `project_id` — cross-project memory leakage.
- `decay_scores` compares ISO datetime to SQLite `date()` (format mismatch).
- 7 tables have no retention policy.

### 08a · Intelligence security + compliance — [full report](./audit/findings_08a_intel_security.md)
`security_intel.py`, `compliance.py`. **14 findings** (0C / 5H / 7M / 2L).
- Docstring claims "AST-based SAST" but implementation is 4 single-line regexes.
- `scan_dependencies` flags every listed package regardless of installed version (false positives).
- `_utils.validate_path` only blocks `..`; absolute paths and symlinks escape.
- Compliance rules are user regexes with no timeout — ReDoS on event loop.
- Exemptions are unsigned free-text, no expiry, no auth — compliance is a logging layer, not an enforcement engine.

### 08b · Intelligence QA/verification/observability — [full report](./audit/findings_08b_intel_qa.md)
`verification.py`, `testing_quality.py`, `observability.py`, `process_intelligence.py`, `quality.py`, `review_learning.py`. **17 findings** (1C / 3H / 6M / 7L).
- Verification never verifies anything — trusts caller-supplied metrics. **[Top-15 #7]**
- Mutation analysis counts AST nodes, does not mutate.
- File-path inputs unconfined → path traversal / arbitrary read.
- Welford online variance implemented incorrectly (can go negative).
- "Performance regression" detects variance, not slowdown.

### 09 · Intelligence collab/social — [full report](./audit/findings_09_intel_collab.md)
`collaboration.py`, `context_providers.py`, `messaging.py`, `social_intelligence.py`, `specialization.py`, `task_intelligence.py`, `tool_router.py`. **20 findings** (1C / 4H / 9M / 6L).
- ToolRouter is advisory-only; role tool allowlists unenforced. **[Top-15 #6]**
- `DocumentationProvider` injects README content verbatim into LLM prompts — README-to-prompt injection.
- `predict_outcome` uses uncalibrated logistic with magic coefficients; no feedback loop closes despite schema for it.
- `detect_prerequisites`, `find_parallel_tasks`, context cache all INSERT unconditionally — unbounded growth on repeated calls.

### 10 · Dashboard app + small routers — [full report](./audit/findings_10_dashboard_small.md)
`app.py`, `chat_manager.py`, `models.py`, 12 small routers. **35 findings** (3C / 12H / 12M / 8L) — the largest slice.
- Admin restart unauthenticated by default.
- MCP endpoints accept any Bearer prefix as valid auth.
- REST auth bypassed when `team_config` absent.
- WebSockets: no auth, no Origin check, cross-tenant broadcast. **[Top-15 #10]**
- Pydantic models have zero size/regex/URL validators — SSRF via webhook URL, ReDoS on prompts.
- **~20 unauthed-but-should-be-authed mutating endpoints** in this slice alone.

### 11a · Dashboard big routers 1 — [full report](./audit/findings_11a_dashboard_big1.md)
`tasks.py` (31 KB), `usage.py` (20 KB), `git.py`, `exports.py`. **25 findings** (1C / 10H / 11M / 3L).
- Zero auth/authz on every endpoint in all four routers.
- CSV formula-injection in all CSV exports. **[Top-15 #14]**
- PATH-hijackable `shutil.which("claude")` on unauthenticated GET. **[Top-15 #13]**
- Arbitrary state transitions via PATCH /complete.
- f-string SQL assembly in PATCH.
- Path-traversal in `get_artifact_content` structured branch.

### 11b · Dashboard big routers 2 — [full report](./audit/findings_11b_dashboard_big2.md)
`system.py` (28 KB), `pipeline_editor.py`, `pipelines.py`. **23 findings** (2C / 5H / 10M / 6L).
- Admin auth dep declared but never applied. **[Top-15 #5]**
- Path traversal via `role_name` in YAML write/delete.
- `activate_project` swaps global orch and spawns agents unauthenticated.
- `/api/browse-directory` shells osascript unauthed.
- YAML writes non-atomic and unlocked everywhere.

### 12a · Intelligence routers v1 + v2 — [full report](./audit/findings_12a_dashboard_intel_v1v2.md)
`intelligence.py` (v1), `intelligence_v2.py` (v2). **20 findings** (0C / 4H / 8M / 8L).
- Both v1 and v2 still registered live; no `Deprecation` header, no `Sunset` date.
- Zero auth on any of the ~123 endpoints.
- No rate-limiting on ~25 LLM-invoking endpoints — cost-DoS.
- `_validate_path` in v2 blocks only `..`; absolute paths and `{file_path:path}` bypass it.
- `rebuild_kg` has no path validation at all.

### 12b · Intelligence router v3 — [full report](./audit/findings_12b_dashboard_intel_v3.md)
`intelligence_v3.py` (59 KB, 172 routes). **19 findings** (1C / 2H / 5M / 11L).
- No auth/authz on any of ~112 endpoints.
- `_validate_path` bypassable; absolute paths pass through.
- 40+ unbounded/unvalidated `limit` params.
- God-router anti-pattern — 2036 lines, 8 subsystems, single file.

### 13 · Frontend JS — [full report](./audit/findings_13_frontend_js.md)
5 JS files, 335 KB. **8 findings** (0C / 2H / 3M / 3L).
- 323 `.innerHTML=` sinks; safety is 100% discipline.
- `v2RenderTable` is unsafe-by-default and used ~90 times. **[Top-15 #15]**
- Inline `onclick="fn('…')"` with an `escapeHtml` that does not escape `'`.
- DOMPurify is the only XSS defense for LLM content, with no locked config.
- Zero `eval`, zero `document.write`, zero string-form timers, zero hardcoded secrets (positive).

### 14 · HTML templates — [full report](./audit/findings_14_templates.md)
`index.html` (621 KB), `settings.html` (191 KB), `metrics.html` (109 KB), `costs.html`. **5 findings** (0C / 0H / 2M / 3L).
- **`|safe` total: 0.** Zero `{% autoescape false %}`. No `{{}}` Jinja at all — templates are static shells, all dynamic rendering is client-side.
- No CSP meta tag in any template (hardens poorly with 361 `innerHTML` sites in inline JS).
- All CDN `<script>` tags carry SRI (positive).
- 920 KB of inlined JS/CSS in 3 templates (no caching).

### 15 · CSS — [full report](./audit/findings_15_css.md)
`main.css` (107 KB), `metrics.css`. **1 finding** (all LOW).
- Zero external `url()` / `@import` (positive — no supply-chain risk).
- Z-index uses ad-hoc values 0–10000 with no tokens.

### 16 · Test suite — [full report](./audit/findings_16_tests.md)
90 test files, ~850 KB. **13 findings** (0C / 2H / 7M / 4L).
- **v3 endpoint tests: 2 of 63 asserts validate business behavior.** The rest are `assert resp.status_code == 200` only.
- v2 endpoint tests: 60% are bare status-code checks.
- `asyncio.sleep(0.1)` flake surface — ~40 sites across webhook/notification/escalation/hooks tests.
- Heavy fixture duplication (5–6 copies of `_build_full_env`); module-level `_X_tables_ensured` flags reset in fixtures blocks parallel execution.
- Security tests thin in depth: XSS test just round-trips `<script>` and declares "frontend's job."
- **Verdict:** breadth impressive, flagship v2/v3 integration suites are mostly smoke tests padding the headline test count.
- Source files with no dedicated test file: `main.py`, `tasks.py`, `system.py`, `ws.py`, `intelligence.py`/v2/v3, `clarification.py`, `monitors.py`, `impact.py`, `tool_router.py`, `context_providers.py`, `orchestrator/interactions.py`, `system_prompt_builder.py`.

### 17 · Infra + config + CI — [full report](./audit/findings_17_infra_config.md)
Dockerfile, docker-compose.yaml, ci.yml, git hooks, Makefile, pyproject, configs. **15 findings** (1C / 4H / 6M / 4L).
- Dockerfile broken for v1.0.6 — references missing files. **[Top-15 #8]**
- `pyproject.toml` version 1.0.6 vs `__init__.py` 0.1.0.
- `.githooks/pre-commit` is a silent no-op.
- GitHub Actions pinned to floating tags.
- Pipelines reference agents (`researcher`/`tester`/`reviewer`) that exist nowhere in roles or presets.
- Bash-capable presets default to `approval_mode: auto` with no Bash-arg allow-list.
- CI matrix skips Python 3.11.

### 18 · Cross-cutting synthesis — [full report](./audit/findings_18_cross_cutting.md)
**11 findings** (0C / 0H / 5M / 6L).
- README claims 170+ endpoints; actual is 426 (+150%).
- 5 disagreeing version strings across pyproject / `__init__.py` / app.py / README / CHANGELOG.
- 6 internal review artifacts ship in repo root.
- `intelligence/__init__.py` is a 49-byte file with no public facade; 33 modules are a filesystem dump, not a curated API.
- Three intelligence API versions coexist with zero deprecation machinery — 295 intel endpoints live.
- CI tolerates floating action pins and skips Py 3.11.
- CHANGELOG never advanced past 1.0.0 despite 6+ pyproject bumps.

---

## 8. Recommended remediation order

**Week-1 blockers (ship nothing until these land):**
1. Fix the Dockerfile so the container builds at all.
2. Default `AUTH_ENABLED=true`; apply `Depends(verify_admin)` at router-include time for every mutating router; use `hmac.compare_digest`.
3. Wrap every migration in an atomic block; add a DB-backed advisory lock; guard every `ALTER TABLE ADD COLUMN` with `IF NOT EXISTS`.
4. Rewrite `claim_task` as a single `UPDATE ... WHERE ... RETURNING *` on a non-autocommit connection; route all query helpers through the pool.
5. Centralize `taskbrew.util.safe_path(root, candidate)` and delete all 4+ broken local validators.
6. Validate `agent_name`; fix `prune_stale` to source truth from `git worktree list --porcelain`.
7. Enforce ToolRouter allowlists at the MCP dispatch and subprocess-spawn boundary.
8. Centralize `taskbrew.util.run_subprocess(...)` (timeout + stderr drain + cwd + GIT_TERMINAL_PROMPT=0); ban direct subprocess usage via ruff.
9. WebSocket: add Origin allow-list, auth, per-tenant filtering, `max_size`, session scoping by connection id.
10. Resolve webhook hostnames at validate time and pin the IP at request time; reject any private/loopback.

**Month-1 cleanups (required for a Google-class OSS release):**
11. Single version source (`importlib.metadata`); update CHANGELOG; move internal review artifacts out of repo root.
12. Mark v1/v2 intelligence routers deprecated with `Deprecation` + `Sunset` headers; schedule removal.
13. Either (a) wire LLM calls into the "intelligence" modules that advertise them, or (b) rename them to "heuristics" and drop false confidence scores.
14. Add cross-process boot lock for migrations + orphan recovery; make orphan recovery heartbeat-keyed, not blanket.
15. Rewrite v3 router as declarative route table with router-level auth/rate-limit/response_model; collapse 112 handlers.
16. Frontend: introduce a safe tagged-template helper that escapes interpolations by default; refactor inline `onclick` to delegated listeners; add a strict CSP.
17. Test suite: replace v2/v3 status-code-only smoke tests with round-trip assertions; move `_build_full_env` to conftest; stop resetting private `_X_tables_ensured` flags in fixtures.
18. CI: pin actions by SHA; add Py 3.11; drop `pytest -x`; add a `scripts/count_routes.py` check that fails when README endpoint count drifts.

---

## 9. What this audit covered and its limits

**Covered:**
- Every `.py` file under `src/taskbrew/` (~130 files, including the 53 KB migration module and the 59 KB v3 router, both read end-to-end).
- All 90 test files (pattern scan + in-depth read on ~18 files).
- All 24 YAML config files (roles/presets/providers/pipelines/team).
- Dockerfile, docker-compose.yaml, Makefile, pyproject.toml, .env.example, CHANGELOG.md, .github/workflows/ci.yml, both git hooks, the one shell script.
- 5 JS files (grep-exhaustive on XSS sinks + spot-read of matched regions).
- 4 HTML templates (grep-exhaustive on `|safe`, autoescape, script/Jinja interpolation, CSRF, CSP, external CDN).
- 2 CSS files.
- README, architecture.md (cross-checked against code).

**Not covered (out of scope or unverifiable from static review alone):**
- Runtime load / stress testing.
- Real pytest run; the "1300+ tests passing" headline is unverified by this audit (slice 16 spot-checked v3 endpoint tests and found them to be status-code smoke tests; full-suite exit status was not validated).
- Real Docker build (the Dockerfile was statically determined to reference missing files).
- External dependencies (fastapi, aiosqlite, uvicorn, jinja2, pyyaml, claude-agent-sdk) — their own CVE/advisory status.
- Dynamic security analysis (no DAST, no fuzzing).
- Performance profiling.
- Network-level attacks against the deployed dashboard.

**Caveats:**
- Several sub-agents encountered a harness-level restriction preventing direct file writes; their findings were returned as text and hand-serialized into the `audit/` directory. Line numbers are verbatim from the sub-agents' grep output; a spot-check against the live files confirmed representative examples, but the full set was not re-verified line-by-line by the synthesis agent.
- Severity is assigned on a "Google-class OSS audit" standard. A hobby project with the same findings would be graded more leniently; a hardened enterprise product would be graded more strictly.
- Finding deduplication across slices was best-effort. A small number of findings restate the same root cause from different vantage points (e.g. default-off auth appears in slices 10, 11a, 11b, 12a, 12b). The top-15 list is deduplicated; the per-slice counts are not.

---

*End of AUDIT_REPORT.md. Individual slice reports contain full finding-by-finding detail with file:line evidence — start at [`audit/findings_18_cross_cutting.md`](./audit/findings_18_cross_cutting.md) for the best single-file overview, or drill into any per-slice file linked above.*
