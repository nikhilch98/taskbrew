# Execution Tracing Endpoint

**Status:** Approved 2026-04-24
**Author:** Brainstormed with user through the brainstorming skill.

## Problem

After five rounds of pipeline optimisation (event-driven claims,
retry classification, VR gate, structured failure feedback,
worktree reuse) we are guessing at which bottleneck matters next
rather than measuring. Users have no way to see, for a given
feature, where the time and cost actually went:

- Which task took longest?
- Where did the $3 of LLM spend go?
- Did every implementation task get a verified merge, or did
  some merge_unverified?
- How many retries fired across the pipeline?

All the data exists (``tasks``, ``task_usage``, ``agent_instances``,
now the new ``completion_checks`` / ``merge_status`` /
``verification_retries`` columns). It just isn't exposed as a
structured trace.

## Design

Add one endpoint and one small dashboard panel.

### Endpoint: `GET /api/groups/{group_id}/trace`

Returns structured JSON for the whole feature's execution:

```json
{
  "group_id": "FEAT-001",
  "group_title": "Add user auth",
  "group_status": "completed",
  "created_at": "2026-04-24T10:00:00+00:00",
  "last_activity_at": "2026-04-24T10:45:00+00:00",
  "wall_clock_ms": 2_700_000,
  "total_cost_usd": 2.35,
  "total_input_tokens": 41_500,
  "total_output_tokens": 12_000,
  "total_num_turns": 47,
  "total_tasks": 6,
  "status_counts": {"completed": 5, "in_progress": 1},
  "merge_status_counts": {"merged": 4, "merged_unverified": 1, null: 1},
  "verification_retries_total": 2,
  "tasks": [
    {
      "id": "AR-001",
      "task_type": "tech_design",
      "assigned_to": "architect",
      "claimed_by": "architect-1",
      "title": "Design auth module",
      "parent_id": null,
      "revision_of": null,
      "branch_name": "feat/ar-001",
      "parent_branch": "main",
      "status": "completed",
      "merge_status": "merged",
      "requires_fanout": 1,
      "fanout_retries": 0,
      "verification_retries": 0,
      "created_at": "...",
      "started_at": "...",
      "completed_at": "...",
      "duration_ms": 180_000,
      "cost_usd": 0.50,
      "input_tokens": 5000,
      "output_tokens": 3000,
      "num_turns": 12,
      "duration_api_ms": 45_000,
      "completion_checks": {"build": {"status": "pass", ...}, ...},
      "children": ["CD-001", "CD-002"]
    },
    ...
  ]
}
```

**Data sources:**
- `tasks` (columns: id, group_id, parent_id, title, task_type,
  priority, assigned_to, claimed_by, status, merge_status,
  requires_fanout, fanout_retries, verification_retries,
  completion_checks, branch_name, parent_branch, revision_of,
  created_at, started_at, completed_at)
- `task_usage` LEFT JOIN on task_id (input_tokens, output_tokens,
  cost_usd, num_turns, duration_api_ms)
- `groups` for title, status, created_at

**Derived fields:**
- `duration_ms` = completed_at - started_at (or NULL if in-flight)
- `wall_clock_ms` = MAX(completed_at) - MIN(created_at) at the
  group level
- `total_*` aggregates summed from task_usage
- `children` populated by traversing `parent_id` in the result set

### Dashboard panel

Small addition to the existing group detail surface. A new
"Trace" tab next to whatever group views exist today, rendering:

- Header row: total wall-clock / total cost / total tokens /
  status counts
- Table of tasks sorted by `created_at`:
  - ID | Type | Role | Status | Merge status | Duration | Cost |
    Turns | Retries (fanout / verify) | Checks summary
  - Status cells coloured (green / red / amber per the existing
    palette)
- Clicking a row expands the `completion_checks` JSON.

Skip fancy DAG visualization — the existing `/graph` endpoint
already covers that. Trace is the tabular/timing complement.

### Access control

The trace endpoint mounts alongside other `/api/groups/{id}/*`
routes. Existing auth middleware gates it the same as the rest
of the tasks router (admin in default deployments; open if the
user explicitly relaxed auth).

### Pagination / size bound

A group with 500 tasks produces a response that is still under
1 MB (structured JSON, small per-task rows). Add a soft cap
at 1000 tasks per response: if the group has more, return the
first 1000 plus a `truncated: true` flag. Real-world groups
rarely exceed dozens of tasks.

## Testing

- Empty group (no tasks) returns an empty `tasks` array and
  zero aggregates.
- Single-task group: aggregates equal the single task's values.
- Fan-out group (architect + 3 coders): parent has the coders
  in `children`; coders have the architect as `parent_id`.
- `total_cost_usd` equals SUM across tasks.
- `status_counts` and `merge_status_counts` match the underlying
  task rows.
- 404 on unknown group_id.

## Out of scope

- Streaming updates (WS feed of trace events). The static GET is
  enough for the "where did this feature go" use case.
- Cross-group trace (e.g., compare two features). Separate
  feature; different UX.
- Historical trend charts ("tech_design tasks have gotten slower
  over the last week"). Different dashboard surface.
- Cost attribution to specific tool calls. ``task_usage`` only
  has the per-task rollup; finer breakdown would require SDK
  instrumentation we don't have.

## Decisions captured

- One endpoint, one panel. No streaming, no historical analysis
  in this round.
- All data computed on the fly; no caching. Response is small
  enough that the SQL + aggregation runs in <100ms even on
  realistic groups.
- Children relationships derived in-memory from the fetched
  `tasks` list rather than with a recursive CTE. Simpler, same
  result at realistic group sizes.
- Dashboard panel is tabular, not visual. The existing
  `/graph` endpoint covers the DAG view; trace is the timing /
  cost complement.
