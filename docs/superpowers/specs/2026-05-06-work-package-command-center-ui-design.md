# Work Package Command Center UI Design

## Context

TaskBrew now has first-class Work Packages, package-level review gates, system-agent
analysis, and a minimal package board. The current dashboard can render Work Package cards,
but the UI is not yet a complete operational surface for package review. Users still need
clear visibility into package health, review queue progress, system-agent activity, blockers,
revision work, and review decisions without refreshing or drilling through raw task data.

This spec defines the next UI layer: a first-class Work Package command center.

## Goals

- Make Work Packages the default project dashboard surface.
- Keep the dashboard action-oriented: show what is running, blocked, queued, stale, or waiting
  for revision.
- Make system-agent package review visible before, during, and after review.
- Preserve the existing task board and task detail workflows.
- Give packages a quick operational drawer and a deeper audit/review page.
- Avoid another per-task review bloat problem by keeping package review gates visually distinct
  from ordinary task cards.

## Non-Goals

- Do not redesign the whole TaskBrew dashboard shell.
- Do not remove task-level review support.
- Do not require package integration branches before this UI can ship.
- Do not build full milestone planning UI in this slice.
- Do not make package cards draggable until package movement semantics are defined.

## Recommended Approach

Build a Package Command Center, not just a board polish pass and not a full portfolio control
plane. The command center should be an ops-first dashboard with a package board at the center,
a compact action rail on the right, and a package drawer for quick drilldown. A full package
page should exist for deep review and audit history.

This is the best balance because it directly addresses the current pain points: review
visibility, flow speed, stalled packages, and system-agent observability. It also stays scoped
enough to implement incrementally.

## Information Architecture

The project dashboard should have three visible layers.

### 1. Command Summary

The top of the project dashboard should show compact project health:

- active packages
- blocked packages
- queued/running review gates
- packages waiting on revision
- failed or rejected packages
- system-agent status
- oldest waiting package or gate
- stale gate count

This summary should be dense, scan-friendly, and operational. It should not become a marketing
hero or decorative analytics area.

### 2. Work Package Board

The main board should default to Package mode. It should show Work Package cards grouped by:

- Backlog
- Pending
- In Progress
- Review
- Blocked
- Completed
- Rejected
- Failed

Task Board mode remains available through a segmented control: `Packages | Tasks`.

Package cards should show:

- package id
- title
- status
- risk
- review status
- task completion progress
- blocker or stale indicator when applicable
- active review/agent indicator when applicable
- group id
- age or waiting time when useful

The Review column should contain system-agent review gates only. In the default flow those gates
represent package or milestone review. Ordinary child tasks should not appear there unless the
system explicitly chooses a task-level review gate.

### 3. Combined Action Rail

The right rail should answer: “what needs attention right now?”

It should combine:

- system-agent state
- active review gate
- queued package reviews
- blocked packages
- revision-needed packages
- failed child tasks or failed checks
- stale backlog/review gates
- recent system decisions

This rail should stay compact and should link into the relevant package drawer or full package
page. It should not duplicate the entire board.

## Package Drawer

Clicking a package card should open a right-side drawer without leaving the board. The drawer
is the daily operational detail surface.

The drawer should use an “attention plus tabs” layout:

- Header
- Summary chips
- Sticky Needs Attention panel
- Tabs for detail sections
- Link to full package page

### Drawer Header

The header should show:

- package id
- title
- status
- risk level
- review status
- owner or creator if available
- `Open full page` action

### Sticky Needs Attention Panel

This panel should be visible above the tabs whenever there is something important:

- blocked by package or task dependency
- failed child task
- missing checks or artifacts
- review gate queued too long
- review gate running too long
- package waiting on revision
- system review failed
- revision tasks not completed

If nothing needs attention, the panel can collapse into a quiet “No active issues” state.

### Drawer Tabs

The drawer should have four tabs.

**Tasks**

- child tasks grouped by status
- assignee and claimed agent
- blockers
- review scope
- revision relationship
- quick link to task detail

**Review**

- current review gate state
- review round
- latest review reason
- review outcome
- revision tasks created by review
- review attempts
- system-agent current activity if running

**Artifacts**

- task outputs
- changed files when available
- check results
- logs or artifact links
- missing expected artifacts/checks

**History**

- package timeline
- package status transitions
- backlog/system decisions
- review gate runs
- revision task creation
- approval/rejection decisions

## Full Package Page

The full package page is for audit and deep review. It should include all drawer data with more
space and historical detail.

It should show:

- package spec and description
- acceptance criteria
- child tasks
- package dependencies
- task dependencies
- review gate history
- review rounds
- revision chains
- artifacts and checks
- raw system-gate run data
- recent agent activity related to the package

The full page should be reachable from the drawer and from direct links. If the dashboard does
not yet support page routing cleanly, hash-based routing is acceptable for the first slice.

## Navigation And Modes

The project dashboard should open in Package mode by default.

Controls:

- segmented mode toggle: `Packages | Tasks`
- group/project filter
- package status filter
- risk filter
- review state filter
- needs-attention filter
- task-only filters remain available in Task mode

When a task-only filter such as assignee or priority is active, the UI should either switch to
Task mode or clearly show that a task filter is active. It must not silently filter package data
in a misleading way.

Batch task actions should be disabled or hidden in Package mode.

## Operational States

The command center should classify package and gate state into user-visible operational states.

### Needs Attention

A package needs attention when any of these are true:

- package is blocked
- package review failed
- package is waiting on revision
- child task failed or was rejected
- expected checks/artifacts are missing
- gate is stale
- system agent appears stalled

### Stale Gate

A backlog or review gate is stale when it has been queued or running longer than a configured
threshold. The first implementation can use conservative static thresholds and move to project
settings later.

### System Agent State

The rail should show:

- idle
- queued work available
- running backlog/package review
- waiting on revision
- failed review attempt
- stalled heartbeat

## Data And API Requirements

The existing package APIs are a start, but the command center needs richer data.

### `GET /api/work-packages/board`

Should include package card fields:

- task counts
- status
- review status
- risk
- blocker summary
- stale flag
- needs-attention flag
- active agent or current gate when available
- latest review reason summary
- waiting age

### `GET /api/work-packages/{id}`

Should return package detail:

- package metadata
- child tasks
- review gate
- review runs
- revision tasks
- artifacts/check summaries
- dependencies
- timeline/history
- package-level decisions

### `GET /api/operations/summary`

Should return command summary and action rail data:

- project/package counts
- needs-attention queue
- review queue
- blocked queue
- system-agent state
- stale gates
- recent decisions

### Optional Later Endpoint

`PATCH /api/work-packages/{id}` can support manual edits after the read/visibility experience
is solid.

## Live Update Behavior

The command center should update without manual refresh.

Events that should refresh package board, rail, and drawer state:

- `work_package.*`
- `review_gate.*`
- `task.*`
- `task.system_gate_*`
- `agent.*`
- `group.*`

If WebSocket updates are unavailable, polling should keep the dashboard moving.

When a package drawer is open, updates for that package should refresh the drawer in place.
When a package review completes, the card should move columns and the action rail should update.

## Error Handling

- If package board data fails, show the existing task board fallback with a visible warning.
- If action rail data fails, show a compact unavailable state and keep the board usable.
- If package drawer data fails, keep the drawer open with a retry action.
- If full package page data fails, show a recoverable error and link back to the board.
- If live events disconnect, show WebSocket state and continue periodic refresh.

## Testing

API tests:

- package board includes card metadata
- package detail includes child tasks, review gate, revision tasks, and history
- operations summary classifies needs-attention items
- stale gates are classified correctly
- blocked packages appear in the rail
- package review completion updates group/package state

Frontend checks:

- dashboard scripts parse
- inline dashboard template script parses until scripts are split out
- package mode remains default when package data exists
- task mode still works
- task-only filters do not silently mis-filter packages
- package drawer opens and renders detail
- action rail updates after package/review events
- batch actions are not available for package cards

Browser checks:

- package board renders as the first project surface
- right rail shows system-agent/review/blocker state
- package drawer opens from a package card
- drawer tabs switch without layout breakage
- full package page opens from drawer
- board updates after package review completion without manual refresh

## Implementation Notes

The current dashboard still has a large inline template script plus static JS files. The first
implementation should either keep both surfaces in sync or explicitly move the browser-visible
logic to static files before adding more UI. The latter is preferred if it can be done without
destabilizing the dashboard.

The Work Package command center should be built incrementally:

1. Expand package board and operations summary APIs.
2. Add command summary and action rail.
3. Add package drawer with tabs.
4. Add full package page or hash route.
5. Tighten live updates and browser tests.

## Acceptance Criteria

- Project dashboard defaults to Package mode.
- Package board remains the main visible working surface.
- Right rail shows actionable review, blocker, stale, revision, and system-agent state.
- Package cards open a drawer with attention summary and tabs.
- Full package page is reachable for audit/deep review.
- Review queue visibility makes it clear whether the system agent has picked up work.
- Package review completion updates the board without refresh.
- Task board mode still works.
- Existing task-level review support remains available.
