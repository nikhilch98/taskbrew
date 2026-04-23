# Audit Findings — Intelligence (code analysis)

**Files reviewed:** intelligence/code_intel.py, intelligence/code_reasoning.py
**Reviewer:** audit-agent-07a

## Finding 1 — Unbounded file read into memory (DoS on large files)
- **Severity:** HIGH
- **Category:** resource-leak
- **Location:** code_intel.py:30, 135, 272, 360, 442, 466, 603; code_reasoning.py:794
- **Finding:** Every analysis method calls `Path(file_path).read_text(errors="replace")` with no size cap, binary sniff, or streaming; `ast.parse` materializes the full AST.
- **Impact:** Multi-MB/GB file (asset, generated `.py`, attacker path) OOMs the worker; binary files with `.py` names silently mangled.
- **Fix:** `stat()` before read, reject >2MB, skip symlinks, verify extension/MIME.

## Finding 2 — `Path.rglob` walks without gitignore / symlink / project-root checks
- **Severity:** HIGH
- **Category:** security
- **Location:** code_intel.py:585-641 (detect_dead_code)
- **Finding:** `detect_dead_code(directory="src/")` accepts caller-supplied directory; never resolve()s or verifies containment; does not skip `.git/`, `.venv/`, `node_modules/`; `rglob` follows symlinks.
- **Impact:** Caller can scan `/` or `$HOME`, hit symlink loops, leak arbitrary paths into DB.
- **Fix:** Resolve against `self._project_dir`, reject outside; skip vendored; track inodes.

## Finding 3 — `validate_path` is trivially bypassable
- **Severity:** HIGH
- **Category:** security
- **Location:** intelligence/_utils.py:23-31
- **Finding:** Rejects `..` after normpath, but absolute paths like `/etc/passwd` normalize cleanly. `Path(self._project_dir) / file_path` silently discards project_dir when right operand is absolute.
- **Impact:** Caller can read arbitrary absolute paths via `index_intent`, `record_dependency`, `check_conformance`, `detect_opportunities`, `generate_narrative`, `record_invariant`, `check_invariant_violations`.
- **Fix:** Reject absolute paths; resolve against project_dir; realpath containment check.

## Finding 4 — `ast.parse` on untrusted source with no depth/size guard
- **Severity:** MEDIUM
- **Category:** resource-leak
- **Location:** code_intel.py:31, 136, 273, 361, 443, 467, 604
- **Finding:** `ast.parse` raises RecursionError / consumes stack on pathologically nested expressions; `ast.walk` unbounded; no node-count cap.
- **Fix:** Cap source length, catch RecursionError/MemoryError, node-count threshold.

## Finding 5 — SQL WHERE uses OR across tokens — wrong semantics, no ranking
- **Severity:** MEDIUM
- **Category:** correctness-bug
- **Location:** code_intel.py:115; code_reasoning.py:156, 705
- **Finding:** Multi-token queries join with `" OR "`, so `"user login"` matches either; results ordered by `created_at` only.
- **Impact:** "Semantic search" returns recency-biased noisy subsets.
- **Fix:** Use AND, or emit relevance score and ORDER BY it.

## Finding 6 — Dead-code detection is fundamentally unsound
- **Severity:** MEDIUM
- **Category:** correctness-bug
- **Location:** code_intel.py:585-656
- **Finding:** "Called" test is bare-name match (`ast.Name.id` / `Attribute.attr`) across tree; any string collision marks live. Dynamic dispatch invisible. Class methods excluded; `test_*` files skipped — functions only used by tests look dead.
- **Impact:** Both false positives and false negatives; users deleting live code.
- **Fix:** Import graph / qualified names; include methods; label as candidates.

## Finding 7 — `_iter_parents` is O(N²) per node → O(N³) per file
- **Severity:** MEDIUM
- **Category:** perf
- **Location:** code_intel.py:664-669, called at 63-66 and 614
- **Finding:** Re-walks entire tree per def; inside `detect_dead_code`'s rglob loop it compounds.
- **Impact:** 5k-line module with 100 defs → ~2.5M node visits.
- **Fix:** Build child→parent map once, O(1) lookup.

## Finding 8 — `detect_opportunities` line counters never reset on block exit
- **Severity:** MEDIUM
- **Category:** correctness-bug
- **Location:** code_reasoning.py:388-457
- **Finding:** `func_lines`/`class_lines` increment on every subsequent line regardless of dedent — once a `def` is seen, every following line counts toward that function until next `def`.
- **Impact:** All but the last function falsely flagged `long_method`; first class almost always `large_class`.
- **Fix:** Indent-based block exit, or use `ast.end_lineno` (as code_intel.py does).

## Finding 9 — Duplicate-block detection is O(N²) memory
- **Severity:** MEDIUM
- **Category:** perf
- **Location:** code_reasoning.py:459-476
- **Finding:** `seen_blocks` dict key is every 3-line concatenation; with no file-size cap, hostile input → hundreds of MB of dict keys.
- **Fix:** Hash key, cap dict size, enforce file-size cap upstream.

## Finding 10 — `check_conformance` trailing-comma heuristic buggy
- **Severity:** LOW
- **Category:** correctness-bug
- **Location:** code_reasoning.py:334-352
- **Finding:** String-matching heuristic flags lines inside docstrings/comments/multi-line strings.
- **Fix:** Use tokenize/ast.

## Finding 11 — `check_invariant_violations` identifier check is naive substring match
- **Severity:** LOW
- **Category:** correctness-bug
- **Location:** code_reasoning.py:809-823
- **Finding:** `if ident not in content` is substring check; identifier list includes Python keywords; `total` "exists" if any comment mentions it.
- **Fix:** Tokenize; compare identifier sets; filter keywords.

## Finding 12 — DB writes not transactional; duplicate-row growth on re-scan
- **Severity:** LOW
- **Category:** error-handling
- **Location:** code_intel.py:216-229, 479-497; code_reasoning.py:478-486
- **Finding:** Each detected item INSERTed in own await; partial failures leak rows. No idempotency — repeated `detect_patterns` produces duplicate AP-* rows.
- **Fix:** Transaction; upsert or delete prior rows for the file first.

## Finding 13 — "Singleton"/"Factory" heuristics over-match
- **Severity:** LOW
- **Category:** correctness-bug
- **Location:** code_intel.py:154-188
- **Finding:** Singleton flagged if any `_instance` name appears inside a class; Factory flagged for any method with ≥2 `Return` + any `if`/`match`.
- **Fix:** Structural checks — classmethod returning `cls`, class-level `_instance = None`.

## Finding 14 — `ensure_tables` not called lazily; no migrations
- **Severity:** LOW
- **Category:** missed-impl
- **Location:** code_reasoning.py:26-105
- **Finding:** Bootstrap must be invoked by caller; first-use raises. No `schema_version`.
- **Fix:** Lazy-ensure on first use; add migrations at startup.

## Finding 15 — Duplicate/overlapping feature surface between two files
- **Severity:** LOW
- **Category:** dead-code
- **Location:** code_intel.py vs code_reasoning.py
- **Finding:** Both implement `search_by_intent`; both implement long-method/deep-nesting/large-class detection with different thresholds/schemas; both persist technical debt (`technical_debt` vs `debt_items`).
- **Impact:** Two inconsistent parallel implementations; callers double-write DB state.
- **Fix:** Consolidate into one manager; delete the older duplicate.

## Systemic issues
- **Path handling insecure and incomplete**: `validate_path` accepts absolutes, no resolve() containment, no symlink/binary guard.
- **No resource limits on file I/O or AST work**: every analysis reads full file, parses full AST, walks it multiple times; combined with `rglob`, weaponizable for DoS.
- **Heuristics persist to DB without confidence gating or idempotency**: pattern/smell/opportunity detection is fire-and-forget INSERT; repeated scans bloat and contradict prior rows.
- **"Semantic search" is OR-of-LIKE with no ranking**.
- **Two modules overlap in scope and schema** — evidence of an unfinished refactor.

**Counts:** HIGH 3, MEDIUM 6, LOW 6 (total 15)
