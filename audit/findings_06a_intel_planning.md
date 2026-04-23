# Audit Findings — Intelligence (planning)

**Files reviewed:** intelligence/planning.py, intelligence/advanced_planning.py
**Reviewer:** audit-agent-06a

## Finding 1 — "Intelligence" modules contain zero LLM calls; alternatives/rollback are hardcoded templates
- **Severity:** HIGH
- **Category:** missed-impl
- **Location:** planning.py:203-248, 250-266, 54-100
- **Finding:** `generate_alternatives` returns static arrays keyed by task_type; `create_rollback_plan` returns an identical 3-step string list for every task. No LLM, no per-task context used beyond `task_type`.
- **Impact:** Users receive generic templates presented with confidence scores as if AI-planned.
- **Fix:** Either wire an LLM with proper parsing/cost tracking or rename to template helpers and drop the misleading confidence fields.

## Finding 2 — Confidence values are hardcoded magic numbers
- **Severity:** MEDIUM
- **Category:** correctness-bug
- **Location:** planning.py:100 (0.7), 151 (0.6), 201 (0.7), 266 (0.8)
- **Finding:** Every `_store_plan` call passes a fixed confidence unrelated to evidence/sample size/model output.
- **Impact:** UIs sort/filter on fabricated signal.
- **Fix:** Derive from sample_size/keyword-match quality, or remove until a real estimator exists.

## Finding 3 — Cycle detection in topo-sort silently emits an invalid schedule
- **Severity:** HIGH
- **Category:** correctness-bug
- **Location:** advanced_planning.py:153-162
- **Finding:** On cycle, all remaining tasks receive the same `current_order` and the function returns; only a logger.warning fires.
- **Impact:** Callers execute dependency-violating schedules; pipelines break without clear error surface.
- **Fix:** Raise or return `{"error": "cycle", "members": [...]}`; persist `has_cycle` for `get_schedule` consumers.

## Finding 4 — `plan_with_resources` computes assignments but never persists them
- **Severity:** HIGH
- **Category:** correctness-bug
- **Location:** advanced_planning.py:237-274
- **Finding:** Assignments returned as a list; neither `tasks.assigned_to` nor any planning table is updated. Subsequent calls recompute from stale DB state.
- **Impact:** Resource-aware planning lost on restart; never takes effect unless a caller re-persists.
- **Fix:** Transactional UPDATE, or rename to `propose_assignments` and document non-persistence.

## Finding 5 — Deadline estimator fabricates a 1.0-hour default when no samples
- **Severity:** MEDIUM
- **Category:** correctness-bug
- **Location:** advanced_planning.py:320-329
- **Finding:** With zero historical completions, `avg_hours = 1.0` and confidence bounds come back as real numbers; only `based_on_samples=0` signals.
- **Impact:** UIs treating `estimated_hours` as authoritative display 1h defaults.
- **Fix:** Return `None` (or explicit `is_default` flag) when samples == 0.

## Finding 6 — `plan_increments` splits on any " and "/" then " token, creating nonsense
- **Severity:** MEDIUM
- **Category:** correctness-bug
- **Location:** advanced_planning.py:427
- **Finding:** `re.split(r'\s+and\s+|\s+then\s+', ...)` over the whole description, ignoring quoted strings, code fragments, and common compounds.
- **Impact:** Increments are meaningless fragments.
- **Fix:** Require explicit separators or sentence segmentation + classification.

## Finding 7 — `get_plans` silently swallows JSON decode errors
- **Severity:** MEDIUM
- **Category:** error-handling
- **Location:** planning.py:46-51
- **Finding:** `except (json.JSONDecodeError, TypeError): pass` leaves `content` as raw string; no log, no flag.
- **Impact:** Corrupt rows undetectable; downstream gets mixed dict/str types.
- **Fix:** Log with row id; return tagged error payload.

## Finding 8 — Plan status "draft" never advances; no lifecycle APIs
- **Severity:** MEDIUM
- **Category:** dead-code
- **Location:** planning.py:28
- **Finding:** All plans inserted `status='draft'`; neither file exposes approve/supersede/close.
- **Impact:** `task_plans.status` is dead data; claimed feedback loop does not exist.
- **Fix:** Add lifecycle methods or drop the column.

## Finding 9 — `build_schedule` DELETE+INSERT has no transaction boundary
- **Severity:** MEDIUM
- **Category:** concurrency
- **Location:** advanced_planning.py:168-184
- **Finding:** Old schedule deleted then rows inserted one-by-one without a transaction or concurrency guard.
- **Impact:** Crash mid-loop leaves empty schedule; concurrent callers interleave.
- **Fix:** Wrap in transaction; per-group lock.

## Finding 10 — Hardcoded token/time estimate buckets masquerade as model-aware
- **Severity:** MEDIUM
- **Category:** missed-impl
- **Location:** planning.py:125-136
- **Finding:** `tokens_estimate` is 5000/15000/40000 by word count; not tied to model or historical `task_usage` (which is queried but unused).
- **Impact:** Budget forecasts built on this are fictional.
- **Fix:** Percentile/regression over `task_usage.total_tokens`.

## Finding 11 — No cost/token tracking for planning operations themselves
- **Severity:** MEDIUM
- **Category:** missed-impl
- **Location:** planning.py, advanced_planning.py
- **Finding:** Neither file writes to `task_usage`; future LLM-backed planning has no plumbing.
- **Fix:** Add usage-row insert in `_store_plan`.

## Finding 12 — Risk file-pattern list misses critical paths (auth, secrets, payments, SQL)
- **Severity:** MEDIUM
- **Category:** edge-case
- **Location:** planning.py:176
- **Finding:** High-risk substring list: `__init__`, `main`, `config`, `database`, `migration`. Misses `auth`, `security`, `secret`, `password`, `payment`, `*.sql`, `env`, `deploy`, `docker`.
- **Impact:** Security-critical edits flagged low-risk.
- **Fix:** Regex path rules with weighted categories.

## Finding 13 — `datetime.fromisoformat` rejects Z suffix on Python <3.11
- **Severity:** LOW
- **Category:** edge-case
- **Location:** advanced_planning.py:308-309, 523-524
- **Finding:** Internal writes use `+00:00` but external writers using `Z` cause ValueError; logged then row silently dropped.
- **Fix:** Normalize `s.replace("Z", "+00:00")` or require 3.11+.

## Finding 14 — Scope creep thresholds fire on tiny descriptions
- **Severity:** LOW
- **Category:** edge-case
- **Location:** advanced_planning.py:366-384
- **Finding:** 50% growth over a 2-keyword/10-char original fires on trivial edits; denominator `max(len(orig_keywords), 1)` inflates pct.
- **Fix:** Minimum original size (e.g. ≥20 words) before evaluating growth.

## Finding 15 — `common_failures` is unstop-worded word frequency, not actionable
- **Severity:** LOW
- **Category:** correctness-bug
- **Location:** advanced_planning.py:534-540
- **Finding:** Top-10 word histogram with no stopword filter; "the", "and", "to" dominate.
- **Fix:** Stopwords filter, cluster by reason, or LLM classify.

## Finding 16 — dep_map KeyError latent
- **Severity:** LOW
- **Category:** edge-case
- **Location:** advanced_planning.py:151
- **Finding:** `all(d in assigned for d in dep_map[tid])` crashes if `dep_map` drifts from `remaining`.
- **Fix:** `dep_map.get(tid, [])`.

## Finding 17 — `snapshot_resources` unbounded row growth
- **Severity:** LOW
- **Category:** resource-leak
- **Location:** advanced_planning.py:207-235
- **Finding:** Each call inserts N rows; called by `plan_with_resources`, so `resource_snapshots` grows unboundedly.
- **Fix:** TTL/retention, or upsert latest-per-agent.

## Finding 18 — Post-mortem writes NULL-vs-0 success_rate ambiguity
- **Severity:** LOW
- **Category:** api-contract
- **Location:** advanced_planning.py:491-512
- **Finding:** When `total == 0`, `success_rate=0` stored — indistinguishable from "all failed."
- **Fix:** NULL or explicit sentinel when total==0.

## Systemic issues
- **Both modules are branded "intelligence" yet contain zero LLM integration** — no JSON parsing, retry loop, feedback guard, or cost tracking for planning operations.
- **Several APIs compute results that are never persisted** (`plan_with_resources` assignments, plan status lifecycle), so outputs are lost on restart.
- **Hardcoded magic values** (confidences 0.6/0.7/0.8, token buckets 5000/15000/40000, default 1.0h deadline, 50% scope threshold) create false precision.
- **Error handling oscillates between silent `pass` and bare exceptions**; no consistent policy.
- **Regex/keyword NLP is brittle and ships as production signal** (scope-creep set, increment splitter, risk-file substrings, common-failures word-frequency) — spots that need either rigorous rules or explicit LLM delegation, not the ad-hoc middle ground.

**Counts:** HIGH 3, MEDIUM 9, LOW 6 (total 18)
