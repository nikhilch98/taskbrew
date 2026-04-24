# VR Gate: Skip for Clean Checks

**Status:** Approved 2026-04-24
**Author:** Brainstormed with user through the brainstorming skill.

## Problem

The auto-VR gate in `complete_and_handoff` creates a verifier task
for every "substantial" implementation/bug_fix/revision diff. Two
issues with the current behaviour:

1. **Orphan VR tasks**: the gate doesn't check whether a verifier
   role actually exists in the deployment. A user who installed
   TaskBrew without configuring a verifier gets VR tasks assigned
   to a role that no agent ever claims -- they sit in `pending`
   forever with no signal to the user.
2. **Redundant VR runs on mid-sized clean changes**: after the
   per-task completion_checks feature (build/tests/lint recorded
   by the coder), the LLM verifier adds little signal on a 50-LOC
   diff whose `{build: pass, tests: pass, lint: pass}` already
   covers the obvious failure modes. Yet the gate still creates
   the VR, adding ~minutes of serial LLM time per task.

## Design

Two layered changes to `_should_require_verification`.

### (a) No-orphan VR

Early-return `False` when no verifier role exists in
`self.all_roles`. Log at DEBUG (most greenfield deployments never
had a verifier and this is their normal state). No event emitted:
the existing `task.unverified_merge` event from the
completion_checks gate already signals "this shipped without LLM
review."

```python
if not any(r.role == "verifier" for r in self.all_roles.values()):
    logger.debug(
        "Skipping VR auto-creation for %s: no verifier role configured",
        task["id"],
    )
    return False
```

### (b) Skip VR for mid-diff with clean checks

Two thresholds replace the single `_VR_DIFF_LOC_THRESHOLD = 20`:

- `_VR_DIFF_LOC_THRESHOLD = 20` — below this, never need VR
  (unchanged behaviour).
- `_VR_DIFF_LOC_HARD_CEILING = 200` — at or above this, always
  need VR regardless of checks (safety net for big changes).
- Between 20 and 199 LOC: skip VR **only** when
  `completion_checks` is non-empty **and** every recorded entry
  has `status` in `{pass, skipped}`. Empty checks or any failed
  check keep the VR as a safety net.

```python
if loc < self._VR_DIFF_LOC_THRESHOLD:
    return False
if loc >= self._VR_DIFF_LOC_HARD_CEILING:
    return True

checks = _parse_completion_checks(task)
all_clean = bool(checks) and all(
    c.get("status") in {"pass", "skipped"} for c in checks.values()
)
return not all_clean
```

### Behaviour matrix

| Diff LOC | Checks state       | Before | After                  |
|----------|--------------------|--------|------------------------|
| < 20     | any                | skip   | skip (unchanged)       |
| 20–199   | empty              | keep   | keep (unchanged)       |
| 20–199   | any fail           | keep   | keep (unchanged)       |
| 20–199   | all pass / skipped | keep   | **skip** ← optimisation |
| >= 200   | any                | keep   | keep (unchanged)       |

### Interaction with existing gates

- The completion_checks verification gate (`_requeue_for_verification`)
  runs *before* `_should_require_verification` returns. A task with
  any failing check has already been re-queued and the gate was
  never reached. So the "any fail" row in the matrix is reached
  only for tasks where failed checks exhausted retries and were
  escalated — we still want VR as a safety net in that case.
- If the auto-VR gate returns False, `complete_and_handoff` proceeds
  to `complete_task_with_output` as normal. The change of
  `merge_status` happens via the existing code path.

## Testing

Add to `tests/test_agent_loop.py`:

1. `test_vr_gate_skips_when_no_verifier_role_configured` —
   `all_roles` omits `verifier`, medium diff, clean checks — gate
   returns False.
2. `test_vr_gate_skips_mid_diff_with_clean_checks` — 50 LOC diff,
   `{build: pass, tests: pass}` — gate returns False.
3. `test_vr_gate_keeps_vr_for_empty_checks_at_mid_diff` — 50 LOC
   diff, `{}` — gate returns True.
4. `test_vr_gate_keeps_vr_for_big_diff_regardless_of_checks` —
   500 LOC diff, `{build: pass, tests: pass}` — gate returns True.
5. `test_vr_gate_skips_tiny_diff_regardless_of_checks` — 5 LOC
   diff — gate returns False (regression against the threshold).

## Rollout

- No schema change, no config change, no prompt change.
- Behaviour change is user-visible: tasks in the 20–199 LOC range
  with clean completion_checks no longer create an auto-VR.
  CHANGELOG entry covers it.
- Risk: coder's self-reported checks pass but the change is
  semantically wrong. The tiny-diff bucket (< 20 LOC) has been
  skipping VR since the gate was added with no reported issues;
  this extends the same trust bubble to the mid-range only when
  checks are clean. Big diffs (>= 200 LOC) always keep VR.

## Out of scope

- Running VR in parallel with the next coder task ("option 2b"
  from the earlier brainstorm). Different shape of change; deserves
  its own design conversation.
- Making the thresholds configurable per role or per project. If
  operators end up tuning them, we'll add config surface later.
- A "verifier role exists but is paused" detection path. Paused
  roles should be treated the same as missing for VR purposes; the
  current `all_roles` lookup will show the role, so the gate will
  still create the VR. If this becomes a real issue we'll add a
  paused-roles check to the gate.

## Decisions captured

- Verifier-role-exists check is a prerequisite to any VR creation.
- Two thresholds (20 floor, 200 ceiling). Between them, trust
  clean checks.
- Empty completion_checks keeps VR as a safety net (we don't
  merge-blind on zero signal in the mid-range).
- Semantic review still mandatory on 200+ LOC diffs regardless of
  checks.
- Log at DEBUG for missing-verifier case; no new event (existing
  `task.unverified_merge` is the durable signal).
