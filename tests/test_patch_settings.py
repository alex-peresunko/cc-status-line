import importlib.util
import json
import pathlib
import shutil
import subprocess
import uuid

import pytest

SCRIPT = pathlib.Path(__file__).parent.parent / "scripts" / "patch-settings.py"
spec = importlib.util.spec_from_file_location("patch_settings", SCRIPT)
ps = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ps)

OLD_POST_TOOL_USE = (
    'input=$(cat); sid=$(echo "$input" | jq -r \'.session_id // ""\'); '
    '[ -n "$sid" ] && rm -f "/tmp/claude-tool-${sid}" || true'
)


@pytest.fixture
def settings(tmp_path):
    return tmp_path / "settings.json"


def commands(path, event):
    return [h["command"] for w in json.loads(path.read_text())["hooks"][event] for h in w["hooks"]]


def hook(event, hooks=None):
    return next(cmd for ev, cmd in (hooks or ps.HOOKS) if ev == event)


def retired(event):
    return hook(event, ps.RETIRED_HOOKS)


def test_legacy_form_is_what_earlier_installs_wrote():
    assert ps._legacy(retired("PostToolUse")) == OLD_POST_TOOL_USE


def test_install_migrates_legacy_hooks_in_place(settings):
    legacy = {"hooks": [{"type": "command", "command": ps._legacy(hook("UserPromptSubmit"))}]}
    settings.write_text(json.dumps({"hooks": {"UserPromptSubmit": [legacy]}}))
    ps.install(settings.parent)
    assert commands(settings, "UserPromptSubmit") == [hook("UserPromptSubmit")]


def test_install_removes_retired_hooks_only(settings):
    other = {"hooks": [{"type": "command", "command": "keep-me"}]}
    settings.write_text(json.dumps({"hooks": {
        "PreToolUse": [{"hooks": [{"type": "command", "command": retired("PreToolUse")}]}],
        "PostToolUse": [other, {"hooks": [{"type": "command", "command": OLD_POST_TOOL_USE}]}],
    }}))
    ps.install(settings.parent)
    hooks = json.loads(settings.read_text())["hooks"]
    assert "PreToolUse" not in hooks
    assert commands(settings, "PostToolUse") == ["keep-me"]


def test_install_is_idempotent(settings):
    settings.write_text("{}")
    ps.install(settings.parent)
    first = settings.read_text()
    ps.install(settings.parent)
    assert settings.read_text() == first


def test_uninstall_removes_new_and_legacy_hooks_only(settings):
    settings.write_text(json.dumps({"hooks": {
        "PostToolUse": [
            {"hooks": [{"type": "command", "command": "keep-me"}]},
            {"hooks": [{"type": "command", "command": OLD_POST_TOOL_USE}]},
            {"hooks": [{"type": "command", "command": retired("PostToolUse")}]},
        ],
        "UserPromptSubmit": [{"hooks": [{"type": "command", "command": hook("UserPromptSubmit")}]}],
    }}))
    ps.uninstall(settings.parent)
    assert commands(settings, "PostToolUse") == ["keep-me"]
    assert "UserPromptSubmit" not in json.loads(settings.read_text())["hooks"]


@pytest.mark.parametrize("folder, command", [
    (".claude", "uv run ~/.claude/statusline.py"),
    (".claude-personal", "uv run ~/.claude-personal/statusline.py"),
])
def test_status_line_command_points_into_the_folder(tmp_path, monkeypatch, folder, command):
    monkeypatch.setattr(pathlib.Path, "home", lambda: tmp_path)
    assert ps.status_line(tmp_path / folder)["command"] == command


def test_status_line_command_outside_home_is_absolute(tmp_path, monkeypatch):
    monkeypatch.setattr(pathlib.Path, "home", lambda: tmp_path / "home")
    assert ps.status_line(tmp_path / "cfg")["command"] == f"uv run {tmp_path}/cfg/statusline.py"


def test_install_creates_missing_settings(settings):
    ps.install(settings.parent)
    data = json.loads(settings.read_text())
    assert data["statusLine"] == ps.status_line(settings.parent)
    assert commands(settings, "UserPromptSubmit") == [hook("UserPromptSubmit")]


def test_uninstall_without_settings_creates_nothing(settings):
    ps.uninstall(settings.parent)
    assert not settings.exists()


@pytest.mark.skipif(shutil.which("jq") is None, reason="hooks need jq")
def test_clear_hook_survives_json_escapes():
    sid = f"hooktest-{uuid.uuid4()}"
    state_file = pathlib.Path(f"/tmp/claude-out-tokens-{sid}")
    state_file.write_text("{}")
    payload = {"session_id": sid, "prompt": "/clear", "transcript": "line1\nline2\ttabbed"}
    try:
        subprocess.run(["sh", "-c", hook("UserPromptSubmit")], input=json.dumps(payload), text=True, check=True)
        assert not state_file.exists()
    finally:
        state_file.unlink(missing_ok=True)
