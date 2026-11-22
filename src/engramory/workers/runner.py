"""Background distillation runner daemon (PLAN-005 Phase 4).

Periodically discovers unreflected episodes across active agents, batches and embeds them
via LLMPort, persists vector projections into VectorPort, and touches a heartbeat file.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import os
import signal
import sys
import tempfile
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from engramory.access.poisoning_guard import MemoryPoisoningError
from engramory.core.experience import EXPERIENCE_EVENT_KINDS, ExperienceEvent
from engramory.core.models import ValidationError
from engramory.core.repository import Repository
from engramory.ports import EventsPort, LLMPort, StoragePort, VectorPort
from engramory.workers.distillation import consolidate, reflect

logger = logging.getLogger("engramory.worker")
DEFAULT_INTERVAL_SEC = 300
DEFAULT_EVENTS_TOPIC = "engramory.events.>"
WORKER_DLQ_TOPIC = "engramory.dlq.worker"
_EVENT_KIND_PREFIXES = frozenset(k.split(".", 1)[0] for k in EXPERIENCE_EVENT_KINDS)


def extract_subject_tenant(subject: str) -> str | None:
    """Extract the verified tenant namespace from a NATS experience subject, or None."""
    clean = (subject or "").strip()
    if not clean:
        return None
    for prefix in ("engramory.events.", "xp.engramory.experience."):
        if not clean.startswith(prefix):
            continue
        rest = clean[len(prefix) :]
        if rest.startswith("tenants."):
            tenant_tok = rest[len("tenants.") :].split(".", 1)[0].strip()
            return tenant_tok or None
        if rest in EXPERIENCE_EVENT_KINDS or "." not in rest:
            return None
        cand_tenant, cand_kind = rest.split(".", 1)
        if cand_kind in EXPERIENCE_EVENT_KINDS and cand_tenant.strip():
            return cand_tenant.strip()
        if cand_tenant not in _EVENT_KIND_PREFIXES and cand_tenant.strip():
            return cand_tenant.strip()
        return None
    return None


async def _publish_dlq(
    events: EventsPort | None,
    *,
    stage: str,
    error: Exception,
    payload: Mapping[str, Any] | None = None,
    tenant_id: str | None = None,
    agent_id: str | None = None,
) -> None:
    if events is None:
        return
    envelope: dict[str, Any] = {
        "stage": stage,
        "error_type": type(error).__name__,
        "error": str(error)[:500],
        "tenant_id": tenant_id,
        "agent_id": agent_id,
    }
    if isinstance(payload, Mapping):
        envelope["event_id"] = payload.get("event_id")
    try:
        await events.publish(topic=WORKER_DLQ_TOPIC, event=envelope)
    except Exception as dlq_exc:  # noqa: BLE001  # reason: DLQ emit is best-effort and must not abort worker loop
        logger.debug("Worker DLQ emit skipped: %s", dlq_exc)


HEARTBEAT_PATH = Path(
    os.environ.get(
        "ENGRAMORY_HEARTBEAT_PATH",
        os.path.join(tempfile.gettempdir(), "engramory-worker.heartbeat"),
    )
)
HEARTBEAT_TICK_MAX_SEC = 60.0


def _touch_heartbeat(heartbeat_path: Path) -> None:
    """Write fresh epoch timestamp for Docker worker HEALTHCHECK."""
    try:
        heartbeat_path.write_text(f"{time.time()}\n", encoding="utf-8")
    except OSError as err:
        logger.warning("Could not write heartbeat to %s: %s", heartbeat_path, err)


async def run_worker_loop(
    repo: Repository,
    *,
    llm: LLMPort | None = None,
    vector: VectorPort | None = None,
    events: EventsPort | None = None,
    storage: StoragePort | None = None,
    events_topic: str = DEFAULT_EVENTS_TOPIC,
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

    if storage is not None and hasattr(storage, "reconcile_truncated_blobs"):
        try:
            reconciled = await storage.reconcile_truncated_blobs(tenant_id=tenant_id)
            if reconciled:
                logger.warning(
                    "Startup CAS reconciliation marked %d truncated UNLOGGED blob(s) as failed",
                    reconciled,
                )
        except Exception as rec_exc:
            logger.warning("Startup CAS blob reconciliation skipped: %s", rec_exc)

    _touch_heartbeat(heartbeat_path)

    try:
        while not shutdown_event.is_set():
            start_time = time.monotonic()
            pass_ok = True
            try:
                # 0. Drain pending experience events from EventsPort if configured
                if events is not None:
                    ingested = 0
                    stream = events.subscribe(topic=events_topic)
                    try:
                        async for raw_ev in stream:
                            try:
                                subj = raw_ev.get("_nats_subject")
                                await ingest_experience_event(
                                    repo,
                                    raw_ev,
                                    enforce_tenant_id=tenant_id,
                                    subject=str(subj) if isinstance(subj, str) else None,
                                    require_subject_tenant=(tenant_id is None),
                                )
                                ingested += 1
                            except (
                                ValidationError,
                                MemoryPoisoningError,
                                ValueError,
                            ) as ev_exc:
                                logger.warning(
                                    "Skipping invalid or poisoned experience event on %s: %s",
                                    events_topic,
                                    ev_exc,
                                )
                                await _publish_dlq(
                                    events,
                                    stage="event_ingest",
                                    error=ev_exc,
                                    payload=raw_ev,
                                    tenant_id=tenant_id,
                                )
                            except Exception as ev_exc:  # noqa: BLE001  # reason: isolate per-event storage failure to DLQ
                                logger.warning(
                                    "Event ingestion failed on %s: %s", events_topic, ev_exc
                                )
                                await _publish_dlq(
                                    events,
                                    stage="event_ingest",
                                    error=ev_exc,
                                    payload=raw_ev,
                                    tenant_id=tenant_id,
                                )
                    finally:
                        aclose_fn = getattr(stream, "aclose", None)
                        if callable(aclose_fn):
                            res_aclose = aclose_fn()
                            if inspect.isawaitable(res_aclose):
                                await res_aclose
                    if ingested:
                        logger.info(
                            "Ingested %d experience event(s) from %s",
                            ingested,
                            events_topic,
                        )

                # 1. Discover pending work (isolated per-tenant/per-agent)
                targets = await repo.get_active_agents_with_unreflected_episodes(
                    tenant_id=tenant_id
                )
                total_created = 0
                consolidated_tenants: set[str] = set()
                for tid, aid in targets:
                    try:
                        created = await reflect(
                            repo,
                            tenant_id=tid,
                            agent_id=aid,
                            llm=llm,
                            vector=vector,
                        )
                        total_created += created
                        await consolidate(repo, tenant_id=tid, agent_id=aid, scope="agent")
                        consolidated_tenants.add(tid)
                    except Exception as agent_exc:  # noqa: BLE001  # reason: single-agent failure must not starve remaining tenants
                        logger.warning(
                            "Agent distillation failed for (%s, %s): %s",
                            tid,
                            aid,
                            agent_exc,
                        )
                        await _publish_dlq(
                            events,
                            stage="reflect_agent",
                            error=agent_exc,
                            tenant_id=tid,
                            agent_id=aid,
                        )

                if tenant_id is not None and tenant_id not in consolidated_tenants:
                    try:
                        await consolidate(repo, tenant_id=tenant_id)
                    except Exception as cons_exc:  # noqa: BLE001  # reason: keep worker loop resilient across passes
                        logger.debug("Periodic consolidation skipped: %s", cons_exc)

                if targets:
                    logger.info(
                        "Reflection pass complete: %d memories created across %d agents in %.2fs",
                        total_created,
                        len(targets),
                        time.monotonic() - start_time,
                    )

                # 1b. Periodic CAS orphan chunk garbage collection (GAP-06 / C10)
                if storage is not None and hasattr(storage, "gc_orphan_chunks"):
                    try:
                        await storage.gc_orphan_chunks(
                            older_than_seconds=7200,
                            tenant_id=tenant_id,
                        )
                    except Exception as gc_exc:
                        logger.debug("CAS orphan chunk GC skipped: %s", gc_exc)

            except Exception as exc:
                pass_ok = False
                logger.error("Error during distillation pass: %s", exc, exc_info=True)
            finally:
                # 2. Refresh heartbeat file for Docker HEALTHCHECK when pass succeeded
                if pass_ok:
                    _touch_heartbeat(heartbeat_path)

            # 3. Wait for interval or shutdown signal while keeping heartbeat fresh
            deadline = time.monotonic() + interval
            while not shutdown_event.is_set():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    await asyncio.wait_for(
                        shutdown_event.wait(),
                        timeout=min(HEARTBEAT_TICK_MAX_SEC, remaining),
                    )
                except TimeoutError:
                    if pass_ok:
                        _touch_heartbeat(heartbeat_path)
    finally:
        for adapter in (events, storage):
            if adapter is not None and hasattr(adapter, "close"):
                try:
                    res = adapter.close()
                    if inspect.isawaitable(res):
                        await res
                except Exception as exc:  # noqa: BLE001  # reason: cleanup must not mask worker exit
                    logger.debug("Worker adapter close error ignored: %s", exc)

    logger.info("Engramory worker shutdown complete.")


def start_daemon(
    repo: Repository,
    *,
    llm: LLMPort | None = None,
    vector: VectorPort | None = None,
    events: EventsPort | None = None,
    storage: StoragePort | None = None,
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
                events=events,
                storage=storage,
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


async def ingest_experience_event(
    repo: Repository,
    raw_event: Mapping[str, Any] | ExperienceEvent,
    *,
    enforce_tenant_id: str | None = None,
    subject: str | None = None,
    require_subject_tenant: bool = False,
) -> str:
    """Hydrate, screen, and persist a canonical ExperienceEvent as an L1 Episode.

    When ``enforce_tenant_id`` is provided (ADR-07 tenant wall), rejects any
    event whose ``tenant_id`` does not match ``enforce_tenant_id``. In multi-tenant
    mode (``require_subject_tenant=True``), rejects any event lacking a broker-verified
    subject tenant matching ``event.tenant_id``.
    """
    event = (
        raw_event
        if isinstance(raw_event, ExperienceEvent)
        else ExperienceEvent.from_dict(raw_event)
    )
    if enforce_tenant_id is not None and event.tenant_id != enforce_tenant_id:
        raise ValidationError(
            f"ExperienceEvent tenant_id {event.tenant_id!r} violates enforced tenant wall "
            f"{enforce_tenant_id!r} (ADR-07)"
        )
    subj_str = (
        subject
        if subject is not None
        else (
            str(raw_event.get("_nats_subject"))
            if isinstance(raw_event, Mapping) and raw_event.get("_nats_subject")
            else None
        )
    )
    subj_tenant = extract_subject_tenant(subj_str or "")
    if require_subject_tenant:
        if not subj_tenant or subj_tenant != event.tenant_id:
            raise ValidationError(
                f"Multi-tenant ExperienceEvent {event.tenant_id!r} missing matching verified "
                f"NATS subject namespace ({subj_str!r})"
            )
    elif (
        enforce_tenant_id is not None
        and subj_tenant is not None
        and subj_tenant != enforce_tenant_id
    ):
        raise ValidationError(
            f"Subject tenant {subj_tenant!r} mismatches pinned worker tenant {enforce_tenant_id!r}"
        )
    episode = event.to_episode()
    return await repo.add_episode(episode)
