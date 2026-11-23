"""Authentication pattern detection, credential specification, and provenance tracing."""

from har2mcp.auth.detector import detect_auth_spec, trace_token_provenance
from har2mcp.auth.models import (
    AuthSpec,
    AuthType,
    CredentialLocation,
    CredentialRequirement,
    TokenProvenance,
)

__all__ = [
    "AuthSpec",
    "AuthType",
    "CredentialLocation",
    "CredentialRequirement",
    "TokenProvenance",
    "detect_auth_spec",
    "trace_token_provenance",
]
