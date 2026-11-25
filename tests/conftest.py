"""Shared ephemeral PG18 + Neo4j test fixtures (integration + conformance suites).

Resolution order:
1. ``ENGRAMORY_TEST_DSN`` env var — use an existing database (migrations are
   applied to it; use a throwaway DB).
2. Ephemeral ``pgvector/pgvector:pg18`` container on a free localhost port
   (docker required; auto-removed on teardown).
3. Neither available -> the whole integration suite skips.

The compose stack's 5432 is deliberately NOT used: the host port may be owned
by an unrelated service (observed on the dev host), and tests must never touch
a shared database.
"""

from __future__ import annotations

import os
import socket
import subprocess
import time
import urllib.parse
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

PG_IMAGE = "pgvector/pgvector:pg18"
NEO4J_IMAGE = "neo4j:5.26-community"

MIGRATIONS = Path(__file__).resolve().parents[1] / "db" / "migrations"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _ensure_rls_roles(dsn: str) -> None:
    """Create non-superuser NOBYPASSRLS roles for live RLS testing when connected as superuser."""
    import psycopg

    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(
            """
            DO $$
            BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'engramory_app') THEN
                    CREATE ROLE engramory_app NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT
                        NOBYPASSRLS LOGIN PASSWORD 'it_app_pw';
                ELSE
                    ALTER ROLE engramory_app WITH NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT
                        NOBYPASSRLS LOGIN PASSWORD 'it_app_pw';
                END IF;
                IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'engramory_worker') THEN
                    CREATE ROLE engramory_worker NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT
                        NOBYPASSRLS LOGIN PASSWORD 'it_worker_pw';
                ELSE
                    ALTER ROLE engramory_worker WITH NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT
                        NOBYPASSRLS LOGIN PASSWORD 'it_worker_pw';
                END IF;
            END $$;
            """
        )


def _replace_dsn_credentials(dsn: str, username: str, password: str) -> str:
    parsed = urllib.parse.urlparse(dsn)
    host = parsed.hostname or "127.0.0.1"
    port = f":{parsed.port}" if parsed.port else ""
    u_enc = urllib.parse.quote(username, safe="")
    p_enc = urllib.parse.quote(password, safe="")
    netloc = f"{u_enc}:{p_enc}@{host}{port}"
    return urllib.parse.urlunparse(parsed._replace(netloc=netloc))


def _apply_migrations(dsn: str) -> None:
    import psycopg

    paths = sorted(MIGRATIONS.glob("*.sql"))
    if not paths:  # a broken path must fail loudly, not yield an empty schema
        raise RuntimeError(f"no migrations found at {MIGRATIONS} — fixture path broken")
    try:
        _ensure_rls_roles(dsn)
    except psycopg.Error:
        pass
    with psycopg.connect(dsn, autocommit=True) as conn:
        for path in paths:
            conn.execute(path.read_text())  # type: ignore[arg-type]


def _container_running(name: str) -> bool:
    out = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Running}}", name], capture_output=True
    )
    return out.returncode == 0 and out.stdout.strip() == b"true"


def _container_logs(name: str) -> str:
    out = subprocess.run(["docker", "logs", name], capture_output=True)
    return (out.stdout + out.stderr).decode(errors="replace")


@pytest.fixture(scope="session")
def pg_dsn() -> Iterator[str]:
    env_dsn = os.environ.get("ENGRAMORY_TEST_DSN")
    if env_dsn:
        _apply_migrations(env_dsn)
        yield env_dsn
        return

    if subprocess.run(["docker", "info"], capture_output=True).returncode != 0:
        pytest.skip("integration: docker unavailable — reason: requires running docker daemon")

    port = _free_port()  # TOCTOU window until docker binds it — acceptable in tests
    name = f"engramory-it-{uuid.uuid4().hex[:8]}"
    try:
        subprocess.run(
            [
                "docker",
                "run",
                "-d",
                "--name",
                name,
                "-e",
                "POSTGRES_PASSWORD=it",
                "-e",
                "POSTGRES_USER=it",
                "-e",
                "POSTGRES_DB=it",
                "-p",
                f"127.0.0.1:{port}:5432",
                PG_IMAGE,
            ],
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"docker run failed (rc={exc.returncode}): {exc.stderr.decode(errors='replace')}"
        ) from exc
    dsn = f"postgresql://it:it@127.0.0.1:{port}/it"
    try:
        import psycopg

        deadline = time.monotonic() + 60
        while True:
            try:
                psycopg.connect(dsn, connect_timeout=2).close()
                break
            except psycopg.OperationalError as exc:
                # Bail early on a dead container and preserve its logs — the
                # only diagnostics — BEFORE teardown removes them.
                if not _container_running(name) or time.monotonic() > deadline:
                    raise RuntimeError(
                        f"postgres container not ready; logs:\n{_container_logs(name)}"
                    ) from exc
                time.sleep(0.5)
        _apply_migrations(dsn)
        yield dsn
    finally:
        rm = subprocess.run(["docker", "rm", "-f", name], capture_output=True)
        if rm.returncode != 0:
            import warnings

            warnings.warn(
                f"leaked container {name}: {rm.stderr.decode(errors='replace')}",
                stacklevel=1,
            )


@pytest.fixture(scope="session")
def pg_app_dsn(pg_dsn: str) -> str:
    """Non-superuser NOBYPASSRLS DSN bound to the `engramory_app` role."""
    _ensure_rls_roles(pg_dsn)
    app_dsn = _replace_dsn_credentials(pg_dsn, "engramory_app", "it_app_pw")
    return app_dsn


@pytest.fixture(scope="session")
def pg_worker_dsn(pg_dsn: str) -> str:
    """Non-superuser NOBYPASSRLS DSN bound to the `engramory_worker` sweep role."""
    _ensure_rls_roles(pg_dsn)
    worker_dsn = _replace_dsn_credentials(pg_dsn, "engramory_worker", "it_worker_pw")
    return worker_dsn


def _neo4j_reachable(host: str = "127.0.0.1", port: int = 7687) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1.0):
            return True
    except OSError:
        return False


def _wait_neo4j_bolt(uri: str, auth: tuple[str, str], timeout_sec: float = 5.0) -> bool:
    from neo4j import GraphDatabase

    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        driver = None
        try:
            driver = GraphDatabase.driver(uri, auth=auth)
            driver.verify_connectivity()
            return True
        except Exception:  # noqa: BLE001  # reason: retry while ephemeral neo4j initializes Bolt listener
            time.sleep(0.5)
        finally:
            if driver is not None:
                driver.close()
    return False


@pytest.fixture(scope="session")
def _neo4j_endpoint() -> Iterator[tuple[str, tuple[str, str]]]:
    explicit_uri = os.getenv("NEO4J_URI")
    if explicit_uri:
        auth = (os.getenv("NEO4J_USER", "neo4j"), os.getenv("NEO4J_PASSWORD", "change_me"))
        parsed = urllib.parse.urlparse(explicit_uri)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 7687
        if not _neo4j_reachable(host, port):
            pytest.skip(f"neo4j offline ({explicit_uri}) — reason: unreachable")
        yield explicit_uri, auth
        return

    if os.getenv("NEO4J_PASSWORD") is not None and _neo4j_reachable("127.0.0.1", 7687):
        auth = (os.getenv("NEO4J_USER", "neo4j"), os.getenv("NEO4J_PASSWORD", "change_me"))
        yield "bolt://127.0.0.1:7687", auth
        return

    if subprocess.run(["docker", "info"], capture_output=True).returncode != 0:
        pytest.skip("neo4j/docker unavailable — reason: requires neo4j or docker")

    port = _free_port()
    name = f"engramory-neo4j-it-{uuid.uuid4().hex[:8]}"
    auth = ("neo4j", "test_password_engramory")
    uri = f"bolt://127.0.0.1:{port}"
    try:
        subprocess.run(
            [
                "docker",
                "run",
                "-d",
                "--name",
                name,
                "-e",
                f"NEO4J_AUTH={auth[0]}/{auth[1]}",
                "-p",
                f"127.0.0.1:{port}:7687",
                NEO4J_IMAGE,
            ],
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"neo4j docker run failed (rc={exc.returncode}): {exc.stderr.decode(errors='replace')}"
        ) from exc

    try:
        deadline = time.monotonic() + 90
        while not _wait_neo4j_bolt(uri, auth, timeout_sec=5.0):
            if not _container_running(name) or time.monotonic() > deadline:
                raise RuntimeError(f"neo4j container not ready; logs:\n{_container_logs(name)}")
        yield uri, auth
    finally:
        rm = subprocess.run(["docker", "rm", "-f", name], capture_output=True)
        if rm.returncode != 0:
            import warnings

            warnings.warn(
                f"leaked container {name}: {rm.stderr.decode(errors='replace')}",
                stacklevel=1,
            )


@pytest_asyncio.fixture
async def neo4j_adapter(_neo4j_endpoint: tuple[str, tuple[str, str]]) -> AsyncIterator[Any]:
    from engramory.adapters.dev.graph_neo4j import Neo4jGraphAdapter

    uri, auth = _neo4j_endpoint
    adapter = Neo4jGraphAdapter(uri=uri, auth=auth)
    try:
        yield adapter
    finally:
        await adapter.close()


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Automatically tag integration/e2e/conformance tests with `@pytest.mark.integration`."""
    integration_dirs = ("integration", "e2e", "conformance")
    for item in items:
        fspath_str = str(item.path)
        parts = Path(fspath_str).parts
        fixturenames = getattr(item, "fixturenames", ())
        if (
            any(d in parts for d in integration_dirs)
            or "pg_dsn" in fixturenames
            or "neo4j_adapter" in fixturenames
        ):
            item.add_marker(pytest.mark.integration)
