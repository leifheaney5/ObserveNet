SHELL := /bin/sh
COMPOSE ?= docker compose
BACKUP_DIR ?= backups

.PHONY: help init up down logs ps demo demo-down test lint backend-test agent-test frontend-test e2e migrate create-admin backup restore openapi fmt

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-16s %s\n", $$1, $$2}'

init: ## Create .env with random secrets (never overwrites)
	@./scripts/init-env.sh

up: ## Build and start the stack (UI on http://127.0.0.1:8080)
	$(COMPOSE) up --build -d

down: ## Stop the stack (data volumes are kept)
	$(COMPOSE) down

logs: ## Follow logs
	$(COMPOSE) logs -f --tail=100

ps: ## Service status
	$(COMPOSE) ps

demo: ## Start with clearly labelled synthetic data (no collector)
	OBSERVENET_DEMO_MODE=true COMPOSE_PROFILES= $(COMPOSE) up --build -d

demo-down: ## Stop the demo and delete its volumes
	OBSERVENET_DEMO_MODE=true COMPOSE_PROFILES= $(COMPOSE) down -v

migrate: ## Apply database migrations
	$(COMPOSE) run --rm migrate

create-admin: ## Create an administrator interactively
	$(COMPOSE) exec api python -m observenet.cli create-admin

backup: ## Dump PostgreSQL to backups/observenet-<timestamp>.sql.gz
	@mkdir -p $(BACKUP_DIR)
	@f=$(BACKUP_DIR)/observenet-$$(date -u +%Y%m%dT%H%M%SZ).sql.gz; \
	$(COMPOSE) exec -T postgres pg_dump -U observenet -d observenet --no-owner | gzip > $$f && echo "wrote $$f"

restore: ## Restore a dump: make restore FILE=backups/observenet-....sql.gz (replaces current data)
	@test -n "$(FILE)" || (echo "usage: make restore FILE=backups/<dump>.sql.gz" && exit 1)
	$(COMPOSE) stop api worker scheduler
	$(COMPOSE) exec -T postgres psql -U observenet -d postgres -c "DROP DATABASE IF EXISTS observenet WITH (FORCE)" -c "CREATE DATABASE observenet OWNER observenet"
	gunzip -c $(FILE) | $(COMPOSE) exec -T postgres psql -q -U observenet -d observenet
	$(COMPOSE) start api worker scheduler

backend-test: ## Backend lint, types and tests (needs PostgreSQL; see README)
	cd backend && uv sync --all-extras && uv run ruff check . && uv run ruff format --check . && uv run mypy observenet && uv run pytest

agent-test: ## Linux agent lint, types and tests
	cd agents/linux && uv sync && uv run ruff check . && uv run ruff format --check . && uv run mypy observenet_agent && uv run pytest

frontend-test: ## Frontend lint, types, unit tests and build
	cd frontend && npm ci && npm run lint && npm run typecheck && npm test && npm run build

lint: ## Lint everything
	cd backend && uv run ruff check . && uv run ruff format --check .
	cd agents/linux && uv run ruff check . && uv run ruff format --check .
	cd frontend && npm run lint

fmt: ## Format Python code
	cd backend && uv run ruff format . && uv run ruff check --fix .
	cd agents/linux && uv run ruff format . && uv run ruff check --fix .

test: backend-test agent-test frontend-test ## Run all unit/integration tests

e2e: ## Playwright smoke tests against a running stack (E2E_BASE_URL, E2E_PASSWORD)
	cd frontend && npm run e2e

openapi: ## Regenerate OpenAPI/GraphQL schema files and the TypeScript API types
	cd backend && uv run python -m observenet.cli export-openapi openapi.json && uv run python -m observenet.cli export-graphql schema.graphql
	cd frontend && npm run gen:api
