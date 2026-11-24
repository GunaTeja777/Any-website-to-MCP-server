"""Unit tests for the LLM client interface, disk cache, retries, and FakeLLM."""

from __future__ import annotations

from pathlib import Path

import pytest

from har2mcp.llm import FakeLLM, LLMMessage, LLMRole


def test_fakellm_basic_completion() -> None:
    """Test FakeLLM returns configured response and records call history."""
    llm = FakeLLM(responses='{"status": "ok"}', cache_dir=None)
    response = llm.complete(
        system="You are a helpful assistant.",
        messages=[LLMMessage(role=LLMRole.USER, content="Hello")],
    )

    assert response.content == '{"status": "ok"}'
    assert response.cached is False
    assert len(llm.calls) == 1
    assert llm.calls[0]["system"] == "You are a helpful assistant."


def test_fakellm_sequence_and_retry(tmp_path: Path) -> None:
    """Test FakeLLM returning sequence of responses upon retries."""
    # First response fails JSON requirement, second succeeds
    llm = FakeLLM(
        responses=["malformed json text", '{"repaired": true}'],
        cache_dir=tmp_path / "cache",
    )
    response = llm.complete(
        system="",
        messages=[{"role": "user", "content": "Generate JSON"}],
        json_schema={"type": "object"},
    )

    assert response.content == '{"repaired": true}'
    assert len(llm.calls) == 2


def test_disk_cache_hit_on_repeated_call(tmp_path: Path) -> None:
    """Test disk cache avoids repeated LLM execution for identical prompts."""
    cache_dir = tmp_path / "llm_cache"
    llm = FakeLLM(
        responses='{"cached_data": 42}',
        cache_dir=cache_dir,
    )

    # First execution (cache miss)
    resp1 = llm.complete(
        system="System",
        messages=[{"role": "user", "content": "Identical query"}],
    )
    assert resp1.cached is False
    assert len(llm.calls) == 1

    # Second execution (cache hit)
    resp2 = llm.complete(
        system="System",
        messages=[{"role": "user", "content": "Identical query"}],
    )
    assert resp2.cached is True
    assert resp2.content == '{"cached_data": 42}'
    # FakeLLM was not called a second time
    assert len(llm.calls) == 1


def test_gemini_client_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test GeminiLLMClient configuration and default base URL."""
    from har2mcp.llm import GeminiLLMClient, get_default_llm_client

    client = GeminiLLMClient(api_key="test-key")
    assert client.api_key == "test-key"
    assert "generativelanguage.googleapis.com" in client.base_url
    assert client.model == "gemini-2.5-flash"

    # Test get_default_llm_client with GEMINI_API_KEY
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-test-secret")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    default_client = get_default_llm_client()
    assert isinstance(default_client, GeminiLLMClient)
    assert default_client.api_key == "gemini-test-secret"
