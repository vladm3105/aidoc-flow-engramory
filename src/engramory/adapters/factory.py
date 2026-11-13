"""Adapter factory — wires the active adapter set from ENGRAMORY_PROFILE.

Only the ports with a consumer have adapters so far; the AdapterSet grows a
field per port as dev adapters land (cache, events, llm, storage, ...). gcp/azure sets
arrive with BRD-02 (cloud migration) and raise until then.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from engramory.adapters.dev.events_nats import NatsEventsAdapter
from engramory.adapters.dev.graph_neo4j import Neo4jGraphAdapter
from engramory.adapters.dev.llm_litellm import LiteLLMAdapter
from engramory.adapters.dev.storage_pg import PgBlobStorageAdapter
from engramory.adapters.dev.vector_pg import PgVectorAdapter
from engramory.ports import EventsPort, GraphPort, LLMPort, StoragePort, VectorPort

PROFILES = ("dev", "gcp", "azure")


@dataclass(frozen=True, slots=True)
class AdapterSet:
    """The active per-environment implementations of the ports."""

    profile: str
    vector: VectorPort
    llm: LLMPort
    graph: GraphPort | None = None
    events: EventsPort | None = None
    storage: StoragePort | None = None


def dev_adapters(
    dsn: str,
    neo4j_uri: str | None = None,
    nats_url: str | None = None,
    blob_dsn: str | None = None,
) -> AdapterSet:
    # GAP-03: PgBlobStorageAdapter connects directly to PostgreSQL :5432 via
    # ENGRAMORY_BLOB_DSN in stage/prod (bypassing PgBouncer :6432 transaction pooling),
    # falling back to dsn in local dev when ENGRAMORY_BLOB_DSN is unset.
    effective_blob_dsn = blob_dsn or os.environ.get("ENGRAMORY_BLOB_DSN") or dsn
    return AdapterSet(
        profile="dev",
        vector=PgVectorAdapter(dsn),
        llm=LiteLLMAdapter(),
        graph=Neo4jGraphAdapter(uri=neo4j_uri),
        events=NatsEventsAdapter(url=nats_url),
        storage=PgBlobStorageAdapter(effective_blob_dsn),
    )


def adapters_for_profile(profile: str | None = None, *, dsn: str) -> AdapterSet:
    """Select the adapter set; profile defaults to $ENGRAMORY_PROFILE, then dev."""
    resolved = profile or os.environ.get("ENGRAMORY_PROFILE", "dev")
    if resolved == "dev":
        return dev_adapters(dsn)
    if resolved in PROFILES:
        raise NotImplementedError(f"adapter set '{resolved}' arrives with BRD-02")
    raise ValueError(f"unknown ENGRAMORY_PROFILE: {resolved!r} (expected one of {PROFILES})")


def require_dev_profile(face: str, profile: str | None = None) -> str:
    """ADR-10 dev-tier fence: faces that construct ActorContext from
    client-supplied config (the CLI) are refused outside profile=dev —
    production identity is gateway-only (OIDC-verified claims)."""
    resolved = profile or os.environ.get("ENGRAMORY_PROFILE", "dev")
    if resolved != "dev":
        raise PermissionError(
            f"{face}: client-constructed ActorContext is dev-tier only (ADR-10); "
            f"ENGRAMORY_PROFILE={resolved!r} — use the MCP gateway"
        )
    return resolved
