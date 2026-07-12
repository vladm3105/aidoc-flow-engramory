# ADR-0001: PostgreSQL as the canonical spine

- **Status:** Accepted
- **Date:** 2026-06-22

## Context

We need one store for relational data, vectors, and distilled memory that exists identically self-hosted and on GCP/Azure.

## Decision

Use PostgreSQL 16 with pgvector as the canonical store. All durable memory lives here as plain rows.

## Consequences

Canonical data migrates via pg_dump/restore to any managed Postgres on either cloud. Vector index dimension is set per deployment.
