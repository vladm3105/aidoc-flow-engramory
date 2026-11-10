"""Canonical agent experience events, deterministic UUIDv5 deduplication,
OriginProvenance, and closed-loop learning effectiveness contracts (PLAN-020 / CHG-006).

Absorbs the four core domain contracts from legacy aidoc-flow-interlog into
Engramory's stdlib-only core domain without GitTransport coupling.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from engramory.access.poisoning_guard import inspect_memory_write
from engramory.core.models import Episode, ValidationError

# Matches legacy aidoc-flow-interlog DATA-INTERCHANGE.md §4:
# uuid5(NAMESPACE_URL, "https://github.com/vladm3105/aidoc-flow-interlog#experience-event")
EXPERIENCE_EVENT_ID_NS = uuid.UUID("9f9cccb2-2bac-5a91-981a-6c897f147444")

EXPERIENCE_EVENT_KINDS: frozenset[str] = frozenset(
    {
        "plan.authored",
        "review.verdict",
        "execution.completed",
        "gate.failed",
        "decision.recorded",
        "lesson.proposed",
        "feedback.received",
        "feedback.routed",
        "feedback.quarantined",
    }
)

MAX_EXPERIENCE_CONTENT_BYTES = 65536
_W3C_TRACE_ID_RE = re.compile(r"^[0-9a-f]{32}$")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(message)


def _frozen_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType(dict(value))


@dataclass(frozen=True, slots=True)
class OriginProvenance:
    """Source provenance linking an experience event or distilled memory to its origin.

    Requires ``repo`` plus at least one concrete artifact/execution anchor
    (``commit``, ``pr``, ``plan_id``, ``chg_id``, or ``run_id``). Optionally
    carries a 32-char lowercase hex W3C ``trace_id`` for cross-plane correlation
    with platform-observability-monitoring (HyperDX / ClickHouse / Langfuse).
    """

    repo: str
    commit: str | None = None
    pr: int | None = None
    plan_id: str | None = None
    chg_id: str | None = None
    run_id: str | None = None
    trace_id: str | None = None

    def __post_init__(self) -> None:
        _require(
            isinstance(self.repo, str) and bool(self.repo.strip()),
            "OriginProvenance.repo is required",
        )
        has_anchor = any(
            (
                bool(self.commit and self.commit.strip()),
                self.pr is not None,
                bool(self.plan_id and self.plan_id.strip()),
                bool(self.chg_id and self.chg_id.strip()),
                bool(self.run_id and self.run_id.strip()),
            )
        )
        _require(
            has_anchor,
            "OriginProvenance requires at least one of commit, pr, plan_id, chg_id, or run_id",
        )
        if self.pr is not None:
            _require(
                isinstance(self.pr, int) and not isinstance(self.pr, bool) and self.pr >= 1,
                "OriginProvenance.pr must be a positive integer when set",
            )
        if self.trace_id is not None:
            _require(
                isinstance(self.trace_id, str) and bool(_W3C_TRACE_ID_RE.match(self.trace_id)),
                "OriginProvenance.trace_id must be a 32-character lowercase hex W3C trace_id",
            )

    def to_dict(self) -> dict[str, Any]:
        """Serialize non-null provenance fields to a plain dictionary."""
        out: dict[str, Any] = {"repo": self.repo}
        if self.commit is not None:
            out["commit"] = self.commit
        if self.pr is not None:
            out["pr"] = self.pr
        if self.plan_id is not None:
            out["plan_id"] = self.plan_id
        if self.chg_id is not None:
            out["chg_id"] = self.chg_id
        if self.run_id is not None:
            out["run_id"] = self.run_id
        if self.trace_id is not None:
            out["trace_id"] = self.trace_id
        return out

    @classmethod
    def from_dict(
        cls,
        data: Mapping[str, Any],
        *,
        fallback_trace_id: str | None = None,
    ) -> OriginProvenance:
        """Hydrate OriginProvenance from a dictionary (or Interlog origin block)."""
        _require(isinstance(data, Mapping), "OriginProvenance input must be a mapping")
        raw_pr = data.get("pr")
        if isinstance(raw_pr, str) and raw_pr.isdigit():
            raw_pr = int(raw_pr)
        trace_id = data.get("trace_id") or fallback_trace_id
        return cls(
            repo=str(data.get("repo") or ""),
            commit=str(data["commit"]) if data.get("commit") is not None else None,
            pr=raw_pr,
            plan_id=str(data["plan_id"]) if data.get("plan_id") is not None else None,
            chg_id=str(data["chg_id"]) if data.get("chg_id") is not None else None,
            run_id=str(data["run_id"]) if data.get("run_id") is not None else None,
            trace_id=str(trace_id) if trace_id is not None else None,
        )


def derive_experience_event_id(
    *,
    project: str,
    producer: str,
    kind: str,
    origin: OriginProvenance | Mapping[str, Any] | None,
    occurred_at: str,
    payload_type: str,
    use_chg_fallback: bool = False,
) -> str:
    """Derive deterministic UUIDv5 over the 10 canonical identity inputs.

    By default (``use_chg_fallback=False``), slot 7 uses ``origin.plan_id or ''``
    for 100% byte-for-byte parity with legacy ``interlog.envelope.derive_event_id``.
    When ``use_chg_fallback=True``, slot 7 falls back to ``origin.chg_id`` if
    ``plan_id`` is absent.
    """
    if isinstance(origin, OriginProvenance):
        origin_map: Mapping[str, Any] = origin.to_dict()
    elif isinstance(origin, Mapping):
        origin_map = origin
    else:
        origin_map = {}

    pr = origin_map.get("pr")
    plan_slot = str(origin_map.get("plan_id") or "")
    if not plan_slot and use_chg_fallback:
        plan_slot = str(origin_map.get("chg_id") or "")

    name = "\n".join(
        [
            str(project or ""),
            str(producer or ""),
            str(kind or ""),
            str(origin_map.get("repo") or ""),
            str(origin_map.get("commit") or ""),
            "" if pr is None else str(pr),
            plan_slot,
            str(origin_map.get("run_id") or ""),
            str(occurred_at or ""),
            str(payload_type or ""),
        ]
    )
    return str(uuid.uuid5(EXPERIENCE_EVENT_ID_NS, name))


@dataclass(frozen=True, slots=True)
class ExperienceEvent:
    """Canonical agent milestone event ready for Episode projection and distillation."""

    project: str
    producer: str
    kind: str
    occurred_at: str
    payload_type: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    origin: OriginProvenance | None = None
    tenant_id: str = "default"
    domain_id: str | None = None
    summary: str | None = None
    effectiveness_score: float | None = None
    calibration_delta: float | None = None
    event_id: str = ""

    def __post_init__(self) -> None:
        _require(bool(self.project and self.project.strip()), "ExperienceEvent.project is required")
        _require(
            bool(self.producer and self.producer.strip()), "ExperienceEvent.producer is required"
        )
        _require(
            self.kind in EXPERIENCE_EVENT_KINDS,
            f"ExperienceEvent.kind must be one of {sorted(EXPERIENCE_EVENT_KINDS)}",
        )
        _require(
            bool(self.occurred_at and self.occurred_at.strip()),
            "ExperienceEvent.occurred_at is required",
        )
        _require(
            bool(self.payload_type and self.payload_type.strip()),
            "ExperienceEvent.payload_type is required",
        )
        _require(
            bool(self.tenant_id and self.tenant_id.strip()),
            "ExperienceEvent.tenant_id is required (ADR-07)",
        )
        if self.origin is not None and not isinstance(self.origin, OriginProvenance):
            if isinstance(self.origin, Mapping):
                object.__setattr__(self, "origin", OriginProvenance.from_dict(self.origin))
            else:
                raise ValidationError(
                    "ExperienceEvent.origin must be an OriginProvenance or mapping"
                )
        if self.effectiveness_score is not None:
            _require(
                isinstance(self.effectiveness_score, (int, float))
                and not isinstance(self.effectiveness_score, bool)
                and 0.0 <= float(self.effectiveness_score) <= 1.0,
                "ExperienceEvent.effectiveness_score must be in [0.0, 1.0]",
            )
            object.__setattr__(self, "effectiveness_score", float(self.effectiveness_score))
        if self.calibration_delta is not None:
            _require(
                isinstance(self.calibration_delta, (int, float))
                and not isinstance(self.calibration_delta, bool),
                "ExperienceEvent.calibration_delta must be numeric when set",
            )
            object.__setattr__(self, "calibration_delta", float(self.calibration_delta))

        object.__setattr__(self, "payload", _frozen_mapping(self.payload))

        derived_strict = derive_experience_event_id(
            project=self.project,
            producer=self.producer,
            kind=self.kind,
            origin=self.origin,
            occurred_at=self.occurred_at,
            payload_type=self.payload_type,
            use_chg_fallback=False,
        )
        derived_chg = derive_experience_event_id(
            project=self.project,
            producer=self.producer,
            kind=self.kind,
            origin=self.origin,
            occurred_at=self.occurred_at,
            payload_type=self.payload_type,
            use_chg_fallback=True,
        )
        if not self.event_id:
            object.__setattr__(self, "event_id", derived_strict)
        else:
            _require(
                self.event_id in (derived_strict, derived_chg),
                f"ExperienceEvent.event_id {self.event_id!r} does not match deterministic "
                f"UUIDv5 derivation ({derived_strict!r})",
            )

    def to_episode(self) -> Episode:
        """Convert this ExperienceEvent into a vetted L1 Episode.

        Screens both the human-readable content_raw and the serialized metadata
        envelope (payload, origin, project, producer) through inspect_memory_write()
        and enforces MAX_EXPERIENCE_CONTENT_BYTES = 65536.
        """
        raw_text = (
            self.summary.strip()
            if (self.summary and self.summary.strip())
            else f"[{self.kind}] {json.dumps(dict(self.payload), sort_keys=True)}"
        )
        metadata_envelope = json.dumps(
            {
                "project": self.project,
                "producer": self.producer,
                "payload_type": self.payload_type,
                "payload": dict(self.payload),
                "origin": self.origin.to_dict() if self.origin is not None else None,
            },
            sort_keys=True,
        )
        raw_bytes = len(raw_text.encode("utf-8"))
        env_bytes = len(metadata_envelope.encode("utf-8"))
        if raw_bytes > MAX_EXPERIENCE_CONTENT_BYTES or env_bytes > MAX_EXPERIENCE_CONTENT_BYTES:
            raise ValidationError(
                f"ExperienceEvent content exceeds {MAX_EXPERIENCE_CONTENT_BYTES} bytes"
            )

        run_id = self.origin.run_id if self.origin is not None else None
        clean_text = inspect_memory_write(
            raw_text,
            tenant_id=self.tenant_id,
            writer_agent_id=self.producer,
            target=f"episodes:{self.project}",
            run_id=run_id,
        )
        inspect_memory_write(
            metadata_envelope,
            tenant_id=self.tenant_id,
            writer_agent_id=self.producer,
            target=f"episodes:{self.project}:metadata",
            run_id=run_id,
        )

        meta: dict[str, Any] = {
            "event_id": self.event_id,
            "event_kind": self.kind,
            "occurred_at": self.occurred_at,
            "payload_type": self.payload_type,
            "payload": dict(self.payload),
        }
        if self.origin is not None:
            meta["origin"] = self.origin.to_dict()
            if self.origin.trace_id is not None:
                meta["trace_id"] = self.origin.trace_id
        if self.effectiveness_score is not None:
            meta["effectiveness_score"] = self.effectiveness_score
        if self.calibration_delta is not None:
            meta["calibration_delta"] = self.calibration_delta

        return Episode(
            agent_id=self.producer,
            content_raw=clean_text,
            project_id=self.project,
            domain_id=self.domain_id,
            tenant_id=self.tenant_id,
            kind=self.kind,
            metadata=meta,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a wire-compatible dictionary."""
        out: dict[str, Any] = {
            "schema_version": "1",
            "event_id": self.event_id,
            "occurred_at": self.occurred_at,
            "source": {"project": self.project, "producer": self.producer},
            "kind": self.kind,
            "payload_type": self.payload_type,
            "payload": dict(self.payload),
            "tenant_id": self.tenant_id,
        }
        if self.origin is not None:
            out["origin"] = self.origin.to_dict()
            if self.origin.trace_id is not None:
                out["trace_id"] = self.origin.trace_id
        if self.domain_id is not None:
            out["domain_id"] = self.domain_id
        if self.summary is not None:
            out["summary"] = self.summary
        if self.effectiveness_score is not None:
            out["effectiveness_score"] = self.effectiveness_score
        if self.calibration_delta is not None:
            out["calibration_delta"] = self.calibration_delta
        return out

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ExperienceEvent:
        """Hydrate ExperienceEvent from either flat or Interlog envelope dict format."""
        _require(isinstance(data, Mapping), "ExperienceEvent input must be a mapping")
        source = data.get("source")
        if isinstance(source, Mapping):
            project = str(source.get("project") or data.get("project") or "")
            producer = str(source.get("producer") or data.get("producer") or "")
        else:
            project = str(data.get("project") or "")
            producer = str(data.get("producer") or "")

        raw_origin = data.get("origin")
        top_trace_id = str(data["trace_id"]) if data.get("trace_id") is not None else None
        origin: OriginProvenance | None = None
        if isinstance(raw_origin, OriginProvenance):
            origin = raw_origin
        elif isinstance(raw_origin, Mapping) and raw_origin:
            origin = OriginProvenance.from_dict(raw_origin, fallback_trace_id=top_trace_id)

        payload_raw = data.get("payload")
        if payload_raw is None:
            payload_map: Mapping[str, Any] = {}
        elif isinstance(payload_raw, Mapping):
            payload_map = payload_raw
        else:
            raise ValidationError("ExperienceEvent.payload must be a mapping")

        return cls(
            project=project,
            producer=producer,
            kind=str(data.get("kind") or ""),
            occurred_at=str(data.get("occurred_at") or ""),
            payload_type=str(data.get("payload_type") or ""),
            payload=payload_map,
            origin=origin,
            tenant_id=str(data.get("tenant_id") or "default"),
            domain_id=str(data["domain_id"]) if data.get("domain_id") is not None else None,
            summary=str(data["summary"]) if data.get("summary") is not None else None,
            effectiveness_score=(
                data["effectiveness_score"] if data.get("effectiveness_score") is not None else None
            ),
            calibration_delta=(
                data["calibration_delta"] if data.get("calibration_delta") is not None else None
            ),
            event_id=str(data.get("event_id") or ""),
        )


def compute_calibration_delta(predicted_score: float, actual_outcome: float) -> float:
    """Compute exec_ready_calibration_delta = round(predicted_score - actual_outcome, 4).

    Both inputs must be numeric in [0.0, 1.0]. Positive delta indicates overconfidence;
    negative delta indicates underconfidence; 0.0 indicates perfect calibration.
    """
    _require(
        isinstance(predicted_score, (int, float))
        and not isinstance(predicted_score, bool)
        and 0.0 <= float(predicted_score) <= 1.0,
        "predicted_score must be in [0.0, 1.0]",
    )
    _require(
        isinstance(actual_outcome, (int, float))
        and not isinstance(actual_outcome, bool)
        and 0.0 <= float(actual_outcome) <= 1.0,
        "actual_outcome must be in [0.0, 1.0]",
    )
    return round(float(predicted_score) - float(actual_outcome), 4)


def compute_repeat_findings_rate(finding_codes: Sequence[str]) -> float:
    """Compute repeat_findings_rate in [0.0, 1.0] across a sequence of finding codes.

    Returns 0.0 for empty sequences; otherwise returns
    ``(total_codes - unique_codes) / total_codes`` rounded to 4 decimal places.
    """
    normalized = [str(code).strip() for code in finding_codes if str(code).strip()]
    if not normalized:
        return 0.0
    total = len(normalized)
    unique = len(set(normalized))
    return round((total - unique) / total, 4)


def compute_learning_health_metrics(episodes: Sequence[Episode]) -> dict[str, Any]:
    """Compute closed-loop learning effectiveness signals across a sequence of Episodes.

    Signal 1 (``exec_ready_calibration``): Pairs ``plan.authored`` episodes
    (``payload.exec_ready_score`` in [0, 100]) with ``execution.completed`` episodes
    (``payload.first_pass_success`` boolean or ``payload.status == 'completed'``)
    sharing ``(origin.repo, origin.plan_id)``.
    Signal 2 (``repeat_findings``): Inspects ``review.verdict`` and ``gate.failed``
    episodes carrying ``payload.findings`` (list of dicts with ``rule_id``) or
    ``payload.rule_id``.
    """
    authored_by_plan: dict[tuple[str, str], float] = {}
    completed_by_plan: dict[tuple[str, str], float] = {}
    seen_finding_keys: set[tuple[str, str]] = set()
    total_findings = 0
    repeat_findings = 0

    for ep in episodes:
        if ep.kind not in EXPERIENCE_EVENT_KINDS:
            continue
        meta = ep.metadata or {}
        origin = meta.get("origin")
        if not isinstance(origin, Mapping):
            continue
        repo = str(origin.get("repo") or "").strip()
        plan_id = str(origin.get("plan_id") or origin.get("chg_id") or "").strip()
        payload = meta.get("payload")
        payload_map: Mapping[str, Any] = payload if isinstance(payload, Mapping) else {}

        if ep.kind == "plan.authored" and repo and plan_id:
            raw_score = payload_map.get("exec_ready_score")
            if isinstance(raw_score, (int, float)) and not isinstance(raw_score, bool):
                ratio = float(raw_score) / 100.0 if float(raw_score) > 1.0 else float(raw_score)
                authored_by_plan[(repo, plan_id)] = max(0.0, min(1.0, ratio))
        elif ep.kind == "execution.completed" and repo and plan_id:
            fps = payload_map.get("first_pass_success")
            if isinstance(fps, bool):
                completed_by_plan[(repo, plan_id)] = 1.0 if fps else 0.0
            elif str(payload_map.get("status") or "").lower() in ("completed", "passed", "success"):
                completed_by_plan[(repo, plan_id)] = 1.0
            else:
                completed_by_plan[(repo, plan_id)] = 0.0
        elif ep.kind in ("review.verdict", "gate.failed") and repo:
            rule_ids: list[str] = []
            findings_list = payload_map.get("findings")
            if isinstance(findings_list, Sequence) and not isinstance(findings_list, (str, bytes)):
                for item in findings_list:
                    if isinstance(item, Mapping) and item.get("rule_id"):
                        rule_ids.append(str(item["rule_id"]).strip())
                    elif isinstance(item, str) and item.strip():
                        rule_ids.append(item.strip())
            if payload_map.get("rule_id"):
                rule_ids.append(str(payload_map["rule_id"]).strip())
            for rid in rule_ids:
                if not rid:
                    continue
                total_findings += 1
                key = (repo, rid)
                if key in seen_finding_keys:
                    repeat_findings += 1
                else:
                    seen_finding_keys.add(key)

    paired_keys = sorted(set(authored_by_plan) & set(completed_by_plan))
    paired_plans = len(paired_keys)
    if paired_plans > 0:
        pred_vals = [authored_by_plan[k] for k in paired_keys]
        act_vals = [completed_by_plan[k] for k in paired_keys]
        mean_pred = round(sum(pred_vals) / paired_plans, 4)
        actual_rate = round(sum(act_vals) / paired_plans, 4)
        mae = round(
            sum(abs(p - a) for p, a in zip(pred_vals, act_vals, strict=True)) / paired_plans, 4
        )
    else:
        mean_pred = 0.0
        actual_rate = 0.0
        mae = 0.0

    repeat_rate = round(repeat_findings / total_findings, 4) if total_findings > 0 else 0.0
    return {
        "exec_ready_calibration": {
            "paired_plans": paired_plans,
            "mean_predicted_ready_ratio": mean_pred,
            "actual_first_pass_rate": actual_rate,
            "calibration_mae": mae,
        },
        "repeat_findings": {
            "total_findings": total_findings,
            "repeat_findings": repeat_findings,
            "repeat_findings_rate": repeat_rate,
        },
    }
