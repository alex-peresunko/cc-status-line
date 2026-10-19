CLAUDE_DIR := $(HOME)/.claude
SCRIPT     := statusline.py

.PHONY: install uninstall test

install:
	ln -sf $(PWD)/$(SCRIPT)  $(CLAUDE_DIR)/$(SCRIPT)
	python3 $(PWD)/scripts/patch-settings.py install
	@echo "Done. Restart Claude Code to activate the status line."

uninstall:
	rm -f $(CLAUDE_DIR)/$(SCRIPT)
	python3 $(PWD)/scripts/patch-settings.py uninstall
	@echo "Done. Restart Claude Code to deactivate the status line."

test:
	uv run --with pytest pytest tests/ -v
