# Audit Findings — Intelligence (QA/verification/observability)

**Files reviewed:** verification.py, testing_quality.py, observability.py, process_intelligence.py, quality.py, review_learning.py
**Reviewer:** audit-agent-08b

## Finding 1 — Verification never actually verifies anything
- **Severity:** CRITICAL
- **Category:** missed-impl
- **Location:** verification.py:15-670 (whole module)
- **Finding:** `VerificationManager` only records claims. `fingerprint_regression`, `record_run`, `mine_spec`, `evaluate_gate`, etc., take caller-provided strings/bools/dicts and INSERT them. Nothing shells out to pytest, parses JUnit XML, computes coverage, or validates that a test ran. `evaluate_gate(gate_name, metrics)` blindly trusts `metrics`.
- **Impact:** An agent claiming "all tests pass, coverage=100, open_bugs=0" passes every quality gate; module is a storage surface masquerading as verification.
- **Fix:** Rename to a ledger manager and add a `_run_pytest()` helper that invokes the test runner and writes numbers itself.

## Finding 2 — Mutation "analysis" counts AST nodes, does not mutate
- **Severity:** HIGH
- **Category:** missed-impl
- **Location:** testing_quality.py:201-256 (run_mutation_analysis)
- **Finding:** Walks AST, counts Compare/BinOp/BoolOp nodes, computes `score = 1 - mutation_points/(lines*2)`. No mutants generated, no test suite executed; `killed=0` hardcoded.
- **Impact:** Reported "mutation score" is code density, not test quality.
- **Fix:** Wrap mutmut/cosmic-ray or remove the feature.

## Finding 3 — File-path inputs unconfined → path traversal / arbitrary read
- **Severity:** HIGH
- **Category:** security
- **Location:** testing_quality.py:148-152, 204-208, 284-288, 346-349; verification.py:467-471
- **Finding:** `source_file`, `file_path`, `files_changed` entries are joined with `self._project_dir` via `os.path.join` and opened. `os.path.join("/proj","/etc/passwd")` returns `/etc/passwd`; `../../etc/passwd` escapes. No confinement.
- **Impact:** Any caller with filename control reads arbitrary host files; contents persist to DB.
- **Fix:** Resolve, verify subpath of `self._project_dir`, reject absolute inputs.

## Finding 4 — Welford online variance is implemented incorrectly
- **Severity:** HIGH
- **Category:** correctness-bug
- **Location:** testing_quality.py:528-537 (record_test_timing)
- **Finding:** Uses `new_variance = (old_variance*n + dx*dy) / new_n`, stores only std (not M2), squares each call. Correct Welford accumulates M2 with `(x-old_mean)*(x-new_mean)`; current formula can go negative (hence the `max(0.0, ...)` guard).
- **Impact:** `detect_perf_regressions` uses this std — false negatives and false positives.
- **Fix:** Store M2 column; update `M2 += (x-old_mean)*(x-new_mean)`; compute std on read.

## Finding 5 — `detect_perf_regressions` measures variance, not regression
- **Severity:** MEDIUM
- **Category:** correctness-bug
- **Location:** testing_quality.py:566-586
- **Finding:** Flags baselines where `std > avg * threshold_pct/100`. That detects noisy tests, not slowdowns.
- **Fix:** Record per-run samples; flag when latest exceeds `mean + k*std`.

## Finding 6 — Log injection / PII leakage via unsanitized fields
- **Severity:** MEDIUM
- **Category:** security
- **Location:** observability.py:120-150 (log_decision), verification.py:399-424 (annotate), review_learning.py:26-78 (extract_feedback)
- **Finding:** `decision`, `reasoning`, `context`, `message`, `output` stored verbatim — no size cap, no CRLF/ANSI strip, no secret scrubber.
- **Impact:** Audit-trail log injection; secrets pasted into `context` persist.
- **Fix:** Cap length, strip `\r\n\x1b`, scrub secret regexes before INSERT.

## Finding 7 — Anomaly + bottleneck detection have unbounded row growth
- **Severity:** MEDIUM
- **Category:** resource-leak
- **Location:** observability.py:362-414, 290-356
- **Finding:** `detect_anomalies` rescans all metric history per call, INSERTs one row per out-of-range sample, no dedup. `detect_bottlenecks` inserts a new row every call.
- **Impact:** Tables bloat to O(N*k).
- **Fix:** Scan only since last run; unique key; retention.

## Finding 8 — Confidence/code-quality scoring is trivially gameable
- **Severity:** MEDIUM
- **Category:** correctness-bug
- **Location:** quality.py:65-138 (extract_self_review, score_confidence, score_code_quality)
- **Finding:** Pure substring counting — "verified"/"tested" raises confidence; agents learn to include magic words.
- **Fix:** Gate on objective signals (test exit code, lint status); demote phrase heuristics.

## Finding 9 — `auto_annotate` DoS surface + incorrect long-function detection
- **Severity:** MEDIUM
- **Category:** perf / correctness-bug
- **Location:** verification.py:457-532
- **Finding:** Opens file with no size cap; one awaited INSERT per annotation; long-function length uses `i - func_start` which mis-measures nested defs.
- **Fix:** Size cap; batch INSERT; use `ast.FunctionDef.end_lineno`.

## Finding 10 — `annotate()` IDs collide with readiness assessments
- **Severity:** MEDIUM
- **Category:** api-contract
- **Location:** verification.py:408 (`RA-`) vs process_intelligence.py:338 (`RA-`)
- **Finding:** Both mint `RA-<hex>` in separate tables.
- **Fix:** Rename one prefix (e.g. `RN-`) and migrate.

## Finding 11 — `auto_map` writes duplicates, mis-resolves basename collisions
- **Severity:** LOW
- **Category:** correctness-bug
- **Location:** verification.py:216-243
- **Finding:** `test_file_mappings` has no UNIQUE constraint; keys by bare `sf.name`.
- **Fix:** UNIQUE(test_file, source_file) + UPSERT; key by relative path.

## Finding 12 — `score_file` upsert not atomic
- **Severity:** LOW
- **Category:** concurrency
- **Location:** process_intelligence.py:167-195
- **Finding:** DELETE + INSERT without transaction.
- **Fix:** `INSERT ... ON CONFLICT(file_path) DO UPDATE`.

## Finding 13 — `detect_bottlenecks` fragile to malformed timestamps
- **Severity:** LOW
- **Category:** error-handling
- **Location:** observability.py:290-356
- **Finding:** `datetime.fromisoformat(row["started_at"])` raises on non-ISO legacy rows, kills the routine.
- **Fix:** Per-row try/except.

## Finding 14 — Doc drift regex misses real refs, flags stdlib
- **Severity:** LOW
- **Category:** correctness-bug
- **Location:** testing_quality.py:457-513
- **Finding:** `file_ref_pattern` requires backtick/quote wrap; misses plain `foo.py`. `func_ref_pattern` flags every `word()` in prose as drift.
- **Fix:** Markdown parser + stdlib allowlist.

## Finding 15 — `review_learning.pattern == feedback_type` (feature unfulfilled)
- **Severity:** LOW
- **Category:** missed-impl / dead-code
- **Location:** review_learning.py:55-68
- **Finding:** Stores same string in both `pattern` and `feedback_type`; discards actual review text.
- **Fix:** Store matched span, or drop the column.

## Finding 16 — Monte-Carlo percentiles are coarse integers
- **Severity:** LOW
- **Category:** correctness-bug
- **Location:** process_intelligence.py:124-159 (forecast)
- **Finding:** Samples whole-sprint velocities; for low-variance history, p50=p75=p90.
- **Fix:** Document or interpolate with `statistics.quantiles`.

## Finding 17 — Regression-risk & mutation-details blobs retained forever
- **Severity:** LOW
- **Category:** resource-leak
- **Location:** testing_quality.py:374-378, 243-247
- **Finding:** Every PR/analysis stores full JSON blob; no retention.
- **Fix:** Retain last N per pr_identifier/file_path.

## Systemic issues
- **Claim-store pattern everywhere.** Verification, testing_quality, quality, readiness accept metrics from the caller and store as truth; no adversarial harness runs tests/coverage/mutants itself.
- **Unbounded tables, no indexes beyond PK.** 9+ tables write-and-never-prune.
- **File paths are a universal traversal primitive.** Every module that reads source uses `os.path.join(project_dir, caller)` + `open()` with no confinement.
- **Statistics are wrong or misnamed.** Welford variance wrong; "performance regression" measures variance; Monte-Carlo percentiles collapse on low variance.
- **Quality/confidence/review learning are phrase heuristics.** LLM maximises score by echoing magic words.
- **ID-prefix namespace collisions.** `RA-` reused across two tables; other short prefixes reused across modules.

**Counts:** CRITICAL 1, HIGH 3, MEDIUM 6, LOW 7 (total 17)
