# Backlog and Review System Gates Design

## Summary

TaskBrew should add two visible Kanban statuses, `Backlog` and `Review`, and route task
quality control through the per-project locked system agent. The design uses explicit async
system gates rather than scattered status special cases:

- a one-time backlog intake gate for every newly created task
- a review gate for completed tasks that the intake gate marked as needing review

The system agent remains analysis-only. It can classify whether a task needs review, explain
that decision, review completed work, reject invalid tasks, and create revision tasks when
completed work needs changes. It cannot rewrite normal task content, change its own prompt, or
receive ordinary team-routed work.

## Goals

- Add visible `Backlog` and `Review` columns to the task board.
- Ensure every newly created task enters `Backlog` first.
- Run backlog intake exactly once for each task record.
- Store visible `needs_review` and `needs_review_reason` data from backlog intake.
- Route only tasks with `needs_review=true` through `Review` before final completion.
- Keep `Review` limited to original tasks waiting on system-agent review gates.
- Let the system agent directly mark invalid tasks as `Rejected` with a visible reason.
- Automatically create normal visible revision tasks when review finds fixable issues.
- Keep the original task in `Review` and blocked by revision tasks until the gate passes.
- Make the workflow async, retryable, auditable, and efficient.

## Non-Goals

- Do not build a general-purpose policy engine in v1.
- Do not let the system agent edit arbitrary task fields during backlog intake.
- Do not use the review column for normal human reviewer tasks or verifier tasks.
- Do not hide revision tasks inside the original card only.
- Do not require PM approval before a high-confidence system rejection.
- Do not run a full repository scan during backlog intake by default.

## Status Model

The board should include these visible statuses:

- `backlog`
- `pending`
- `in_progress`
- `review`
- `blocked`
- `completed`
- `rejected`
- `failed`

`Backlog` is the default initial status for every task. The task also stores its intended next
status, usually `pending` or `blocked`, so the backlog gate can move it forward after intake.

`Review` is only for original tasks that require a system-agent review gate. Revision tasks
created by the system are normal task records and move through `Backlog`, `Pending`,
`In Progress`, and other ordinary columns.

## Backlog Intake Gate

When a task is created, TaskBrew should persist it with:

- `status=backlog`
- `intended_status=pending` or `blocked`
- `backlog_intake_status=pending`
- `needs_review` unset
- `needs_review_reason` unset

The system agent then processes backlog intake asynchronously. Intake runs once per task record.
Retries are allowed only when the intake job itself fails before storing a decision.

During backlog intake, the system agent may set only:

- `needs_review`
- `needs_review_reason`
- supporting audit metadata for that decision
- `backlog_intake_status`

It must not rewrite the task title, description, acceptance criteria, dependencies, assignee, or
intended state.

After a successful intake decision, TaskBrew moves the task to `intended_status`. If the task was
created with dependencies, `intended_status` should be `blocked`; otherwise it should usually be
`pending`.

## Needs Review Decision Data

The review decision should be visible in task details. The Kanban card can show a compact
`needs_review` tag only when the value is true.

Store the decision as structured data, represented in the UI as readable text:

```json
{
  "decision": true,
  "reason": "Touches shared task state transitions and completion routing.",
  "signals": ["shared_orchestration_logic", "cross_module_change"],
  "confidence": "medium",
  "decided_by": "system_agent",
  "decided_at": "2026-05-05T00:00:00Z"
}
```

`needs_review_reason` should remain useful even if the structured representation changes later.
The reason text should explain why the task does or does not need review.

## Intake Context

Backlog intake should use a compact context bundle for speed and stability. The gate should not
load broad project context until it needs more information.

Always include:

- task title, description, acceptance criteria, and intended next state
- creator, intended assignee, role, provider/model metadata when available
- task category or type when present
- dependencies and dependent tasks
- project system-agent settings and review policy
- nearby task board context such as parent task, epic, or similar active tasks

Include when available and relevant:

- files, modules, tools, PRs, commits, logs, or artifacts directly mentioned by the task
- linked previous tasks or conversations
- recent history for the intended assignee in the same area
- signals from known flaky, security-sensitive, migration, deployment, data, auth, or payment areas

Do not include by default:

- full repository scans
- large file contents
- private agent scratchpads
- unrelated task histories
- prompt-editing authority or task-rewriting authority

The decision process should be tiered:

1. Use metadata-only context for obvious low-risk or high-risk tasks.
2. Fetch focused referenced context when the metadata is insufficient.
3. If uncertainty remains, prefer `needs_review=true`.

## Review Gate

When a task attempts to complete:

- if `needs_review=false`, it moves directly to its requested final state
- if `needs_review=true`, it moves to `review` instead of `completed`

The review gate is processed asynchronously by the system agent. It may inspect whatever context is
needed to judge the completed work, including:

- original task data and acceptance criteria
- backlog intake reason and signals
- completion output and artifacts
- changed files or relevant repository state
- tests, logs, command output, screenshots, generated files, and review history
- linked dependencies, revision tasks, and previous review rounds

The system agent is still analysis-based. It reviews and creates follow-up work, but does not patch
the implementation itself.

## Review Outcomes

The review gate should produce one of these outcomes:

- `approved`: move the original task from `review` to `completed`
- `needs_revision`: keep the original in `review`, create revision tasks, and block the original
- `rejected`: move the original to `rejected` and store visible `rejection_reason`
- `failed_review`: keep the original reviewable and retryable because the review job failed

`Rejected` should be used sparingly. It means the task should not continue as requested, not that
the delivered work merely needs fixes. Valid rejection cases include:

- invalid, impossible, incoherent, or obsolete tasks
- duplicate or superseded work
- task shape that is fundamentally wrong for the workflow
- requests that contradict project constraints or locked system-agent boundaries
- unsafe or destructive requests
- delivered work that solved the wrong problem so completely that a new task is needed

Fixable quality issues should produce `needs_revision`, not `rejected`.

## Revision Tasks

When review returns `needs_revision`, TaskBrew should automatically create one or more revision
tasks. Revision tasks are ordinary visible Kanban cards. They should:

- start in `Backlog`
- go through backlog intake like any new task
- reference the original task as their review parent
- include the specific review finding they address
- be assigned intelligently based on finding type, touched area, original assignee, role ownership,
  workload, and project/team settings
- block the original task until completed

The original task remains in `Review` and records `blocked_by` references to open revision tasks.
When all blocking revision tasks finish, the system review gate runs again for the original task.

To avoid infinite loops, each original task should track review rounds. A default
`max_review_rounds` of 3 is recommended. In v1, when the limit is reached, the system agent should
mark the original task as `rejected` with a visible `rejection_reason` that explains that the task
failed to pass review after the allowed revision rounds. A later policy engine can add human
escalation if that proves useful.

## System Agent Role

This workflow depends on the per-project locked system agent profile. The system agent should be:

- visible in project team settings
- configured with provider, model, reasoning/thinking level, autoscaling, max instances, and idle
  timeout settings
- backed by a locked read-only system prompt
- excluded from normal team task routing
- invoked only by TaskBrew-owned system work types such as backlog intake and review gates

The system agent's work queue should be separate from normal role queues so system gates do not
appear as assignable team-member tasks.

## Data Model

Task records need fields for workflow, decisions, and auditability:

- `status`
- `intended_status`
- `needs_review`
- `needs_review_reason`
- `needs_review_decision`
- `backlog_intake_status`
- `backlog_intake_processed_at`
- `review_status`
- `review_round`
- `max_review_rounds`
- `review_parent_task_id`
- `revision_task_ids`
- `rejection_reason`
- `system_gate_runs`

`needs_review_reason` and `rejection_reason` should be visible in task details. `system_gate_runs`
can hold detailed audit metadata such as prompt version, system agent config, started/finished
timestamps, outcome, retry count, and error summaries.

The implementation can map these fields onto the existing persistence layer in the smallest safe
way, but the domain model should preserve the concepts above.

## API and Dashboard

API responses should expose:

- the new statuses
- `needs_review`
- visible `needs_review_reason`
- visible `rejection_reason`
- review round and blocking revision references

The dashboard should:

- show `Backlog` and `Review` as normal Kanban columns
- keep `Review` limited to original review-gate tasks
- show a compact `needs_review` tag on cards when true
- show the full system decision in task details
- show revision tasks as normal cards
- make the system agent configurable in project team settings as a locked role

## Efficiency and Stability

The gates should be designed for predictable load:

- queue backlog intake and review work asynchronously
- make gate handlers idempotent
- use retries for transient system-agent, CLI, or context-loading failures
- store decisions so backlog intake is not repeated for the same task
- use tiered context loading for intake
- reserve deeper code/artifact analysis for review gates
- cap review rounds
- record visible reasons for decisions and rejections

This keeps the system useful without creating a full policy engine or repeatedly re-analyzing the
same task.

## Testing

Add focused tests for:

- task creation starts in `backlog` with correct `intended_status`
- backlog intake runs once and stores `needs_review` plus `needs_review_reason`
- backlog intake moves tasks to `pending` or `blocked` after decision
- dependency resolution still moves blocked tasks correctly after backlog intake
- completion routes `needs_review=true` tasks to `review`
- completion routes `needs_review=false` tasks directly to final status
- review approval moves a task to `completed`
- review rejection stores `rejection_reason` and moves to `rejected`
- review revisions create normal visible backlog tasks and block the original
- original review task reruns review after revision tasks complete
- review round limits prevent infinite revision loops
- dashboard board data includes `Backlog` and `Review`
- task details expose visible review decision and rejection reasons
- system agent cannot be selected for normal team-routed work

Browser verification should cover:

- `Backlog` and `Review` columns render in the board
- new tasks appear in `Backlog` before intake completes
- tasks marked `needs_review` display a visible tag and detail reason
- completed flagged tasks move to `Review`
- revision tasks appear as normal cards
- system agent settings remain visible and locked in team settings

## Migration

Existing tasks should keep their current status. They should not be moved into `Backlog` during
migration. New tasks created after this feature ships should use the backlog intake flow.

Existing projects should get default system-agent gate settings from the per-project system agent
profile. No destructive migration is required.
