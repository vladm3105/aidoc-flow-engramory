"""RAG & Shared Memory Poisoning Guard (SPEC-01 / ADR-06 / OWASP ASI03 / Gap G12).

Protects long-term memory and knowledge base against indirect prompt injection,
Unicode Bidi/Tag smuggling, malware, and credential persistence.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from typing import Any

logger = logging.getLogger(__name__)

# P14 Control chars, Bidi overrides, Zero-Width chars, and Unicode Tag block
_CTRL_AND_BIDI_RE = re.compile(
    r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f"
    r"\u200b-\u200f\u202a-\u202e\u2066-\u2069\ufeff"
    r"\U000e0000-\U000e007f]"
)

# OWASP ASI03: Indirect prompt injection patterns in shared memory
_INJECTION_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "PI_SYSTEM_ROLE_OVERRIDE",
        re.compile(
            r"(?i)(?:ignore|disregard|forget)\s+(?:all\s+)?(?:prior|previous)\s+"
            r"(?:instructions|directions|prompts|rules)"
        ),
    ),
    (
        "PI_CHATML_DELIMITER",
        re.compile(r"<\|im_start\|>|<\|im_end\|>|\[INST\]|\[/INST\]|<system>|<assistant>"),
    ),
    (
        "PI_ROLEPLAY_OVERRIDE",
        re.compile(
            r"(?i)\byou\s+are\s+now\s+(?:in\s+developer\s+mode|unrestricted|dan|root|superuser)\b"
        ),
    ),
    (
        "PI_REMOTE_PIPE_SH",
        re.compile(r"(?i)\b(?:curl|wget)\s+https?://[^\s]+\s*\|\s*(?:ba)?sh\b"),
    ),
]

# Hard secrets: API keys, private keys, high-entropy tokens
_SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("secret_github_pat", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,255}\b")),
    ("secret_github_fine_grained", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{40,255}\b")),
    ("secret_aws_akid", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    (
        "secret_private_key",
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PRIVATE )?PRIVATE KEY-----"),
    ),
    ("secret_openbao_token", re.compile(r"\bhvs\.[A-Za-z0-9_-]{24,255}\b")),
    ("secret_llm_api_key", re.compile(r"\bsk-(?:proj-|ant-)?[A-Za-z0-9_-]{24,255}\b")),
]

_PORTABLE_REDACT_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    *_SECRET_PATTERNS,
    (
        "[REDACTED_EMAIL]",
        re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    ),
    (
        "[REDACTED_URL]",
        re.compile(r"https?://[^\s)\"'>]+"),
    ),
    (
        "[REDACTED_IP]",
        re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d{1,5})?\b"),
    ),
    (
        "[REDACTED_ID]",
        re.compile(
            r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
        ),
    ),
]


def sanitize_portable_text(text: str, *, redact_terms: Sequence[str] = ()) -> tuple[str, int]:
    """Scrub secrets, emails, URLs, IPs, UUIDs, and tenant/project identifiers from portable skills.

    Returns (sanitized_text, redaction_count).
    """
    cleaned = text
    total_redactions = 0
    for replacement, pattern in _PORTABLE_REDACT_PATTERNS:
        label = replacement if replacement.startswith("[REDACTED_") else "[REDACTED_SECRET]"
        cleaned, count = pattern.subn(label, cleaned)
        total_redactions += count

    sorted_terms = sorted(
        {t.strip() for t in redact_terms if t and t.strip()},
        key=len,
        reverse=True,
    )
    for norm in sorted_terms:
        if len(norm) < 3 or norm.lower() in {"default", "dev", "prod", "stage", "agent", "project"}:
            continue
        term_re = re.compile(re.escape(norm), re.IGNORECASE)
        cleaned, count = term_re.subn("[REDACTED_TENANT]", cleaned)
        total_redactions += count

    return cleaned, total_redactions


def sanitize_portable_value(value: Any, *, redact_terms: Sequence[str] = ()) -> tuple[Any, int]:
    """Recursively scrub strings, lists, and dicts for portable Brain Passports."""
    if isinstance(value, str):
        return sanitize_portable_text(value, redact_terms=redact_terms)
    if isinstance(value, list | tuple):
        out_list: list[Any] = []
        total = 0
        for item in value:
            cleaned_item, count = sanitize_portable_value(item, redact_terms=redact_terms)
            out_list.append(cleaned_item)
            total += count
        return out_list, total
    if isinstance(value, dict):
        out_dict: dict[str, Any] = {}
        total = 0
        for k, v in value.items():
            cleaned_val, count = sanitize_portable_value(v, redact_terms=redact_terms)
            out_dict[str(k)] = cleaned_val
            total += count
        return out_dict, total
    return value, 0


class MemoryPoisoningError(PermissionError):
    """Raised when memory write fails OWASP ASI03 or platform poisoning checks."""


def has_dangerous_unicode(text: str) -> bool:
    """Return True if text contains P14 control, bidi override, or Unicode Tag characters."""
    return bool(_CTRL_AND_BIDI_RE.search(text))


def detect_prompt_injection(text: str) -> list[str]:
    """Return list of matched prompt injection pattern IDs."""
    matches: list[str] = []
    for pid, pattern in _INJECTION_PATTERNS:
        if pattern.search(text):
            matches.append(pid)
    return matches


def detect_hard_secrets(text: str) -> list[str]:
    """Return list of matched hard secret pattern IDs."""
    matches: list[str] = []
    for sid, pattern in _SECRET_PATTERNS:
        if pattern.search(text):
            matches.append(sid)
    return matches


def inspect_memory_write(
    content: str,
    *,
    tenant_id: str,
    writer_agent_id: str,
    target: str,
    run_id: str | None = None,
) -> str:
    """Validate content against prompt injection, Unicode smuggling, and credential leakage.

    Args:
        content: Raw memory or KB text.
        tenant_id: Tenant isolation identifier.
        writer_agent_id: Identity of writing agent.
        target: Destination collection or document ID.
        run_id: Optional execution run identifier.

    Returns:
        Clean, validated text content.

    Raises:
        MemoryPoisoningError: If content violates security policies.
    """
    # 1. P14 Unicode Bidi / Tag smuggling
    if has_dangerous_unicode(content):
        raise MemoryPoisoningError(
            "Memory poisoning blocked: forbidden Unicode control/bidi/tag characters (P14)"
        )

    # 2. OWASP ASI03 Indirect Prompt Injection
    injections = detect_prompt_injection(content)
    if injections:
        raise MemoryPoisoningError(
            f"Memory poisoning blocked (OWASP ASI03): prompt injection {injections}"
        )

    # 3. Hard secret leakage block
    secrets = detect_hard_secrets(content)
    if secrets:
        raise MemoryPoisoningError(
            f"Memory write blocked by DLP: content contains unredacted credentials {secrets}"
        )

    # 4. Optional delegation to platform_security.agents.MemoryPoisoningGuard if available
    try:
        from platform_security.agents.memory_guard import MemoryPoisoningGuard

        guard = MemoryPoisoningGuard()
        clean_text, _ = guard.validate_and_sign_write(
            content,
            namespace=tenant_id,
            writer_agent_id=writer_agent_id,
            run_id=run_id or "run-direct",
            target_collection_or_path=target,
        )
        return clean_text
    except ImportError:
        # Fall back to native inspection above when platform_security is not in pythonpath
        pass
    except Exception as exc:  # noqa: BLE001  # reason: propagate security guard failures
        if isinstance(exc, MemoryPoisoningError):
            raise
        raise MemoryPoisoningError(f"Memory poisoning validation failed: {exc}") from exc

    return content
