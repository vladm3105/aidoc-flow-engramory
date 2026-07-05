# Portability — self-hosted → GCP or Azure

Portability is a first-class goal. The rules that guarantee it:

1. **Depend on ports, not vendors.** Every infrastructure dependency is reached through an interface in `engramory.ports`; each environment supplies adapters. Cloud migration = select a different adapter set (`ENGRAMORY_PROFILE`).
2. **PostgreSQL is the canonical spine.** Relational data, vectors (pgvector), and distilled memory live in Postgres — identical self-hosted, on GCP (Cloud SQL / AlloyDB), and on Azure (Flexible Server). Migration of canonical data = `pg_dump` + restore.
3. **Embeddings and the graph are rebuildable projections.** Always keep `content_raw` + `embedding_model` + dims so memory can be **re-embedded** after a model change. The graph (Neo4j) can be **re-projected** by re-running entity extraction — losing it is a rebuild, not data loss.
4. **One model gateway.** All LLM/embedding calls go through LiteLLM. Dev → Ollama; GCP → Vertex AI; Azure → Azure OpenAI — a config change.

## Cross-environment component map

| Capability (Port) | Self-hosted dev | GCP | Azure |
|---|---|---|---|
| Relational + Vector | Postgres 16 + pgvector | Cloud SQL / AlloyDB (pgvector) | Azure DB for PostgreSQL Flexible (pgvector) |
| Graph | pure Postgres → Neo4j Community | Neo4j Aura (or pure-Postgres) | Neo4j Aura (or managed Apache AGE) |
| Object storage | MinIO | Cloud Storage | Azure Blob |
| Cache / L1 | Redis | Memorystore | Azure Cache for Redis |
| LLM / embeddings | LiteLLM → Ollama | LiteLLM → Vertex AI | LiteLLM → Azure OpenAI |
| Secrets | SOPS / Infisical | Secret Manager | Key Vault |
| Identity | Keycloak | Identity Platform | Entra ID |
| Events | Redis Streams / NATS | Pub/Sub | Service Bus / Event Grid |
| Compute | Docker Compose | Cloud Run / GKE | Container Apps / AKS |

## Graph engine note
Managed **Apache AGE** exists on Azure Postgres but **not** on GCP Cloud SQL/AlloyDB. Since the cloud is undecided, prefer **Neo4j** (multi-cloud managed Aura) when a dedicated graph engine is needed; keep pure-Postgres while graph needs are shallow. Decision recorded in [adr/0003-graph-engine-neo4j-over-age.md](adr/0003-graph-engine-neo4j-over-age.md).
