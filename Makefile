# Settlement Witness developer commands.
#
# Run `make` or `make help` to list every target.
# `make setup` is the only command a fresh clone needs before `make ci`.

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
        build audit ci clean docker-build docker-up docker-down

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

audit: ## Report known vulnerabilities in the locked dependencies
	cd $(BACKEND) && $(UV) run --with pip-audit pip-audit
	cd $(FRONTEND) && $(PNPM) audit --audit-level moderate

ci: lint typecheck test build ## Run the same checks the pipeline runs

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
