import pathlib
from dataclasses import dataclass

from claude_status import terminal
from claude_status.features.base import Context, Feature, Settings

_SAME_EXPIRY_TOLERANCE = 5
PING = "Cache keep-alive ping, not a request. Ignore it and reply only: ok"


@dataclass(frozen=True)
class AutoCompactSettings(Settings):
    tokens: int = 150_000
    within: int = 30
    keep_warm_minutes: int = 60


def _already_fired(marker: pathlib.Path, expiry: int) -> bool:
    try:
        return abs(int(marker.read_text()) - expiry) <= _SAME_EXPIRY_TOLERANCE
    except (OSError, ValueError):
        return False


def _read_streak_start(path: pathlib.Path) -> int | None:
    try:
        return int(path.read_text())
    except (OSError, ValueError):
        return None


def maybe_compact(ctx: Context, settings: AutoCompactSettings, *, _spawn=terminal.spawn_detached,
                  _has_input=terminal.has_typed_input) -> bool:
    """Before the prompt cache expires, type /compact into the session's terminal, or `PING` while the
    context is below `tokens`. At most one of them per cache expiry.

    Pings in a row keep the cache warm for about `keep_warm_minutes` past the expiry the first of them
    extended (0 = no pings); the streak continues only while the last prompt in the transcript is `PING`,
    so any other prompt starts a new one.
    Skipped while a tool call is in flight (typing Enter could approve a pending permission prompt),
    while a request awaits its reply (the cache countdown only restarts when the reply lands),
    while background subagents are running, and while text is typed in the input box. A tick skipped
    for typed text leaves no marker, so a later tick of the same window can still fire.
    """
    remaining = ctx.cache_remaining
    if (not ctx.session_id or ctx.tool_in_flight or ctx.awaiting_response
            or ctx.background_agents or remaining is None
            or not 0 < remaining <= settings.within):
        return False
    expiry = int(ctx.now + remaining)
    streak_file = ctx.state_dir / f"claude-keepwarm-{ctx.session_id}"
    streak_start = None
    if ctx.total_in >= settings.tokens:
        text = "/compact"
    else:
        streak_start = _read_streak_start(streak_file) if ctx.last_prompt == PING else None
        streak_start = expiry if streak_start is None else streak_start
        if expiry - streak_start >= settings.keep_warm_minutes * 60:
            return False
        text = PING
    cmd = terminal.type_line(ctx.env, text)
    if cmd is None:
        return False
    marker = ctx.state_dir / f"claude-compact-{ctx.session_id}"
    if _already_fired(marker, expiry) or _has_input(ctx.env):
        return False
    try:
        marker.write_text(str(expiry))
        if streak_start is not None:
            streak_file.write_text(str(streak_start))
    except OSError:
        return False
    _spawn(cmd)
    return True


class AutoCompact(Feature):
    name = "auto_compact"
    Settings = AutoCompactSettings

    def run(self, ctx: Context, settings: AutoCompactSettings) -> None:
        maybe_compact(ctx, settings)
