"""LLMPort — chat, embedding, vision, and audio transcription interface via LiteLLM (ADR-04).

Dev: LiteLLM -> Ollama / Whisper. Cloud: LiteLLM -> Vertex AI / Azure OpenAI. A config change,
not a code change — the core only ever sees this port.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol


class LLMPort(Protocol):
    async def chat(
        self,
        *,
        messages: Sequence[Mapping[str, Any]],
        model: str = "chat",
        temperature: float = 0.0,
    ) -> str:
        """Return the assistant completion for a list of role/content messages."""
        ...

    async def embed(
        self, *, texts: Sequence[str], model: str = "embed"
    ) -> Sequence[Sequence[float]]:
        """Return one embedding vector per input text."""
        ...

    async def describe_image(
        self,
        *,
        image_bytes: bytes,
        mime_type: str = "image/png",
        prompt: str = (
            "Describe this image in structured Markdown, including any visible text (OCR), "
            "architectural components, data flows, or tables."
        ),
        model: str = "vision",
    ) -> str:
        """Return a structured Markdown description and OCR transcription of an image."""
        ...

    async def transcribe_audio(
        self,
        *,
        audio_bytes: bytes,
        filename: str = "audio.wav",
        mime_type: str = "audio/wav",
        model: str = "audio-transcribe",
    ) -> dict[str, Any]:
        """Transcribe audio bytes and return {'text': str, 'segments': list[dict[str, Any]]}."""
        ...
