#!/usr/bin/env python3
"""Patch ~/.claude/settings.json to install or uninstall the status line."""
import json, sys, pathlib

SETTINGS = pathlib.Path.home() / ".claude" / "settings.json"

STATUS_LINE = {
    "type": "command",
    "command": "bash ~/.claude/statusline-command.sh",
}

# Exact hook command stored in settings.json
HOOK_CMD = (
    'input=$(cat); '
    'sid=$(echo "$input" | jq -r \'.session_id // ""\'); '
    'prompt=$(echo "$input" | jq -r \'.prompt // ""\'); '
    '[ "$prompt" = "/clear" ] && [ -n "$sid" ] && rm -f "/tmp/claude-out-tokens-${sid}" || true'
)

HOOK_ENTRY = {"type": "command", "command": HOOK_CMD}
HOOK_WRAPPER = {"hooks": [HOOK_ENTRY]}


def load():
    return json.loads(SETTINGS.read_text())


def save(data):
    SETTINGS.write_text(json.dumps(data, indent=2) + "\n")


def install():
    data = load()
    data["statusLine"] = STATUS_LINE

    # Ensure hooks.UserPromptSubmit exists
    data.setdefault("hooks", {}).setdefault("UserPromptSubmit", [])

    # Idempotent: only add the hook wrapper if the command isn't already present
    existing_cmds = [
        h.get("command")
        for w in data["hooks"]["UserPromptSubmit"]
        for h in w.get("hooks", [])
    ]
    if HOOK_CMD not in existing_cmds:
        data["hooks"]["UserPromptSubmit"].append(HOOK_WRAPPER)

    save(data)
    print("settings.json patched (install).")


def uninstall():
    data = load()
    data.pop("statusLine", None)

    # Remove our hook wrapper (any wrapper whose sole hook matches our command)
    ups = data.get("hooks", {}).get("UserPromptSubmit", [])
    data["hooks"]["UserPromptSubmit"] = [
        w for w in ups
        if not (
            len(w.get("hooks", [])) == 1
            and w["hooks"][0].get("command") == HOOK_CMD
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
