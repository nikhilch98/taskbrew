# Metrics / Observability Dashboard Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a full-page `/metrics` dashboard with Chart.js visualizations for cost, tokens, throughput, agent performance, and error analysis.

**Architecture:** Four new API endpoints serve aggregated metrics data from the existing SQLite tables (`task_usage`, `tasks`, `agent_instances`). A new `metrics.html` Jinja2 template renders the page with Chart.js v4 via CDN. A "Metrics" nav button on the main dashboard links to the new page. WebSocket reuse enables live chart updates.

**Tech Stack:** Python/FastAPI (backend), Chart.js v4 CDN (charts), Jinja2 (template), existing SQLite DB

---

### Task 1: Add metrics API endpoints to app.py

**Files:**
- Modify: `src/taskbrew/dashboard/app.py` (insert after the `/api/usage` section, around line 344)

**Step 1: Add the four metrics endpoints**

Insert this block after the existing `# Usage` section (after `get_usage` endpoint, before `# Board filters`):

```python
    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    @app.get("/api/metrics/timeseries")
    async def get_metrics_timeseries(
        range: str = "today",
        granularity: str = "hour",
    ):
        """Return cost, tokens, task counts per time bucket."""
        now = datetime.now(timezone.utc)
        range_map = {
            "1h": timedelta(hours=1),
            "6h": timedelta(hours=6),
            "today": timedelta(hours=now.hour, minutes=now.minute, seconds=now.second),
            "7d": timedelta(days=7),
            "30d": timedelta(days=30),
            "live": timedelta(minutes=30),
        }
        delta = range_map.get(range, range_map["today"])
        # "today" should be from midnight
        if range == "today":
            since = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        else:
            since = (now - delta).isoformat()

        # Determine strftime format for grouping
        if granularity == "hour":
            fmt = "%Y-%m-%dT%H:00:00"
        elif granularity == "minute":
            fmt = "%Y-%m-%dT%H:%M:00"
        else:
            fmt = "%Y-%m-%dT00:00:00"

        # Cost/token timeseries from task_usage
        usage_rows = await task_board._db.execute_fetchall(
            "SELECT strftime(?, recorded_at) AS bucket, "
            "  model, "
            "  SUM(cost_usd) AS cost, "
            "  SUM(input_tokens) AS input_tokens, "
            "  SUM(output_tokens) AS output_tokens, "
            "  COUNT(*) AS task_count "
            "FROM task_usage WHERE recorded_at >= ? "
            "GROUP BY bucket, model ORDER BY bucket",
            (fmt, since),
        )

        # Task completion/failure timeseries from tasks
        task_rows = await task_board._db.execute_fetchall(
            "SELECT strftime(?, completed_at) AS bucket, "
            "  status, COUNT(*) AS count "
            "FROM tasks WHERE completed_at IS NOT NULL AND completed_at >= ? "
            "GROUP BY bucket, status ORDER BY bucket",
            (fmt, since),
        )

        # Task status totals (all time for the status breakdown)
        status_totals = await task_board._db.execute_fetchall(
            "SELECT status, COUNT(*) AS count FROM tasks GROUP BY status"
        )

        return {
            "usage": usage_rows,
            "tasks": task_rows,
            "status_totals": {r["status"]: r["count"] for r in status_totals},
            "since": since,
            "granularity": granularity,
        }

    @app.get("/api/metrics/roles")
    async def get_metrics_roles():
        """Per-role success rates, costs, durations."""
        role_tasks = await task_board._db.execute_fetchall(
            "SELECT assigned_to AS role, "
            "  COUNT(*) AS total, "
            "  SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) AS completed, "
            "  SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS failed "
            "FROM tasks GROUP BY assigned_to ORDER BY total DESC"
        )
        role_costs = await task_board._db.execute_fetchall(
            "SELECT "
            "  SUBSTR(agent_id, 1, INSTR(agent_id, '-') - 1) AS role, "
            "  SUM(cost_usd) AS cost, "
            "  SUM(input_tokens) AS input_tokens, "
            "  SUM(output_tokens) AS output_tokens, "
            "  AVG(duration_api_ms) AS avg_duration_ms, "
            "  SUM(num_turns) AS total_turns "
            "FROM task_usage GROUP BY role ORDER BY cost DESC"
        )
        return {"task_stats": role_tasks, "cost_stats": role_costs}

    @app.get("/api/metrics/agents")
    async def get_metrics_agents(top: int = 10):
        """Agent leaderboard."""
        rows = await task_board._db.execute_fetchall(
            "SELECT agent_id, "
            "  COUNT(*) AS tasks_completed, "
            "  SUM(cost_usd) AS total_cost, "
            "  SUM(input_tokens) AS input_tokens, "
            "  SUM(output_tokens) AS output_tokens, "
            "  AVG(duration_api_ms) AS avg_duration_ms, "
            "  SUM(num_turns) AS total_turns "
            "FROM task_usage GROUP BY agent_id "
            "ORDER BY tasks_completed DESC LIMIT ?",
            (top,),
        )
        return rows

    @app.get("/api/metrics/failures")
    async def get_metrics_failures(limit: int = 20):
        """Recent failed tasks."""
        rows = await task_board._db.execute_fetchall(
            "SELECT id, title, assigned_to, task_type, group_id, "
            "  created_at, completed_at "
            "FROM tasks WHERE status = 'failed' "
            "ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
        return rows
```

**Step 2: Add the /metrics route**

Insert this right before `return app` at the bottom of `create_app()` (around line 493):

```python
    @app.get("/metrics")
    async def metrics_page(request: Request):
        return templates.TemplateResponse("metrics.html", {"request": request})
```

**Step 3: Verify the server starts**

Run: `kill $(pgrep -f 'ai-team serve') 2>/dev/null; cd /Users/nikhilchatragadda/Personal\ Projects/ai-team && .venv/bin/pip install -e . && .venv/bin/ai-team serve &`
Then: `sleep 3 && curl -s http://127.0.0.1:8420/api/metrics/roles | python3 -m json.tool | head -20`
Expected: JSON with role stats

**Step 4: Commit**

```bash
git add src/taskbrew/dashboard/app.py
git commit -m "feat: add metrics API endpoints (/timeseries, /roles, /agents, /failures)"
```

---

### Task 2: Add Metrics button to main dashboard nav

**Files:**
- Modify: `src/taskbrew/dashboard/templates/index.html`

**Step 1: Add the Metrics nav button**

Find the Agents button (around line 1917-1919) and insert a Metrics button right after it, before the FAQ button. Also add `nav-metrics-btn` to the CSS selectors for nav buttons.

In the CSS section (around line 286), add `nav-metrics-btn` to the selector lists:

```css
        .nav-faq-btn,
        .nav-metrics-btn,
        .nav-agents-btn,
        .nav-pause-btn,
        .nav-settings-btn,
        .nav-restart-btn {
```

And similarly for the `:hover` and `svg` selectors.

Add accent color for metrics button:
```css
        .nav-metrics-btn { color: var(--accent-cyan); border-color: rgba(6, 182, 212, 0.2); }
        .nav-metrics-btn:hover { background: rgba(6, 182, 212, 0.12); border-color: rgba(6, 182, 212, 0.3); }
```

In the HTML nav (after the Agents button, before FAQ button), add:

```html
            <button class="nav-metrics-btn" onclick="window.location.href='/metrics'" title="Metrics & Observability">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/></svg>
                Metrics
            </button>
```

**Step 2: Verify visually**

Restart server, open http://127.0.0.1:8420, confirm "Metrics" button appears in nav bar between "Agents" and "FAQ".

**Step 3: Commit**

```bash
git add src/taskbrew/dashboard/templates/index.html
git commit -m "feat: add Metrics nav button to main dashboard"
```

---

### Task 3: Create the metrics.html template

**Files:**
- Create: `src/taskbrew/dashboard/templates/metrics.html`

**Step 1: Create the full metrics page**

Create `src/taskbrew/dashboard/templates/metrics.html` with:

- Same design tokens (CSS variables) as index.html
- Chart.js v4 CDN (`https://cdn.jsdelivr.net/npm/chart.js`)
- Header with "Back to Dashboard" link and time range selector (`Live | 1H | 6H | Today | 7D | 30D`)
- **Section 1: KPI row** — 6 stat cards (Total Cost, Tasks Completed, Success Rate, Avg Duration, Total Tokens, Active Agents)
- **Section 2: Cost & Tokens** — 2-column grid: Area chart (cost over time by model), Stacked bar chart (input vs output tokens)
- **Section 3: Task Pipeline** — 3-column grid: Line chart (throughput), Doughnut (status breakdown), Horizontal bar (pipeline flow PM→Arch→Coder→Tester→Reviewer)
- **Section 4: Agent Performance** — 3-column grid: Horizontal bar (role success rates), Bar chart (cost by role), Table (agent leaderboard)
- **Section 5: Error Analysis** — 3-column grid: Line chart (failure rate), Pie chart (failures by role), Scrollable table (recent failures)
- JavaScript: fetch all 4 API endpoints, populate charts, WebSocket connection for live updates
- All charts use dark theme colors from the CSS variables
- Time range buttons re-fetch all data with the selected range param

Key Chart.js configuration patterns:
- Dark backgrounds: `backgroundColor: 'transparent'`
- Grid lines: `color: 'rgba(99, 102, 241, 0.08)'`
- Text color: `color: '#8b93a7'`
- Role colors map: `{ pm: '#3b82f6', architect: '#8b5cf6', coder: '#f59e0b', tester: '#10b981', reviewer: '#ec4899' }`

The template must be a complete, self-contained HTML file (no build step). Inline all CSS and JS.

**Step 2: Verify the page loads**

Restart server, navigate to http://127.0.0.1:8420/metrics, confirm all charts render with data.

**Step 3: Commit**

```bash
git add src/taskbrew/dashboard/templates/metrics.html
git commit -m "feat: add metrics dashboard page with Chart.js visualizations"
```

---

### Task 4: Add WebSocket live updates to metrics page

**Files:**
- Modify: `src/taskbrew/dashboard/templates/metrics.html` (the JS section)

**Step 1: Add WebSocket listener**

In the metrics.html JavaScript section, after initial data load, add WebSocket connection that:
- Connects to `ws://${location.host}/ws`
- On `task.completed` or `task.failed` events: re-fetch `/api/metrics/timeseries` and update charts
- Debounce updates to max 1 refresh per 5 seconds to avoid hammering the API
- Show connection status indicator (reuse the same pattern as index.html)

**Step 2: Verify live updates**

With the metrics page open, submit a goal on the main dashboard. Confirm the metrics charts update within a few seconds.

**Step 3: Commit**

```bash
git add src/taskbrew/dashboard/templates/metrics.html
git commit -m "feat: add WebSocket live updates to metrics dashboard"
```

---

### Task 5: Final verification and cleanup

**Step 1: Full end-to-end test**

1. Start server: `.venv/bin/ai-team serve`
2. Open http://127.0.0.1:8420 — verify Metrics button in nav
3. Click Metrics — verify /metrics page loads with all 5 sections
4. Verify time range selector works (click different ranges, charts update)
5. Verify API endpoints return data:
   - `curl http://127.0.0.1:8420/api/metrics/timeseries?range=7d`
   - `curl http://127.0.0.1:8420/api/metrics/roles`
   - `curl http://127.0.0.1:8420/api/metrics/agents`
   - `curl http://127.0.0.1:8420/api/metrics/failures`

**Step 2: Commit any final fixes**

```bash
git add -A
git commit -m "feat: metrics dashboard complete with all visualizations"
```
