# Worktree Reuse Across Tasks

**Status:** Approved 2026-04-24
**Author:** Brainstormed with user through the brainstorming skill.

## Problem

Every task's `run_once` creates a git worktree at claim time and
destroys it in its `finally` block. A fresh worktree has no
untracked-but-ignored state (``node_modules``, ``.venv``,
``target/``, ``.next/``) because those are in ``.gitignore`` and
git's worktree mechanism only populates tracked files.

Result: the coder's first Bash command on every task pays the full
dependency-install cost. On a typical JS repo that's 30 s-5 min of
``npm install`` per task, per agent, forever. The dep cache never
survives a task boundary.

## Non-goals

- **Project-specific build detection.** The user explicitly scoped
  out "detect npm / pyproject / Cargo and run the right install"
  during the completion_checks brainstorm. We are not going to
  teach the system what install command to run.
- **Dep sharing across agents.** Different AgentLoops (e.g.,
  ``coder-1`` vs ``coder-2``) still keep separate worktrees. A
  single-install-per-deployment scheme would need symlinks or
  pnpm workspaces and is out of scope.
- **Parallel-task isolation within one agent.** An AgentLoop
  processes tasks serially (one at a time). Worktree reuse is
  safe because there's no overlap.

## Design

Stop destroying the worktree between tasks on the same agent.
The ``AgentLoop`` keeps it alive for the agent's whole lifetime;
between tasks, ``WorktreeManager`` switches branches inside the
live worktree after a narrow-scoped scrub that preserves ignored
files.

### A) Reuse path in `WorktreeManager._create_worktree_locked`

Current behaviour: if ``worktree_path_obj.exists()``, remove it
first and rebuild. That destroys the cache.

New behaviour: if the path exists **and** ``_worktrees[agent_name]``
matches that path ("it's our own, not a stranger's"), switch
branches in place:

```
git reset --hard HEAD            # discard any uncommitted tracked changes
git clean -fd                    # remove unignored untracked; PRESERVES ignored
git checkout <base_branch>       # parent_branch, or 'main' fallback
git checkout -B <new_branch>     # create-or-reset new branch
```

The critical flag: ``git clean -fd`` **without** ``-x`` preserves
ignored files. Compare to the existing retry loop, which uses
``git clean -fdx`` with ``-x`` because retries want a pristine
state inside the same branch.

If the path exists but is **not** ours (stale worktree from a
restart / crashed prior agent), fall through to the current
tear-down-rebuild logic. Same-branch re-invocation is a no-op.

### B) Move cleanup from per-task to per-agent shutdown

`agent_loop.py:run_once()` currently calls `cleanup_worktree` in
its outer `finally`. Remove that call. Keep `cleanup_worktree` in
`run()`'s outer `finally` so the worktree is destroyed when the
agent stops, not when a task completes.

Crash recovery: if the agent process dies mid-task, the worktree
survives on disk. Next boot: the `prune_stale` call at
`start_agents` time cleans up orphaned worktrees whose agents are
no longer in the in-memory dict. That logic already exists; we
just stop fighting it.

## Behaviour matrix

| Event | Before | After |
|-------|--------|-------|
| Agent start | no worktree | no worktree |
| Task 1 claim | create worktree (fast) | create worktree (fast) |
| Task 1 first Bash command | full `npm install` | full `npm install` |
| Task 1 completes | **destroy worktree** | keep worktree |
| Task 2 claim | create worktree (fast) | **switch branches in place** |
| Task 2 first Bash command | **full `npm install` again** | already installed, ~0s |
| Task N completes | destroy | keep |
| Agent stops | (no extra work) | destroy worktree |
| Crash mid-task | (orphaned worktree, pruned on next boot) | (same) |

## Risk

Ignored files leak cross-task state. If task 1 wrote to a
gitignored config file (``.local/override.yaml``), task 2 inherits
it. In 99% of cases this is the point — the whole goal is
preservation. The remaining 1% (e.g., a test run that
permanently corrupts an SQLite dev DB at a gitignored path)
could cause subtle inter-task bugs.

Mitigations:
- DEBUG log on each reuse: ``"Reusing worktree for agent X: prior
  untracked-ignored state preserved"`` so operators can correlate.
- If reuse proves painful in practice, operators can force-clean
  by calling `cleanup_worktree` from a maintenance endpoint or
  restarting the agent.

## Testing

Add to `tests/test_worktree_manager.py` (new file or existing):

1. `test_worktree_reuse_preserves_ignored_files` — create worktree
   for agent X on branch A, drop a file matching .gitignore
   (e.g. `node_modules/foo`), call create_worktree for the same
   agent on branch B, assert the file still exists.
2. `test_worktree_reuse_resets_tracked_uncommitted` — modify a
   tracked file without committing, call create_worktree for a
   new branch, assert the modification is gone.
3. `test_worktree_reuse_same_branch_is_noop` — create twice with
   the same branch, assert the path is stable and no destructive
   git ops fire.
4. `test_worktree_separate_agents_get_separate_worktrees` —
   regression: `create_worktree(X, branch_a)` and
   `create_worktree(Y, branch_b)` return different paths.
5. `test_cleanup_removes_reused_worktree` — after
   `cleanup_worktree`, the path no longer exists.

Add / modify in `tests/test_agent_loop.py`:

6. `test_run_once_no_longer_cleans_up_worktree_per_task` — mock
   `cleanup_worktree` to count calls, run two tasks on the same
   loop, assert cleanup was never called.
7. `test_agent_run_stop_triggers_cleanup` — start `run()`, stop,
   assert `cleanup_worktree` was called exactly once.

## Rollout

- No schema change, no config change, no prompt change.
- Behaviour change is user-visible on two axes:
  1. **Disk usage**: worktrees persist across tasks. For a typical
     repo with a few GB of node_modules, this means the worktree
     directory stays at that size until agent shutdown. Operators
     who rely on "worktree cleaned between tasks" as a disk-usage
     throttle need to know.
  2. **Cross-task ignored-file persistence** (the 1% case above).
- CHANGELOG entry covers both.

## Decisions captured

- Worktree survives across tasks; destroyed only at agent stop.
- `git clean -fd` (no `-x`) between tasks preserves ignored files.
- Retries inside a task still use `git clean -fdx` (unchanged).
- No dep-install plumbing. Agents that need deps run their own
  install; the benefit is that install survives to the next task.
- Stale worktrees from crashes are handled by the existing
  `prune_stale` at startup.
