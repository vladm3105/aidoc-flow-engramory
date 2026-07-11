"""CLI-face integration tests (PLAN-002 Phase 2; SPEC-07).

The CLI is a thin binding over AccessSurface: exit codes 0 (success) /
1 (fail-closed reject) / 2 (usage-config-environment) / 3 (retryable),
global --json, config discovery at .engramory/config.toml, and the ADR-10
dev-tier fence. Tests drive the real entry point in-process against the
shared Postgres fixture.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from engramory.cli import main


@pytest.fixture
def project_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ENGRAMORY_PROFILE", raising=False)
    return tmp_path


def _run(*argv: str) -> tuple[int, str]:
    """Invoke the CLI in-process; returns (exit_code, stdout)."""
    import contextlib
    import io

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = main(list(argv))
    return code, out.getvalue()


def _init(pg_dsn: str, agent: str = "agent-cli", tenant: str = "t-cli") -> None:
    code, _ = _run(
        "init", "--agent-id", agent, "--project-id", "p-cli",
        "--tenant-id", tenant, "--dsn", pg_dsn,
    )
    assert code == 0


# ── init ─────────────────────────────────────────────────────────────────────

def test_init_scaffolds_config_and_profile(
    project_dir: Path, pg_dsn: str
) -> None:
    code, out = _run(
        "--json", "init", "--agent-id", "agent-cli", "--project-id", "p-cli",
        "--tenant-id", "t-cli-init", "--dsn", pg_dsn,
    )
    assert code == 0
    assert json.loads(out)["created"] is True
    assert (project_dir / ".engramory" / "config.toml").exists()
    # init upserts the caller's L3 profile row (PLAN-002 Phase 2.2)
    from engramory.core.repository import Repository

    profile = asyncio.run(Repository(pg_dsn).get_agent_profile("agent-cli"))
    assert profile is not None


def test_init_is_idempotent(project_dir: Path, pg_dsn: str) -> None:
    _init(pg_dsn)
    code, out = _run(
        "--json", "init", "--agent-id", "agent-cli", "--project-id", "p-cli",
        "--tenant-id", "t-cli", "--dsn", pg_dsn,
    )
    assert code == 0
    assert json.loads(out)["created"] is False


# ── dev-tier fence (ADR-10) ─────────────────────────────────────────────────

def test_fence_refuses_non_dev_profile(
    project_dir: Path, pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init(pg_dsn)
    monkeypatch.setenv("ENGRAMORY_PROFILE", "gcp")
    code, _ = _run("memory", "add", "--content", "should not land")
    assert code == 2


# ── memory round-trip: add → distill → search → feedback → forget ──────────

def test_memory_roundtrip(project_dir: Path, pg_dsn: str) -> None:
    _init(pg_dsn, tenant="t-cli-rt")

    code, out = _run("--json", "memory", "add", "--content", "prefer uv over pip")
    assert code == 0
    assert json.loads(out)["episode_id"]

    code, out = _run("--json", "memory", "distill")
    assert code == 0
    assert json.loads(out)["created"] == 1

    code, out = _run("--json", "memory", "search", "--query", "uv", "-k", "5")
    assert code == 0
    hits = json.loads(out)["hits"]
    assert len(hits) == 1 and "uv over pip" in hits[0]["summary"]
    assert hits[0]["retrieval_id"] and hits[0]["memory_id"]

    code, _ = _run(
        "memory", "feedback", "--retrieval-id", hits[0]["retrieval_id"],
        "--outcome", "useful",
    )
    assert code == 0

    code, _ = _run(
        "memory", "forget", "--memory-id", hits[0]["memory_id"],
        "--reason", "test cleanup",
    )
    assert code == 0

    code, out = _run("--json", "memory", "search", "--query", "uv", "-k", "5")
    assert code == 0 and json.loads(out)["hits"] == []


# ── exit-code contract ──────────────────────────────────────────────────────

def test_authz_deny_is_exit_1(project_dir: Path, pg_dsn: str) -> None:
    """A real surface deny is a fail-closed reject (1), never usage (2) —
    AuthzError subclasses PermissionError; handler order matters."""
    _init(pg_dsn, tenant="t-cli-deny")  # default scopes: agent, project
    code, _ = _run("memory", "search", "--query", "x", "--scope", "space")
    assert code == 1


def test_unknown_retrieval_id_is_exit_1(project_dir: Path, pg_dsn: str) -> None:
    _init(pg_dsn, tenant="t-cli-u1")
    code, _ = _run(
        "memory", "feedback",
        "--retrieval-id", "00000000-0000-0000-0000-000000000000",
        "--outcome", "useful",
    )
    assert code == 1


def test_unknown_memory_id_forget_is_exit_1(project_dir: Path, pg_dsn: str) -> None:
    _init(pg_dsn, tenant="t-cli-u2")
    code, _ = _run(
        "memory", "forget",
        "--memory-id", "00000000-0000-0000-0000-000000000000",
        "--reason", "gone",
    )
    assert code == 1


def test_config_discovered_upward_from_subdir(
    project_dir: Path, pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init(pg_dsn, tenant="t-cli-up")
    sub = project_dir / "deep" / "er"
    sub.mkdir(parents=True)
    monkeypatch.chdir(sub)
    code, out = _run("--json", "status")
    assert code == 0 and json.loads(out)["store"] == "ok"


def test_explicit_config_path(
    project_dir: Path, pg_dsn: str, tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _init(pg_dsn, tenant="t-cli-cfg")
    config = project_dir / ".engramory" / "config.toml"
    elsewhere = tmp_path_factory.mktemp("no-config-here")
    monkeypatch.chdir(elsewhere)
    code, out = _run("--json", "--config", str(config), "status")
    assert code == 0 and json.loads(out)["store"] == "ok"


def test_malformed_config_is_usage_error(project_dir: Path, pg_dsn: str) -> None:
    _init(pg_dsn)
    (project_dir / ".engramory" / "config.toml").write_text("not toml [[[")
    assert _run("memory", "add", "--content", "x")[0] == 2


def test_config_missing_store_section_is_usage_error(
    project_dir: Path, pg_dsn: str
) -> None:
    _init(pg_dsn)
    (project_dir / ".engramory" / "config.toml").write_text(
        '[actor]\nagent_id = "a"\nproject_id = "p"\ntenant_id = "t"\n'
    )
    assert _run("memory", "add", "--content", "x")[0] == 2


def test_help_is_exit_0_and_bogus_command_is_exit_2(project_dir: Path) -> None:
    assert _run("--help")[0] == 0
    assert _run("bogus")[0] == 2


def test_bad_outcome_is_fail_closed_reject(
    project_dir: Path, pg_dsn: str
) -> None:
    _init(pg_dsn, tenant="t-cli-x1")
    code, _ = _run(
        "memory", "feedback",
        "--retrieval-id", "00000000-0000-0000-0000-000000000000",
        "--outcome", "amazing",
    )
    assert code == 1


def test_missing_config_is_usage_error(project_dir: Path) -> None:
    code, _ = _run("memory", "add", "--content", "no config yet")
    assert code == 2


def test_store_down_is_retryable(project_dir: Path) -> None:
    _init("postgresql://engramory:x@127.0.0.1:1/engramory")  # nothing listens
    code, _ = _run("memory", "add", "--content", "store is down")
    assert code == 3


# ── profile + status ────────────────────────────────────────────────────────

def test_profile_get(project_dir: Path, pg_dsn: str) -> None:
    _init(pg_dsn, agent="agent-cli-p", tenant="t-cli-p")
    code, out = _run("--json", "profile", "get")
    assert code == 0
    assert json.loads(out)["agent_id"] == "agent-cli-p"


def test_status_reports_counts(project_dir: Path, pg_dsn: str) -> None:
    _init(pg_dsn, tenant="t-cli-s")
    _run("memory", "add", "--content", "one fact")
    code, out = _run("--json", "status")
    assert code == 0
    payload = json.loads(out)
    assert payload["store"] == "ok"
    assert payload["episodes"] == 1
