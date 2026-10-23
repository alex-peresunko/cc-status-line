import pathlib
from dataclasses import dataclass

from claude_status import terminal
from claude_status.features.base import Context, Feature, Settings

_SAME_EXPIRY_TOLERANCE = 5


@dataclass(frozen=True)
class AutoCompactSettings(Settings):
    tokens: int = 150_000
    within: int = 30


def _already_fired(marker: pathlib.Path, expiry: int) -> bool:
    try:
        return abs(int(marker.read_text()) - expiry) <= _SAME_EXPIRY_TOLERANCE
    except (OSError, ValueError):
        return False


def maybe_compact(ctx: Context, settings: AutoCompactSettings, *, _spawn=terminal.spawn_detached) -> bool:
    """Type /compact into the session's terminal at most once per prompt-cache expiry.

    `ctx.active_tool` is set by the PreToolUse hook while a tool runs or awaits a permission
    prompt; typing Enter then could approve that prompt.
    """
    remaining = ctx.cache_remaining
    if (not ctx.session_id or ctx.active_tool or remaining is None
            or ctx.total_in < settings.tokens or not 0 < remaining <= settings.within):
        return False
    cmd = terminal.type_line(ctx.env, "/compact")
    if cmd is None:
        return False
    expiry = int(ctx.now + remaining)
    marker = ctx.state_dir / f"claude-compact-{ctx.session_id}"
    if _already_fired(marker, expiry):
        return False
    try:
        marker.write_text(str(expiry))
    except OSError:
        return False
    _spawn(cmd)
    return True


class AutoCompact(Feature):
    name = "auto_compact"
    Settings = AutoCompactSettings

    def run(self, ctx: Context, settings: AutoCompactSettings) -> None:
        maybe_compact(ctx, settings)
