"""Unit tests for the LLM client interface, disk cache, retries, and FakeLLM."""

from __future__ import annotations

from pathlib import Path

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
