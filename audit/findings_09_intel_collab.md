# Audit Findings — Intelligence (collab/context/social/task/routing)

**Files reviewed:** collaboration.py, context_providers.py, messaging.py, social_intelligence.py, specialization.py, task_intelligence.py, tool_router.py
**Reviewer:** audit-agent-09

## Finding 1 — ToolRouter is advisory-only; no allowlist enforcement
- **Severity:** CRITICAL
- **Category:** security
- **Location:** tool_router.py:37-77 (`select_tools`)
- **Finding:** `ToolRouter.select_tools()` returns a *recommended* tool list merged from task-type and role profiles, but the module has no `authorize()`/`is_allowed()` entrypoint. The return is purely advisory.
- **Impact:** Agents can invoke any tool regardless of a role's declared `tools:` allowlist — role tool scoping advertised elsewhere is not enforced here.
- **Fix:** Add `is_tool_allowed(role, tool_name) -> bool` and wire it into the tool execution path.

## Finding 2 — DocumentationProvider injects README content verbatim into LLM prompts
- **Severity:** HIGH
- **Category:** security (prompt injection)
- **Location:** context_providers.py:249-253
- **Finding:** `DocumentationProvider.gather()` reads `README.md` and embeds raw content into the context blob concatenated into agent prompts. Any markdown instructions in README flow straight to the model.
- **Impact:** Anyone who can modify README (PR, dependency, supply chain) can steer agents on every task using the `documentation` provider.
- **Fix:** Fence content with a strong delimiter and instruct the model to treat as data; require explicit opt-in.

## Finding 3 — GitHistoryProvider swallows all errors silently
- **Severity:** MEDIUM
- **Category:** error-handling
- **Location:** context_providers.py:87-108
- **Finding:** `except Exception: return ""` hides missing git binary, TimeoutExpired, permission errors.
- **Impact:** "Empty history" indistinguishable from "git broken"; degraded context flows into prompts without warning.
- **Fix:** Log at WARNING; narrow exception scope.

## Finding 4 — DependencyGraphProvider hand-rolled TOML parser is broken
- **Severity:** MEDIUM
- **Category:** correctness-bug
- **Location:** context_providers.py:157-170
- **Finding:** Scans pyproject.toml line-by-line for `"dependencies"` + `"="`; matches `optional-dependencies`, commented lines, and misparses multi-line strings/inline arrays.
- **Impact:** Wrong/empty dependency lists fed to LLMs as ground truth.
- **Fix:** Use stdlib `tomllib`.

## Finding 5 — Mental-model conflict detector relies on GROUP_CONCAT truncation
- **Severity:** MEDIUM
- **Category:** correctness-bug
- **Location:** social_intelligence.py:435-447
- **Finding:** `GROUP_CONCAT(DISTINCT value)` has undefined ordering and silently truncates at `group_concat_max_len` (default 1000 bytes).
- **Impact:** Conflict reports corrupted/truncated for non-trivial values.
- **Fix:** Return grouped keys, then per-key queries; or use `json_group_array`.

## Finding 6 — messaging.send silently drops priority/thread_id on fallback
- **Severity:** MEDIUM
- **Category:** correctness-bug
- **Location:** messaging.py:29-41
- **Finding:** The fallback INSERT (for unmigrated DB) omits priority/thread_id/message_type, but the method still returns them in the result dict — implying persistence.
- **Impact:** Pair-session threads and broadcast tagging silently lost when migration 4 is pending; `get_thread()` returns `[]`.
- **Fix:** Fail loudly on missing migration, or mark result as degraded.

## Finding 7 — MessagingManager.broadcast: N+1 writes, no transaction
- **Severity:** MEDIUM
- **Category:** perf / concurrency
- **Location:** messaging.py:55-81
- **Finding:** One INSERT per instance in a Python loop, no transaction wrapper.
- **Impact:** Non-atomic broadcasts; partial failures produce inbox inconsistencies; O(N) roundtrips.
- **Fix:** Wrap in `async with self._db.transaction()`; use `executemany`.

## Finding 8 — work_areas unbounded; detect_overlaps joins across full history with duplicate alerts
- **Severity:** HIGH
- **Category:** resource-leak / correctness-bug
- **Location:** social_intelligence.py:453-510
- **Finding:** `work_areas` rows never expire; `detect_overlaps()` self-joins on `file_path` with no time/status filter, finding overlaps across ALL history. Also INSERTs new `coordination_alerts` on every call with no dedup against prior alerts.
- **Impact:** Table grows unbounded; overlap detection accuracy degrades to zero; duplicate alert flood.
- **Fix:** Add TTL/expiry; filter by `status='in_progress'`; `UNIQUE(agent_ids, overlapping_files, resolved=0)`.

## Finding 9 — predict_consensus ignores participants argument
- **Severity:** HIGH
- **Category:** missed-impl
- **Location:** social_intelligence.py:656-703
- **Finding:** Docstring says "historical voting patterns"; implementation queries global `argument_sessions` without filtering by `participants`. Argument is stored but never consulted.
- **Impact:** Prediction identical for any participant set — advertised behavior does not match implementation.
- **Fix:** Join argument_evidence, filter `agent_id IN participants`; or update docstring.

## Finding 10 — resolve_argument has no tie-breaker; submit_evidence has no participant check
- **Severity:** MEDIUM
- **Category:** correctness-bug / security
- **Location:** social_intelligence.py:151-212
- **Finding:** Ties resolved nondeterministically by SQLite ordering. `submit_evidence()` never validates `agent_id` is a listed participant — any agent can tilt outcomes.
- **Impact:** Non-participants can influence arguments; tied outcomes insertion-order dependent.
- **Fix:** Enforce participant check; break ties deterministically or return `status='tied'`.

## Finding 11 — generate_handoff_summary file extraction is substring-match noise
- **Severity:** LOW
- **Category:** correctness-bug
- **Location:** collaboration.py:128-135
- **Finding:** `any(ext in line for ext in [".py", ".js", ...])` matches any line containing the literal — URLs, sentences like "package.json is deprecated."
- **Impact:** Handoff summaries include noise as "referenced files."
- **Fix:** Use a path regex like `(?:[\w./-]+/)?[\w.-]+\.(py|js|ts|html|yaml|json)\b`.

## Finding 12 — analyze_rejections keyword bucketing is cargo-cult substring matching
- **Severity:** LOW
- **Category:** correctness-bug
- **Location:** specialization.py:184-196
- **Finding:** `"test" in reason` catches "latest"/"fastest"; `"format"` catches "information"; `"scope"` catches "microscope". `elif` chain means only first match counts.
- **Impact:** Systematically wrong categorization; downstream suggestions act on noise.
- **Fix:** Word-boundary regex (`\btest(s|ing)?\b`) with multi-category hits allowed.

## Finding 13 — detect_prerequisites creates unbounded duplicates on re-run
- **Severity:** MEDIUM
- **Category:** correctness-bug / resource-leak
- **Location:** task_intelligence.py:191-272
- **Finding:** No uniqueness check; unconditional INSERT. Repeated calls create duplicate PR-* rows with divergent `confirmed` state.
- **Impact:** `get_prerequisites()` returns exponentially growing duplicate lists; UI noise; inconsistent confirmation state.
- **Fix:** `UNIQUE(task_id, prerequisite_task_id, reason)` + `INSERT OR IGNORE`.

## Finding 14 — find_parallel_tasks is O(N²), pair-only, and non-idempotent
- **Severity:** MEDIUM
- **Category:** perf / correctness-bug
- **Location:** task_intelligence.py:372-421
- **Finding:** Pairwise disjoint-file search with one INSERT per pair; no uniqueness on `(group_id, task_set)`. Cannot detect triples+.
- **Impact:** Opportunity table grows quadratically per invocation; "new opportunity" duplicates.
- **Fix:** Uniqueness key + dedup; document pair-only limit or extend to k-cliques.

## Finding 15 — predict_outcome uses uncalibrated logistic with magic coefficients
- **Severity:** HIGH
- **Category:** correctness-bug
- **Location:** task_intelligence.py:509-553 (line 524-525)
- **Finding:** `z = 0.5 * complexity - 3; p = 1/(1+exp(z))` has hardcoded coefficients with no fitting. Yields 40-point P swings across a single complexity step (complexity-5: P=0.78, complexity-7: P=0.38). `get_prediction_accuracy` exists but feedback never updates coefficients.
- **Impact:** Severely overconfident at low complexity, underconfident at high; advertised "prediction" is an ungrounded heuristic.
- **Fix:** Fit coefficients against `actual_success` history, or replace with kNN over historical data; flag as heuristic until calibrated.

## Finding 16 — find_similar loads all fingerprints into memory per call
- **Severity:** MEDIUM
- **Category:** perf
- **Location:** task_intelligence.py:630-666
- **Finding:** `SELECT * FROM task_fingerprints ORDER BY created_at DESC` with no LIMIT, then in-Python Jaccard over every row.
- **Impact:** Linear full-scan per similarity query; hundreds of ms at 10k tasks.
- **Fix:** Pre-filter by task_type/keyword prefix; FTS5 or inverted index.

## Finding 17 — start_debate assigns both debaters to the same role
- **Severity:** LOW
- **Category:** api-contract / missed-impl
- **Location:** collaboration.py:164-207
- **Finding:** Both debater A and debater B use the single `debater_role` param. With one coder instance, same agent claims both sides.
- **Impact:** "Adversarial debate" collapses to one role arguing with itself.
- **Fix:** Accept `debater_role_a`/`debater_role_b`, or require distinct instances at claim time.

## Finding 18 — Context provider cache check is racy
- **Severity:** LOW
- **Category:** concurrency
- **Location:** context_providers.py:44-68
- **Finding:** Read-modify-write: SELECT then INSERT. Concurrent callers both miss cache and both insert. TTL compared as ISO string is fragile if non-Z format ever appears.
- **Impact:** Duplicate cache rows; monotonic growth (no eviction shown).
- **Fix:** `UNIQUE(context_type, scope)` + `INSERT OR REPLACE`; periodic eviction.

## Finding 19 — Both ensure_tables schemas declare zero non-PK indexes
- **Severity:** LOW
- **Category:** perf
- **Location:** social_intelligence.py:28-123, task_intelligence.py:42-118
- **Finding:** Hot paths like `trust_scores WHERE from_agent=?`, `work_areas JOIN ON file_path`, `mental_model_facts WHERE key=? AND source_agent=?`, `detected_prerequisites WHERE task_id=?` are all full scans.
- **Impact:** Fine on 100 rows; unusable at 10k.
- **Fix:** `CREATE INDEX IF NOT EXISTS` on lookup columns.

## Finding 20 — record_preference classic lost-update race
- **Severity:** LOW
- **Category:** concurrency
- **Location:** social_intelligence.py:303-340
- **Finding:** SELECT-then-INSERT/UPDATE pattern; concurrent writers both INSERT (UNIQUE raises on one) or both UPDATE (later wins silently).
- **Impact:** Occasional exceptions; silent lost updates.
- **Fix:** `INSERT ... ON CONFLICT(agent_role, preference_key) DO UPDATE SET ...`.

## Systemic issues observed across this slice

- **Aspirational ML without calibration loop:** `predict_outcome` (magic logistic), `predict_consensus` (ignores participants), `update_trust` (EMA α=0.3), `update_skill_badge` (0.9/0.1 smoothing) all advertise learning. The schemas store `actual_*` columns and `get_*_accuracy` helpers compute accuracy, but no code feeds results back into coefficients — the loop never closes.
- **No idempotency on repeated detections:** `detect_prerequisites`, `find_parallel_tasks`, `report_work_area → detect_overlaps`, and `context_providers` cache all INSERT unconditionally. Repeated invocation produces unbounded duplicates.
- **No indexes on either large schema:** Both `ensure_tables` bodies create ~8 tables each with zero non-PK indexes. Every lookup-by-agent/by-task/by-file is a full scan.
- **Substring keyword matching pervasive:** rejection bucketing, prerequisite detection, file-reference extraction — all use `"x" in text` without word boundaries, producing systematic false positives that flow into downstream recommendations.
- **Silent schema-drift fallbacks:** `messaging.py` wraps INSERTs in `try/except Exception` to fall back to an older schema, silently losing priority/thread_id while returning a dict claiming they were set. Missing migrations should fail loudly.
- **Tool routing is advisory-only:** No enforcement entrypoint exists in `tool_router.py`; the `tools:` allowlist advertised at the role level is not enforced by this module.
- **Unsanitized disk content → LLM prompts:** README, pyproject, package.json, task output all concatenated into prompts verbatim with no fencing. Any writer to those files is an implicit prompt-author.

**Counts:** CRITICAL 1, HIGH 4, MEDIUM 9, LOW 6 (total 20)
