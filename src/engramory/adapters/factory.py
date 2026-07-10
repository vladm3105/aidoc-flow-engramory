"""Adapter factory — wires the active adapter set from ENGRAMORY_PROFILE.

Only the ports with a consumer have adapters so far; the AdapterSet grows a
field per port as dev adapters land (cache, events, llm, ...). gcp/azure sets
arrive with BRD-02 (cloud migration) and raise until then.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from engramory.adapters.dev.vector_pg import PgVectorAdapter
from engramory.ports import VectorPort

PROFILES = ("dev", "gcp", "azure")


@dataclass(frozen=True, slots=True)
class AdapterSet:
    """The active per-environment implementations of the ports."""

    profile: str
    vector: VectorPort


def dev_adapters(dsn: str) -> AdapterSet:
    return AdapterSet(profile="dev", vector=PgVectorAdapter(dsn))


def adapters_for_profile(profile: str | None = None, *, dsn: str) -> AdapterSet:
    """Select the adapter set; profile defaults to $ENGRAMORY_PROFILE, then dev."""
    resolved = profile or os.environ.get("ENGRAMORY_PROFILE", "dev")
    if resolved == "dev":
        return dev_adapters(dsn)
    if resolved in PROFILES:
        raise NotImplementedError(f"adapter set '{resolved}' arrives with BRD-02")
    raise ValueError(f"unknown ENGRAMORY_PROFILE: {resolved!r} (expected one of {PROFILES})")
