# Settlement Witness developer commands.
#
# Run `make` or `make help` to list every target.
# `make setup` is the only command a fresh clone needs before `make ci`.
#
# `make ci` runs the core checks and needs neither Docker nor network access.
# `make verify` runs those plus the checks that do, which are the dependency
# audit and the container verification.

.DEFAULT_GOAL := help

BACKEND  := backend
FRONTEND := frontend
UV       ?= uv
PNPM     ?= pnpm

.PHONY: help setup dev dev-backend dev-frontend \
        test test-backend test-frontend \
        lint lint-backend lint-frontend \
        format format-backend format-frontend \
        typecheck typecheck-backend typecheck-frontend \
        build schema db-setup import-fixtures reconcile-fixtures audit ci \
        verify verify-containers clean \
        docker-build docker-up docker-down

help: ## List the available targets
	@echo "Settlement Witness"
	@echo ""
	@grep -hE '^[a-zA-Z0-9_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| sort \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

setup: ## Install every toolchain and dependency this repository needs
	@bash scripts/setup.sh

dev: ## Run the backend and frontend dev servers together
	@bash scripts/dev.sh

dev-backend: ## Run the backend dev server only
	cd $(BACKEND) && $(UV) run python -m app

dev-frontend: ## Run the frontend dev server only
	cd $(FRONTEND) && $(PNPM) run dev

test: test-backend test-frontend ## Run every test suite

test-backend: ## Run the backend tests with coverage
	cd $(BACKEND) && $(UV) run pytest

test-frontend: ## Run the frontend tests with coverage
	cd $(FRONTEND) && $(PNPM) run test

lint: lint-backend lint-frontend ## Check formatting and lint rules everywhere

lint-backend: ## Check backend formatting and lint rules
	cd $(BACKEND) && $(UV) run ruff format --check .
	cd $(BACKEND) && $(UV) run ruff check .

lint-frontend: ## Check frontend formatting and lint rules
	cd $(FRONTEND) && $(PNPM) run format:check
	cd $(FRONTEND) && $(PNPM) run lint

format: format-backend format-frontend ## Rewrite files into the project style

format-backend: ## Format backend files and apply safe lint fixes
	cd $(BACKEND) && $(UV) run ruff format .
	cd $(BACKEND) && $(UV) run ruff check --fix .

format-frontend: ## Format frontend files
	cd $(FRONTEND) && $(PNPM) run format

typecheck: typecheck-backend typecheck-frontend ## Type check everything

typecheck-backend: ## Type check the backend with mypy in strict mode
	cd $(BACKEND) && $(UV) run mypy

typecheck-frontend: ## Type check the frontend with the TypeScript compiler
	cd $(FRONTEND) && $(PNPM) run typecheck

build: ## Produce the frontend production bundle
	cd $(FRONTEND) && $(PNPM) run build

schema: ## Regenerate the published JSON Schema from the domain models
	cd $(BACKEND) && $(UV) run python -m app.schema_export

DB ?= data/generated/settlement.sqlite

db-setup: ## Create the SQLite schema at $(DB), safe to run again
	cd $(BACKEND) && $(UV) run python -m app.db_setup --database ../$(DB)

import-fixtures: db-setup ## Import the documented example documents into $(DB)
	cd $(BACKEND) && $(UV) run python -m app.ingest_cli --database ../$(DB) \
		--source-system PSP_API --record-type PAYMENT_EVENT \
		../data/fixtures/ingestion/payment_events.csv
	cd $(BACKEND) && $(UV) run python -m app.ingest_cli --database ../$(DB) \
		--source-system PSP_API --record-type SETTLEMENT_LINE \
		../data/fixtures/ingestion/settlement_lines.csv
	cd $(BACKEND) && $(UV) run python -m app.ingest_cli --database ../$(DB) \
		--source-system PSP_API --record-type PAYOUT \
		../data/fixtures/ingestion/payouts.csv

reconcile-fixtures: ## Reconcile the facts in $(DB) and print JSON
	cd $(BACKEND) && $(UV) run python -m app.reconcile_cli --database ../$(DB)

audit: ## Report known vulnerabilities in the locked dependencies (needs network)
	cd $(BACKEND) && $(UV) run --with pip-audit pip-audit
	cd $(FRONTEND) && $(PNPM) audit --audit-level moderate

ci: lint typecheck test build ## Run the core local checks that CI mirrors

verify: ci audit verify-containers ## Run the core checks plus the audit and the container checks

verify-containers: ## Build the images, start them, and check they serve and run as non-root (needs Docker)
	@bash scripts/verify-containers.sh

clean: ## Remove build output, caches and coverage reports
	rm -rf $(FRONTEND)/dist $(FRONTEND)/coverage $(FRONTEND)/node_modules/.tmp
	rm -rf $(BACKEND)/.pytest_cache $(BACKEND)/.mypy_cache $(BACKEND)/.ruff_cache
	rm -f  $(BACKEND)/.coverage $(BACKEND)/coverage.xml
	find . -type d -name __pycache__ -not -path './*/node_modules/*' -exec rm -rf {} +

docker-build: ## Build both container images
	docker compose build

docker-up: ## Start both services in containers
	docker compose up --build

docker-down: ## Stop the containers and remove their volumes
	docker compose down --volumes --remove-orphans
