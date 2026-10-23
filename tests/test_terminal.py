import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
from claude_status import terminal

GUID = "6C2B1A3E-0F4D-4B8A-9E57-1D2C3B4A5F60"


def test_tmux():
    argv = terminal.type_line({"TMUX_PANE": "%3"}, "/compact")
    assert argv == ["tmux", "send-keys", "-t", "%3", "-l", "/compact", ";", "send-keys", "-t", "%3", "Enter"]


def test_iterm_extracts_session_guid():
    argv = terminal.type_line({"ITERM_SESSION_ID": f"w0t2p1:{GUID}"}, "/compact")
    assert argv[:2] == ["osascript", "-e"]
    assert f'unique id of s is "{GUID}"' in argv[2]
    assert 'write text "/compact"' in argv[2]


def test_tmux_wins_over_iterm():
    env = {"TMUX_PANE": "%1", "ITERM_SESSION_ID": f"w0t0p0:{GUID}"}
    assert terminal.type_line(env, "/compact")[0] == "tmux"


def test_unrecognised_terminal():
    assert terminal.type_line({}, "/compact") is None
    assert terminal.type_line({"TERMINAL_EMULATOR": "JetBrains-JediTerm"}, "/compact") is None


def test_iterm_rejects_malformed_session_id():
    assert terminal.type_line({"ITERM_SESSION_ID": 'w0t0p0:x" & (do shell script "id")'}, "/compact") is None
    assert terminal.type_line({"ITERM_SESSION_ID": "garbage"}, "/compact") is None


def test_iterm_escapes_text():
    argv = terminal.type_line({"ITERM_SESSION_ID": f"w0t0p0:{GUID}"}, 'say "hi"\\')
    assert 'write text "say \\"hi\\"\\\\"' in argv[2]
