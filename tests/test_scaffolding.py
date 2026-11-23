"""Tests verifying repository scaffolding integrity and module definitions."""

from __future__ import annotations

import asyncio
import importlib
from pathlib import Path

import pytest
from pydantic import ValidationError

from har2mcp.cli.main import GenerateConfig
from har2mcp.llm.client import (
    BaseLLMClient,
    LLMMessage,
    LLMRequest,
    LLMResponse,
    LLMRole,
    NoOpLLMClient,
)


@pytest.mark.parametrize(
    "module_name",
    [
        "har2mcp",
        "har2mcp.parser",
        "har2mcp.redact",
        "har2mcp.cluster",
        "har2mcp.schema",
        "har2mcp.auth",
        "har2mcp.design",
        "har2mcp.codegen",
        "har2mcp.verify",
        "har2mcp.llm",
        "har2mcp.llm.client",
        "har2mcp.cli",
        "har2mcp.cli.main",
    ],
)
def test_all_modules_importable(module_name: str) -> None:
    """Verify that every module in the package layout can be imported cleanly."""
    mod = importlib.import_module(module_name)
    assert mod is not None


def test_llm_client_protocol_and_models() -> None:
    """Verify LLM models and NoOp client behavior."""
    msg = LLMMessage(role=LLMRole.USER, content="Hello")
    assert msg.role == LLMRole.USER
    assert msg.content == "Hello"

    req = LLMRequest(messages=[msg])
    client: BaseLLMClient = NoOpLLMClient(fixed_reply='{"status": "ok"}')
    resp: LLMResponse = client.complete(req)

    assert resp.content == '{"status": "ok"}'
    assert resp.model == "noop"


def test_llm_client_async_complete() -> None:
    """Verify async completion on LLM client."""
    msg = LLMMessage(role=LLMRole.SYSTEM, content="System prompt")
    req = LLMRequest(messages=[msg])
    client = NoOpLLMClient(fixed_reply="async reply")
    resp = asyncio.run(client.acomplete(req))
    assert resp.content == "async reply"


def test_generate_config_validation(tmp_path: Path) -> None:
    """Verify GenerateConfig pydantic validation and immutability."""
    har_file = tmp_path / "test.har"
    out_dir = tmp_path / "out"

    config = GenerateConfig(har_path=har_file, out_dir=out_dir)
    assert config.allow_writes is False
    assert config.har_path == har_file
    assert config.out_dir == out_dir

    with pytest.raises(ValidationError):
        # frozen model should reject modification
        config.allow_writes = True  # type: ignore[misc]
