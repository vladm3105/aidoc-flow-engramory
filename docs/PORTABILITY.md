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
| Memory (`MemoryPort`) + Storage | Postgres 16 (canonical rows) | Cloud SQL / AlloyDB | Azure DB for PostgreSQL Flexible |
| Vector | pgvector | Cloud SQL / AlloyDB (pgvector) | Azure DB for PostgreSQL Flexible (pgvector) |
| Graph | pure Postgres → Neo4j Community | Neo4j Aura (or pure-Postgres) | Neo4j Aura (or managed Apache AGE) |
| Object storage (Storage) | MinIO | Cloud Storage | Azure Blob |
| Cache / L1 | Redis | Memorystore | Azure Cache for Redis |
| LLM / embeddings | LiteLLM → Ollama | LiteLLM → Vertex AI | LiteLLM → Azure OpenAI |
| Secrets | SOPS / Infisical | Secret Manager | Key Vault |
| Events | Redis Streams / NATS | Pub/Sub | Service Bus / Event Grid |
| Compute | Docker Compose | Cloud Run / GKE | Container Apps / AKS |
| Identity/auth (**gateway concern, not a core port**) | Keycloak / OIDC | Identity Platform | Entra ID |

## Graph engine note
Managed **Apache AGE** exists on Azure Postgres but **not** on GCP Cloud SQL/AlloyDB. Since the cloud is undecided, prefer **Neo4j** (multi-cloud managed Aura) when a dedicated graph engine is needed; keep pure-Postgres while graph needs are shallow. Decision recorded in [adr/0003-graph-engine-neo4j-over-age.md](adr/0003-graph-engine-neo4j-over-age.md).
