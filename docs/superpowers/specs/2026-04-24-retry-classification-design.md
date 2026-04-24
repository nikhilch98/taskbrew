# Retry Classification

**Status:** Approved 2026-04-24
**Author:** Brainstormed with user through the brainstorming skill.

## Problem

The retry loop in `agent_loop.py` currently treats every exception
identically:

```python
except Exception as e:
    if attempt < MAX_RETRIES:
        await asyncio.sleep(RETRY_BASE_DELAY * (3 ** attempt))  # 5s, 15s, 45s
        # worktree reset + continue
    else:
        raise
```

For a transient failure (rate limit, network blip) this is correct.
For a **hard failure** (schema violation, invalid tool call, missing
required field) retry with the same prompt and same code gets the
same result -- two wasted attempts burning `2 × task_time` of LLM
work plus `5 + 15 = 20 s` of pure backoff before escalating.

Worst case: a hard error on a 2-minute task wastes ~7 minutes and
~3× the token spend before the user sees anything.

## Design

Add a classifier that distinguishes retryable (transient) from
non-retryable (hard) errors, and short-circuit retry on the
latter.

**Default is non-retryable.** We opt in known-transient types
rather than opt out; unknown exceptions fail fast. This is the
load-bearing autonomy change -- today hidden bugs masquerade as
flaky agents; tomorrow they surface immediately.

### Classifier

New module-level function `_is_retryable(exc)` in
`src/taskbrew/agents/agent_loop.py`. Two-layer test:

**Layer 1 — exception type allowlist.** Typed exceptions from the
SDKs we actually use:

- Anthropic SDK: `RateLimitError`, `APIConnectionError`,
  `APITimeoutError`, `InternalServerError` (503/504).
- Python stdlib: `ConnectionError` (and subclasses like
  `ConnectionResetError`), `OSError` for socket-level issues
  that surface as `[Errno 104] Connection reset`.

Imports are deferred so `agent_loop` doesn't hard-fail when the
Anthropic SDK isn't installed; the stdlib set still works.

**Layer 2 — message substring fallback.** Some call paths wrap
transient errors as bare `Exception("429 rate limit")` or
`RuntimeError("503 Service Unavailable")` -- especially the Claude
Code SDK subprocess and the Gemini CLI. A lower-cased substring
match on `str(exc)` against:

```
"rate limit", "429", "503", "504", "connection reset",
"connection refused"
```

...catches these without needing to know each SDK's exception
hierarchy.

**Explicitly NOT retryable** (previously retried 3×, now fail-fast):

- `ValueError`, `TypeError`, `AttributeError`, `KeyError` —
  code / schema bugs.
- `json.JSONDecodeError` — could be transient but empirically
  signals a real response-shape issue.
- `anthropic.AuthenticationError`, `anthropic.BadRequestError` —
  credential / caller errors.
- Anything not in the allowlist and not matching the substring list.

### Retry loop integration

The `except Exception as e:` branch in the per-attempt loop gains
a single condition:

```python
except Exception as e:
    retryable = _is_retryable(e)
    if attempt < MAX_RETRIES and retryable:
        # existing backoff + jitter + worktree reset (unchanged)
        ...
    else:
        if not retryable:
            task_logger.error(
                "Task %s failed with non-retryable error %s: %s — "
                "skipping remaining %d retries, failing immediately",
                task["id"], type(e).__name__, e,
                MAX_RETRIES - attempt,
            )
        raise
```

The `type(e).__name__` + explicit "skipping N retries" log is
deliberately loud -- operators who rely on retry output as their
error signal now see a clearer alternative.

**No behaviour change on the retryable path.** Same exponential
backoff (base 3, ±50% jitter), same worktree reset between
attempts.

### Where the classifier lives

`_is_retryable` is a module-level function in `agent_loop.py`,
alongside `MAX_RETRIES` and `RETRY_BASE_DELAY`. It's retry policy,
not general utility, and belongs with the policy it serves.
Moving it to `utils` would invite misuse.

## Testing

Add to `tests/test_agent_loop.py`:

- **Parametrised classifier unit tests**: `RateLimitError → True`,
  `ConnectionResetError → True`, bare `Exception("429 rate limit")`
  → `True`, `RuntimeError("503 Service Unavailable")` → `True`,
  `ValueError → False`, `KeyError → False`, `json.JSONDecodeError`
  → `False`, unknown `RuntimeError("something weird")` → `False`.
- **Retry-loop honours non-retryable**: monkeypatch `_is_retryable`
  to `False`, make `execute_task` raise, assert `fail_task` is
  called after 1 attempt.
- **Retry-loop still retries on retryable**: monkeypatch
  `_is_retryable` to `True`, assert all `MAX_RETRIES + 1` attempts
  fire before `fail_task`.

## Rollout

- No schema change, no config change, no prompt change.
- **Behaviour change is user-visible.** Tasks that previously
  failed after 3 × (task_time + backoff) on a hard error now
  fail after 1 × task_time. Operators who tuned timeouts against
  the old retry-inclusive wall-clock need to know. One CHANGELOG
  entry covers it.
- **Risk.** A false-positive non-retryable classification turns a
  truly transient failure into a hard failure one attempt early.
  Mitigations: the type allowlist covers the Anthropic SDK's
  documented transient set; the substring fallback is generous;
  the unknown-exception case is worst-case equivalent to "fail
  after 1 attempt" instead of "fail after 3" -- a regression of
  at most 2 retries on whatever rare exception we missed.

## Decisions captured

- Two buckets (retryable / non-retryable), not three-way (fast /
  slow / none). Rate-limit handling stays on the existing
  exponential backoff.
- Default is non-retryable. Opt in known-transient types.
- Classifier lives in `agent_loop.py`, not a shared utility.
- Substring fallback supplements the typed allowlist for SDK
  wrappers that drop exception type information.
- Unknown exceptions fail-fast; two retries skipped is cheaper
  than two retries hiding a real bug.
