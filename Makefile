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
        build schema db-setup api import-fixtures import-fixtures-http reconcile-fixtures \
        benchmark-generate benchmark-evaluate benchmark-evaluate-private phase-13 audit ci \
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

db-setup: ## Migrate the SQLite schema at $(DB) to head, safe to run again
	cd $(BACKEND) && $(UV) run python -m app.db_setup --database ../$(DB)

api: ## Run the backend API against $(DB) at http://127.0.0.1:8000
	cd $(BACKEND) && SW_DATABASE_PATH=../$(DB) $(UV) run python -m app

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

API_URL ?= http://127.0.0.1:8000

import-fixtures-http: ## Import the example documents through the running API at $(API_URL)
	@echo "==> Importing the three example documents through $(API_URL)"
	@for pair in "PAYMENT_EVENT:payment_events.csv" \
	             "SETTLEMENT_LINE:settlement_lines.csv" \
	             "PAYOUT:payouts.csv"; do \
		type=$${pair%%:*}; file=$${pair#*:}; \
		curl --silent --show-error --fail-with-body \
			--request POST "$(API_URL)/v1/imports" \
			--form "file=@data/fixtures/ingestion/$$file;type=text/csv" \
			--form "source_system=PSP_API" \
			--form "record_type=$$type" \
		| python3 -c "import json,sys; r=json.load(sys.stdin); \
			print(f\"  {r['source_record_type']:16s} {r['outcome']:18s} \
rows={r['row_count']} accepted={r['accepted_count']} receipt={r['receipt_id']}\")"; \
	done
	@echo "==> Import history"
	@curl --silent --fail-with-body "$(API_URL)/v1/imports?limit=5" \
		| python3 -c "import json,sys; p=json.load(sys.stdin); \
			print(f\"  {p['total']} receipt(s)\"); \
			[print(f\"  {r['received_at']}  {r['outcome']:18s} {r['document_name']}\") \
			 for r in p['receipts']]"
	@echo "==> Reconciling what was imported"
	@curl --silent --show-error --fail-with-body --request POST "$(API_URL)/v1/reconciliation/runs" \
		| python3 -c "import json,sys; r=json.load(sys.stdin); \
			print(f\"  run {r['run_id']}  facts={r['fact_count']} decisions={r['decision_count']}\")"

reconcile-fixtures: ## Reconcile the facts in $(DB) and print JSON
	cd $(BACKEND) && $(UV) run python -m app.reconcile_cli --database ../$(DB)

BENCHMARK_CONFIG ?= benchmark/public-corpus.json
BENCHMARK_OUT    ?= data/generated/benchmark

benchmark-generate: ## Write the public synthetic corpus to $(BENCHMARK_OUT)
	cd $(BACKEND) && $(UV) run python -m app.benchmark_cli generate \
		--config ../$(BENCHMARK_CONFIG) --output ../$(BENCHMARK_OUT)/public

benchmark-evaluate: ## Evaluate the baseline on the public synthetic corpus
	cd $(BACKEND) && $(UV) run python -m app.benchmark_cli evaluate \
		--config ../$(BENCHMARK_CONFIG) \
		--report ../$(BENCHMARK_OUT)/public-report.json

benchmark-evaluate-private: ## Evaluate an externally supplied config: make benchmark-evaluate-private CONFIG=path
	@test -n "$(CONFIG)" || (echo "set CONFIG=path/to/private-corpus.json" && exit 1)
	cd $(BACKEND) && $(UV) run python -m app.benchmark_cli evaluate --config ../$(CONFIG)

phase-13: ## Run the fixed three-attempt hosted shadow-evaluation protocol
	@bash scripts/run-phase-13.sh

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
