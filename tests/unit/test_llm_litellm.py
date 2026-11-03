"""Unit tests for LiteLLMAdapter (LLMPort dev adapter)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from engramory.adapters.dev.llm_litellm import LiteLLMAdapter


@pytest.mark.asyncio
async def test_embed_empty_texts() -> None:
    adapter = LiteLLMAdapter(base_url="http://mock-proxy:4000/v1")
    vectors = await adapter.embed(texts=[])
    assert vectors == []


@pytest.mark.asyncio
async def test_embed_success() -> None:
    adapter = LiteLLMAdapter(base_url="http://mock-proxy:4000/v1")
    mock_response = {
        "data": [
            {"index": 0, "embedding": [0.1, 0.2, 0.3]},
            {"index": 1, "embedding": [0.4, 0.5, 0.6]},
        ]
    }

    mock_resp_obj = MagicMock()
    mock_resp_obj.read.return_value = json.dumps(mock_response).encode("utf-8")
    mock_resp_obj.__enter__.return_value = mock_resp_obj

    with patch("urllib.request.urlopen", return_value=mock_resp_obj) as mock_urlopen:
        vectors = await adapter.embed(texts=["hello", "world"])
        assert len(vectors) == 2
        assert vectors[0] == [0.1, 0.2, 0.3]
        assert vectors[1] == [0.4, 0.5, 0.6]
        assert mock_urlopen.call_count == 1
        req = mock_urlopen.call_args[0][0]
        sent_body = json.loads(req.data.decode("utf-8"))
        assert sent_body["model"] == "embed"
        assert "dimensions" not in sent_body


@pytest.mark.asyncio
async def test_chat_success() -> None:
    adapter = LiteLLMAdapter(base_url="http://mock-proxy:4000/v1")
    mock_response = {
        "choices": [{"message": {"role": "assistant", "content": "Hello! I am Engramory."}}]
    }

    mock_resp_obj = MagicMock()
    mock_resp_obj.read.return_value = json.dumps(mock_response).encode("utf-8")
    mock_resp_obj.__enter__.return_value = mock_resp_obj

    with patch("urllib.request.urlopen", return_value=mock_resp_obj):
        reply = await adapter.chat(
            messages=[{"role": "user", "content": "Hi"}],
            model="chat",
        )
        assert reply == "Hello! I am Engramory."


def test_api_key_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LITELLM_API_KEY", raising=False)
    monkeypatch.setenv("LITELLM_MASTER_KEY", "secret-master-token")
    # Must NOT fall back to master key per platform policy
    adapter = LiteLLMAdapter(base_url="http://mock-proxy:4000/v1")
    assert adapter._api_key == "sk-platform-token"

    monkeypatch.setenv("LITELLM_API_KEY", "custom-virtual-token")
    adapter2 = LiteLLMAdapter(base_url="http://mock-proxy:4000/v1")
    assert adapter2._api_key == "custom-virtual-token"
