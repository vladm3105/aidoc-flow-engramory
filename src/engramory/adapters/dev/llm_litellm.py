"""LiteLLMAdapter — LLMPort dev adapter via the LiteLLM proxy (ADR-04).

Satisfies the LLMPort protocol using non-blocking calls (asyncio.to_thread over urllib.request)
to LiteLLM's OpenAI-compatible endpoints (/v1/embeddings and /v1/chat/completions).
"""

from __future__ import annotations

import asyncio
import json
import os
import urllib.request
from collections.abc import Mapping, Sequence
from typing import Any

DEFAULT_BASE_URL = "http://localhost:4000/v1"
DEFAULT_EMBED_MODEL = "embed"
DEFAULT_EMBED_DIMS = 1536
DEFAULT_CHAT_MODEL = "chat"


class LiteLLMAdapter:
    """Dev-tier LLMPort implementation talking to the local/staging LiteLLM proxy."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        default_embed_model: str | None = None,
        default_embed_dims: int | None = None,
        default_chat_model: str | None = None,
        timeout_sec: float | None = None,
    ) -> None:
        raw_url = base_url or os.getenv("LITELLM_BASE_URL") or DEFAULT_BASE_URL
        clean_url = raw_url.rstrip("/")
        if not clean_url.endswith("/v1"):
            clean_url = f"{clean_url}/v1"
        self._base_url = clean_url
        self._api_key = api_key or os.getenv("LITELLM_API_KEY", "sk-platform-token")
        self._default_embed_model = default_embed_model or os.getenv(
            "ENGRAMORY_EMBED_MODEL", DEFAULT_EMBED_MODEL
        )
        self._embed_model = self._default_embed_model
        env_dims = os.getenv("ENGRAMORY_EMBED_DIMS")
        self._default_embed_dims = (
            default_embed_dims
            if default_embed_dims is not None
            else (int(env_dims) if env_dims else DEFAULT_EMBED_DIMS)
        )
        self._default_chat_model = default_chat_model or os.getenv(
            "ENGRAMORY_CHAT_MODEL", DEFAULT_CHAT_MODEL
        )
        env_timeout = os.getenv("ENGRAMORY_TIMEOUT_SEC")
        self._timeout_sec = (
            timeout_sec
            if timeout_sec is not None
            else (float(env_timeout) if env_timeout else 30.0)
        )

    @property
    def embed_model(self) -> str:
        """Configured default embedding model name."""
        return str(self._default_embed_model)

    def _sync_request(self, endpoint: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        url = f"{self._base_url}/{endpoint.lstrip('/')}"
        if not url.startswith(("http://", "https://")):
            raise ValueError(f"LiteLLM base_url must use http:// or https://: {url!r}")
        data = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
        }
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        # nosemgrep  # reason: url scheme validated to http/https
        with urllib.request.urlopen(req, timeout=self._timeout_sec) as resp:  # nosec B310  # reason: url scheme validated to http/https
            raw = resp.read().decode("utf-8")
            return json.loads(raw)  # type: ignore[no-any-return]  # reason: json.loads returns Any and caller parses structure

    async def chat(
        self,
        *,
        messages: Sequence[Mapping[str, str]],
        model: str = "chat",
        temperature: float = 0.0,
    ) -> str:
        """Return the assistant completion for a list of role/content messages."""
        chosen_model = self._default_chat_model if model == "chat" else model
        payload = {
            "model": chosen_model,
            "messages": list(messages),
            "temperature": temperature,
        }
        res = await asyncio.to_thread(self._sync_request, "chat/completions", payload)
        choices = res.get("choices", [])
        if not choices:
            raise RuntimeError(f"LiteLLM chat response contained no choices: {res}")
        msg = choices[0].get("message", {})
        return str(msg.get("content", ""))

    async def embed(
        self,
        *,
        texts: Sequence[str],
        model: str = "embed",
    ) -> Sequence[Sequence[float]]:
        """Return one embedding vector per input text."""
        if not texts:
            return []
        chosen_model = (
            self._default_embed_model if (model == "embed" or model == "default") else model
        )
        payload: dict[str, Any] = {
            "model": chosen_model,
            "input": list(texts),
        }
        if self._default_embed_dims and chosen_model != "embed":
            payload["dimensions"] = self._default_embed_dims
        res = await asyncio.to_thread(self._sync_request, "embeddings", payload)
        data = res.get("data", [])
        if len(data) != len(texts):
            raise RuntimeError(
                f"LiteLLM embed returned {len(data)} embeddings for {len(texts)} inputs"
            )
        # Sort by index if returned out of order
        sorted_data = sorted(data, key=lambda item: int(item.get("index", 0)))
        return [item["embedding"] for item in sorted_data]
