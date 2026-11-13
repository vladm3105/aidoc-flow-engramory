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
import http.server
import json
import logging
import sys
import urllib.parse
from collections.abc import Callable, Coroutine, Mapping, Sequence
from typing import Any

from engramory.access.authz import ActorContext
from engramory.access.jwt_auth import (
    AuthenticationError,
    JWKSValidator,
    check_token_revocation,
    claims_to_actor_context,
)
from engramory.access.surface import AccessSurface, AuthzError, GovernedWriteRejected
from engramory.core.repository import StoreUnavailable

logger = logging.getLogger(__name__)

MCP_PROTOCOL_VERSION = "2024-11-05"

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
            },
            "required": ["content"],
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
            },
            "required": ["passport", "evidence_ref"],
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
                "reconcile": {
                    "type": "boolean",
                    "description": "Purge stale nodes/edges not in corpus (default: true)",
                    "default": True,
                },
            },
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
        path = props.get("path", "")
        file_link = f"file:///{path.lstrip('/')}" if path else ""
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
                hits = await self._surface.memory_search(
                    call_ctx,
                    query=arguments["query"],
                    k=arguments.get("k", 8),
                    scope=arguments.get("scope", "agent"),
                    token_budget=2000 if raw_budget is None else int(raw_budget),
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
                            {"provenance": dict(h.provenance)}
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
                ep_id = await self._surface.memory_add(
                    call_ctx,
                    content=arguments["content"],
                    kind=arguments.get("kind", "note"),
                    **add_kwargs,
                )
                return {"content": [{"type": "text", "text": f"Added episode {ep_id}"}]}

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
                health = await self._surface.memory_learning_health(
                    call_ctx,
                    project_id=arguments.get("project_id"),
                    limit=int(arguments.get("limit", 500)),
                    scope=str(arguments.get("scope", "project")),
                )
                return {"content": [{"type": "text", "text": json.dumps(health, indent=2)}]}

            elif name == "agent_profile_get":
                profile = await self._surface.agent_profile_get(call_ctx)
                data = {
                    "agent_id": profile.agent_id,
                    "display_name": profile.display_name,
                    "standing_preferences": dict(profile.standing_preferences),
                }
                return {"content": [{"type": "text", "text": json.dumps(data, indent=2)}]}

            elif name == "agent_brain_export":
                raw_redact = arguments.get("redact_terms", [])
                redact_terms = (
                    [str(x) for x in raw_redact if str(x)] if isinstance(raw_redact, list) else []
                )
                bundle = await self._surface.agent_brain_export(
                    call_ctx,
                    domain_id=arguments.get("domain_id"),
                    min_confidence=float(arguments.get("min_confidence", 0.5)),
                    redact_terms=redact_terms,
                )
                return {"content": [{"type": "text", "text": json.dumps(bundle, indent=2)}]}

            elif name == "agent_brain_import":
                imported = await self._surface.agent_brain_import(
                    call_ctx,
                    passport=dict(arguments["passport"]),
                    evidence_ref=str(arguments["evidence_ref"]),
                    target_domain_id=arguments.get("target_domain_id"),
                )
                return {"content": [{"type": "text", "text": json.dumps(imported, indent=2)}]}

            elif name == "knowledge_ingest":
                sec_id = await self._surface.knowledge_ingest(
                    call_ctx,
                    doc_id=arguments["doc_id"],
                    citation=arguments["citation"],
                    text=arguments["text"],
                    evidence_ref=arguments["evidence_ref"],
                    scope=arguments.get("scope", "project"),
                    version=arguments.get("version", 1),
                )
                return {"content": [{"type": "text", "text": f"Ingested section {sec_id}"}]}

            elif name == "knowledge_search":
                search_kwargs: dict[str, Any] = {
                    "query": arguments["query"],
                    "domain_id": arguments.get("domain_id"),
                    "scope": arguments.get("scope", "project"),
                    "limit": int(arguments.get("k", arguments.get("limit", 5))),
                }
                if "modality" in arguments and arguments["modality"] is not None:
                    search_kwargs["modality"] = str(arguments["modality"])
                if "expand_graph" in arguments:
                    search_kwargs["expand_graph"] = bool(arguments["expand_graph"])
                if "graph_depth" in arguments and arguments["graph_depth"] is not None:
                    search_kwargs["graph_depth"] = int(arguments["graph_depth"])

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
                        "media_metadata": dict(getattr(s, "media_metadata", {}) or {}),
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

            elif name == "graph_neighbors":
                neighbors = await self._surface.graph_neighbors(
                    call_ctx,
                    id=arguments["id"],
                    rel=arguments.get("rel"),
                    depth=arguments.get("depth", 1),
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
                depth = int(arguments.get("depth", 6))
                layers = arguments.get("layers")
                max_tokens = int(arguments.get("max_tokens", 2000))
                fmt = str(arguments.get("format", "json"))
                offline = bool(arguments.get("offline", False))
                ws_dir = arguments.get("workspace_dir")
                enforce_project = self._auth_enabled and ("space" not in call_ctx.scopes)

                if offline or fmt == "digest":
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
                    local_res = slice_local_context(
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
                    try:
                        ancestors = await self._surface.context_slice(
                            call_ctx,
                            task_id=arguments["task_id"],
                            depth=depth,
                            scope=scope,
                            layers=layers,
                            max_tokens=max_tokens,
                        )
                    except StoreUnavailable:
                        if not ws_dir:
                            raise
                        from engramory.access.authz import authorize

                        decision = authorize(
                            call_ctx,
                            "context_slice",
                            TargetScope(tenant_id=call_ctx.tenant_id, scope=scope),
                        )
                        if not decision.allowed:
                            raise AuthzError(decision.reason) from None
                        ancestors = []
                    if not ancestors and (ws_dir or not self._auth_enabled):
                        confined_ws = resolve_confined_repo_path(
                            str(ws_dir or "."),
                            project_id=call_ctx.project_id,
                            enforce_project_boundary=enforce_project,
                        )
                        local_res = slice_local_context(
                            confined_ws,
                            task_id=arguments["task_id"],
                            depth=depth,
                            layers=layers,
                            max_tokens=max_tokens,
                            output_format=fmt,
                            allow_sibling_lookup=not enforce_project,
                        )
                        ancestors = local_res["ancestors"]

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
                depth = int(arguments.get("depth", 6))
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
                    local_blast = local_blast_radius(
                        confined_ws,
                        doc_id=arguments["doc_id"],
                        depth=depth,
                        allow_sibling_lookup=not enforce_project,
                    )
                    descendants: Sequence[Mapping[str, Any]] = local_blast["descendants"]
                else:
                    try:
                        descendants = await self._surface.blast_radius(
                            call_ctx,
                            doc_id=arguments["doc_id"],
                            depth=depth,
                            scope=scope,
                        )
                    except StoreUnavailable:
                        if not ws_dir:
                            raise
                        from engramory.access.authz import authorize

                        decision = authorize(
                            call_ctx,
                            "blast_radius",
                            TargetScope(tenant_id=call_ctx.tenant_id, scope=scope),
                        )
                        if not decision.allowed:
                            raise AuthzError(decision.reason) from None
                        descendants = []
                    if not descendants and (ws_dir or not self._auth_enabled):
                        confined_ws = resolve_confined_repo_path(
                            str(ws_dir or "."),
                            project_id=call_ctx.project_id,
                            enforce_project_boundary=enforce_project,
                        )
                        local_blast = local_blast_radius(
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
        raw_params = request.get("params")
        params = raw_params if isinstance(raw_params, Mapping) else {}

        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "engramory-mcp", "version": "0.2.0"},
                },
            }
        elif method == "notifications/initialized":
            return None
        elif method == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"tools": TOOLS_SCHEMA},
            }
        elif method == "tools/call":
            tool_name = str(params.get("name") or "")
            raw_args = params.get("arguments")
            tool_args = dict(raw_args) if isinstance(raw_args, Mapping) else {}
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

    def create_http_server(
        self, host: str = "127.0.0.1", port: int = 8000
    ) -> http.server.ThreadingHTTPServer:
        """Create a ThreadingHTTPServer for MCP SSE and HTTP transports."""
        import threading

        server_instance = self
        max_body_bytes = 4 * 1024 * 1024
        bg_loop = asyncio.new_event_loop()
        loop_ready = threading.Event()

        def _run_bg_loop() -> None:
            asyncio.set_event_loop(bg_loop)
            loop_ready.set()
            bg_loop.run_forever()
            bg_loop.close()

        loop_thread = threading.Thread(target=_run_bg_loop, daemon=True, name="engramory-mcp-loop")
        loop_thread.start()
        loop_ready.wait(timeout=5.0)

        class MCPRequestHandler(http.server.BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:
                # Suppress noisy default console access logs
                pass

            def do_GET(self) -> None:
                parsed = urllib.parse.urlparse(self.path)
                if parsed.path in ("/sse", "/sse/"):
                    if server_instance._auth_enabled:
                        extracted_bearer = ""
                        auth_hdr = self.headers.get("Authorization")
                        if auth_hdr and auth_hdr.strip().startswith("Bearer "):
                            extracted_bearer = auth_hdr.strip()[7:].strip()
                        else:
                            query_params = urllib.parse.parse_qs(parsed.query)
                            token_vals = query_params.get("token")
                            if token_vals:
                                extracted_bearer = token_vals[0].strip()

                        if not extracted_bearer:
                            self.send_response(401)
                            self.send_header("Content-Type", "application/json")
                            self.end_headers()
                            err_resp = {"error": "Unauthorized: Missing Bearer token"}
                            self.wfile.write(json.dumps(err_resp).encode("utf-8"))
                            return

                        try:
                            if server_instance._token_validator is None:
                                raise AuthenticationError("Token validator not configured")
                            claims = server_instance._token_validator.validate_token(
                                extracted_bearer
                            )
                            check_token_revocation(
                                claims,
                                security_gate_url=server_instance._security_gate_url,
                                revoked_identifiers=server_instance._revoked_identifiers,
                            )
                        except AuthenticationError as exc:
                            self.send_response(401)
                            self.send_header("Content-Type", "application/json")
                            self.end_headers()
                            err_resp = {"error": f"Unauthorized: {exc}"}
                            self.wfile.write(json.dumps(err_resp).encode("utf-8"))
                            return

                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Cache-Control", "no-cache")
                    self.send_header("Connection", "keep-alive")
                    self.end_headers()
                    endpoint_event = "event: endpoint\ndata: /messages\n\n"
                    self.wfile.write(endpoint_event.encode("utf-8"))
                    self.wfile.flush()
                elif parsed.path in ("/", "/health", "/healthz"):
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    resp = {"status": "ok", "service": "engramory-mcp", "version": "0.2.0"}
                    self.wfile.write(json.dumps(resp).encode("utf-8"))
                else:
                    self.send_response(404)
                    self.end_headers()

            def do_POST(self) -> None:
                if self.path not in ("/messages", "/messages/"):
                    self.send_response(404)
                    self.end_headers()
                    return

                content_length = int(self.headers.get("Content-Length", 0))
                if content_length < 0 or content_length > max_body_bytes:
                    self.send_response(413)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    err = {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {"code": -32600, "message": "Request payload too large"},
                    }
                    self.wfile.write(json.dumps(err).encode("utf-8"))
                    return

                body = self.rfile.read(content_length).decode("utf-8")
                req_id: Any = None
                try:
                    try:
                        req = json.loads(body)
                    except json.JSONDecodeError as parse_exc:
                        self.send_response(400)
                        self.send_header("Content-Type", "application/json")
                        self.end_headers()
                        err = {
                            "jsonrpc": "2.0",
                            "id": None,
                            "error": {"code": -32700, "message": f"Parse error: {parse_exc}"},
                        }
                        self.wfile.write(json.dumps(err).encode("utf-8"))
                        return

                    if isinstance(req, dict):
                        req_id = req.get("id")

                    if server_instance._auth_enabled:
                        auth_hdr = self.headers.get("Authorization")
                        if not auth_hdr or not auth_hdr.strip().startswith("Bearer "):
                            self.send_response(401)
                            self.send_header("Content-Type", "application/json")
                            self.end_headers()
                            err = {
                                "jsonrpc": "2.0",
                                "id": req_id,
                                "error": {
                                    "code": -32001,
                                    "message": "Unauthorized: Missing Bearer token",
                                },
                            }
                            self.wfile.write(json.dumps(err).encode("utf-8"))
                            return

                        if server_instance._token_validator is None:
                            raise AuthenticationError("Token validator not configured")
                        claims = server_instance._token_validator.validate_token(auth_hdr)
                        check_token_revocation(
                            claims,
                            security_gate_url=server_instance._security_gate_url,
                            revoked_identifiers=server_instance._revoked_identifiers,
                        )
                        req_ctx = claims_to_actor_context(claims)
                    else:
                        req_ctx = server_instance._ctx
                        tenant_hdr = self.headers.get("X-Tenant-ID")
                        project_hdr = self.headers.get("X-Project-ID")
                        if tenant_hdr or project_hdr:
                            req_ctx = ActorContext(
                                agent_id=req_ctx.agent_id,
                                tenant_id=tenant_hdr or req_ctx.tenant_id,
                                project_id=project_hdr or req_ctx.project_id,
                                scopes=req_ctx.scopes,
                            )

                    fut = asyncio.run_coroutine_threadsafe(
                        server_instance.handle_request(req, ctx=req_ctx), bg_loop
                    )
                    res = fut.result(timeout=60.0)
                    if res is None:
                        self.send_response(204)
                        self.end_headers()
                    else:
                        self.send_response(200)
                        self.send_header("Content-Type", "application/json")
                        self.end_headers()
                        self.wfile.write(json.dumps(res).encode("utf-8"))
                except AuthenticationError as auth_exc:
                    self.send_response(401)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    err = {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": {"code": -32001, "message": f"Unauthorized: {auth_exc}"},
                    }
                    self.wfile.write(json.dumps(err).encode("utf-8"))
                except Exception as exc:  # noqa: BLE001  # reason: HTTP server must return JSON-RPC error
                    self.send_response(500)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    err = {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": {"code": -32603, "message": str(exc)},
                    }
                    self.wfile.write(json.dumps(err).encode("utf-8"))

        class _ManagedMCPHTTPServer(http.server.ThreadingHTTPServer):
            def server_close(self) -> None:
                try:
                    if bg_loop.is_running():
                        bg_loop.call_soon_threadsafe(bg_loop.stop)
                finally:
                    super().server_close()

        return _ManagedMCPHTTPServer((host, port), MCPRequestHandler)

    def run_sse(self, host: str = "127.0.0.1", port: int = 8000) -> None:
        """Run MCP HTTP/SSE server loop over TCP host:port."""
        httpd = self.create_http_server(host, port)
        logger.info("Engramory MCP SSE server listening on http://%s:%s", host, port)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            httpd.server_close()

    async def run_stdio(self) -> None:
        """Run MCP JSON-RPC server loop over stdin/stdout."""
        loop = asyncio.get_running_loop()
        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        await loop.connect_read_pipe(lambda: protocol, sys.stdin)

        while not reader.at_eof():
            try:
                line = await reader.readline()
                if not line:
                    break
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

            target = resolve_confined_repo_path(
                str(arguments["path"]),
                project_id=call_ctx.project_id,
                enforce_project_boundary=enforce_project,
            )
            parsed = parse_markdown_document(
                target,
                tenant_id=call_ctx.tenant_id,
                project_id=call_ctx.project_id,
            )
            if parsed.kb_document is None:
                raise ValueError(
                    f"Runbook '{target.name}' is missing required governed YAML frontmatter"
                )
            ing_res = await self._surface.docs_ingest_runbook(
                call_ctx,
                doc=parsed.kb_document,
                steps=parsed.steps,
                evidence_ref=arguments.get("evidence_ref"),
                scope=scope,
            )
            return {"content": [{"type": "text", "text": json.dumps(ing_res, indent=2)}]}

        elif name == "docs_slice_runbook":
            from engramory.core.docs_tools import format_runbook_slice

            fmt = str(arguments.get("format", "json"))
            token_budget = int(arguments.get("token_budget", 2000))
            doc_id = arguments.get("doc_id")
            alert_id = arguments.get("alert_id")
            max_tier = arguments.get("max_tier")
            repo_path_arg = arguments.get("repo_path")

            res = await self._surface.docs_slice_runbook(
                call_ctx,
                doc_id=doc_id,
                alert_id=alert_id,
                max_tier=max_tier,
                scope=scope,
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
                local_slice = slice_local_runbooks(
                    confined,
                    doc_id=doc_id,
                    alert_id=alert_id,
                    max_tier=max_tier,
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
            report = lint_repo(
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
            report = check_repo_drift(
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
            report = export_repo_llms(
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
            report = smoke_run_green_steps(
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
            report = stage_portal_bundle(
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
            report = validate_sdd_graph(
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
            report = check_sdd_invariants(
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
            report = lint_stack_policy(
                target,
                policy_path=arguments.get("policy_path"),
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
            "readOnlyHint": True,
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
            "readOnlyHint": True,
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
            "readOnlyHint": True,
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
