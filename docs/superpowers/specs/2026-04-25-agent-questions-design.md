# Agent Questions: Structured Clarification with Auto / Manual Modes

**Status:** Approved 2026-04-25
**Author:** Brainstormed with user through the brainstorming skill.

## Problem

The existing clarification flow is functionally broken (see
2026-04-24 review):

1. The agent can call `request_clarification(question, ...)` and gets
   back ``{status: pending, request_id}``, but no agent prompt tells
   it to wait or poll, so the answer is never read.
2. ``max_clarification_requests`` config exists but is never enforced.
3. There is no task-level pause; the only pause is role-level and
   only blocks new claims, not running tasks.
4. Tasks that legitimately need user input get killed by the
   ``max_execution_time`` watchdog (default 1800 s) regardless of
   whether they're working or waiting on a human.

Users want the Claude-Chat experience: when a task is ambiguous,
the agent surfaces a structured question with candidate options.
A per-role config decides whether the agent picks for itself
(auto) or the task pauses for human selection (manual). Pipelines
run overnight; humans answer in the morning.

## Design

### Per-role config

`RoleConfig` gains two fields:

```yaml
clarification_mode: auto       # | manual; default auto
idle_timeout: 1800             # seconds; replaces max_execution_time semantics
```

`max_execution_time` stays as a back-compat alias — if
`idle_timeout` is missing, fall back to `max_execution_time`. The
semantics shift: it now bounds *idle* time (no SDK activity), not
total execution time.

### MCP tool

New `POST /mcp/tools/ask_question` (separate from the legacy
`request_clarification`, which stays intact for free-text):

```
ask_question(
    task_id:          str,
    group_id:         str,
    agent_role:       str,
    question:         str,         # required, max 2000 chars
    options:          list[str],   # 2-10 distinct entries; each ≤ 500 chars
    preferred_answer: str,         # required, must be in `options`
    reasoning:        str,         # required, max 4000 chars
) -> {
    "selected_answer": str,
    "selected_by":     "agent" | "user",
    "request_id":      str,        # always returned for audit / dashboard linking
    "status":          "answered" | "cancelled",
}
```

**Auto mode behaviour**: persists the question with
`selected_answer = preferred_answer`, `selected_by = "agent"`,
`status = resolved`. Returns immediately.

**Manual mode behaviour**: persists the question with
`status = pending`. Sets `tasks.awaiting_input_since = now`. Blocks
indefinitely on an `asyncio.Event` until either:
- A user POSTs `/api/questions/{id}/answer` with one of the options
  → returns ``{selected_answer, selected_by: "user", status: "answered"}``
- The task is cancelled → returns ``{status: "cancelled"}`` and the
  agent's flow surfaces the cancellation.
- The MCP layer process restarts → the question's row survives in
  the DB; on restart, any agent that re-claims the task can re-issue
  the same question (idempotent on a request_key derived from
  task_id + question hash).

No timeout on the wait. The user's expectation is overnight
pipelines.

Validation at the API boundary:

- `question`, `reasoning` are non-empty and within size caps.
- `options` has 2–10 entries; each non-empty and ≤ 500 chars; no
  duplicates.
- `preferred_answer` is exactly one of `options`.
- Beyond the budget cap (see below) returns 429.

### `max_clarification_requests` enforcement

Counted per `(task_id, agent_role)` against the new
`agent_questions` table. Treats `pending` + `resolved` + `cancelled`
the same — once an agent has burned its budget on a task, no more
questions. Default 10 (existing config).

### Storage

New table `agent_questions` (separate from
`human_interaction_requests` so the schema stays focused per type):

```sql
CREATE TABLE agent_questions (
    id                TEXT PRIMARY KEY,            -- "qst-<uuid12>"
    task_id           TEXT NOT NULL REFERENCES tasks(id),
    group_id          TEXT NOT NULL,
    agent_role        TEXT NOT NULL,
    instance_id       TEXT,                        -- claimed_by at ask time
    question          TEXT NOT NULL,
    options           TEXT NOT NULL,               -- JSON array of strings
    preferred_answer  TEXT NOT NULL,
    reasoning         TEXT NOT NULL,
    selected_answer   TEXT,                        -- NULL while pending
    selected_by       TEXT,                        -- "agent" | "user" | NULL
    status            TEXT NOT NULL DEFAULT 'pending',
                                                   -- "pending" | "resolved" | "cancelled"
    created_at        TEXT NOT NULL,
    resolved_at       TEXT
);
CREATE INDEX idx_agent_questions_task ON agent_questions(task_id);
CREATE INDEX idx_agent_questions_status ON agent_questions(status);
```

New column on `tasks`:

```sql
ALTER TABLE tasks ADD COLUMN awaiting_input_since TEXT;
```

Migration 32 adds both. Baseline schema in `database.py` mirrors so
fresh installs don't need to run migration to use the feature.

### Task-level pause via `awaiting_input_since`

When `ask_question` enters its wait (manual mode), it sets
``tasks.awaiting_input_since = now()``. When the question resolves
(answered or cancelled), the column is cleared back to NULL.

Orphan-recovery (which filters on ``status='in_progress'`` and
stale heartbeat) doesn't reclaim such tasks because the per-task
heartbeat loop keeps ticking even while blocked on the MCP wait.
The instance's status becomes ``awaiting_input`` (a new value,
distinct from ``working`` / ``paused``) so dashboards render the
state clearly. The orphan-recovery query continues to filter on
``status='working'`` so awaiting_input agents are never
incorrectly reclaimed.

Server restart: the `awaiting_input_since` column survives. On
restart the agent process is gone (and its asyncio.Event with it),
so the question stays `pending` in the DB. When the agent re-claims
the task on the next poll, it re-runs `build_context`. The retry
can re-issue `ask_question` with the same parameters; the
idempotent `request_key` (hash of question text) returns the
existing pending row instead of creating a duplicate.

### Idle watchdog (replaces `max_execution_time`)

The existing `asyncio.wait_for(execute_task(), timeout=...)`
is replaced with an activity-based watchdog:

```
last_activity_ts = task_claim_time
On any tool call / token / model-message event from the SDK:
    last_activity_ts = now
On `awaiting_input_since` set:
    timer effectively paused (watchdog skips this case)
On `awaiting_input_since` cleared (answer or cancel):
    last_activity_ts = now
Watchdog tick (every ~15 s, piggy-backed on the heartbeat loop):
    if (awaiting_input_since is None) and
       (now - last_activity_ts) > role.idle_timeout:
        kill the task as idle-timeout
```

Implementation: extend `AgentRunner` to accept `on_tool_use`,
`on_token`, `on_message` callbacks (chat_manager already does this
pattern). The `AgentLoop` registers a callback that updates
`self._last_activity_ts`. The heartbeat loop checks the watchdog
condition each tick.

`max_execution_time` stays as a YAML field. On RoleConfig load:
``idle_timeout = data.get("idle_timeout") or data.get("max_execution_time") or 1800``.

### Dashboard surface

Three new endpoints:

```
GET  /api/questions/pending              # list pending agent_questions
GET  /api/questions/{id}                 # single question detail
POST /api/questions/{id}/answer          # human picks an option
                                         # body: { selected_answer: str }
                                         # admin-gated (verify_admin)
```

Small new dashboard panel on the home page next to the existing
pending-interactions widget. For each pending question:

- Task ID + role badge
- Question text
- Options as radio buttons, **none pre-selected** (no anchoring on
  the agent's preferred)
- ``<details>`` block titled "Agent's recommendation: <preferred>"
  collapsed by default; expanding reveals the agent's reasoning
- Submit button (disabled until a radio is selected)
- Cancel-this-task button (calls existing cancel endpoint)

**Notifications**: visual count badge only this round. Browser /
email / Slack push are layered later if needed.

**Live updates**: WS event bus emits
``question.pending(question_id, task_id, group_id, role)`` on
creation and ``question.resolved(question_id, selected_answer,
selected_by)`` on answer. Dashboards listen and update without
polling.

### Coder system-prompt addendum

Default `coder.yaml` and the six coder presets gain one paragraph:

> "When the task is ambiguous, use `ask_question(question, options,
> preferred_answer, reasoning)` instead of guessing. List 2–5
> candidate answers; record your best guess in `preferred_answer`
> and explain the choice in `reasoning`. The system will return
> the selected answer (your own pick in auto mode, or the user's
> in manual mode). Do not call this for trivial decisions; you have
> a budget per task."

## Testing

- Auto mode immediate return
- Manual mode blocks then resumes on POST /answer
- Cancel-task while waiting returns cancelled to agent
- `awaiting_input_since` set/cleared correctly
- Idle watchdog kills inactive task
- Idle watchdog does NOT kill task while `awaiting_input` even past timeout
- `max_clarification_requests` enforces; 11th call returns 429
- Budget exhaustion emits `task.clarification_budget_exhausted` event
- Dashboard endpoints: pending list, answer flow, 404 on unknown id
- Migration adds columns idempotently
- Re-issue with same question hash on restart returns existing pending row

## Rollout

- Migration 32 runs at next dashboard boot.
- No-op for existing roles (default `clarification_mode=auto`).
- Operators flip individual roles to `manual` via dashboard role
  settings or YAML edit.
- Behaviour change is user-visible: `max_execution_time` semantics
  shift from "total runtime" to "idle runtime" (counted only when
  not awaiting input). Tasks that today hit the wall-clock at 30
  min while doing real work continue to be killed (no idle since
  start, but no real activity either) -- this case is rare. Tasks
  that today get killed because the agent is stuck in a long Bash
  command will continue to be killed when the Bash timeout fires.
  CHANGELOG entry covers it.

## Out of scope

- Browser / email / Slack notifications for pending questions.
- Free-text answers in manual mode (we only support picking from
  the agent's enumerated options; that's the whole point of
  structured clarification).
- Cross-task context for a question (e.g., "this answer applies to
  every coder task in this group"). One question, one task.
- Replacing the legacy `request_clarification` endpoint. It stays
  for free-text use cases.
- Migration of existing pending `human_interaction_requests` rows
  into the new table. They live in their own table.

## Decisions captured

- Per-role mode (`clarification_mode: auto | manual`), default `auto`.
- One uniform tool shape regardless of mode; agent always provides
  preferred_answer + reasoning.
- New table `agent_questions` separate from
  `human_interaction_requests`.
- Indefinite wait in manual mode (no timeout on the wait itself).
- `idle_timeout` replaces `max_execution_time` semantics; activity-
  based watchdog; default 30 min.
- Dashboard radio buttons start with no selection (no anchoring on
  agent recommendation).
- `max_clarification_requests` enforced per (task, role); default 10.
- `request_clarification` legacy tool kept; new tool is additive.
- No notifications beyond visual count this round.
