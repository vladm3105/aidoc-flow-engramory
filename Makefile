# Engramory dev convenience targets
.PHONY: help up down logs migrate pull-models lint typecheck test fmt smoke
.DEFAULT_GOAL := help

# Load .env so host-side commands (migrate) honor your local overrides.
ifneq (,$(wildcard .env))
include .env
export
endif

POSTGRES_USER ?= engramory
POSTGRES_DB   ?= engramory
ENGRAMORY_EMBED_MODEL ?= embed
ENGRAMORY_EMBED_DIMS  ?= 1536

help:          ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-12s %s\n", $$1, $$2}'

up:            ## Start the dev stack
	docker compose up -d

down:          ## Stop the dev stack
	docker compose down

logs:          ## Tail logs
	docker compose logs -f

migrate:       ## Apply all database migrations in order
	@for f in $$(ls db/migrations/*.sql | sort); do \
		echo "applying $$f"; \
		docker compose exec -T postgres psql -v ON_ERROR_STOP=1 \
			-U $(POSTGRES_USER) -d $(POSTGRES_DB) -f /migrations/$$(basename $$f); \
	done

pull-models:   ## Pull local Ollama chat model (--profile local-ai); embed uses 1536d LiteLLM gateway
	docker compose --profile local-ai exec ollama ollama pull llama3.1

lint:          ## Lint
	uv run ruff check src tests

typecheck:     ## Static type check
	uv run mypy src

fmt:           ## Format
	uv run ruff format src tests

test:          ## Run tests
	uv run pytest

smoke:         ## Pre-prod smoke: full agent memory loop via the engramory CLI (PLAN-002)
	bash scripts/smoke_preprod.sh
