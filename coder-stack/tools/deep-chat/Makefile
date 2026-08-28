PYTHON ?= python3.11

.PHONY: test syntax shellcheck

test:
	bash tests/test_deep_chat.sh

syntax:
	bash -n hermes-deep-chat install-local.sh tests/test_deep_chat.sh
	PYTHONPYCACHEPREFIX=$${TMPDIR:-/tmp}/hermes-deep-chat-pycache $(PYTHON) -m py_compile secure_runtime.py claude_worker.py deep_chat_bridge.py

shellcheck:
	@if command -v shellcheck >/dev/null 2>&1; then \
		shellcheck -S warning hermes-deep-chat install-local.sh tests/test_deep_chat.sh; \
	else \
		echo "shellcheck not installed; skipped"; \
	fi
