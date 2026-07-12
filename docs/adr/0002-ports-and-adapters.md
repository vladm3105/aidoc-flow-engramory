# ADR-0002: Ports & adapters (hexagonal)

- **Status:** Accepted
- **Date:** 2026-06-22

## Context

The platform must run cheaply self-hosted now and migrate to GCP or Azure later without rewrites.

## Decision

The core depends only on interfaces (engramory.ports). Each environment provides adapters selected by ENGRAMORY_PROFILE.

## Consequences

Cloud migration becomes an adapter swap. Core/business code never imports a vendor SDK.
