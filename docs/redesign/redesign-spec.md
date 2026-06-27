# TaskBrew Dashboard — UI Redesign Proposal

> Goal: make the dashboard approachable for normal users **without removing a single feature**. The strategy is **progressive disclosure** — a calm default surface that answers "what's happening and what do I do next?", with every advanced panel one deliberate drill-down deeper.

---

## 1. Diagnosis — what's actually overwhelming

I explored every surface live (28 view tabs, 11 header controls, Settings, Metrics, all modals). The complexity is real and it comes from five concrete problems, not just "too many features."

### 1.1 Twenty-eight peer view tabs in one flat strip
The secondary nav exposes all of these as equal-weight tabs, most off-screen behind a scroll:

```
Board · List · Graph · Memory · Quality · Skills · Knowledge · Security ·
Observability · Code Intel · Planning · Autonomous · Coordination · Learning ·
Testing · Monitoring · Leaderboard · Notifications · Pipelines · Webhooks ·
Self-Improvement · Social Intel · Code Reasoning · Task Intel · Verification ·
Process Intel · Knowledge Mgmt · Compliance
```

A normal user opens the app to **submit a goal and watch it get built**. Tabs 4–28 are diagnostic panels they will touch maybe once a month. Putting them at the same altitude as "Board" is the single biggest source of overwhelm.

### 1.2 The app already knows the right grouping — but hides it
The breadcrumbs reveal the developers' own mental model:
- `Dashboard › Board`
- `Dashboard › Task Management › List / Graph`
- `Dashboard › Intelligence › Memory / Quality / Skills / Knowledge / Security / Observability / Code Intel / Planning / Autonomous / Learning / …`
- `Dashboard › Intelligence › Coordination`

So 28 flat tabs are really **~4 conceptual areas**. The information architecture exists; it's just not expressed in the nav. The redesign mostly surfaces what's already there.

### 1.3 Filters are duplicated 2–3 times on screen
On the Board there are **three overlapping filter rows** visible at once:
1. Global row: search + Status + Roles + Priorities
2. "FILTERS:" row: Groups + Roles + Status + Priorities (+ Layout + Cols)
3. In-view row: search + Status + Priorities + Assignees + date-from + date-to

Three search boxes, three status dropdowns, two role dropdowns. This alone makes the screen look twice as dense as the feature set warrants.

### 1.4 The same data has three front doors
- **KPI strip** (Cost Today, Active Tasks, Agents…) on the dashboard
- **Observability tab** (Total Cost, Anomalies, Bottlenecks, Decisions)
- **Metrics page** (`/metrics`: Total Cost, Tasks Completed, Tokens, charts)
- **Usage popover** (Claude token usage)

Four places to look at cost/throughput. Consolidating them is itself a simplification.

### 1.5 Dead / unconfigured panels look like broken features
Several panels render empty or error text, which reads as "the product is broken":
- Knowledge tab: **`[object Object]`** instead of node/edge counts (real bug)
- "Secrets service unavailable", "Locks service unavailable"
- "Experiments service unavailable", "Benchmarks service unavailable", "Cross-project service unavailable"

> ⚠️ These must be **confirmed with you before pruning** — several are likely just optional services that aren't running in this instance, not features to delete. "All features available" means we hide them gracefully, not remove them.

---

## 2. Design principles

1. **Default to the 20%.** The first screen shows only what a normal user needs every session: submit a goal, watch tasks flow, see cost/agents at a glance. Everything else is depth-on-demand.
2. **Group by the app's own breadcrumbs.** Three top-level areas — **Work**, **Insights**, **Team & Config** — not 28 tabs.
3. **Depth-on-demand, not a mode flip.** "Advanced" is never a switch that re-dumps all 28 panels. Each advanced panel lives *inside* the area it belongs to, reachable in one click.
4. **One filter bar, one source of truth.** A single contextual filter bar; advanced filters behind a "Filters" button with a count badge.
5. **Consolidate overlaps.** One Cost & Performance home. Merge the near-duplicate intelligence panels.
6. **Degrade gracefully.** Unconfigured services show a one-line "not enabled — how to turn on" hint, never raw errors or `[object Object]`.

---

## 3. New information architecture

### Top level: 3 areas (replaces 28 tabs)

```
┌─ WORK ──────────── the default. Goal → tasks → board/list/graph.
│                     (Board, List, Graph, Task detail, Create)
│
├─ INSIGHTS ──────── everything diagnostic, grouped into 5 sub-sections
│   ├ Cost & Performance   ← Metrics + Observability + KPI strip + Usage (merged)
│   ├ Quality & Safety      ← Quality, Verification, Security, Compliance, Testing
│   ├ Knowledge & Memory    ← Memory, Knowledge, Knowledge Mgmt, Code Intel, Code Reasoning
│   ├ Agent Intelligence    ← Skills, Leaderboard, Learning, Self-Improvement, Autonomous,
│   │                          Process Intel, Task Intel, Social Intel
│   └ Planning & Coord.     ← Planning, Coordination, Monitoring
│
└─ TEAM & CONFIG ── Agents sidebar + Settings (pipeline, roles, budgets) +
                     Pipelines, Webhooks, Notifications (integrations)
```

Every one of the 28 panels has a home. Nothing is deleted — it's nested one level deeper under a label that explains *why you'd go there*.

> **Honesty note:** I opened and inspected 14 of the 28 panels live. The groupings for the other 14 (Testing, Monitoring, Leaderboard, Notifications, Pipelines, Webhooks, Self-Improvement, Social Intel, Code Reasoning, Task Intel, Verification, Process Intel, Knowledge Mgmt, Compliance) are inferred from their tab names and should be confirmed before building — a couple may belong in a different sub-section once their actual content is seen.

> **Naming note:** "Work / Insights / Team & Config" deliberately renames the app's internal vocabulary (Task Management / Intelligence / Coordination) to more user-facing terms. This is a conscious UX choice, not a mismatch — keep the internal names as code identifiers if useful, but lead with plain language in the UI.

### Mapping table (old tab → new home)

| Old flat tab | New location |
|---|---|
| Board, List, Graph | **Work** (primary) |
| Observability, Metrics page, KPI strip, Usage | **Insights › Cost & Performance** |
| Quality, Verification, Testing | **Insights › Quality & Safety** |
| Security, Compliance | **Insights › Quality & Safety** |
| Memory, Knowledge, Knowledge Mgmt | **Insights › Knowledge & Memory** |
| Code Intel, Code Reasoning | **Insights › Knowledge & Memory** |
| Skills, Leaderboard | **Insights › Agent Intelligence** |
| Learning, Self-Improvement, Process Intel | **Insights › Agent Intelligence** |
| Autonomous, Task Intel, Social Intel | **Insights › Agent Intelligence** |
| Planning, Coordination, Monitoring | **Insights › Planning & Coordination** |
| Pipelines, Webhooks, Notifications | **Team & Config › Integrations** |
| Settings (pipeline, roles, budget) | **Team & Config › Settings** |
| Agents sidebar | **Team & Config › Agents** (keep quick-access) |

---

## 4. Wireframes

### 4.1 Default view — "Work" (what a normal user sees on open)

```
┌──────────────────────────────────────────────────────────────────────────┐
│  ▣ TaskBrew     [ Taskbrew ▾ ]              ● 5 agents · $0.00 today   ◐ ⌄│  ← slim header
├──────────────────────────────────────────────────────────────────────────┤
│   Work          Insights          Team & Config                          │  ← 3 areas only
├──────────────────────────────────────────────────────────────────────────┤
│                                                                            │
│   ┌────────────────────────────────────────────────────────┐  ┌────────┐ │
│   │  What should the team build?                            │  │ Submit │ │  ← goal bar = hero
│   │  e.g. Add dark mode to the app                          │  │  Goal  │ │
│   └────────────────────────────────────────────────────────┘  └────────┘ │
│                                                                            │
│   ⚡ 0 active   ✅ 0 done today   🔒 0 blocked   ⏱ idle    [ Resume team ] │  ← 1 compact status line
│                                                                            │
│   ┌── Tasks ─────────────────────────────────  [Board ▾] [⛭ Filters] [+ New]│  ← ONE view switch + ONE filter btn
│   │                                                                        │
│   │   BACKLOG       PENDING       IN PROGRESS     REVIEW      DONE         │
│   │  ┌────────┐    ┌────────┐    ┌──────────┐   ┌────────┐  ┌────────┐    │
│   │  │ card   │    │ card   │    │ card     │   │ card   │  │ card   │    │
│   │  └────────┘    └────────┘    └──────────┘   └────────┘  └────────┘    │
│   │                                                                        │
│   └────────────────────────────────────────────────────────────────────  │
│                                                       ▸ Activity log (12)  │  ← collapsed by default
└──────────────────────────────────────────────────────────────────────────┘
```

Key moves vs today:
- Goal bar is the **hero**, not a thin strip wedged under 6 KPI cards.
- The 6-card KPI strip → **one status line**. Tap it to expand into the full Cost & Performance page.
- View switch (Board / List / Graph / Compact / Focus) collapses into **one `[Board ▾]` dropdown** instead of a 28-item rail + a Layout dropdown + a Cols dropdown.
- All filtering behind **one `[⛭ Filters]` button** with a count badge — replaces the 3 stacked filter rows.
- Event Log → **collapsed "Activity log" drawer** with an unread count.

### 4.2 Drill-down — "Insights" landing (replaces the 25-tab tail)

```
┌──────────────────────────────────────────────────────────────────────────┐
│   Work        ‹Insights›        Team & Config                             │
├──────────────────────────────────────────────────────────────────────────┤
│   Insights                                          [ Today ▾ ] [Export ▾] │
│                                                                            │
│   ┌─ Cost & Performance ───────────┐  ┌─ Quality & Safety ──────────────┐ │
│   │ $0.00 today · 50.3K tokens     │  │ 0 vulns · 0 SAST · no quality   │ │
│   │ ▁▂▃▅▂▁ cost over time          │  │ scores yet                      │ │
│   │ Tasks ✓ 0/0 · success --       │  │ → Security, Verification, Tests │ │
│   │ → open full metrics            │  └─────────────────────────────────┘ │
│   └────────────────────────────────┘                                       │
│   ┌─ Knowledge & Memory ───────────┐  ┌─ Agent Intelligence ────────────┐ │
│   │ 0 memories · 0 graph nodes     │  │ Leaderboard · Skills · Learning │ │
│   │ → Memory, Knowledge, Code Intel│  │ 5 agents · 0 badges             │ │
│   └────────────────────────────────┘  └─────────────────────────────────┘ │
│   ┌─ Planning & Coordination ──────────────────────────────────────────┐  │
│   │ 0 post-mortems · 0 standups · no scope-creep flags                  │  │
│   └────────────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────────┘
```

Insights is a **summary-card hub**: each card shows the headline number and links into the detailed panel(s). A power user reaches any of the original 28 panels in exactly **two clicks** (Insights → card → panel), but a normal user just sees five calm summaries instead of a 28-item scroll bar.

### 4.3 Unconfigured service — graceful state (replaces raw errors)

```
┌─ Secret Detections ───────────────────────────────┐
│  🔌  Secrets scanning isn't enabled                │
│      Turn it on in Settings › Security, or run     │
│      `taskbrew serve --enable secrets`.            │
│                                            [ Docs ] │
└────────────────────────────────────────────────────┘
```

---

## 5. Specific consolidations (each one removes a redundant surface)

| Merge | From | Into |
|---|---|---|
| **Cost & Performance** | KPI strip + Observability tab + `/metrics` page + Usage popover | one page, with the status-line as its glanceable summary |
| **Filters** | 3 stacked rows | 1 contextual bar + `[Filters]` overflow with badge |
| **View switch** | 28-tab rail + Layout dropdown + Cols dropdown | 1 `[Board ▾]` control (Board/List/Graph/Compact/Focus + density) |
| **Quality & Safety** | Quality + Verification + Testing + Security + Compliance | one sub-section, tabbed inside |
| **Knowledge** | Memory + Knowledge + Knowledge Mgmt + Code Intel + Code Reasoning | one sub-section |
| **Agent Intelligence** | Skills + Leaderboard + Learning + Self-Improvement + Autonomous + Process/Task/Social Intel | one sub-section |

---

## 6. Mapping to the actual codebase

The dashboard is **vanilla JS + server-rendered templates** (no framework), so this can be done incrementally without a rewrite.

| File | Size | Role | Redesign work |
|---|---|---|---|
| `src/taskbrew/dashboard/templates/index.html` | 695 KB | the monolith (header, KPI strip, goal bar, 28-tab rail, board, modals, event log) | Biggest lift. Replace the flat tab rail with the 3-area nav + Insights hub. Collapse the 3 filter rows into one bar + overflow. |
| `src/taskbrew/dashboard/static/js/dashboard-ui.js` | 97 KB | tab switching, filters, board render | Add area→sub-section routing; dedupe filter state into one model. |
| `src/taskbrew/dashboard/static/js/dashboard-core.js` | 76 KB | state, websocket/event feed | Mostly unchanged; feed the collapsed Activity drawer. |
| `src/taskbrew/dashboard/static/js/intelligence.js` | 79 KB | the 20+ intelligence panels | Unchanged renderers — just mounted under Insights sub-sections instead of flat tabs. **Low risk: panels keep working, only their parent container moves.** |
| `src/taskbrew/dashboard/static/js/metrics.js` | 53 KB | `/metrics` charts | Reuse as the Cost & Performance page. |
| `src/taskbrew/dashboard/templates/metrics.html` | 110 KB | metrics page | Becomes Insights › Cost & Performance (or stays a route the hub links to). |
| `src/taskbrew/dashboard/templates/settings.html` | 204 KB | pipeline builder, roles, budgets | Largely keep; reachable via Team & Config. Already well-sectioned (collapsibles). |
| `routers/intelligence.py`, `analytics.py`, `usage.py`, `costs.py` | — | back-end APIs | **No API changes needed** — same endpoints, regrouped front-end. |

The crucial insight: **the panel renderers don't change.** The 28 panels already exist as independent render functions in `intelligence.js`. The redesign is almost entirely an **information-architecture and navigation change** in `index.html` + `dashboard-ui.js` — we re-parent existing components, we don't rebuild them. That makes it low-risk and shippable in phases.

---

## 6b. Control inventory — proof nothing was lost

Every interactive control I observed, and where it lives in the new design. This is the literal check against the "all features available" constraint:

| Current control | New home |
|---|---|
| Project selector (`Taskbrew ▾`) | Header (kept) |
| Online-agents count | Header status (kept) |
| Activity / Event Log | **Work** → collapsed "Activity" drawer (unread badge) |
| Resume / Pause Team | **Work** status line (primary action) |
| Restart Server | **Team & Config** → Settings (it's a maintenance action, not a daily one) |
| Settings | **Team & Config** → Settings |
| Agents sidebar | **Team & Config** → Agents (also a header quick-access icon) |
| **Per-agent Chat** | Agents panel — each agent row keeps its Chat action; also surfaced from a task's assignee |
| Usage (token popover) | folded into **Insights › Cost & Performance**; quick-glance stays in header status |
| Metrics page | **Insights › Cost & Performance** |
| FAQ / Help | Header `?` (kept) |
| Theme toggle, Notifications | Header (kept) |
| Goal bar + Submit | **Work** (hero) |
| 6 KPI cards | one status line → expands to Cost & Performance |
| Board view-switch (Board/List/Graph) | one `[Board ▾]` control |
| Layout (Grid/Compact/List/Focus) + Cols (2/3/4) | density options inside `[Board ▾]` |
| 3 filter rows (search/status/role/priority/group/assignee/date) | one filter bar + `[⛭ Filters]` overflow with count badge |
| **+ New Task** | **Work** board toolbar (kept) |
| **Export CSV** | board toolbar overflow (⋮ menu) + Insights export |
| **Select Mode / bulk actions** | board toolbar overflow (⋮ menu) |
| Packages / Tasks sub-tabs | kept as a toggle inside the Work board |
| "Needs Attention" rail | kept on the Work board (collapsible) |
| Create Task / Task Detail modals | unchanged |
| Settings: pipeline builder, agent roles, team/intelligence/security config, cost budgets | **Team & Config** → Settings (unchanged content) |

No control is removed — each is either kept in place, promoted to the default surface, or moved one click deeper into the area it belongs to.

---

## 7. Phased implementation plan

**Phase 0 — Fixes & inventory (½ day)**
- Fix the `[object Object]` Knowledge counters (real bug).
- Replace every "service unavailable" with the graceful "not enabled" card.
- Confirm with you which of the empty services are live vs intentionally off.

**Phase 1 — Calm the default (highest ROI, ~2 days)**
- Collapse the 6-card KPI strip → one status line.
- Make the goal bar the hero.
- Merge the 3 filter rows → one bar + `[Filters]` overflow.
- Collapse view switch + Layout + Cols → one `[Board ▾]` control.
- Collapse Event Log → "Activity" drawer with unread badge.
*(No panels touched yet — this alone removes ~70% of the visual density.)*

**Phase 2 — Three-area nav + Insights hub (~3 days)**
- Replace the 28-tab rail with **Work / Insights / Team & Config**.
- Build the Insights summary-card hub; re-parent the existing intelligence panels under its 5 sub-sections.
- Wire `[Board ▾]`, deep links, and breadcrumb back-nav.

**Phase 3 — Consolidate overlaps (~2 days)**
- Merge Observability + Metrics + Usage into Cost & Performance.
- Tab the Quality/Knowledge/Agent sub-sections internally.

**Phase 4 — Polish**
- Empty-state illustrations, keyboard nav, density/Focus modes, onboarding tooltip on first run.

---

## 8. Open questions for you

1. **Which empty panels are intentionally off** vs broken? (Secrets, Locks, Experiments, Benchmarks, Cross-project Knowledge.)
2. **Audience split:** is there a real "developer/operator" persona who lives in the 28 panels daily, or is everyone a "submit a goal" user? (Affects how aggressively we bury Insights.)
3. **Do you want me to implement Phase 1 now** against `index.html` / `dashboard-ui.js`, or keep this as a design doc first?
