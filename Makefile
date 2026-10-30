# Claude Code config folder to install into: `make install DIR=~/.claude-personal`.
DIR    ?= $(or $(CLAUDE_CONFIG_DIR),$(HOME)/.claude)
SCRIPT := statusline.py

.PHONY: install uninstall test

# The script must stay a symlink: claude_status/ is found next to the resolved file.
install:
	@test -d $(DIR) || { echo "No such folder: $(DIR)"; exit 1; }
	@echo "Installing into $(DIR)"
	ln -sf $(PWD)/$(SCRIPT)  $(DIR)/$(SCRIPT)
	cp -n $(PWD)/config/statusline.default.toml $(DIR)/statusline.toml || true
	python3 $(PWD)/scripts/patch-settings.py install $(DIR)
	@echo "Done. Restart Claude Code to activate the status line."
	@echo "Features are toggled in $(DIR)/statusline.toml"

uninstall:
	@echo "Uninstalling from $(DIR)"
	rm -f $(DIR)/$(SCRIPT)
	python3 $(PWD)/scripts/patch-settings.py uninstall $(DIR)
	@echo "Done. Restart Claude Code to deactivate the status line."
	@echo "Kept $(DIR)/statusline.toml"

test:
	uv run --with pytest pytest tests/ -v
