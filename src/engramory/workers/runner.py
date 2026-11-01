"""Background distillation runner daemon (PLAN-005 Phase 4).

Periodically discovers unreflected episodes across active agents, batches and embeds them
via LLMPort, persists vector projections into VectorPort, and touches a heartbeat file.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
import time
from pathlib import Path

from engramory.core.repository import Repository
from engramory.ports import LLMPort, VectorPort
from engramory.workers.distillation import reflect

logger = logging.getLogger("engramory.worker")
DEFAULT_INTERVAL_SEC = 300
HEARTBEAT_PATH = Path("/tmp/engramory-worker.heartbeat")


async def run_worker_loop(
    repo: Repository,
    *,
    llm: LLMPort | None = None,
    vector: VectorPort | None = None,
    interval_sec: float | None = None,
    tenant_id: str | None = None,
    heartbeat_path: Path = HEARTBEAT_PATH,
) -> None:
    """Run continuous distillation loop until SIGTERM or SIGINT is received."""
    interval = (
        interval_sec
        if interval_sec is not None
        else float(os.getenv("ENGRAMORY_REFLECT_INTERVAL_SEC", DEFAULT_INTERVAL_SEC))
    )

    shutdown_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _on_shutdown_signal(sig_name: str) -> None:
        logger.info("Received %s signal, stopping background worker gracefully...", sig_name)
        shutdown_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _on_shutdown_signal, sig.name)
        except (NotImplementedError, RuntimeError):
            # Not all platforms/loops support signal handlers
            pass

    logger.info(
        "Engramory worker started (interval=%.1fs, tenant=%s)",
        interval,
        tenant_id or "all-active",
    )

    while not shutdown_event.is_set():
        start_time = time.monotonic()
        try:
            # 1. Discover pending work
            targets = await repo.get_active_agents_with_unreflected_episodes(tenant_id=tenant_id)
            total_created = 0
            for tid, aid in targets:
                created = await reflect(
                    repo,
                    tenant_id=tid,
                    agent_id=aid,
                    llm=llm,
                    vector=vector,
                )
                total_created += created

            if targets:
                logger.info(
                    "Reflection pass complete: %d memories created across %d agents in %.2fs",
                    total_created,
                    len(targets),
                    time.monotonic() - start_time,
                )

            # 2. Touch heartbeat file for Docker HEALTHCHECK
            try:
                heartbeat_path.write_text(f"{time.time()}\n", encoding="utf-8")
            except OSError as err:
                logger.warning("Could not write heartbeat to %s: %s", heartbeat_path, err)

        except Exception as exc:
            logger.error("Error during distillation pass: %s", exc, exc_info=True)

        # 3. Wait for interval or shutdown signal
        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=interval)
        except TimeoutError:
            pass

    logger.info("Engramory worker shutdown complete.")


def start_daemon(
    repo: Repository,
    *,
    llm: LLMPort | None = None,
    vector: VectorPort | None = None,
    interval_sec: float | None = None,
    tenant_id: str | None = None,
) -> int:
    """Synchronous entry point for daemon."""
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    try:
        asyncio.run(
            run_worker_loop(
                repo,
                llm=llm,
                vector=vector,
                interval_sec=interval_sec,
                tenant_id=tenant_id,
            )
        )
        return 0
    except (KeyboardInterrupt, SystemExit):
        return 0
    except Exception as exc:
        print(f"Worker daemon failed: {exc}", file=sys.stderr)
        return 1
