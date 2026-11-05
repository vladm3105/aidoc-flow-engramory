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
                    "enum": ["markdown", "json"],
                    "default": "json",
                    "description": "Output format ('markdown' with file links or 'json')",
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
        # In authenticated mode, tenant_id cannot be spoofed via client arguments (ADR-10)
        tenant_id = (
            base.tenant_id if self._auth_enabled else (arguments.get("tenant_id") or base.tenant_id)
        )
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
                hits = await self._surface.memory_search(
                    call_ctx,
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
                    call_ctx,
                    content=arguments["content"],
                    kind=arguments.get("kind", "note"),
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

            elif name == "agent_profile_get":
                profile = await self._surface.agent_profile_get(call_ctx)
                data = {
                    "agent_id": profile.agent_id,
                    "display_name": profile.display_name,
                    "standing_preferences": dict(profile.standing_preferences),
                }
                return {"content": [{"type": "text", "text": json.dumps(data, indent=2)}]}

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

            elif name == "graph_neighbors":
                neighbors = await self._surface.graph_neighbors(
                    call_ctx,
                    id=arguments["id"],
                    rel=arguments.get("rel"),
                    depth=arguments.get("depth", 1),
                )
                return {
                    "content": [{"type": "text", "text": json.dumps(list(neighbors), indent=2)}]
                }

            elif name == "sdd_context_slice":
                ancestors = await self._surface.context_slice(
                    call_ctx,
                    task_id=arguments["task_id"],
                    depth=arguments.get("depth", 6),
                    scope=arguments.get("scope", "project"),
                    layers=arguments.get("layers"),
                    max_tokens=arguments.get("max_tokens", 2000),
                )
                fmt = arguments.get("format", "json")
                if fmt == "markdown":
                    md_text = format_context_slice_markdown(arguments["task_id"], ancestors)
                    return {"content": [{"type": "text", "text": md_text}]}
                return {
                    "content": [{"type": "text", "text": json.dumps(list(ancestors), indent=2)}]
                }

            elif name == "sdd_blast_radius":
                descendants = await self._surface.blast_radius(
                    call_ctx,
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
                project_id = arguments.get("project_id", call_ctx.project_id)
                reconcile = arguments.get("reconcile", True)
                num_ent, num_rel = await self._ingest_fn(
                    sdd_root=sdd_root,
                    tenant_id=call_ctx.tenant_id,
                    project_id=project_id,
                    reconcile=reconcile,
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

    async def handle_request(
        self, request: dict[str, Any], ctx: ActorContext | None = None
    ) -> dict[str, Any] | None:
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
        server_instance = self

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
                else:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    resp = {"status": "ok", "service": "engramory-mcp", "version": "0.1.0"}
                    self.wfile.write(json.dumps(resp).encode("utf-8"))

            def do_POST(self) -> None:
                if self.path not in ("/messages", "/messages/"):
                    self.send_response(404)
                    self.end_headers()
                    return

                content_length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_length).decode("utf-8")
                try:
                    req = json.loads(body)
                    if server_instance._auth_enabled:
                        auth_hdr = self.headers.get("Authorization")
                        if not auth_hdr or not auth_hdr.strip().startswith("Bearer "):
                            self.send_response(401)
                            self.send_header("Content-Type", "application/json")
                            self.end_headers()
                            err = {
                                "jsonrpc": "2.0",
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

                    res = asyncio.run(server_instance.handle_request(req, ctx=req_ctx))
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
                        "error": {"code": -32001, "message": f"Unauthorized: {auth_exc}"},
                    }
                    self.wfile.write(json.dumps(err).encode("utf-8"))
                except Exception as exc:  # noqa: BLE001  # reason: HTTP server must return JSON-RPC error
                    self.send_response(500)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    err = {"jsonrpc": "2.0", "error": {"code": -32603, "message": str(exc)}}
                    self.wfile.write(json.dumps(err).encode("utf-8"))

        return http.server.ThreadingHTTPServer((host, port), MCPRequestHandler)

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
