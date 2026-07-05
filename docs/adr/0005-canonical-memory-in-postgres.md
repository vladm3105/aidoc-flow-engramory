# ADR-0005: Canonical memory in Postgres; projections are rebuildable

- **Status:** Accepted
- **Date:** 2026-06-22

## Context
The agent 'brain' must survive model and platform migrations (the user's primary concern).

## Decision
Store distilled memory as rows with content_raw + provenance + embedding_model/dims. Embeddings and the graph are projections that can be regenerated from canonical text.

## Consequences
On migration: re-embed from content_raw, re-project the graph. No vendor export button is required; owning the store is the guarantee.
