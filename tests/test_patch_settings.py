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
def settings(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    monkeypatch.setattr(ps, "SETTINGS", path)
    return path


def commands(path, event):
    return [h["command"] for w in json.loads(path.read_text())["hooks"][event] for h in w["hooks"]]


def hook(event):
    return next(cmd for ev, cmd in ps.HOOKS if ev == event)


def test_legacy_form_is_what_earlier_installs_wrote():
    assert ps._legacy(hook("PostToolUse")) == OLD_POST_TOOL_USE


def test_install_migrates_legacy_hooks_in_place(settings):
    other = {"hooks": [{"type": "command", "command": "keep-me"}]}
    legacy = {"hooks": [{"type": "command", "command": OLD_POST_TOOL_USE}]}
    settings.write_text(json.dumps({"hooks": {"PostToolUse": [other, legacy]}}))
    ps.install()
    assert commands(settings, "PostToolUse") == ["keep-me", hook("PostToolUse")]


def test_install_is_idempotent(settings):
    settings.write_text("{}")
    ps.install()
    first = settings.read_text()
    ps.install()
    assert settings.read_text() == first


def test_uninstall_removes_new_and_legacy_hooks_only(settings):
    settings.write_text(json.dumps({"hooks": {
        "PostToolUse": [
            {"hooks": [{"type": "command", "command": "keep-me"}]},
            {"hooks": [{"type": "command", "command": OLD_POST_TOOL_USE}]},
            {"hooks": [{"type": "command", "command": hook("PostToolUse")}]},
        ],
    }}))
    ps.uninstall()
    assert commands(settings, "PostToolUse") == ["keep-me"]


@pytest.mark.skipif(shutil.which("jq") is None, reason="hooks need jq")
def test_tool_file_hooks_survive_json_escapes():
    sid = f"hooktest-{uuid.uuid4()}"
    tool_file = pathlib.Path(f"/tmp/claude-tool-{sid}")

    def run(event, payload):
        subprocess.run(["sh", "-c", hook(event)], input=json.dumps(payload), text=True, check=True)

    try:
        run("PreToolUse", {"session_id": sid, "tool_name": "Bash", "tool_input": {"command": "echo a\necho b"}})
        assert tool_file.read_text() == "Bash"
        run("PostToolUse", {"session_id": sid, "tool_response": "line1\nline2\ttabbed"})
        assert not tool_file.exists()
    finally:
        tool_file.unlink(missing_ok=True)
