# Structured Failure Feedback on Retries

**Status:** Approved 2026-04-24
**Author:** Brainstormed with user through the brainstorming skill.

## Problem

When a task with failing completion checks is re-queued by the
verification gate, the retry agent gets a fixed-shape context
block: `details`, `command`, check status. Missing:

- Which files the prior attempt touched (agent has to re-discover
  via diff).
- The full stderr / test output (a 500-line pytest dump won't fit
  in a `details` string without losing signal).
- A "read these first" nudge pointing at relevant files.

Result: the retry burns extra tool calls re-exploring the problem
space before converging on the fix.

## Design

**Two additions, no schema change:**

### 1. record_check accepts `artifact_paths`

Optional list of paths (relative to project_dir) where the agent
saved full output — test logs, stderr dumps, ruff reports, etc.

Stored in `completion_checks[check_name]["artifact_paths"]`
alongside the existing `status` / `details` / `duration_ms` /
`command` fields. The completion_checks JSON blob is already
freeform so no schema migration is needed.

Agents populate at their discretion; absent is fine (back-compat
with all existing record_check callers).

Validation at write time: `artifact_paths` must be a list of
non-empty strings, each ≤ 2 KiB. Reject anything else with 400.

### 2. Retry context block gains two sections

In `build_context`, the existing `## Previous verification failed`
block is already rendered when `verification_retries > 0`. Two
additions:

**Per-check**: if `artifact_paths` is present, render each as a
`Full output at: <path>` line so the retry agent has a pointer
to the raw log.

**Overall**: emit a new `### Files you previously modified` line
based on `git diff --name-only <parent_branch>...HEAD` in the
worktree. Skips silently if no worktree exists (rare but possible
for non-Bash roles). Path list truncated to first 30 entries to
keep prompts bounded.

**Closing nudge**: a final line after the block:
`"Read these files and any linked artifacts before attempting the fix."`

### 3. Prompt addendum

One paragraph added to `config/roles/coder.yaml` and each of the
six `config/presets/coder_*.yaml` files:

> "When a check fails, save the full stderr / log to
> `artifacts/<task_id>-<check_name>.log` and pass that path as
> `artifact_paths` when you call `record_check`. Your retry will
> see the actual output instead of a summary."

Agents already have `Write` and `Bash` tools; saving the output
to an artifact file is one extra command.

## Behaviour matrix

| Situation | Before | After |
|-----------|--------|-------|
| First attempt (no retries) | unchanged | unchanged |
| Retry, terse details only | sees "tests failed" | sees details + files-modified list |
| Retry, rich details + artifact_paths | sees details | sees details + artifact pointers + files-modified |
| Retry, no worktree | details only | details only (files-modified silently skipped) |

## Testing

Add to `tests/test_mcp_record_check.py`:

- `record_check` accepts `artifact_paths` list and persists it.
- `record_check` rejects non-string entries / entries over 2 KiB.

Add to `tests/test_agent_loop.py`:

- `build_context` on a first attempt (verification_retries=0) does
  not emit the failure block or files-modified line.
- `build_context` on a retry with `artifact_paths` includes
  `Full output at: <path>` lines.
- `build_context` on a retry with a worktree includes
  `### Files you previously modified` with at least one path.
- `build_context` on a retry without a worktree omits the
  files-modified section gracefully.

## Rollout

- No schema change, no config change.
- Prompt addendum is a text edit in seven YAML files.
- Behaviour change is user-visible: retry prompts become larger
  (more tokens per retry). On a typical 50-LOC task with 5 modified
  files this adds ~200 tokens to the retry prompt. Net effect on
  cost is negative — shorter retry loops (fewer exploratory
  tool calls) dominate the prompt-size increase.
- Risk: `git diff --name-only` failing (no worktree, dirty HEAD,
  detached head) — handled by silent omission of the
  files-modified section.

## Out of scope

- Automatic capture of stderr to artifact files (would require
  wrapping Bash tool invocations). Agents control this explicitly
  via their own Write calls.
- Diff-aware prompt summarisation ("you previously changed X from
  A to B"). The retry agent can Read the files; we don't need to
  pre-chew the diff.
- Retry-attempt-delta rendering ("on attempt 1 you tried X, got
  error Y"). Separate feature; requires us to persist prior outputs
  per attempt, which we don't today.

## Decisions captured

- `artifact_paths` is optional and freeform (list of paths, not
  an enum of categories). Agents decide what's worth saving.
- Files-modified list comes from git diff at build_context time,
  not from agent declaration. Git is authoritative; agent-declared
  file lists drift.
- 30-path cap on files-modified keeps the prompt bounded without
  truncating meaningful signal on normal-sized tasks.
- Artifact paths are rendered as pointers (agent Reads them on
  demand), not embedded as content, to avoid prompt bloat on
  large logs.
