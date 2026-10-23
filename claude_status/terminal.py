import re
import subprocess
from collections.abc import Mapping, Sequence

_ITERM_SESSION = re.compile(r"^[^:]*:([0-9A-Fa-f-]+)$")

_ITERM_SCRIPT = """\
tell application "iTerm2"
    repeat with w in windows
        repeat with t in tabs of w
            repeat with s in sessions of t
                if unique id of s is "{guid}" then
                    tell s to write text {text}
                    return
                end if
            end repeat
        end repeat
    end repeat
end tell"""


def _applescript_string(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def type_line(env: Mapping[str, str], text: str) -> list[str] | None:
    """argv that types `text` plus Enter into the terminal hosting this session.

    None when the terminal isn't recognised. tmux is checked first: inside tmux-in-iTerm2
    the pane, not the iTerm2 session, is where the input must go.
    """
    pane = env.get("TMUX_PANE")
    if pane:
        return ["tmux", "send-keys", "-t", pane, "-l", text, ";", "send-keys", "-t", pane, "Enter"]
    iterm = _ITERM_SESSION.match(env.get("ITERM_SESSION_ID", ""))
    if iterm:
        return ["osascript", "-e", _ITERM_SCRIPT.format(guid=iterm[1], text=_applescript_string(text))]
    return None


def spawn_detached(argv: Sequence[str]) -> None:
    """Fire and forget. Claude Code cancels an in-flight status line script when a new update starts."""
    subprocess.Popen(
        argv, start_new_session=True,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
