"""LLMPort — chat + embedding interface, always via the LiteLLM gateway (ADR-04).

Dev: LiteLLM -> Ollama. Cloud: LiteLLM -> Vertex AI / Azure OpenAI. A config change,
not a code change — the core only ever sees this port.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol


class LLMPort(Protocol):
    async def chat(self, *, messages: Sequence[Mapping[str, str]],
                   model: str = "chat", temperature: float = 0.0) -> str:
        """Return the assistant completion for a list of role/content messages."""
        ...

    async def embed(self, *, texts: Sequence[str],
                    model: str = "embed") -> Sequence[Sequence[float]]:
        """Return one embedding vector per input text."""
        ...
