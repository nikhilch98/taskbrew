"""Claude Code usage monitor.

Reads:
  - ~/.claude/stats-cache.json          → weekly/all-time aggregated stats
  - ~/.claude/projects/*/SESSION.jsonl  → per-message token usage for live sessions
  - /api/oauth/profile (Anthropic)      → plan type, rate tier, extra usage flag
  - Interactive `claude /usage` command  → actual plan usage percentages & reset times
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time as _time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from fastapi import APIRouter

logger = logging.getLogger(__name__)

router = APIRouter()

CLAUDE_DIR = Path.home() / ".claude"
STATS_CACHE = CLAUDE_DIR / "stats-cache.json"
PROJECTS_DIR = CLAUDE_DIR / "projects"
PROFILE_API = "https://api.anthropic.com/api/oauth/profile"

MODEL_DISPLAY_NAMES = {
    "claude-opus-4-6": "Opus 4.6",
    "claude-sonnet-4-6": "Sonnet 4.6",
    "claude-opus-4-5-20251101": "Opus 4.5",
    "claude-sonnet-4-5-20250929": "Sonnet 4.5",
    "claude-haiku-4-5-20251001": "Haiku 4.5",
}

MODEL_COLORS = {
    "claude-opus-4-6": "#a855f7",
    "claude-sonnet-4-6": "#6366f1",
    "claude-opus-4-5-20251101": "#8b5cf6",
    "claude-sonnet-4-5-20250929": "#3b82f6",
    "claude-haiku-4-5-20251001": "#06b6d4",
}


def _display_name(model_id: str) -> str:
    return MODEL_DISPLAY_NAMES.get(model_id, model_id)


def _model_color(model_id: str) -> str:
    return MODEL_COLORS.get(model_id, "#6b7280")


def _read_stats() -> dict:
    if not STATS_CACHE.exists():
        return {}
    try:
        return json.loads(STATS_CACHE.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


# ------------------------------------------------------------------
# /usage scraper — launches interactive Claude Code to get plan limits
# ------------------------------------------------------------------

_usage_cache: dict | None = None
_usage_cache_ts: float = 0
_USAGE_CACHE_TTL = 120  # seconds


def _strip_ansi(raw: bytes) -> str:
    """Remove ANSI escape sequences from terminal output."""
    text = re.sub(rb"\x1b\[[^a-zA-Z]*[a-zA-Z]", b" ", raw)
    text = re.sub(rb"\x1b\][^\x07]*\x07", b" ", text)
    text = re.sub(rb"\x1b\[\?[0-9;]*[a-zA-Z]", b" ", text)
    return re.sub(r"\s+", " ", text.decode("utf-8", errors="replace"))


def _parse_usage_text(text: str) -> dict:
    """Parse the cleaned /usage output into structured data."""
    result: dict = {"limits": []}

    # Split text into segments by known labels, then parse each
    label_re = re.compile(
        r"(Current session|Current week \([^)]+\)|Extra usage)"
    )
    splits = list(label_re.finditer(text))
    for i, m in enumerate(splits):
        label = m.group(1)
        start = m.end()
        end = splits[i + 1].start() if i + 1 < len(splits) else len(text)
        segment = text[start:end]

        pct_m = re.search(r"(\d+)%\s*used", segment)
        if not pct_m:
            continue
        pct = int(pct_m.group(1))
        entry: dict = {"label": label, "pct_used": pct}

        # Look for reset info within this segment only
        # Note: terminal output may have partial text like "Rese s" instead of "Resets"
        reset_m = re.search(r"Rese\w*\s+(.+?\([^)]+\))", segment)
        if reset_m:
            resets_text = reset_m.group(1).strip()
            # Clean up partial text artifacts (e.g. leading "s " from "Rese s")
            resets_text = re.sub(r"^[a-z]\s+", "", resets_text)
            entry["resets"] = resets_text

        result["limits"].append(entry)

    # Extra usage spending: "$68.38 / $50.00 spent"
    spend_match = re.search(r"\$([0-9.]+)\s*/\s*\$([0-9.]+)\s*spent", text)
    if spend_match:
        result["extra_usage_spent"] = float(spend_match.group(1))
        result["extra_usage_limit"] = float(spend_match.group(2))

    return result


def _run_usage_cli_sync() -> dict | None:
    """Synchronous helper that uses pexpect to run Claude /usage.

    Must run in a thread because pexpect is blocking.
    """
    import shutil

    try:
        import pexpect
    except ImportError:
        logger.warning("pexpect not installed, cannot fetch /usage")
        return None

    claude_bin = shutil.which("claude") or str(
        Path.home() / ".local" / "bin" / "claude"
    )

    env = dict(os.environ)
    for key in [
        "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT",
        "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS",
        "CLAUDE_CODE_MAX_OUTPUT_TOKENS",
    ]:
        env.pop(key, None)

    try:
        child = pexpect.spawn(
            claude_bin, ["--no-chrome"],
            env=env,
            timeout=5,
            dimensions=(50, 120),
            encoding=None,  # binary mode
        )

        # Wait for initial UI
        for _ in range(4):
            try:
                child.expect(r".+", timeout=3)
            except (pexpect.TIMEOUT, pexpect.EOF):
                pass

        # Type /usage and wait for autocomplete
        child.send("/usage")
        _time.sleep(2)
        try:
            child.expect(r".+", timeout=3)
        except (pexpect.TIMEOUT, pexpect.EOF):
            pass

        # Press Enter to select
        child.send("\r")

        # Collect output for ~15 seconds
        output = b""
        for _ in range(15):
            try:
                child.expect(r".+", timeout=1)
                output += child.before + child.after
            except pexpect.TIMEOUT:
                pass
            except pexpect.EOF:
                break

        # Exit
        child.send("\x1b")  # Escape
        _time.sleep(0.5)
        child.send("/exit\r")
        try:
            child.expect(pexpect.EOF, timeout=5)
        except (pexpect.TIMEOUT, pexpect.EOF):
            pass
        child.close()

        if not output:
            return None

        text = _strip_ansi(output)
        parsed = _parse_usage_text(text)
        return parsed if parsed.get("limits") else None

    except Exception as e:
        logger.error("Failed to run Claude /usage: %s", e)
        return None


async def _fetch_usage_via_cli() -> dict | None:
    """Run interactive Claude Code with /usage command and parse output.

    Cached for 2 minutes to avoid spawning too many processes.
    """
    global _usage_cache, _usage_cache_ts

    now = _time.time()
    if _usage_cache and now - _usage_cache_ts < _USAGE_CACHE_TTL:
        return _usage_cache

    try:
        loop = asyncio.get_event_loop()
        result = await asyncio.wait_for(
            loop.run_in_executor(None, _run_usage_cli_sync),
            timeout=50,
        )

        if result:
            _usage_cache = result
            _usage_cache_ts = now
            return result

        return _usage_cache

    except asyncio.TimeoutError:
        logger.warning("Claude /usage timed out")
        return _usage_cache
    except Exception as e:
        logger.error("Failed to run Claude /usage: %s", e)
        return _usage_cache


# ------------------------------------------------------------------
# Gemini /usage scraper
# ------------------------------------------------------------------

GEMINI_MODEL_DISPLAY_NAMES = {
    "gemini-3.1-pro-preview": "Gemini 3.1 Pro",
    "gemini-3-flash-preview": "Gemini 3 Flash",
    "gemini-2.5-pro": "Gemini 2.5 Pro",
    "gemini-2.5-flash": "Gemini 2.5 Flash",
    "gemini-2.0-flash": "Gemini 2.0 Flash",
}

GEMINI_MODEL_COLORS = {
    "gemini-3.1-pro-preview": "#4285f4",
    "gemini-3-flash-preview": "#34a853",
    "gemini-2.5-pro": "#1a73e8",
    "gemini-2.5-flash": "#0d652d",
    "gemini-2.0-flash": "#137333",
}

_gemini_usage_cache: dict | None = None
_gemini_usage_cache_ts: float = 0


def _parse_gemini_usage_text(text: str) -> dict:
    """Parse the cleaned Gemini CLI /usage output into structured data.

    Gemini /usage output format:
      Session Stats:  <N> requests, <time> duration
      Performance:  avg <Nms> response time
      Model Usage:
        Model           Reqs  Usage remaining  Resets
        gemini-xxx       N       XX%           in Xh Xm
    """
    result: dict = {"session": {}, "models": []}

    # Session stats
    sess_m = re.search(r"(\d+)\s*requests?,\s*([\d.]+\s*\w+)\s*duration", text)
    if sess_m:
        result["session"]["requests"] = int(sess_m.group(1))
        result["session"]["duration"] = sess_m.group(2).strip()

    # Performance
    perf_m = re.search(r"avg\s*([\d.]+)\s*ms\s*response", text)
    if perf_m:
        result["session"]["avg_response_ms"] = float(perf_m.group(1))

    # Model usage table rows
    # Pattern: model_name  reqs  usage_remaining%  resets_info
    model_re = re.compile(
        r"(gemini[\w.-]+)\s+(\d+)\s+(\d+)%\s+(in\s+.+?)(?=gemini|\Z)",
        re.IGNORECASE,
    )
    for m in model_re.finditer(text):
        model_id = m.group(1).strip()
        reqs = int(m.group(2))
        remaining_pct = int(m.group(3))
        resets = m.group(4).strip()
        # Clean trailing whitespace/noise from resets
        resets = re.sub(r"\s+", " ", resets).strip()
        result["models"].append({
            "model_id": model_id,
            "display_name": GEMINI_MODEL_DISPLAY_NAMES.get(model_id, model_id),
            "color": GEMINI_MODEL_COLORS.get(model_id, "#4285f4"),
            "requests": reqs,
            "remaining_pct": remaining_pct,
            "used_pct": 100 - remaining_pct,
            "resets": resets,
        })

    return result


def _run_gemini_usage_cli_sync() -> dict | None:
    """Synchronous helper that uses pexpect to run Gemini /usage."""
    import shutil

    try:
        import pexpect
    except ImportError:
        logger.warning("pexpect not installed, cannot fetch Gemini /usage")
        return None

    gemini_bin = shutil.which("gemini") or "/opt/homebrew/bin/gemini"

    env = dict(os.environ)

    try:
        child = pexpect.spawn(
            gemini_bin, [],
            env=env,
            timeout=5,
            dimensions=(50, 120),
            encoding=None,
        )

        # Wait for initial UI
        for _ in range(4):
            try:
                child.expect(r".+", timeout=3)
            except (pexpect.TIMEOUT, pexpect.EOF):
                pass

        # Type /usage and wait
        child.send("/usage")
        _time.sleep(2)
        try:
            child.expect(r".+", timeout=3)
        except (pexpect.TIMEOUT, pexpect.EOF):
            pass

        # Press Enter
        child.send("\r")

        # Collect output for ~15 seconds
        output = b""
        for _ in range(15):
            try:
                child.expect(r".+", timeout=1)
                output += child.before + child.after
            except pexpect.TIMEOUT:
                pass
            except pexpect.EOF:
                break

        # Exit
        child.send("\x1b")
        _time.sleep(0.5)
        child.send("/exit\r")
        try:
            child.expect(pexpect.EOF, timeout=5)
        except (pexpect.TIMEOUT, pexpect.EOF):
            pass
        child.close()

        if not output:
            return None

        text = _strip_ansi(output)
        parsed = _parse_gemini_usage_text(text)
        return parsed if parsed.get("models") else None

    except Exception as e:
        logger.error("Failed to run Gemini /usage: %s", e)
        return None


async def _fetch_gemini_usage_via_cli() -> dict | None:
    """Run interactive Gemini CLI with /usage and parse output.

    Cached for 2 minutes.
    """
    global _gemini_usage_cache, _gemini_usage_cache_ts

    now = _time.time()
    if _gemini_usage_cache and now - _gemini_usage_cache_ts < _USAGE_CACHE_TTL:
        return _gemini_usage_cache

    try:
        loop = asyncio.get_event_loop()
        result = await asyncio.wait_for(
            loop.run_in_executor(None, _run_gemini_usage_cli_sync),
            timeout=50,
        )

        if result:
            _gemini_usage_cache = result
            _gemini_usage_cache_ts = now
            return result

        return _gemini_usage_cache

    except asyncio.TimeoutError:
        logger.warning("Gemini /usage timed out")
        return _gemini_usage_cache
    except Exception as e:
        logger.error("Failed to run Gemini /usage: %s", e)
        return _gemini_usage_cache


@router.get("/api/usage/gemini/summary")
async def get_gemini_usage_summary():
    """Return Gemini CLI usage data for the dashboard."""
    usage = await _fetch_gemini_usage_via_cli()
    return {
        "available": usage is not None and bool(usage.get("models")),
        "usage": usage,
    }


_profile_cache: dict = {}
_profile_ts: float = 0


async def _fetch_profile() -> dict | None:
    """Fetch plan info from Anthropic OAuth profile endpoint.

    Uses CLAUDE_CODE_SESSION_ACCESS_TOKEN env var. Cached for 5 minutes.
    """
    global _profile_cache, _profile_ts
    import time

    now = time.time()
    if _profile_cache and now - _profile_ts < 300:
        return _profile_cache

    token = os.environ.get("CLAUDE_CODE_SESSION_ACCESS_TOKEN", "")
    if not token:
        return None
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(
                PROFILE_API,
                headers={"Authorization": f"Bearer {token}"},
            )
            if resp.status_code == 200:
                _profile_cache = resp.json()
                _profile_ts = now
                return _profile_cache
    except Exception:
        pass
    return _profile_cache or None


def _week_range(ref: datetime | None = None) -> tuple[str, str]:
    d = ref or datetime.now()
    monday = d - timedelta(days=d.weekday())
    sunday = monday + timedelta(days=6)
    return monday.strftime("%Y-%m-%d"), sunday.strftime("%Y-%m-%d")


def _find_active_sessions() -> list[Path]:
    """Find the most recently modified JSONL session files (likely active)."""
    if not PROJECTS_DIR.exists():
        return []
    jsonl_files: list[tuple[float, Path]] = []
    for proj_dir in PROJECTS_DIR.iterdir():
        if not proj_dir.is_dir():
            continue
        for f in proj_dir.glob("*.jsonl"):
            jsonl_files.append((f.stat().st_mtime, f))
    jsonl_files.sort(key=lambda x: x[0], reverse=True)
    return [f for _, f in jsonl_files[:5]]


def _parse_session(path: Path) -> dict:
    """Parse a JSONL session file for token usage by model."""
    models: dict[str, dict] = {}
    first_ts = last_ts = None
    msg_count = 0

    try:
        with open(path) as f:
            for line in f:
                try:
                    d = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue

                ts = d.get("timestamp")
                if ts:
                    if first_ts is None:
                        first_ts = ts
                    last_ts = ts

                if d.get("type") != "assistant" or "message" not in d:
                    continue

                msg_count += 1
                msg = d["message"]
                usage = msg.get("usage", {})
                model = msg.get("model", "unknown")
                if model == "<synthetic>":
                    continue

                if model not in models:
                    models[model] = {
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "cache_read": 0,
                        "cache_create": 0,
                        "messages": 0,
                    }
                m = models[model]
                m["input_tokens"] += usage.get("input_tokens", 0)
                m["output_tokens"] += usage.get("output_tokens", 0)
                m["cache_read"] += usage.get("cache_read_input_tokens", 0)
                m["cache_create"] += usage.get("cache_creation_input_tokens", 0)
                m["messages"] += 1
    except OSError:
        pass

    return {
        "file": path.name,
        "first_ts": first_ts,
        "last_ts": last_ts,
        "messages": msg_count,
        "models": models,
    }


# ------------------------------------------------------------------
# GET /api/usage/summary
# ------------------------------------------------------------------


def _hour_window_info() -> dict:
    """Compute 5-hour rolling window info for rate limit context.

    Claude Max uses a 5-hour rolling window for rate limits.
    """
    now = datetime.now(timezone.utc)
    # Window started 5h ago
    window_start = now - timedelta(hours=5)
    # Next full reset is 5h from now (but the window slides)
    window_end = now + timedelta(hours=5)
    return {
        "window_hours": 5,
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "now_utc": now.isoformat(),
    }


@router.get("/api/usage/summary")
async def get_usage_summary():
    """Return usage data for the popdown: current session + weekly stats + plan info."""
    stats = _read_stats()
    profile = await _fetch_profile()

    # ---- Plan info ----
    plan_data = None
    if profile:
        acct = profile.get("account", {})
        org = profile.get("organization", {})
        plan_type = "Claude Max" if acct.get("has_claude_max") else (
            "Claude Pro" if acct.get("has_claude_pro") else "Free"
        )
        plan_data = {
            "plan": plan_type,
            "rate_tier": org.get("rate_limit_tier", ""),
            "extra_usage": org.get("has_extra_usage_enabled", False),
            "subscription": org.get("subscription_status", ""),
        }

    # ---- Current session (most recently modified JSONL) ----
    session_data = None
    active = _find_active_sessions()
    if active:
        parsed = _parse_session(active[0])
        total_out = sum(m["output_tokens"] for m in parsed["models"].values())
        total_in = sum(m["input_tokens"] for m in parsed["models"].values())
        total_cache = sum(
            m["cache_read"] + m["cache_create"] for m in parsed["models"].values()
        )

        model_list = []
        for mid, mu in sorted(
            parsed["models"].items(), key=lambda x: x[1]["output_tokens"], reverse=True
        ):
            model_list.append({
                "model_id": mid,
                "display_name": _display_name(mid),
                "color": _model_color(mid),
                "output_tokens": mu["output_tokens"],
                "input_tokens": mu["input_tokens"],
                "cache_read": mu["cache_read"],
                "messages": mu["messages"],
            })

        session_data = {
            "start": parsed["first_ts"],
            "last_active": parsed["last_ts"],
            "messages": parsed["messages"],
            "output_tokens": total_out,
            "input_tokens": total_in,
            "cache_tokens": total_cache,
            "models": model_list,
        }

    # ---- Weekly stats from stats-cache.json ----
    week_start, week_end = _week_range()
    week_data: dict = {"start": week_start, "end": week_end, "models": [], "total_tokens": 0}

    if stats:
        daily_model_tokens = stats.get("dailyModelTokens", [])
        week_entries = [
            d for d in daily_model_tokens if week_start <= d["date"] <= week_end
        ]
        tokens_by_model: dict[str, int] = {}
        for day in week_entries:
            for model, tokens in day.get("tokensByModel", {}).items():
                tokens_by_model[model] = tokens_by_model.get(model, 0) + tokens

        total = sum(tokens_by_model.values())
        week_data["total_tokens"] = total

        sonnet_total = 0
        for mid, tok in sorted(tokens_by_model.items(), key=lambda x: x[1], reverse=True):
            pct = (tok / total * 100) if total > 0 else 0
            week_data["models"].append({
                "model_id": mid,
                "display_name": _display_name(mid),
                "color": _model_color(mid),
                "tokens": tok,
                "percentage": round(pct, 1),
            })
            if "sonnet" in mid.lower():
                sonnet_total += tok

        week_data["sonnet_tokens"] = sonnet_total
        week_data["sonnet_percentage"] = round(
            (sonnet_total / total * 100) if total > 0 else 0, 1
        )

    # ---- Daily activity for today ----
    today_str = datetime.now().strftime("%Y-%m-%d")
    today_activity = None
    if stats:
        for day in stats.get("dailyActivity", []):
            if day.get("date") == today_str:
                today_activity = {
                    "messages": day.get("messageCount", 0),
                    "sessions": day.get("sessionCount", 0),
                    "tool_calls": day.get("toolCallCount", 0),
                }
                break

    # ---- Plan limits from /usage CLI ----
    plan_limits = await _fetch_usage_via_cli()

    return {
        "available": bool(stats) or session_data is not None,
        "session": session_data,
        "week": week_data,
        "plan": plan_data,
        "plan_limits": plan_limits,
        "today": today_activity,
        "hour_window": _hour_window_info(),
        "stats_last_computed": stats.get("lastComputedDate") if stats else None,
    }
