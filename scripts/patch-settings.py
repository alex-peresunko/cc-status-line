#!/usr/bin/env python3
"""Patch ~/.claude/settings.json to install or uninstall the status line."""
import json, sys, pathlib

SETTINGS = pathlib.Path.home() / ".claude" / "settings.json"

STATUS_LINE = {
    "type": "command",
    "command": "uv run ~/.claude/statusline.py",
    "refreshInterval": 1,
}

# Each hook is (event_name, command_string)
HOOKS = [
    (
        "UserPromptSubmit",
        'input=$(cat); '
        'sid=$(echo "$input" | jq -r \'.session_id // ""\'); '
        'prompt=$(echo "$input" | jq -r \'.prompt // ""\'); '
        '[ "$prompt" = "/clear" ] && [ -n "$sid" ] && rm -f "/tmp/claude-out-tokens-${sid}" || true',
    ),
    (
        "PreToolUse",
        'input=$(cat); '
        'sid=$(echo "$input" | jq -r \'.session_id // ""\'); '
        'tool=$(echo "$input" | jq -r \'.tool_name // ""\'); '
        '[ -n "$sid" ] && [ -n "$tool" ] && printf "%s" "$tool" > "/tmp/claude-tool-${sid}" || true',
    ),
    (
        "PostToolUse",
        'input=$(cat); '
        'sid=$(echo "$input" | jq -r \'.session_id // ""\'); '
        '[ -n "$sid" ] && rm -f "/tmp/claude-tool-${sid}" || true',
    ),
]


def load():
    return json.loads(SETTINGS.read_text())


def save(data):
    SETTINGS.write_text(json.dumps(data, indent=2) + "\n")


def _existing_cmds(data, event):
    return {
        h.get("command")
        for w in data.get("hooks", {}).get(event, [])
        for h in w.get("hooks", [])
    }


def install():
    data = load()
    data["statusLine"] = STATUS_LINE
    data.setdefault("hooks", {})

    for event, cmd in HOOKS:
        data["hooks"].setdefault(event, [])
        if cmd not in _existing_cmds(data, event):
            data["hooks"][event].append({"hooks": [{"type": "command", "command": cmd}]})

    save(data)
    print("settings.json patched (install).")


def uninstall():
    data = load()
    data.pop("statusLine", None)

    for event, cmd in HOOKS:
        entries = data.get("hooks", {}).get(event, [])
        data["hooks"][event] = [
            w for w in entries
            if not (
                len(w.get("hooks", [])) == 1
                and w["hooks"][0].get("command") == cmd
            )
        ]

    save(data)
    print("settings.json patched (uninstall).")


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in ("install", "uninstall"):
        sys.exit("Usage: patch-settings.py install|uninstall")
    if sys.argv[1] == "install":
        install()
    else:
        uninstall()
