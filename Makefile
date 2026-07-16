SHELL := /bin/bash

UV ?= uv
PYTEST_MARK_EXPR ?= not requires_live_env
LOCAL_CONFIG := .orchestrator/config.json
LOCAL_CONFIG_EXAMPLE := .orchestrator/config.example.json

.PHONY: help bootstrap lint test smoke dependency-audit security node-check api-contract api-contract-refresh product-e2e-gate product-release-gate ci clean

help:
	@printf "ODay Plus developer commands\n\n"
	@printf "  make bootstrap   Prepare ignored local config needed by tests\n"
	@printf "  make lint        Run Python lint checks\n"
	@printf "  make test        Run CI-safe Python tests\n"
	@printf "  make smoke       Run fast foundation smoke tests\n"
	@printf "  make security    Run dependency audit and security acceptance tests\n"
	@printf "  make node-check  Run Node workspace checks when a lockfile exists\n"
	@printf "  make product-e2e-gate  Run product E2E release gate checks\n"
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

# API contract gate (ODP-PGAP-API-001): the OpenAPI artifact matches the live
# app, the generated TypeScript client matches the artifact, and no unapproved
# breaking change reaches the target branch.
# Regenerate after an intentional API change with:
#   make api-contract-refresh
api-contract: bootstrap
	$(UV) run python scripts/openapi/check_drift.py --base-ref $${ODP_API_BASE_REF:-origin/dev}

api-contract-refresh: bootstrap
	$(UV) run python scripts/openapi/export_openapi.py
	$(UV) run python scripts/openapi/generate_client.py

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

product-e2e-gate:
	python3 scripts/e2e/check_product_release_gate.py

product-release-gate: product-e2e-gate
	scripts/e2e/run_product_e2e.sh

ci: bootstrap lint security test smoke node-check

clean:
	rm -rf .pytest_cache .ruff_cache htmlcov .coverage .coverage.*
