"""Dev-profile adapters: free/OSS backends from docker-compose.yml.

Implemented so far: PgVectorAdapter (VectorPort over pgvector in the canonical
Postgres) and LiteLLMAdapter (LLMPort over LiteLLM proxy). The remaining ports
gain dev adapters as their consumers arrive.
"""

from __future__ import annotations

from engramory.adapters.dev.graph_neo4j import Neo4jGraphAdapter
from engramory.adapters.dev.llm_litellm import LiteLLMAdapter
from engramory.adapters.dev.vector_pg import PgVectorAdapter

__all__ = ["LiteLLMAdapter", "Neo4jGraphAdapter", "PgVectorAdapter"]
