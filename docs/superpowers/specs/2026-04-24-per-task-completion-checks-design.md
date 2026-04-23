# Per-Task Completion Checks

**Status:** Approved 2026-04-24
**Author:** Brainstormed with user through the brainstorming skill.

## Problem

A user installs TaskBrew, starts a project, and runs a coder task. Today the
system has no built-in way to verify that the task's output (a diff on a git
branch) is correct before it merges. The existing VR gate ensures a *reviewer
task exists*, but it does not assert that the coder's build and tests actually
passed. Users who never configure a verifier role therefore get pipelines that
feel fast but ship unverified code.

We want a correctness gate that:

1. Works out of the box without the user configuring a verifier role.
2. Is not project-specific (can't assume Python or Node or any particular
   build system).
3. Does not depend on prompt guidance alone for correctness (prompts quietly
   fail; infra enforces).
4. Integrates with the existing orchestrator gates (fanout, VR) and uses the
   same patterns already in the codebase.

## Design

Each task carries its own verification fingerprint as structured data. The
merge policy reads the task's recorded checks and decides. No project-wide
detection, no required verifier role, no LLM-as-judge.

### Data model

Three new columns on `tasks`, added via migration 31 and mirrored in the
baseline schema:

```sql
ALTER TABLE tasks ADD COLUMN completion_checks      TEXT DEFAULT '{}';
ALTER TABLE tasks ADD COLUMN merge_status           TEXT;
ALTER TABLE tasks ADD COLUMN verification_retries   INTEGER DEFAULT 0;
```

`completion_checks` holds a JSON object keyed by freeform check name. Each
value records status + optional metadata:

```json
{
  "build": {"status": "pass",    "details": "...",
            "duration_ms": 2300, "command": "npm run build"},
  "tests": {"status": "pass",    "details": "42 passed", "duration_ms": 5600},
  "lint":  {"status": "skipped", "details": "no lint command"}
}
```

`status` is one of `pass` / `fail` / `skipped`. Anything else is rejected by
the write path. The system does not enforce which check names exist: a
docs-only task may record `{markdown_lint: pass}`; a core-logic task may
record `{build, tests, type_check}`.

`merge_status` is populated by the verification gate at completion time and
takes one of:

- `merged` — all recorded checks passed, task merged.
- `merged_unverified` — no checks recorded, task merged, event emitted.
- `verification_failed` — verification retries exhausted, escalated.
- `NULL` — task has not reached the merge gate yet.

`verification_retries` mirrors `fanout_retries`: bumped each time a task is
re-queued due to a failing check, capped at 2 before escalation.

### Agent interface

A new MCP tool, `record_check`, available on every agent with MCP access and
authed via the existing `_get_token` path:

```
record_check(
  task_id:     str,
  check_name:  str,              # freeform
  status:      "pass" | "fail" | "skipped",
  details:     str | None,
  duration_ms: int | None,
  command:     str | None,
) -> dict
```

Semantics:
- Merges the entry into `tasks.completion_checks` via `json_patch`-style
  UPDATE. Overwrites a prior entry with the same `check_name`, so re-running
  a check is idempotent.
- Validates `status` enum server-side; rejects anything else with 400.
- Emits `task.check_recorded` with `{task_id, check_name, status}` so the
  dashboard's WS consumer can render live.
- Writes are agent-supplied and trusted. The user has explicitly chosen this
  trust model; a future hardening pass ("infra re-runs the commands") can
  layer on without changing the data model.

Default coder role YAMLs (`config/roles/coder.yaml`,
`config/presets/coder_*.yaml`) receive a standard addendum to their system
prompt:

> "After you finish your work, run the project's build, tests, and linter
> (whatever applies). For each, call `record_check` with the result. Mark
> checks you can't run as `skipped` with a reason. A task with a failed
> check will be re-queued for you to fix; a task with no checks recorded
> will merge as unverified."

Users who customize their own coder prompt get nothing new; they opt in by
copying the guidance from the updated defaults.

### Merge gate

The gate lives in `complete_and_handoff` in `agent_loop.py`, after the
existing fanout + VR-child gates and before `complete_task_with_output`:

```python
checks = json.loads(task.completion_checks or "{}")
failed = [name for name, c in checks.items() if c.get("status") == "fail"]

if failed:
    retries = task.get("verification_retries") or 0
    if retries < 2:
        await self._requeue_for_verification(task, retries, failed)
        return
    await self.event_bus.emit("task.escalation_required", {
        "task_id": task["id"],
        "reason": "verification_failed_after_retries",
        "failed_checks": failed,
        "retries": retries,
    })
    task["merge_status"] = "verification_failed"
elif not checks:
    task["merge_status"] = "merged_unverified"
    await self.event_bus.emit("task.unverified_merge", {
        "task_id": task["id"],
        "group_id": task["group_id"],
    })
else:
    task["merge_status"] = "merged"
```

`_requeue_for_verification` mirrors the existing `_requeue_for_fanout`:

```python
await self.board._db.execute(
    "UPDATE tasks "
    "SET status = 'pending', claimed_by = NULL, started_at = NULL, "
    "    verification_retries = ? "
    "WHERE id = ? AND status = 'in_progress'",
    (current_retries + 1, task["id"]),
)
```

The `AND status = 'in_progress'` predicate is load-bearing — same race fix
as the fanout re-queue (no resurrecting cancelled / failed tasks).

### Context surface on retry

When a task is re-queued, the next `build_context` call includes a failed-
checks block at the top of the prompt so the agent sees what to fix:

> "## Previous verification failed
> - build: {status: fail, details: ..., command: ...}
> - tests: {status: fail, details: ..., command: ...}
> Fix these and re-run `record_check` for each."

This keeps the fix loop self-contained to the agent; no reviewer task needs
to exist, no human-in-the-loop unless retries exhaust.

### Integration with existing gates

Ordering in `complete_and_handoff`:

1. Fanout gate (existing) — re-queue if missing children.
2. VR-child-exists gate (existing) — auto-create verifier task if needed.
3. **Verification-checks gate (new)** — re-queue on fail; merge-unverified on empty.
4. Duplicate-handoff check (existing).
5. `complete_task_with_output`.

VR and completion-checks solve different problems and can coexist on the
same task: VR asserts "a reviewer task exists"; completion-checks asserts
"the coder actually ran build/tests." A coder task with no completion
checks and with VR required will still auto-create a VR row, and the merge
itself will be tagged `merged_unverified`.

### Dashboard visibility

One metric card in the existing metrics view: **"Unverified merges: N in
last 24h"** with a click-through list. The `task.completed` WS event gains
`merge_status` and `completion_checks` fields so the per-task detail modal
renders the structured check block.

### Backwards compatibility

Existing tasks have `completion_checks = '{}'` after migration. They flow
through the gate, match the empty-checks branch, merge as
`merged_unverified`, and emit the event. No breaking behavior for in-flight
groups; existing dashboard code is unaffected by the new columns because it
reads tasks by explicit column list.

## Testing

- Unit tests for `record_check`: overwrite semantics, bad status enum
  rejected with 400, JSON shape preserved across multiple calls.
- Unit tests for `_requeue_for_verification`: status predicate prevents
  resurrection of cancelled tasks, retry counter increments correctly,
  exhaustion triggers escalation event.
- Integration test: full coder → record check-fail → auto re-queue →
  record check-pass → merge cycle.
- Integration test: no-checks-recorded → `merged_unverified` path.

## Out of scope for this spec

- An infrastructure-run verifier (option (c) in the brainstorm) that
  re-executes the checks instead of trusting the agent. The data model
  accommodates this as a later hardening; for now the user chose explicit
  trust.
- The `merge_task` MCP tool itself (per-group integrator lock, actual
  `git merge` call). Filed separately.
- LLM-as-judge semantic verification.
- Project-wide build detection.

## Decisions captured

- Trust the agent's recorded checks rather than re-running them in infra.
- Freeform check names (not an enum).
- Re-queue on fail with cap of 2 retries; escalate after (mirrors
  `fanout_retries`).
- Empty checks merge as `merged_unverified`, don't block (fail-open for
  new-user experience with durable signal).
- No new role; the gate + MCP tool handle everything the verifier role
  would have.
