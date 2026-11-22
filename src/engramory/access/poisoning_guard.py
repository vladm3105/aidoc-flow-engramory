"""Backwards-compatible re-export shim for ``engramory.core.poisoning_guard``."""

from __future__ import annotations

from engramory.core.poisoning_guard import (
    _CTRL_AND_BIDI_RE,
    _INJECTION_PATTERNS,
    _PORTABLE_REDACT_PATTERNS,
    _PORTABLE_STOP_TERMS,
    _PRIVATE_KEY_BLOCK_RE,
    _SECRET_PATTERNS,
    _VARIATION_SELECTOR_16_RE,
    MemoryPoisoningError,
    _normalize_for_match,
    detect_hard_secrets,
    detect_prompt_injection,
    has_dangerous_unicode,
    inspect_memory_write,
    redact_hard_secrets,
    sanitize_portable_text,
    sanitize_portable_value,
)

__all__ = [
    "MemoryPoisoningError",
    "_CTRL_AND_BIDI_RE",
    "_INJECTION_PATTERNS",
    "_PORTABLE_REDACT_PATTERNS",
    "_PORTABLE_STOP_TERMS",
    "_PRIVATE_KEY_BLOCK_RE",
    "_SECRET_PATTERNS",
    "_VARIATION_SELECTOR_16_RE",
    "_normalize_for_match",
    "detect_hard_secrets",
    "detect_prompt_injection",
    "has_dangerous_unicode",
    "inspect_memory_write",
    "redact_hard_secrets",
    "sanitize_portable_text",
    "sanitize_portable_value",
]
