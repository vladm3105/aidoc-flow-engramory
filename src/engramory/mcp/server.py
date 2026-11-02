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
import json
import logging
import sys
from collections.abc import Callable, Coroutine
from typing import Any

from engramory.access.authz import ActorContext
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
                    "description": "Visibility scope (agent, project)",
                    "default": "agent",
                },
                "token_budget": {
                    "type": "integer",
                    "description": "Maximum tokens returned",
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
            },
        },
    },
]


class McpServer:
    """Async JSON-RPC stdio MCP Gateway over AccessSurface."""

    def __init__(
        self,
        surface: AccessSurface,
        ctx: ActorContext,
        *,
        ingest_fn: Callable[..., Coroutine[Any, Any, tuple[int, int]]] | None = None,
    ) -> None:
        self._surface = surface
        self._ctx = ctx
        self._ingest_fn = ingest_fn

    async def handle_call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Dispatch an MCP tool call to the AccessSurface or storage engine."""
        try:
            if name == "memory_search":
                hits = await self._surface.memory_search(
                    self._ctx,
                    query=arguments["query"],
                    k=arguments.get("k", 8),
                    scope=arguments.get("scope", "agent"),
                    token_budget=arguments.get("token_budget"),
                )
                formatted = [
                    {
                        "memory_id": h.memory_id,
                        "summary": h.summary,
                        "score": h.score,
                        "token_count": h.token_count,
                        "retrieval_id": h.retrieval_id,
                    }
                    for h in hits
                ]
                return {"content": [{"type": "text", "text": json.dumps(formatted, indent=2)}]}

            elif name == "memory_add":
                ep_id = await self._surface.memory_add(
                    self._ctx,
                    content=arguments["content"],
                    kind=arguments.get("kind", "note"),
                )
                return {"content": [{"type": "text", "text": f"Added episode {ep_id}"}]}

            elif name == "memory_feedback":
                await self._surface.memory_feedback(
                    self._ctx,
                    retrieval_id=arguments["retrieval_id"],
                    outcome=arguments["outcome"],
                )
                return {"content": [{"type": "text", "text": "Feedback recorded"}]}

            elif name == "memory_forget":
                await self._surface.memory_forget(
                    self._ctx,
                    memory_id=arguments["memory_id"],
                    reason=arguments["reason"],
                )
                return {"content": [{"type": "text", "text": "Memory retired"}]}

            elif name == "agent_profile_get":
                profile = await self._surface.agent_profile_get(self._ctx)
                data = {
                    "agent_id": profile.agent_id,
                    "display_name": profile.display_name,
                    "standing_preferences": dict(profile.standing_preferences),
                }
                return {"content": [{"type": "text", "text": json.dumps(data, indent=2)}]}

            elif name == "knowledge_ingest":
                sec_id = await self._surface.knowledge_ingest(
                    self._ctx,
                    doc_id=arguments["doc_id"],
                    citation=arguments["citation"],
                    text=arguments["text"],
                    evidence_ref=arguments["evidence_ref"],
                    scope=arguments.get("scope", "project"),
                    version=arguments.get("version", 1),
                )
                return {"content": [{"type": "text", "text": f"Ingested section {sec_id}"}]}

            elif name == "graph_neighbors":
                neighbors = await self._surface.graph_neighbors(
                    self._ctx,
                    id=arguments["id"],
                    rel=arguments.get("rel"),
                    depth=arguments.get("depth", 1),
                )
                return {
                    "content": [{"type": "text", "text": json.dumps(list(neighbors), indent=2)}]
                }

            elif name == "sdd_context_slice":
                ancestors = await self._surface.context_slice(
                    self._ctx,
                    task_id=arguments["task_id"],
                    depth=arguments.get("depth", 6),
                    scope=arguments.get("scope", "project"),
                )
                return {
                    "content": [{"type": "text", "text": json.dumps(list(ancestors), indent=2)}]
                }

            elif name == "sdd_blast_radius":
                descendants = await self._surface.blast_radius(
                    self._ctx,
                    doc_id=arguments["doc_id"],
                    depth=arguments.get("depth", 6),
                    scope=arguments.get("scope", "project"),
                )
                return {
                    "content": [{"type": "text", "text": json.dumps(list(descendants), indent=2)}]
                }

            elif name == "sdd_ingest":
                if not self._ingest_fn:
                    return {
                        "isError": True,
                        "content": [{"type": "text", "text": "Ingest function not configured"}],
                    }
                sdd_root = arguments.get("sdd_root", "sdd")
                project_id = arguments.get("project_id", self._ctx.project_id)
                num_ent, num_rel = await self._ingest_fn(
                    sdd_root=sdd_root,
                    tenant_id=self._ctx.tenant_id,
                    project_id=project_id,
                )
                msg = (
                    f"Ingested {num_ent} entities and {num_rel} relations for project {project_id}"
                )
                return {"content": [{"type": "text", "text": msg}]}

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

    async def handle_request(self, request: dict[str, Any]) -> dict[str, Any] | None:
        """Handle a single MCP JSON-RPC request."""
        req_id = request.get("id")
        method = request.get("method")
        params = request.get("params", {})

        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "engramory-mcp", "version": "0.1.0"},
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
            tool_name = params.get("name", "")
            tool_args = params.get("arguments", {})
            call_result = await self.handle_call_tool(tool_name, tool_args)
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
