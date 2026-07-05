# Engramory dev convenience targets
.PHONY: up down logs migrate pull-models lint test fmt

up:            ## Start the dev stack
	docker compose up -d

down:          ## Stop the dev stack
	docker compose down

logs:          ## Tail logs
	docker compose logs -f

migrate:       ## Apply database migrations
	docker compose exec -T postgres psql -U $${POSTGRES_USER:-engramory} -d $${POSTGRES_DB:-engramory} -f /migrations/0001_init_memory.sql

pull-models:   ## Pull local Ollama models used in dev
	docker compose exec ollama ollama pull llama3.1
	docker compose exec ollama ollama pull nomic-embed-text

lint:          ## Lint
	ruff check src

fmt:           ## Format
	ruff format src

test:          ## Run tests
	pytest -q
