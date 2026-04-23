# Audit Findings — Intelligence (security/compliance)
**Files reviewed:** src/taskbrew/intelligence/security_intel.py, src/taskbrew/intelligence/compliance.py, src/taskbrew/intelligence/_utils.py (helper)
**Reviewer:** audit-agent-08a

## Finding 1 — scan_dependencies ignores installed version, reports false positives
- **Severity:** H
- **Category:** correctness-bug
- **Location:** security_intel.py:244-278 (scan_dependencies) + 16-55 (_KNOWN_VULNERABILITIES)
- **Finding:** The vuln table carries `max_version` tuples (the vulnerable ceiling) and `fix_version` strings, but `scan_dependencies` never parses `version_spec` nor compares it against `max_version`; every package whose name is in the hardcoded dict is flagged, regardless of whether the pinned version is already patched.
- **Impact:** Users pinning `requests>=2.32` or `pyyaml==6.0.1` still receive a "critical" CVE report, eroding trust and hiding real issues in noise.
- **Fix:** Parse the version spec (e.g. with `packaging.version`/`specifiers`) and skip the finding when the installed version is `>= fix_version` or outside `max_version`.

## Finding 2 — "SAST" is regex grep, not AST analysis (contract/honesty)
- **Severity:** H
- **Category:** api-contract
- **Location:** security_intel.py:408-530 (run_sast), docstring claims "AST-based analysis"
- **Finding:** The docstring says "AST-based analysis for SQL injection, XSS, and path traversal" but the implementation is a handful of line-oriented `re.search` calls. No `ast.parse`, no data-flow, no taint tracking; it misses any multi-line statement, any indirect call (`cur=db.cursor(); cur.execute(f"...")` on separate lines), and any user-input name other than the four hardcoded tokens `user_input|request|args|params`.
- **Impact:** False sense of security. The hotspot question "does it actually perform SAST or return empty" — it runs regexes that will produce results on trivially contrived code but miss essentially all realistic vulnerabilities. Claiming AST analysis in the docstring is materially misleading.
- **Fix:** Either implement real AST walking (`ast.NodeVisitor` over call nodes, tracking `cursor.execute` arguments) or rewrite the docstring to say "regex-based heuristic scan" and downgrade the severity labels accordingly.

## Finding 3 — Unbounded regex on full-line input — ReDoS exposure
- **Severity:** M
- **Category:** perf
- **Location:** security_intel.py:85-108 (_SECRET_PATTERNS), 436-446 (SAST SQLi regex)
- **Finding:** `credential_assignment` and `generic_long_secret` patterns combine case-insensitive alternation, `\s*[=:]\s*`, and unbounded character classes (`[A-Za-z0-9_\-]{8,}`, `[A-Za-z0-9]{32,}`) against arbitrary file lines. The SAST SQLi regex `(?:execute|query|cursor)\s*\(.*(?:f['"]|%s|%d|\.format\(|\+\s*\w)` uses a greedy `.*` between anchors. A maliciously crafted (or minified/obfuscated) single long line can cause catastrophic backtracking.
- **Impact:** A single large one-line JSON/JS file under MAX_SCAN_FILE_SIZE (1 MB) can hang the scanner and block the async loop for seconds-to-minutes, since these regexes run synchronously.
- **Fix:** Anchor the alternations, bound the `.*` with `.{0,200}`, or pre-split on length/line heuristics; also run the scan in a thread via `asyncio.to_thread` to avoid blocking the event loop.

## Finding 4 — validate_path does not prevent escape via absolute paths or symlinks
- **Severity:** H
- **Category:** security
- **Location:** _utils.py:24-31 (validate_path), used by security_intel.py:290, 414, and scan_directory
- **Finding:** `validate_path` only checks for literal `..` segments after `os.path.normpath`. It does not reject absolute paths (`/etc/passwd` normalizes with no `..`), does not resolve symlinks, and does not assert the result stays under `self._project_dir`. Callers then do `os.path.join(self._project_dir, file_path)` — but `os.path.join(root, '/etc/passwd')` returns `/etc/passwd`.
- **Impact:** Any code path that takes a user-supplied `file_path` (e.g. via an agent tool call into `scan_for_secrets`/`run_sast`/`flag_security_changes`) can read arbitrary files on the host. The read content is then copied into the DB (secret_detections.pattern_matched, sast_findings.code_snippet) and exposed through `get_*` APIs — an information-disclosure primitive.
- **Fix:** Resolve both `project_dir` and the joined path with `Path.resolve()` and verify the resolved target `is_relative_to(project_root)`; reject absolute inputs up front.

## Finding 5 — flag_security_changes reads files without validate_path or size limit
- **Severity:** H
- **Category:** security
- **Location:** security_intel.py:609-637
- **Finding:** Unlike `scan_for_secrets`/`run_sast`, this method iterates `files_changed` and opens each path via `os.path.join(self._project_dir, file_path)` with no `validate_path`, no size check, and no encoding handling; then loads the entire content into memory and lowercases it.
- **Impact:** Caller-controlled absolute or `..`-containing paths read arbitrary files; very large files (gigabyte logs) OOM the process; binary files raise `UnicodeDecodeError` which is not in the `except (FileNotFoundError, OSError)` catch and crashes the flow.
- **Fix:** Route through `validate_path`, add `Path.resolve().is_relative_to(project_root)` check, cap read size at `MAX_SCAN_FILE_SIZE`, use `errors="ignore"` or read bytes, and catch `UnicodeDecodeError`.

## Finding 6 — Open() calls use platform default encoding
- **Severity:** M
- **Category:** error-handling
- **Location:** security_intel.py:291 (scan_for_secrets), 415 (run_sast), 621 (flag_security_changes), 186 (_parse_requirements.read_text), 205 (_parse_pyproject_deps.read_text)
- **Finding:** None of the file reads pass `encoding="utf-8"` or `errors="ignore"`. On Windows (cp1252) or files with non-ASCII bytes, `UnicodeDecodeError` raises out of the catch clause `(FileNotFoundError, OSError)`.
- **Impact:** Any non-UTF-8 or binary-ish file encountered during `scan_directory` aborts the entire scan (one bad `.json` kills the run).
- **Fix:** Pass `encoding="utf-8", errors="replace"` and/or add `UnicodeDecodeError` to the except tuple.

## Finding 7 — _parse_pyproject_deps uses fragile line scanner, skips `[project.optional-dependencies]`, and misparses PEP 508
- **Severity:** M
- **Category:** correctness-bug
- **Location:** security_intel.py:195-220
- **Finding:** The parser triggers `in_deps` on any line containing `dependencies` and `[` — so `[project.optional-dependencies]`, `[tool.something.dependencies]`, and a comment `# dependencies = [...]` all flip the flag. It exits on any line starting with `]`, which breaks on multi-line strings. Name regex `^([A-Za-z0-9_.-]+)(.*)?$` lowercases but does not canonicalize PEP 503 names (e.g. `PyYAML` vs `pyyaml` works by coincidence, but underscores vs hyphens do not).
- **Impact:** False negatives (missed vuln packages) and false positives (non-dependency strings matched as deps). Packages listed only under `optional-dependencies` are never scanned.
- **Fix:** Use `tomllib.loads(...)` (stdlib 3.11+) and read `project.dependencies` / `project.optional-dependencies` structurally; canonicalize names per PEP 503.

## Finding 8 — scan_directory duplicates scans and detections for files with multiple extensions or nested projects
- **Severity:** L
- **Category:** correctness-bug
- **Location:** security_intel.py:353-369
- **Finding:** For each extension in `_SCANNABLE_EXTENSIONS` the code calls `rglob(f"*{ext}")`, which hits the filesystem five times. It also does not deduplicate `.yaml`/`.yml` if a file is symlinked, and it does not exclude `.git`, `node_modules`, `venv`, `.venv`, or `__pycache__`. Every scan re-inserts detections with fresh IDs, so DB rows grow without bound on repeat runs.
- **Impact:** Repeated scans produce exponential DB bloat (same detections on every run), and scanning a project with a local venv scans every vendored library — slow and misleading.
- **Fix:** Single `rglob("*")` with extension set check, skip common vendor dirs, and use `INSERT OR IGNORE` with a uniqueness key of (file_path, line_number, secret_type).

## Finding 9 — add_vulnerability stores fix_version under the wrong key
- **Severity:** M
- **Category:** correctness-bug
- **Location:** security_intel.py:310-320
- **Finding:** The method takes `version_spec` (the version where the fix landed) and writes it as `"fix_version": version_spec, "max_version": None`. Subsequent calls to `scan_dependencies` ignore `max_version` (see Finding 1), but any future version-aware check would be broken because there is no ceiling recorded. Also, the in-memory `_KNOWN_VULNERABILITIES` is mutated — persisting across processes relies solely on module import, so added CVEs are lost on restart.
- **Impact:** User-added CVEs evaporate on process restart; schema is inconsistent with the hardcoded table above.
- **Fix:** Persist user-added vulns to a DB table and accept both `fix_version` and `max_version` (or derive one from the other); clarify the API contract.

## Finding 10 — compliance.check_file: unbounded user-regex compiled on every file scan — ReDoS + resource burn
- **Severity:** H
- **Category:** security
- **Location:** compliance.py:286-320 (check_file)
- **Finding:** Rules are user-provided regex strings (`add_rule` only calls `re.compile` once to validate, but does not cache the compiled object). `check_file` recompiles every rule per call and runs `finditer` against the full file content with no timeout and no complexity bound. A malicious or careless rule like `(a+)+$` will hang the event loop on adversarial input.
- **Impact:** Policy-bypass/DoS: anyone who can add a compliance rule can wedge the scanner on a target file. Rules are not sandboxed.
- **Fix:** Cache compiled patterns keyed by rule_id/check_pattern, run `check_file` in `asyncio.to_thread`, enforce a per-rule timeout (e.g. signal-based or `regex` module with timeout), and consider a vetted subset of regex syntax for rules.

## Finding 11 — Exemption workflow has no scope, expiry, or audit integrity
- **Severity:** M
- **Category:** security
- **Location:** compliance.py:395-420 (add_exemption), 296-302 (check_file skip)
- **Finding:** `add_exemption(rule_id, file_path, reason, approved_by)` simply writes a row; `approved_by` is a free-text string with no authentication, no signature, and no verification. There is no expiry, no revocation table, no `list_exemptions`/`revoke_exemption` API, and `file_path` is matched exact-string (no glob, no path normalization), so `./foo.py` vs `foo.py` behave differently. Anyone who can call `add_exemption` can silently disable any rule.
- **Impact:** This is the "policy-bypass" hotspot: the compliance engine records claimed approval but enforces nothing. An agent that can call the manager can self-exempt. The system *records* compliance, it does not *enforce* it.
- **Fix:** Require a signed token / explicit approver identity tied to auth, add `expires_at` + revocation, normalize `file_path`, add audit log of who requested the exemption vs who approved, and expose read/revoke APIs.

## Finding 12 — compliance_percentage math is meaningless
- **Severity:** M
- **Category:** correctness-bug
- **Location:** compliance.py:367-383 (get_compliance_status)
- **Finding:** `compliance_pct = (1 - total_violations / total_rules_checked) * 100`. `total_rules_checked` is the SUM of `rules_checked` across `compliance_checks` rows, i.e. rule×file events, not a distinct denominator. If 100 rules run against 10 files with 1 violation, the reported compliance is 99.9%; re-running the check doubles the denominator and reports 99.95% for the same state. The value trends monotonically toward 100% just by scanning more.
- **Impact:** Dashboards and alerting based on this percentage are fundamentally misleading and trivially gameable by re-scanning.
- **Fix:** Compute compliance as `1 - distinct_violations / (rules * files)` for the latest run per file (or per framework), and deduplicate historical runs.

## Finding 13 — record_check does not persist `details`, and check_file does not call record_check
- **Severity:** M
- **Category:** missed-impl
- **Location:** compliance.py:323-345 (record_check) and 274-320 (check_file)
- **Finding:** `compliance_checks.details` column exists in the schema but `record_check` never writes it (INSERT omits the column). More importantly, `check_file` returns violations but never calls `record_check`, so `compliance_checks` is only populated if a caller happens to invoke it separately. Consequently `get_compliance_status` typically reports `files_checked=0`.
- **Impact:** Dead schema column and disconnected plumbing: the scanner can return violations, yet the status summary shows zero activity. Looks functional in isolated tests but not in integrated flow.
- **Fix:** Have `check_file` call `record_check` with a serialized `details` JSON of violations; either write `details` or drop the column.

## Finding 14 — assess_risk mutates its breakdown dict with unknown levels
- **Severity:** L
- **Category:** correctness-bug
- **Location:** compliance.py:190-207 (assess_risk)
- **Finding:** `breakdown[level] = breakdown.get(level, 0) + 1` will happily add new keys if the DB holds a `risk_level` outside `_RISK_WEIGHTS` (possible because the DB column has no CHECK constraint — only `add_threat` enforces the enum). The returned breakdown then includes unexpected keys while `risk_score` adds 0 for them — inconsistent totals.
- **Impact:** Non-deterministic output shape; downstream consumers may assume the four keys.
- **Fix:** Add a CHECK constraint on `threat_entries.risk_level` (and `compliance_rules.severity`), and guard `breakdown` to the known set.

## Finding 15 — Missing subprocess/bandit/gitleaks integration (hotspot claim check)
- **Severity:** Informational
- **Category:** api-contract
- **Location:** security_intel.py (whole file)
- **Finding:** The audit hotspot hypothesised subprocess calls to `bandit`/`gitleaks` with command-injection risk. There are **no** subprocess, shell, or external-tool invocations anywhere in `security_intel.py` or `compliance.py`. So: no command-injection surface via those tools, but also no real SAST or real secret scanner — the module is *entirely* self-contained regex+hardcoded-lookup logic.
- **Impact:** The claimed capability ("vulnerability scanning, secret detection, SAST, license compliance") is implemented at toy-demo depth: ~4 CVEs hardcoded, ~15 licenses hardcoded, 4 regex secret patterns, 4 regex SAST checks.
- **Fix:** Either integrate a real scanner (pip-audit, osv-scanner, bandit, gitleaks) via `asyncio.create_subprocess_exec` (list form, no shell) with timeouts and validated output parsing, or relabel the module as "heuristic screening" and reduce the severities it produces.

## Systemic issues
- **Honesty verdict on security_intel.py:** Mostly aspirational. The module *does* run regex-based scans end to end (it is not returning hardcoded empty lists), so it passes the minimum "does something" bar. But the docstring-level claims ("AST-based analysis", "Dependency Vulnerability Scanning") oversell what is in practice a 4-CVE dictionary lookup and four one-line regexes. No version comparison, no taint analysis, no external scanner. For a user who enables this expecting protection, the gap between claim and capability is the primary risk.
- **Compliance engine records claims, does not enforce them.** Exemptions are unsigned free-text rows; `check_file` does not auto-record; compliance percentage is a monotone-to-100% metric; there is no blocking gate. The name "rule engine" implies enforcement — it is a logging layer.
- **Path handling is shared and shared-broken.** `validate_path` in `_utils.py` is a blocklist that rejects only `..`; absolute paths, symlinks, and sibling escapes pass. Every file-reading intelligence module that imports it inherits this weakness (see also Finding 4/5). Fix it once in `_utils.py` for an across-the-board hardening.
- **Regex-on-async patterns without threading or timeouts.** Multiple hot paths (`scan_for_secrets`, `run_sast`, `check_file`) run potentially expensive regex against user-controlled content on the event loop. Under pathological input this will stall the whole async pipeline. A single `asyncio.to_thread` wrapper plus bounded patterns would neutralise most of the ReDoS surface.
- **DB row explosion on repeat runs.** No module uses INSERT OR IGNORE / unique keys on (file_path, line, finding_type), so each scan appends duplicates. Queries like `get_vulnerabilities` / `get_sast_findings` will grow linearly with scan count and mislead any trend analysis.
