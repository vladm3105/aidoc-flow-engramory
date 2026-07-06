# Engramory roadmap

This file is the **engineering Phase view** (Phase 0–3). The canonical plan for
scope and sequencing is the MVP cycle plan in [`../roadmap/ROADMAP.md`](../roadmap/ROADMAP.md)
(MVP-1..8). Approximate mapping: **Phase 0–2 ≈ MVP-1** (self-hosted memory &
knowledge core); **Phase 3 ≈ MVP-2** (cloud migration).

| Phase | Goal | Key work | Outcome |
|---|---|---|---|
| **0 — Dev foundation** | Cheap self-hosted base | `docker compose up`: Postgres+pgvector, Redis, MinIO, LiteLLM+Ollama, Neo4j, Keycloak. Define all Ports. Apply memory schema. | Free local platform, portable by construction |
| **1 — Consolidate** | One platform | Fold RAC's MCP tools/parsers onto the core; RAC becomes the per-project domain-config layer (Trading = one example project); everything behind Ports | RAC + Nexus v3 merged, no stack duplication |
| **2 — Cognition** | The differentiator | `agent_id` scoping; reflection + consolidation workers; wire a MemoryPort adapter (LangMem/Cipher/Mem0) | Per-agent distilled, endless memory |
| **3 — Cloud migration** | GCP or Azure | Swap adapters (managed Postgres, Redis, object store, model endpoint, secrets, identity); IaC; pg_dump + object copy; re-embed if model changes | Same app, cloud-native, data intact |

Phases 0–2 are entirely free/self-hosted. Phase 3 is an adapter swap, not a rewrite.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full design and the portability matrix.
