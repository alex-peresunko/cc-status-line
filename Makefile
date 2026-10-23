CLAUDE_DIR := $(HOME)/.claude
SCRIPT     := statusline.py

.PHONY: install uninstall test

# The script must stay a symlink: claude_status/ is found next to the resolved file.
install:
	ln -sf $(PWD)/$(SCRIPT)  $(CLAUDE_DIR)/$(SCRIPT)
	cp -n $(PWD)/config/statusline.default.toml $(CLAUDE_DIR)/statusline.toml || true
	python3 $(PWD)/scripts/patch-settings.py install
	@echo "Done. Restart Claude Code to activate the status line."
	@echo "Features are toggled in $(CLAUDE_DIR)/statusline.toml"

uninstall:
	rm -f $(CLAUDE_DIR)/$(SCRIPT)
	python3 $(PWD)/scripts/patch-settings.py uninstall
	@echo "Done. Restart Claude Code to deactivate the status line."
	@echo "Kept $(CLAUDE_DIR)/statusline.toml"

test:
	uv run --with pytest pytest tests/ -v
