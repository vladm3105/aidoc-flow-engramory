"""Unit, integration, and adversarial security tests for built-in SDD context tools (PLAN-016)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from engramory.access.authz import ActorContext
from engramory.cli import main as cli_main
from engramory.core.context_tools import (
    AidocDependencyGraph,
    ArtifactNode,
    InvariantChecker,
    ScopedContractResolver,
    StackPolicyValidator,
    compact_node_content,
    estimate_tokens,
    lint_stack_policy,
    local_blast_radius,
    normalize_layer,
    slice_local_context,
    validate_sdd_graph,
)
from engramory.core.models import ValidationError
from engramory.mcp.server import McpServer


def test_normalize_layer_and_token_compaction() -> None:
    assert normalize_layer("01_BRD") == "BRD"
    assert normalize_layer("06-spec") == "SPEC"
    assert normalize_layer(5) == "ADR"
    assert normalize_layer(None) == "UNKNOWN"

    raw = (
        "---\n"
        "id: EARS-01\n"
        "---\n"
        "# Requirement Title\n"
        "Informal narrative prose paragraph that explains background context.\n"
        "- WHEN a client connects THE gateway SHALL verify mTLS.\n"
        "Given an unauthenticated socket\n"
        "Then the server MUST reject the connection.\n"
    )
    assert compact_node_content(raw, tier=0) == raw.strip()

    t1 = compact_node_content(raw, tier=1)
    assert "id: EARS-01" in t1
    assert "# Requirement Title" in t1
    assert "SHALL verify mTLS" in t1
    assert "Given an unauthenticated socket" in t1
    assert "Informal narrative prose" not in t1
    assert "Tier 1 Formal compaction applied" in t1

    t2 = compact_node_content(raw, tier=2)
    assert "id: EARS-01" in t2
    assert "# Requirement Title" in t2
    assert "SHALL verify mTLS" not in t2
    assert "Tier 2 Distant summary" in t2
    assert estimate_tokens(t2) < estimate_tokens(raw)


def test_sdd_graph_v2_and_workspace_scanner_and_invariants(tmp_path: Path) -> None:
    sdd_dir = tmp_path / "sdd"
    (sdd_dir / "01_BRD").mkdir(parents=True)
    (sdd_dir / "05_ADR").mkdir(parents=True)
    (sdd_dir / "06_SPEC").mkdir(parents=True)

    # SDD-GRAPH v2.0 YAML
    (sdd_dir / "01_BRD" / "graph.yaml").write_text(
        "schema_version: '2.0-graph'\n"
        "nodes:\n"
        "  - id: BRD.01\n"
        "    layer: BRD\n"
        "    title: Root Business Goal\n"
        "  - id: EARS.01\n"
        "    layer: EARS\n"
        "    title: Formal Auth Requirement\n"
        "    trace:\n"
        "      upstream: [BRD.01]\n"
        "  - id: BDD.01\n"
        "    layer: BDD\n"
        "    title: Auth Acceptance Scenario\n"
        "edges:\n"
        "  - from: BDD.01\n"
        "    to: EARS.01\n",
        encoding="utf-8",
    )

    # ADR YAML with document_control and related_decisions
    (sdd_dir / "05_ADR" / "ADR-01.yaml").write_text(
        "document_control:\n"
        "  document_id: ADR-01\n"
        "  title: Zero Trust Architecture\n"
        "metadata:\n"
        "  layer: 5\n"
        "related_decisions:\n"
        "  dependencies:\n"
        "    - adr: BRD.01\n",
        encoding="utf-8",
    )

    # Markdown with frontmatter and @-pointers
    (sdd_dir / "06_SPEC" / "IPLAN-01.md").write_text(
        "---\n"
        "id: IPLAN-01\n"
        "title: Implement Auth Gate\n"
        "layer: IPLAN\n"
        "depends_on:\n"
        "  - ADR-01\n"
        "---\n"
        "# Implementation Plan\n"
        "Traces to @EARS.01 and @proto:auth_service.\n",
        encoding="utf-8",
    )

    # Local proto contract
    proto_dir = tmp_path / "proto"
    proto_dir.mkdir()
    (proto_dir / "auth_service.proto").write_text(
        'syntax = "proto3";\nservice AuthService { rpc Verify (Req) returns (Resp); }\n',
        encoding="utf-8",
    )

    val_report = validate_sdd_graph(tmp_path, check_invariants=True)
    assert val_report["passed"] is True
    assert val_report["cycles"] == []
    assert val_report["violations"] == []

    # Slice context for IPLAN-01 (and verify @-prefix deduplication & self-exclusion)
    slice_res = slice_local_context(
        tmp_path,
        task_id="@IPLAN-01",
        depth=6,
        max_tokens=200,
    )
    anc_ids = [a["id"] for a in slice_res["ancestors"]]
    assert "IPLAN-01" not in anc_ids
    assert "@IPLAN-01" not in anc_ids
    assert "ADR-01" in anc_ids
    assert "EARS.01" in anc_ids
    assert "@proto:auth_service" in anc_ids
    assert "BRD.01" in anc_ids
    # Check AccessSurface-compatible keys
    first_anc = slice_res["ancestors"][0]
    assert "labels" in first_anc and isinstance(first_anc["labels"], list)
    assert "properties" in first_anc and "name" in first_anc["properties"]
    assert "=== AIDOC SLICED CONTEXT: TASK [@IPLAN-01] ===" in slice_res["digest"]
    assert "Compaction: budget 200" in slice_res["digest"]

    # Local blast radius for BRD.01
    blast_res = local_blast_radius(tmp_path, doc_id="BRD-01", depth=6)
    desc_ids = {d["id"] for d in blast_res["descendants"]}
    assert "EARS.01" in desc_ids
    assert "ADR-01" in desc_ids
    assert "IPLAN-01" in desc_ids


def test_graph_cycles_and_cardinality_violations() -> None:
    graph = AidocDependencyGraph()
    graph.add_node(ArtifactNode("BRD-01", "BRD", "Orphan seed", dependencies=[]))
    graph.add_node(ArtifactNode("EARS-99", "EARS", "Unverified req", dependencies=["SPEC-01"]))
    graph.add_node(ArtifactNode("SPEC-01", "SPEC", "Cycle A", dependencies=["SPEC-02"]))
    graph.add_node(ArtifactNode("SPEC-02", "SPEC", "Cycle B", dependencies=["SPEC-01"]))

    cycles = graph.detect_cycles()
    assert len(cycles) >= 1

    passed, violations = graph.validate_invariants()
    assert passed is False
    assert any("GRAPH-CARD-001" in v for v in violations)
    assert any("GRAPH-CARD-002" in v for v in violations)
    assert any("GRAPH-CARD-003" in v for v in violations)


def test_scoped_contract_resolver_security_and_sibling_confinement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws_a = tmp_path / "proj-a"
    ws_b = tmp_path / "platform-proj-b"
    (ws_a / "proto").mkdir(parents=True)
    (ws_b / "proto").mkdir(parents=True)
    (ws_b / "proto" / "shared.proto").write_text("message Shared {}", encoding="utf-8")

    # 1. Path traversal pointer rejected
    resolver_strict = ScopedContractResolver(ws_a, allow_sibling_lookup=False)
    assert resolver_strict.resolve("@proj-b:SPEC:../../etc/passwd") is None

    # 2. When allow_sibling_lookup=False, sibling and AIDOC_CONTRACT_PATH are not read
    monkeypatch.setenv("AIDOC_CONTRACT_PATH", str(ws_b))
    node_blocked = resolver_strict.resolve("@proj-b:SPEC:shared")
    assert node_blocked is not None
    assert node_blocked.content.startswith("# Imported Contract Pointer:")

    # 3. When allow_sibling_lookup=True, sibling is resolved with relativized file_path
    resolver_open = ScopedContractResolver(ws_a, allow_sibling_lookup=True)
    node_ok = resolver_open.resolve("@proj-b:SPEC:shared")
    assert node_ok is not None
    assert "message Shared {}" in node_ok.content
    assert node_ok.file_path == "proj-b/proto/shared.proto"


def test_invariant_checker_contract_erosion_blast_warnings_and_git_ref_injection(
    tmp_path: Path,
) -> None:
    sdd_dir = tmp_path / "sdd" / "05_ADR"
    sdd_dir.mkdir(parents=True)
    (sdd_dir / "ADR-01.yaml").write_text(
        "id: ADR-01\nlayer: ADR\ntitle: Core Contract\n", encoding="utf-8"
    )
    (tmp_path / "sdd" / "08_IPLAN").mkdir(parents=True)
    (tmp_path / "sdd" / "08_IPLAN" / "IPLAN-01.yaml").write_text(
        "id: IPLAN-01\nlayer: IPLAN\ntitle: Task\ndepends_on: [ADR-01]\n",
        encoding="utf-8",
    )

    checker = InvariantChecker(tmp_path)

    # Reject malicious git ref option injection
    with pytest.raises(ValidationError, match="Invalid or unsafe git ref"):
        checker.get_changed_files(base_ref="--upload-pack=evil")

    # Fast-path CODE2C modifying proto/*.proto fails
    passed, violations = checker.check_contract_erosion(
        ["proto/service.proto", "src/app.py"], chg_type="CODE2C"
    )
    assert passed is False
    assert len(violations) == 1
    assert "Contract Erosion Violation" in violations[0]

    # SEED2C modifying proto/*.proto is allowed
    passed_seed, _ = checker.check_contract_erosion(["proto/service.proto"], chg_type="SEED2C")
    assert passed_seed is True

    # Blast radius warning when ADR-01 is modified without IPLAN-01
    warnings = checker.check_blast_radius_warnings(["sdd/05_ADR/ADR-01.yaml"])
    assert len(warnings) == 1
    assert "IPLAN-01" in warnings[0]


def test_stack_policy_validator_and_path_confinement(tmp_path: Path) -> None:
    aidoc_dir = tmp_path / ".aidoc"
    aidoc_dir.mkdir()
    (aidoc_dir / "stack-policy.yaml").write_text(
        "boundaries:\n"
        "  strict_allowlist: true\n"
        "  banned_patterns:\n"
        "    - 'eval('\n"
        "dependencies:\n"
        "  allowed_packages:\n"
        "    - name: pydantic-settings\n"
        "    - name: pyyaml\n",
        encoding="utf-8",
    )

    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "bad.py").write_text("x = eval('1 + 1')  # inline comment\n", encoding="utf-8")

    # PEP 503 normalized pydantic_settings is allowed, but requests is disallowed
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\ndependencies = [\n'
        '  "pydantic_settings>=2.0",\n  "requests>=2.31",\n]\n',
        encoding="utf-8",
    )
    (tmp_path / "poetry.lock").write_text("# legacy lockfile\n", encoding="utf-8")

    report = lint_stack_policy(tmp_path)
    assert report["passed"] is False
    v_text = "\n".join(report["violations"])
    assert "Banned pattern 'eval('" in v_text
    assert "Disallowed dependency 'requests'" in v_text
    assert "pydantic_settings" not in v_text
    assert "poetry.lock" in v_text

    # Reject policy_path outside workspace boundary
    validator = StackPolicyValidator(tmp_path)
    with pytest.raises(ValidationError, match="outside allowed boundary"):
        validator.validate(policy_path="../outside-policy.yaml")


@pytest.mark.asyncio
async def test_cli_and_mcp_sdd_context_tools_integration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("ENGRAMORY_ALLOWED_DOCS_ROOT", str(tmp_path))
    monkeypatch.setenv("ENGRAMORY_ENV", "dev")

    proj_dir = tmp_path / "proj-alpha"
    (proj_dir / "sdd" / "01_BRD").mkdir(parents=True)
    (proj_dir / ".aidoc").mkdir(parents=True)

    # Initialize git repo for check-invariants
    subprocess.run(["git", "init"], cwd=proj_dir, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=proj_dir,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=proj_dir,
        check=True,
        capture_output=True,
    )

    (proj_dir / "sdd" / "01_BRD" / "graph.yaml").write_text(
        "schema_version: '2.0-graph'\n"
        "nodes:\n"
        "  - id: BRD-01\n"
        "    layer: BRD\n"
        "    title: Business Goal\n"
        "  - id: EARS-01\n"
        "    layer: EARS\n"
        "    title: Requirement\n"
        "    trace: [BRD-01]\n"
        "  - id: BDD-01\n"
        "    layer: BDD\n"
        "    title: Scenario\n"
        "    trace: [EARS-01]\n"
        "  - id: IPLAN-01\n"
        "    layer: IPLAN\n"
        "    title: Build Feature\n"
        "    trace: [BDD-01]\n"
        "edges: []\n",
        encoding="utf-8",
    )
    (proj_dir / ".aidoc" / "stack-policy.yaml").write_text(
        "boundaries:\n  strict_allowlist: false\n  banned_patterns: []\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=proj_dir, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"], cwd=proj_dir, check=True, capture_output=True
    )

    # 1. CLI: validate-graph --invariants
    rc = cli_main(["--json", "sdd", "validate-graph", "--root", str(proj_dir), "--invariants"])
    assert rc == 0
    val_out = json.loads(capsys.readouterr().out)
    assert val_out["passed"] is True

    # 2. CLI: slice --offline --digest
    rc_slice = cli_main(
        [
            "sdd",
            "slice",
            "--task",
            "IPLAN-01",
            "--root",
            str(proj_dir),
            "--offline",
            "--digest",
        ]
    )
    assert rc_slice == 0
    digest_out = capsys.readouterr().out
    assert "=== AIDOC SLICED CONTEXT: TASK [IPLAN-01] ===" in digest_out

    # 3. CLI: blast-radius --offline
    rc_blast = cli_main(
        [
            "--json",
            "sdd",
            "blast-radius",
            "--doc",
            "BRD-01",
            "--root",
            str(proj_dir),
            "--offline",
        ]
    )
    assert rc_blast == 0
    blast_out = json.loads(capsys.readouterr().out)
    assert blast_out["impact_count"] == 3

    # 4. CLI: check-invariants & lint-policy
    rc_inv = cli_main(
        ["--json", "sdd", "check-invariants", "--root", str(proj_dir), "--base", "HEAD"]
    )
    assert rc_inv == 0
    capsys.readouterr()

    rc_pol = cli_main(["--json", "sdd", "lint-policy", "--root", str(proj_dir)])
    assert rc_pol == 0
    capsys.readouterr()

    # 5. MCP Server: verify _decide audit call & multi-project boundary enforcement
    surface = MagicMock()
    surface._decide = AsyncMock(return_value=None)
    ctx = ActorContext(
        agent_id="agent-1",
        tenant_id="tenant-a",
        project_id="proj-alpha",
        scopes=frozenset({"project"}),
    )
    mcp = McpServer(surface, ctx, auth_enabled=True)

    # Offline digest slice via MCP calls _decide
    res_slice = await mcp.handle_call_tool(
        "sdd_context_slice",
        {
            "task_id": "IPLAN-01",
            "offline": True,
            "format": "digest",
            "workspace_dir": "proj-alpha",
        },
        ctx=ctx,
    )
    assert not res_slice.get("isError")
    assert "AIDOC SLICED CONTEXT" in res_slice["content"][0]["text"]
    surface._decide.assert_awaited()

    # Cross-project access to proj-beta is rejected when auth_enabled=True
    other_dir = tmp_path / "proj-beta"
    other_dir.mkdir()
    res_cross = await mcp.handle_call_tool(
        "sdd_validate_graph",
        {"workspace_dir": str(other_dir), "invariants": True},
        ctx=ctx,
    )
    assert res_cross.get("isError") is True
    assert "outside allowed boundary" in res_cross["content"][0]["text"]


@pytest.mark.asyncio
async def test_multi_segment_sdd_ids_external_pointers_and_review_regressions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ENGRAMORY_ALLOWED_DOCS_ROOT", str(tmp_path))

    ws = tmp_path / "proj-multi"
    (ws / "sdd" / "01_BRD").mkdir(parents=True)
    (ws / "sdd" / "05_ADR").mkdir(parents=True)
    (ws / "sdd" / "08_IPLAN").mkdir(parents=True)
    (ws / ".aidoc").mkdir(parents=True)
    (ws / "src").mkdir(parents=True)

    # 1. Multi-segment SDD-GRAPH v2.0 IDs + compensated_by + authorized_by + external pointer
    (ws / "sdd" / "01_BRD" / "graph.yaml").write_text(
        "schema_version: '2.0-graph'\n"
        "nodes:\n"
        "  - id: BRD.01.01.a1b2\n"
        "    layer: BRD\n"
        "    properties:\n"
        "      title: Goal One\n"
        "  - id: BRD.01.02.c3d4\n"
        "    layer: BRD\n"
        "    title: Goal Two\n"
        "    trace: [BRD.01.01.a1b2]\n"
        "  - id: EARS.01.01\n"
        "    layer: EARS\n"
        "    title: Sub-Requirement A\n"
        "    trace: [BRD.01.01.a1b2, '@remote:EARS:ext_auth']\n"
        "  - id: EARS.01.02\n"
        "    layer: EARS\n"
        "    title: Sub-Requirement B\n"
        "    trace: [BRD.01.02.c3d4]\n"
        "  - id: BDD.01.01\n"
        "    layer: BDD\n"
        "    title: Verifier A\n"
        "    trace: [EARS.01.01]\n"
        "  - id: BDD.01.02\n"
        "    layer: BDD\n"
        "    title: Verifier B\n"
        "    trace: [EARS.01.02]\n"
        "  - id: CHG.01\n"
        "    layer: CHG\n"
        "    type: change_request\n"
        "    title: Authorizing Change\n"
        "  - id: TASK_01\n"
        "    layer: TASK\n"
        "    title: Task One\n"
        "    trace: [BDD.01.01]\n"
        "  - id: TASK_02\n"
        "    layer: TASK\n"
        "    title: Task Two\n"
        "    trace: [BDD.01.02, TASK_01]\n"
        "edges:\n"
        "  - from: TASK_01\n"
        "    to: TASK_02\n"
        "    relation: compensated_by\n"
        "  - from: CHG.01\n"
        "    to: TASK_02\n"
        "    relation: authorized_by\n",
        encoding="utf-8",
    )

    (ws / "sdd" / "05_ADR" / "ADR-01_foundation.yaml").write_text(
        "id: ADR-01\nlayer: ADR\ntitle: Foundation ADR\n",
        encoding="utf-8",
    )
    (ws / "sdd" / "08_IPLAN" / "IPLAN-01_impl.md").write_text(
        "---\n"
        "id: IPLAN-01\n"
        "layer: IPLAN\n"
        "depends_on: [ADR-01, TASK_02]\n"
        "---\n"
        "Contact maintainer at dev@example.com or see @spec: BDD.01.02.\n",
        encoding="utf-8",
    )

    # Validate graph & invariants: external @remote:EARS:ext_auth and compensated_by do not fail
    val = validate_sdd_graph(ws, check_invariants=True)
    assert val["passed"] is True, f"Unexpected failure: {val}"

    # Multi-segment IDs do not collide in slice_local_context
    s_res = slice_local_context(ws, task_id="IPLAN-01", depth=8)
    anc_ids = {a["id"] for a in s_res["ancestors"]}
    assert {
        "ADR-01",
        "TASK_01",
        "TASK_02",
        "CHG.01",
        "BDD.01.01",
        "BDD.01.02",
        "EARS.01.01",
        "EARS.01.02",
        "BRD.01.01.a1b2",
        "BRD.01.02.c3d4",
    }.issubset(anc_ids)
    # Email address dev@example.com was not parsed as a @example.com pointer
    assert not any("example.com" in i for i in anc_ids)

    # Slugged filename ADR-01_foundation.yaml and multi-node graph.yaml trigger blast warnings
    checker = InvariantChecker(ws)
    w_slug = checker.check_blast_radius_warnings(["sdd/05_ADR/ADR-01_foundation.yaml"])
    assert len(w_slug) == 1
    assert "IPLAN-01" in w_slug[0]

    w_graph = checker.check_blast_radius_warnings(["sdd/01_BRD/graph.yaml"])
    assert any("IPLAN-01" in w for w in w_graph)

    # StackPolicyValidator ignores banned patterns in docstrings and empty [project].dependencies
    (ws / ".aidoc" / "stack-policy.yaml").write_text(
        "boundaries:\n"
        "  strict_allowlist: true\n"
        "  banned_patterns:\n"
        "    - 'eval('\n"
        "dependencies:\n"
        "  allowed_packages:\n"
        "    - name: pyyaml\n",
        encoding="utf-8",
    )
    (ws / "src" / "clean.py").write_text(
        '"""Module docstring mentioning eval( safely."""\n\n'
        "def helper() -> int:\n"
        '    """Function docstring with eval( example."""\n'
        "    return 42\n",
        encoding="utf-8",
    )
    (ws / "pyproject.toml").write_text(
        '[project]\nname = "clean"\ndependencies = []\n\n'
        '[tool.hatch.envs.test]\ndependencies = ["pytest"]\n',
        encoding="utf-8",
    )
    pol_res = lint_stack_policy(ws)
    assert pol_res["passed"] is True, f"Violations: {pol_res['violations']}"

    # MCP online-empty fallback to workspace_dir
    surface = MagicMock()
    surface._decide = AsyncMock(return_value=None)
    surface.context_slice = AsyncMock(return_value=[])
    surface.blast_radius = AsyncMock(return_value=[])
    ctx = ActorContext(
        agent_id="agent-1",
        tenant_id="tenant-a",
        project_id="proj-multi",
        scopes=frozenset({"project"}),
    )
    mcp = McpServer(surface, ctx, auth_enabled=True)
    mcp_slice = await mcp.handle_call_tool(
        "sdd_context_slice",
        {"task_id": "IPLAN-01", "workspace_dir": "proj-multi"},
        ctx=ctx,
    )
    parsed_slice = json.loads(mcp_slice["content"][0]["text"])
    assert any(a["id"] == "ADR-01" for a in parsed_slice)
