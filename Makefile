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

pull-models:   ## Pull local Ollama models used in dev
	docker compose exec ollama ollama pull llama3.1
	docker compose exec ollama ollama pull nomic-embed-text

lint:          ## Lint
	ruff check src

typecheck:     ## Static type check
	mypy src

fmt:           ## Format
	ruff format src

test:          ## Run tests
	pytest

smoke:         ## Pre-prod smoke: full agent memory loop via the engramory CLI (PLAN-002)
	bash scripts/smoke_preprod.sh
