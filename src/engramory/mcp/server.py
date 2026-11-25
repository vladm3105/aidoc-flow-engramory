"""MCP gateway — the single tool surface Engramory exposes to agents (SPEC-01 / ADR-10).

Exposes memory, knowledge, and SDD graph context slicing tools over standard MCP JSON-RPC.

Tools:
    memory_search     — scoped retrieval of knowledge + distilled memory (token-budgeted)
    memory_add        — append an episode in the caller's scope (idempotent by content hash)
    memory_feedback   — record a retrieval outcome (useful | not_useful | harmful)
    memory_forget     — retire a memory (end-date + supersede; soft, audited)
    agent_profile_get — load the caller's L3 identity profile
    knowledge_ingest  — create/update an SDD-artifact KB entry (governed write; needs evidence)
    graph_neighbors   — retrieve connected graph neighbors for an entity
    sdd_context_slice — compute deterministic upstream ancestor context slice for an IPLAN task
    sdd_blast_radius  — compute downstream blast radius impact of an ADR/SPEC change
    sdd_ingest        — ingest SDD documentation corpus into PostgreSQL and Neo4j
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import math
import socket
import sys
import threading
import uuid
from collections.abc import AsyncIterator, Callable, Coroutine, Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import Any

import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from engramory.access.authz import ActorContext
from engramory.access.jwt_auth import (
    AuthenticationError,
    JWKSValidator,
    RevocationCache,
    check_token_revocation_async,
    claims_to_actor_context,
)
from engramory.access.surface import AccessSurface, AuthzError, GovernedWriteRejected
from engramory.core.models import ValidationError, thaw_mapping
from engramory.core.repository import Repository, StoreUnavailable

logger = logging.getLogger(__name__)

MCP_PROTOCOL_VERSION = "2024-11-05"
MAX_MCP_INLINE_BLOB_BYTES = 4 * 1024 * 1024


def _coerce_int(value: Any, default: int, *, name: str) -> int:
    """Strictly coerce MCP integer arguments, rejecting booleans and non-integral values."""
    if value is None:
        return default
    if isinstance(value, bool):
        raise ValidationError(f"Invalid integer for '{name}': boolean not allowed")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer():
            raise ValidationError(f"Invalid integer for '{name}': {value!r}")
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError as exc:
            raise ValidationError(f"Invalid integer for '{name}': {value!r}") from exc
    raise ValidationError(f"Invalid integer for '{name}': expected integer")


def _format_context_slice_digest(
    task_id: str, ancestors: Sequence[Mapping[str, Any]], max_tokens: int
) -> str:
    """Render bounded compact digest text from AccessSurface context_slice ancestors."""
    header = f"=== AIDOC SLICED CONTEXT: TASK [{task_id}] ==="
    footer = f"<!-- Compaction: budget {max_tokens} -->\n=== END OF DIGEST ==="
    body = format_context_slice_markdown(task_id, ancestors)
    full = f"{header}\n\n{body}\n\n{footer}"
    max_chars = max(64, int(max(1, max_tokens) * 3.8))
    if len(full) <= max_chars:
        return full
    overhead = len(header) + len(footer) + 6
    if overhead >= max_chars:
        return f"{header}\n{footer}"[:max_chars]
    keep = max(16, max_chars - overhead - 20)
    return f"{header}\n\n{body[:keep]}\n...[truncated]\n\n{footer}"


TOOLS_SCHEMA: list[dict[str, Any]] = [
    {
        "name": "memory_search",
        "description": "Scoped retrieval of knowledge and distilled memory (token-budgeted).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query text"},
                "k": {"type": "integer", "description": "Maximum number of hits", "default": 8},
                "scope": {
                    "type": "string",
                    "description": "Visibility scope (agent, project, domain, space)",
                    "default": "agent",
                },
                "domain_id": {
                    "type": "string",
                    "description": "Optional department/domain focus filter",
                },
                "brain_variant": {
                    "type": "string",
                    "description": "Optional multi-model brain variant filter",
                },
                "specialization": {
                    "type": "string",
                    "description": "Optional specialist tag filter (e.g. stripe, oidc)",
                },
                "token_budget": {
                    "type": "integer",
                    "description": "Maximum tokens returned",
                },
                "include_knowledge": {
                    "type": "boolean",
                    "description": (
                        "Include matching L0 extracted knowledge sections alongside L2 memories"
                    ),
                    "default": False,
                },
                "expand_graph": {
                    "type": "boolean",
                    "description": "Attach connected GraphRAG neighborhood context",
                    "default": False,
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "memory_add",
        "description": "Append an episode in the caller's scope (idempotent by content hash).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "Raw episode content"},
                "kind": {
                    "type": "string",
                    "description": "Episode kind (note, prompt, observation)",
                    "default": "note",
                },
                "scope": {
                    "type": "string",
                    "description": "Target memory scope (agent, project, domain, space)",
                    "default": "agent",
                },
                "domain_id": {
                    "type": "string",
                    "description": "Optional department/domain focus identifier",
                },
                "specializations": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional specialist tags for this episode",
                },
                "brain_variant": {
                    "type": "string",
                    "description": "Optional brain variant tag",
                },
            },
            "required": ["content"],
        },
    },
    {
        "name": "memory_promote",
        "description": (
            "Governed promotion of a validated L2 memory up the ADR-07 scope ladder "
            "(agent -> project -> domain -> space)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "memory_id": {"type": "string", "description": "Source memory ID"},
                "target_scope": {
                    "type": "string",
                    "enum": ["project", "domain", "space"],
                    "description": "Target shared scope level",
                },
                "evidence_ref": {
                    "type": "string",
                    "description": "Approved governance evidence reference (ADR-06)",
                },
                "target_domain_id": {
                    "type": "string",
                    "description": "Target department/domain ID (for domain scope)",
                },
                "target_project_id": {
                    "type": "string",
                    "description": "Target project ID (for project scope)",
                },
                "specializations": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional additional specialist tags",
                },
            },
            "required": ["memory_id", "target_scope", "evidence_ref"],
        },
    },
    {
        "name": "memory_feedback",
        "description": "Record a retrieval outcome (useful, not_useful, harmful).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "retrieval_id": {"type": "string", "description": "Retrieval record ID"},
                "outcome": {
                    "type": "string",
                    "enum": ["useful", "not_useful", "harmful"],
                    "description": "Feedback outcome",
                },
            },
            "required": ["retrieval_id", "outcome"],
        },
    },
    {
        "name": "memory_forget",
        "description": "Retire a memory (soft end-date + supersede; audited with reason).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "memory_id": {"type": "string", "description": "Target memory ID"},
                "reason": {"type": "string", "description": "Audit rationale for retirement"},
            },
            "required": ["memory_id", "reason"],
        },
    },
    {
        "name": "agent_profile_get",
        "description": "Load the caller's L3 identity profile and standing preferences.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "agent_profile_upsert",
        "description": (
            "Governed L3 Virtual Employee profile upsert storing agent_type, role, "
            "department_focus (domain_id), specializations, and brain_config."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "display_name": {"type": "string"},
                "agent_type": {
                    "type": "string",
                    "description": "Agent archetype (e.g. agent-support, agent-pm, agent-dev)",
                },
                "role": {"type": "string", "description": "Functional role"},
                "domain_id": {"type": "string", "description": "Department focus"},
                "specializations": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Specialist tags (e.g. ['stripe', 'chargebacks'])",
                },
                "brain_config": {
                    "type": "object",
                    "description": (
                        "Multi-model brain config (active_brain_variant, distiller_model, "
                        "extraction_model, embedding_model, rag_params)"
                    ),
                },
                "standing_preferences": {"type": "object"},
            },
        },
    },
    {
        "name": "agent_roster_list",
        "description": (
            "List and rank Virtual Employee instances in the caller's tenant "
            "filtered by domain_id, agent_type, role, or specialization."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "domain_id": {"type": "string"},
                "agent_type": {"type": "string"},
                "role": {"type": "string"},
                "specialization": {"type": "string"},
                "include_stats": {"type": "boolean", "default": True},
            },
        },
    },
    {
        "name": "agent_brain_export",
        "description": (
            "Export a sanitized portable Virtual Employee Brain Passport "
            "(L3 profile + L2 skills with PII/secrets/tenant names scrubbed)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "domain_id": {
                    "type": "string",
                    "description": "Optional department/domain filter (e.g. billing)",
                },
                "brain_variant": {
                    "type": "string",
                    "description": "Optional brain variant filter",
                },
                "specialization": {
                    "type": "string",
                    "description": "Optional specialization tag filter",
                },
                "min_confidence": {
                    "type": "number",
                    "description": "Minimum skill confidence threshold (0.0..1.0)",
                    "default": 0.5,
                },
                "redact_terms": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Additional tenant/customer terms to scrub",
                },
            },
        },
    },
    {
        "name": "agent_brain_import",
        "description": (
            "Governed import of a sanitized Virtual Employee Brain Passport "
            "into the caller's tenant and agent space."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "passport": {
                    "type": "object",
                    "description": "Portable brain passport JSON object",
                },
                "evidence_ref": {
                    "type": "string",
                    "description": "Approved governance evidence reference (ADR-06)",
                },
                "target_domain_id": {
                    "type": "string",
                    "description": "Optional target department/domain focus override",
                },
                "brain_variant": {
                    "type": "string",
                    "description": "Optional target brain variant override",
                },
                "redact_terms": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Additional terms to scrub on import",
                },
            },
            "required": ["passport", "evidence_ref"],
        },
    },
    {
        "name": "agent_brain_compare",
        "description": (
            "Compare two multi-model brain variants (variant_a vs. variant_b) "
            "across skill stats, retrieval feedback useful_rate, and live query hits."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "variant_a": {"type": "string"},
                "variant_b": {"type": "string"},
                "query": {"type": "string"},
                "k": {"type": "integer", "default": 5},
                "scope": {"type": "string", "default": "agent"},
                "domain_id": {"type": "string"},
                "specialization": {"type": "string"},
            },
            "required": ["variant_a", "variant_b"],
        },
    },
    {
        "name": "agent_space_offboard",
        "description": (
            "Governed transactional offboarding of a Virtual Employee's private "
            "scope='agent' space on the caller's tenant."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "evidence_ref": {
                    "type": "string",
                    "description": "Approved governance evidence reference (ADR-06)",
                },
                "reason": {
                    "type": "string",
                    "default": "cross_tenant_reassignment",
                },
                "purge_episodes": {"type": "boolean", "default": True},
                "retire_memories": {"type": "boolean", "default": True},
            },
            "required": ["evidence_ref"],
        },
    },
    {
        "name": "knowledge_ingest",
        "description": "Governed write of an SDD-artifact knowledge section (requires evidence).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "doc_id": {"type": "string", "description": "Document ID (e.g. SPEC-01)"},
                "citation": {"type": "string", "description": "Section or paragraph citation"},
                "text": {"type": "string", "description": "Canonical section text"},
                "evidence_ref": {
                    "type": "string",
                    "description": "Approved evidence / review reference",
                },
                "scope": {"type": "string", "default": "project"},
                "version": {"type": "integer", "default": 1},
            },
            "required": ["doc_id", "citation", "text", "evidence_ref"],
        },
    },
    {
        "name": "knowledge_search",
        "description": "Scoped hybrid search over L0 knowledge base sections.",
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query text"},
                "project_id": {
                    "type": "string",
                    "description": "Project override (default: caller scope)",
                },
                "domain_id": {
                    "type": "string",
                    "description": "Optional domain filter",
                },
                "scope": {
                    "type": "string",
                    "description": "Visibility scope (project, domain, space)",
                    "default": "project",
                },
                "modality": {
                    "type": "string",
                    "description": (
                        "Optional modality filter "
                        "(text, markdown, pdf, document, image, audio, video)"
                    ),
                    "enum": ["text", "markdown", "pdf", "document", "image", "audio", "video"],
                },
                "expand_graph": {
                    "type": "boolean",
                    "description": "Expand connected GraphRAG entities via Neo4j / PostgreSQL CTE",
                    "default": False,
                },
                "graph_depth": {
                    "type": "integer",
                    "description": "GraphRAG neighborhood hop depth (1..6)",
                    "default": 1,
                },
                "k": {
                    "type": "integer",
                    "description": "Maximum number of sections returned",
                    "default": 5,
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "knowledge_ingest_media",
        "description": (
            "Governed multimodal ingestion (image, audio, 20 GB+ video, PDF) "
            "into CAS + kb_sections + graph."
        ),
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Workspace-confined path to media file",
                },
                "evidence_ref": {
                    "type": "string",
                    "description": "Approved evidence reference (ADR-06)",
                },
                "scope": {
                    "type": "string",
                    "description": "Visibility scope (project, domain, space)",
                    "default": "project",
                },
                "embed": {
                    "type": "boolean",
                    "description": "Compute vector embeddings for derived sections",
                    "default": True,
                },
                "project_graph": {
                    "type": "boolean",
                    "description": "Project DOCUMENT, SECTION, and MEDIA_BLOB graph nodes",
                    "default": True,
                },
            },
            "required": ["file_path", "evidence_ref"],
        },
    },
    {
        "name": "blob_head",
        "description": "Inspect PostgreSQL CAS blob manifest metadata and chunk status by SHA-256.",
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "sha256": {
                    "type": "string",
                    "description": "64-character hex SHA-256 content address",
                },
                "scope": {
                    "type": "string",
                    "description": "Visibility scope (project, domain, space)",
                    "default": "project",
                },
            },
            "required": ["sha256"],
        },
    },
    {
        "name": "blob_put",
        "description": (
            "Governed write of a raw binary blob (<= 4 MiB base64 or workspace-confined "
            "file_path) into PostgreSQL CAS storage."
        ),
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Workspace-confined local file path to store",
                },
                "data_base64": {
                    "type": "string",
                    "description": "Base64-encoded payload (<= 4 MiB decoded)",
                },
                "content_type": {
                    "type": "string",
                    "default": "application/octet-stream",
                },
                "scope": {
                    "type": "string",
                    "description": "Visibility scope (project, domain, space)",
                    "default": "project",
                },
                "metadata": {
                    "type": "object",
                    "description": "Optional governed metadata JSON object",
                },
            },
        },
    },
    {
        "name": "blob_get",
        "description": "Governed bounded read of a CAS blob by SHA-256 returning base64 bytes.",
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "sha256": {
                    "type": "string",
                    "description": "64-character hex SHA-256 content address",
                },
                "scope": {
                    "type": "string",
                    "description": "Visibility scope (project, domain, space)",
                    "default": "project",
                },
                "max_bytes": {
                    "type": "integer",
                    "description": "Maximum bytes to read (capped by surface limit)",
                    "default": MAX_MCP_INLINE_BLOB_BYTES,
                },
            },
            "required": ["sha256"],
        },
    },
    {
        "name": "graph_neighbors",
        "description": "Retrieve connected graph neighbors for an entity within depth hops.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Entity ID (e.g. ADR-01)"},
                "depth": {"type": "integer", "description": "Traversal depth", "default": 1},
                "rel": {"type": "string", "description": "Optional relationship type filter"},
            },
            "required": ["id"],
        },
    },
    {
        "name": "sdd_context_slice",
        "description": (
            "Retrieve deterministic upstream ancestor contract closure for an SDD task node "
            "(Layer 08 IPLAN) to avoid token context bloat and hallucination."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "Task node ID (e.g. IPLAN-03 or IPLAN-319)",
                },
                "depth": {
                    "type": "integer",
                    "description": "Max traversal depth (default 6)",
                    "default": 6,
                },
                "scope": {"type": "string", "default": "project"},
                "layers": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Filter to specific layers (e.g. ['SPEC', 'ADR', 'BRD'])",
                },
                "max_tokens": {
                    "type": "integer",
                    "description": "Maximum token budget for returned slice",
                    "default": 2000,
                },
                "format": {
                    "type": "string",
                    "enum": ["markdown", "json", "digest"],
                    "default": "json",
                    "description": (
                        "Output format ('markdown' with file links, 'digest' compacted, or 'json')"
                    ),
                },
                "offline": {
                    "type": "boolean",
                    "default": False,
                    "description": "Force local workspace DAG traversal without querying DB/Neo4j",
                },
                "workspace_dir": {
                    "type": "string",
                    "description": "Optional confined workspace root for local/offline DAG slicing",
                },
                "tenant_id": {
                    "type": "string",
                    "description": "Tenant override (default: caller scope)",
                },
                "project_id": {
                    "type": "string",
                    "description": "Project override (default: caller scope)",
                },
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "sdd_blast_radius",
        "description": (
            "Calculate downstream descendant specification and test impacts affected by an "
            "architectural or specification change (anti-contract erosion gating)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "doc_id": {
                    "type": "string",
                    "description": "Changed document ID (e.g. ADR-03 or SPEC-03)",
                },
                "depth": {
                    "type": "integer",
                    "description": "Max traversal depth (default 6)",
                    "default": 6,
                },
                "scope": {"type": "string", "default": "project"},
                "offline": {
                    "type": "boolean",
                    "default": False,
                    "description": "Force local workspace DAG traversal without querying DB/Neo4j",
                },
                "workspace_dir": {
                    "type": "string",
                    "description": (
                        "Optional confined workspace root for local/offline blast radius"
                    ),
                },
            },
            "required": ["doc_id"],
        },
    },
    {
        "name": "sdd_ingest",
        "description": (
            "Ingest complete multi-layer SDD documentation corpus into PostgreSQL and "
            "project to Neo4j."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "sdd_root": {
                    "type": "string",
                    "description": "Root path to SDD layers",
                    "default": "sdd",
                },
                "project_id": {
                    "type": "string",
                    "description": "Project identifier",
                    "default": "core",
                },
                "evidence_ref": {
                    "type": "string",
                    "description": "Approved change/evidence reference (ADR-06 required)",
                },
                "reconcile": {
                    "type": "boolean",
                    "description": "Purge stale nodes/edges not in corpus (default: true)",
                    "default": True,
                },
            },
            "required": ["evidence_ref"],
        },
    },
    {
        "name": "memory_ingest_event",
        "description": (
            "Validate, screen, and persist a structured ExperienceEvent into L1 episodes."
        ),
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "event": {
                    "type": "object",
                    "description": "ExperienceEvent payload dictionary",
                },
                "scope": {
                    "type": "string",
                    "enum": ["agent", "project", "domain", "space"],
                    "default": "project",
                },
            },
            "required": ["event"],
        },
    },
    {
        "name": "memory_learning_health",
        "description": (
            "Compute closed-loop learning effectiveness metrics over recent tenant episodes."
        ),
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {
                    "type": "string",
                    "description": "Optional project filter",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max recent episodes to inspect (default: 500)",
                    "default": 500,
                },
                "scope": {
                    "type": "string",
                    "enum": ["agent", "project", "domain", "space"],
                    "default": "project",
                },
            },
        },
    },
    {
        "name": "docs_ingest_runbook",
        "description": (
            "Governed ingestion of a Markdown runbook and executable steps into PostgreSQL."
        ),
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the governed Markdown runbook file",
                },
                "evidence_ref": {
                    "type": "string",
                    "description": "Approved evidence reference (e.g. git commit or CHG ID)",
                },
                "scope": {
                    "type": "string",
                    "enum": ["agent", "project", "domain", "space"],
                    "default": "project",
                },
            },
            "required": ["path", "evidence_ref"],
        },
    },
]


def format_context_slice_markdown(task_id: str, ancestors: Sequence[Mapping[str, Any]]) -> str:
    """Format an ancestor context slice into clean Markdown with clickable file links."""
    if not ancestors:
        return f"## Context Slice for {task_id}\n\nNo upstream specification ancestors found."
    lines = [
        f"## Context Slice for {task_id} ({len(ancestors)} upstream specification ancestors)\n"
    ]
    for a in ancestors:
        nid = str(a.get("id", "UNKNOWN"))
        props = a.get("properties", {})
        title = props.get("name") or props.get("title") or nid
        path = str(props.get("path", "") or "")
        file_link = f"file://{path}" if path.startswith("/") else path
        header = f"### [{nid}: {title}]({file_link})" if file_link else f"### {nid}: {title}"
        lines.append(header)
        domain = props.get("domain_id")
        if domain:
            lines.append(f"- **Domain**: {domain}")
        status = props.get("status")
        if status:
            lines.append(f"- **Status**: {status}")
        summary = props.get("summary")
        if summary:
            lines.append(f"- **Summary**: {summary}")
        lines.append("")
    return "\n".join(lines).strip()


def is_loopback_host(host: str) -> bool:
    """Return True iff `host` binds only to a loopback address."""
    cleaned = host.strip().strip("[]").lower()
    return cleaned in {"127.0.0.1", "localhost", "::1"}


class _ThreadUvicornServer(uvicorn.Server):
    """Uvicorn Server subclass that disables OS signal registration in worker threads."""

    @contextmanager
    def capture_signals(self) -> Iterator[None]:
        yield


class _UvicornMCPServer:
    """Thread-safe lifecycle wrapper around pre-bound TCP socket + uvicorn.Server."""

    def __init__(
        self,
        app: FastAPI,
        host: str,
        port: int,
    ) -> None:
        family = socket.AF_INET6 if ":" in host else socket.AF_INET
        sock = socket.socket(family, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
            sock.listen(128)
        except OSError:
            sock.close()
            raise
        self._sock = sock
        self.app = app
        self.server_address = sock.getsockname()
        config = uvicorn.Config(
            app,
            host=self.server_address[0],
            port=self.server_address[1],
            log_level="error",
            access_log=False,
            lifespan="off",
            timeout_graceful_shutdown=1,
        )
        self._server = _ThreadUvicornServer(config)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stopped = threading.Event()

    async def _serve_async(self) -> None:
        self._loop = asyncio.get_running_loop()
        self.app.state.shutdown_event = asyncio.Event()
        try:
            await self._server.serve(sockets=[self._sock])
        finally:
            self._stopped.set()

    def serve_forever(self) -> None:
        asyncio.run(self._serve_async())

    def shutdown(self) -> None:
        self._server.should_exit = True
        if self._loop is not None and not self._loop.is_closed():
            shutdown_ev = getattr(self.app.state, "shutdown_event", None)
            if isinstance(shutdown_ev, asyncio.Event):
                try:
                    self._loop.call_soon_threadsafe(shutdown_ev.set)
                except RuntimeError:
                    pass
        self._stopped.wait(timeout=3.0)

    def server_close(self) -> None:
        self._server.should_exit = True
        try:
            self._sock.close()
        except OSError:
            pass


class McpServer:
    """Async JSON-RPC stdio MCP Gateway over AccessSurface."""

    def __init__(
        self,
        surface: AccessSurface,
        ctx: ActorContext,
        *,
        ingest_fn: Callable[..., Coroutine[Any, Any, tuple[int, int]]] | None = None,
        token_validator: JWKSValidator | None = None,
        auth_enabled: bool = False,
        security_gate_url: str | None = None,
        revoked_identifiers: set[str] | None = None,
    ) -> None:
        self._surface = surface
        self._ctx = ctx
        self._ingest_fn = ingest_fn
        self._token_validator = token_validator
        self._auth_enabled = auth_enabled
        self._security_gate_url = security_gate_url
        self._revoked_identifiers = revoked_identifiers
        self._revocation_cache = RevocationCache()

    def _resolve_call_ctx(
        self, arguments: dict[str, Any], ctx: ActorContext | None = None
    ) -> ActorContext:
        base = ctx or self._ctx
        # In authenticated mode, tenant_id and project_id cannot be spoofed
        # via client arguments (ADR-10)
        if self._auth_enabled:
            tenant_id = base.tenant_id
            project_id = base.project_id
        else:
            tenant_id = arguments.get("tenant_id") or base.tenant_id
            project_id = arguments.get("project_id") or base.project_id
        if tenant_id != base.tenant_id or project_id != base.project_id:
            return ActorContext(
                agent_id=base.agent_id,
                tenant_id=tenant_id,
                project_id=project_id,
                scopes=base.scopes,
                domain_id=base.domain_id,
            )
        return base

    async def handle_call_tool(
        self, name: str, arguments: dict[str, Any], ctx: ActorContext | None = None
    ) -> dict[str, Any]:
        """Dispatch an MCP tool call to the AccessSurface or storage engine."""
        call_ctx = self._resolve_call_ctx(arguments, ctx)
        try:
            if name == "memory_search":
                raw_budget = arguments.get("token_budget", 2000)
                extra_mem_kwargs: dict[str, Any] = {}
                if arguments.get("expand_graph"):
                    extra_mem_kwargs["expand_graph"] = True
                if arguments.get("include_knowledge"):
                    extra_mem_kwargs["include_knowledge"] = True
                if arguments.get("domain_id"):
                    extra_mem_kwargs["domain_id"] = str(arguments["domain_id"])
                if arguments.get("brain_variant"):
                    extra_mem_kwargs["brain_variant"] = str(arguments["brain_variant"])
                if arguments.get("specialization"):
                    extra_mem_kwargs["specialization"] = str(arguments["specialization"])
                hits = await self._surface.memory_search(
                    call_ctx,
                    query=arguments["query"],
                    k=_coerce_int(arguments.get("k"), 8, name="k"),
                    scope=arguments.get("scope", "agent"),
                    token_budget=_coerce_int(raw_budget, 2000, name="token_budget"),
                    **extra_mem_kwargs,
                )
                formatted = [
                    {
                        "memory_id": h.memory_id,
                        "summary": h.summary,
                        "score": h.score,
                        "token_count": h.token_count,
                        "retrieval_id": h.retrieval_id,
                        **(
                            {"provenance": thaw_mapping(h.provenance)}
                            if (arguments.get("include_knowledge") or arguments.get("expand_graph"))
                            else {}
                        ),
                    }
                    for h in hits
                ]
                return {"content": [{"type": "text", "text": json.dumps(formatted, indent=2)}]}

            elif name == "memory_add":
                add_kwargs: dict[str, Any] = {}
                if arguments.get("scope"):
                    add_kwargs["scope"] = str(arguments["scope"])
                if arguments.get("domain_id"):
                    add_kwargs["domain_id"] = str(arguments["domain_id"])
                if isinstance(arguments.get("specializations"), list):
                    add_kwargs["specializations"] = [
                        str(s) for s in arguments["specializations"] if str(s)
                    ]
                if arguments.get("brain_variant"):
                    add_kwargs["brain_variant"] = str(arguments["brain_variant"])
                ep_id = await self._surface.memory_add(
                    call_ctx,
                    content=arguments["content"],
                    kind=arguments.get("kind", "note"),
                    **add_kwargs,
                )
                return {"content": [{"type": "text", "text": f"Added episode {ep_id}"}]}

            elif name == "memory_promote":
                raw_specs = arguments.get("specializations")
                specs_list = (
                    [str(s) for s in raw_specs if str(s)] if isinstance(raw_specs, list) else None
                )
                promoted = await self._surface.memory_promote(
                    call_ctx,
                    memory_id=str(arguments["memory_id"]),
                    target_scope=str(arguments["target_scope"]),
                    evidence_ref=arguments.get("evidence_ref"),
                    target_domain_id=arguments.get("target_domain_id"),
                    target_project_id=arguments.get("target_project_id"),
                    specializations=specs_list,
                )
                return {"content": [{"type": "text", "text": json.dumps(promoted, indent=2)}]}

            elif name == "memory_feedback":
                await self._surface.memory_feedback(
                    call_ctx,
                    retrieval_id=arguments["retrieval_id"],
                    outcome=arguments["outcome"],
                )
                return {"content": [{"type": "text", "text": "Feedback recorded"}]}

            elif name == "memory_forget":
                await self._surface.memory_forget(
                    call_ctx,
                    memory_id=arguments["memory_id"],
                    reason=arguments["reason"],
                )
                return {"content": [{"type": "text", "text": "Memory retired"}]}

            elif name == "memory_ingest_event":
                ev_payload = arguments.get("event")
                if not isinstance(ev_payload, Mapping):
                    ev_payload = {k: v for k, v in arguments.items() if k not in ("scope", "event")}
                ingested = await self._surface.memory_ingest_event(
                    call_ctx,
                    ev_payload,
                    scope=str(arguments.get("scope", "project")),
                )
                return {"content": [{"type": "text", "text": json.dumps(ingested, indent=2)}]}

            elif name == "memory_learning_health":
                req_proj = (
                    call_ctx.project_id
                    if (self._auth_enabled and "space" not in call_ctx.scopes)
                    else arguments.get("project_id")
                )
                health = await self._surface.memory_learning_health(
                    call_ctx,
                    project_id=req_proj,
                    limit=_coerce_int(arguments.get("limit"), 500, name="limit"),
                    scope=str(arguments.get("scope", "project")),
                )
                return {"content": [{"type": "text", "text": json.dumps(health, indent=2)}]}

            elif name == "agent_profile_get":
                profile = await self._surface.agent_profile_get(call_ctx)
                data = {
                    "agent_id": profile.agent_id,
                    "display_name": profile.display_name,
                    "standing_preferences": thaw_mapping(profile.standing_preferences),
                }
                return {"content": [{"type": "text", "text": json.dumps(data, indent=2)}]}

            elif name == "agent_profile_upsert":
                raw_specs = arguments.get("specializations")
                specs_list = (
                    [str(s) for s in raw_specs if str(s)] if isinstance(raw_specs, list) else None
                )
                raw_bcfg = arguments.get("brain_config")
                bcfg = dict(raw_bcfg) if isinstance(raw_bcfg, Mapping) else None
                raw_prefs = arguments.get("standing_preferences")
                prefs = dict(raw_prefs) if isinstance(raw_prefs, Mapping) else None
                upserted = await self._surface.agent_profile_upsert(
                    call_ctx,
                    display_name=arguments.get("display_name"),
                    agent_type=arguments.get("agent_type"),
                    role=arguments.get("role"),
                    domain_id=arguments.get("domain_id"),
                    specializations=specs_list,
                    brain_config=bcfg,
                    standing_preferences=prefs,
                )
                out_data = {
                    "agent_id": upserted.agent_id,
                    "display_name": upserted.display_name,
                    "standing_preferences": thaw_mapping(upserted.standing_preferences),
                }
                return {"content": [{"type": "text", "text": json.dumps(out_data, indent=2)}]}

            elif name == "agent_roster_list":
                roster = await self._surface.agent_roster_list(
                    call_ctx,
                    domain_id=arguments.get("domain_id"),
                    agent_type=arguments.get("agent_type"),
                    role=arguments.get("role"),
                    specialization=arguments.get("specialization"),
                    include_stats=bool(arguments.get("include_stats", True)),
                )
                return {"content": [{"type": "text", "text": json.dumps(roster, indent=2)}]}

            elif name == "agent_brain_export":
                raw_redact = arguments.get("redact_terms", [])
                redact_terms = (
                    [str(x) for x in raw_redact if str(x)] if isinstance(raw_redact, list) else []
                )
                exp_kwargs: dict[str, Any] = {
                    "domain_id": arguments.get("domain_id"),
                    "min_confidence": float(arguments.get("min_confidence", 0.5)),
                    "redact_terms": redact_terms,
                }
                if arguments.get("brain_variant"):
                    exp_kwargs["brain_variant"] = str(arguments["brain_variant"])
                if arguments.get("specialization"):
                    exp_kwargs["specialization"] = str(arguments["specialization"])
                bundle = await self._surface.agent_brain_export(
                    call_ctx,
                    **exp_kwargs,
                )
                return {"content": [{"type": "text", "text": json.dumps(bundle, indent=2)}]}

            elif name == "agent_brain_import":
                raw_imp_redact = arguments.get("redact_terms", [])
                imp_redact_terms = (
                    [str(x) for x in raw_imp_redact if str(x)]
                    if isinstance(raw_imp_redact, list)
                    else []
                )
                imp_kwargs: dict[str, Any] = {
                    "passport": dict(arguments["passport"]),
                    "evidence_ref": str(arguments["evidence_ref"]),
                    "target_domain_id": arguments.get("target_domain_id"),
                    "verify_integrity": True,
                    "allow_type_override": False,
                }
                if arguments.get("brain_variant"):
                    imp_kwargs["brain_variant"] = str(arguments["brain_variant"])
                if imp_redact_terms:
                    imp_kwargs["redact_terms"] = imp_redact_terms
                imported = await self._surface.agent_brain_import(
                    call_ctx,
                    **imp_kwargs,
                )
                return {"content": [{"type": "text", "text": json.dumps(imported, indent=2)}]}

            elif name == "agent_brain_compare":
                compared = await self._surface.agent_brain_compare(
                    call_ctx,
                    variant_a=str(arguments["variant_a"]),
                    variant_b=str(arguments["variant_b"]),
                    query=arguments.get("query"),
                    k=_coerce_int(arguments.get("k"), 5, name="k"),
                    scope=str(arguments.get("scope", "agent")),
                    domain_id=arguments.get("domain_id"),
                    specialization=arguments.get("specialization"),
                )
                return {"content": [{"type": "text", "text": json.dumps(compared, indent=2)}]}

            elif name == "agent_space_offboard":
                offboarded = await self._surface.agent_space_offboard(
                    call_ctx,
                    evidence_ref=arguments.get("evidence_ref"),
                    reason=str(arguments.get("reason", "cross_tenant_reassignment")),
                    purge_episodes=bool(arguments.get("purge_episodes", True)),
                    retire_memories=bool(arguments.get("retire_memories", True)),
                )
                return {"content": [{"type": "text", "text": json.dumps(offboarded, indent=2)}]}

            elif name == "knowledge_ingest":
                sec_id = await self._surface.knowledge_ingest(
                    call_ctx,
                    doc_id=arguments["doc_id"],
                    citation=arguments["citation"],
                    text=arguments["text"],
                    evidence_ref=arguments["evidence_ref"],
                    scope=arguments.get("scope", "project"),
                    version=_coerce_int(arguments.get("version"), 1, name="version"),
                )
                return {"content": [{"type": "text", "text": f"Ingested section {sec_id}"}]}

            elif name == "knowledge_search":
                raw_k = arguments["k"] if "k" in arguments else arguments.get("limit")
                search_kwargs: dict[str, Any] = {
                    "query": arguments["query"],
                    "domain_id": arguments.get("domain_id"),
                    "scope": arguments.get("scope", "project"),
                    "limit": _coerce_int(raw_k, 5, name="k"),
                }
                if "modality" in arguments and arguments["modality"] is not None:
                    search_kwargs["modality"] = str(arguments["modality"])
                if "expand_graph" in arguments:
                    search_kwargs["expand_graph"] = bool(arguments["expand_graph"])
                if "graph_depth" in arguments and arguments["graph_depth"] is not None:
                    search_kwargs["graph_depth"] = _coerce_int(
                        arguments["graph_depth"], 1, name="graph_depth"
                    )

                sections = await self._surface.knowledge_search(
                    call_ctx,
                    **search_kwargs,
                )
                formatted_secs = [
                    {
                        "id": s.id,
                        "doc_id": s.doc_id,
                        "citation": s.citation,
                        "text": s.text,
                        "project_id": s.project_id,
                        "domain_id": s.domain_id,
                        "scope": s.scope,
                        "version": s.version,
                        "modality": getattr(s, "modality", "text"),
                        "blob_sha256": getattr(s, "blob_sha256", None),
                        "blob_uri": getattr(s, "blob_uri", None),
                        "media_metadata": thaw_mapping(getattr(s, "media_metadata", None)),
                    }
                    for s in sections
                ]
                return {"content": [{"type": "text", "text": json.dumps(formatted_secs, indent=2)}]}

            elif name == "knowledge_ingest_media":
                enforce_proj = self._auth_enabled and ("space" not in call_ctx.scopes)
                media_res = await self._surface.knowledge_ingest_media(
                    call_ctx,
                    file_path=arguments["file_path"],
                    evidence_ref=arguments.get("evidence_ref"),
                    scope=arguments.get("scope", "project"),
                    domain_id=arguments.get("domain_id"),
                    embed=bool(arguments.get("embed", True)),
                    project_graph=bool(arguments.get("project_graph", True)),
                    enforce_project_boundary=enforce_proj,
                )
                return {"content": [{"type": "text", "text": json.dumps(media_res, indent=2)}]}

            elif name == "blob_head":
                head_res = await self._surface.blob_head(
                    call_ctx,
                    sha256=arguments["sha256"],
                    scope=arguments.get("scope", "project"),
                )
                return {"content": [{"type": "text", "text": json.dumps(head_res, indent=2)}]}

            elif name == "blob_put":
                enforce_proj = self._auth_enabled and ("space" not in call_ctx.scopes)
                raw_fp = arguments.get("file_path")
                raw_b64 = arguments.get("data_base64")
                if raw_fp is not None and raw_b64 is not None:
                    raise ValidationError(
                        "blob_put accepts either 'file_path' or 'data_base64', not both"
                    )
                if raw_fp is None and raw_b64 is None:
                    raise ValidationError("blob_put requires either 'file_path' or 'data_base64'")
                decoded_data: bytes | None = None
                if raw_b64 is not None:
                    if not isinstance(raw_b64, str):
                        raise ValidationError("blob_put 'data_base64' must be a base64 string")
                    stripped_b64 = raw_b64.strip()
                    if len(stripped_b64) > (MAX_MCP_INLINE_BLOB_BYTES * 4) // 3 + 16:
                        raise ValidationError("blob_put 'data_base64' exceeds maximum payload size")
                    try:
                        decoded_data = base64.b64decode(stripped_b64, validate=True)
                    except ValueError as exc:
                        raise ValidationError("Invalid base64 payload for blob_put") from exc
                    if len(decoded_data) > MAX_MCP_INLINE_BLOB_BYTES:
                        raise ValidationError("blob_put decoded payload exceeds 4 MiB limit")
                raw_meta = arguments.get("metadata")
                if raw_meta is not None and not isinstance(raw_meta, Mapping):
                    raise ValidationError("blob_put 'metadata' must be a JSON object")
                put_res = await self._surface.blob_put(
                    call_ctx,
                    file_path=str(raw_fp) if raw_fp is not None else None,
                    data=decoded_data,
                    content_type=str(arguments.get("content_type", "application/octet-stream")),
                    scope=str(arguments.get("scope", "project")),
                    metadata=raw_meta,
                    enforce_project_boundary=enforce_proj,
                )
                return {"content": [{"type": "text", "text": json.dumps(put_res, indent=2)}]}

            elif name == "blob_get":
                max_bytes = _coerce_int(
                    arguments.get("max_bytes"), MAX_MCP_INLINE_BLOB_BYTES, name="max_bytes"
                )
                if max_bytes <= 0:
                    raise ValidationError("blob_get 'max_bytes' must be positive")
                raw_bytes = await self._surface.blob_get(
                    call_ctx,
                    sha256=str(arguments["sha256"]),
                    scope=str(arguments.get("scope", "project")),
                    max_bytes=max_bytes,
                )
                payload = {
                    "sha256": str(arguments["sha256"]),
                    "byte_size": len(raw_bytes),
                    "data_base64": base64.b64encode(raw_bytes).decode("ascii"),
                }
                return {"content": [{"type": "text", "text": json.dumps(payload, indent=2)}]}

            elif name == "graph_neighbors":
                neighbors = await self._surface.graph_neighbors(
                    call_ctx,
                    id=arguments["id"],
                    rel=arguments.get("rel"),
                    depth=_coerce_int(arguments.get("depth"), 1, name="depth"),
                    scope=arguments.get("scope", "project"),
                )
                return {
                    "content": [{"type": "text", "text": json.dumps(list(neighbors), indent=2)}]
                }

            elif name == "sdd_context_slice":
                from engramory.access.authz import TargetScope
                from engramory.core.context_tools import slice_local_context
                from engramory.core.docs_tools import resolve_confined_repo_path

                scope = str(arguments.get("scope", "project"))
                depth = _coerce_int(arguments.get("depth"), 6, name="depth")
                layers = arguments.get("layers")
                max_tokens = _coerce_int(arguments.get("max_tokens"), 2000, name="max_tokens")
                fmt = str(arguments.get("format", "json"))
                offline = bool(arguments.get("offline", False))
                ws_dir = arguments.get("workspace_dir")
                enforce_project = self._auth_enabled and ("space" not in call_ctx.scopes)
                used_local_digest = False
                local_digest_text = ""

                if offline:
                    await self._surface._decide(
                        call_ctx,
                        "context_slice",
                        TargetScope(tenant_id=call_ctx.tenant_id, scope=scope),
                    )
                    confined_ws = resolve_confined_repo_path(
                        str(ws_dir or "."),
                        project_id=call_ctx.project_id,
                        enforce_project_boundary=enforce_project,
                    )
                    local_res = await asyncio.to_thread(
                        slice_local_context,
                        confined_ws,
                        task_id=arguments["task_id"],
                        depth=depth,
                        layers=layers,
                        max_tokens=max_tokens,
                        output_format=fmt,
                        allow_sibling_lookup=not enforce_project,
                    )
                    if fmt == "digest":
                        return {"content": [{"type": "text", "text": str(local_res["digest"])}]}
                    ancestors: Sequence[Mapping[str, Any]] = local_res["ancestors"]
                else:
                    ancestors = await self._surface.context_slice(
                        call_ctx,
                        task_id=arguments["task_id"],
                        depth=depth,
                        scope=scope,
                        layers=layers,
                        max_tokens=max_tokens,
                    )
                    if not ancestors and (ws_dir or not self._auth_enabled):
                        confined_ws = resolve_confined_repo_path(
                            str(ws_dir or "."),
                            project_id=call_ctx.project_id,
                            enforce_project_boundary=enforce_project,
                        )
                        local_res = await asyncio.to_thread(
                            slice_local_context,
                            confined_ws,
                            task_id=arguments["task_id"],
                            depth=depth,
                            layers=layers,
                            max_tokens=max_tokens,
                            output_format=fmt,
                            allow_sibling_lookup=not enforce_project,
                        )
                        if fmt == "digest":
                            used_local_digest = True
                            local_digest_text = str(local_res["digest"])
                        ancestors = local_res["ancestors"]

                if fmt == "digest":
                    digest_text = (
                        local_digest_text
                        if used_local_digest
                        else _format_context_slice_digest(
                            str(arguments["task_id"]), ancestors, max_tokens
                        )
                    )
                    return {"content": [{"type": "text", "text": digest_text}]}
                if fmt == "markdown":
                    md_text = format_context_slice_markdown(arguments["task_id"], ancestors)
                    return {"content": [{"type": "text", "text": md_text}]}
                return {
                    "content": [{"type": "text", "text": json.dumps(list(ancestors), indent=2)}]
                }

            elif name == "sdd_blast_radius":
                from engramory.access.authz import TargetScope
                from engramory.core.context_tools import local_blast_radius
                from engramory.core.docs_tools import resolve_confined_repo_path

                scope = str(arguments.get("scope", "project"))
                depth = _coerce_int(arguments.get("depth"), 6, name="depth")
                offline = bool(arguments.get("offline", False))
                ws_dir = arguments.get("workspace_dir")
                enforce_project = self._auth_enabled and ("space" not in call_ctx.scopes)

                if offline:
                    await self._surface._decide(
                        call_ctx,
                        "blast_radius",
                        TargetScope(tenant_id=call_ctx.tenant_id, scope=scope),
                    )
                    confined_ws = resolve_confined_repo_path(
                        str(ws_dir or "."),
                        project_id=call_ctx.project_id,
                        enforce_project_boundary=enforce_project,
                    )
                    local_blast = await asyncio.to_thread(
                        local_blast_radius,
                        confined_ws,
                        doc_id=arguments["doc_id"],
                        depth=depth,
                        allow_sibling_lookup=not enforce_project,
                    )
                    descendants: Sequence[Mapping[str, Any]] = local_blast["descendants"]
                else:
                    descendants = await self._surface.blast_radius(
                        call_ctx,
                        doc_id=arguments["doc_id"],
                        depth=depth,
                        scope=scope,
                    )
                    if not descendants and (ws_dir or not self._auth_enabled):
                        confined_ws = resolve_confined_repo_path(
                            str(ws_dir or "."),
                            project_id=call_ctx.project_id,
                            enforce_project_boundary=enforce_project,
                        )
                        local_blast = await asyncio.to_thread(
                            local_blast_radius,
                            confined_ws,
                            doc_id=arguments["doc_id"],
                            depth=depth,
                            allow_sibling_lookup=not enforce_project,
                        )
                        descendants = local_blast["descendants"]
                return {
                    "content": [{"type": "text", "text": json.dumps(list(descendants), indent=2)}]
                }

            elif name in ("sdd_validate_graph", "sdd_check_invariants", "sdd_lint_policy"):
                return await self._handle_sdd_context_tool(name, arguments, call_ctx)

            elif name == "sdd_ingest":
                from engramory.access.authz import TargetScope
                from engramory.core.docs_tools import resolve_confined_repo_path

                scope = str(arguments.get("scope", "project"))
                await self._surface._decide(
                    call_ctx,
                    "sdd_ingest",
                    TargetScope(tenant_id=call_ctx.tenant_id, scope=scope),
                )
                evidence_ref = str(arguments.get("evidence_ref") or "").strip()
                if not evidence_ref:
                    repo_obj = getattr(self._surface, "_repo", None)
                    if repo_obj is not None and hasattr(repo_obj, "record_audit"):
                        await repo_obj.record_audit(
                            tenant_id=call_ctx.tenant_id,
                            agent_id=call_ctx.agent_id,
                            project_id=call_ctx.project_id,
                            action="sdd_ingest",
                            allowed=False,
                            reason="missing_evidence_ref",
                        )
                    raise GovernedWriteRejected("sdd_ingest requires evidence_ref")
                if not self._ingest_fn:
                    return {
                        "isError": True,
                        "content": [{"type": "text", "text": "Ingest function not configured"}],
                    }
                sdd_root = arguments.get("sdd_root", "sdd")
                project_id = (
                    call_ctx.project_id
                    if self._auth_enabled
                    else arguments.get("project_id", call_ctx.project_id)
                )
                enforce_project = self._auth_enabled and ("space" not in call_ctx.scopes)
                confined_sdd = resolve_confined_repo_path(
                    str(sdd_root),
                    project_id=project_id,
                    enforce_project_boundary=enforce_project,
                )
                reconcile = arguments.get("reconcile", True)
                num_ent, num_rel = await self._ingest_fn(
                    sdd_root=str(confined_sdd),
                    tenant_id=call_ctx.tenant_id,
                    project_id=project_id,
                    reconcile=reconcile,
                )
                msg = (
                    f"Ingested {num_ent} entities and {num_rel} relations for project {project_id}"
                )
                return {"content": [{"type": "text", "text": msg}]}

            elif name.startswith("docs_"):
                return await self._handle_docs_tool(name, arguments, call_ctx)

            else:
                return {
                    "isError": True,
                    "content": [{"type": "text", "text": f"Unknown tool: {name}"}],
                }

        except (AuthzError, GovernedWriteRejected) as exc:
            return {"isError": True, "content": [{"type": "text", "text": f"Denied: {exc}"}]}
        except ValidationError as exc:
            return {
                "isError": True,
                "content": [{"type": "text", "text": f"Invalid input: {exc}"}],
            }
        except StoreUnavailable as exc:
            return {
                "isError": True,
                "content": [{"type": "text", "text": f"Store unavailable (retryable): {exc}"}],
            }
        except Exception as exc:  # noqa: BLE001  # reason: MCP tool execution must return structured error
            logger.exception("Error executing tool %s: %s", name, exc)
            return {"isError": True, "content": [{"type": "text", "text": str(exc)}]}

    async def handle_request(
        self, request: dict[str, Any], ctx: ActorContext | None = None
    ) -> dict[str, Any] | None:
        """Handle a single MCP JSON-RPC request."""
        if not isinstance(request, Mapping):
            return {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32600, "message": "Invalid Request: expected JSON object"},
            }
        req_id = request.get("id")
        method = request.get("method")
        if "id" not in request or (isinstance(method, str) and method.startswith("notifications/")):
            return None
        raw_params = request.get("params")
        params = raw_params if isinstance(raw_params, Mapping) else {}

        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "engramory-mcp", "version": "0.3.0"},
                },
            }
        elif method == "ping":
            return {"jsonrpc": "2.0", "id": req_id, "result": {}}
        elif method == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"tools": TOOLS_SCHEMA},
            }
        elif method == "tools/call":
            if not isinstance(raw_params, Mapping):
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32602, "message": "Invalid params: expected object"},
                }
            raw_name = params.get("name")
            raw_args = params.get("arguments")
            if not isinstance(raw_name, str) or not raw_name.strip():
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {
                        "code": -32602,
                        "message": "Invalid params: tools/call requires non-empty string 'name'",
                    },
                }
            if raw_args is not None and not isinstance(raw_args, Mapping):
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {
                        "code": -32602,
                        "message": "Invalid params: 'arguments' must be an object",
                    },
                }
            tool_name = raw_name.strip()
            tool_args = dict(raw_args) if raw_args is not None else {}
            call_result = await self.handle_call_tool(tool_name, tool_args, ctx=ctx)
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": call_result,
            }
        else:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"Method not found: {method}"},
            }

    def create_http_app(self, *, max_body_bytes: int = 4 * 1024 * 1024) -> FastAPI:
        """Build FastAPI ASGI application for MCP Streamable HTTP & persistent SSE transports."""
        server_instance = self
        app = FastAPI(
            title="engramory-mcp",
            version="0.3.0",
            docs_url=None,
            redoc_url=None,
            openapi_url=None,
        )
        sessions: dict[str, tuple[asyncio.Queue[str], str | None]] = {}
        app.state.sessions = sessions

        async def _authenticate_http_request(request: Request) -> ActorContext:
            if "token" in request.query_params:
                raise AuthenticationError(
                    "Query-string tokens are forbidden; use Authorization: Bearer header"
                )
            if server_instance._auth_enabled:
                auth_hdr = request.headers.get("authorization")
                if not auth_hdr or auth_hdr.strip()[:7].lower() != "bearer ":
                    raise AuthenticationError("Missing Bearer token")
                if server_instance._token_validator is None:
                    raise AuthenticationError("Token validator not configured")
                claims = server_instance._token_validator.validate_token(auth_hdr)
                await check_token_revocation_async(
                    claims,
                    security_gate_url=server_instance._security_gate_url,
                    revoked_identifiers=server_instance._revoked_identifiers,
                    cache=server_instance._revocation_cache,
                )
                return claims_to_actor_context(claims)

            req_ctx = server_instance._ctx
            tenant_hdr = request.headers.get("x-tenant-id")
            project_hdr = request.headers.get("x-project-id")
            domain_hdr = request.headers.get("x-domain-id")
            if tenant_hdr or project_hdr or domain_hdr:
                return ActorContext(
                    agent_id=req_ctx.agent_id,
                    tenant_id=tenant_hdr or req_ctx.tenant_id,
                    project_id=project_hdr or req_ctx.project_id,
                    scopes=req_ctx.scopes,
                    domain_id=domain_hdr or req_ctx.domain_id,
                )
            return req_ctx

        async def _read_bounded_body(request: Request) -> bytes:
            raw_cl = request.headers.get("content-length")
            if raw_cl is not None:
                try:
                    content_length = int(raw_cl)
                except ValueError as exc:
                    raise ValueError(f"Invalid Content-Length header: {raw_cl}") from exc
                if content_length < 0 or content_length > max_body_bytes:
                    raise OverflowError("Request payload too large")
            buf = bytearray()
            async for chunk in request.stream():
                if not chunk:
                    continue
                if len(buf) + len(chunk) > max_body_bytes:
                    raise OverflowError("Request payload too large")
                buf.extend(chunk)
            return bytes(buf)

        @app.get("/")
        @app.get("/healthz")
        async def liveness() -> JSONResponse:
            return JSONResponse(
                content={"status": "ok", "service": "engramory-mcp", "version": "0.3.0"},
                status_code=200,
            )

        @app.get("/health")
        @app.get("/ready")
        async def health() -> JSONResponse:
            checks: dict[str, str] = {}
            ok = True
            repo_obj = getattr(self._surface, "_repo", None)
            if isinstance(repo_obj, Repository):
                pg_ok = await repo_obj.ping(timeout_sec=2.0)
                checks["postgres"] = "ok" if pg_ok else "unreachable"
                if not pg_ok:
                    ok = False

            graph_obj = getattr(self._surface, "_graph", None)
            if graph_obj is not None and not type(graph_obj).__module__.startswith("unittest.mock"):
                ping_fn = getattr(graph_obj, "ping", None)
                driver = getattr(graph_obj, "_driver", None)
                verify_fn = getattr(driver, "verify_connectivity", None)
                if callable(ping_fn):
                    try:
                        res = ping_fn()
                        g_ok = (
                            bool(await asyncio.wait_for(res, timeout=2.0))
                            if asyncio.iscoroutine(res)
                            else bool(res)
                        )
                    except Exception:  # noqa: BLE001  # reason: readiness probe reports degraded graph cleanly
                        g_ok = False
                    checks["neo4j"] = "ok" if g_ok else "unreachable"
                    if not g_ok:
                        ok = False
                elif callable(verify_fn):
                    try:
                        vres = verify_fn()
                        if asyncio.iscoroutine(vres):
                            await asyncio.wait_for(vres, timeout=2.0)
                        checks["neo4j"] = "ok"
                    except Exception:  # noqa: BLE001  # reason: readiness probe reports degraded graph cleanly
                        checks["neo4j"] = "unreachable"
                        ok = False

            if self._auth_enabled:
                checks["jwks"] = "configured" if self._token_validator is not None else "missing"
                if self._token_validator is None:
                    ok = False

            status_code = 200 if ok else 503
            return JSONResponse(
                content={
                    "status": "ok" if ok else "degraded",
                    "service": "engramory-mcp",
                    "version": "0.3.0",
                    "checks": checks,
                },
                status_code=status_code,
            )

        @app.get("/sse")
        @app.get("/sse/")
        async def sse_endpoint(request: Request) -> Response:
            try:
                req_ctx = await _authenticate_http_request(request)
            except AuthenticationError as exc:
                return JSONResponse(
                    content={"error": f"Unauthorized: {exc}"},
                    status_code=401,
                )

            session_id = uuid.uuid4().hex
            queue: asyncio.Queue[str] = asyncio.Queue(maxsize=64)
            owner = req_ctx.agent_id if server_instance._auth_enabled else None
            sessions[session_id] = (queue, owner)

            async def event_generator() -> AsyncIterator[str]:
                try:
                    yield f"event: endpoint\ndata: /messages?session_id={session_id}\n\n"
                    while True:
                        shutdown_ev = getattr(app.state, "shutdown_event", None)
                        if isinstance(shutdown_ev, asyncio.Event) and shutdown_ev.is_set():
                            break
                        if await request.is_disconnected():
                            break
                        get_task: asyncio.Task[str] = asyncio.create_task(queue.get())
                        stop_task: asyncio.Task[bool] | None = (
                            asyncio.create_task(shutdown_ev.wait())
                            if isinstance(shutdown_ev, asyncio.Event)
                            else None
                        )
                        try:
                            tasks: list[asyncio.Task[Any]] = [get_task]
                            if stop_task is not None:
                                tasks.append(stop_task)
                            done, pending = await asyncio.wait(
                                tasks, timeout=0.1, return_when=asyncio.FIRST_COMPLETED
                            )
                            for pending_task in pending:
                                pending_task.cancel()
                                try:
                                    await pending_task
                                except asyncio.CancelledError:
                                    pass
                            if stop_task is not None and stop_task in done:
                                break
                            if get_task in done:
                                payload = get_task.result()
                                yield f"event: message\ndata: {payload}\n\n"
                        except BaseException:
                            for task in (get_task, stop_task):
                                if task is not None and not task.done():
                                    task.cancel()
                            raise
                finally:
                    sessions.pop(session_id, None)

            return StreamingResponse(
                event_generator(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                },
            )

        @app.post("/messages")
        @app.post("/messages/")
        @app.post("/mcp")
        @app.post("/mcp/")
        async def messages_endpoint(request: Request) -> Response:
            # Authenticate BEFORE reading or parsing request body.
            try:
                req_ctx = await _authenticate_http_request(request)
            except AuthenticationError as auth_exc:
                return JSONResponse(
                    content={
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {"code": -32001, "message": f"Unauthorized: {auth_exc}"},
                    },
                    status_code=401,
                )

            try:
                raw_body = await _read_bounded_body(request)
            except OverflowError:
                return JSONResponse(
                    content={
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {"code": -32600, "message": "Request payload too large"},
                    },
                    status_code=413,
                )
            except ValueError as cl_exc:
                return JSONResponse(
                    content={
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {"code": -32600, "message": str(cl_exc)},
                    },
                    status_code=400,
                )

            try:
                req = json.loads(raw_body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as parse_exc:
                return JSONResponse(
                    content={
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {"code": -32700, "message": f"Parse error: {parse_exc}"},
                    },
                    status_code=400,
                )

            req_id: Any = req.get("id") if isinstance(req, dict) else None
            session_id = request.query_params.get("session_id")
            session_entry = sessions.get(session_id) if session_id else None
            if session_entry is not None:
                _, owner = session_entry
                if (
                    server_instance._auth_enabled
                    and owner is not None
                    and owner != req_ctx.agent_id
                ):
                    return JSONResponse(
                        content={
                            "jsonrpc": "2.0",
                            "id": req_id,
                            "error": {
                                "code": -32003,
                                "message": "Forbidden: SSE session belongs to another principal",
                            },
                        },
                        status_code=403,
                    )

            try:
                res = await server_instance.handle_request(req, ctx=req_ctx)
                if session_entry is not None and res is not None:
                    try:
                        session_entry[0].put_nowait(json.dumps(res))
                    except asyncio.QueueFull:
                        pass
                if res is None:
                    return Response(status_code=204)
                return JSONResponse(content=res, status_code=200)
            except Exception as exc:  # noqa: BLE001  # reason: HTTP server must return JSON-RPC error
                return JSONResponse(
                    content={
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": {"code": -32603, "message": str(exc)},
                    },
                    status_code=500,
                )

        return app

    def create_http_server(self, host: str = "127.0.0.1", port: int = 8000) -> _UvicornMCPServer:
        """Create a uvicorn-backed HTTP/SSE server with pre-bound socket for MCP transports."""
        if not self._auth_enabled and not is_loopback_host(host):
            raise PermissionError(
                f"Refusing to bind unauthenticated MCP HTTP server to non-loopback host '{host}'; "
                f"enable --auth or bind to 127.0.0.1"
            )
        app = self.create_http_app()
        return _UvicornMCPServer(app, host=host, port=port)

    def run_sse(self, host: str = "127.0.0.1", port: int = 8000) -> None:
        """Run MCP HTTP/SSE server loop over TCP host:port."""
        httpd = self.create_http_server(host, port)
        logger.info("Engramory MCP SSE server listening on http://%s:%s", host, port)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            httpd.shutdown()
        finally:
            httpd.server_close()

    async def run_stdio(self) -> None:
        """Run MCP JSON-RPC server loop over stdin/stdout (dev unauthenticated only)."""
        if self._auth_enabled:
            raise PermissionError(
                "MCP stdio transport cannot be used with --auth enabled (ADR-10); "
                "use '--transport sse' with Bearer token verification"
            )
        max_line_bytes = 16 * 1024 * 1024
        loop = asyncio.get_running_loop()
        raw_reader = asyncio.StreamReader(limit=max_line_bytes)
        reader: asyncio.StreamReader | None = raw_reader
        protocol = asyncio.StreamReaderProtocol(raw_reader)
        try:
            await loop.connect_read_pipe(lambda: protocol, sys.stdin)
        except (NotImplementedError, OSError, AttributeError):
            reader = None

        async def _readline() -> bytes:
            if reader is not None:
                return await reader.readline()
            raw_stream = getattr(sys.stdin, "buffer", sys.stdin)
            line_raw = await asyncio.to_thread(raw_stream.readline)
            if isinstance(line_raw, str):
                return line_raw.encode("utf-8")
            return bytes(line_raw)

        while reader is None or not reader.at_eof():
            try:
                line = await _readline()
                if not line:
                    break
                if len(line) > max_line_bytes:
                    raise ValueError(f"stdio message exceeds {max_line_bytes} byte limit")
                text = line.decode("utf-8").strip()
                if not text:
                    continue
                req = json.loads(text)
                resp = await self.handle_request(req)
                if resp is not None:
                    sys.stdout.write(json.dumps(resp) + "\n")
                    sys.stdout.flush()
            except json.JSONDecodeError as exc:
                err = {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": f"Parse error: {exc}"},
                }
                sys.stdout.write(json.dumps(err) + "\n")
                sys.stdout.flush()
            except Exception as exc:  # noqa: BLE001  # reason: stdio loop must survive client transport errors
                logger.error("Error in stdio loop: %s", exc)

    async def _handle_docs_tool(
        self, name: str, arguments: dict[str, Any], call_ctx: ActorContext
    ) -> dict[str, Any]:
        """Dispatch AI-first documentation & executable runbook MCP tools."""
        from engramory.access.authz import TargetScope
        from engramory.core.docs_tools import (
            check_repo_drift,
            export_repo_llms,
            lint_repo,
            list_documentation_tools,
            resolve_confined_repo_path,
            slice_local_runbooks,
            smoke_run_green_steps,
            stage_portal_bundle,
        )

        scope = str(arguments.get("scope", "project"))
        enforce_project = self._auth_enabled and ("space" not in call_ctx.scopes)

        if name == "docs_ingest_runbook":
            from engramory.core.docs_tools import parse_markdown_document

            await self._surface._decide(
                call_ctx,
                "docs_ingest_runbook",
                TargetScope(tenant_id=call_ctx.tenant_id, scope=scope),
            )
            evidence_ref = str(arguments.get("evidence_ref") or "").strip()
            if not evidence_ref:
                repo_obj = getattr(self._surface, "_repo", None)
                if repo_obj is not None and hasattr(repo_obj, "record_audit"):
                    await repo_obj.record_audit(
                        tenant_id=call_ctx.tenant_id,
                        agent_id=call_ctx.agent_id,
                        project_id=call_ctx.project_id,
                        action="docs_ingest_runbook",
                        allowed=False,
                        reason="missing_evidence_ref",
                    )
                raise GovernedWriteRejected("docs_ingest_runbook requires evidence_ref")
            target = resolve_confined_repo_path(
                str(arguments["path"]),
                project_id=call_ctx.project_id,
                enforce_project_boundary=enforce_project,
            )
            trust_fm = (
                call_ctx.domain_id is not None
                or "domain" in call_ctx.scopes
                or "space" in call_ctx.scopes
            )
            parsed = await asyncio.to_thread(
                parse_markdown_document,
                target,
                tenant_id=call_ctx.tenant_id,
                project_id=None if scope in ("domain", "space") else call_ctx.project_id,
                domain_id=call_ctx.domain_id,
                scope=scope,
                trust_frontmatter_domain=trust_fm,
            )
            if parsed.kb_document is None:
                raise ValueError(
                    f"Runbook '{target.name}' is missing required governed YAML frontmatter"
                )
            ing_res = await self._surface.docs_ingest_runbook(
                call_ctx,
                doc=parsed.kb_document,
                steps=parsed.steps,
                evidence_ref=evidence_ref,
                scope=scope,
                _preauthorized=True,
            )
            return {"content": [{"type": "text", "text": json.dumps(ing_res, indent=2)}]}

        elif name == "docs_slice_runbook":
            from engramory.core.docs_tools import format_runbook_slice

            fmt = str(arguments.get("format", "json"))
            token_budget = _coerce_int(arguments.get("token_budget"), 2000, name="token_budget")
            doc_id = arguments.get("doc_id")
            alert_id = arguments.get("alert_id")
            max_tier = arguments.get("max_tier")
            repo_path_arg = arguments.get("repo_path")
            req_domain = arguments.get("domain_id")

            res = await self._surface.docs_slice_runbook(
                call_ctx,
                doc_id=doc_id,
                alert_id=alert_id,
                max_tier=max_tier,
                scope=scope,
                domain_id=req_domain,
                include_memories=bool(arguments.get("include_memories", True)),
                token_budget=token_budget,
                format=fmt,
            )
            if not res.get("l0_steps") and repo_path_arg:
                confined = resolve_confined_repo_path(
                    str(repo_path_arg),
                    project_id=call_ctx.project_id,
                    enforce_project_boundary=enforce_project,
                )
                local_slice = await asyncio.to_thread(
                    slice_local_runbooks,
                    confined,
                    doc_id=doc_id,
                    alert_id=alert_id,
                    max_tier=max_tier,
                    domain_id=str(req_domain) if req_domain is not None else call_ctx.domain_id,
                    token_budget=token_budget,
                    output_format="json",
                )
                res = format_runbook_slice(
                    local_slice.get("l0_steps", ()),
                    memories=res.get("l2_memories", ()),
                    token_budget=token_budget,
                    output_format=fmt,
                )
            text_out = (
                str(res["markdown"])
                if fmt == "markdown" and "markdown" in res
                else json.dumps(res, indent=2)
            )
            return {"content": [{"type": "text", "text": text_out}]}

        elif name == "docs_validate":
            await self._surface._decide(
                call_ctx, "docs_validate", TargetScope(tenant_id=call_ctx.tenant_id, scope=scope)
            )
            target = resolve_confined_repo_path(
                str(arguments.get("path", ".")),
                project_id=call_ctx.project_id,
                enforce_project_boundary=enforce_project,
            )
            report = await asyncio.to_thread(
                lint_repo,
                target,
                strict=bool(arguments.get("strict", False)),
                require_frontmatter=bool(arguments.get("require_frontmatter", False)),
                verify_registry=bool(arguments.get("verify_registry", False)),
            )
            return {"content": [{"type": "text", "text": json.dumps(report, indent=2)}]}

        elif name == "docs_check_drift":
            await self._surface._decide(
                call_ctx,
                "docs_check_drift",
                TargetScope(tenant_id=call_ctx.tenant_id, scope=scope),
            )
            target = resolve_confined_repo_path(
                str(arguments.get("repo_path", ".")),
                project_id=call_ctx.project_id,
                enforce_project_boundary=enforce_project,
            )
            report = await asyncio.to_thread(
                check_repo_drift,
                target,
                base_ref=str(arguments.get("base_ref", "HEAD~1")),
                head_ref=str(arguments.get("head_ref", "HEAD")),
            )
            return {"content": [{"type": "text", "text": json.dumps(report, indent=2)}]}

        elif name == "docs_export_llms":
            await self._surface._decide(
                call_ctx,
                "docs_export_llms",
                TargetScope(tenant_id=call_ctx.tenant_id, scope=scope),
            )
            target = resolve_confined_repo_path(
                str(arguments.get("repo_path", ".")),
                project_id=call_ctx.project_id,
                enforce_project_boundary=enforce_project,
            )
            # Read-only MCP tool: block disk writes when remote auth is enabled
            allow_write = False if self._auth_enabled else bool(arguments.get("write", False))
            report = await asyncio.to_thread(
                export_repo_llms,
                target,
                write_files=allow_write,
                include_full=bool(arguments.get("full", False)),
            )
            return {"content": [{"type": "text", "text": json.dumps(report, indent=2)}]}

        elif name == "docs_smoke_run":
            await self._surface._decide(
                call_ctx,
                "docs_smoke_run",
                TargetScope(tenant_id=call_ctx.tenant_id, scope=scope),
            )
            target = resolve_confined_repo_path(
                str(arguments.get("repo_path", ".")),
                project_id=call_ctx.project_id,
                enforce_project_boundary=enforce_project,
            )
            # Enforce dry_run=True when remote auth is enabled; default to True otherwise
            dry_run = True if self._auth_enabled else bool(arguments.get("dry_run", True))
            report = await asyncio.to_thread(
                smoke_run_green_steps,
                target,
                doc_id=arguments.get("doc_id"),
                dry_run=dry_run,
            )
            return {"content": [{"type": "text", "text": json.dumps(report, indent=2)}]}

        elif name == "docs_tools_inventory":
            await self._surface._decide(
                call_ctx,
                "docs_tools_inventory",
                TargetScope(tenant_id=call_ctx.tenant_id, scope=scope),
            )
            tier_arg = arguments.get("tier")
            report = list_documentation_tools(tier=str(tier_arg) if tier_arg is not None else None)
            return {"content": [{"type": "text", "text": json.dumps(report, indent=2)}]}

        elif name == "docs_build_portal":
            await self._surface._decide(
                call_ctx,
                "docs_build_portal",
                TargetScope(tenant_id=call_ctx.tenant_id, scope=scope),
            )
            target = resolve_confined_repo_path(
                str(arguments.get("repo_path", ".")),
                project_id=call_ctx.project_id,
                enforce_project_boundary=enforce_project,
            )
            out_dir_arg = arguments.get("output_dir")
            confined_out = (
                resolve_confined_repo_path(
                    str(out_dir_arg),
                    project_id=call_ctx.project_id,
                    enforce_project_boundary=enforce_project,
                )
                if out_dir_arg is not None
                else None
            )
            dry_run = True if self._auth_enabled else bool(arguments.get("dry_run", True))
            report = await asyncio.to_thread(
                stage_portal_bundle,
                target,
                output_dir=confined_out,
                dry_run=dry_run,
            )
            return {"content": [{"type": "text", "text": json.dumps(report, indent=2)}]}

        return {
            "isError": True,
            "content": [{"type": "text", "text": f"Unknown tool: {name}"}],
        }

    async def _handle_sdd_context_tool(
        self, name: str, arguments: dict[str, Any], call_ctx: ActorContext
    ) -> dict[str, Any]:
        """Dispatch built-in SDD DAG validation, invariant, and stack-policy MCP tools."""
        from engramory.access.authz import TargetScope
        from engramory.core.context_tools import (
            check_sdd_invariants,
            lint_stack_policy,
            validate_sdd_graph,
        )
        from engramory.core.docs_tools import resolve_confined_repo_path

        scope = str(arguments.get("scope", "project"))
        enforce_project = self._auth_enabled and ("space" not in call_ctx.scopes)
        ws_arg = str(arguments.get("workspace_dir", "."))

        if name == "sdd_validate_graph":
            await self._surface._decide(
                call_ctx,
                "sdd_validate_graph",
                TargetScope(tenant_id=call_ctx.tenant_id, scope=scope),
            )
            target = resolve_confined_repo_path(
                ws_arg,
                project_id=call_ctx.project_id,
                enforce_project_boundary=enforce_project,
            )
            report = await asyncio.to_thread(
                validate_sdd_graph,
                target,
                check_invariants=bool(arguments.get("invariants", False)),
                allow_sibling_lookup=not enforce_project,
            )
            return {
                "isError": not bool(report["passed"]),
                "content": [{"type": "text", "text": json.dumps(report, indent=2)}],
            }

        elif name == "sdd_check_invariants":
            await self._surface._decide(
                call_ctx,
                "sdd_check_invariants",
                TargetScope(tenant_id=call_ctx.tenant_id, scope=scope),
            )
            target = resolve_confined_repo_path(
                ws_arg,
                project_id=call_ctx.project_id,
                enforce_project_boundary=enforce_project,
            )
            report = await asyncio.to_thread(
                check_sdd_invariants,
                target,
                base_ref=str(arguments.get("base_ref", "origin/dev")),
                chg_type=str(arguments.get("change_type", "CODE2C")),
                include_blast_warnings=not bool(arguments.get("offline", False)),
                allow_sibling_lookup=not enforce_project,
            )
            return {
                "isError": not bool(report["passed"]),
                "content": [{"type": "text", "text": json.dumps(report, indent=2)}],
            }

        elif name == "sdd_lint_policy":
            await self._surface._decide(
                call_ctx,
                "sdd_lint_policy",
                TargetScope(tenant_id=call_ctx.tenant_id, scope=scope),
            )
            target = resolve_confined_repo_path(
                ws_arg,
                project_id=call_ctx.project_id,
                enforce_project_boundary=enforce_project,
            )
            raw_pol = arguments.get("policy_path")
            confined_pol = (
                resolve_confined_repo_path(
                    str(raw_pol),
                    project_id=call_ctx.project_id,
                    enforce_project_boundary=enforce_project,
                )
                if raw_pol
                else None
            )
            report = await asyncio.to_thread(
                lint_stack_policy,
                target,
                policy_path=confined_pol,
            )
            return {
                "isError": not bool(report["passed"]),
                "content": [{"type": "text", "text": json.dumps(report, indent=2)}],
            }

        return {
            "isError": True,
            "content": [{"type": "text", "text": f"Unknown tool: {name}"}],
        }


DOCS_TOOLS_SCHEMA: list[dict[str, Any]] = [
    {
        "name": "docs_slice_runbook",
        "description": (
            "Slice L0 canonical executable runbook steps and L2 operational memories by "
            "doc_id, alert_id, and max_tier (token-budgeted, L0-before-L2 ordered)."
        ),
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "doc_id": {"type": "string", "description": "Target runbook document ID"},
                "alert_id": {
                    "type": "string",
                    "description": "Alert trigger ID (e.g. ALT-DOC-004)",
                },
                "max_tier": {
                    "type": "string",
                    "enum": ["green", "yellow", "red"],
                    "description": "Maximum autonomy tier to include",
                },
                "repo_path": {
                    "type": "string",
                    "description": "Optional confined local repo path fallback",
                },
                "scope": {"type": "string", "default": "project"},
                "include_memories": {"type": "boolean", "default": True},
                "token_budget": {
                    "type": "integer",
                    "default": 2000,
                    "description": "Maximum token budget for returned context",
                },
                "format": {
                    "type": "string",
                    "enum": ["json", "markdown"],
                    "default": "json",
                },
            },
        },
    },
    {
        "name": "docs_validate",
        "description": (
            "Validate Markdown frontmatter, executable runbook step AST invariants "
            "(INV-STEP-01/02), chunk-independence prose rules, and referential integrity."
        ),
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "default": "."},
                "strict": {"type": "boolean", "default": False},
                "require_frontmatter": {"type": "boolean", "default": False},
                "verify_registry": {"type": "boolean", "default": False},
                "scope": {"type": "string", "default": "project"},
            },
        },
    },
    {
        "name": "docs_check_drift",
        "description": (
            "Check git diff against watched_paths globs and last_verified TTL staleness."
        ),
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo_path": {"type": "string", "default": "."},
                "base_ref": {"type": "string", "default": "HEAD~1"},
                "head_ref": {"type": "string", "default": "HEAD"},
                "scope": {"type": "string", "default": "project"},
            },
        },
    },
    {
        "name": "docs_export_llms",
        "description": (
            "Generate deterministic, D14-scrubbed /llms.txt and /llms-full.txt manifests."
        ),
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo_path": {"type": "string", "default": "."},
                "write": {"type": "boolean", "default": False},
                "full": {"type": "boolean", "default": False},
                "scope": {"type": "string", "default": "project"},
            },
        },
    },
    {
        "name": "docs_smoke_run",
        "description": (
            "Verify and smoke-test tier='green' mode='read-only' runbook steps "
            "(defaults to dry_run=true bash -n syntax check over MCP)."
        ),
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo_path": {"type": "string", "default": "."},
                "doc_id": {"type": "string"},
                "dry_run": {"type": "boolean", "default": True},
                "scope": {"type": "string", "default": "project"},
            },
        },
    },
    {
        "name": "docs_tools_inventory",
        "description": (
            "Return the governed documentation tool inventory (Adopt, Trial, Hold tiers)."
        ),
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "tier": {
                    "type": "string",
                    "enum": ["Adopt", "Trial", "Hold"],
                    "description": "Optional governance tier filter",
                },
                "scope": {"type": "string", "default": "project"},
            },
        },
    },
    {
        "name": "docs_build_portal",
        "description": (
            "Stage governed docs/*.md into Astro Starlight portal collection with D14 secret "
            "scrubbing (forces dry_run=true when remote auth is enabled)."
        ),
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo_path": {"type": "string", "default": "."},
                "output_dir": {"type": "string"},
                "dry_run": {"type": "boolean", "default": True},
                "scope": {"type": "string", "default": "project"},
            },
        },
    },
]

TOOLS_SCHEMA.extend(DOCS_TOOLS_SCHEMA)

SDD_CONTEXT_TOOLS_SCHEMA: list[dict[str, Any]] = [
    {
        "name": "sdd_validate_graph",
        "description": (
            "Validate SDD dependency graph acyclicity (DAG) and optional SDD-GRAPH v2.0 "
            "semantic cardinality and oracle trace invariants (GRAPH-CARD-001..003)."
        ),
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "workspace_dir": {
                    "type": "string",
                    "default": ".",
                    "description": "Confined workspace root directory to scan",
                },
                "invariants": {
                    "type": "boolean",
                    "default": False,
                    "description": "Enforce SDD-GRAPH v2.0 semantic cardinality invariants",
                },
                "scope": {"type": "string", "default": "project"},
            },
        },
    },
    {
        "name": "sdd_check_invariants",
        "description": (
            "Pre-merge contract erosion guard preventing fast-path changes (CODE2C, DIR2C, "
            "HOTFIX) from mutating protected contracts and emitting blast-radius advisories."
        ),
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "workspace_dir": {
                    "type": "string",
                    "default": ".",
                    "description": "Confined workspace root directory",
                },
                "base_ref": {
                    "type": "string",
                    "default": "origin/dev",
                    "description": "Base git reference for diff analysis",
                },
                "change_type": {
                    "type": "string",
                    "default": "CODE2C",
                    "description": "Change classification (CODE2C, DIR2C, SEED2C, SDD2C, HOTFIX)",
                },
                "offline": {
                    "type": "boolean",
                    "default": False,
                    "description": "Skip downstream blast-radius advisory check",
                },
                "scope": {"type": "string", "default": "project"},
            },
        },
    },
    {
        "name": "sdd_lint_policy",
        "description": (
            "Validate codebase banned patterns and dependency lockfiles (pyproject.toml, "
            "uv.lock, requirements.txt, poetry.lock) against .aidoc/stack-policy.yaml."
        ),
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "workspace_dir": {
                    "type": "string",
                    "default": ".",
                    "description": "Confined workspace root directory",
                },
                "policy_path": {
                    "type": "string",
                    "description": "Optional confined path to stack-policy.yaml",
                },
                "scope": {"type": "string", "default": "project"},
            },
        },
    },
]

TOOLS_SCHEMA.extend(SDD_CONTEXT_TOOLS_SCHEMA)
