"""The ``engramory`` CLI — the ADR-10 dev/CI face over AccessSurface (SPEC-07).

A thin binding: this module constructs ActorContext from local config, parses
arguments, and maps errors to exit codes — every memory/knowledge operation
goes through AccessSurface (authorize -> audit -> execute). ``memory distill``
and ``status`` are dev-tier ops commands (worker/count plumbing), not SPEC-01
agent tools.

Exit codes (SPEC-07, adopted from the interlog convention):
    0  success
    1  fail-closed reject (authz deny, governed-write reject, invalid input,
       unknown id)
    2  usage / config / environment (including the ADR-10 dev-tier fence)
    3  retryable — the canonical store is unreachable (StoreUnavailable)

TRUST (ADR-10 dev-tier carve-out): the identity in ``.engramory/config.toml``
is client-supplied and therefore trusted ONLY under the dev tier —
``require_dev_profile`` refuses anything else. Production access is
gateway-only with OIDC-verified claims.
"""

from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import logging
import os
import sys
import tomllib
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from engramory.access.authz import ActorContext
from engramory.access.poisoning_guard import MemoryPoisoningError
from engramory.access.surface import AccessSurface, AuthzError, GovernedWriteRejected
from engramory.adapters.factory import dev_adapters, require_dev_profile
from engramory.core.kb_graph import ingest_sdd_knowledge_graph, resolve_sdd_project_id
from engramory.core.models import ValidationError
from engramory.core.repository import Repository, StoreUnavailable
from engramory.workers.distillation import reflect
from engramory.workers.graph_projection import reproject_all
from engramory.workers.runner import start_daemon

CONFIG_DIRNAME = ".engramory"
CONFIG_FILENAME = "config.toml"
DEFAULT_SCOPES = ("agent", "project")


class ConfigError(RuntimeError):
    """Missing or malformed .engramory/config.toml (exit 2)."""


@dataclass(frozen=True, slots=True)
class CliConfig:
    """Parsed .engramory/config.toml — actor identity + store DSN."""

    ctx: ActorContext
    dsn: str
    path: Path


def _discover_config(start: Path | None = None) -> Path:
    current = (start or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        path = candidate / CONFIG_DIRNAME / CONFIG_FILENAME
        if path.exists():
            return path
    raise ConfigError(
        f"no {CONFIG_DIRNAME}/{CONFIG_FILENAME} found from {current} upward — "
        "run `engramory init` first"
    )


def _parse_config_text(raw_text: str, path: Path) -> CliConfig:
    try:
        raw = tomllib.loads(raw_text)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"unreadable config {path}: {exc}") from exc
    try:
        actor = raw["actor"]
        scopes = actor.get("scopes", list(DEFAULT_SCOPES))
        if not isinstance(scopes, list):  # a TOML string is iterable char-wise
            raise ConfigError(f"config {path}: actor.scopes must be a list")
        ctx = ActorContext(
            agent_id=actor["agent_id"],
            project_id=actor["project_id"],
            tenant_id=actor["tenant_id"],
            scopes=frozenset(scopes),
        )
        dsn = raw["store"]["dsn"]
    except (KeyError, TypeError) as exc:
        raise ConfigError(f"config {path} is missing required keys: {exc}") from exc
    return CliConfig(ctx=ctx, dsn=dsn, path=path)


def _load_config(explicit: str | None = None) -> CliConfig:
    if explicit:
        path = Path(explicit)
        try:
            return _parse_config_text(path.read_text(), path)
        except OSError as exc:
            raise ConfigError(f"unreadable config {path}: {exc}") from exc

    try:
        path = _discover_config()
        return _parse_config_text(path.read_text(), path)
    except ConfigError:
        dsn = os.getenv("ENGRAMORY_DATABASE_URL") or os.getenv("DATABASE_URL")
        if dsn:
            agent_id = os.getenv("ENGRAMORY_AGENT_ID", "default-agent")
            project_id = os.getenv("ENGRAMORY_PROJECT_ID", "default-project")
            tenant_id = os.getenv("ENGRAMORY_TENANT_ID", "default")
            scopes_raw = os.getenv("ENGRAMORY_SCOPES")
            scopes = scopes_raw.split(",") if scopes_raw else list(DEFAULT_SCOPES)
            ctx = ActorContext(
                agent_id=agent_id,
                project_id=project_id,
                tenant_id=tenant_id,
                scopes=frozenset(scopes),
            )
            return CliConfig(ctx=ctx, dsn=dsn, path=Path(".env"))
        raise


def _emit(as_json: bool, payload: dict[str, Any], human: str) -> None:
    print(json.dumps(payload, ensure_ascii=False) if as_json else human)


# ── commands ────────────────────────────────────────────────────────────────


def _upsert_profile_best_effort(agent_id: str, dsn: str, tenant_id: str = "default") -> str:
    """L3 profile row: init still succeeds when the store is down — the row
    lands on a later init run (every init retries, including re-runs on an
    existing config)."""
    from engramory.core.models import AgentProfile

    try:
        asyncio.run(
            Repository(dsn).upsert_agent_profile(
                AgentProfile(agent_id=agent_id, tenant_id=tenant_id)
            )
        )
    except StoreUnavailable as exc:
        print(f"engramory init: store unreachable, profile deferred: {exc}", file=sys.stderr)
        return "deferred"
    return "created"


def _cmd_init(args: argparse.Namespace) -> int:
    cfg_path = Path(CONFIG_DIRNAME) / CONFIG_FILENAME
    if cfg_path.exists():
        profile_state = _upsert_profile_best_effort(args.agent_id, args.dsn, args.tenant_id)
        _emit(
            args.json,
            {"created": False, "config": str(cfg_path), "profile": profile_state},
            f"exists: {cfg_path}",
        )
        return 0
    scopes = args.scopes.split(",") if args.scopes else list(DEFAULT_SCOPES)
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(
        "[actor]\n"
        f"agent_id = {json.dumps(args.agent_id)}\n"
        f"project_id = {json.dumps(args.project_id)}\n"
        f"tenant_id = {json.dumps(args.tenant_id)}\n"
        f"scopes = {json.dumps(scopes)}\n"
        "\n[store]\n"
        f"dsn = {json.dumps(args.dsn)}\n"
    )
    # The config carries a DSN (credentials): keep the whole directory out of git.
    gitignore = Path(".gitignore")
    entry = f"{CONFIG_DIRNAME}/"
    existing = gitignore.read_text() if gitignore.exists() else ""
    if entry not in existing:
        gitignore.write_text(existing.rstrip("\n") + ("\n" if existing else "") + entry + "\n")
    profile_state = _upsert_profile_best_effort(args.agent_id, args.dsn, args.tenant_id)
    _emit(
        args.json,
        {"created": True, "config": str(cfg_path), "profile": profile_state},
        f"created: {cfg_path}",
    )
    return 0


async def _close_adapters(adapters: Any) -> None:
    for attr in ("graph", "events", "storage"):
        adapter = getattr(adapters, attr, None)
        if adapter is not None and hasattr(adapter, "close"):
            try:
                res = adapter.close()
                if inspect.isawaitable(res):
                    await res
            except Exception as exc:  # noqa: BLE001  # reason: adapter cleanup must never mask command outcome
                logging.getLogger(__name__).debug("Adapter close error ignored: %s", exc)


def _run_with_surface(
    args: argparse.Namespace,
    op: Callable[[AccessSurface, ActorContext], Coroutine[Any, Any, dict[str, Any]]],
    human: Callable[[dict[str, Any]], str],
) -> int:
    config = _load_config(getattr(args, "config", None))
    adapters = dev_adapters(config.dsn)
    surface = AccessSurface(
        Repository(config.dsn),
        graph=adapters.graph,
        llm=getattr(adapters, "llm", None),
        storage=getattr(adapters, "storage", None),
    )

    async def _runner() -> dict[str, Any]:
        try:
            return await op(surface, config.ctx)
        finally:
            await _close_adapters(adapters)

    payload = asyncio.run(_runner())
    _emit(args.json, payload, human(payload))
    return 0


def _cmd_memory_add(args: argparse.Namespace) -> int:
    async def op(surface: AccessSurface, ctx: ActorContext) -> dict[str, Any]:
        extra_add_kwargs: dict[str, Any] = {}
        if getattr(args, "scope", "agent") != "agent":
            extra_add_kwargs["scope"] = args.scope
        if getattr(args, "domain_id", None):
            extra_add_kwargs["domain_id"] = args.domain_id
        episode_id = await surface.memory_add(
            ctx, content=args.content, kind=args.kind, **extra_add_kwargs
        )
        return {"episode_id": episode_id}

    return _run_with_surface(args, op, lambda p: f"episode {p['episode_id']}")


def _cmd_memory_search(args: argparse.Namespace) -> int:
    async def op(surface: AccessSurface, ctx: ActorContext) -> dict[str, Any]:
        extra_kwargs: dict[str, Any] = {}
        if getattr(args, "expand_graph", False):
            extra_kwargs["expand_graph"] = True
        if getattr(args, "include_knowledge", False):
            extra_kwargs["include_knowledge"] = True
        if getattr(args, "domain_id", None):
            extra_kwargs["domain_id"] = args.domain_id
        hits = await surface.memory_search(
            ctx, args.query, scope=args.scope, k=args.k, **extra_kwargs
        )
        return {
            "hits": [
                {
                    "memory_id": h.memory_id,
                    "summary": h.summary,
                    "score": h.score,
                    "retrieval_id": h.retrieval_id,
                    "token_count": h.token_count,
                    **(
                        {"provenance": dict(h.provenance)}
                        if (
                            getattr(args, "include_knowledge", False)
                            or getattr(args, "expand_graph", False)
                        )
                        else {}
                    ),
                }
                for h in hits
            ]
        }

    def human(payload: dict[str, Any]) -> str:
        hits = payload["hits"]
        if not hits:
            return "no hits"
        return "\n".join(f"{h['memory_id']}  {h['summary']}" for h in hits)

    return _run_with_surface(args, op, human)


def _cmd_memory_feedback(args: argparse.Namespace) -> int:
    async def op(surface: AccessSurface, ctx: ActorContext) -> dict[str, Any]:
        await surface.memory_feedback(ctx, args.retrieval_id, args.outcome)
        return {"ok": True}

    return _run_with_surface(args, op, lambda p: "recorded")


def _cmd_memory_forget(args: argparse.Namespace) -> int:
    async def op(surface: AccessSurface, ctx: ActorContext) -> dict[str, Any]:
        await surface.memory_forget(ctx, args.memory_id, reason=args.reason)
        return {"ok": True}

    return _run_with_surface(args, op, lambda p: "retired")


def _cmd_memory_distill(args: argparse.Namespace) -> int:
    """Dev-tier ops command (not a SPEC-01 tool): run the interim reflect pass."""
    config = _load_config(getattr(args, "config", None))
    repo = Repository(config.dsn)
    adapters = dev_adapters(config.dsn)

    async def _run_distill() -> int:
        try:
            return await reflect(
                repo,
                tenant_id=config.ctx.tenant_id,
                agent_id=config.ctx.agent_id,
                llm=adapters.llm,
                vector=adapters.vector,
            )
        finally:
            await _close_adapters(adapters)

    created = asyncio.run(_run_distill())
    _emit(args.json, {"created": created}, f"distilled {created} memorie(s)")
    return 0


def _cmd_knowledge_ingest(args: argparse.Namespace) -> int:
    if getattr(args, "text", None) is not None:

        async def op(surface: AccessSurface, ctx: ActorContext) -> dict[str, Any]:
            section_id = await surface.knowledge_ingest(
                ctx,
                doc_id=args.doc_id or "local",
                citation=args.citation or "§content",
                text=args.text,
                evidence_ref=args.evidence_ref,
                scope=args.scope,
                version=args.version,
            )
            return {"id": section_id, "doc_id": args.doc_id, "citation": args.citation}

        return _run_with_surface(
            args, op, lambda p: f"ingested section {p['id']} ({p['doc_id']} {p['citation']})"
        )

    if not getattr(args, "path", None):
        sys.stderr.write("knowledge ingest requires a path or --text\n")
        return 2

    config = _load_config(getattr(args, "config", None))

    target_path = Path(args.path)
    if not target_path.exists():
        sys.stderr.write(f"Path does not exist: {target_path}\n")
        return 2

    files_to_ingest: list[Path] = []
    if target_path.is_file():
        files_to_ingest.append(target_path)
    elif target_path.is_dir():
        glob_pat = "**/*" if getattr(args, "recursive", False) else "*"
        for f in sorted(target_path.glob(glob_pat)):
            if f.is_file() and not f.name.startswith("."):
                files_to_ingest.append(f)

    if not files_to_ingest:
        _emit(args.json, {"files": 0, "sections": 0}, "No files found to ingest")
        return 0

    adapters = dev_adapters(config.dsn)
    repo = Repository(config.dsn)

    from engramory.core.extraction.router import SmartExtractionRouter
    from engramory.core.knowledge import KnowledgeCore

    router = SmartExtractionRouter(enable_docling=bool(getattr(args, "docling", False)))
    core = KnowledgeCore(
        repo,
        llm=adapters.llm,
        storage=getattr(adapters, "storage", None),
        graph=adapters.graph,
        router=router,
    )

    async def run_batch() -> dict[str, Any]:
        try:
            results = await core.ingest_documents_batch(
                files_to_ingest,
                tenant_id=config.ctx.tenant_id,
                project_id=config.ctx.project_id,
                scope=getattr(args, "scope", "project"),
                evidence_ref=getattr(args, "evidence_ref", None),
                project_graph=True,
                repo_root=target_path if target_path.is_dir() else None,
            )
            total_sections = sum(len(ids) for ids in results.values())
            return {
                "files": len(results),
                "sections": total_sections,
                "details": results,
            }
        finally:
            await _close_adapters(adapters)

    payload = asyncio.run(run_batch())
    _emit(
        args.json,
        payload,
        f"ingested {payload['files']} file(s), {payload['sections']} section(s)",
    )
    return 0


def _cmd_knowledge_sync_git(args: argparse.Namespace) -> int:
    config = _load_config(getattr(args, "config", None))
    adapters = dev_adapters(config.dsn)
    repo = Repository(config.dsn)
    repo_path = Path(getattr(args, "repo_path", "."))
    base_sha = getattr(args, "base_sha", None)
    head_sha = getattr(args, "head_sha", None)

    from engramory.core.git_sync import GitSyncEngine

    engine = GitSyncEngine()

    async def run_sync() -> dict[str, Any]:
        try:
            sync_result = await engine.sync(
                repo,
                repo_path=repo_path,
                base_sha=base_sha,
                head_sha=head_sha,
                tenant_id=config.ctx.tenant_id,
                project_id=config.ctx.project_id,
                scope=getattr(args, "scope", "project"),
                llm=adapters.llm,
                storage=getattr(adapters, "storage", None),
                graph=adapters.graph,
            )
            return {
                "success": sync_result.success,
                "base_sha": sync_result.base_sha,
                "head_sha": sync_result.head_sha,
                "files_added": sync_result.files_added,
                "files_modified": sync_result.files_modified,
                "files_deleted": sync_result.files_deleted,
                "files_skipped": sync_result.files_skipped,
                "sections_created": sync_result.sections_created,
                "duration_ms": sync_result.duration_ms,
            }
        finally:
            await _close_adapters(adapters)

    payload = asyncio.run(run_sync())
    _emit(
        args.json,
        payload,
        f"git sync {'success' if payload['success'] else 'failed'}: "
        f"{payload['sections_created']} sections, {payload['files_added']} added, "
        f"{payload['files_modified']} modified, {payload['files_skipped']} skipped "
        f"({payload['duration_ms']}ms)",
    )
    return 0


def _cmd_knowledge_search(args: argparse.Namespace) -> int:
    config = _load_config(getattr(args, "config", None))
    adapters = dev_adapters(config.dsn)
    repo = Repository(config.dsn)
    from engramory.core.knowledge import KnowledgeCore

    core = KnowledgeCore(
        repo,
        llm=adapters.llm,
        storage=getattr(adapters, "storage", None),
        graph=adapters.graph,
    )
    modality = getattr(args, "modality", None)
    expand_graph = bool(getattr(args, "expand_graph", False))
    graph_depth = int(getattr(args, "graph_depth", 1))

    async def run_search() -> dict[str, Any]:
        try:
            if expand_graph:
                surface = AccessSurface(
                    repo,
                    graph=adapters.graph,
                    llm=adapters.llm,
                    storage=getattr(adapters, "storage", None),
                )
                sections = await surface.knowledge_search(
                    config.ctx,
                    args.query,
                    scope=getattr(args, "scope", None),
                    modality=modality,
                    expand_graph=True,
                    graph_depth=graph_depth,
                    limit=getattr(args, "limit", 5),
                )
            else:
                sections = await core.search_sections(
                    args.query,
                    tenant_id=config.ctx.tenant_id,
                    project_id=config.ctx.project_id,
                    scope=getattr(args, "scope", None),
                    modality=modality,
                    limit=getattr(args, "limit", 5),
                )
            return {
                "query": args.query,
                "count": len(sections),
                "results": [
                    {
                        "id": s.id,
                        "doc_id": s.doc_id,
                        "citation": s.citation,
                        "scope": s.scope,
                        "version": s.version,
                        "modality": s.modality,
                        "blob_sha256": s.blob_sha256,
                        "blob_uri": s.blob_uri,
                        "media_metadata": dict(s.media_metadata),
                        "text": s.text[:200] + ("..." if len(s.text) > 200 else ""),
                    }
                    for s in sections
                ],
            }
        finally:
            await _close_adapters(adapters)

    payload = asyncio.run(run_search())
    _emit(
        args.json,
        payload,
        f"found {payload['count']} section(s) for query: {args.query!r}",
    )
    return 0


def _cmd_knowledge_ingest_media(args: argparse.Namespace) -> int:
    async def op(surface: AccessSurface, ctx: ActorContext) -> dict[str, Any]:
        return await surface.knowledge_ingest_media(
            ctx,
            file_path=args.path,
            evidence_ref=args.evidence_ref,
            scope=getattr(args, "scope", "project"),
            embed=not bool(getattr(args, "no_embed", False)),
            project_graph=not bool(getattr(args, "no_graph", False)),
        )

    return _run_with_surface(
        args,
        op,
        lambda p: (
            f"ingested media {p['path']} (modality={p.get('modality')}, "
            f"sections={len(p.get('section_ids', []))}, blob={p.get('blob_uri')})"
        ),
    )


def _cmd_knowledge_blob_stat(args: argparse.Namespace) -> int:
    async def op(surface: AccessSurface, ctx: ActorContext) -> dict[str, Any]:
        res = await surface.blob_head(
            ctx,
            sha256=args.sha256,
            scope=getattr(args, "scope", "project"),
        )
        return {"found": res is not None, "blob": res}

    return _run_with_surface(
        args,
        op,
        lambda p: (
            f"blob {args.sha256}: status={p['blob']['status']} size={p['blob']['byte_size']} "
            f"tier={p['blob']['storage_tier']} chunks={p['blob']['chunk_count']}"
            if p["found"] and p["blob"]
            else f"blob {args.sha256}: not found"
        ),
    )


def _cmd_knowledge_blob_get(args: argparse.Namespace) -> int:
    from engramory.access.authz import TargetScope
    from engramory.core.docs_tools import resolve_confined_repo_path

    config = _load_config(getattr(args, "config", None))
    adapters = dev_adapters(config.dsn)
    repo = Repository(config.dsn)
    surface = AccessSurface(
        repo,
        graph=adapters.graph,
        llm=getattr(adapters, "llm", None),
        storage=getattr(adapters, "storage", None),
    )
    out_path = resolve_confined_repo_path(args.out)

    async def run_download() -> dict[str, Any]:
        try:
            await surface._decide(
                config.ctx,
                "blob_get",
                TargetScope(
                    tenant_id=config.ctx.tenant_id, scope=getattr(args, "scope", "project")
                ),
            )
            if adapters.storage is None:
                raise RuntimeError("StoragePort adapter is not configured")
            out_path.parent.mkdir(parents=True, exist_ok=True)
            total_written = 0
            with out_path.open("wb") as fh:
                async for chunk in adapters.storage.get_stream(
                    key=args.sha256,
                    tenant_id=config.ctx.tenant_id,
                ):
                    fh.write(chunk)
                    total_written += len(chunk)
            return {
                "sha256": args.sha256,
                "out": str(out_path),
                "bytes_written": total_written,
            }
        finally:
            await _close_adapters(adapters)

    payload = asyncio.run(run_download())
    _emit(
        args.json,
        payload,
        f"wrote {payload['bytes_written']} bytes to {payload['out']}",
    )
    return 0


def _cmd_worker_start(args: argparse.Namespace) -> int:
    config = _load_config(getattr(args, "config", None))
    adapters = dev_adapters(config.dsn)
    repo = Repository(config.dsn)
    tenant_id = os.getenv("ENGRAMORY_TENANT_ID")
    if not tenant_id and config.path != Path(".env"):
        tenant_id = config.ctx.tenant_id
    interval = args.interval or int(os.getenv("ENGRAMORY_REFLECT_INTERVAL_SEC", "300"))
    try:
        return start_daemon(
            repo,
            llm=adapters.llm,
            vector=adapters.vector,
            events=adapters.events,
            storage=getattr(adapters, "storage", None),
            interval_sec=interval,
            tenant_id=tenant_id,
        )
    finally:
        asyncio.run(_close_adapters(adapters))


def _cmd_profile_get(args: argparse.Namespace) -> int:
    async def op(surface: AccessSurface, ctx: ActorContext) -> dict[str, Any]:
        profile = await surface.agent_profile_get(ctx)
        return {
            "agent_id": profile.agent_id,
            "display_name": profile.display_name,
            "standing_preferences": dict(profile.standing_preferences),
        }

    return _run_with_surface(args, op, lambda p: f"{p['agent_id']} ({p['display_name']})")


def _cmd_profile_export_brain(args: argparse.Namespace) -> int:
    async def op(surface: AccessSurface, ctx: ActorContext) -> dict[str, Any]:
        bundle = await surface.agent_brain_export(
            ctx,
            domain_id=getattr(args, "domain_id", None),
            min_confidence=float(getattr(args, "min_confidence", 0.5)),
            redact_terms=list(getattr(args, "redact_term", None) or []),
        )
        out_file = getattr(args, "out", None)
        if out_file:
            out_path = Path(out_file)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json.dumps(bundle, indent=2), encoding="utf-8")
        return bundle

    return _run_with_surface(
        args,
        op,
        lambda p: f"exported {p['skills_exported']} portable skill(s) for {p['agent_id']}",
    )


def _cmd_profile_import_brain(args: argparse.Namespace) -> int:
    bundle_path = Path(args.file)
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))

    async def op(surface: AccessSurface, ctx: ActorContext) -> dict[str, Any]:
        return await surface.agent_brain_import(
            ctx,
            passport=bundle,
            evidence_ref=args.evidence_ref,
            target_domain_id=getattr(args, "domain_id", None),
        )

    return _run_with_surface(
        args,
        op,
        lambda p: (
            f"imported {p['skills_imported']} portable skill(s) into "
            f"{p['tenant_id']}/{p['agent_id']}"
        ),
    )


def _cmd_graph_reproject(args: argparse.Namespace) -> int:
    """Rebuild all graph projections from PostgreSQL into Neo4j (ADR-01/03/05)."""
    config = _load_config(getattr(args, "config", None))
    adapters = dev_adapters(config.dsn)
    if not adapters.graph:
        _emit(
            args.json,
            {"error": "no graph adapter configured"},
            "error: no graph adapter configured",
        )
        return 1

    repo = Repository(config.dsn)
    tenant_id = args.tenant_id or config.ctx.tenant_id

    async def run_reproject() -> dict[str, Any]:
        try:
            if adapters.graph is None:
                raise RuntimeError("Graph adapter is required for reproject")
            nodes, edges = await reproject_all(repo, adapters.graph, tenant_id=tenant_id)
            return {"status": "ok", "tenant_id": tenant_id, "nodes": nodes, "edges": edges}
        finally:
            await _close_adapters(adapters)

    payload = asyncio.run(run_reproject())
    msg = (
        f"reprojected graph for tenant '{tenant_id}': "
        f"{payload['nodes']} node(s), {payload['edges']} edge(s)"
    )
    _emit(args.json, payload, msg)
    return 0


def _cmd_sdd_ingest(args: argparse.Namespace) -> int:
    """Ingest SDD multi-layer corpus into PostgreSQL and project to Neo4j."""
    config = _load_config(getattr(args, "config", None))
    adapters = dev_adapters(config.dsn)
    repo = Repository(config.dsn)
    sdd_root = Path(args.sdd_root)
    tenant_id = args.tenant_id or config.ctx.tenant_id
    project_id = resolve_sdd_project_id(
        sdd_root,
        explicit_id=args.project_id,
        config_id=config.ctx.project_id,
    )
    reconcile = not getattr(args, "no_reconcile", False)

    async def run_ingest() -> dict[str, Any]:
        try:
            num_ent, num_rel = await ingest_sdd_knowledge_graph(
                repo,
                adapters.graph,
                sdd_root=sdd_root,
                tenant_id=tenant_id,
                project_id=project_id,
                reconcile=reconcile,
            )
            return {
                "status": "ok",
                "sdd_root": str(sdd_root),
                "tenant_id": tenant_id,
                "project_id": project_id,
                "reconcile": reconcile,
                "entities_ingested": num_ent,
                "relations_ingested": num_rel,
            }
        finally:
            await _close_adapters(adapters)

    payload = asyncio.run(run_ingest())
    msg = (
        f"ingested SDD corpus from '{sdd_root}': "
        f"{payload['entities_ingested']} entities, {payload['relations_ingested']} relations "
        f"(project '{project_id}', tenant '{tenant_id}')"
    )
    _emit(args.json, payload, msg)
    return 0


def _cmd_sdd_slice(args: argparse.Namespace) -> int:
    """Compute deterministic upstream ancestor closure for an IPLAN task."""
    scope = getattr(args, "scope", "project")
    raw_layers = getattr(args, "layers", None)
    layers = [x.strip() for x in raw_layers.split(",") if x.strip()] if raw_layers else None
    max_tokens = getattr(args, "max_tokens", 2000)
    as_markdown = getattr(args, "markdown", False)
    as_digest = getattr(args, "digest", False)
    offline = getattr(args, "offline", False)
    root_arg = getattr(args, "root", None)

    def format_slice(p: dict[str, Any]) -> str:
        if as_digest and "digest" in p:
            return str(p["digest"])
        ancestors = p["ancestors"]
        if as_markdown:
            from engramory.mcp.server import format_context_slice_markdown

            return format_context_slice_markdown(p["task_id"], ancestors)
        if not ancestors:
            return f"no upstream ancestors found for task {p['task_id']}"
        lines = [f"Context slice for {p['task_id']} ({len(ancestors)} upstream contracts):"]
        for a in ancestors:
            labels = ",".join(a.get("labels", []))
            title = a.get("properties", {}).get("name", "")
            lines.append(f"  - [{labels}] {a.get('id')}: {title}")
        return "\n".join(lines)

    if offline or root_arg is not None or as_digest:
        from engramory.core.context_tools import slice_local_context
        from engramory.core.docs_tools import resolve_confined_repo_path

        target_root = resolve_confined_repo_path(root_arg or ".")
        out_fmt = "digest" if as_digest else ("markdown" if as_markdown else "json")
        res = slice_local_context(
            target_root,
            task_id=args.task,
            depth=args.depth,
            layers=layers,
            max_tokens=max_tokens,
            output_format=out_fmt,
        )
        _emit(args.json, res, format_slice(res))
        return 0

    try:

        async def op(surface: AccessSurface, ctx: ActorContext) -> dict[str, Any]:
            ancestors = await surface.context_slice(
                ctx,
                task_id=args.task,
                depth=args.depth,
                scope=scope,
                layers=layers,
                max_tokens=max_tokens,
            )
            if not ancestors:
                from engramory.core.context_tools import slice_local_context
                from engramory.core.docs_tools import resolve_confined_repo_path

                target_root = resolve_confined_repo_path(".")
                local_res = slice_local_context(
                    target_root,
                    task_id=args.task,
                    depth=args.depth,
                    layers=layers,
                    max_tokens=max_tokens,
                    output_format="markdown" if as_markdown else "json",
                )
                if local_res["ancestors"]:
                    return local_res
            return {
                "task_id": args.task,
                "depth": args.depth,
                "ancestor_count": len(ancestors),
                "ancestors": list(ancestors),
            }

        return _run_with_surface(args, op, format_slice)
    except (ConfigError, StoreUnavailable):
        from engramory.core.context_tools import slice_local_context
        from engramory.core.docs_tools import resolve_confined_repo_path

        target_root = resolve_confined_repo_path(".")
        res = slice_local_context(
            target_root,
            task_id=args.task,
            depth=args.depth,
            layers=layers,
            max_tokens=max_tokens,
            output_format="markdown" if as_markdown else "json",
        )
        _emit(args.json, res, format_slice(res))
        return 0


def _cmd_sdd_blast_radius(args: argparse.Namespace) -> int:
    """Compute downstream descendant impact of modifying a document."""
    scope = getattr(args, "scope", "project")
    offline = getattr(args, "offline", False)
    root_arg = getattr(args, "root", None)

    def format_blast(p: dict[str, Any]) -> str:
        descendants = p["descendants"]
        if not descendants:
            return f"no downstream dependents found for document {p['doc_id']}"
        lines = [
            f"Blast radius for {p['doc_id']} ({len(descendants)} impacted downstream artifacts):"
        ]
        for d in descendants:
            labels = ",".join(d.get("labels", []))
            title = d.get("properties", {}).get("name", "")
            lines.append(f"  - [{labels}] {d.get('id')}: {title}")
        return "\n".join(lines)

    if offline or root_arg is not None:
        from engramory.core.context_tools import local_blast_radius
        from engramory.core.docs_tools import resolve_confined_repo_path

        target_root = resolve_confined_repo_path(root_arg or ".")
        res = local_blast_radius(target_root, doc_id=args.doc, depth=args.depth)
        _emit(args.json, res, format_blast(res))
        return 0

    try:

        async def op(surface: AccessSurface, ctx: ActorContext) -> dict[str, Any]:
            descendants = await surface.blast_radius(
                ctx,
                doc_id=args.doc,
                depth=args.depth,
                scope=scope,
            )
            if not descendants:
                from engramory.core.context_tools import local_blast_radius
                from engramory.core.docs_tools import resolve_confined_repo_path

                target_root = resolve_confined_repo_path(".")
                local_res = local_blast_radius(target_root, doc_id=args.doc, depth=args.depth)
                if local_res["descendants"]:
                    return local_res
            return {
                "doc_id": args.doc,
                "depth": args.depth,
                "impact_count": len(descendants),
                "descendants": list(descendants),
            }

        return _run_with_surface(args, op, format_blast)
    except (ConfigError, StoreUnavailable):
        from engramory.core.context_tools import local_blast_radius
        from engramory.core.docs_tools import resolve_confined_repo_path

        target_root = resolve_confined_repo_path(".")
        res = local_blast_radius(target_root, doc_id=args.doc, depth=args.depth)
        _emit(args.json, res, format_blast(res))
        return 0


def _cmd_sdd_validate_graph(args: argparse.Namespace) -> int:
    """Validate SDD dependency graph acyclicity and semantic cardinality invariants."""
    from engramory.core.context_tools import validate_sdd_graph
    from engramory.core.docs_tools import resolve_confined_repo_path

    target = resolve_confined_repo_path(getattr(args, "root", "."))
    report = validate_sdd_graph(target, check_invariants=bool(getattr(args, "invariants", False)))
    summary = (
        f"status={report['status']} nodes={report['nodes_scanned']} "
        f"cycles={len(report['cycles'])} violations={len(report['violations'])}"
    )
    _emit(args.json, report, summary)
    return 0 if report["passed"] else 1


def _cmd_sdd_check_invariants(args: argparse.Namespace) -> int:
    """Run pre-merge contract erosion guard and optional downstream blast-radius advisories."""
    from engramory.core.context_tools import check_sdd_invariants
    from engramory.core.docs_tools import resolve_confined_repo_path

    target = resolve_confined_repo_path(getattr(args, "root", "."))
    include_warnings = not bool(getattr(args, "offline", False))
    report = check_sdd_invariants(
        target,
        base_ref=getattr(args, "base", "origin/dev"),
        chg_type=getattr(args, "type", "CODE2C"),
        include_blast_warnings=include_warnings,
    )
    summary = (
        f"status={report['status']} changed={report['changed_files_count']} "
        f"violations={len(report['violations'])} warnings={len(report['warnings'])}"
    )
    _emit(args.json, report, summary)
    return 0 if report["passed"] else 1


def _cmd_sdd_lint_policy(args: argparse.Namespace) -> int:
    """Validate repository source tree and lockfiles against .aidoc/stack-policy.yaml."""
    from engramory.core.context_tools import lint_stack_policy
    from engramory.core.docs_tools import resolve_confined_repo_path

    target = resolve_confined_repo_path(getattr(args, "root", "."))
    report = lint_stack_policy(target, policy_path=getattr(args, "policy", None))
    summary = f"status={report['status']} violations={len(report['violations'])}"
    _emit(args.json, report, summary)
    return 0 if report["passed"] else 1


def _cmd_mcp_serve(args: argparse.Namespace) -> int:
    """Run the MCP Gateway JSON-RPC server over stdio or SSE (SPEC-01 / ADR-10)."""
    from engramory.mcp.server import McpServer

    config = _load_config(getattr(args, "config", None))
    adapters = dev_adapters(config.dsn)
    repo = Repository(config.dsn)
    surface = AccessSurface(
        repo,
        graph=adapters.graph,
        llm=getattr(adapters, "llm", None),
        storage=getattr(adapters, "storage", None),
    )

    async def do_ingest(
        sdd_root: str, tenant_id: str, project_id: str, reconcile: bool = True
    ) -> tuple[int, int]:
        return await ingest_sdd_knowledge_graph(
            repo,
            adapters.graph,
            sdd_root=sdd_root,
            tenant_id=tenant_id,
            project_id=project_id,
            reconcile=reconcile,
        )

    auth_env = os.environ.get("AUTHENTIK_ENABLED", "").lower() in ("1", "true", "yes")
    auth_enabled = bool(getattr(args, "auth", False) or auth_env)

    token_validator = None
    security_gate_url = os.environ.get("SECURITY_GATE_URL")
    if auth_enabled:
        from engramory.access.jwt_auth import JWKSValidator

        jwks_url = os.environ.get(
            "AUTHENTIK_JWKS_URL",
            "https://auth-stage.aiops-flow.io/application/o/engramory/jwks/",
        )
        issuer = os.environ.get(
            "AUTHENTIK_ISSUER",
            "https://auth-stage.aiops-flow.io/application/o/engramory/",
        )
        audience = os.environ.get("AUTHENTIK_AUDIENCE", "engramory")
        token_validator = JWKSValidator(jwks_url, issuer=issuer, audience=audience)

    server = McpServer(
        surface,
        config.ctx,
        ingest_fn=do_ingest,
        token_validator=token_validator,
        auth_enabled=auth_enabled,
        security_gate_url=security_gate_url,
    )
    transport = getattr(args, "transport", "stdio")
    try:
        if transport == "sse":
            host = getattr(args, "host", "127.0.0.1")
            port = getattr(args, "port", 8000)
            server.run_sse(host=host, port=port)
        else:
            asyncio.run(server.run_stdio())
    finally:
        asyncio.run(_close_adapters(adapters))
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    """Dev-tier ops command: store reachability + tenant counts."""
    config = _load_config(getattr(args, "config", None))
    repo = Repository(config.dsn)

    async def counts() -> dict[str, Any]:
        return {
            "store": "ok",
            "episodes": await repo.count_episodes(tenant_id=config.ctx.tenant_id),
            "kb_sections": await repo.count_kb_sections(tenant_id=config.ctx.tenant_id),
            "graph_entities": await repo.count_graph_entities(tenant_id=config.ctx.tenant_id),
            "graph_relations": await repo.count_graph_relations(tenant_id=config.ctx.tenant_id),
        }

    payload = asyncio.run(counts())
    _emit(
        args.json,
        payload,
        f"store ok — {payload['episodes']} episode(s), {payload['kb_sections']} kb section(s), "
        f"{payload['graph_entities']} entity(ies), {payload['graph_relations']} relation(s)",
    )
    return 0


# ── parser + entry point ────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="engramory",
        description="Agent memory plane — dev/CI face over AccessSurface (ADR-10).",
    )
    parser.add_argument(
        "--json", action="store_true", help="one machine-readable JSON object on stdout"
    )
    parser.add_argument(
        "--config",
        help="explicit config path "
        f"(default: {CONFIG_DIRNAME}/{CONFIG_FILENAME} discovered upward)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="scaffold .engramory/config.toml + L3 profile")
    p_init.add_argument("--agent-id", required=True)
    p_init.add_argument("--project-id", required=True)
    p_init.add_argument("--tenant-id", required=True)
    p_init.add_argument("--dsn", required=True)
    p_init.add_argument("--scopes", help="comma-separated (default: agent,project)")
    p_init.set_defaults(func=_cmd_init)

    p_memory = sub.add_parser("memory", help="SPEC-01 memory tools")
    mem_sub = p_memory.add_subparsers(dest="memory_command", required=True)

    p_add = mem_sub.add_parser("add", help="append an episode (idempotent)")
    p_add.add_argument("--content", required=True)
    p_add.add_argument("--kind", default="note")
    p_add.add_argument(
        "--scope",
        default="agent",
        choices=["agent", "project", "domain", "space"],
        help="target memory scope (agent, project, domain, space)",
    )
    p_add.add_argument("--domain-id", default=None, help="optional department/domain focus id")
    p_add.set_defaults(func=_cmd_memory_add)

    p_search = mem_sub.add_parser("search", help="scoped retrieval")
    p_search.add_argument("--query", required=True)
    p_search.add_argument("--scope", default="agent")
    p_search.add_argument("--domain-id", default=None, help="optional department/domain focus id")
    p_search.add_argument("-k", type=int, default=8)
    p_search.add_argument(
        "--include-knowledge",
        action="store_true",
        help="fuse matching L0 extracted knowledge sections alongside L2 memories",
    )
    p_search.add_argument(
        "--expand-graph",
        action="store_true",
        help="attach connected GraphRAG neighborhood context",
    )
    p_search.set_defaults(func=_cmd_memory_search)

    p_fb = mem_sub.add_parser("feedback", help="record a retrieval outcome")
    p_fb.add_argument("--retrieval-id", required=True)
    p_fb.add_argument("--outcome", required=True, help="useful | not_useful | harmful")
    p_fb.set_defaults(func=_cmd_memory_feedback)

    p_forget = mem_sub.add_parser("forget", help="retire a memory (soft, audited)")
    p_forget.add_argument("--memory-id", required=True)
    p_forget.add_argument("--reason", required=True)
    p_forget.set_defaults(func=_cmd_memory_forget)

    p_distill = mem_sub.add_parser(
        "distill", help="ops: interim reflect pass (episodes -> memories)"
    )
    p_distill.set_defaults(func=_cmd_memory_distill)

    p_ingest_ev = mem_sub.add_parser(
        "ingest-event",
        help="ingest a canonical ExperienceEvent (deterministic UUIDv5 + OriginProvenance)",
    )
    p_ingest_ev.add_argument("--event-json", default=None, help="inline JSON ExperienceEvent")
    p_ingest_ev.add_argument("--event-file", default=None, help="path to JSON ExperienceEvent file")
    p_ingest_ev.add_argument(
        "--publish-nats",
        action="store_true",
        help="also publish the validated event to EventsPort (engramory.events.<kind>)",
    )
    p_ingest_ev.set_defaults(func=_cmd_memory_ingest_event)

    p_learn_health = mem_sub.add_parser(
        "learning-health",
        help="compute closed-loop learning calibration and repeat-findings metrics",
    )
    p_learn_health.add_argument("--project-id", default=None)
    p_learn_health.add_argument("--limit", type=int, default=500)
    p_learn_health.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    p_learn_health.set_defaults(func=_cmd_memory_learning_health)

    p_profile = sub.add_parser("profile", help="L3 identity and portable virtual employee brain")
    prof_sub = p_profile.add_subparsers(dest="profile_command", required=True)
    p_pget = prof_sub.add_parser("get", help="load the caller's own profile")
    p_pget.set_defaults(func=_cmd_profile_get)

    p_pexport = prof_sub.add_parser(
        "export-brain",
        help="export sanitized portable L3 profile + L2 agent-scoped skills for reassignment",
    )
    p_pexport.add_argument("--domain-id", default=None, help="optional domain/department filter")
    p_pexport.add_argument(
        "--min-confidence", type=float, default=0.5, help="minimum L2 skill confidence"
    )
    p_pexport.add_argument(
        "--redact-term",
        action="append",
        default=[],
        help="additional tenant/customer terms to scrub (repeatable)",
    )
    p_pexport.add_argument("--out", default=None, help="optional path to write JSON bundle")
    p_pexport.set_defaults(func=_cmd_profile_export_brain)

    p_pimport = prof_sub.add_parser(
        "import-brain",
        help="import a sanitized portable virtual employee brain passport into the target tenant",
    )
    p_pimport.add_argument("--file", required=True, help="path to exported JSON brain bundle")
    p_pimport.add_argument(
        "--evidence-ref", required=True, help="governed CHG/TICKET/STEWARD evidence reference"
    )
    p_pimport.add_argument(
        "--domain-id", default=None, help="optional target domain/department override"
    )
    p_pimport.set_defaults(func=_cmd_profile_import_brain)

    p_knowledge = sub.add_parser("knowledge", aliases=["kb"], help="L0 knowledge tools")
    kb_sub = p_knowledge.add_subparsers(dest="knowledge_command", required=True)
    p_kb_ingest = kb_sub.add_parser("ingest", help="governed knowledge section/file ingest")
    p_kb_ingest.add_argument("path", nargs="?", default=None, help="path to file or directory")
    p_kb_ingest.add_argument("--doc-id", default=None)
    p_kb_ingest.add_argument("--citation", default=None)
    p_kb_ingest.add_argument("--text", default=None)
    p_kb_ingest.add_argument("--evidence-ref", default=None)
    p_kb_ingest.add_argument("--scope", default="project")
    p_kb_ingest.add_argument("--version", type=int, default=1)
    p_kb_ingest.add_argument(
        "--recursive", "-r", action="store_true", help="recursively ingest directory"
    )
    p_kb_ingest.add_argument("--docling", action="store_true", help="enable Docling extraction")
    p_kb_ingest.set_defaults(func=_cmd_knowledge_ingest)

    p_kb_media = kb_sub.add_parser(
        "ingest-media", help="governed multimodal file ingest into CAS + kb_sections + graph"
    )
    p_kb_media.add_argument("path", help="path to media file (image, audio, video, pdf)")
    p_kb_media.add_argument(
        "--evidence-ref", required=True, help="approved evidence reference (ADR-06)"
    )
    p_kb_media.add_argument("--scope", default="project")
    p_kb_media.add_argument("--no-embed", action="store_true", help="skip vector embedding")
    p_kb_media.add_argument("--no-graph", action="store_true", help="skip graph projection")
    p_kb_media.set_defaults(func=_cmd_knowledge_ingest_media)

    p_kb_bstat = kb_sub.add_parser("blob-stat", help="inspect PostgreSQL CAS blob manifest")
    p_kb_bstat.add_argument("sha256", help="64-character hex SHA-256 content address")
    p_kb_bstat.add_argument("--scope", default="project")
    p_kb_bstat.set_defaults(func=_cmd_knowledge_blob_stat)

    p_kb_bget = kb_sub.add_parser("blob-get", help="stream PostgreSQL CAS blob to local file")
    p_kb_bget.add_argument("sha256", help="64-character hex SHA-256 content address")
    p_kb_bget.add_argument("--out", required=True, help="confined output file path")
    p_kb_bget.add_argument("--scope", default="project")
    p_kb_bget.set_defaults(func=_cmd_knowledge_blob_get)

    p_kb_sync = kb_sub.add_parser("sync-git", help="commit-differential incremental git sync")
    p_kb_sync.add_argument("--repo-path", default=".", help="path to git repository")
    p_kb_sync.add_argument("--base-sha", default=None, help="base commit SHA for diff")
    p_kb_sync.add_argument("--head-sha", default=None, help="head commit SHA for diff")
    p_kb_sync.add_argument("--scope", default="project")
    p_kb_sync.set_defaults(func=_cmd_knowledge_sync_git)

    p_kb_search = kb_sub.add_parser("search", help="search knowledge sections")
    p_kb_search.add_argument("query", help="query string")
    p_kb_search.add_argument("--limit", type=int, default=5, help="max results to return")
    p_kb_search.add_argument("--scope", default=None, help="filter by scope")
    p_kb_search.add_argument(
        "--modality",
        default=None,
        choices=["text", "markdown", "pdf", "document", "image", "audio", "video"],
        help="filter by modality",
    )
    p_kb_search.add_argument(
        "--expand-graph",
        action="store_true",
        help="attach connected GraphRAG neighborhood context",
    )
    p_kb_search.add_argument(
        "--graph-depth",
        type=int,
        default=1,
        help="GraphRAG traversal depth (1..6)",
    )
    p_kb_search.set_defaults(func=_cmd_knowledge_search)

    p_worker = sub.add_parser("worker", help="background worker daemon")
    w_sub = p_worker.add_subparsers(dest="worker_command", required=True)
    p_wstart = w_sub.add_parser("start", help="start the reflection background runner")
    p_wstart.add_argument("--interval", type=int, default=None, help="reflection interval seconds")
    p_wstart.set_defaults(func=_cmd_worker_start)

    p_graph = sub.add_parser("graph", help="L0/L1 graph tools")
    g_sub = p_graph.add_subparsers(dest="graph_command", required=True)
    p_greproject = g_sub.add_parser(
        "reproject", help="rebuild graph projections from PostgreSQL into Neo4j"
    )
    p_greproject.add_argument(
        "--tenant-id", default=None, help="target tenant (default: from config)"
    )
    p_greproject.set_defaults(func=_cmd_graph_reproject)

    p_sdd = sub.add_parser("sdd", help="SDD knowledge graph & context slicing tools")
    sdd_sub = p_sdd.add_subparsers(dest="sdd_command", required=True)

    p_sdd_ingest = sdd_sub.add_parser(
        "ingest", help="ingest multi-layer SDD corpus into Postgres and Neo4j"
    )
    p_sdd_ingest.add_argument(
        "--sdd-root", default="sdd", help="root directory of SDD layers (default: sdd)"
    )
    p_sdd_ingest.add_argument(
        "--project-id", default=None, help="target project id (default: from config)"
    )
    p_sdd_ingest.add_argument(
        "--tenant-id", default=None, help="target tenant id (default: from config)"
    )
    p_sdd_ingest.add_argument(
        "--no-reconcile",
        action="store_true",
        help="disable automatic pruning of stale deleted/renamed entities",
    )
    p_sdd_ingest.set_defaults(func=_cmd_sdd_ingest)

    p_sdd_slice = sdd_sub.add_parser(
        "slice", help="compute deterministic upstream ancestor context slice for a task"
    )
    p_sdd_slice.add_argument("--task", required=True, help="task id (e.g. IPLAN-03)")
    p_sdd_slice.add_argument(
        "--depth", type=int, default=6, help="maximum traversal depth (default: 6)"
    )
    p_sdd_slice.add_argument(
        "--scope", default="project", help="visibility scope (default: project)"
    )
    p_sdd_slice.add_argument(
        "--layers",
        default=None,
        help="comma-separated list of SDD layers to include (e.g. SPEC,ADR,EARS)",
    )
    p_sdd_slice.add_argument(
        "--max-tokens",
        type=int,
        default=2000,
        help="maximum token budget for the slice (default: 2000)",
    )
    p_sdd_slice.add_argument(
        "--markdown",
        action="store_true",
        help="format output as structured markdown with links",
    )
    p_sdd_slice.add_argument(
        "--digest",
        action="store_true",
        help="format output as a Tier 0/1/2 token-compacted prompt digest",
    )
    p_sdd_slice.add_argument(
        "--offline",
        action="store_true",
        help="force local workspace DAG traversal without querying Postgres/Neo4j",
    )
    p_sdd_slice.add_argument(
        "--root",
        default=None,
        help="local workspace root directory for offline/hybrid DAG slicing",
    )
    p_sdd_slice.set_defaults(func=_cmd_sdd_slice)

    p_sdd_blast = sdd_sub.add_parser(
        "blast-radius", help="compute downstream blast radius impact of a document change"
    )
    p_sdd_blast.add_argument("--doc", required=True, help="document id (e.g. ADR-03 or SPEC-03)")
    p_sdd_blast.add_argument(
        "--depth", type=int, default=6, help="maximum traversal depth (default: 6)"
    )
    p_sdd_blast.add_argument(
        "--scope", default="project", help="visibility scope (default: project)"
    )
    p_sdd_blast.add_argument(
        "--offline",
        action="store_true",
        help="force local workspace DAG traversal without querying Postgres/Neo4j",
    )
    p_sdd_blast.add_argument(
        "--root",
        default=None,
        help="local workspace root directory for offline/hybrid blast radius",
    )
    p_sdd_blast.set_defaults(func=_cmd_sdd_blast_radius)

    p_sdd_val = sdd_sub.add_parser(
        "validate-graph", help="validate SDD DAG acyclicity and semantic cardinality invariants"
    )
    p_sdd_val.add_argument("--root", default=".", help="workspace root directory (default: .)")
    p_sdd_val.add_argument(
        "--invariants",
        action="store_true",
        help="enforce SDD-GRAPH v2.0 semantic cardinality and oracle trace rules",
    )
    p_sdd_val.set_defaults(func=_cmd_sdd_validate_graph)

    p_sdd_inv = sdd_sub.add_parser(
        "check-invariants", help="pre-merge contract erosion guard and blast-radius advisories"
    )
    p_sdd_inv.add_argument("--root", default=".", help="workspace root directory (default: .)")
    p_sdd_inv.add_argument("--base", default="origin/dev", help="base git reference")
    p_sdd_inv.add_argument(
        "--type", default="CODE2C", help="change classification (e.g. CODE2C, DIR2C, SEED2C)"
    )
    p_sdd_inv.add_argument(
        "--offline", action="store_true", help="skip blast-radius advisory check"
    )
    p_sdd_inv.set_defaults(func=_cmd_sdd_check_invariants)

    p_sdd_pol = sdd_sub.add_parser(
        "lint-policy", help="validate codebase and lockfiles against .aidoc/stack-policy.yaml"
    )
    p_sdd_pol.add_argument("--root", default=".", help="workspace root directory (default: .)")
    p_sdd_pol.add_argument(
        "--policy",
        default=None,
        help="path to stack-policy.yaml (default: .aidoc/stack-policy.yaml)",
    )
    p_sdd_pol.set_defaults(func=_cmd_sdd_lint_policy)

    p_mcp = sub.add_parser("mcp", help="run MCP gateway server (stdio or SSE)")
    p_mcp.add_argument(
        "--transport",
        choices=["stdio", "sse"],
        default="stdio",
        help="MCP transport protocol (default: stdio)",
    )
    p_mcp.add_argument("--host", default="127.0.0.1", help="SSE host to bind (default: 127.0.0.1)")
    p_mcp.add_argument(
        "--port", type=int, default=8000, help="SSE port to listen on (default: 8000)"
    )
    p_mcp.add_argument(
        "--auth", action="store_true", help="enable Authentik OIDC bearer token verification"
    )
    p_mcp.set_defaults(func=_cmd_mcp_serve)

    p_status = sub.add_parser("status", help="ops: store reachability + counts")
    p_status.set_defaults(func=_cmd_status)
    _register_docs_subcommands(sub)
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = _build_parser().parse_args(argv)
    except SystemExit as exc:  # argparse: 0 for --help, 2 for usage errors
        return 0 if exc.code in (0, None) else 2
    try:
        require_dev_profile("cli")  # ADR-10 dev-tier fence — before any identity use
        return int(args.func(args))
    except ConfigError as exc:
        print(f"engramory: {exc}", file=sys.stderr)
        return 2
    # AuthzError/GovernedWriteRejected/MemoryPoisoningError MUST
    # be matched before the fence clause or every deny would exit 2, not 1.
    except (AuthzError, GovernedWriteRejected, MemoryPoisoningError) as exc:
        print(f"engramory: denied: {exc}", file=sys.stderr)
        return 1
    except PermissionError as exc:  # the ADR-10 dev-tier fence
        print(f"engramory: {exc}", file=sys.stderr)
        return 2
    except (ValidationError, ValueError) as exc:
        print(f"engramory: rejected: {exc}", file=sys.stderr)
        return 1
    except KeyError as exc:
        print(f"engramory: not found: {exc}", file=sys.stderr)
        return 1
    except StoreUnavailable as exc:
        print(f"engramory: store unavailable (retryable): {exc}", file=sys.stderr)
        return 3


def _register_docs_subcommands(sub: Any) -> None:
    p_docs = sub.add_parser("docs", help="AI-first documentation, runbook & drift tools")
    docs_sub = p_docs.add_subparsers(dest="docs_command", required=True)

    p_lint = docs_sub.add_parser(
        "lint", help="validate frontmatter, executable code blocks & prose"
    )
    p_lint.add_argument("path", nargs="?", default=".", help="file or directory to lint")
    p_lint.add_argument(
        "--repo-path",
        "--target",
        dest="repo_path",
        default=None,
        help="target file or repository path",
    )
    p_lint.add_argument("--strict", action="store_true", help="treat warnings as errors")
    p_lint.add_argument(
        "--require-frontmatter",
        action="store_true",
        help="require deterministic YAML frontmatter on all inspected Markdown files",
    )
    p_lint.add_argument(
        "--verify-registry",
        action="store_true",
        help="validate frontmatter endpoints and triggers structure (DOC-REG-001)",
    )
    p_lint.add_argument(
        "--json", action="store_true", default=argparse.SUPPRESS, help="emit JSON output"
    )
    p_lint.set_defaults(func=_cmd_docs_lint)

    p_drift = docs_sub.add_parser(
        "check-drift", help="detect watched_paths git drift & TTL staleness"
    )
    p_drift.add_argument("path", nargs="?", default=".", help="repository path (default: .)")
    p_drift.add_argument(
        "--repo-path",
        "--target",
        dest="repo_path",
        default=None,
        help="repository path override",
    )
    p_drift.add_argument(
        "--base-ref",
        "--base",
        dest="base_ref",
        default="HEAD~1",
        help="git base ref (default: HEAD~1)",
    )
    p_drift.add_argument(
        "--head-ref",
        "--head",
        dest="head_ref",
        default="HEAD",
        help="git head ref (default: HEAD)",
    )
    p_drift.add_argument(
        "--json", action="store_true", default=argparse.SUPPRESS, help="emit JSON output"
    )
    p_drift.set_defaults(func=_cmd_docs_check_drift)

    p_llms = docs_sub.add_parser(
        "export-llms", help="generate deterministic /llms.txt and /llms-full.txt"
    )
    p_llms.add_argument("path", nargs="?", default=".", help="repository path (default: .)")
    p_llms.add_argument(
        "--repo-path",
        "--target",
        dest="repo_path",
        default=None,
        help="repository path override",
    )
    p_llms.add_argument(
        "--write", action="store_true", help="write llms.txt (and llms-full.txt) to disk"
    )
    p_llms.add_argument(
        "--full", action="store_true", help="include full markdown corpus in output"
    )
    p_llms.add_argument(
        "--json", action="store_true", default=argparse.SUPPRESS, help="emit JSON output"
    )
    p_llms.set_defaults(func=_cmd_docs_export_llms)

    p_slice = docs_sub.add_parser(
        "slice-runbook", help="slice executable runbook steps by doc_id or alert_id"
    )
    p_slice.add_argument(
        "--repo-path",
        "--target",
        dest="repo_path",
        default=None,
        help="optional local repository path for offline slicing",
    )
    p_slice.add_argument("--doc-id", default=None, help="filter by runbook document id")
    p_slice.add_argument(
        "--alert-id", default=None, help="filter by trigger alert id (e.g. ALT-DOC-004)"
    )
    p_slice.add_argument("--max-tier", choices=["green", "yellow", "red"], default=None)
    p_slice.add_argument(
        "--token-budget", type=int, default=2000, help="token budget (default: 2000)"
    )
    p_slice.add_argument("--format", choices=["json", "markdown"], default="json")
    p_slice.add_argument(
        "--json", action="store_true", default=argparse.SUPPRESS, help="emit JSON output"
    )
    p_slice.set_defaults(func=_cmd_docs_slice_runbook)

    p_smoke = docs_sub.add_parser(
        "smoke-run", help="verify and run tier='green' read-only runbook probes"
    )
    p_smoke.add_argument(
        "path", nargs="?", default=".", help="repository or file path (default: .)"
    )
    p_smoke.add_argument(
        "--repo-path",
        "--target",
        dest="repo_path",
        default=None,
        help="repository or file path override",
    )
    p_smoke.add_argument("--doc-id", default=None, help="optional document id filter")
    p_smoke.add_argument(
        "--dry-run", action="store_true", help="run bash -n syntax check without executing"
    )
    p_smoke.add_argument(
        "--json", action="store_true", default=argparse.SUPPRESS, help="emit JSON output"
    )
    p_smoke.set_defaults(func=_cmd_docs_smoke_run)

    p_ingest = docs_sub.add_parser(
        "ingest", help="ingest governed docs and runbook steps into Postgres"
    )
    p_ingest.add_argument(
        "path", nargs="?", default=".", help="repository or file path (default: .)"
    )
    p_ingest.add_argument(
        "--repo-path",
        "--target",
        dest="repo_path",
        default=None,
        help="repository or file path override",
    )
    p_ingest.add_argument("--project-id", default=None, help="override project id")
    p_ingest.add_argument(
        "--json", action="store_true", default=argparse.SUPPRESS, help="emit JSON output"
    )
    p_ingest.set_defaults(func=_cmd_docs_ingest)

    for cmd_name in ("tools", "tool-inventory"):
        p_tools = docs_sub.add_parser(
            cmd_name, help="list the governed documentation tool inventory"
        )
        p_tools.add_argument(
            "--tier",
            choices=["Adopt", "Trial", "Hold"],
            default=None,
            help="filter tool inventory by governance tier",
        )
        p_tools.add_argument(
            "--json", action="store_true", default=argparse.SUPPRESS, help="emit JSON output"
        )
        p_tools.set_defaults(func=_cmd_docs_tools)

    p_portal = docs_sub.add_parser(
        "build-portal",
        help="stage governed docs/*.md into Astro Starlight portal with D14 redaction",
    )
    p_portal.add_argument("path", nargs="?", default=".", help="repository path (default: .)")
    p_portal.add_argument(
        "--repo-path",
        "--target",
        dest="repo_path",
        default=None,
        help="repository path override",
    )
    p_portal.add_argument(
        "--output-dir",
        default=None,
        help="custom output directory for staged Starlight Markdown",
    )
    p_portal.add_argument(
        "--dry-run",
        action="store_true",
        help="validate and count staged files without writing to disk",
    )
    p_portal.add_argument(
        "--json", action="store_true", default=argparse.SUPPRESS, help="emit JSON output"
    )
    p_portal.set_defaults(func=_cmd_docs_build_portal)


def _cmd_docs_lint(args: argparse.Namespace) -> int:
    from engramory.core.docs_tools import lint_repo, resolve_confined_repo_path

    raw_path = args.repo_path or args.path
    target = resolve_confined_repo_path(raw_path)
    report = lint_repo(
        target,
        strict=bool(args.strict),
        require_frontmatter=bool(getattr(args, "require_frontmatter", False)),
        verify_registry=bool(getattr(args, "verify_registry", False)),
    )
    summary = (
        f"status={report['status']} files={report['files_checked']} errors={report['error_count']}"
    )
    _emit(bool(getattr(args, "json", False)), report, summary)
    return 0 if report["status"] == "ok" else 1


def _cmd_docs_tools(args: argparse.Namespace) -> int:
    from engramory.core.docs_tools import list_documentation_tools

    report = list_documentation_tools(tier=args.tier)
    lines = [
        f"{item['tool']} [{item['tier']}] — {item['category']} ({item['license']})"
        for item in report["tools"]
    ]
    _emit(bool(getattr(args, "json", False)), report, "\n".join(lines))
    return 0


def _cmd_docs_build_portal(args: argparse.Namespace) -> int:
    from pathlib import Path

    from engramory.core.docs_tools import resolve_confined_repo_path, stage_portal_bundle

    raw_path = args.repo_path or args.path
    target = resolve_confined_repo_path(raw_path)
    out_dir = Path(args.output_dir) if args.output_dir else None
    report = stage_portal_bundle(
        target,
        output_dir=out_dir,
        dry_run=bool(args.dry_run),
    )
    summary = (
        f"status={report['status']} files_staged={report['files_staged']} "
        f"scrubbed={report['scrubbed_files']}"
    )
    _emit(bool(getattr(args, "json", False)), report, summary)
    return 0


def _cmd_docs_check_drift(args: argparse.Namespace) -> int:
    from engramory.core.docs_tools import check_repo_drift, resolve_confined_repo_path

    raw_path = args.repo_path or args.path
    target = resolve_confined_repo_path(raw_path)
    report = check_repo_drift(target, base_ref=args.base_ref, head_ref=args.head_ref)
    _emit(
        bool(getattr(args, "json", False)),
        report,
        f"status={report['status']} findings={report['finding_count']}",
    )
    return 0 if report["status"] == "ok" else 1


def _cmd_docs_export_llms(args: argparse.Namespace) -> int:
    from engramory.core.docs_tools import export_repo_llms, resolve_confined_repo_path

    raw_path = args.repo_path or args.path
    target = resolve_confined_repo_path(raw_path)
    report = export_repo_llms(target, write_files=bool(args.write), include_full=bool(args.full))
    _emit(bool(getattr(args, "json", False)), report, str(report["llms_txt"]).rstrip())
    return 0


def _cmd_docs_slice_runbook(args: argparse.Namespace) -> int:
    from engramory.core.docs_tools import resolve_confined_repo_path, slice_local_runbooks

    def _fmt_human(p: dict[str, Any]) -> str:
        return str(p.get("markdown")) if args.format == "markdown" else f"steps={p['step_count']}"

    if args.repo_path is not None:
        target = resolve_confined_repo_path(args.repo_path)
        res = slice_local_runbooks(
            target,
            doc_id=args.doc_id,
            alert_id=args.alert_id,
            max_tier=args.max_tier,
            token_budget=int(args.token_budget),
            output_format=args.format,
        )
        _emit(bool(getattr(args, "json", False)), res, _fmt_human(res))
        return 0

    try:

        async def _op(surface: AccessSurface, actor: ActorContext) -> dict[str, Any]:
            return await surface.docs_slice_runbook(
                actor,
                doc_id=args.doc_id,
                alert_id=args.alert_id,
                max_tier=args.max_tier,
                token_budget=int(args.token_budget),
                format=args.format,
            )

        return _run_with_surface(args, _op, _fmt_human)
    except (ConfigError, StoreUnavailable):
        target = resolve_confined_repo_path(".")
        res = slice_local_runbooks(
            target,
            doc_id=args.doc_id,
            alert_id=args.alert_id,
            max_tier=args.max_tier,
            token_budget=int(args.token_budget),
            output_format=args.format,
        )
        _emit(bool(getattr(args, "json", False)), res, _fmt_human(res))
        return 0


def _cmd_docs_smoke_run(args: argparse.Namespace) -> int:
    from engramory.core.docs_tools import resolve_confined_repo_path, smoke_run_green_steps

    raw_path = args.repo_path or args.path
    target = resolve_confined_repo_path(raw_path)
    report = smoke_run_green_steps(target, doc_id=args.doc_id, dry_run=bool(args.dry_run))
    summary = f"status={report['status']} passed={report['passed']} failed={report['failed']}"
    _emit(bool(getattr(args, "json", False)), report, summary)
    return 0 if report["status"] == "ok" else 1


def _cmd_docs_ingest(args: argparse.Namespace) -> int:
    import dataclasses

    from engramory.core.chunking import chunk_markdown
    from engramory.core.docs_tools import (
        _is_sdd_doc_path,
        _is_within_root,
        parse_markdown_document,
        resolve_confined_repo_path,
    )
    from engramory.core.models import KBSection

    raw_path = args.repo_path or args.path
    target = resolve_confined_repo_path(raw_path)
    bound_root = target if target.is_dir() else target.parent
    raw_files = (
        [target]
        if target.is_file()
        else sorted((target / "docs").rglob("*.md"))
        if (target / "docs").is_dir()
        else sorted(target.glob("*.md"))
    )
    md_files = [
        f
        for f in raw_files
        if _is_within_root(f, bound_root)
        and (target.is_file() or not _is_sdd_doc_path(f, bound_root))
    ]

    async def _op(surface: AccessSurface, actor: ActorContext) -> dict[str, Any]:
        eff_actor = (
            dataclasses.replace(actor, project_id=args.project_id) if args.project_id else actor
        )
        docs_ingested = 0
        steps_ingested = 0
        sections_ingested = 0
        for f in md_files:
            parsed = parse_markdown_document(
                f,
                repo_root=bound_root,
                tenant_id=eff_actor.tenant_id,
                project_id=eff_actor.project_id,
            )
            if not parsed.has_frontmatter or parsed.kb_document is None:
                continue
            doc = parsed.kb_document
            ev_ref = doc.git_commit_sha or "local-head"
            raw_text = f.read_text(encoding="utf-8", errors="replace")
            chunks = chunk_markdown(raw_text, doc.doc_id)
            if chunks:
                kb_secs = [
                    KBSection(
                        doc_id=doc.doc_id,
                        citation=cit,
                        text=txt,
                        tenant_id=eff_actor.tenant_id,
                        project_id=eff_actor.project_id,
                        domain_id=doc.domain_id,
                        scope=doc.scope,
                        version=1,
                    )
                    for cit, txt in chunks
                ]
                await surface.knowledge_ingest_batch(
                    eff_actor,
                    sections=kb_secs,
                    evidence_ref=ev_ref,
                    scope=doc.scope,
                )
                sections_ingested += len(kb_secs)
            r = await surface.docs_ingest_runbook(
                eff_actor,
                doc=doc,
                steps=parsed.steps,
                evidence_ref=ev_ref,
            )
            docs_ingested += 1
            steps_ingested += int(r.get("step_count", 0))
        return {
            "status": "ok",
            "docs_ingested": docs_ingested,
            "steps_ingested": steps_ingested,
            "sections_ingested": sections_ingested,
        }

    return _run_with_surface(
        args,
        _op,
        lambda p: (
            f"docs_ingested={p['docs_ingested']} "
            f"steps_ingested={p['steps_ingested']} "
            f"sections_ingested={p['sections_ingested']}"
        ),
    )


def _cmd_memory_ingest_event(args: argparse.Namespace) -> int:
    """Ingest a canonical ExperienceEvent via CLI (and optionally publish to EventsPort)."""
    from engramory.core.experience import ExperienceEvent
    from engramory.workers.runner import ingest_experience_event

    raw_str = getattr(args, "event_json", None)
    event_file = getattr(args, "event_file", None)
    if event_file:
        raw_str = Path(event_file).read_text(encoding="utf-8")
    if not isinstance(raw_str, str) or not raw_str:
        raise ValueError("memory ingest-event requires either --event-json or --event-file")
    raw_dict = json.loads(raw_str)
    if not isinstance(raw_dict, dict):
        raise ValueError("ExperienceEvent JSON must decode to a JSON object")

    config = _load_config(getattr(args, "config", None))
    if "tenant_id" not in raw_dict:
        raw_dict = {**raw_dict, "tenant_id": config.ctx.tenant_id}

    event = ExperienceEvent.from_dict(raw_dict)
    adapters = dev_adapters(config.dsn)
    repo = Repository(config.dsn)

    async def _run() -> dict[str, Any]:
        try:
            episode_id = await ingest_experience_event(
                repo,
                event,
                enforce_tenant_id=config.ctx.tenant_id,
            )
            published_topic: str | None = None
            if getattr(args, "publish_nats", False) and adapters.events is not None:
                published_topic = f"engramory.events.{event.kind}"
                await adapters.events.publish(topic=published_topic, event=event.to_dict())
            return {
                "episode_id": episode_id,
                "event_id": event.event_id,
                "kind": event.kind,
                "published_topic": published_topic,
            }
        finally:
            await _close_adapters(adapters)

    payload = asyncio.run(_run())
    _emit(
        args.json,
        payload,
        f"episode {payload['episode_id']} (event_id={payload['event_id']})",
    )
    return 0


def _cmd_memory_learning_health(args: argparse.Namespace) -> int:
    """Compute closed-loop learning effectiveness signals over recent episodes."""
    config = _load_config(getattr(args, "config", None))
    repo = Repository(config.dsn)
    project_id = getattr(args, "project_id", None) or config.ctx.project_id
    limit = int(getattr(args, "limit", 500))
    payload = asyncio.run(
        repo.get_learning_health_summary(
            tenant_id=config.ctx.tenant_id,
            project_id=project_id,
            limit=limit,
        )
    )
    cal = payload["exec_ready_calibration"]
    rep = payload["repeat_findings"]
    _emit(
        args.json,
        payload,
        (
            f"paired_plans={cal['paired_plans']} "
            f"calibration_mae={cal['calibration_mae']} "
            f"repeat_findings_rate={rep['repeat_findings_rate']}"
        ),
    )
    return 0
