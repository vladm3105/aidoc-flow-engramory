# Portability — self-hosted → GCP or Azure

Portability is a first-class goal. The rules that guarantee it:

1. **Depend on ports, not vendors.** Every infrastructure dependency is reached through an interface in `engramory.ports`; each environment supplies adapters. Cloud migration = select a different adapter set (`ENGRAMORY_PROFILE`).
2. **PostgreSQL is the canonical spine.** Relational data, vectors (pgvector), and distilled memory live in Postgres — identical self-hosted, on GCP (Cloud SQL / AlloyDB), and on Azure (Flexible Server). Migration of canonical data = `pg_dump` + restore.
3. **Embeddings and the graph are rebuildable projections.** Always keep `content_raw` + `embedding_model` + dims so memory can be **re-embedded** after a model change. The graph (Neo4j) can be **re-projected** by re-running entity extraction — losing it is a rebuild, not data loss. (Re-embedding is *deterministic* from the same text+model; graph re-projection re-runs LLM extraction and is **not** bit-identical — so any human-curated graph edits must be persisted as their own source, not treated as a disposable projection.)
4. **One model gateway.** All LLM/embedding calls go through LiteLLM. Dev → Ollama; GCP → Vertex AI; Azure → Azure OpenAI — a config change.

## Cross-environment component map

The core depends on **8 ports**: Memory, Storage, Vector, Graph, Cache, LLM, Secrets, Events. Identity/auth is **not** a core port — it is a **gateway concern** (Keycloak/OIDC at the MCP gateway).

| Capability (Port) | Self-hosted dev | GCP | Azure |
|---|---|---|---|
| Memory (`MemoryPort`) + Storage | PostgreSQL 18 (canonical rows) | Cloud SQL / AlloyDB | Azure DB for PostgreSQL Flexible |
| Vector | pgvector (`vector(1536)` HNSW) | Cloud SQL / AlloyDB (pgvector) | Azure DB for PostgreSQL Flexible (pgvector) |
| Graph | pure Postgres → Neo4j 5.26 Community | Neo4j Aura (or pure-Postgres) | Neo4j Aura (or managed Apache AGE) |
| Object & Multimodal Blob storage (`StoragePort`) | PostgreSQL 18 Chunked Blob CAS (`PgBlobStorageAdapter` in `ts_blob_cold` up to `20 GB+`) or OCI Object Storage | Cloud Storage | Azure Blob |
| Cache / L1 | Valkey / Redis | Memorystore | Azure Cache for Redis |
| LLM / embeddings | LiteLLM → Ollama | LiteLLM → Vertex AI | LiteLLM → Azure OpenAI |
| Secrets | OpenBao / SOPS | Secret Manager | Key Vault |
| Events | NATS JetStream / Redis Streams | Pub/Sub | Service Bus / Event Grid |
| Compute | Docker Compose | Cloud Run / GKE | Container Apps / AKS |
| Identity/auth (**gateway concern, not a core port**) | Authentik / Keycloak OIDC | Identity Platform | Entra ID |

## Virtual Employee Brain Portability (`engramory.brain_passport.v1`)

In addition to infrastructure portability across self-hosted, GCP, and Azure environments, Engramory supports **Virtual Employee Brain Portability** across tenants and sibling agent instances (see [VIRTUAL_EMPLOYEE_ARCHITECTURE.md](VIRTUAL_EMPLOYEE_ARCHITECTURE.md)):

1. **Sanitized Export (`agent_brain_export`):** Exports the Virtual Employee's L3 `AgentProfile` (`agent_type`, `specializations`, `brain_metadata`, `standing_preferences`) and high-confidence L2 `scope='agent'` distilled skills, recursively scrubbing secrets, emails, URLs, IPs, UUIDs, and custom `redact_terms` via `sanitize_portable_value()` while omitting all raw L1 `episodes` and `episode_id` links.
2. **Cryptographic Integrity & Archetype Guard (`agent_brain_import`):** Verifies the canonical `passport_sha256` digest fail-closed, enforces `agent_type` compatibility (`agent_type_mismatch` guard unless `allow_type_override=True`), preserves sibling instance `display_name` while union-merging `specializations`, and re-embeds imported skill summaries into the target tenant's `1536`-dim vector space.
3. **Clean Source Offboarding (`agent_space_offboard`):** Transactionally retires private `scope='agent'` memories, purges private `scope='agent'` episodes, and removes the L3 profile on the source tenant while leaving shared `project`/`domain`/`space` artifacts untouched.

## Graph engine note

Managed **Apache AGE** exists on Azure Postgres but **not** on GCP Cloud SQL/AlloyDB. Since the cloud is undecided, prefer **Neo4j** (multi-cloud managed Aura) when a dedicated graph engine is needed; keep pure-Postgres while graph needs are shallow. Decision recorded in [adr/0003-graph-engine-neo4j-over-age.md](adr/0003-graph-engine-neo4j-over-age.md).
