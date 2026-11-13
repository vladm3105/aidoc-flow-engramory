"""Dev-profile adapters: free/OSS backends from docker-compose.yml.

Implemented adapters:
- ``PgVectorAdapter`` (``VectorPort`` over pgvector in canonical Postgres)
- ``PgBlobStorageAdapter`` & ``LocalFsStorageAdapter`` (``StoragePort`` 4 MiB chunked CAS)
- ``LiteLLMAdapter`` (``LLMPort`` over LiteLLM proxy: chat, embed, describe_image, transcribe_audio)
- ``Neo4jGraphAdapter`` (``GraphPort`` over Neo4j Bolt driver)
- ``NatsEventsAdapter`` (``EventsPort`` over NATS JetStream)
"""

from __future__ import annotations

from engramory.adapters.dev.events_nats import NatsEventsAdapter
from engramory.adapters.dev.graph_neo4j import Neo4jGraphAdapter
from engramory.adapters.dev.llm_litellm import LiteLLMAdapter
from engramory.adapters.dev.storage_local import LocalFsStorageAdapter
from engramory.adapters.dev.storage_pg import PgBlobStorageAdapter
from engramory.adapters.dev.vector_pg import PgVectorAdapter

__all__ = [
    "LiteLLMAdapter",
    "LocalFsStorageAdapter",
    "NatsEventsAdapter",
    "Neo4jGraphAdapter",
    "PgBlobStorageAdapter",
    "PgVectorAdapter",
]
