#!/usr/bin/env python3
"""Patch <claude-dir>/settings.json to install or uninstall the status line."""
import json, sys, pathlib


def status_line(claude_dir):
    script = claude_dir / "statusline.py"
    try:
        # ~ form keeps the default folder's command identical to what earlier installs wrote
        script = "~" / script.relative_to(pathlib.Path.home())
    except ValueError:
        pass
    return {"type": "command", "command": f"uv run {script}", "refreshInterval": 5}

def _jq(field):
    # printf, not echo: sh's echo expands the \n escapes inside the JSON and jq then rejects it
    return f"""$(printf '%s' "$input" | jq -r '.{field} // ""')"""


def _legacy(cmd):
    """The echo-based form of `cmd`, which earlier installs wrote to settings.json."""
    return cmd.replace("printf '%s' \"$input\"", 'echo "$input"')


# Each hook is (event_name, command_string)
HOOKS = [
    (
        "UserPromptSubmit",
        'input=$(cat); '
        f'sid={_jq("session_id")}; '
        f'prompt={_jq("prompt")}; '
        '[ "$prompt" = "/clear" ] && [ -n "$sid" ] && rm -f "/tmp/claude-out-tokens-${sid}" || true',
    ),
]

# Hooks earlier installs added; install and uninstall both remove them.
RETIRED_HOOKS = [
    (
        "PreToolUse",
        'input=$(cat); '
        f'sid={_jq("session_id")}; '
        f'tool={_jq("tool_name")}; '
        '[ -n "$sid" ] && [ -n "$tool" ] && printf "%s" "$tool" > "/tmp/claude-tool-${sid}" || true',
    ),
    (
        "PostToolUse",
        'input=$(cat); '
        f'sid={_jq("session_id")}; '
        '[ -n "$sid" ] && rm -f "/tmp/claude-tool-${sid}" || true',
    ),
]


def load(settings):
    return json.loads(settings.read_text()) if settings.exists() else {}


def save(settings, data):
    settings.write_text(json.dumps(data, indent=2) + "\n")


def _existing_cmds(data, event):
    return {
        h.get("command")
        for w in data.get("hooks", {}).get(event, [])
        for h in w.get("hooks", [])
    }


def _remove(data, event, cmd):
    """Drop single-hook entries for `cmd` (current or legacy form) under `event`, and the event if left empty."""
    hooks = data.get("hooks", {})
    if event not in hooks:
        return
    hooks[event] = [
        w for w in hooks[event]
        if not (
            len(w.get("hooks", [])) == 1
            and w["hooks"][0].get("command") in (cmd, _legacy(cmd))
        )
    ]
    if not hooks[event]:
        del hooks[event]


def install(claude_dir):
    settings = claude_dir / "settings.json"
    data = load(settings)
    data["statusLine"] = status_line(claude_dir)
    data.setdefault("hooks", {})

    for event, cmd in HOOKS:
        data["hooks"].setdefault(event, [])
        for w in data["hooks"][event]:
            for h in w.get("hooks", []):
                if h.get("command") == _legacy(cmd):
                    h["command"] = cmd
        if cmd not in _existing_cmds(data, event):
            data["hooks"][event].append({"hooks": [{"type": "command", "command": cmd}]})
    for event, cmd in RETIRED_HOOKS:
        _remove(data, event, cmd)

    save(settings, data)
    print(f"{settings} patched (install).")


def uninstall(claude_dir):
    settings = claude_dir / "settings.json"
    if not settings.exists():
        return
    data = load(settings)
    data.pop("statusLine", None)

    for event, cmd in HOOKS + RETIRED_HOOKS:
        _remove(data, event, cmd)

    save(settings, data)
    print(f"{settings} patched (uninstall).")


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] not in ("install", "uninstall"):
        sys.exit("Usage: patch-settings.py install|uninstall <claude-dir>")
    claude_dir = pathlib.Path(sys.argv[2]).expanduser().absolute()
    if sys.argv[1] == "install":
        install(claude_dir)
    else:
        uninstall(claude_dir)
