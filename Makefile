SHELL := /bin/bash

UV ?= uv
PYTEST_MARK_EXPR ?= not requires_live_env
LOCAL_CONFIG := .orchestrator/config.json
LOCAL_CONFIG_EXAMPLE := .orchestrator/config.example.json

.PHONY: help bootstrap lint test smoke dependency-audit security node-check ci clean

help:
	@printf "ODay Plus developer commands\n\n"
	@printf "  make bootstrap   Prepare ignored local config needed by tests\n"
	@printf "  make lint        Run Python lint checks\n"
	@printf "  make test        Run CI-safe Python tests\n"
	@printf "  make smoke       Run fast foundation smoke tests\n"
	@printf "  make security    Run dependency audit and security acceptance tests\n"
	@printf "  make node-check  Run Node workspace checks when a lockfile exists\n"
	@printf "  make ci          Run the full CI baseline\n"
	@printf "  make clean       Remove local test and lint caches\n"

bootstrap:
	@if [[ ! -f "$(LOCAL_CONFIG)" ]]; then \
		cp "$(LOCAL_CONFIG_EXAMPLE)" "$(LOCAL_CONFIG)"; \
		printf "Created %s from %s\n" "$(LOCAL_CONFIG)" "$(LOCAL_CONFIG_EXAMPLE)"; \
	else \
		printf "Using existing %s\n" "$(LOCAL_CONFIG)"; \
	fi

lint: bootstrap
	$(UV) run ruff check .

test: bootstrap
	$(UV) run pytest -m "$(PYTEST_MARK_EXPR)"

smoke: bootstrap
	$(UV) run pytest tests/smoke

dependency-audit:
	@if [[ -f package-lock.json ]]; then \
		npm run audit:security; \
	else \
		printf "Skipping dependency audit: package-lock.json is not present yet.\n"; \
	fi

security: bootstrap dependency-audit
	$(UV) run pytest tests/security

node-check:
	@if [[ -f package-lock.json ]]; then \
		npm ci; \
		npm run lint --workspaces --if-present; \
		npm run typecheck --workspaces --if-present; \
		npm run build --workspaces --if-present; \
		npm run test --workspaces --if-present; \
	else \
		printf "Skipping Node workspace checks: package-lock.json is not present yet.\n"; \
	fi

ci: bootstrap lint security test smoke node-check

clean:
	rm -rf .pytest_cache .ruff_cache htmlcov .coverage .coverage.*
