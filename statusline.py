#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Claude Code status line: profile | model effort | project:branch | tokens | ctx% | cache"""

import json
import os
import pathlib
import re
import subprocess
import sys
import time
from datetime import datetime

from claude_status.config import CACHE_TTL, load as load_config
from claude_status.features import Context, run_enabled

# ANSI color codes
GREEN   = "\033[0;32m"
YELLOW  = "\033[0;33m"
RED     = "\033[0;31m"
DIM     = "\033[0;90m"

# Segment colors (256-color palette)
MODEL   = "\033[1;38;5;39m"
PROJECT = "\033[38;5;117m"
BRANCH  = "\033[38;5;147m"
TOKENS  = "\033[38;5;253m"
LABEL   = "\033[38;5;243m"
RATE    = "\033[38;5;110m"
EFFORT  = "\033[38;5;80m"
SEP     = "\033[38;5;238m"
PROFILE = "\033[1;31m"
RESET   = "\033[0m"

def format_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}".removesuffix(".0") + "M"
    if n >= 1_000:
        return f"{n // 1_000}k"
    return str(n)


_TRANSCRIPT_TAIL_BYTES = 256 * 1024


def _transcript_tail(transcript_path: str) -> list[str] | None:
    """Complete JSONL lines from the end of the transcript; None if it can't be read."""
    if not transcript_path:
        return None
    try:
        with open(transcript_path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - _TRANSCRIPT_TAIL_BYTES))
            tail = f.read().decode("utf-8", errors="replace")
    except OSError:
        return None
    lines = tail.splitlines()
    if size > _TRANSCRIPT_TAIL_BYTES:
        lines = lines[1:]  # first line is cut mid-record
    return lines


def tool_in_flight(transcript_path: str) -> bool:
    """True while a tool call in the current turn has no result yet: running, or awaiting a
    permission or dialog answer. Read from the transcript, so it doesn't depend on hooks.
    """
    called, answered = set(), set()
    for line in reversed(_transcript_tail(transcript_path) or []):
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict) or entry.get("isSidechain") or entry.get("type") not in ("assistant", "user"):
            continue
        content = (entry.get("message") or {}).get("content")
        blocks = [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []
        if entry["type"] == "assistant":
            called.update(b["id"] for b in blocks if b.get("type") == "tool_use" and "id" in b)
            continue
        results = [b.get("tool_use_id") for b in blocks if b.get("type") == "tool_result"]
        if not results:
            break  # the prompt that started this turn
        answered.update(results)
    return bool(called - answered)


_TASK_ID = re.compile(r"<task-id>([^<]+)</task-id>")


def background_agents_running(transcript_path: str) -> bool:
    """True while a background subagent launched in the transcript tail has not reported completion.

    A launch is a tool result with status "async_launched"; completion is a user entry holding
    a <task-notification> for the same agent id. A launch older than the tail is not seen.
    """
    running = set()
    for line in _transcript_tail(transcript_path) or []:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict) or entry.get("isSidechain") or entry.get("type") != "user":
            continue
        launch = entry.get("toolUseResult")
        if isinstance(launch, dict) and launch.get("status") == "async_launched" and launch.get("agentId"):
            running.add(launch["agentId"])
        content = (entry.get("message") or {}).get("content")
        if isinstance(content, str) and content.startswith("<task-notification>"):
            running.difference_update(_TASK_ID.findall(content))
    return bool(running)


def cache_remaining(transcript_path: str, ttl: int = CACHE_TTL, _now=time.time) -> int | None:
    """Seconds until the prompt cache expires, or None if no main-thread assistant message is found.

    Subagent (sidechain) messages are ignored: they use their own cache.
    """
    lines = _transcript_tail(transcript_path)
    if lines is None:
        return None
    for line in reversed(lines):
        try:
            entry = json.loads(line)
            if entry.get("type") != "assistant" or entry.get("isSidechain"):
                continue
            ts = datetime.fromisoformat(entry["timestamp"].replace("Z", "+00:00")).timestamp()
        except (ValueError, KeyError, AttributeError, TypeError):
            continue
        return max(0, int(ttl - (_now() - ts)))
    return None


def cache_left(cache_expires_at, transcript_path: str, ttl: int = CACHE_TTL, _now=time.time) -> int | None:
    """Seconds until the prompt cache expires, from Claude Code's `prompt_cache.expires_at` when sent,
    else estimated from the transcript with `ttl`. None if neither is known."""
    if cache_expires_at is not None:
        return max(0, int(cache_expires_at - _now()))
    return cache_remaining(transcript_path, ttl, _now)


def format_countdown(seconds: int) -> str:
    return f"{seconds // 60}:{seconds % 60:02d}"


def format_time_left(seconds: int) -> str:
    """Coarse duration for a rate-limit reset: `2d:5h`, `3h:12m` or `45m`."""
    minutes = seconds // 60
    days, hours, mins = minutes // 1440, minutes // 60 % 24, minutes % 60
    if days:
        return f"{days}d:{hours}h"
    if hours:
        return f"{hours}h:{mins:02d}m"
    return f"{mins}m"


def pace_color(pct: int, seconds_left: float, window: int) -> str:
    """GREEN when `pct` is at or below the share of `window` already elapsed, RED when above it."""
    elapsed = window - min(max(seconds_left, 0), window)
    return GREEN if pct <= elapsed / window * 100 else RED


_TMP = pathlib.Path("/tmp")


USAGE_KEYS = ("output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")


def session_usage(session_id: str, transcript_path: str, _tmp_dir: pathlib.Path = _TMP) -> dict[str, int]:
    """Session totals of each `USAGE_KEYS` field over the main thread's API responses in the transcript.

    The transcript is read incrementally: the state file keeps the offset of the first unread line.
    A response is written as several consecutive lines sharing a message id and usage, so a repeated id
    adds only the difference from what was already counted for it.
    """
    empty = dict.fromkeys(USAGE_KEYS, 0)
    if not transcript_path:
        return empty
    state_file = _tmp_dir / f"claude-out-tokens-{session_id}" if session_id else None
    state = {}
    if state_file and state_file.exists():
        try:
            state = json.loads(state_file.read_text())
        except (ValueError, OSError):
            state = {}
        if (not isinstance(state, dict) or state.get("path") != transcript_path
                or not isinstance(state.get("totals"), dict) or not isinstance(state.get("last_usage"), dict)):
            state = {}
    offset = int(state.get("offset") or 0)
    totals = {k: int(state.get("totals", {}).get(k) or 0) for k in USAGE_KEYS}
    last_id = state.get("last_id")
    last_usage = {k: int(state.get("last_usage", {}).get(k) or 0) for k in USAGE_KEYS}
    try:
        with open(transcript_path, "rb") as f:
            f.seek(0, os.SEEK_END)
            if f.tell() < offset:  # transcript replaced or truncated
                offset, totals, last_id, last_usage = 0, dict(empty), None, dict(empty)
            f.seek(offset)
            chunk = f.read()
    except OSError:
        return totals
    complete = chunk[: chunk.rfind(b"\n") + 1]  # a trailing partial line is read on a later tick
    for line in complete.splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict) or entry.get("type") != "assistant" or entry.get("isSidechain"):
            continue
        message = entry.get("message") or {}
        raw = message.get("usage") or {}
        usage = {k: int(raw.get(k) or 0) for k in USAGE_KEYS}
        msg_id = message.get("id")
        repeated = msg_id is not None and msg_id == last_id
        for k in USAGE_KEYS:
            totals[k] += usage[k] - (last_usage[k] if repeated else 0)
        last_id, last_usage = msg_id, usage
    if state_file:
        try:
            state_file.write_text(json.dumps({
                "path": transcript_path, "offset": offset + len(complete),
                "totals": totals, "last_id": last_id, "last_usage": last_usage,
            }))
        except OSError:
            pass
    return totals


def claude_profile(name: str = "", env=os.environ) -> tuple[str, bool]:
    """Profile label (`name`, else the config folder's name), and whether the folder is the standard `~/.claude`."""
    default = pathlib.Path.home() / ".claude"
    folder = pathlib.Path(env.get("CLAUDE_CONFIG_DIR") or default).expanduser().resolve()
    return name or folder.name, folder == default.resolve()


def profile_arg(argv: list[str]) -> str:
    """Value of `--profile NAME`, which `make install NAME=...` puts into the status line command."""
    if "--profile" in argv[:-1]:
        return argv[argv.index("--profile") + 1]
    return ""


def git_branch(cwd: str) -> str:
    """Current branch name; empty if `cwd` is not in a git repo."""
    if not cwd:
        return ""
    result = subprocess.run(
        ["git", "-C", cwd, "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True, text=True
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def parse_input(data: dict) -> dict:
    model        = data.get("model", {}).get("display_name") or "Claude"

    workspace  = data.get("workspace", {})
    cwd        = workspace.get("current_dir") or ""
    project    = os.path.basename((workspace.get("project_dir") or cwd).rstrip("/"))
    session_id = data.get("session_id") or ""
    transcript_path = data.get("transcript_path") or ""

    ctx        = data.get("context_window", {})
    total_in   = int(ctx.get("total_input_tokens") or 0)
    ctx_size   = int(ctx.get("context_window_size") or 0)
    used_pct   = ctx.get("used_percentage")

    pc         = data.get("prompt_cache") or {}
    cache_expires_at = pc.get("expires_at")

    rl         = data.get("rate_limits", {})
    five_hour  = rl.get("five_hour") or {}
    seven_day  = rl.get("seven_day") or {}
    five_pct   = five_hour.get("used_percentage")
    week_pct   = seven_day.get("used_percentage")
    five_resets_at = five_hour.get("resets_at")
    week_resets_at = seven_day.get("resets_at")

    effort_raw = data.get("effort") or {}
    effort     = (effort_raw.get("level") if isinstance(effort_raw, dict) else effort_raw) or ""

    return dict(
        model=model, cwd=cwd, project=project,
        session_id=session_id, transcript_path=transcript_path, total_in=total_in, ctx_size=ctx_size,
        cache_expires_at=cache_expires_at, used_pct=used_pct, five_pct=five_pct, week_pct=week_pct,
        five_resets_at=five_resets_at, week_resets_at=week_resets_at, effort=effort,
    )


def build_segments(
    model: str,
    cwd: str,
    project: str,
    session_id: str,
    transcript_path: str,
    total_in: int,
    ctx_size: int,
    cache_expires_at,
    used_pct,
    five_pct,
    week_pct,
    effort: str,
    five_resets_at=None,
    week_resets_at=None,
    *,
    profile: str = "",
    profile_default: bool = True,
    ctx_warn: int = 50,
    ctx_crit: int = 80,
    bar_width: int = 10,
    cache_ttl: int = CACHE_TTL,
    _git_branch_fn=git_branch,
    _usage_fn=session_usage,
    _cache_left_fn=cache_left,
    _now=time.time,
) -> list[str]:
    parts = []

    # Profile (Claude Code config folder)
    if profile and not profile_default:
        parts.append(f"🔴 {PROFILE}{profile}{RESET}")

    # 1. Model and effort
    level = f" {EFFORT}{effort}{RESET}" if effort else ""
    parts.append(f"🧠 {MODEL}{model}{RESET}{level}")

    # 2. Project and branch
    branch = _git_branch_fn(cwd)
    if project and branch:
        parts.append(f"{PROJECT}{project}{LABEL}:{BRANCH}{branch}{RESET}")
    elif project or branch:
        parts.append(f"{PROJECT}{project}{RESET}" if project else f"{BRANCH}{branch}{RESET}")

    # 3. Tokens
    usage = _usage_fn(session_id, transcript_path)
    if any(usage.values()):
        parts.append(
            f"{TOKENS}↓{format_tokens(usage['output_tokens'])} "
            f"{LABEL}R{TOKENS}{format_tokens(usage['cache_read_input_tokens'])} "
            f"{LABEL}W{TOKENS}{format_tokens(usage['cache_creation_input_tokens'])}{RESET}"
        )

    # 4. Context bar
    if used_pct is not None:
        used_int = round(float(used_pct))
        if used_int >= ctx_crit:
            color = RED
        elif used_int >= ctx_warn:
            color = YELLOW
        else:
            color = GREEN
        filled = min(round(used_int / 100 * bar_width), bar_width)
        bar = "█" * filled + "░" * (bar_width - filled)
        size = f"/{format_tokens(ctx_size)}" if ctx_size else ""
        fill = f"{format_tokens(total_in)}{size} ({used_int}%)" if total_in else f"{used_int}%"
        parts.append(f"{color}{bar} {fill}{RESET}")

    # 5. Cache expiry countdown
    remaining = _cache_left_fn(cache_expires_at, transcript_path, cache_ttl)
    if remaining is not None:
        if remaining == 0:
            parts.append(f"{DIM}⧗ expired{RESET}")
        else:
            color = RED if remaining < 30 else YELLOW if remaining <= 120 else GREEN
            parts.append(f"{color}⧗ {format_countdown(remaining)}{RESET}")

    # 6. Rate limits
    rate_parts = []
    limits = (("5h", five_pct, five_resets_at, 5 * 3600), ("7d", week_pct, week_resets_at, 7 * 86400))
    for label, pct, resets_at, window in limits:
        if pct is None:
            continue
        pct_int = round(float(pct))
        shown, left = f"{pct_int}%", ""
        if isinstance(resets_at, (int, float)):
            seconds_left = resets_at - _now()
            shown = f"{pace_color(pct_int, seconds_left, window)}{shown}{RATE}"
            left = f" ({format_time_left(max(0, int(seconds_left)))})"
        rate_parts.append(f"{label}:{shown}{left}")
    if rate_parts:
        parts.append(f"{RATE}{' '.join(rate_parts)}{RESET}")

    return parts


def render(segments: list[str]) -> None:
    sep = f" {SEP}|{RESET} "
    print(sep.join(segments))


def build_context(
    fields: dict,
    cache_ttl: int,
    env=os.environ,
    *,
    _cache_left_fn=cache_left,
    _tool_in_flight_fn=tool_in_flight,
    _background_agents_fn=background_agents_running,
    _now=time.time,
) -> Context:
    return Context(
        session_id=fields["session_id"],
        total_in=fields["total_in"],
        cache_remaining=_cache_left_fn(fields["cache_expires_at"], fields["transcript_path"], cache_ttl),
        tool_in_flight=_tool_in_flight_fn(fields["transcript_path"]),
        background_agents=_background_agents_fn(fields["transcript_path"]),
        env=env,
        now=_now(),
        state_dir=_TMP,
    )


def main() -> None:
    config = load_config()

    try:
        data = json.loads(sys.stdin.read())
    except json.JSONDecodeError:
        data = {}

    fields = parse_input(data)
    profile, profile_default = claude_profile(profile_arg(sys.argv[1:]))
    segments = build_segments(
        **fields,
        profile=profile,
        profile_default=profile_default,
        ctx_warn=config.display.ctx_warn,
        ctx_crit=config.display.ctx_crit,
        bar_width=config.display.bar_width,
        cache_ttl=config.cache.ttl,
    )
    if config.error:
        print(f"claude-status: {config.error}", file=sys.stderr)
        segments.append(f"{RED}cfg error{RESET}")
    render(segments)

    if any(s.enabled for s in config.features.values()):
        run_enabled(config.features, build_context(fields, config.cache.ttl))


if __name__ == "__main__":
    main()
