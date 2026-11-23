"""Data models for authentication specifications and credential requirements."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class AuthType(StrEnum):
    """Categorization of authentication mechanisms."""

    BEARER_TOKEN = "bearer_token"
    COOKIE_SESSION = "cookie_session"
    API_KEY_HEADER = "api_key_header"
    API_KEY_QUERY = "api_key_query"
    CUSTOM_HEADER = "custom_header"
    NONE = "none"


class CredentialLocation(StrEnum):
    """Location where runtime credential is injected."""

    HEADER = "header"
    COOKIE = "cookie"
    QUERY = "query"


class CredentialRequirement(BaseModel):
    """Describes a single credential needed by the generated MCP server."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(..., description="Header, cookie, or query parameter name.")
    location: CredentialLocation = Field(
        ..., description="Where the credential is sent."
    )
    env_var_name: str = Field(
        ..., description="Environment variable name for supplying value."
    )
    placeholder: str | None = Field(
        default=None, description="Redacted placeholder value."
    )
    header_prefix: str | None = Field(
        default=None,
        description="Prefix to prepend to header value, e.g. 'Bearer '.",
    )
    description: str = Field(default="", description="Human-readable description.")


class TokenProvenance(BaseModel):
    """Traced origin and reuse lifecycle of an authentication token or cookie."""

    model_config = ConfigDict(frozen=True)

    placeholder: str = Field(..., description="Placeholder identifier, e.g. <TOKEN_1>.")
    origin_endpoint: str | None = Field(
        default=None,
        description="Endpoint that emitted the credential (e.g. POST /auth/login).",
    )
    origin_type: str | None = Field(
        default=None,
        description="Emission mechanism, e.g. 'set_cookie' or 'response_body'.",
    )
    reused_in_endpoints: list[str] = Field(
        default_factory=list,
        description="Subsequent endpoints observed reusing this credential.",
    )
    is_login_flow: bool = Field(
        default=False,
        description="Whether origin endpoint represents a user login flow.",
    )
    is_refresh_flow: bool = Field(
        default=False,
        description="Whether origin endpoint represents a token refresh flow.",
    )


class AuthSpec(BaseModel):
    """Complete authentication specification for generated MCP servers."""

    model_config = ConfigDict(frozen=True)

    primary_type: AuthType = Field(
        ..., description="Primary detected authentication mechanism."
    )
    credentials: list[CredentialRequirement] = Field(
        default_factory=list,
        description="Required credentials to be supplied via environment variables.",
    )
    csrf_credential: CredentialRequirement | None = Field(
        default=None,
        description="CSRF token requirement if observed.",
    )
    provenance: list[TokenProvenance] = Field(
        default_factory=list,
        description="Token provenance traces matching origins to later requests.",
    )
    has_refresh_flow: bool = Field(
        default=False,
        description="Whether a token refresh endpoint was detected.",
    )
    refresh_endpoint: str | None = Field(
        default=None,
        description="Endpoint path for refreshing tokens.",
    )
    login_endpoint: str | None = Field(
        default=None,
        description="Endpoint path where user credentials or session initiated.",
    )
    requires_csrf: bool = Field(
        default=False,
        description="Whether mutating operations require a CSRF token header.",
    )
    env_var_template: dict[str, str] = Field(
        default_factory=dict,
        description="Mapping of environment variable names to placeholder values.",
    )
