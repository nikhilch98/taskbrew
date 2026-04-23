# Audit Findings — Tools / Worktree / MCP

**Files reviewed:** tools/__init__.py, git_tools.py, intelligence_tools.py, task_tools.py, worktree_manager.py
**Reviewer:** audit-agent-05

## Finding 1 — `agent_name` path-traversal in worktree paths
- **Severity:** CRITICAL
- **Category:** security
- **Location:** worktree_manager.py:77
- **Finding:** `os.path.join(self.worktree_base, agent_name)` with zero validation; `agent_name="/etc"` or `".."` escapes the base, then flows into `shutil.rmtree(..., ignore_errors=True)` on cleanup.
- **Impact:** Arbitrary filesystem overwrite / recursive delete if agent_name is LLM-supplied.
- **Fix:** Regex-validate `[A-Za-z0-9_-]+` and `os.path.commonpath` realpath containment check.

## Finding 2 — `prune_stale` rm -rf every dir in worktree_base not in process-local dict
- **Severity:** CRITICAL
- **Category:** security
- **Location:** worktree_manager.py:136-142
- **Finding:** After restart the in-memory `_worktrees` dict is empty, so prune_stale deletes every sibling directory in the configured base. Misconfigured base (e.g. `~` or repo root) = mass data loss.
- **Impact:** Mass deletion of user data on restart with stale-prune job.
- **Fix:** Source truth from `git worktree list --porcelain` + realpath containment.

## Finding 3 — Branch-name flag-injection in WorktreeManager
- **Severity:** HIGH
- **Category:** security
- **Location:** worktree_manager.py:106-108
- **Finding:** `git worktree add ... -b <branch_name>` runs without invoking `git_tools.sanitize_branch_name`; leading-hyphen branch (e.g. `--upload-pack=...`) is parsed as a git flag = arbitrary command execution.
- **Impact:** Git-option-injection RCE via LLM-generated branch names.
- **Fix:** Call `sanitize_branch_name` and/or insert `--` arg terminator before the branch name.

## Finding 4 — Symlink TOCTOU on shutil.rmtree fallback with ignore_errors=True
- **Severity:** HIGH
- **Category:** security
- **Location:** worktree_manager.py:87-90, 113-115, 140
- **Finding:** `shutil.rmtree(path, ignore_errors=True)` used on a path that may be a symlink; removes the link target.
- **Impact:** Symlink-swap between check and rmtree deletes arbitrary files.
- **Fix:** `realpath` containment + refuse to delete symlinks; use `os.lstat`.

## Finding 5 — No timeout on any subprocess communicate(); GIT_TERMINAL_PROMPT not set
- **Severity:** HIGH
- **Category:** resource-leak
- **Location:** git_tools.py:82, 103; worktree_manager.py:32-38
- **Finding:** Subprocess calls have no `timeout=`; no `GIT_TERMINAL_PROMPT=0` in env.
- **Impact:** A git prompting for credentials freezes the orchestrator indefinitely.
- **Fix:** Add timeouts; set `GIT_TERMINAL_PROMPT=0`.

## Finding 6 — Concurrent-worktree race with no lock on create/cleanup
- **Severity:** HIGH
- **Category:** concurrency
- **Location:** worktree_manager.py:69-110
- **Finding:** No lock around the create-or-reuse sequence; two agents claiming adjacent tasks can race on the same path.
- **Impact:** Directory collisions, partial cleanup, lost work.
- **Fix:** Per-base-path asyncio.Lock or fcntl advisory lock.

## Finding 7 — get_diff_summary / create_feature_branch run without cwd=
- **Severity:** HIGH
- **Category:** correctness-bug
- **Location:** git_tools.py:74-109
- **Finding:** Git commands launched without `cwd=` touch the main checkout, not the agent's worktree.
- **Impact:** Operations apply to wrong repo; diffs report unrelated changes.
- **Fix:** Thread the worktree path through and pass `cwd=worktree`.

## Finding 8 — MCP task_tools has zero auth; forwards LLM-supplied assigned_by
- **Severity:** HIGH
- **Category:** security
- **Location:** task_tools.py:23-214
- **Finding:** The MCP task server forwards any LLM-supplied `assigned_by` to the HTTP API; no role/token verification.
- **Impact:** Role impersonation — agents can claim to be the PM, verifier, etc.
- **Fix:** Bind each MCP instance to an agent identity; ignore body-supplied `assigned_by`.

## Finding 9 — intelligence_tools duplicates implementations (dead code + drift)
- **Severity:** MEDIUM
- **Category:** dead-code
- **Location:** intelligence_tools.py:18-86 vs 89-197
- **Finding:** `register_intelligence_tools` (L18-86) is shadowed by redefined bodies in `build_intelligence_tools_server` (L89-197).
- **Impact:** Future fixes will drift; one copy is dead.
- **Fix:** Delete the older copy.

## Finding 10 — _LazyDB never closes the Database
- **Severity:** MEDIUM
- **Category:** resource-leak
- **Location:** intelligence_tools.py:120-126, 139-197
- **Finding:** `Database` created on first access but never `close()`d on shutdown.
- **Impact:** WAL-corruption risk on ungraceful shutdown; file handle leak.
- **Fix:** Register close() with MCP server lifecycle hook.

## Finding 11 — No input validation on intelligence tool args
- **Severity:** MEDIUM
- **Category:** security
- **Location:** intelligence_tools.py:39-48, 61-70, 137-170
- **Finding:** No role whitelist, length cap, or path sanitization on args; `check_impact`'s `file_paths` flows into `analyzer.trace_dependencies`.
- **Impact:** Path-injection via LLM-supplied tool args.
- **Fix:** Validate at tool boundary.

## Finding 12 — list_worktrees returns only in-memory dict
- **Severity:** MEDIUM
- **Category:** correctness-bug
- **Location:** worktree_manager.py:118-123
- **Finding:** After restart the dict is empty; `list_worktrees` reports nothing even though disk worktrees exist.
- **Impact:** Silent state lies; cleanup jobs miss real worktrees.
- **Fix:** Hydrate from `git worktree list --porcelain` on init.

## Finding 13 — sanitize_branch_name regex lets git-invalid names through
- **Severity:** LOW
- **Category:** correctness-bug
- **Location:** git_tools.py:10-53
- **Finding:** Accepts `.lock` suffix, trailing `/`, bare `HEAD`, bare `@`; final charclass anchored with `$` only stops at newlines.
- **Impact:** Subsequent git ops fail with cryptic errors.
- **Fix:** Use git's official validation rules (`git check-ref-format`).

## Finding 14 — urllib.error bodies read with no size cap / default utf-8 decode
- **Severity:** LOW
- **Category:** resource-leak
- **Location:** task_tools.py:104, 146, 170, 196, 212
- **Finding:** Large/invalid error bodies crash tool handlers on decode.
- **Fix:** `.read(max_bytes)` + `errors='replace'`.

## Finding 15 — No User-Agent/retry/pooling on urllib.request calls
- **Severity:** LOW
- **Category:** perf
- **Location:** task_tools.py:89, 122, 160, 183, 203
- **Fix:** Use httpx.AsyncClient with pooling, retries, and a UA header.

## Systemic issues across this slice
- **Validation lives in the wrong module:** `sanitize_branch_name` is in `git_tools.py` but the higher-risk `WorktreeManager` never imports it, and `agent_name` has no validator anywhere.
- **Every subprocess call is timeoutless and inherits parent env** (no `GIT_TERMINAL_PROMPT=0`); one stuck git freezes the orchestrator.
- **`shutil.rmtree(..., ignore_errors=True)` used three times** in worktree_manager.py with no realpath containment and no symlink refusal — largest security surface here.
- **In-memory `_worktrees` dict treated as ground truth** by `list_worktrees` and `prune_stale`; after restart it lies and deletes real directories.
- **MCP subprocess servers have no authentication** — trust every LLM-supplied role/path, enabling cross-role impersonation and data corruption.
- **intelligence_tools.py maintains two parallel tool-set copies** — guarantees future divergence on any fix.

**Counts:** CRITICAL 2, HIGH 6, MEDIUM 4, LOW 3 (total 15)
