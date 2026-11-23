"""Provider-agnostic LLM interface with structured JSON, caching, and retries."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from abc import ABC, abstractmethod
from collections.abc import Sequence
from enum import StrEnum
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger("har2mcp.llm")

# Model pricing in USD per 1M tokens (input, output)
MODEL_PRICING: dict[str, tuple[float, float]] = {
    "gpt-4o": (5.00, 15.00),
    "gpt-4o-mini": (0.15, 0.60),
    "claude-3-5-sonnet": (3.00, 15.00),
    "gemini-1.5-pro": (3.50, 10.50),
    "gemini-1.5-flash": (0.075, 0.30),
    "default": (2.00, 6.00),
}


class LLMRole(StrEnum):
    """Permitted message roles for LLM exchanges."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


class LLMMessage(BaseModel):
    """Single prompt message within a conversation."""

    model_config = ConfigDict(frozen=True)

    role: LLMRole = Field(..., description="Message author role.")
    content: str = Field(..., description="Text content of the message.")


class LLMRequest(BaseModel):
    """Standardized request envelope for LLM completions (legacy compatibility)."""

    model_config = ConfigDict(frozen=True)

    messages: list[LLMMessage] = Field(..., description="Prompt history messages.")
    model: str = Field(default="gpt-4o", description="Target model identifier.")
    temperature: float = Field(
        default=0.0,
        ge=0.0,
        le=2.0,
        description="Sampling temperature.",
    )
    max_tokens: int = Field(
        default=2048,
        gt=0,
        description="Maximum tokens to generate.",
    )


class LLMUsage(BaseModel):
    """Token consumption and cost accounting."""

    model_config = ConfigDict(frozen=True)

    prompt_tokens: int = Field(default=0, ge=0, description="Tokens in prompt.")
    completion_tokens: int = Field(default=0, ge=0, description="Tokens in completion.")
    total_tokens: int = Field(default=0, ge=0, description="Total tokens used.")
    estimated_cost_usd: float = Field(
        default=0.0,
        ge=0.0,
        description="Estimated USD cost.",
    )


class LLMResponse(BaseModel):
    """Standardized response envelope from an LLM completion."""

    model_config = ConfigDict(frozen=True)

    content: str = Field(..., description="Generated text completion.")
    model: str = Field(..., description="Model identifier that produced completion.")
    usage: LLMUsage = Field(default_factory=LLMUsage, description="Usage metrics.")
    cached: bool = Field(default=False, description="Whether returned from disk cache.")
    prompt_tokens: int = Field(default=0, ge=0, description="Legacy prompt tokens.")
    completion_tokens: int = Field(
        default=0, ge=0, description="Legacy completion tokens."
    )
    raw: dict[str, Any] = Field(
        default_factory=dict,
        description="Provider-specific raw response metadata.",
    )


def compute_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Compute estimated dollar cost based on model token rates."""
    rate_in, rate_out = MODEL_PRICING.get(model, MODEL_PRICING["default"])
    cost = (prompt_tokens / 1_000_000 * rate_in) + (
        completion_tokens / 1_000_000 * rate_out
    )
    return round(cost, 6)


class BaseLLMClient(ABC):
    """Abstract base class for provider-agnostic LLM clients with caching & retries."""

    def __init__(
        self,
        model: str = "gpt-4o",
        cache_dir: Path | str | None = Path(".cache/har2mcp_llm"),
        max_retries: int = 3,
        initial_backoff: float = 0.5,
    ) -> None:
        """Initialize LLM client with model and optional disk cache."""
        self.model = model
        self.cache_dir = Path(cache_dir) if cache_dir is not None else None
        self.max_retries = max_retries
        self.initial_backoff = initial_backoff

        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _compute_cache_key(
        self,
        system: str,
        messages: list[LLMMessage],
        json_schema: dict[str, Any] | None,
    ) -> str:
        """Derive deterministic SHA-256 cache key from prompt parameters."""
        payload = {
            "model": self.model,
            "system": system,
            "messages": [m.model_dump() for m in messages],
            "json_schema": json_schema,
        }
        encoded = json.dumps(payload, sort_keys=True).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _read_cache(self, key: str) -> LLMResponse | None:
        """Look up cached response from disk."""
        if not self.cache_dir:
            return None
        cache_file = self.cache_dir / f"{key}.json"
        if cache_file.is_file():
            try:
                data = json.loads(cache_file.read_text(encoding="utf-8"))
                response = LLMResponse.model_validate(data)
                return response.model_copy(update={"cached": True})
            except Exception as e:
                logger.warning("Failed to read cache file %s: %s", cache_file, e)
        return None

    def _write_cache(self, key: str, response: LLMResponse) -> None:
        """Persist successful LLM response to disk cache."""
        if not self.cache_dir:
            return
        cache_file = self.cache_dir / f"{key}.json"
        try:
            cache_file.write_text(
                json.dumps(response.model_dump(mode="json"), indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning("Failed to write cache file %s: %s", cache_file, e)

    def complete(
        self,
        system: str | LLMRequest,
        messages: Sequence[LLMMessage | dict[str, str]] | None = None,
        json_schema: dict[str, Any] | None = None,
    ) -> LLMResponse:
        """Execute LLM completion with caching, retries, and token accounting.

        Args:
            system: System instructions or legacy LLMRequest.
            messages: List of LLMMessage or role/content dicts.
            json_schema: Optional JSON Schema for structured output validation.

        Returns:
            Validated LLMResponse.
        """
        if isinstance(system, LLMRequest):
            return self.complete(
                system="",
                messages=system.messages,
                json_schema=json_schema,
            )

        # Normalize messages
        norm_messages: list[LLMMessage] = []
        for m in messages or []:
            if isinstance(m, LLMMessage):
                norm_messages.append(m)
            elif isinstance(m, dict):
                norm_messages.append(
                    LLMMessage(
                        role=LLMRole(m.get("role", "user")),
                        content=m.get("content", ""),
                    )
                )

        cache_key = self._compute_cache_key(system, norm_messages, json_schema)
        cached_resp = self._read_cache(cache_key)
        if cached_resp:
            logger.info("LLM cache hit for key %s", cache_key[:8])
            return cached_resp

        # Retry loop with exponential backoff
        last_error: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                resp = self._execute_completion(system, norm_messages, json_schema)

                # Validate JSON schema if required
                if json_schema:
                    try:
                        json.loads(resp.content)
                    except json.JSONDecodeError as err:
                        raise ValueError(
                            f"LLM output failed JSON parsing: {err}"
                        ) from err

                cost = compute_cost(
                    self.model,
                    resp.usage.prompt_tokens,
                    resp.usage.completion_tokens,
                )
                final_usage = resp.usage.model_copy(update={"estimated_cost_usd": cost})
                final_resp = resp.model_copy(
                    update={
                        "usage": final_usage,
                        "prompt_tokens": final_usage.prompt_tokens,
                        "completion_tokens": final_usage.completion_tokens,
                    }
                )

                logger.info(
                    "LLM call success (%s tokens, ~$%.4f USD)",
                    final_usage.total_tokens,
                    cost,
                )
                self._write_cache(cache_key, final_resp)
                return final_resp

            except Exception as e:
                last_error = e
                backoff = self.initial_backoff * (2**attempt)
                logger.warning(
                    "LLM completion attempt %d/%d failed: %s. Retrying in %.2fs",
                    attempt + 1,
                    self.max_retries,
                    e,
                    backoff,
                )
                time.sleep(backoff)

        raise RuntimeError(
            f"LLM completion failed after {self.max_retries} attempts: {last_error}"
        )

    def complete_json(
        self,
        system: str,
        messages: Sequence[LLMMessage | dict[str, str]],
        json_schema: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Convenience method returning parsed JSON dictionary."""
        resp = self.complete(system, messages, json_schema=json_schema)
        return json.loads(resp.content)  # type: ignore[no-any-return]

    @abstractmethod
    def _execute_completion(
        self,
        system: str,
        messages: list[LLMMessage],
        json_schema: dict[str, Any] | None,
    ) -> LLMResponse:
        """Provider-specific call implementation to be overridden by subclasses."""

    async def acomplete(self, request: LLMRequest) -> LLMResponse:
        """Asynchronous execution for legacy protocol compatibility."""
        return self.complete(
            system="",
            messages=request.messages,
        )


class FakeLLM(BaseLLMClient):
    """Deterministic mock client for testing LLM interactions and retry behaviors."""

    def __init__(
        self,
        responses: Sequence[str] | str = "{}",
        model: str = "fake-model",
        cache_dir: Path | str | None = None,
    ) -> None:
        """Initialize FakeLLM with predetermined response sequence."""
        super().__init__(
            model=model, cache_dir=cache_dir, max_retries=3, initial_backoff=0.01
        )
        if isinstance(responses, str):
            self._responses = [responses]
        else:
            self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def set_responses(self, responses: Sequence[str] | str) -> None:
        """Update queued responses."""
        if isinstance(responses, str):
            self._responses = [responses]
        else:
            self._responses = list(responses)

    def _execute_completion(
        self,
        system: str,
        messages: list[LLMMessage],
        json_schema: dict[str, Any] | None,
    ) -> LLMResponse:
        """Return next response from queued responses list."""
        self.calls.append(
            {
                "system": system,
                "messages": [m.model_dump() for m in messages],
                "json_schema": json_schema,
            }
        )
        if not self._responses:
            reply = "{}"
        elif len(self._responses) == 1:
            reply = self._responses[0]
        else:
            reply = self._responses.pop(0)

        prompt_len = sum(len(m.content) for m in messages) // 4 + len(system) // 4
        comp_len = len(reply) // 4

        return LLMResponse(
            content=reply,
            model=self.model,
            usage=LLMUsage(
                prompt_tokens=prompt_len,
                completion_tokens=comp_len,
                total_tokens=prompt_len + comp_len,
            ),
        )


class OpenAILLMClient(BaseLLMClient):
    """Standard OpenAI-compatible HTTP client for production LLM calls."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = "https://api.openai.com/v1",
        model: str = "gpt-4o",
        cache_dir: Path | str | None = None,
    ) -> None:
        """Initialize with API key and endpoint configuration."""
        super().__init__(model=model, cache_dir=cache_dir)
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self.base_url = base_url.rstrip("/")

    def _execute_completion(
        self,
        system: str,
        messages: list[LLMMessage],
        json_schema: dict[str, Any] | None,
    ) -> LLMResponse:
        """Execute OpenAI chat completion HTTP request."""
        if not self.api_key:
            raise ValueError(
                "OPENAI_API_KEY not configured. Set environment variable or pass key."
            )

        api_messages: list[dict[str, str]] = []
        if system:
            api_messages.append({"role": "system", "content": system})
        for m in messages:
            api_messages.append({"role": m.role.value, "content": m.content})

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": api_messages,
            "temperature": 0.0,
        }

        if json_schema:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "structured_output",
                    "strict": True,
                    "schema": json_schema,
                },
            }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        with httpx.Client(timeout=60.0) as client:
            resp = client.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()

        choice = data["choices"][0]
        content = choice["message"]["content"]
        raw_usage = data.get("usage", {})
        prompt_t = raw_usage.get("prompt_tokens", 0)
        comp_t = raw_usage.get("completion_tokens", 0)

        return LLMResponse(
            content=content,
            model=data.get("model", self.model),
            usage=LLMUsage(
                prompt_tokens=prompt_t,
                completion_tokens=comp_t,
                total_tokens=prompt_t + comp_t,
            ),
            raw=data,
        )


class NoOpLLMClient(FakeLLM):
    """Deterministic no-op client maintained for backward compatibility."""

    def __init__(self, fixed_reply: str = "{}") -> None:
        """Initialize with predetermined return string."""
        super().__init__(responses=fixed_reply, model="noop", cache_dir=None)

    def complete_request(self, request: LLMRequest) -> LLMResponse:
        """Legacy request-based invocation."""
        return self.complete(
            system="",
            messages=request.messages,
        )
