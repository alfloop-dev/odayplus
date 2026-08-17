SHELL := /bin/bash

UV ?= uv
PYTEST_MARK_EXPR ?= not requires_live_env
LOCAL_CONFIG := .orchestrator/config.json
LOCAL_CONFIG_EXAMPLE := .orchestrator/config.example.json

.PHONY: help bootstrap boundary-check lint test smoke dependency-audit security node-check api-contract api-contract-refresh release-gate-registry task-dependency-check product-e2e-gate product-release-gate ci clean

help:
	@printf "ODay Plus developer commands\n\n"
	@printf "  make bootstrap   Prepare ignored local config needed by tests\n"
	@printf "  make boundary-check  Enforce product/development/removal boundaries\n"
	@printf "  make lint        Run Python lint checks\n"
	@printf "  make test        Run CI-safe Python tests\n"
	@printf "  make smoke       Run fast foundation smoke tests\n"
	@printf "  make security    Run dependency audit and security acceptance tests\n"
	@printf "  make node-check  Run Node workspace checks when a lockfile exists\n"
	@printf "  make release-gate-registry  Validate the Gate 0-6 release registry\n"
	@printf "  make task-dependency-check Verify every task depends_on resolves (Control Pack 3.1)\n"
	@printf "  make product-e2e-gate  Run ordinary dev-merge product E2E checks\n"
	@printf "  make product-release-gate  Require final production GO authorization\n"
	@printf "  make ci          Run the full CI baseline\n"
	@printf "  make clean       Remove local test and lint caches\n"

bootstrap:
	@if [[ ! -f "$(LOCAL_CONFIG)" ]]; then \
		cp "$(LOCAL_CONFIG_EXAMPLE)" "$(LOCAL_CONFIG)"; \
		printf "Created %s from %s\n" "$(LOCAL_CONFIG)" "$(LOCAL_CONFIG_EXAMPLE)"; \
	else \
		printf "Using existing %s\n" "$(LOCAL_CONFIG)"; \
	fi

boundary-check:
	$(UV) run python delivery_toolchain/governance/check_code_boundaries.py

lint: bootstrap
	$(UV) run ruff check .orchestrator delivery_toolchain scripts tests modules apps shared models solver pipelines infra

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
	$(UV) run --with pip-audit pip-audit --local


security: bootstrap dependency-audit
	$(UV) run pytest tests/security

# API contract gate (ODP-PGAP-API-001): the OpenAPI artifact matches the live
# app, the generated TypeScript client matches the artifact, and no unapproved
# breaking change reaches the target branch.
# Regenerate after an intentional API change with:
#   make api-contract-refresh
api-contract: bootstrap
	$(UV) run python delivery_toolchain/openapi/check_drift.py --base-ref $${ODP_API_BASE_REF:-origin/dev}

api-contract-refresh: bootstrap
	$(UV) run python delivery_toolchain/openapi/export_openapi.py
	$(UV) run python delivery_toolchain/openapi/generate_client.py

node-check:
	@if [[ -f package-lock.json ]]; then \
		npm ci && \
		npm run lint --workspaces --if-present && \
		npm run typecheck --workspaces --if-present && \
		npm run build --workspaces --if-present && \
		npm run bundle:budget --workspaces --if-present && \
		npm run test --workspaces --if-present; \
	else \
		printf "Skipping Node workspace checks: package-lock.json is not present yet.\n"; \
	fi

release-gate-registry:
	python3 delivery_toolchain/e2e/check_release_gate_registry.py

# Dispatch preflight for Control Pack 3.1: every task `depends_on` entry must
# resolve through the live board or the official archive. Supervisor state lives
# outside this repo, so point the target at it explicitly:
#   make task-dependency-check \
#     ODP_SUPERVISOR_STATUS_FILE=/path/to/ai-status.json \
#     ODP_SUPERVISOR_ARCHIVE_DIR=/path/to/ai-task-archive/tasks
task-dependency-check:
	@if [[ -z "$(ODP_SUPERVISOR_STATUS_FILE)" || -z "$(ODP_SUPERVISOR_ARCHIVE_DIR)" ]]; then \
		printf "Set ODP_SUPERVISOR_STATUS_FILE and ODP_SUPERVISOR_ARCHIVE_DIR to the live supervisor state.\n"; \
		exit 2; \
	fi
	python3 scripts/orchestrator/check_task_dependency_resolvability.py \
		--status "$(ODP_SUPERVISOR_STATUS_FILE)" \
		--archive-dir "$(ODP_SUPERVISOR_ARCHIVE_DIR)"

product-e2e-gate: release-gate-registry
	python3 delivery_toolchain/e2e/check_product_release_gate.py --dev-merge
	delivery_toolchain/e2e/run_product_e2e.sh

product-release-gate:
	python3 delivery_toolchain/e2e/check_product_release_gate.py --require-go $(if $(EXPECTED_SHA),--expected-sha $(EXPECTED_SHA))

ci: bootstrap lint security test smoke node-check boundary-check

clean:
	rm -rf .pytest_cache .ruff_cache htmlcov .coverage .coverage.*
