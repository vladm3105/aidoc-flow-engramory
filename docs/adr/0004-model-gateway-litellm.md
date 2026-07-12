# ADR-0004: LiteLLM as the single model gateway

- **Status:** Accepted
- **Date:** 2026-06-22

## Context

Agents and the core must call models without binding to a provider; dev should be free/local.

## Decision

Route all LLM/embedding traffic through a self-hosted LiteLLM proxy (OpenAI-compatible). Dev → Ollama; GCP → Vertex AI; Azure → Azure OpenAI, by config only.

## Consequences

Switching providers/models is configuration, not code. Cost tracking and budgets are centralized.
