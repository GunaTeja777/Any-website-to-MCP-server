"""Data models for credential redaction and placeholder mappings."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from har2mcp.parser.models import Entry


class SecretCategory(StrEnum):
    """Category classification for secret values."""

    TOKEN = "TOKEN"
    COOKIE = "COOKIE"
    CSRF = "CSRF"
    API_KEY = "API_KEY"
    SECRET = "SECRET"


class SecretLocation(StrEnum):
    """Location where secret was detected."""

    HEADER = "HEADER"
    COOKIE = "COOKIE"
    QUERY = "QUERY"
    BODY = "BODY"
    JWT = "JWT"


class SecretMetadata(BaseModel):
    """Metadata describing a single redacted secret."""

    model_config = ConfigDict(frozen=True)

    placeholder: str = Field(..., description="Stable placeholder string, e.g. <TOKEN_1>.")
    original_value: str = Field(..., description="Redacted original secret value.")
    category: SecretCategory = Field(..., description="Classification category.")
    location: SecretLocation = Field(..., description="Source location.")
    key_name: str = Field(..., description="Parameter, header, or field name.")
    env_var_name: str = Field(
        ...,
        description="Suggested runtime environment variable name.",
    )


class RedactionResult(BaseModel):
    """Result container holding sanitized entries and secret mappings."""

    model_config = ConfigDict(frozen=True)

    entries: list[Entry] = Field(..., description="Redacted entries.")
    placeholder_map: dict[str, str] = Field(
        ...,
        description="Mapping from placeholder to original secret string.",
    )
    secrets: list[SecretMetadata] = Field(
        default_factory=list,
        description="Detailed metadata for all discovered secrets.",
    )

    def get_env_template(self) -> dict[str, str]:
        """Generate environment variable key-value pairs for local testing."""
        return {s.env_var_name: s.original_value for s in self.secrets}

    def get_server_placeholder_map(self) -> dict[str, str]:
        """Generate mapping from placeholder to environment variable name."""
        return {s.placeholder: s.env_var_name for s in self.secrets}
