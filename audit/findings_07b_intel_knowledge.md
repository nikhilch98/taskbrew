# Audit Findings — Intelligence (knowledge/memory/learning)
**Files reviewed:** src/taskbrew/intelligence/knowledge_graph.py, src/taskbrew/intelligence/knowledge_management.py, src/taskbrew/intelligence/memory.py, src/taskbrew/intelligence/learning.py, src/taskbrew/intelligence/self_improvement.py, src/taskbrew/intelligence/_utils.py
**Reviewer:** audit-agent-07b

## Finding 1 — `learn_conventions` reads arbitrary project files with a path-traversal-weak validator
- **Severity:** M
- **Category:** security
- **Location:** learning.py:260-267 (walks `directory` after `validate_path`); _utils.py:23-30 (`validate_path`)
- **Finding:** `validate_path` only rejects literal `..` segments after `os.normpath`, so symlink-based escape, absolute paths like `/etc`, or `~` expansion all bypass the check. `os.walk` then follows symlinks by default, and each `.py` file is read and regex-scanned.
- **Impact:** A malicious/untrusted `directory` argument (e.g., from an API or agent-provided path) can cause reads of arbitrary files on the host including `~/.ssh`, `/etc/passwd`, absorbing them into conventions.
- **Fix:** Resolve `directory` to absolute path and require it to be within the project root via `Path.resolve().relative_to(project_root)`, and pass `followlinks=False` to `os.walk` plus a per-file size cap.

## Finding 2 — `knowledge_graph.build_from_directory` walks arbitrary directory, no project-root check
- **Severity:** M
- **Category:** security
- **Location:** knowledge_graph.py:177-196
- **Finding:** Accepts any `directory`, calls `os.walk` (default follows symlinks), then calls `analyze_file(fpath)` with absolute paths. In `_safe_read`, when `_project_dir` is set the absolute `fpath` is resolved against `project_root`, producing a `relative_to` mismatch — files under the walked directory are then silently rejected. When `_project_dir` is None, files outside the project are read with no confinement.
- **Impact:** Either (a) feature silently returns empty results with a project_dir, or (b) is a read-arbitrary-files vector without one.
- **Fix:** Pass relative paths to `analyze_file`, enforce `directory` under `_project_dir`, and set `followlinks=False`.

## Finding 3 — `scan_for_gaps` reads caller-supplied file list with zero validation
- **Severity:** M
- **Category:** security
- **Location:** knowledge_management.py:228-283
- **Finding:** `code_files` and `doc_files` are both `Path(df).read_text(...)` with no project-root confinement, no size cap, and no symlink check. The accumulated `doc_text` grows unbounded in memory.
- **Impact:** Read arbitrary files and unbounded memory growth on large/symlinked inputs; cheap DoS via a large file list.
- **Fix:** Confine to project root, enforce per-file size limit, skip symlinks.

## Finding 4 — Knowledge-graph `_add_edge` swallows every exception as "duplicate"
- **Severity:** M
- **Category:** error-handling
- **Location:** knowledge_graph.py:95-103
- **Finding:** `except Exception: pass  # Duplicate edge` masks schema errors, disk-full, concurrency conflicts, and programmer errors as if they were unique-constraint violations (schema has no explicit uniqueness on edges).
- **Impact:** Silent data loss; real DB errors go untraced.
- **Fix:** Catch only IntegrityError, or dedupe with an existence check; re-raise anything else.

## Finding 5 — SQL `LIKE` injection via user-controlled wildcards
- **Severity:** M
- **Category:** security
- **Location:** memory.py:74-79 (recall), memory.py:111-117 (find_patterns), memory.py:151-155 (get_style_guide), learning.py:414-416 (get_prevention_hints), knowledge_management.py:432-438 (search_knowledge), self_improvement.py:430-438 (find_relevant_reflections)
- **Finding:** User strings wrapped `f"%{kw}%"` without escaping `%`, `_`, or `\\`. Parameter binding prevents classic SQL injection, but an attacker-controlled query (e.g., `%`) returns every row and `_` matches any char — scope-expansion / data exfiltration.
- **Impact:** Cross-row data disclosure, wrong results, degraded index performance.
- **Fix:** Escape LIKE meta-chars and add `ESCAPE '\\'` to each clause.

## Finding 6 — `recall` does not respect `project_id` filter despite the column existing
- **Severity:** L
- **Category:** correctness-bug
- **Location:** memory.py:55-95
- **Finding:** `recall` never filters by `project_id`, so memories stored with a project_id leak across projects when queried by `agent_role`. `get_project_context` partially compensates but still ORs in the same unscoped recall.
- **Impact:** Cross-project memory leakage — preferences from project A surface in project B.
- **Fix:** Add optional `project_id` parameter and include it in the WHERE clause.

## Finding 7 — `decay_scores` age filter compares ISO datetime to `date(...)` output
- **Severity:** M
- **Category:** correctness-bug
- **Location:** memory.py:180-188
- **Finding:** Query is `WHERE last_accessed < date(?, '-N days')` with `?` bound to a full ISO timestamp. SQLite `date(...)` returns `YYYY-MM-DD`; `last_accessed` is stored as `YYYY-MM-DDTHH:MM:SS+00:00`. Lexical comparison across different formats is unreliable — rows on the cutoff day sort inconsistently.
- **Impact:** Decay runs over wrong rows — either decays fresh memories or misses stale ones.
- **Fix:** Compute the cutoff in Python (`(now - timedelta(days=N)).isoformat()`) and compare directly with matching format.

## Finding 8 — `recall` relevance scoring never learns (write-never, decay-only)
- **Severity:** L
- **Category:** correctness-bug
- **Location:** memory.py:82-95
- **Finding:** SELECT orders by `relevance_score DESC, created_at DESC` but `relevance_score` is only written by `decay_scores` (always downward). `access_count` is incremented on hit but never reflects in ranking. Popular memories get buried over time.
- **Impact:** "Relevance" semantics are decay-only, contradicting the docstring.
- **Fix:** Boost `relevance_score` on hit (with ceiling), or compute a composite live score including `access_count` and `last_accessed`.

## Finding 9 — `cluster_errors` / `track_repeated_corrections` produce meaningless "patterns"
- **Severity:** L
- **Category:** missed-impl
- **Location:** learning.py:244-258, learning.py:371-412
- **Finding:** Both functions label individual English words as error "patterns" / "clusters" (`cluster_name = f"error-{word}"`). A stopword-filtered bag-of-words is not clustering; prevention hints are templated strings.
- **Impact:** DB accumulates noise like `error-timeout`, `error-failed`, `error-error`; downstream hints non-actionable.
- **Fix:** Either rename as `top_rejection_terms` (no DB writes), or implement real clustering (TF-IDF + k-means, or LLM grouping).

## Finding 10 — `scan_for_gaps` re-inserts duplicate gaps every run
- **Severity:** L
- **Category:** correctness-bug
- **Location:** knowledge_management.py:244-282
- **Finding:** No dedupe against existing open `doc_gaps` for `(symbol_name, file_path)` — each scan inserts a fresh row. Regex also misses multi-line class signatures.
- **Impact:** Duplicate gap records explode on each scan; coverage stats mislead.
- **Fix:** Use `ast` (consistent with knowledge_graph.py), check for open unresolved gap before insert, or `ON CONFLICT` upsert.

## Finding 11 — `analyze_file` inflates `nodes_created` by counting upserts, not new creates
- **Severity:** L
- **Category:** correctness-bug
- **Location:** knowledge_graph.py:124-174
- **Finding:** Each `_upsert_node` call increments `nodes_created` regardless of whether it was an UPDATE or INSERT.
- **Impact:** Re-analysis reports bogus "new nodes: N" counts.
- **Fix:** Return `(id, was_created)` from `_upsert_node` and only increment on insert.

## Finding 12 — `get_winner` has no statistical significance check
- **Severity:** L
- **Category:** correctness-bug
- **Location:** learning.py:85-108
- **Finding:** Declares winner by `success_rate + quality` sum with no minimum sample size. Variants with 1 trial can beat variants with 1000. Summing a 0-1 rate with a possibly-unbounded quality score is dimensionally inconsistent.
- **Impact:** A/B-test decisions flip on a single success; poor prompts get promoted.
- **Fix:** Enforce minimum per-variant trials (e.g., ≥30), normalize quality to [0,1], consider confidence intervals.

## Finding 13 — `find_relevant_reflections` OR-chain with unescaped LIKE on unsplit user tokens
- **Severity:** L
- **Category:** security
- **Location:** self_improvement.py:430-438
- **Finding:** Splits task description on whitespace and ORs each token into a `LIKE ?` chain. Combined with Finding 5's missing `%`/`_` escaping, a crafted token (`%`) matches every reflection.
- **Impact:** Scope-expansion / exfiltration — returns unrelated reflections.
- **Fix:** Escape LIKE meta-chars; consider FTS.

## Finding 14 — `record_load` trusts caller-supplied token counts
- **Severity:** L
- **Category:** missed-impl
- **Location:** self_improvement.py:332-360
- **Finding:** No validation: `context_tokens` could be negative or exceed `max_tokens`. `clamp(ratio)` masks the bug.
- **Impact:** Eviction recommendations become junk for miscalibrated callers.
- **Fix:** Validate `context_tokens >= 0` and `max_tokens > 0`; reject invalid snapshots instead of clamping.

## Finding 15 — Post-mortem JSON-content contract mismatch
- **Severity:** L
- **Category:** api-contract
- **Location:** memory.py:129-141 (store_postmortem), memory.py:143-145 (get_similar_failures)
- **Finding:** `store_postmortem` stashes a JSON blob into `content`, but `get_similar_failures` returns raw rows without decoding. Consumers get a string that looks like content but is JSON.
- **Impact:** Downstream double-JSON encoding or display of raw braces.
- **Fix:** Decode on read, or store fields as columns.

## Finding 16 — `get_coverage_stats` is not a coverage metric
- **Severity:** L
- **Category:** correctness-bug
- **Location:** knowledge_management.py:310-328
- **Finding:** `coverage_percent = resolved_gaps / total_gaps`. Total is only already-flagged symbols, so a codebase with 10 undocumented symbols reads `0%` while another with 100 resolved reads `100%`. Doesn't measure true code-coverage.
- **Impact:** Dashboard metric is misleading.
- **Fix:** Count total public symbols in scanned files; compute `(symbols - open_gaps) / symbols`.

## Systemic issues
- **No `pickle` / `eval` / `exec` / `compile` usage detected** in any of the five audited files. `self_improvement.py` does NOT modify its own source or write LLM output to disk — the "self-improvement" name is misleading but the implementation is pure DB storage. Red flag is a false alarm.
- **AST parsing (knowledge_graph.py) uses `ast.parse` with a 1 MB size cap** — safe in isolation, but `build_from_directory` bypasses `_safe_read`'s project-root confinement (Finding 2).
- **Three inconsistent path-validation schemes** across these files: `_safe_read` (knowledge_graph), `validate_path` (_utils, used by learning), and raw `Path.read_text` (knowledge_management). None prevent symlink escape; `validate_path` only checks `..` segments.
- **LIKE queries have no `%`/`_` escaping at 6 call sites** (Finding 5): memory.py:74,111,151; learning.py:414; knowledge_management.py:432; self_improvement.py:430. Parameterization blocks classic SQLi but not wildcard-scope expansion.
- **No dedupe / uniqueness at insert time** in `knowledge_graph_edges` (relies on broad except), `doc_gaps`, `cognitive_load_snapshots`, `confidence_records`, `prompt_performance`, `compression_profiles`, `institutional_knowledge`. Unbounded DB growth on repeated scans.
- **Memory growth unbounded** — only `decay_scores` exists, no retention/prune for any table. `agent_memories`, `compression_profiles`, `confidence_records`, `cognitive_load_snapshots`, `prompt_performance`, `institutional_knowledge` all grow forever.
- **Learning heuristics are stub-quality** — `cluster_errors` and `track_repeated_corrections` are word counters; `get_winner` lacks statistical rigor; `match_task_to_agent` uses binary threshold counting. Marketed as features, behave like placeholders.
