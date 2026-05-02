# Durable Merge Queue Design

## Problem

TaskBrew currently lets each verifier agent run brokered git integration
inline from `AgentLoop`. Parallel verifier approvals can mutate the primary
checkout at the same time with `git checkout main` and `git merge ...`. This
creates root `.git/index.lock` races, leaves approved branches unmerged, and
can still mark a group completed because group completion only checks task
terminal states. Even serialized root-checkout merges remain fragile when the
primary worktree has user edits, generated config changes, or inspection files.

The fix must be provider-neutral. Claude, Codex, and Gemini should all produce
and verify work on isolated branches while TaskBrew alone integrates approved
branches into the target branch.

## Goals

- Serialize all merges into a repository target branch.
- Keep merge execution out of the primary checkout.
- Survive TaskBrew restarts during queued or running merges.
- Retry transient git lock contention without creating revision tasks.
- Create coder revision tasks only for real merge conflicts.
- Prevent group completion while required integrations are queued, running,
  retrying, blocked, or conflicted.
- Keep the existing verifier approval semantics and task graph intact.

## Non-Goals

- Replacing git worktrees.
- Adding a human merge UI.
- Solving semantic over-decomposition by models.
- Changing provider-specific CLI behavior.

## Data Model

Add migration table `merge_queue`:

```sql
CREATE TABLE IF NOT EXISTS merge_queue (
  id TEXT PRIMARY KEY,
  group_id TEXT NOT NULL,
  parent_task_id TEXT NOT NULL,
  verifier_task_id TEXT NOT NULL,
  source_branch TEXT NOT NULL,
  target_branch TEXT NOT NULL DEFAULT 'main',
  status TEXT NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0,
  next_attempt_at TEXT,
  leased_by TEXT,
  leased_until TEXT,
  last_error TEXT,
  target_sha_before TEXT,
  target_sha_after TEXT,
  root_refresh_status TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  completed_at TEXT
);
```

Statuses are `queued`, `running`, `retry_pending`, `merged`,
`already_merged`, `conflict`, `root_refresh_blocked`, `blocked`, and
`failed`. `target_sha_before` and `target_sha_after` record the branch movement
for traceability. `root_refresh_status` records whether the visible project
checkout was updated after the branch merge.

Indexes:

```sql
CREATE UNIQUE INDEX IF NOT EXISTS uq_merge_queue_verifier
  ON merge_queue(verifier_task_id);
CREATE INDEX IF NOT EXISTS idx_merge_queue_group_status
  ON merge_queue(group_id, status);
CREATE INDEX IF NOT EXISTS idx_merge_queue_ready
  ON merge_queue(status, next_attempt_at, created_at);
```

The unique verifier index makes enqueue idempotent when a verifier completion
path is retried.

## Components

### MergeQueue

`taskbrew.orchestrator.merge_queue.MergeQueue` owns queue persistence:

- `enqueue(...)`: insert a `queued` row or return the existing row for the
  verifier task.
- `claim_ready(worker_id, lease_seconds)`: atomically lease the oldest row in
  `queued` or due `retry_pending` status.
- `complete(row_id, status, details)`: record terminal queue results.
- `retry(row_id, details, delay_seconds)`: schedule transient failures.
- `release_expired_leases()`: return stale `running` rows to `retry_pending`.
- `has_open_group_merges(group_id)`: true for `queued`, `running`,
  `retry_pending`, `blocked`, `conflict`, `root_refresh_blocked`, or `failed`.

### MergeBroker

`taskbrew.orchestrator.merge_broker.MergeBroker` is a single background
orchestrator task. It repeatedly:

1. Releases expired leases.
2. Cleans abandoned integration worktrees for rows that are no longer running.
3. Claims one ready merge row.
4. Acquires a durable repo integration lock.
5. Creates a temporary integration worktree from the target branch.
6. Merges the source branch into the target branch.
7. Updates the target branch ref with a compare-and-swap `git update-ref`
   when it has not moved.
8. Refreshes the primary checkout when it is on the target branch.
9. Cleans up the integration worktree.
10. Updates queue state, task `merge_status`, escalations, and events.
11. Calls the task board group-completion check for the parent task.

Only this broker may mutate target branch refs or refresh the primary checkout.
Agents never merge into `main`, and the broker does not run merge conflict
resolution inside the primary checkout.

## Agent Flow

Verifier approval no longer calls `_attempt_brokered_merge()` inline. Instead,
`AgentLoop._broker_verification_merge_if_needed()` validates that the verifier
output clearly approves, loads the parent task, and enqueues a merge request.
It returns `queued` so the verifier task can complete with
`merge_status = merge_queued`.

The parent task remains completed, but not integrated, until the broker updates
its `merge_status` to `merged` or `already_merged`.

## Git Integration Rules

The broker uses a repository lock file under `.taskbrew/locks/integration.lock`,
not `.git`, so TaskBrew can coordinate across processes without interfering
with git internals. If this lock is older than 120 seconds and its recorded
process is no longer alive, the broker removes it before acquiring a fresh
lock.

Before merging, the broker:

- verifies `source_branch` and `target_branch` exist;
- checks whether `source_branch` is already an ancestor of `target_branch`;
- records the current target branch commit as `expected_target_sha`;
- creates `.worktrees/.integration/<queue_id>` as a detached or temporary
  branch worktree from `expected_target_sha`;
- checks for an in-progress merge/rebase/cherry-pick inside that integration
  worktree.
- updates `refs/heads/<target_branch>` with
  `git update-ref refs/heads/<target_branch> <merged_sha> <expected_target_sha>`
  after a clean merge.

The broker never deletes the primary checkout's `.git/index.lock`. Root
checkout lock files are no longer part of the merge path. If an integration
worktree has a stale lock from a crashed broker, it is removed only when no live
git process owns it and the lock is older than 120 seconds. If the lock may be
active, the queue row becomes `retry_pending`.

Dirty primary checkout state does not block the branch integration because the
merge happens in the temporary integration worktree. It can block the final
root refresh if user edits cannot be preserved safely.

## Primary Checkout Refresh

Advancing `refs/heads/main` from an integration worktree is not enough by
itself: if the project root also has `main` checked out, its visible files can
remain stale until the checkout is refreshed. The broker therefore performs a
post-merge refresh step after the target ref update.

Refresh rules:

- If the primary checkout is not on `target_branch`, skip refresh and record
  `root_refresh_status = skipped_not_on_target`.
- If the primary checkout is clean, hard reset it to `target_sha_after`.
- If the primary checkout is dirty, create a temporary stash including
  untracked files but excluding `.worktrees/` and `.taskbrew/`, reset to
  `target_sha_after`, and re-apply the stash.
- If stash re-apply succeeds, record `root_refresh_status = refreshed_with_stash`.
- If stash re-apply conflicts or fails, record queue status
  `root_refresh_blocked`, create an escalation, and leave the group active.

The broker must never discard user edits. A failed root refresh means the
branch merge succeeded, but the project is not yet usable from the visible root
checkout, so the group remains incomplete.

Merge result handling:

- `merged`: update the target branch ref only if it still equals
  `expected_target_sha`, refresh the primary checkout, then update parent and
  verifier `merge_status = merged`.
- `already_merged`: refresh the primary checkout if needed, then update parent
  and verifier `merge_status = merged`.
- `conflict`: abort merge, set queue `conflict`, set verifier
  `merge_status = merge_conflict`, and create one coder revision task.
- target branch moved while merging: set `retry_pending` so the row reruns
  against the new target tip.
- root refresh failed after branch update: set `root_refresh_blocked`, create
  an escalation, and leave the group active.
- transient lock/process failures: set `retry_pending` with exponential
  backoff.
- missing branch: set `blocked`, create an escalation, and leave the group
  active.

## Group Completion

`TaskBoard._check_group_completion()` must consult `MergeQueue` before marking
a group complete. A group cannot complete when it has any merge queue row with
status `queued`, `running`, `retry_pending`, `blocked`, or `conflict`.
`root_refresh_blocked` and `failed` are also open for completion purposes.

This prevents a project from reaching `completed` while approved artifacts are
still only present in agent worktrees.

## Events and Trace

Emit:

- `task.merge_queued`
- `task.merge_started`
- `task.branch_merged`
- `task.merge_retry_pending`
- `task.merge_blocked`
- `task.merge_conflict`
- `task.merge_root_refresh_blocked`

The trace endpoint should include merge queue counts per group so benchmark
analysis can distinguish model behavior from integration failures.

## Testing

Unit tests:

- enqueue is idempotent per verifier task;
- ready rows are claimed FIFO;
- expired `running` leases return to `retry_pending`;
- group completion is blocked by open merge rows;
- group completion succeeds after merge rows become terminal success;
- group completion is blocked by `root_refresh_blocked`;
- verifier approval enqueues instead of merging inline;
- conflicts create exactly one coder revision task;
- lock contention schedules retry instead of final block.

Integration tests:

- two verifier approvals completed concurrently produce two queue rows and one
  serialized merge sequence;
- stale `.git/index.lock` is removed only when safe;
- active `.git/index.lock` results in retry;
- missing source branch blocks with escalation;
- dirty root checkout does not block branch integration;
- dirty root checkout user edits are stashed, refreshed, and restored when
  possible;
- stash re-apply conflicts leave the queue `root_refresh_blocked`;
- target branch moving during integration causes retry, not a lost update.

## Rollout

1. Add schema migration and queue model.
2. Add broker component and start/stop it with the orchestrator.
3. Change verifier approval to enqueue.
4. Change group completion to consult merge queue.
5. Update trace output.
6. Add tests for queue, broker, and group completion behavior.
