# Audit Findings — Agents Subsystem

**Files reviewed:**
- src/taskbrew/agents/__init__.py
- src/taskbrew/agents/agent_loop.py
- src/taskbrew/agents/auto_scaler.py
- src/taskbrew/agents/base.py
- src/taskbrew/agents/gemini_cli.py
- src/taskbrew/agents/instance_manager.py
- src/taskbrew/agents/provider.py
- src/taskbrew/agents/provider_base.py
- src/taskbrew/agents/roles.py

**Reviewer:** audit-agent-02

---

## Finding 1 — Gemini subprocess never enforces a wall-clock timeout
- **Severity:** HIGH
- **Category:** resource-leak
- **Location:** src/taskbrew/agents/gemini_cli.py:151-251
- **Finding:** `asyncio.create_subprocess_exec` starts the gemini CLI and the generator awaits `process.stdout` + `process.wait()` with no `asyncio.wait_for`/cancel-on-stall. If the CLI hangs (no stdout, no EOF) the coroutine blocks forever; only the outer `wait_for` in `run_once` bounds `execute_task`, and that bound is optional per-role.
- **Impact:** A stuck CLI invocation leaks an agent instance and subprocess indefinitely, stalling the role's queue and consuming FDs.
- **Fix:** Wrap the stdout read loop and `process.wait()` in `asyncio.wait_for` with a provider-level timeout and SIGTERM→SIGKILL the child in `finally`.

## Finding 2 — Gemini stderr never drained — pipe deadlock risk
- **Severity:** HIGH
- **Category:** resource-leak
- **Location:** src/taskbrew/agents/gemini_cli.py:153-255
- **Finding:** The subprocess is launched with `stderr=subprocess.PIPE`, but stderr is only read *after* the stdout loop exits (line 255) and only if `got_result` is False. If gemini writes >64KB to stderr (verbose/debug modes) while stdout still streams, stderr blocks → child blocks → stdout stops → parent hangs.
- **Impact:** Deterministic hang under any verbose Gemini session; full agent stall.
- **Fix:** Drain stderr concurrently via a second task, or use `stderr=DEVNULL` when not needed.

## Finding 3 — LLM prompt injection via rejection_reason / parent output / task fields
- **Severity:** HIGH
- **Category:** security
- **Location:** src/taskbrew/agents/agent_loop.py:106-289
- **Finding:** `build_context` interpolates untrusted strings — `task[title]`, `task[description]`, `parent[output_text]`, `original[rejection_reason]`, sibling titles, memories, conventions — directly into a markdown-structured prompt with no escaping or delimiters. A previous agent, or an external API caller, can inject fake `## Your Task` / `<system>` headers, counterfeit "Connected Agents" blocks, or "ignore prior steps" directives.
- **Impact:** Cross-agent prompt injection: coder tasks can rewrite architect prompts; an API caller can push privileged instructions; verifier outputs poison downstream revisions.
- **Fix:** Fence interpolated task content inside untrusted-content delimiters (sentinel-fenced blocks) and strip markdown/XML headers from description/title/rejection_reason before concatenation.

## Finding 4 — `routing_mode: restricted` is never enforced on the agent path
- **Severity:** HIGH
- **Category:** api-contract
- **Location:** src/taskbrew/agents/agent_loop.py:173-213, src/taskbrew/agents/roles.py (entire)
- **Finding:** `RoleConfig.routing_mode` can be `"restricted"`. Only `dashboard/routers/tasks.py` checks it for dashboard task creation; the in-agent `create_task` MCP tool receives no server-side check that `assigned_to` is in the caller's `routes_to`. `build_context` just lists allowed routes in the prompt — advisory only.
- **Impact:** A "restricted" coder can still delegate to any role by calling `create_task` with arbitrary `assigned_to`, bypassing the pipeline contract.
- **Fix:** Enforce `routing_mode` at the MCP `create_task` boundary (reject when caller role is restricted and target not in routes_to), not just in the dashboard.

## Finding 5 — Retries reuse the same worktree without resetting state
- **Severity:** HIGH
- **Category:** correctness-bug
- **Location:** src/taskbrew/agents/agent_loop.py:746-805
- **Finding:** The worktree is created **once** before the retry for-loop (line 751). Any exception inside `execute_task` is retried up to 3 times with the *same* `worktree_path` and `branch_name`; if a prior attempt left uncommitted state, merge conflicts, or a detached head, subsequent attempts inherit it.
- **Impact:** Partial commits, silent corruption of branch history across retries, misleading "succeeded on attempt 3" outputs that reference first-attempt artifacts.
- **Fix:** `git reset --hard && git clean -fdx` between attempts, or tear down and recreate the worktree on retry.

## Finding 6 — Retry backoff has no jitter and can exceed heartbeat staleness window
- **Severity:** MEDIUM
- **Category:** correctness-bug
- **Location:** src/taskbrew/agents/agent_loop.py:791-797
- **Finding:** Backoff is `RETRY_BASE_DELAY * (3 ** attempt)` = 5/15/45/135s with no jitter. Simultaneously-failing agents retry in lockstep (thundering herd). Cumulative wall time (~200s + 3·timeout) stays live because the heartbeat loop keeps ticking — defeating the 90s staleness window used by orphan recovery.
- **Impact:** Queue starvation plus orphan-recovery blindness during upstream failure storms.
- **Fix:** Add random jitter and cap total retry wall time against `role.max_execution_time`.

## Finding 7 — `AgentStatus.ERROR` cannot be persisted; InstanceManager rejects it
- **Severity:** MEDIUM
- **Category:** correctness-bug
- **Location:** src/taskbrew/agents/base.py:22-27 vs src/taskbrew/agents/instance_manager.py:28
- **Finding:** `AgentStatus` enum defines `ERROR = "error"`, but `InstanceManager.VALID_STATUSES = {"idle","working","paused","stopped"}` and `update_status` raises `ValueError` on anything else. `AgentRunner.run` sets `self.status = AgentStatus.ERROR` on exception but the run loop's outer handler resets the DB to `"idle"` (line 886), dropping the error signal.
- **Impact:** Agent error states are invisible to the dashboard/DB; crashed agents show as idle.
- **Fix:** Add `"error"` to `VALID_STATUSES` and persist it, or drop the enum value.

## Finding 8 — Crash path in `run()` leaves stale `current_task` on the instance row
- **Severity:** MEDIUM
- **Category:** correctness-bug
- **Location:** src/taskbrew/agents/agent_loop.py:884-887, 863
- **Finding:** If `run_once` raises uncaught, the outer `except` calls `update_status(..., "idle")` with `current_task` defaulting to None — but a reentrant crash before the `run_once` `finally` (line 854-863) means the worktree cleanup and the explicit `current_task=None` update are skipped. Combined with the timeout branch returning True at line 789 without clearing `current_task`, stale references accumulate.
- **Impact:** Stale task references survive on instance rows; orphan recovery double-recovers; dashboards mis-attribute.
- **Fix:** Always pass `current_task=None` in the `run()` recovery path; move cleanup into a `try/finally` that is guaranteed to run.

## Finding 9 — Heartbeat coverage is fragile against long backoff sleeps
- **Severity:** MEDIUM
- **Category:** concurrency
- **Location:** src/taskbrew/agents/agent_loop.py:692-701, 762-805, main.py _orphan_recovery_loop
- **Finding:** `_heartbeat_loop` is per-task (cancelled at line 801). The `run()`-level heartbeat ticks only once per poll cycle (line 888). Long retry-backoff sleeps live inside the per-task hb, so 15s hb interval + 90s staleness is safe — but `get_stale_instances` default timeout is 60s (instance_manager.py:131), and if anyone invokes it with the default rather than the 90s used in main.py, the gap is unsafe.
- **Impact:** Potential false-positive orphan recovery reclaiming tasks while the original agent is mid-handoff.
- **Fix:** Either make the hb loop instance-level (lives across poll cycles) or raise the default staleness on `get_stale_instances` to match the sum of the longest non-hb gap.

## Finding 10 — Auto-scaler scale-down races with task claim
- **Severity:** MEDIUM
- **Category:** concurrency
- **Location:** src/taskbrew/agents/auto_scaler.py:186-225
- **Finding:** `_check_and_scale` reads `pending_count == 0` and the idle list, then awaits `_agent_stopper`. Between those reads and the stop, the target instance may claim a fresh task (no lock). If the stopper hard-cancels the asyncio task, the claim is stranded in `in_progress` with a dead claimant.
- **Impact:** Tasks stuck `in_progress` until orphan recovery catches them (~30–120s).
- **Fix:** Atomically transition instance to `"stopped"` in DB before cancelling the task, and have `claim_task` refuse claims on stopped instances.

## Finding 11 — Auto-scaler `_active_extra` drifts on external stops / crashes / restart
- **Severity:** MEDIUM
- **Category:** correctness-bug
- **Location:** src/taskbrew/agents/auto_scaler.py:157-184, 223
- **Finding:** `_active_extra[role]` is in-memory only. Manual stops via dashboard or agent crashes bypass `_agent_stopper`, so the counter never decrements — the scaler refuses to grow further. The naming `{role}-auto-{N}` with `N = _active_extra.get(role)+j+1` starts at 1 again on restart, colliding with existing `agent_instances` rows (INSERT OR REPLACE clobbers metadata).
- **Impact:** Auto-scaler capacity permanently lost; instance ID collisions after restart.
- **Fix:** Re-derive `_active_extra` each tick from DB (`instance_id LIKE 'role-auto-%'`) and pick `MAX(suffix)+1`.

## Finding 12 — `_count_changed_loc` hardcodes `main` as the base branch
- **Severity:** MEDIUM
- **Category:** edge-case
- **Location:** src/taskbrew/agents/agent_loop.py:639-676
- **Finding:** `git diff --numstat main...HEAD` is literal. Repos using `master`/`develop` always return non-zero, hit the None fallback, and — per line 632-634 — force-require verification on every substantial task. On a `main`-repo with stale `main`, the wrong diff baseline is used.
- **Impact:** Verifier queue inflated on non-`main` repos; wrong LoC counts elsewhere.
- **Fix:** Discover default branch via `git symbolic-ref refs/remotes/origin/HEAD` or orchestrator config.

## Finding 13 — YAML-loaded providers can hijack any model-name prefix
- **Severity:** MEDIUM
- **Category:** security
- **Location:** src/taskbrew/agents/provider.py:120-147
- **Finding:** `load_yaml_providers` does `yaml.safe_load` (good), then calls `register(name, detect_patterns=...)` with attacker-controlled values. Nothing prevents a YAML file from claiming `name="claude"` and `detect_models=["*"]`, silently rerouting every model to a user-supplied provider. Provider dispatch (`sdk_query`, `build_sdk_options`) only knows `"gemini"` vs anything-else — custom providers registered via YAML aren't actually invoked here, but the registry still claims to `.detect(model)` them.
- **Impact:** On systems with a user-writable providers dir, registration poisons provider choice; combined with `cli_path` (finding 14), this is an escalation vector.
- **Fix:** Validate YAML-provider names (regex, not builtin shadow), restrict detect patterns, require admin confirmation.

## Finding 14 — `cli_path` is used as argv[0] with no validation
- **Severity:** MEDIUM
- **Category:** security
- **Location:** src/taskbrew/agents/provider.py:223-249, src/taskbrew/agents/gemini_cli.py:97-109
- **Finding:** `cli_path` flows from config → `ClaudeAgentOptions.cli_path` / `GeminiOptions.cli_path` → subprocess argv[0]. `_find_cli` returns the value verbatim when set. No ownership/allow-list/hash check. Any config-injection sink that can set `cli_path` (YAML, API endpoint, DB row) becomes arbitrary code execution.
- **Impact:** RCE via configuration injection.
- **Fix:** Require `cli_path` inside an allow-listed directory (`/usr/local/bin`, `/opt/homebrew/bin`) or match `shutil.which(name)`.

## Finding 15 — Context trimming uses a lossy 4-chars/token heuristic
- **Severity:** LOW
- **Category:** edge-case
- **Location:** src/taskbrew/agents/base.py:106-129
- **Finding:** `_trim_context` keeps first 20% + last 60% (80% total, 20% dropped) with a marker. Estimator `len(text)//4` under-counts code-heavy prompts; a 600k-char prompt with 150k budget → no trim, SDK rejects at 200k tokens.
- **Impact:** Long revision chains hit the model's context limit despite the guard.
- **Fix:** Use the provider's tokenizer, or conservatively 3 chars/token.

## Finding 16 — `_on_pre_tool_use` / `_on_post_tool_use` leak fire-and-forget tasks
- **Severity:** LOW
- **Category:** resource-leak
- **Location:** src/taskbrew/agents/base.py:85-103
- **Finding:** Both hooks `asyncio.create_task(self.event_bus.emit(...))` and discard the handle. Emit failures become silent "Task exception was never retrieved" warnings; no backpressure on the event bus.
- **Impact:** Swallowed errors during high tool-use churn.
- **Fix:** `await` the emit (the hook is already async) or attach a done-callback that logs exceptions.

## Finding 17 — `_count_actionable_children` hardcodes a role allow-list
- **Severity:** LOW
- **Category:** dead-code
- **Location:** src/taskbrew/agents/agent_loop.py:574-587
- **Finding:** Filters by `assigned_to IN ('coder','verifier','reviewer','integrator')`. Projects with custom role names (e.g. `implementer`, `qa`) see 0 actionable children and get re-queued + escalated.
- **Impact:** False-positive fanout escalations on teams with custom role names.
- **Fix:** Derive the allow-list from `self.all_roles` (any role whose `produces` includes implementation/verification tags).

## Finding 18 — Builtin MCP env_source lookup will KeyError on extension
- **Severity:** LOW
- **Category:** edge-case
- **Location:** src/taskbrew/agents/provider.py:59-75
- **Finding:** `env_sources = {"api_url": api_url, "db_path": db_path}` is hardcoded. Any new builtin whose `env_source` is not one of those two raises KeyError at SDK-build time.
- **Impact:** Fragile extension point; new builtins need two coordinated edits.
- **Fix:** Resolve each builtin's env via a handler callable, or pass the full mapping explicitly.

## Finding 19 — `get_stale_instances` runs a full scan every 30s (no composite index)
- **Severity:** LOW
- **Category:** perf
- **Location:** src/taskbrew/agents/instance_manager.py:131-149
- **Finding:** Query filters `status` and two timestamp columns; schema (orchestrator/database.py) has no composite index on `(status, last_heartbeat)`. Orphan-recovery tick scans the whole table.
- **Impact:** Slow at hundreds of instances; cheap fix.
- **Fix:** `CREATE INDEX idx_agent_status_heartbeat ON agent_instances(status, last_heartbeat)`.

---

## Systemic issues observed across this slice

- **Prompt content is trusted end-to-end.** Untrusted task strings, rejection reasons, memories, and conventions are concatenated into prompts with no escaping, delimiters, or length caps. The loop assumes a cooperating-operator threat model.
- **Subprocess lifecycle is casual.** Gemini CLI has no wall-clock timeout, no concurrent stderr drain, no SIGKILL-on-stall. `cli_path` is honored verbatim. These add up to DoS and RCE vectors when config is even partially attacker-influenced.
- **Enforcement of routing contracts only lives at the dashboard.** `routing_mode: restricted` is checked in `dashboard/routers/tasks.py`, but the MCP `create_task` tool path does not enforce it — the pipeline is a suggestion the LLM can ignore.
- **In-memory bookkeeping diverges from DB truth.** `AutoScaler._active_extra`, `InstanceManager._paused_roles`, and `AgentStatus.ERROR` live only in the Python process; restarts, manual stops, or crashes leave them inconsistent with `agent_instances`.
- **Retry logic treats failures as idempotent.** Worktrees aren't reset between attempts, backoff has no jitter, and the heartbeat loop's coverage of long sleeps relies on coincidental threshold ordering rather than explicit guarantees. Failure storms amplify rather than damp.
