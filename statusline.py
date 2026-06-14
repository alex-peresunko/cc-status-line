#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Claude Code status line: model | location | git | tokens | ctx% | cost"""

import json
import os
import pathlib
import subprocess
import sys

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

PRICING_CACHE = pathlib.Path.home() / ".claude" / "model-pricing-cache.json"


def format_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n // 1_000}k"
    return str(n)


def compute_cost(model_id: str, total_in: int, total_out: int, cache_path: pathlib.Path = PRICING_CACHE) -> str:
    if total_in == 0 and total_out == 0:
        return ""

    in_rate, out_rate = 3.00, 15.00
    if cache_path.exists():
        try:
            cache = json.loads(cache_path.read_text())
            models = cache.get("models", {})
            if model_id in models:
                in_rate  = models[model_id]["input"]
                out_rate = models[model_id]["output"]
            else:
                family = "sonnet"
                lower = model_id.lower()
                if "haiku" in lower:
                    family = "haiku"
                elif "opus" in lower:
                    family = "opus"
                fallback = cache.get("fallback_by_family", {}).get(family, {})
                in_rate  = fallback.get("input",  in_rate)
                out_rate = fallback.get("output", out_rate)
        except (json.JSONDecodeError, KeyError):
            pass

    cost = (total_in / 1_000_000 * in_rate) + (total_out / 1_000_000 * out_rate)
    return f"${cost:.2f}"


_TMP = pathlib.Path("/tmp")


def session_timer(session_id: str, _tmp_dir: pathlib.Path = _TMP) -> str:
    if not session_id:
        return ""
    import time
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
    raw_model_id = data.get("model", {}).get("id") or ""
    # Strip vendor prefix (e.g. "us.anthropic." or "aws.anthropic.")
    model_id = raw_model_id
    for prefix in ("us.anthropic.", "aws.anthropic.", "anthropic."):
        if model_id.startswith(prefix):
            model_id = model_id[len(prefix):]
            break

    cwd        = data.get("workspace", {}).get("current_dir") or ""
    repo_info  = data.get("workspace", {}).get("repo")
    repo       = f"{repo_info['owner']}/{repo_info['name']}" if repo_info else ""
    session_id = data.get("session_id") or ""

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
        model=model, model_id=model_id, cwd=cwd, repo=repo,
        session_id=session_id, total_in=total_in, turn_out=turn_out,
        used_pct=used_pct, five_pct=five_pct, week_pct=week_pct, effort=effort,
    )


def build_segments(
    model: str,
    model_id: str,
    cwd: str,
    repo: str,
    session_id: str,
    total_in: int,
    turn_out: int,
    used_pct,
    five_pct,
    week_pct,
    effort: str,
    *,
    ctx_warn: int = 50,
    ctx_crit: int = 70,
    bar_width: int = 10,
    _git_info_fn=git_info,
    _session_timer_fn=session_timer,
    _accumulate_fn=accumulate_output_tokens,
    _active_tool_fn=active_tool,
    _compute_cost_fn=compute_cost,
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

    # 7. Cost
    cost = _compute_cost_fn(model_id, total_in, total_out)
    if cost:
        parts.append(f"{YELLOW}{cost}{RESET}")

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


def main() -> None:
    ctx_warn  = int(os.environ.get("CLAUDE_STATUS_CTX_WARN",  "50"))
    ctx_crit  = int(os.environ.get("CLAUDE_STATUS_CTX_CRIT",  "70"))
    bar_width = int(os.environ.get("CLAUDE_STATUS_BAR_WIDTH", "10"))

    try:
        data = json.loads(sys.stdin.read())
    except json.JSONDecodeError:
        data = {}

    fields = parse_input(data)
    segments = build_segments(
        **fields,
        ctx_warn=ctx_warn,
        ctx_crit=ctx_crit,
        bar_width=bar_width,
    )
    render(segments)


if __name__ == "__main__":
    main()
