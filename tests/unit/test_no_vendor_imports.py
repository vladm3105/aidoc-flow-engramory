"""Architecture guard (TDD-06.04.a100): core imports no vendor SDKs, only ports.

Reconciled rule (SPEC-06 / ADR-01, post PLAN-001): the Postgres driver
(psycopg) is spine infrastructure and IS allowed in ``engramory.core``;
every other vendor SDK belongs behind a port, in ``engramory.adapters``.
The ports package itself must stay stdlib-only.
"""
from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "engramory"

# SDKs that must never appear outside engramory.adapters.
VENDOR_ROOTS = {
    "redis", "neo4j", "boto3", "botocore", "google", "azure", "openai",
    "anthropic", "litellm", "qdrant_client", "pinecone", "minio", "keycloak",
}
SPINE = {"psycopg"}  # allowed in core only (ADR-01/SPEC-06 carve-out)


def _import_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
                if alias.name.startswith("engramory.adapters"):
                    roots.add("engramory.adapters")  # dotted form: import engramory.adapters.x
        elif isinstance(node, ast.ImportFrom):
            if node.level > 0:
                # Relative import: `from ..adapters... import X` crosses the
                # boundary just as much as the absolute form does.
                if node.module and node.module.split(".")[0] == "adapters":
                    roots.add("engramory.adapters")
                continue
            if node.module:
                roots.add(node.module.split(".")[0])
                if node.module.startswith("engramory.adapters"):
                    roots.add("engramory.adapters")
    return roots


def _package_files(package: str) -> list[Path]:
    files = sorted((SRC / package).rglob("*.py"))
    assert files, f"package {package} has no modules"
    return files


def test_ports_are_stdlib_only() -> None:
    for path in _package_files("ports"):
        roots = _import_roots(path)
        offenders = roots & (VENDOR_ROOTS | SPINE | {"engramory.adapters"})
        assert not offenders, f"ports must be stdlib-only; {path.name} imports {offenders}"


def test_core_has_no_vendor_imports() -> None:
    """core/workers/mcp: no vendor SDKs, no adapter imports; psycopg only in core."""
    for package in ("core", "workers", "mcp"):
        for path in _package_files(package):
            roots = _import_roots(path)
            offenders = roots & VENDOR_ROOTS
            assert not offenders, f"{package}/{path.name} imports vendor SDK {offenders}"
            assert "engramory.adapters" not in roots, (
                f"{package}/{path.name} imports an adapter — depend on ports instead"
            )
            if package != "core":
                assert not (roots & SPINE), (
                    f"{package}/{path.name} imports psycopg — the spine carve-out is core-only"
                )
