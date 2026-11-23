"""Provider-agnostic interface for LLM completions.

All LLM interactions across HAR2MCP (tool naming, clustering descriptions,
and repair loop) must pass through this interface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


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
    """Standardized request envelope for LLM completions."""

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


class LLMResponse(BaseModel):
    """Standardized response envelope from an LLM completion."""

    model_config = ConfigDict(frozen=True)

    content: str = Field(..., description="Generated text completion.")
    model: str = Field(
        ...,
        description="Model identifier that produced the completion.",
    )
    prompt_tokens: int = Field(default=0, ge=0, description="Tokens in prompt.")
    completion_tokens: int = Field(default=0, ge=0, description="Tokens in completion.")
    raw: dict[str, Any] = Field(
        default_factory=dict,
        description="Provider-specific raw response metadata.",
    )


class BaseLLMClient(ABC):
    """Abstract base class for provider-agnostic LLM clients."""

    @abstractmethod
    def complete(self, request: LLMRequest) -> LLMResponse:
        """Execute a synchronous completion request.

        Args:
            request: Standardized completion request envelope.

        Returns:
            Standardized completion response.
        """

    @abstractmethod
    async def acomplete(self, request: LLMRequest) -> LLMResponse:
        """Execute an asynchronous completion request.

        Args:
            request: Standardized completion request envelope.

        Returns:
            Standardized completion response.
        """


class NoOpLLMClient(BaseLLMClient):
    """Deterministic no-op client used for testing and offline runs."""

    def __init__(self, fixed_reply: str = "{}") -> None:
        """Initialize with a predetermined return string."""
        self._fixed_reply = fixed_reply

    def complete(self, request: LLMRequest) -> LLMResponse:
        """Return predetermined reply synchronously."""
        return LLMResponse(
            content=self._fixed_reply,
            model="noop",
            prompt_tokens=0,
            completion_tokens=0,
            raw={"request_message_count": len(request.messages)},
        )

    async def acomplete(self, request: LLMRequest) -> LLMResponse:
        """Return predetermined reply asynchronously."""
        return self.complete(request)
