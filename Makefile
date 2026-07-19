.PHONY: help setup dry-run test audit verify merge clean

HERMES_HOME ?= $(HOME)/.hermes
PYTHON      ?= python3

help: ## Show this help
	@grep -hE '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | awk -F':.*?## ' '{printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

setup: ## Install Hermes + this starter's patch and examples
	./setup.sh

dry-run: ## Show what setup.sh would do, without changing anything
	./setup.sh --dry-run

test: ## Run the test suite
	$(PYTHON) -m pytest tests second-brain/tests -q

audit: ## Scan this repo for secrets, PII and local paths
	$(PYTHON) scripts/audit_public.py .

verify: ## Everything: shell syntax, python, tests, audit, patch check
	./verify.sh

merge: ## Dry-run merge the feature overlay into your live config
	$(PYTHON) scripts/merge_config.py --base $(HERMES_HOME)/config.yaml --overlay config.example.yaml

clean: ## Remove Python caches
	find . -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache second-brain/.pytest_cache second-brain/src/*.egg-info
