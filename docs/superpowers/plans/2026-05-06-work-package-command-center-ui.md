# Work Package Command Center UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first production slice of the Work Package command center UI with actionable package visibility, package detail drilldown, and live refresh support.

**Architecture:** Extend the existing `TaskBoard` read APIs with package card metadata, package detail data, and operations summary data. Expose that data through dashboard task routes, then render it in the existing dashboard shell as a package-first board with command summary, right action rail, and a package drawer. Keep `src/taskbrew/dashboard/static/js/dashboard-core.js` and the inline script in `src/taskbrew/dashboard/templates/index.html` aligned because the template is currently browser-visible while static scripts are still parse-tested.

**Tech Stack:** Python 3.10+, FastAPI routes, SQLite via `TaskBoard`, pytest/httpx API tests, vanilla JS dashboard code, dashboard CSS.

---

## File Structure

- Modify `src/taskbrew/orchestrator/task_board.py`
  - Add package board card enrichment, package detail, operations summary, review-gate normalization, stale/attention classification, and safe JSON parsing for review gate runs.
- Modify `src/taskbrew/dashboard/routers/tasks.py`
  - Add `GET /api/work-packages/{package_id}` and `GET /api/operations/summary`.
- Modify `tests/test_work_packages.py`
  - Cover package board metadata and package detail behavior at the board layer.
- Modify `tests/test_dashboard_api.py`
  - Cover package detail and operations summary API behavior.
- Modify `src/taskbrew/dashboard/templates/index.html`
  - Add command summary/action rail/drawer markup and inline JS/CSS behavior used by the active dashboard page.
- Modify `src/taskbrew/dashboard/static/js/dashboard-core.js`
  - Mirror package command center data-fetch/render/drawer behavior for parse-tested static JS.
- Modify `src/taskbrew/dashboard/static/css/main.css`
  - Mirror command center/drawer styling for static asset parity.

---

### Task 1: Board-Layer Package Metadata

**Files:**
- Modify: `src/taskbrew/orchestrator/task_board.py`
- Test: `tests/test_work_packages.py`

- [ ] **Step 1: Write failing tests for package board metadata**

Add tests that create packages in review, blocked, and waiting-revision states, then assert:

```python
board_data = await board.get_work_package_board(group_id=group["id"])
card = next(pkg for pkg in board_data["packages"] if pkg["id"] == package["id"])

assert card["task_counts"]["total"] == 1
assert card["review_gate"]["status"] == "pending"
assert card["needs_attention"] is True
assert card["attention_reasons"]
assert card["waiting_age_seconds"] >= 0
assert "latest_review_reason_summary" in card
```

- [ ] **Step 2: Run the tests and verify RED**

Run: `.venv/bin/pytest tests/test_work_packages.py -q`

Expected: FAIL because the package board does not yet expose `review_gate`, `needs_attention`, `attention_reasons`, or `waiting_age_seconds`.

- [ ] **Step 3: Implement minimal board metadata**

Add helpers in `TaskBoard`:

```python
def _json_list(self, value) -> list:
    ...

def _parse_timestamp(self, value: str | None) -> datetime | None:
    ...

def _age_seconds(self, value: str | None, now: datetime) -> int | None:
    ...

def _normalize_review_gate(self, gate: dict | None, now: datetime) -> dict | None:
    ...

def _package_attention_reasons(self, package: dict, tasks: list[dict], gate: dict | None, now: datetime) -> list[dict]:
    ...
```

Update `get_work_package_board()` to load child task rows and review gate rows for the selected packages, then attach:

```python
card["task_counts"] = task_counts
card["review_gate"] = normalized_gate
card["needs_attention"] = bool(attention_reasons)
card["attention_reasons"] = attention_reasons
card["stale"] = any(reason["type"] == "stale_gate" for reason in attention_reasons)
card["latest_review_reason_summary"] = self._summary_text(package.get("review_reason"))
card["waiting_age_seconds"] = self._age_seconds(package.get("updated_at") or package.get("created_at"), now)
```

- [ ] **Step 4: Run tests and verify GREEN**

Run: `.venv/bin/pytest tests/test_work_packages.py -q`

Expected: PASS for the new board metadata tests and existing package tests.

- [ ] **Step 5: Commit**

```bash
git add src/taskbrew/orchestrator/task_board.py tests/test_work_packages.py
git commit -m "feat: enrich work package board data"
```

---

### Task 2: Package Detail And Operations Summary APIs

**Files:**
- Modify: `src/taskbrew/orchestrator/task_board.py`
- Modify: `src/taskbrew/dashboard/routers/tasks.py`
- Test: `tests/test_work_packages.py`
- Test: `tests/test_dashboard_api.py`

- [ ] **Step 1: Write failing tests for package detail and operations summary**

Add board-level assertions:

```python
detail = await board.get_work_package_detail(package["id"])

assert detail["id"] == package["id"]
assert detail["tasks"][0]["id"] == task["id"]
assert detail["review_gate"]["entity_id"] == package["id"]
assert detail["timeline"]
assert "artifacts" in detail
```

Add API assertions:

```python
detail_resp = await client.get(f"/api/work-packages/{package['id']}")
assert detail_resp.status_code == 200
assert detail_resp.json()["id"] == package["id"]

summary_resp = await client.get(f"/api/operations/summary?group_id={group['id']}")
assert summary_resp.status_code == 200
summary = summary_resp.json()
assert summary["counts"]["packages_total"] >= 1
assert summary["system_agent"]["status"] in {"idle", "working"}
assert summary["queues"]["review"]
```

- [ ] **Step 2: Run tests and verify RED**

Run: `.venv/bin/pytest tests/test_work_packages.py tests/test_dashboard_api.py -q`

Expected: FAIL because `get_work_package_detail`, `get_operations_summary`, and the routes do not exist yet.

- [ ] **Step 3: Implement package detail and operations summary**

Add `TaskBoard.get_work_package_detail(package_id)` returning:

```python
{
    **package,
    "tasks": normalized_child_tasks,
    "review_gate": normalized_gate,
    "review_runs": review_gate_runs,
    "revision_tasks": revision_tasks,
    "artifacts": artifact_summaries,
    "dependencies": dependencies,
    "timeline": timeline,
    "attention_reasons": attention_reasons,
    "needs_attention": bool(attention_reasons),
}
```

Add `TaskBoard.get_operations_summary(group_id=None)` returning:

```python
{
    "counts": {
        "packages_total": len(packages),
        "packages_active": active_count,
        "packages_blocked": blocked_count,
        "packages_review": review_count,
        "packages_waiting_revision": waiting_revision_count,
        "packages_attention": attention_count,
    },
    "queues": {
        "review": review_items,
        "blocked": blocked_items,
        "revision": revision_items,
        "stale": stale_items,
        "attention": attention_items,
    },
    "system_agent": system_agent_summary,
    "recent_decisions": recent_review_gate_runs,
}
```

Add routes:

```python
@router.get("/api/work-packages/{package_id}")
async def get_work_package_detail(package_id: str):
    ...

@router.get("/api/operations/summary")
async def get_operations_summary(group_id: str | None = None):
    ...
```

- [ ] **Step 4: Run tests and verify GREEN**

Run: `.venv/bin/pytest tests/test_work_packages.py tests/test_dashboard_api.py -q`

Expected: PASS for new API tests and existing dashboard API tests.

- [ ] **Step 5: Commit**

```bash
git add src/taskbrew/orchestrator/task_board.py src/taskbrew/dashboard/routers/tasks.py tests/test_work_packages.py tests/test_dashboard_api.py
git commit -m "feat: expose work package operations summary"
```

---

### Task 3: Command Center Dashboard UI

**Files:**
- Modify: `src/taskbrew/dashboard/templates/index.html`
- Modify: `src/taskbrew/dashboard/static/js/dashboard-core.js`
- Modify: `src/taskbrew/dashboard/static/css/main.css`

- [ ] **Step 1: Add package command center markup**

In the board view area, add:

```html
<section class="package-command-center" id="packageCommandCenter" hidden>
  <div class="package-command-summary" id="packageCommandSummary"></div>
  <div class="package-command-layout">
    <div class="package-board-shell">
      <div class="package-mode-bar">
        <div class="mode-segmented" role="tablist" aria-label="Board mode">
          <button type="button" class="mode-btn active" id="modePackagesBtn" onclick="setBoardMode('packages')">Packages</button>
          <button type="button" class="mode-btn" id="modeTasksBtn" onclick="setBoardMode('tasks')">Tasks</button>
        </div>
      </div>
      <div class="kanban-board" id="kanbanBoard" role="list" aria-label="Task columns">
        ...
      </div>
    </div>
    <aside class="package-action-rail" id="packageActionRail"></aside>
  </div>
</section>
<div class="package-detail-overlay" id="packageDetailOverlay">...</div>
<div class="package-full-page" id="packageFullPage" hidden>...</div>
```

Keep the existing task board columns inside the package board shell.

- [ ] **Step 2: Add JS state and fetches**

Add state:

```javascript
let currentBoardMode = 'packages';
let lastTaskBoardData = null;
let lastPackageBoardData = null;
let lastOperationsSummary = null;
let openPackageId = null;
```

Add fetch helpers:

```javascript
async function loadOperationsSummary() { ... }
async function loadPackageDetail(packageId) { ... }
```

- [ ] **Step 3: Render summary, rail, cards, and drawer**

Add render helpers:

```javascript
function renderPackageCommandCenter(summary) { ... }
function renderPackageCommandSummary(summary) { ... }
function renderPackageActionRail(summary) { ... }
function openPackageDrawer(packageId) { ... }
function renderPackageDrawer(detail) { ... }
function setPackageDrawerTab(tab) { ... }
function openPackageFullPage(packageId) { ... }
function renderPackageFullPage(detail) { ... }
function setBoardMode(mode) { ... }
```

Update `createPackageCard(pkg)` so package cards call `openPackageDrawer(pkg.id)` on click and show attention, review, age, risk, and gate state.

- [ ] **Step 4: Add hash-routed package full page**

Support `#package=<id>` as the first deep-link path. The full page should reuse the package detail API and render:

```text
package spec and description
child tasks grouped by status
review gate state and review runs
revision tasks
artifacts/check summaries
timeline/history
raw system-gate run data
```

The drawer `Open full page` action should set `location.hash = "package=" + packageId`. Clearing the hash should return to the command center board.

- [ ] **Step 5: Make package/task mode filter behavior explicit**

When `currentBoardMode === "packages"`:

```javascript
filterAssignee.disabled = true;
filterPriority.disabled = true;
batchSelectModeBtn.disabled = true;
```

When the user chooses a task-only filter, call `setBoardMode("tasks")` before refreshing. Status and group filters continue to work in both modes.

- [ ] **Step 6: Wire live refresh**

Update WebSocket handling so `task.*`, `task.system_gate_*`, `review_gate.*`, `work_package.*`, `group.*`, and `agent.*` refresh board/summary/rail. If `openPackageId` is set, refresh the drawer detail in place.

- [ ] **Step 7: Keep static and inline script aligned**

Apply the same JS behavior to:

```text
src/taskbrew/dashboard/static/js/dashboard-core.js
src/taskbrew/dashboard/templates/index.html
```

Apply CSS to:

```text
src/taskbrew/dashboard/static/css/main.css
src/taskbrew/dashboard/templates/index.html
```

- [ ] **Step 8: Run frontend syntax checks**

Run:

```bash
node --check src/taskbrew/dashboard/static/js/dashboard-core.js
node --check src/taskbrew/dashboard/static/js/dashboard-ui.js
python - <<'PY'
from pathlib import Path
import re, subprocess, tempfile
html = Path("src/taskbrew/dashboard/templates/index.html").read_text()
scripts = re.findall(r"<script>(.*?)</script>", html, re.S)
for i, script in enumerate(scripts):
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
        f.write(script)
        path = f.name
    subprocess.run(["node", "--check", path], check=True)
print(f"checked {len(scripts)} inline scripts")
PY
```

Expected: all checks pass.

- [ ] **Step 9: Commit**

```bash
git add src/taskbrew/dashboard/templates/index.html src/taskbrew/dashboard/static/js/dashboard-core.js src/taskbrew/dashboard/static/css/main.css
git commit -m "feat: add work package command center ui"
```

---

### Task 4: Verification And Browser Smoke

**Files:**
- No required source changes unless verification finds a defect.

- [ ] **Step 1: Run focused backend tests**

Run:

```bash
.venv/bin/pytest tests/test_work_packages.py tests/test_dashboard_api.py tests/test_system_gates.py tests/test_task_board.py -q
```

Expected: PASS.

- [ ] **Step 2: Run lint on touched Python files**

Run:

```bash
.venv/bin/ruff check src/taskbrew/orchestrator/task_board.py src/taskbrew/dashboard/routers/tasks.py tests/test_work_packages.py tests/test_dashboard_api.py
```

Expected: PASS.

- [ ] **Step 3: Browser smoke without restarting an existing TaskBrew server**

If a TaskBrew server is already listening, use the in-app browser to inspect the dashboard. Confirm:

```text
Package command summary renders.
Package mode is selected by default.
Package cards open the drawer.
Right rail renders review/blocker/system-agent state.
Task mode still renders ordinary task cards.
```

If no server is listening, report that browser smoke was blocked and do not start or restart TaskBrew without explicit permission.

- [ ] **Step 4: Final status**

Report commits, tests, and any browser-smoke limitation.
