"""Shared ephemeral-Postgres fixture (integration + conformance suites).

Resolution order:
1. ``ENGRAMORY_TEST_DSN`` env var — use an existing database (migrations are
   applied to it; use a throwaway DB).
2. Ephemeral ``pgvector/pgvector:pg16`` container on a free localhost port
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
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

MIGRATIONS = Path(__file__).resolve().parents[1] / "db" / "migrations"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _apply_migrations(dsn: str) -> None:
    import psycopg

    paths = sorted(MIGRATIONS.glob("*.sql"))
    if not paths:  # a broken path must fail loudly, not yield an empty schema
        raise RuntimeError(f"no migrations found at {MIGRATIONS} — fixture path broken")
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

    if os.environ.get("CI"):
        # CI runs must not depend on a Docker Hub pull per run (rate limits /
        # outages would fail, not skip). Enable in CI by adding a `services:`
        # postgres container to ci.yml and exporting ENGRAMORY_TEST_DSN.
        pytest.skip("integration: CI without ENGRAMORY_TEST_DSN — see conftest note")

    if subprocess.run(["docker", "info"], capture_output=True).returncode != 0:
        pytest.skip("integration: no ENGRAMORY_TEST_DSN and docker unavailable")

    port = _free_port()  # TOCTOU window until docker binds it — acceptable in tests
    name = f"engramory-it-{uuid.uuid4().hex[:8]}"
    try:
        subprocess.run(
            [
                "docker", "run", "-d", "--name", name,
                "-e", "POSTGRES_PASSWORD=it", "-e", "POSTGRES_USER=it",
                "-e", "POSTGRES_DB=it",
                "-p", f"127.0.0.1:{port}:5432",
                "pgvector/pgvector:pg16",
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
