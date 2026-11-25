"""Ports: the interfaces the application core depends on.

The core / business code imports only from here — never from a concrete adapter or a
vendor SDK. Concrete adapters live in ``engramory.adapters`` and are selected per
environment (ENGRAMORY_PROFILE = dev | stage [active] | gcp | azure [planned]).

The eight ports (active dev/stage adapters vs. planned/spine):
    MemoryPort   — experiential memory interface (spine: Repository; adapter planned) (memory.py)
    StoragePort  — object/CAS storage (dev/stage: PgBlobStorageAdapter)               (storage.py)
    VectorPort   — similarity search over embeddings (dev/stage: PgVectorAdapter)     (vector.py)
    GraphPort    — entity/relationship graph (dev/stage: Neo4jGraphAdapter)           (graph.py)
    CachePort    — ephemeral key/value cache (planned interface; no concrete adapter) (cache.py)
    LLMPort      — chat + embeddings via LiteLLM (dev/stage: LiteLLMAdapter)          (llm.py)
    SecretsPort  — secret resolution (OpenBao/env runtime; planned adapter)           (secrets.py)
    EventsPort   — publish/subscribe (dev/stage: NatsEventsAdapter)                   (events.py)

Identity / auth is deliberately NOT a core port: it is enforced at the MCP gateway via
Authentik OIDC (dev/stage) or Identity Platform / Entra ID (cloud). See docs/ARCHITECTURE.md.
"""

from __future__ import annotations

from .cache import CachePort
from .events import EventsPort
from .graph import GraphPort
from .llm import LLMPort
from .memory import MemoryPort
from .secrets import SecretsPort
from .storage import StoragePort
from .vector import VectorPort

__all__ = [
    "CachePort",
    "EventsPort",
    "GraphPort",
    "LLMPort",
    "MemoryPort",
    "SecretsPort",
    "StoragePort",
    "VectorPort",
]
