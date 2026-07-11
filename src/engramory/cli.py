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
import json
import sys
import tomllib
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from engramory.access.authz import ActorContext
from engramory.access.surface import AccessSurface, AuthzError, GovernedWriteRejected
from engramory.adapters.factory import require_dev_profile
from engramory.core.models import ValidationError
from engramory.core.repository import Repository, StoreUnavailable
from engramory.workers.distillation import reflect

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


def _load_config(explicit: str | None = None) -> CliConfig:
    path = Path(explicit) if explicit else _discover_config()
    try:
        raw = tomllib.loads(path.read_text())
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


def _emit(as_json: bool, payload: dict[str, Any], human: str) -> None:
    print(json.dumps(payload, ensure_ascii=False) if as_json else human)


# ── commands ────────────────────────────────────────────────────────────────

def _upsert_profile_best_effort(agent_id: str, dsn: str) -> str:
    """L3 profile row: init still succeeds when the store is down — the row
    lands on a later init run (every init retries, including re-runs on an
    existing config)."""
    from engramory.core.models import AgentProfile

    try:
        asyncio.run(Repository(dsn).upsert_agent_profile(AgentProfile(agent_id=agent_id)))
    except StoreUnavailable as exc:
        print(f"engramory init: store unreachable, profile deferred: {exc}",
              file=sys.stderr)
        return "deferred"
    return "created"


def _cmd_init(args: argparse.Namespace) -> int:
    cfg_path = Path(CONFIG_DIRNAME) / CONFIG_FILENAME
    if cfg_path.exists():
        profile_state = _upsert_profile_best_effort(args.agent_id, args.dsn)
        _emit(args.json,
              {"created": False, "config": str(cfg_path), "profile": profile_state},
              f"exists: {cfg_path}")
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
    profile_state = _upsert_profile_best_effort(args.agent_id, args.dsn)
    _emit(args.json,
          {"created": True, "config": str(cfg_path), "profile": profile_state},
          f"created: {cfg_path}")
    return 0


def _run_with_surface(
    args: argparse.Namespace,
    op: Callable[[AccessSurface, ActorContext], Coroutine[Any, Any, dict[str, Any]]],
    human: Callable[[dict[str, Any]], str],
) -> int:
    config = _load_config(getattr(args, "config", None))
    surface = AccessSurface(Repository(config.dsn))
    payload = asyncio.run(op(surface, config.ctx))
    _emit(args.json, payload, human(payload))
    return 0


def _cmd_memory_add(args: argparse.Namespace) -> int:
    async def op(surface: AccessSurface, ctx: ActorContext) -> dict[str, Any]:
        episode_id = await surface.memory_add(ctx, content=args.content, kind=args.kind)
        return {"episode_id": episode_id}

    return _run_with_surface(args, op, lambda p: f"episode {p['episode_id']}")


def _cmd_memory_search(args: argparse.Namespace) -> int:
    async def op(surface: AccessSurface, ctx: ActorContext) -> dict[str, Any]:
        hits = await surface.memory_search(ctx, args.query, scope=args.scope, k=args.k)
        return {
            "hits": [
                {
                    "memory_id": h.memory_id,
                    "summary": h.summary,
                    "score": h.score,
                    "retrieval_id": h.retrieval_id,
                    "token_count": h.token_count,
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
    created = asyncio.run(
        reflect(repo, tenant_id=config.ctx.tenant_id, agent_id=config.ctx.agent_id)
    )
    _emit(args.json, {"created": created}, f"distilled {created} memorie(s)")
    return 0


def _cmd_profile_get(args: argparse.Namespace) -> int:
    async def op(surface: AccessSurface, ctx: ActorContext) -> dict[str, Any]:
        profile = await surface.agent_profile_get(ctx)
        return {
            "agent_id": profile.agent_id,
            "display_name": profile.display_name,
            "standing_preferences": dict(profile.standing_preferences),
        }

    return _run_with_surface(args, op, lambda p: f"{p['agent_id']} ({p['display_name']})")


def _cmd_status(args: argparse.Namespace) -> int:
    """Dev-tier ops command: store reachability + tenant counts."""
    config = _load_config(getattr(args, "config", None))
    repo = Repository(config.dsn)

    async def counts() -> dict[str, Any]:
        return {
            "store": "ok",
            "episodes": await repo.count_episodes(tenant_id=config.ctx.tenant_id),
            "kb_sections": await repo.count_kb_sections(tenant_id=config.ctx.tenant_id),
        }

    payload = asyncio.run(counts())
    _emit(args.json, payload,
          f"store ok — {payload['episodes']} episode(s), "
          f"{payload['kb_sections']} kb section(s)")
    return 0


# ── parser + entry point ────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="engramory",
        description="Agent memory plane — dev/CI face over AccessSurface (ADR-10).",
    )
    parser.add_argument("--json", action="store_true",
                        help="one machine-readable JSON object on stdout")
    parser.add_argument("--config", help="explicit config path "
                        f"(default: {CONFIG_DIRNAME}/{CONFIG_FILENAME} discovered upward)")
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
    p_add.set_defaults(func=_cmd_memory_add)

    p_search = mem_sub.add_parser("search", help="scoped retrieval")
    p_search.add_argument("--query", required=True)
    p_search.add_argument("--scope", default="agent")
    p_search.add_argument("-k", type=int, default=8)
    p_search.set_defaults(func=_cmd_memory_search)

    p_fb = mem_sub.add_parser("feedback", help="record a retrieval outcome")
    p_fb.add_argument("--retrieval-id", required=True)
    p_fb.add_argument("--outcome", required=True,
                      help="useful | not_useful | harmful")
    p_fb.set_defaults(func=_cmd_memory_feedback)

    p_forget = mem_sub.add_parser("forget", help="retire a memory (soft, audited)")
    p_forget.add_argument("--memory-id", required=True)
    p_forget.add_argument("--reason", required=True)
    p_forget.set_defaults(func=_cmd_memory_forget)

    p_distill = mem_sub.add_parser(
        "distill", help="ops: interim reflect pass (episodes -> memories)"
    )
    p_distill.set_defaults(func=_cmd_memory_distill)

    p_profile = sub.add_parser("profile", help="L3 identity")
    prof_sub = p_profile.add_subparsers(dest="profile_command", required=True)
    p_pget = prof_sub.add_parser("get", help="load the caller's own profile")
    p_pget.set_defaults(func=_cmd_profile_get)

    p_status = sub.add_parser("status", help="ops: store reachability + counts")
    p_status.set_defaults(func=_cmd_status)

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
    # AuthzError/GovernedWriteRejected subclass PermissionError — they MUST
    # be matched before the fence clause or every deny would exit 2, not 1.
    except (AuthzError, GovernedWriteRejected) as exc:
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
