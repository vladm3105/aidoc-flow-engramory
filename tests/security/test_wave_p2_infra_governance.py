"""Wave P2 infra, stack policy, Astro Starlight portal, health probes, and CI governance tests."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import yaml

import engramory.workers.runner as runner_mod
from engramory.access.authz import ActorContext
from engramory.access.surface import AccessSurface
from engramory.adapters.factory import (
    PROFILES,
    DevTierFenceError,
    adapters_for_profile,
    require_dev_profile,
)
from engramory.core.context_tools import StackPolicyValidator
from engramory.core.docs_tools import BANNED_RETIRED_TOOLS, DOCUMENTATION_TOOL_CATALOG
from engramory.core.portal_builder import stage_portal_bundle
from engramory.core.repository import Repository
from engramory.mcp.server import McpServer
from engramory.workers.runner import run_worker_loop

REPO_ROOT = Path(__file__).resolve().parents[2]


def _read(rel_path: str) -> str:
    return (REPO_ROOT / rel_path).read_text(encoding="utf-8")


# ── C11: Compose, Dockerfile & fail-closed deploy-stage retirement ──────────


def test_c11_dev_compose_pg18_loopback_valkey_nats_and_hardening() -> None:
    data = yaml.safe_load(_read("docker-compose.yml"))
    services = data["services"]

    assert "keycloak" not in services
    assert "/var/lib/postgresql" in " ".join(services["postgres"]["volumes"])
    assert services["valkey"]["image"] == "valkey/valkey:8-alpine"
    assert services["nats"]["image"] == "nats:2.10-alpine"
    assert "-js" in services["nats"]["command"]
    assert "OPENROUTER_API_KEY" in services["litellm"]["environment"]

    for svc_name in ("postgres", "valkey", "nats", "ollama", "litellm", "neo4j", "mcp"):
        for port_entry in services[svc_name]["ports"]:
            assert str(port_entry).startswith("127.0.0.1:"), f"{svc_name}: {port_entry}"

    for hardened in ("mcp", "worker"):
        svc = services[hardened]
        assert svc["cap_drop"] == ["ALL"]
        assert "no-new-privileges:true" in svc["security_opt"]
        assert svc["read_only"] is True
        env = svc["environment"]
        assert "NATS_URL" in env
        assert "${ENGRAMORY_EMBED_MODEL:-embed}" == env["ENGRAMORY_EMBED_MODEL"]

    assert "/health" in " ".join(services["mcp"]["healthcheck"]["test"])


def test_c11_platform_compose_fail_closed_secrets_and_no_interlog() -> None:
    raw = _read("compose.platform.yaml")
    assert "/var/spool/interlog" not in raw
    assert "auth-stage.aiops-flow.io" not in raw

    data = yaml.safe_load(raw)
    services = data["services"]
    required_secret_vars = (
        "ENGRAMORY_DATABASE_URL",
        "ENGRAMORY_BLOB_DSN",
        "ENGRAMORY_GRAPH_PASSWORD",
        "NEO4J_PASSWORD",
        "LITELLM_API_KEY",
    )

    for svc_name in ("mcp", "worker"):
        svc = services[svc_name]
        assert svc["cap_drop"] == ["ALL"]
        assert "no-new-privileges:true" in svc["security_opt"]
        assert svc["read_only"] is True
        env_map = dict(item.split("=", 1) for item in svc["environment"])
        for key in required_secret_vars:
            assert ":?" in env_map[key], f"{svc_name}.{key} must use fail-closed :?"

    mcp_env = dict(item.split("=", 1) for item in services["mcp"]["environment"])
    assert mcp_env["ENGRAMORY_ALLOWED_DOCS_ROOT"] == "${ENGRAMORY_ALLOWED_DOCS_ROOT:-/workspace}"
    assert "auth.int.aiops-flow.io" in mcp_env["AUTHENTIK_JWKS_URL"]
    assert "auth.int.aiops-flow.io" in mcp_env["AUTHENTIK_ISSUER"]
    assert "/health" in " ".join(services["mcp"]["healthcheck"]["test"])


def test_c11_dockerfile_and_retired_deploy_stage_script() -> None:
    dockerfile = _read("Dockerfile")
    assert "uv sync --frozen --no-dev --no-editable" in dockerfile
    assert "ffmpeg" in dockerfile
    assert "git" in dockerfile
    assert "/var/spool/interlog" not in dockerfile
    assert "tempfile.gettempdir()" in dockerfile

    deploy_script = REPO_ROOT / "scripts" / "deploy-stage.sh"
    assert deploy_script.is_file()
    assert os.access(deploy_script, os.X_OK)
    res = subprocess.run(
        ["bash", str(deploy_script)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 1
    assert "platform-stage-ops" in res.stderr


# ── H13: Stack policy, packaging, profiles, OIDC defaults & secret scan ───


def test_h13_pyproject_packaging_and_mise_pin() -> None:
    pyproject = tomllib.loads(_read("pyproject.toml"))
    assert "dev" in pyproject.get("dependency-groups", {})
    opt_deps = pyproject["project"].get("optional-dependencies", {})
    assert "dev" not in opt_deps
    extraction_deps = opt_deps.get("extraction", [])
    assert any(d.startswith("pypdf") for d in extraction_deps)
    all_declared = pyproject["project"]["dependencies"] + [
        d for group in opt_deps.values() for d in group
    ]
    assert not any("pymupdf" in d.lower() for d in all_declared)
    assert (
        pyproject["project"]["urls"]["Repository"]
        == "https://github.com/vladm3105/aidoc-flow-engramory"
    )
    assert 'python = "3.12"' in _read("mise.toml")


def test_h13_stack_policy_validator_clean_and_word_boundary(tmp_path: Path) -> None:
    ok, violations = StackPolicyValidator(REPO_ROOT).validate()
    assert ok is True
    assert violations == []

    (tmp_path / ".aidoc").mkdir()
    (tmp_path / "src").mkdir()
    (tmp_path / ".aidoc" / "stack-policy.yaml").write_text(
        "boundaries:\n  strict_allowlist: false\n  banned_patterns:\n    - 'eval('\n",
        encoding="utf-8",
    )
    (tmp_path / "src" / "clean_mod.py").write_text(
        "async def get_retrieval(item_id: int) -> int:\n    return item_id\n",
        encoding="utf-8",
    )
    (tmp_path / "src" / "unsafe_mod.py").write_text(
        'import builtins\nx = eval("1")\ny = builtins.eval("2")\n',
        encoding="utf-8",
    )

    ok_tmp, tmp_violations = StackPolicyValidator(tmp_path).validate()
    assert ok_tmp is False
    assert all("clean_mod.py" not in v for v in tmp_violations)
    assert any("unsafe_mod.py:2" in v for v in tmp_violations)
    assert any("unsafe_mod.py:3" in v for v in tmp_violations)


def test_h13_stage_profile_routing_and_cli_authentik_defaults() -> None:
    assert PROFILES == ("dev", "stage", "gcp", "azure")
    bundle = adapters_for_profile(
        profile="stage",
        dsn="postgresql://user:pass@127.0.0.1:5432/engramory",
    )
    assert bundle.profile == "stage"

    with pytest.raises(DevTierFenceError):
        require_dev_profile("cli", profile="stage")

    cli_source = _read("src/engramory/cli.py")
    assert "auth.int.aiops-flow.io" in cli_source
    assert "auth-stage.aiops-flow.io" not in cli_source


def test_h13_secret_scan_catches_leaks_and_passes_live_repo(tmp_path: Path) -> None:
    scanner = REPO_ROOT / "scripts" / "secret_scan.py"
    clean = subprocess.run(
        [sys.executable, str(scanner), "."],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert clean.returncode == 0, clean.stdout + clean.stderr

    samples = {
        "a.env": "LITELLM_API_KEY=" + "sk-" + "litellm1234567890abcd\n",
        "b.yaml": "NEO4J_PASSWORD: " + "stage_graph_" + "secret_2026\n",
        "c.py": 'OPENAI = "' + "sk-" + 'proj-abcdefghijklmnopqrstuvwxyz1234"\n',
        "d.txt": 'GH = "' + "github_pat_" + '11ABCDEFG0123456789_abcdefghijklmnopqrstuvwxyz"\n',
        "e.txt": 'BAO = "' + "hvs." + 'CAESIJabcdefghijklmnopqrstuvwxyz123456"\n',
        "f.txt": 'DSN = "' + "postgres://" + 'appuser:s3cr3tpass@db.internal:5432/engramory"\n',
    }
    for fname, body in samples.items():
        (tmp_path / fname).write_text(body, encoding="utf-8")

    dirty = subprocess.run(
        [sys.executable, str(scanner), str(tmp_path)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert dirty.returncode == 1
    for fname in samples:
        assert fname in dirty.stderr


# ── H14: Protected CI config, gitleaks & GitHub workflows ─────────────────


def test_h14_protected_ci_governance_and_workflows() -> None:
    assert 'PROMOTION_BRANCHES="staging main"' in _read(".ci-config")
    gitleaks = _read(".gitleaks.toml")
    assert "AKIA[0-9A-Z]{9}EXAMPLE" in gitleaks
    assert "AKIA[0-9A-Z]{16}" not in gitleaks

    for removed in (
        ".semgrepignore",
        ".pre-commit-config.yaml",
        ".github/actions/pre-commit/action.yml",
        ".github/dependabot.yml",
        ".github/workflows/pre-commit.yml",
        ".github/workflows/markdown-lint.yml",
        ".github/workflows/links.yml",
    ):
        assert not (REPO_ROOT / removed).exists(), f"Legacy file must be removed: {removed}"

    standards_drift = _read(".github/workflows/standards-drift.yml")
    assert 'SHA="' in standards_drift
    assert "sha256sum -c -" in standards_drift

    bot_signoff = _read(".github/workflows/bot-signoff.yml")
    assert "branches: [dev]" in bot_signoff
    assert "(startsWith(github.head_ref, 'dependabot/')" in bot_signoff

    branch_promo = _read(".github/workflows/branch-promotion.yml")
    assert 'PROMOTION_TARGETS: "staging main"' in branch_promo

    quick_gates = _read(".github/workflows/quick-gates.yml")
    assert "id: taskcheck" in quick_gates
    assert "R_TASKCHECK:" in quick_gates
    assert "R_PRECOMMIT" not in quick_gates
    assert "./.github/actions/pre-commit" not in quick_gates

    ci_yml = _read(".github/workflows/ci.yml")
    assert "uv sync --locked" in ci_yml
    assert "jdx/mise-action@" in ci_yml

    verify_yml = _read(".github/workflows/verify.yml")
    assert "branches: [staging, main]" in verify_yml
    assert ("TO" + "DO") not in verify_yml

    nightly_yml = _read(".github/workflows/nightly-audit.yml")
    assert "vars.TRUFFLEHOG_VERIFY == 'true'" in nightly_yml
    assert "TRUFLEHOG" not in nightly_yml

    pinned_sha = "@845964a439747001b5e3febbeebde4aa5a551d20"
    for caller in (
        ".github/workflows/ai-review.yml",
        ".github/workflows/auto-merge-ai-prs.yml",
        ".github/workflows/composition.yml",
        ".github/workflows/docs-sync.yml",
        ".github/workflows/labeler.yml",
        ".github/workflows/secret-scan.yml",
    ):
        assert pinned_sha in _read(caller), f"Unpinned reusable caller: {caller}"


# ── M3: Harness lockfile, Astro Starlight portal, portal_builder & UTS ────


def test_m3_harness_lockfile_portal_and_uts_config(tmp_path: Path) -> None:
    lock_data = json.loads(_read(".aidoc/harness.lock.json"))
    digests = json.dumps(lock_data)
    assert ("0" * 64) not in digests
    canons = lock_data["canons"]
    assert len(canons) >= 8
    for name, entry in canons.items():
        assert len(entry["pinned_commit"]) == 40, name
        assert len(entry["manifest_sha256"]) == 64, name

    astro_cfg = _read("portal/astro.config.mjs")
    portal_pkg = json.loads(_read("portal/package.json"))
    assert "port: 4321" in astro_cfg
    assert "port: 8005" not in astro_cfg
    assert "4321" in portal_pkg["scripts"]["dev"]
    assert "8005" not in json.dumps(portal_pkg)

    for required_portal_file in (
        "portal/pnpm-lock.yaml",
        "portal/src/content.config.ts",
        "portal/src/content/docs/index.mdx",
        "portal/src/pages/contracts/playground.astro",
    ):
        assert (REPO_ROOT / required_portal_file).is_file(), required_portal_file

    uts = json.loads(_read("unified-test-suite.config.json"))
    assert "ENGRAMORY_DATABASE_URL" in uts["secrets"]["envKeys"]
    assert "smoke" in uts["layers"]
    assert "smoke" in uts["gates"]["all"]["layers"]
    assert uts["stack"]["compose"] == "docker-compose.yml"
    assert uts["coverage"]["unit"]["minPercent"] == 85

    # Verify stage_portal_bundle title synthesis & stale markdown pruning
    repo_dir = tmp_path / "repo"
    (repo_dir / "docs").mkdir(parents=True)
    (repo_dir / "docs" / "untitled.md").write_text(
        "---\ndomain: core\n---\n\n# Synthesized Title Heading\n\nBody text.\n",
        encoding="utf-8",
    )
    out_dir = repo_dir / "out"
    out_dir.mkdir()
    stale = out_dir / "stale-orphan.md"
    stale.write_text("# Old\n", encoding="utf-8")

    staged = stage_portal_bundle(repo_dir, output_dir=out_dir, allowed_root=tmp_path)
    assert not stale.exists()
    assert staged["pruned_count"] == 1
    assert "stale-orphan.md" in staged["pruned_files"]
    staged_doc = (out_dir / "untitled.md").read_text(encoding="utf-8")
    assert 'title: "Synthesized Title Heading"' in staged_doc


# ── M6: Dependency-aware /health & /ready and worker heartbeat probes ─────


@pytest.mark.asyncio
async def test_m6_mcp_health_ready_probes_and_repository_ping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = Repository("postgresql://u:p@127.0.0.1:1/db")
    assert await repo.ping(timeout_sec=0.2) is False

    ping_state = {"ok": False}

    async def _fake_ping(_self: Repository, *, timeout_sec: float = 2.0) -> bool:
        del timeout_sec
        return ping_state["ok"]

    monkeypatch.setattr(Repository, "ping", _fake_ping)

    surface = SimpleNamespace(_repo=repo, _graph=None)
    ctx = ActorContext(
        agent_id="agent-probe",
        tenant_id="tenant-probe",
        project_id="proj-probe",
        scopes=("read",),
        domain_id="default",
    )
    srv = McpServer(cast(AccessSurface, surface), ctx, auth_enabled=False)
    app = srv.create_http_app()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        for path in ("/", "/healthz"):
            res = await client.get(path)
            assert res.status_code == 200

        for path in ("/health", "/ready"):
            degraded = await client.get(path)
            assert degraded.status_code == 503
            body = degraded.json()
            assert body["status"] == "degraded"
            assert body["checks"]["postgres"] == "unreachable"

        ping_state["ok"] = True
        for path in ("/health", "/ready"):
            healthy = await client.get(path)
            assert healthy.status_code == 200
            body = healthy.json()
            assert body["status"] == "ok"
            assert body["checks"]["postgres"] == "ok"


@pytest.mark.asyncio
async def test_m6_worker_periodic_heartbeat_and_failure_latch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runner_mod, "HEARTBEAT_TICK_MAX_SEC", 0.02)
    monkeypatch.setattr(runner_mod, "reflect", AsyncMock(return_value=0))
    monkeypatch.setattr(runner_mod, "consolidate", AsyncMock())

    touches: list[Path] = []
    real_touch = runner_mod._touch_heartbeat

    def _spy_touch(path: Path) -> None:
        touches.append(path)
        real_touch(path)

    monkeypatch.setattr(runner_mod, "_touch_heartbeat", _spy_touch)

    healthy_hb = tmp_path / "healthy.heartbeat"
    ok_repo = MagicMock(spec=Repository)
    ok_repo.get_active_agents_with_unreflected_episodes = AsyncMock(return_value=[])
    task_ok = asyncio.create_task(
        run_worker_loop(ok_repo, interval_sec=0.09, heartbeat_path=healthy_hb)
    )
    await asyncio.sleep(0.11)
    task_ok.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task_ok

    assert healthy_hb.is_file()
    assert len(touches) >= 3

    touches.clear()
    failing_hb = tmp_path / "failing.heartbeat"
    bad_repo = MagicMock(spec=Repository)
    bad_repo.get_active_agents_with_unreflected_episodes = AsyncMock(
        side_effect=RuntimeError("postgres unavailable")
    )
    task_bad = asyncio.create_task(
        run_worker_loop(bad_repo, interval_sec=0.07, heartbeat_path=failing_hb)
    )
    await asyncio.sleep(0.06)
    task_bad.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task_bad

    assert len(touches) == 1


# ── M7, L2, L5: Retired tools parity, env templates, scripts & SKILL.md ───


def test_m7_l2_l5_retired_tools_env_templates_and_scripts(tmp_path: Path) -> None:
    vale_rule = _read(".vale/styles/AIOpsAgent/BannedRetiredTools.yml")
    assert "bytebase" in BANNED_RETIRED_TOOLS
    assert "seaweedfs" in BANNED_RETIRED_TOOLS
    assert "pre-commit" in BANNED_RETIRED_TOOLS
    assert "bytebase" in vale_rule.lower()
    assert "seaweedfs" in vale_rule.lower()

    catalog = {entry["tool"]: entry for entry in DOCUMENTATION_TOOL_CATALOG}
    assert "docling + pypdf + beautifulsoup4" in catalog
    assert all("pymupdf" not in k.lower() for k in catalog)

    for env_rel in (".aidoc/security.env.example", ".aidoc/litellm.env.example"):
        env_text = _read(env_rel)
        assert "sk-" not in env_text
        assert "auth-stage" not in env_text
        assert ":?" in env_text

    assert "docs/sdd/06_SPEC/SPEC-07_cli_face.yaml" in _read("skills/engramory-memory/SKILL.md")

    ingest_src = _read("scripts/ingest_docs.py")
    assert 'choices=["agent", "project", "domain", "space"]' in ingest_src
    assert "return 1 if errors > 0 else 0" in ingest_src
    bad_scope = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "ingest_docs.py"), "--scope", "global"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert bad_scope.returncode == 2

    # Stubbed PATH failing task must NOT abort a pure unprotected remote branch delete
    stub_bin = tmp_path / "bin"
    stub_bin.mkdir()
    fake_task = stub_bin / "task"
    fake_task.write_text("#!/usr/bin/env bash\nexit 99\n", encoding="utf-8")
    fake_task.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{stub_bin}:{env.get('PATH', '')}"

    delete_input = (
        "refs/heads/feature/old 0000000000000000000000000000000000000000 "
        "refs/heads/feature/old 1111111111111111111111111111111111111111\n"
    )
    guard_del = subprocess.run(
        ["bash", str(REPO_ROOT / "scripts" / "pre_push_guard.sh"), "origin"],
        cwd=REPO_ROOT,
        input=delete_input,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert guard_del.returncode == 0, guard_del.stderr

    protected_del = (
        "refs/heads/staging 0000000000000000000000000000000000000000 "
        "refs/heads/staging 1111111111111111111111111111111111111111\n"
    )
    guard_prot = subprocess.run(
        ["bash", str(REPO_ROOT / "scripts" / "pre_push_guard.sh"), "origin"],
        cwd=REPO_ROOT,
        input=protected_del,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert guard_prot.returncode == 1
