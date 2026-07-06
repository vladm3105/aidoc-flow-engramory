"""Ports: the interfaces the application core depends on.

The core / business code imports only from here — never from a concrete adapter or a
vendor SDK. Concrete adapters live in ``engramory.adapters`` and are selected per
environment (ENGRAMORY_PROFILE = dev | gcp | azure).

The eight ports:
    MemoryPort   — experiential memory (L1/L2/L3)       (memory.py)
    StoragePort  — object storage                        (storage.py)
    VectorPort   — similarity search over embeddings     (vector.py)
    GraphPort    — entity/relationship graph             (graph.py)
    CachePort    — ephemeral key/value cache             (cache.py)
    LLMPort      — chat + embeddings via LiteLLM          (llm.py)
    SecretsPort  — secret resolution                      (secrets.py)
    EventsPort   — publish/subscribe                      (events.py)

Identity / auth is deliberately NOT a core port: it is enforced at the MCP gateway via
Keycloak/OIDC (dev) or Identity Platform / Entra ID (cloud). See docs/ARCHITECTURE.md.
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
