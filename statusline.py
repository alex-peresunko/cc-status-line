#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Claude Code status line: model | location | git | tokens | ctx% | cache"""

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
CYAN    = "\033[0;36m"
BLUE    = "\033[0;34m"
GREEN   = "\033[0;32m"
YELLOW  = "\033[0;33m"
RED     = "\033[0;31m"
WHITE   = "\033[0;37m"
MAGENTA = "\033[0;35m"
DIM     = "\033[0;90m"
RESET   = "\033[0m"

def format_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
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


def format_countdown(seconds: int) -> str:
    return f"{seconds // 60}:{seconds % 60:02d}"


_TMP = pathlib.Path("/tmp")


def session_timer(session_id: str, _tmp_dir: pathlib.Path = _TMP) -> str:
    if not session_id:
        return ""
    start_file = _tmp_dir / f"claude-start-{session_id}"
    now = int(time.time())
    if not start_file.exists():
        start_file.write_text(str(now))
        session_start = now
    else:
        try:
            session_start = int(start_file.read_text().strip())
        except (ValueError, OSError):
            session_start = now
    elapsed = now - session_start
    if elapsed >= 3600:
        return f"{elapsed // 3600}h {(elapsed % 3600) // 60}m"
    return f"{elapsed // 60}m"


def accumulate_output_tokens(session_id: str, turn_out: int, _tmp_dir: pathlib.Path = _TMP) -> int:
    if not session_id:
        return turn_out
    acc_file = _tmp_dir / f"claude-out-tokens-{session_id}"
    prev = 0
    if acc_file.exists():
        try:
            prev = int(acc_file.read_text().strip())
        except (ValueError, OSError):
            prev = 0
    total = prev + turn_out
    try:
        acc_file.write_text(str(total))
    except OSError:
        pass
    return total


def active_tool(session_id: str, _tmp_dir: pathlib.Path = _TMP) -> str:
    if not session_id:
        return ""
    tool_file = _tmp_dir / f"claude-tool-{session_id}"
    if tool_file.exists():
        try:
            return tool_file.read_text().strip()
        except OSError:
            pass
    return ""


def git_info(cwd: str) -> tuple[str, str]:
    """Returns (branch, porcelain_output). Empty strings if not a git repo."""
    if not cwd:
        return "", ""
    result = subprocess.run(
        ["git", "-C", cwd, "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True, text=True
    )
    branch = result.stdout.strip() if result.returncode == 0 else ""
    if not branch:
        return "", ""
    porcelain = subprocess.run(
        ["git", "-C", cwd, "status", "--porcelain"],
        capture_output=True, text=True
    )
    return branch, porcelain.stdout if porcelain.returncode == 0 else ""


def parse_input(data: dict) -> dict:
    model        = data.get("model", {}).get("display_name") or "Claude"

    cwd        = data.get("workspace", {}).get("current_dir") or ""
    repo_info  = data.get("workspace", {}).get("repo")
    repo       = f"{repo_info['owner']}/{repo_info['name']}" if repo_info else ""
    session_id = data.get("session_id") or ""
    transcript_path = data.get("transcript_path") or ""

    ctx        = data.get("context_window", {})
    total_in   = int(ctx.get("total_input_tokens") or 0)
    turn_out   = int((ctx.get("current_usage") or {}).get("output_tokens") or 0)
    used_pct   = ctx.get("used_percentage")

    rl         = data.get("rate_limits", {})
    five_pct   = (rl.get("five_hour") or {}).get("used_percentage")
    week_pct   = (rl.get("seven_day") or {}).get("used_percentage")

    effort_raw = data.get("effort") or {}
    effort     = (effort_raw.get("level") if isinstance(effort_raw, dict) else effort_raw) or ""

    return dict(
        model=model, cwd=cwd, repo=repo,
        session_id=session_id, transcript_path=transcript_path, total_in=total_in, turn_out=turn_out,
        used_pct=used_pct, five_pct=five_pct, week_pct=week_pct, effort=effort,
    )


def build_segments(
    model: str,
    cwd: str,
    repo: str,
    session_id: str,
    transcript_path: str,
    total_in: int,
    turn_out: int,
    used_pct,
    five_pct,
    week_pct,
    effort: str,
    *,
    ctx_warn: int = 50,
    ctx_crit: int = 80,
    bar_width: int = 10,
    cache_ttl: int = CACHE_TTL,
    _git_info_fn=git_info,
    _session_timer_fn=session_timer,
    _accumulate_fn=accumulate_output_tokens,
    _active_tool_fn=active_tool,
    _cache_remaining_fn=cache_remaining,
) -> list[str]:
    parts = []

    # 1. Model
    parts.append(f"{CYAN}{model}{RESET}")

    # 2. Location + branch
    branch, porcelain = _git_info_fn(cwd)
    location = repo or (os.path.basename(cwd) if cwd else "")
    if branch:
        parts.append(f"{BLUE}{location}{DIM}:{CYAN}{branch}{RESET}")
    elif location:
        parts.append(f"{BLUE}{location}{RESET}")

    # 3. Git state
    if porcelain:
        staged    = sum(1 for l in porcelain.splitlines() if l[:1] in "MADRC")
        modified  = sum(1 for l in porcelain.splitlines() if l[1:2] in "MD")
        untracked = sum(1 for l in porcelain.splitlines() if l.startswith("??"))
        git_str = ""
        if staged:    git_str += f"+{staged} "
        if modified:  git_str += f"~{modified} "
        if untracked: git_str += f"?{untracked}"
        parts.append(f"{YELLOW}{git_str.strip()}{RESET}")
    elif branch:
        parts.append(f"{GREEN}✓{RESET}")

    # 4. Session timer
    elapsed = _session_timer_fn(session_id)
    if elapsed:
        parts.append(f"{WHITE}{elapsed}{RESET}")

    # 5. Tokens
    total_out = _accumulate_fn(session_id, turn_out)
    if total_in > 0 or total_out > 0:
        parts.append(f"{WHITE}↑{format_tokens(total_in)} ↓{format_tokens(total_out)}{RESET}")

    # 6. Context bar
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
        parts.append(f"{color}{bar} {used_int}%{RESET}")

    # 7. Cache expiry countdown
    remaining = _cache_remaining_fn(transcript_path, cache_ttl)
    if remaining is not None:
        if remaining == 0:
            parts.append(f"{DIM}cache expired{RESET}")
        else:
            color = RED if remaining < 30 else YELLOW if remaining <= 120 else GREEN
            parts.append(f"{color}cache {format_countdown(remaining)}{RESET}")

    # 8. Rate limits
    rate_parts = []
    if five_pct is not None:
        rate_parts.append(f"5h:{round(float(five_pct))}%")
    if week_pct is not None:
        rate_parts.append(f"7d:{round(float(week_pct))}%")
    if rate_parts:
        parts.append(f"{MAGENTA}{' '.join(rate_parts)}{RESET}")

    # 9. Effort
    if effort:
        parts.append(f"{CYAN}🧠 {effort}{RESET}")

    # 10. Active tool
    tool = _active_tool_fn(session_id)
    if tool:
        parts.append(f"{YELLOW}⚙ {tool}{RESET}")

    return parts


def render(segments: list[str]) -> None:
    sep = f" {DIM}|{RESET} "
    print(sep.join(segments))


def build_context(
    fields: dict,
    cache_ttl: int,
    env=os.environ,
    *,
    _cache_remaining_fn=cache_remaining,
    _tool_in_flight_fn=tool_in_flight,
    _background_agents_fn=background_agents_running,
    _now=time.time,
) -> Context:
    return Context(
        session_id=fields["session_id"],
        total_in=fields["total_in"],
        cache_remaining=_cache_remaining_fn(fields["transcript_path"], cache_ttl),
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
    segments = build_segments(
        **fields,
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
