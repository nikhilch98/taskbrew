# Adaptive Work Package Review Design

**Status:** Approved for specification on 2026-05-06
**Author:** Brainstormed with the user through the brainstorming skill.

## Summary

TaskBrew should move from task-level review by default to adaptive review units built around
first-class Work Packages. A user goal can be tiny, small, medium, or large. Reviewing every
individual task bloats the board and slows delivery, while waiting until a whole large goal is
finished finds problems too late. The right default is to review coherent deliverable slices.

The new planning hierarchy is:

```text
Project
  Goal / Initiative
    Milestone
      Work Package
        Task
```

Existing `groups` continue to represent the top-level Goal or Initiative. Milestones are optional
and used for large work. Work Packages are always first-class and visible in the dashboard. Tasks
remain execution units inside a Work Package.

## Goals

- Make Work Package a first-class visible planning, execution, and review unit.
- Let TaskBrew handle tiny, small, medium, and large goals without one rigid review policy.
- Reduce review-column bloat by reviewing packages or milestones instead of every task.
- Preserve task-level self-verification through recorded checks.
- Keep task-level review available as a high-risk override, not the default.
- Review large goals incrementally at package or milestone boundaries.
- Keep the system agent analysis-only: it can classify, review, and create revision work, but does
  not patch implementation itself.
- Give the dashboard clear visibility into package progress, active review gates, blocked state,
  branch/integration state, and revision loops.

## Non-Goals

- Do not remove individual tasks or the existing task board data model.
- Do not require every large goal to be fully planned before the first Work Package starts.
- Do not force all teams to use Milestones for small goals.
- Do not make human reviewer or verifier roles mandatory.
- Do not hide revision tasks inside a package-only timeline.
- Do not let the system agent edit normal task specs or its locked prompt.

## Domain Model

### Goal / Initiative

A Goal is the top-level user request and maps to the existing `groups` table. A Goal owns the
original PRD, decomposition strategy, final acceptance criteria, and final completion status.

For compatibility, existing groups can be treated as Goals without changing their identifiers.

### Milestone

A Milestone is an optional planning and review boundary for large goals. It represents a shippable
or internally coherent phase, such as "Authentication foundations" or "Billing MVP".

Milestones are useful when:

- the goal would take many Work Packages
- the user can benefit from partial progress
- later packages depend on earlier architecture or integration decisions
- reviewing only at final goal completion would be too late

Small and medium goals may skip Milestones entirely.

### Work Package

A Work Package is the default unit of execution and review. It should represent a coherent
deliverable slice, not a single implementation step.

Good Work Packages:

- "Implement auth onboarding flow"
- "Wire dashboard live updates"
- "Add CLI provider model settings"
- "Create billing plan CRUD"

Poor Work Packages:

- "Edit one CSS file"
- "Run tests"
- "Implement the entire SaaS platform"

A Work Package owns:

- package title and spec
- acceptance criteria
- parent Goal and optional Milestone
- risk level and review policy
- task list
- dependencies on other packages
- package branch or integration branch metadata when available
- package-level review gate state
- package revision history

### Task

A Task remains the smallest assignable execution unit. Tasks should be created inside a Work
Package and continue to use existing task statuses. A task is responsible for doing work,
recording checks, and producing output. By default, task completion feeds package progress; it
does not create a system review gate unless the system explicitly chose task-level review.

## Adaptive Review Scope

Replace the task-only `needs_review` mindset with an explicit review scope:

```text
review_scope = none | task | work_package | milestone | goal
```

Each reviewable entity should also store:

```text
review_reason
review_policy
review_gate_id
review_round
max_review_rounds
review_status
```

`needs_review` can remain as a backward-compatible task-level indicator, but new workflow logic
should prefer `review_scope`.

## Review Policy

The system agent chooses review granularity during goal/package intake. The default policy should
be:

```text
tiny goal   -> no review or task review for risky work
small goal  -> one Work Package review
medium goal -> Work Package reviews
large goal  -> Milestone reviews plus selective Work Package reviews
```

Task-level review is reserved for high-risk exceptions:

- auth, permissions, payments, data deletion, migrations, deployment, or security-sensitive work
- cross-cutting orchestration or state-machine changes
- tasks with failed or missing self-checks
- tasks that touch shared infrastructure in a way the package review may not isolate well
- tasks whose output cannot be safely judged from package-level context alone

No-review is valid for low-risk work:

- docs-only updates
- trivial config text changes
- exploratory analysis tasks with no durable project change
- package-internal helper tasks that are covered by a stronger package review

## Workflow

### Goal Intake

When a user creates a goal, the system agent performs lightweight intake and classifies:

- goal size: `tiny`, `small`, `medium`, or `large`
- likely decomposition shape
- whether Milestones are needed
- default review scope
- obvious invalid, duplicate, unsafe, or obsolete requests

The system agent stores a visible decision reason. It does not rewrite the user's goal.

### Planning

PM creates the PRD and initial decomposition. Architect turns the PRD into Work Packages and,
for large goals, Milestones.

The default decomposition target should be:

- tiny: one task or one minimal Work Package
- small: one Work Package with a handful of tasks
- medium: two to six Work Packages
- large: multiple Milestones, each containing focused Work Packages

Architect and PM prompts should prefer Work Package boundaries that can be reviewed independently.

### Execution

Tasks inside a package run normally:

- claim work
- use a task branch or package branch strategy
- implement changes
- run relevant build, test, lint, or manual checks
- record checks through `record_check`
- complete with concise output

Task failures and blocked dependencies still behave like normal task-board states.

### Package Readiness

A Work Package becomes ready for review when:

- all required tasks are terminal
- required dependencies are resolved
- all package branches or task branches needed for review are integrated into the package review
  target, when branch metadata is available
- no package-blocking merge queue entry remains open
- required task checks are passing or explicitly skipped with reasons

If a task is terminal but has failed checks, the package should not advance to review until the
task retry/escalation policy resolves that failure.

### Review Gate

The Review column should show review gates at the chosen scope:

```text
Review
  WP-004 Review: Dashboard live update reliability
  MS-002 Review: Billing milestone
```

It should not show every package task unless the system deliberately chose task-level review.

The system agent reviews the active entity against:

- original goal and PRD
- milestone spec, if present
- Work Package spec and acceptance criteria
- task outputs and artifacts
- recorded checks
- changed files and diff summaries
- branch/integration state
- dependency and revision history
- relevant user decisions or agent questions

The review result is one of:

- `approved`: mark the package, milestone, task, or goal review as complete
- `needs_revision`: create targeted revision tasks or packages
- `rejected`: reject the entity only when it should not continue as requested
- `failed_review`: retryable system-review failure

### Revision Flow

For a Work Package review failure, revision tasks are created inside the same Work Package. The
package stays in Review or `waiting_revision` until those tasks complete and the review reruns.

For a Milestone review failure, the system may create:

- revision tasks inside affected packages
- a new Work Package if the missing work is substantial
- a package dependency adjustment if order or integration is wrong

For a Goal review failure, the system should prefer creating a new Work Package or Milestone-level
revision rather than scattering unrelated task revisions.

Review rounds must be capped. When a gate reaches `max_review_rounds`, TaskBrew should reject or
escalate the reviewable entity with a visible reason instead of looping.

## Branch and Integration Strategy

The ideal branch model is package-aware:

- each Work Package can have a package integration branch
- task branches can merge into the package branch
- package review inspects the integrated package diff
- approved package branches merge into the target branch or next milestone integration branch

This is more accurate than reviewing isolated task branches because it catches integration gaps
inside the deliverable slice.

For the first implementation, TaskBrew can preserve the existing task-branch behavior and review
package context from task branches, outputs, and artifacts. The package integration branch can be
introduced incrementally as the merge broker grows package awareness.

## Dashboard

The dashboard should make Work Packages first-class.

Default board view:

- Kanban columns show Work Package cards by default.
- Tasks are visible inside a package drawer or detail pane.
- A task-level board can remain available as a secondary view.
- Large goals can show Milestone swimlanes or filters.

Work Package cards should show:

- package id, title, status, and owner
- parent Goal and optional Milestone
- progress count: total, completed, running, blocked, failed
- active agent count for tasks inside the package
- review scope and review status
- review round and latest review reason
- branch or integration state when available
- blocked-by package/task references
- risk level

Review gate cards should make system-agent activity visible:

- queued, running, waiting for revisions, approved, failed, or rejected
- current review round
- latest system decision summary
- revision tasks created by the gate

## API and Persistence

Add first-class persistence for Work Packages and optional Milestones. A practical schema shape is:

```text
milestones(id, group_id, title, description, status, review_scope, review_status, ...)
work_packages(id, group_id, milestone_id, title, description, status, risk_level,
              review_scope, review_status, review_reason, branch_name, ...)
tasks(..., work_package_id, milestone_id, review_scope, ...)
review_gates(id, entity_type, entity_id, status, outcome, round, max_rounds, ...)
```

Existing `tasks.group_id` remains required for initiative-level filtering. Existing tasks without
a Work Package can be assigned to a generated default package per group during migration or when
first rendered.

API responses should support:

- package board data
- package detail with nested tasks
- package review gate status
- milestone summary and package list
- task board compatibility for users who still want raw tasks

## System Agent Context

System review should use tiered context for speed:

1. package metadata, specs, task summaries, and recorded checks
2. focused diffs and artifacts for changed areas
3. selected file contents only when risk or uncertainty requires it
4. broader repository scans only for high-risk package or milestone reviews

The system agent should emit concise, structured review decisions. Long analysis can be stored in
review gate audit data, while dashboard cards show a compact summary.

## Migration

Existing projects can migrate safely:

- existing groups become Goals
- each group gets a default Work Package if it has tasks without one
- existing task-level `needs_review=true` maps to `review_scope=task`
- tasks without `needs_review=true` inherit the parent Work Package review policy
- the current Review column continues to work while package gates are introduced

New projects should default to Work Package review for non-trivial implementation goals.

## Testing

Add focused tests for:

- goal intake classification chooses expected review scopes for tiny, small, medium, and large goals
- Work Package creation and nested task creation
- package status derives from child task states
- package review starts only after required tasks and dependencies are resolved
- task-level review still works for high-risk overrides
- Review column includes package/milestone gates and excludes covered child tasks
- revision tasks stay attached to the package being reviewed
- max review rounds prevent infinite loops
- migration creates default packages for existing task groups
- dashboard API returns package cards with progress and review metadata

Browser verification should cover:

- package-first Kanban board
- package detail drawer with nested tasks
- review gate visibility while system review is queued and running
- automatic board updates when package review finishes
- task-board compatibility view

## Open Implementation Notes

This design intentionally changes the default review boundary, but it does not require deleting
the current task-level review implementation. The safe implementation path is to add Work Packages
and package review gates alongside the current fields, then move default scaffolding and system
agent policy toward package review once the package board and review flow are stable.
