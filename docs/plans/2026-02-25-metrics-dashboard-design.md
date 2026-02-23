# Metrics / Observability Dashboard Design

**Date:** 2026-02-25
**Status:** Approved

## Overview

A dedicated `/metrics` page with full Chart.js visualizations showing cost, tokens, task throughput, agent performance, error analysis, and pipeline health. Accessible via a "Metrics" button in the main nav bar.

## Data Sources

- `task_usage` table: input_tokens, output_tokens, cost_usd, duration_api_ms, num_turns, model, agent_id, recorded_at
- `tasks` table: status, assigned_to, claimed_by, created_at, started_at, completed_at, priority, task_type, group_id
- `agent_instances` table: status, role, last_heartbeat
- `task_dependencies` table: dependency graph data

## Page Structure

### Nav Bar
Add "Metrics" button between "Agents" and "FAQ" in the existing header. Navigates to `/metrics`. Metrics page has its own header with "Back to Dashboard" link.

### Time Range Selector
Sticky bar at top: `Live` | `1H` | `6H` | `Today` | `7D` | `30D`. Defaults to `Today`. All charts update on switch.

### Section 1: KPI Summary Row
Six cards matching existing stat-card style:
- Total Cost (daily/weekly)
- Tasks Completed (in range)
- Success Rate (% completed vs failed)
- Avg Task Duration (minutes)
- Total Tokens (input + output)
- Active Agents (currently working)

### Section 2: Cost & Token Charts
- Cost Over Time — Area chart, colored by model (Haiku vs Opus)
- Token Usage Over Time — Stacked bar chart (input vs output) per hour/day

### Section 3: Task Pipeline
- Task Throughput — Line chart: tasks completed per hour/day
- Task Status Breakdown — Doughnut chart
- Pipeline Flow — Horizontal bar: PM → Architect → Coder → Tester → Reviewer

### Section 4: Agent Performance
- Per-Role Success Rate — Horizontal bar chart
- Cost by Role — Bar chart
- Agent Leaderboard — Table: top 10 agents by tasks completed

### Section 5: Error Analysis
- Failure Rate Over Time — Line chart
- Failures by Role — Pie chart
- Recent Failures — Scrollable table of last 20 failed tasks

## Backend API Endpoints

- `GET /api/metrics/timeseries?range=today&granularity=hour` — cost, tokens, task counts per time bucket
- `GET /api/metrics/roles` — per-role success rates, costs, durations
- `GET /api/metrics/agents?top=10` — agent leaderboard
- `GET /api/metrics/failures?limit=20` — recent failed tasks

## Tech Stack
- Chart.js v4 via CDN
- New template: `metrics.html`
- Live updates via existing `/ws` WebSocket
- Responsive CSS grid (2-col desktop, 1-col mobile)
- Reuses existing design tokens (--bg-primary, --accent-*, etc.)
