import re
import subprocess
from collections.abc import Mapping, Sequence

_ITERM_SESSION = re.compile(r"^[^:]*:([0-9A-Fa-f-]+)$")
_SGR = re.compile(r"\x1b\[([0-9;]*)m")
_RULE = re.compile(r"^─{10,}")
_PROMPT = "❯"

_ENTER_DELAY = "0.5"

_ITERM_SCRIPT = """\
tell application "iTerm2"
    repeat with w in windows
        repeat with t in tabs of w
            repeat with s in sessions of t
                if unique id of s is "{guid}" then
                    tell s to write text {text} newline NO
                    delay {delay}
                    tell s to write text ""
                    return
                end if
            end repeat
        end repeat
    end repeat
end tell"""


def _applescript_string(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def type_line(env: Mapping[str, str], text: str) -> list[str] | None:
    """argv that types `text`, then Enter after `_ENTER_DELAY` seconds, into the terminal hosting this session.

    Claude Code reads an Enter that arrives in the same burst as typed text as part of a paste, so it
    inserts a newline instead of submitting; the delay keeps them apart. None when the terminal isn't recognised. tmux is checked first: inside tmux-in-iTerm2
    the pane, not the iTerm2 session, is where the input must go.
    """
    pane = env.get("TMUX_PANE")
    if pane:
        return ["tmux", "send-keys", "-t", pane, "-l", text, ";", "run-shell", f"sleep {_ENTER_DELAY}", ";",
                "send-keys", "-t", pane, "Enter"]
    iterm = _ITERM_SESSION.match(env.get("ITERM_SESSION_ID", ""))
    if iterm:
        return ["osascript", "-e", _ITERM_SCRIPT.format(guid=iterm[1], text=_applescript_string(text), delay=_ENTER_DELAY)]
    return None


def _undimmed_text(line: str) -> str:
    """`line` without ANSI escapes and without the runs drawn faint (SGR 2)."""
    out, dim, pos = [], False, 0
    for m in _SGR.finditer(line):
        if not dim:
            out.append(line[pos:m.start()])
        params = m[1].split(";") if m[1] else ["0"]
        i = 0
        while i < len(params):
            p = params[i]
            if p in ("38", "48", "58"):
                i += 3 if params[i + 1:i + 2] == ["5"] else 5
                continue
            if p == "2":
                dim = True
            elif p in ("0", "22"):
                dim = False
            i += 1
        pos = m.end()
    if not dim:
        out.append(line[pos:])
    return "".join(out)


def input_box_empty(pane_capture: str) -> bool:
    """Whether Claude Code's input box, the last block framed by two horizontal rules, holds no typed text.

    Expects `tmux capture-pane -e` output. Claude Code draws the placeholder and prompt suggestions
    faint, so faint text counts as empty. False when no box with a `❯` prompt is found (a dialog, bash
    mode, a layout this doesn't know).
    """
    lines = pane_capture.splitlines()
    rules = [i for i, line in enumerate(lines) if _RULE.match(_SGR.sub("", line).strip())]
    boxes = [(a, b) for a, b in zip(rules, rules[1:]) if b - a > 1]
    if not boxes:
        return False
    top, bottom = boxes[-1]
    body = [_undimmed_text(line) for line in lines[top + 1:bottom]]
    first = body[0].lstrip()
    if not first.startswith(_PROMPT):
        return False
    body[0] = first[len(_PROMPT):]
    return not "".join(body).strip()


def has_typed_input(env: Mapping[str, str], *, _run=subprocess.run) -> bool:
    """Whether the user may have text typed into this session's input box.

    Only tmux panes can be inspected; other terminals report False. True when the pane can't be read.
    """
    pane = env.get("TMUX_PANE")
    if not pane:
        return False
    try:
        out = _run(["tmux", "capture-pane", "-p", "-e", "-t", pane], capture_output=True, text=True, timeout=2)
    except (OSError, subprocess.SubprocessError):
        return True
    return out.returncode != 0 or not input_box_empty(out.stdout)


def spawn_detached(argv: Sequence[str]) -> None:
    """Fire and forget. Claude Code cancels an in-flight status line script when a new update starts."""
    subprocess.Popen(
        argv, start_new_session=True,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
