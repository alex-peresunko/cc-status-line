import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
from claude_status import terminal

GUID = "6C2B1A3E-0F4D-4B8A-9E57-1D2C3B4A5F60"


def test_tmux():
    argv = terminal.type_line({"TMUX_PANE": "%3"}, "/compact")
    assert argv == ["tmux", "send-keys", "-t", "%3", "-l", "/compact", ";", "run-shell", "sleep 0.5", ";",
                    "send-keys", "-t", "%3", "Enter"]


def test_iterm_extracts_session_guid():
    argv = terminal.type_line({"ITERM_SESSION_ID": f"w0t2p1:{GUID}"}, "/compact")
    assert argv[:2] == ["osascript", "-e"]
    assert f'unique id of s is "{GUID}"' in argv[2]
    assert 'write text "/compact" newline NO' in argv[2]
    assert 'delay 0.5' in argv[2]
    assert 'write text ""' in argv[2]


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


RULE = "\x1b[38;5;244m" + "─" * 40
STATUS = "\x1b[39m  \x1b[38;5;246m🧠 \x1b[1m\x1b[38;5;39mOpus 5.5\x1b[0m | ⧗ expired"


def box(*body, top=RULE):
    return "\n".join(["✻ Brewed for 58s", top, *body, RULE, STATUS, "  ⏵⏵ auto mode on"])


def test_empty_box_with_cursor():
    assert terminal.input_box_empty(box("\x1b[39m❯\xa0\x1b[7m \x1b[0m"))


def test_faint_suggestion_counts_as_empty():
    assert terminal.input_box_empty(box("\x1b[39m❯ \x1b[2mcommit and push the handoff\x1b[0m"))
    assert terminal.input_box_empty(box("❯ \x1b[38;5;2;2mfaint after extended color\x1b[22m"))


def test_named_session_rule():
    assert terminal.input_box_empty(box("❯\xa0", top=RULE + " df-relations-update-nrd ─"))


def test_typed_text():
    assert not terminal.input_box_empty(box("❯ fix the bu\x1b[7mg\x1b[0m"))
    assert not terminal.input_box_empty(box("❯ \x1b[38;5;2;5;7mx\x1b[0m"))


def test_typed_text_on_a_later_line():
    assert not terminal.input_box_empty(box("❯ ", "  second line"))


def test_no_recognisable_box():
    assert not terminal.input_box_empty("$ ls\nfoo\n")
    assert not terminal.input_box_empty(box("! git status"))


def test_has_typed_input_reads_the_tmux_pane():
    calls = []

    def run(argv, **kw):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout=box("❯ hi"))

    assert terminal.has_typed_input({"TMUX_PANE": "%3"}, _run=run)
    assert calls == [["tmux", "capture-pane", "-p", "-e", "-t", "%3"]]


def test_has_typed_input_unreadable_pane_counts_as_typed():
    failed = lambda argv, **kw: subprocess.CompletedProcess(argv, 1, stdout="")
    assert terminal.has_typed_input({"TMUX_PANE": "%3"}, _run=failed)

    def boom(argv, **kw):
        raise subprocess.TimeoutExpired(argv, 2)

    assert terminal.has_typed_input({"TMUX_PANE": "%3"}, _run=boom)


def test_has_typed_input_outside_tmux():
    assert not terminal.has_typed_input({"ITERM_SESSION_ID": f"w0t0p0:{GUID}"}, _run=None)
