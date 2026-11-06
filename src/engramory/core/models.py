"""Typed data contracts for the canonical store (SPEC-02).

Stdlib-only frozen dataclasses mirroring db/migrations/0001..0003: the schema's
CHECK domains and NOT NULL rules are enforced here at construction time so a
contract violation fails before it reaches Postgres. ValidationError is the
single failure mode (SPEC-02 data-model invariants; TDD-02.04.a100).

Notes for consumers:
- Instances are NOT hashable (they carry Mapping fields) — key on ``id`` or
  ``Episode.idempotency_key()``, never on the instance itself.
- ``id`` / ``valid_from`` are None until the row is persisted (DB generates
  them); hydrated rows carry both.
- Mapping fields are defensively copied into read-only proxies at
  construction, so post-construction mutation of the caller's dict cannot
  revoke a validated invariant.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import Any

MEMORY_TYPES = frozenset({"semantic", "episodic", "procedural"})
MEMORY_STATUSES = frozenset({"active", "advisory", "quarantined", "superseded"})
SOURCE_TRUST_LEVELS = frozenset({"human", "tool", "agent"})
SCOPES = frozenset({"agent", "project", "domain", "space"})  # ladder per ADR-07
# The schema deliberately has no CHECK on scope (extended additively, see 0002);
# the model enforces the current ADR-07 ladder — extend this set with the schema.


class ValidationError(ValueError):
    """A model field violates a SPEC-02 invariant or a schema CHECK domain."""


def content_hash(content_raw: str) -> str:
    """Episode idempotency key — matches migration 0003's md5 backfill expression."""
    return hashlib.md5(content_raw.encode("utf-8"), usedforsecurity=False).hexdigest()


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(message)


def _frozen_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType(dict(value))


@dataclass(frozen=True, slots=True)
class Episode:
    """Raw experience (L1 source material) — episodes table.

    project_id is nullable by design (agent-scoped episodes may have no
    project); uq_episodes_idempotency coalesces NULL to ''. The SPEC-02
    ``required: true`` marking was stale and is corrected alongside this model.
    """

    agent_id: str
    content_raw: str
    project_id: str | None = None
    domain_id: str | None = None
    tenant_id: str = "default"
    kind: str = "note"
    metadata: Mapping[str, Any] = field(default_factory=dict)
    id: str | None = None  # None until persisted
    # Always derived — init=False means dataclasses.replace() re-derives it,
    # so a stale hash can never corrupt uq_episodes_idempotency.
    content_hash: str = field(init=False, default="")

    def __post_init__(self) -> None:
        _require(bool(self.agent_id), "Episode.agent_id is required")
        _require(bool(self.content_raw), "Episode.content_raw is required")
        _require(bool(self.tenant_id), "Episode.tenant_id is required (tenant wall, ADR-07)")
        _require(bool(self.kind), "Episode.kind is required (decision|error|outcome|note|...)")
        object.__setattr__(self, "metadata", _frozen_mapping(self.metadata))
        object.__setattr__(self, "content_hash", content_hash(self.content_raw))

    def idempotency_key(self) -> tuple[str, str, str, str]:
        """Uniqueness scope of uq_episodes_idempotency (migration 0003):
        (tenant_id, agent_id, coalesce(project_id, ''), content_hash)."""
        return (self.tenant_id, self.agent_id, self.project_id or "", self.content_hash)


@dataclass(frozen=True, slots=True)
class Memory:
    """Distilled long-term memory (L2) — memories table."""

    content_raw: str
    summary: str
    type: str  # semantic | episodic | procedural
    embedding_model: str
    provenance: Mapping[str, Any]
    agent_id: str | None = None  # nullable in schema; required for agent scope
    project_id: str | None = None
    domain_id: str | None = None
    tenant_id: str = "default"
    scope: str = "agent"
    confidence: float = 0.5
    status: str = "active"
    source_trust: str = "agent"
    embedding_dims: int | None = None
    valid_from: datetime | None = None  # None until persisted (DB default now())
    valid_to: datetime | None = None  # None = currently valid
    supersedes: str | None = None  # predecessor memory id
    id: str | None = None  # None until persisted

    def __post_init__(self) -> None:
        _require(bool(self.content_raw), "Memory.content_raw is required (re-embedding source)")
        _require(bool(self.summary), "Memory.summary is required (prompt-injected form)")
        _require(bool(self.embedding_model), "Memory.embedding_model is required (re-embedding)")
        _require(bool(self.provenance), "Memory.provenance is required (EARS.01.03.f030)")
        _require(bool(self.tenant_id), "Memory.tenant_id is required (tenant wall, ADR-07)")
        _require(self.type in MEMORY_TYPES, f"Memory.type must be one of {sorted(MEMORY_TYPES)}")
        _require(
            self.status in MEMORY_STATUSES,
            f"Memory.status must be one of {sorted(MEMORY_STATUSES)}",
        )
        _require(
            self.source_trust in SOURCE_TRUST_LEVELS,
            f"Memory.source_trust must be one of {sorted(SOURCE_TRUST_LEVELS)}",
        )
        _require(self.scope in SCOPES, f"Memory.scope must be one of {sorted(SCOPES)}")
        _require(
            self.scope != "agent" or bool(self.agent_id),
            "Memory.agent_id is required for agent-scoped memories",
        )
        _require(0.0 <= self.confidence <= 1.0, "Memory.confidence must be in [0, 1]")
        _require(
            self.embedding_dims is None or self.embedding_dims > 0,
            "Memory.embedding_dims must be positive when set",
        )
        object.__setattr__(self, "provenance", _frozen_mapping(self.provenance))


@dataclass(frozen=True, slots=True)
class KBSection:
    """Knowledge-core section (L0) — kb_sections table (migration 0003)."""

    doc_id: str
    citation: str
    text: str
    project_id: str | None = None
    domain_id: str | None = None
    tenant_id: str = "default"
    scope: str = "project"
    version: int = 1
    id: str | None = None  # None until persisted

    def __post_init__(self) -> None:
        _require(bool(self.doc_id), "KBSection.doc_id is required")
        _require(bool(self.citation), "KBSection.citation is required (citation anchor)")
        _require(bool(self.text), "KBSection.text is required")
        _require(bool(self.tenant_id), "KBSection.tenant_id is required")
        _require(self.scope in SCOPES, f"KBSection.scope must be one of {sorted(SCOPES)}")
        _require(self.version >= 1, "KBSection.version starts at 1")


@dataclass(frozen=True, slots=True)
class AgentProfile:
    """Per-agent identity (L3) — agent_profiles table."""

    agent_id: str
    display_name: str | None = None
    standing_preferences: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require(bool(self.agent_id), "AgentProfile.agent_id is required")
        object.__setattr__(self, "standing_preferences", _frozen_mapping(self.standing_preferences))


@dataclass(frozen=True, slots=True)
class GraphEntity:
    """Canonical graph entity (ADR-01 / ADR-03 / ADR-05) — graph_entities table."""

    id: str
    entity_type: str
    name: str
    tenant_id: str = "default"
    domain_id: str = "default"
    properties: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require(bool(self.id), "GraphEntity.id is required")
        _require(bool(self.entity_type), "GraphEntity.entity_type is required")
        _require(bool(self.name), "GraphEntity.name is required")
        _require(bool(self.tenant_id), "GraphEntity.tenant_id is required")
        object.__setattr__(self, "properties", _frozen_mapping(self.properties))


@dataclass(frozen=True, slots=True)
class GraphRelation:
    """Canonical graph relation (ADR-01 / ADR-03 / ADR-05) — graph_relations table."""

    src_id: str
    dst_id: str
    rel_type: str
    tenant_id: str = "default"
    properties: Mapping[str, Any] = field(default_factory=dict)
    id: str | None = None

    def __post_init__(self) -> None:
        _require(bool(self.src_id), "GraphRelation.src_id is required")
        _require(bool(self.dst_id), "GraphRelation.dst_id is required")
        _require(bool(self.rel_type), "GraphRelation.rel_type is required")
        _require(bool(self.tenant_id), "GraphRelation.tenant_id is required")
        object.__setattr__(self, "properties", _frozen_mapping(self.properties))


AUTONOMY_TIERS = frozenset({"green", "yellow", "red"})
RUNBOOK_MODES = frozenset({"read-only", "mutating"})
DOC_STATUSES = frozenset({"active", "draft", "deprecated", "archived"})


@dataclass(frozen=True, slots=True)
class KBDocument:
    """Canonical L0 documentation & runbook metadata (migration 0008)."""

    doc_id: str
    repo_path: str
    title: str
    kind: str
    namespace: str = "ns:platform"
    status: str = "active"
    autonomy_tier: str = "green"
    frontmatter: Mapping[str, Any] = field(default_factory=dict)
    watched_paths: tuple[str, ...] = ()
    trigger_alerts: tuple[str, ...] = ()
    last_verified: str = "2026-11-06"
    review_ttl_days: int = 90
    git_commit_sha: str = "local-head"
    project_id: str | None = None
    domain_id: str | None = None
    tenant_id: str = "default"
    scope: str = "project"

    def __post_init__(self) -> None:
        _require(bool(self.doc_id), "KBDocument.doc_id is required")
        _require(bool(self.repo_path), "KBDocument.repo_path is required")
        _require(bool(self.title), "KBDocument.title is required")
        _require(bool(self.kind), "KBDocument.kind is required")
        _require(bool(self.tenant_id), "KBDocument.tenant_id is required")
        _require(self.scope in SCOPES, f"KBDocument.scope must be one of {sorted(SCOPES)}")
        _require(
            self.status in DOC_STATUSES,
            f"KBDocument.status must be one of {sorted(DOC_STATUSES)}",
        )
        _require(
            self.autonomy_tier in AUTONOMY_TIERS,
            f"KBDocument.autonomy_tier must be one of {sorted(AUTONOMY_TIERS)}",
        )
        _require(self.review_ttl_days >= 1, "KBDocument.review_ttl_days must be >= 1")
        object.__setattr__(self, "frontmatter", _frozen_mapping(self.frontmatter))
        object.__setattr__(self, "watched_paths", tuple(self.watched_paths))
        object.__setattr__(self, "trigger_alerts", tuple(self.trigger_alerts))


@dataclass(frozen=True, slots=True)
class KBRunbookStep:
    """Annotated executable code block inside a runbook (migration 0008)."""

    step_uid: str
    doc_id: str
    step_order: int
    step_id: str
    heading_path: str
    tier: str
    mode: str
    command_body: str
    prose_context: str = ""
    expect_exit: int = 0
    timeout_sec: int = 30
    rollback_step: str | None = None
    tenant_id: str = "default"

    def __post_init__(self) -> None:
        _require(bool(self.step_uid), "KBRunbookStep.step_uid is required")
        _require(bool(self.doc_id), "KBRunbookStep.doc_id is required")
        _require(bool(self.step_id), "KBRunbookStep.step_id is required")
        _require(bool(self.heading_path), "KBRunbookStep.heading_path is required")
        _require(bool(self.command_body.strip()), "KBRunbookStep.command_body is required")
        _require(bool(self.tenant_id), "KBRunbookStep.tenant_id is required")
        _require(
            self.tier in AUTONOMY_TIERS,
            f"KBRunbookStep.tier must be one of {sorted(AUTONOMY_TIERS)}",
        )
        _require(
            self.mode in RUNBOOK_MODES,
            f"KBRunbookStep.mode must be one of {sorted(RUNBOOK_MODES)}",
        )
        _require(
            self.tier != "green" or self.mode == "read-only",
            "INV-STEP-01: KBRunbookStep with tier='green' must have mode='read-only'",
        )
        _require(self.timeout_sec >= 1, "KBRunbookStep.timeout_sec must be >= 1")
