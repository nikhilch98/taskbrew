# Multi-project, isolated — why TaskBrew runs one at a time, and how to lift it

## TL;DR

TaskBrew runs **one project at a time by design, not because projects share state.**
Each project is already fully isolated (its own DB, git repo, worktrees, config, and
`Orchestrator`). The single-project limit is **three singletons** plus **one HTTP
callback path**. Lifting it is bounded work — you do *not* need to touch the ~170
view endpoints.

---

## Why only one project today

### Isolation already exists (per project)

| Resource | Scope | Where |
|---|---|---|
| SQLite DB | per project | `team.yaml` `database.path` → `~/.taskbrew/data/{slug}.db` |
| Git repo / working dir | per project | project `directory` in `~/.taskbrew/projects.yaml` |
| Worktrees | per project | `{project_dir}/.worktrees` (`WorktreeManager`) |
| Config (team + roles) | per project | `{project_dir}/config/` |
| `Orchestrator` (agent loops, task board, event bus, merge broker, system gates, intelligence managers) | per project | `build_orchestrator(project_dir)` in `main.py` |

Tasks have **no `project_id` column** — and they don't need one. Two projects never
mix because they live in two different `.db` files opened by two different
orchestrators.

### The actual blockers

1. **Registry** (`~/.taskbrew/projects.yaml`) stores a single `active_project` string.
2. **`ProjectManager.orchestrator`** is a single attribute. `activate_project()` calls
   `deactivate_current()` *first* — it tears the running project down (DB close, agent-task
   cancel, worktree cleanup) before building the next one.
3. **`_deps._orchestrator`** is a module-global. Every API endpoint reads it via
   `get_orch()`. Activation swaps this one reference, so the whole dashboard binds to
   exactly one project.
4. **Agent write-back is HTTP, not in-process.** Agents *poll/claim* via an in-process
   `self.board` (per-orchestrator, isolated ✅), but their `create_task` / `complete_task`
   / `record_check` tools run in a **FastMCP stdio subprocess** that calls back over HTTP
   to `TASKBREW_API_URL` (default `http://127.0.0.1:8420`) → the dashboard → `get_orch()`.
   The system agent calls back the same way (`build_system_agent_config(api_url=...)`).

Blocker 4 is the one that matters: run two orchestrators at once and project B's
`complete_task` callback can land on whichever orchestrator `get_orch()` currently
points at — i.e. **project A's database.** So concurrency is unsafe until the *callback*
path is project-scoped. That's a small, well-defined subset of endpoints (the
`mcp__task-tools__*` and system-agent routes), **not** all 170.

---

## Target: “focus one, run many”

The user need is to **run and pause projects independently**, not to *view* several at
once. So:

- Multiple `Orchestrator` instances run concurrently in the daemon, each isolated.
- The dashboard still *focuses* one project for detailed views (board, metrics, settings).
- Per-project Start / Pause / Resume / Stop controls act on each orchestrator independently.

This avoids a 170-endpoint rewrite while delivering the actual ask.

---

## Implemented (Phases 1–2 — concurrent orchestrators)

Shipped. The dashboard now runs **N isolated projects concurrently** in one
process, focuses one for detail views, and starts/pauses/resumes each
independently. Full test suite green (1724 passed).

**How scoping works:** each agent's MCP subprocess is given `TASKBREW_PROJECT_ID`
(`provider._build_mcp_dict` → `task_tools._json_headers`), so its HTTP callbacks
carry an `X-Taskbrew-Project` header. The `_project_scope` middleware binds it to
a request-scoped `ContextVar`; `routers/_deps.get_orch()` resolves the
request's own orchestrator from a pool (`{project_id: Orchestrator}`). Resolution
is **strict** — a header naming an unknown project returns `None` (→ 409) rather
than silently cross-writing the focused project's database. Verified to propagate
and isolate under genuine concurrency on the pinned Starlette
(`tests/test_concurrent_project_scope.py`).

**The identity invariant:** `registry id == orch.project_id == _deps pool key ==
X-Taskbrew-Project == TASKBREW_PROJECT_ID`. `activate_project`/`start_project`
pass the registry id into `build_orchestrator(project_id=...)` so all five agree
(the dir-slug default is a fallback for standalone/test callers only).

**Key pieces:** `ProjectManager` keeps an orchestrator pool with
`start_project`/`stop_project`/`focus_project` (additive — no teardown of
others); `.orchestrator` is a property returning the focused one (+ back-compat
setter). `activate_project` keeps its switch-semantics for back-compat. New
endpoints `POST /api/projects/{id}/{start,stop,focus}`;
`GET /api/projects/status` is enriched with per-project `runtime` (running,
paused, focused). Each `Orchestrator` carries its own `interaction_manager`;
`mcp_tools`/`interactions` resolve `task_board`/`event_bus`/`interaction_manager`
per-request via the contextvar-aware getter. The home UI sends the
`X-Taskbrew-Project` header on pause/resume and starts projects via `/start`.

### Known focused-project-only limitations

These resolve to the **focused** project (no header), which is acceptable for the
shipped scope but should be made header-scoped if cross-project use of them grows:

- **`pipeline_getter`** (`mcp_tools.route_task` / `get_my_connections`) stays a
  fixed global — pipeline edges are static role topology, identical across
  default projects, so route validation uses the focused project's pipeline.
- **`/api/interactions/pending`** lists only the focused project's pending
  approvals; a manual-mode interaction on a non-focused background project is
  not visible until that project is focused.
- **`/api/interactions/{id}/approve|reject|...`** resolve to the focused
  project, so approving an interaction belonging to a different running project
  requires focusing it first. (Manual/first-run approval is non-default; the
  default `auto` mode never touches the interaction manager.)

### Operational constraints (by design, not bugs)

- **Shared dashboard port.** Every concurrent orchestrator derives its agent
  `api_url` from its own `team_config.dashboard_port`, but the server binds one
  port. This works only because the default `team.yaml` hardcodes `8420` for
  every project. A project whose `team.yaml` sets a *different* port would have
  its agents POST callbacks to a dead port and silently never call back (total
  callback failure for that project — not corruption). If per-project ports are
  ever needed, the agents must be pointed at the single bound port, not the
  per-project config value.
- **Stale/unknown header never cross-writes (strict).** An explicit
  `X-Taskbrew-Project` value that does not resolve to a live orchestrator makes
  the MCP resolvers return `None` → the handler 503s → **no write**. The global
  (boot) board is used as a fallback *only* for headerless human-dashboard
  requests, never for an explicit-but-unresolved header. This closes a real
  stop-race: a project can be stopped/unregistered while one of its agent's
  fire-and-forget callbacks is still on the wire; that callback arrives with a
  now-stale header and, without the guard, would have fallen back to the boot
  board and silently cross-written it (sharpened by task-id collisions across
  projects). Enforced by `_header_scoped()` in `mcp_tools` / `_mgr()` in
  `interactions`; proven by `tests/test_concurrent_callback_isolation.py`.

### Verification

The scoping seam is tested end-to-end, both halves:

- **Client half** (env → header): `tests/test_provider_mcp.py` asserts
  `agent_project` lands in the subprocess env as `TASKBREW_PROJECT_ID`;
  `tests/test_task_tools_mcp.py` asserts that env var becomes the
  `X-Taskbrew-Project` callback header (and is absent when unset).
- **Server half** (header → contextvar → resolution): `tests/test_concurrent_project_scope.py`
  exercises concurrent interleaved requests on the pinned Starlette.
- **End-to-end** (real DBs): `tests/test_concurrent_callback_isolation.py` runs
  two real orchestrators with their own databases through the real
  `record_check` callback and proves a beta-scoped write lands in beta's DB and
  never the focused alpha's — even with alpha set as the global-fallback board.
  It also proves a headerless request follows the focused project, and a
  stale/unknown header refuses (503) without touching either database.

## Phased plan

### Phase 0 — per-project run control UI (DONE)

`home.html` “Your projects” tiles (and the full Projects cards) now carry a ▶ / ⏸
control:

- **Inactive project** → ▶ **Start** = `POST /api/projects/{id}/activate` (switch-with-confirm; honest about the teardown under the current model).
- **Active + running** → ⏸ **Pause** = `POST /api/agents/pause {role:"all"}`.
- **Active + paused** → ▶ **Resume** = `POST /api/agents/resume {role:"all"}`.

Paused state is read from `GET /api/agents/paused` (now fetched in `loadActiveData`).
No backend change required. This is forward-compatible: the same buttons become truly
per-project once Phase 2 lands.

### Phase 1 — orchestrator pool (DONE)

- `ProjectManager.orchestrator` (single) → `orchestrators: dict[project_id, Orchestrator]`
  plus a `focused_project_id`. Keep `get_active()`/`set_active()` meaning “focused”.
- Stop tearing down on switch: `focus_project(id)` just repoints `_deps` (build lazily if
  not yet running). Add `start_project` / `stop_project` / `pause_project` / `resume_project`.
- Registry: `active_project` → `focused_project` + a `running: [ids]` list so the daemon
  restores the running set on boot. Move pause state from the in-memory
  `InstanceManager._paused_roles` into the per-project DB so it survives restarts.
- Still **serialize execution** (only the focused project’s agents run) until Phase 2 makes
  callbacks safe. This phase is pure refactor, low risk.

### Phase 2 — project-scoped agent callbacks (DONE)

The one piece that makes “run many” safe.

- Parent orchestrator already exports `TASKBREW_AGENT_ROLE` / `TASKBREW_AGENT_INSTANCE`
  to each MCP subprocess. **Also export `TASKBREW_PROJECT_ID`.**
- `task_tools.py` (and the system-agent client) send that id on every callback
  (header `X-Taskbrew-Project` or path prefix).
- The callback subset of routes resolves `orchestrators[project_id]` instead of the global
  `get_orch()`. Concretely: the `mcp__task-tools__*` targets in `routers/tasks.py`
  (`create_task`, `complete_task`, `record_check`, `ask_question`, work-package ops) and the
  system-gate/questions callbacks. A `get_orch_for_project(request)` dependency centralizes it.
- Everything else keeps using the focused-orchestrator `get_orch()` — untouched.

With callbacks scoped, multiple orchestrators can run their agents in parallel safely,
each writing only to its own DB.

### Phase 3 — polish

- Per-project Stop (full `shutdown`) vs Pause (halt agents, keep orchestrator warm).
- Rail status dots already model `work` / `pause` / `warn` / `idle` per project — wire them
  to each orchestrator’s real state.
- Resource guardrails: a max-concurrent-projects cap and aggregate cost budget, since N
  projects = N× the CLI-subprocess / token load.

---

## Risk notes

- **Cost/load**: every running project spawns its full agent fleet (CLI subprocesses).
  Concurrency multiplies spend — gate it behind an explicit cap.
- **Ports**: orchestrators share the single dashboard port. Cross-writes are prevented by
  the project-id callback header (Phase 2); agents still resolve the *port* itself from the
  default `team.yaml` (`8420` for all). See "Operational constraints" above before giving a
  project a non-default `dashboard_port`.
- **Merge broker / system gates** are already per-orchestrator, so they parallelize cleanly.
- **Pause durability**: today pause state is in-memory and lost on restart (Phase 1 fixes it).
