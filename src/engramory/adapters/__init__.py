"""Adapters implement ports per environment.

Layout (create as needed):
    adapters/dev/    - Postgres+pgvector, Redis, MinIO, Neo4j, LiteLLM->Ollama, Keycloak
    adapters/gcp/    - Cloud SQL/AlloyDB, Memorystore, GCS, Neo4j Aura, Vertex, Secret Manager
    adapters/azure/  - Azure PG Flexible, Azure Cache, Blob, Neo4j Aura/AGE, Azure OpenAI, Key Vault

A factory selects the adapter set from ENGRAMORY_PROFILE (dev|gcp|azure).
"""
