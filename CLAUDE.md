# claude-status

Claude Code status line script (`statusline.py`).

- `statusline.py` renders the segments; `claude_status/` holds config loading and opt-in *features* (side-effect actions run each tick, toggled in `~/.claude/statusline.toml`).
- Add a feature: a module in `claude_status/features/` with a `Settings` dataclass (all fields defaulted, `enabled` off) and a `Feature` subclass, one entry in `REGISTRY` (`features/__init__.py`), and a `[features.<name>]` table in `config/statusline.default.toml`. A test keeps that template equal to the code defaults.
- `~/.claude/statusline.py` must stay a symlink to the repo file so `claude_status/` resolves.
