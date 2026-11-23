"""Authentication pattern detection and token provenance tracing."""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Sequence
from urllib.parse import urlparse

from har2mcp.auth.models import (
    AuthSpec,
    AuthType,
    CredentialLocation,
    CredentialRequirement,
    TokenProvenance,
)
from har2mcp.parser.models import Entry
from har2mcp.redact.engine import API_KEY_PARAM_NAMES, CSRF_HEADER_NAMES

API_KEY_HEADERS: frozenset[str] = frozenset(
    {
        "x-api-key",
        "api-key",
        "apikey",
        "x-auth-token",
        "x-access-token",
        "x-api-token",
    }
)

SESSION_COOKIE_NAMES: frozenset[str] = frozenset(
    {
        "session_id",
        "sessionid",
        "session",
        "sid",
        "connect.sid",
        "jsessionid",
        "phpsessid",
        "auth_token",
        "authtoken",
        "token",
        "remember_token",
    }
)

LOGIN_KEYWORDS: tuple[str, ...] = ("login", "signin", "authenticate", "session", "auth")
REFRESH_KEYWORDS: tuple[str, ...] = ("refresh", "renew", "token")
PLACEHOLDER_REGEX: re.Pattern[str] = re.compile(r"<[A-Z_]+_\d+>")


def _get_endpoint_signature(entry: Entry) -> str:
    """Derive method and normalized path signature for an entry."""
    method = entry.request.method.upper()
    path = urlparse(entry.request.url).path
    return f"{method} {path}"


def trace_token_provenance(entries: Sequence[Entry]) -> list[TokenProvenance]:
    """Trace which earlier response emitted a token/cookie that later requests reuse.

    Args:
        entries: Chronologically ordered HTTP exchanges.

    Returns:
        List of TokenProvenance objects mapping origins to reuses.
    """
    emitted_map: dict[str, tuple[str, str, bool, bool]] = {}
    reused_map: dict[str, list[str]] = defaultdict(list)

    for entry in entries:
        sig = _get_endpoint_signature(entry)
        path_lower = urlparse(entry.request.url).path.lower()
        is_login = any(k in path_lower for k in LOGIN_KEYWORDS)
        is_refresh = any(k in path_lower for k in REFRESH_KEYWORDS) and not is_login

        # 1. Inspect response headers (e.g. Set-Cookie)
        for h in entry.response.headers:
            matches = PLACEHOLDER_REGEX.findall(h.value)
            origin_type = "set_cookie" if h.name.lower() == "set-cookie" else "response_header"
            for m in matches:
                if m not in emitted_map:
                    emitted_map[m] = (sig, origin_type, is_login, is_refresh)

        # 2. Inspect response cookies list
        for c in entry.response.cookies:
            if c.value.startswith("<") and c.value.endswith(">"):
                if c.value not in emitted_map:
                    emitted_map[c.value] = (sig, "set_cookie", is_login, is_refresh)

        # 3. Inspect response body text
        if entry.response.content.text:
            matches = PLACEHOLDER_REGEX.findall(entry.response.content.text)
            for m in matches:
                if m not in emitted_map:
                    emitted_map[m] = (sig, "response_body", is_login, is_refresh)

        # 4. Check request reuse of previously or globally emitted placeholders
        req_placeholders: set[str] = set()
        for h in entry.request.headers:
            req_placeholders.update(PLACEHOLDER_REGEX.findall(h.value))
        for c in entry.request.cookies:
            req_placeholders.update(PLACEHOLDER_REGEX.findall(c.value))
        for q in entry.request.query_string:
            req_placeholders.update(PLACEHOLDER_REGEX.findall(q.value))

        for p in req_placeholders:
            if p in emitted_map and sig != emitted_map[p][0]:
                if sig not in reused_map[p]:
                    reused_map[p].append(sig)

    provenances: list[TokenProvenance] = []
    for placeholder, (origin_sig, origin_type, is_login, is_refresh) in emitted_map.items():
        reused_endpoints = reused_map.get(placeholder, [])
        provenances.append(
            TokenProvenance(
                placeholder=placeholder,
                origin_endpoint=origin_sig,
                origin_type=origin_type,
                reused_in_endpoints=reused_endpoints,
                is_login_flow=is_login,
                is_refresh_flow=is_refresh,
            )
        )

    return provenances


def detect_auth_spec(entries: Sequence[Entry]) -> AuthSpec:
    """Analyze HTTP exchanges and extract the primary authentication specification.

    Args:
        entries: Candidate API exchanges.

    Returns:
        Structured AuthSpec model with credential mappings and provenance.
    """
    if not entries:
        return AuthSpec(primary_type=AuthType.NONE)

    provenance_traces = trace_token_provenance(entries)

    # Detect login / refresh endpoints from provenance
    login_endpoint: str | None = None
    refresh_endpoint: str | None = None
    for p in provenance_traces:
        if p.is_login_flow and not login_endpoint:
            login_endpoint = p.origin_endpoint
        if p.is_refresh_flow and not refresh_endpoint:
            refresh_endpoint = p.origin_endpoint

    has_bearer = False
    bearer_credential: CredentialRequirement | None = None

    cookie_credentials: dict[str, CredentialRequirement] = {}
    api_key_header_cred: CredentialRequirement | None = None
    api_key_query_cred: CredentialRequirement | None = None
    csrf_cred: CredentialRequirement | None = None

    for entry in entries:
        req = entry.request

        # 1. Check Authorization header
        auth_header = req.get_header("authorization")
        if auth_header:
            trimmed = auth_header.strip()
            parts = trimmed.split(None, 1)
            prefix = parts[0] if len(parts) == 2 else None
            token_val = parts[1] if len(parts) == 2 else trimmed

            has_bearer = True
            if not bearer_credential:
                bearer_credential = CredentialRequirement(
                    name="Authorization",
                    location=CredentialLocation.HEADER,
                    env_var_name="HAR2MCP_AUTH_TOKEN",
                    placeholder=token_val,
                    header_prefix=prefix,
                    description=f"Authorization header with {prefix or 'token'}",
                )

        # 2. Check CSRF header
        for h in req.headers:
            if h.name.lower() in CSRF_HEADER_NAMES and not csrf_cred:
                csrf_cred = CredentialRequirement(
                    name=h.name,
                    location=CredentialLocation.HEADER,
                    env_var_name="HAR2MCP_CSRF_TOKEN",
                    placeholder=h.value,
                    description=f"CSRF protection token ({h.name})",
                )

        # 3. Check API Key Header
        for h in req.headers:
            if h.name.lower() in API_KEY_HEADERS and not api_key_header_cred:
                api_key_header_cred = CredentialRequirement(
                    name=h.name,
                    location=CredentialLocation.HEADER,
                    env_var_name="HAR2MCP_API_KEY",
                    placeholder=h.value,
                    description=f"API key header ({h.name})",
                )

        # 4. Check Cookies
        for c in req.cookies:
            cname_clean = c.name.strip().lower()
            if cname_clean in SESSION_COOKIE_NAMES and c.name not in cookie_credentials:
                env_key = f"HAR2MCP_COOKIE_{re.sub(r'[^A-Z0-9_]', '_', c.name.upper())}"
                cookie_credentials[c.name] = CredentialRequirement(
                    name=c.name,
                    location=CredentialLocation.COOKIE,
                    env_var_name=env_key,
                    placeholder=c.value,
                    description=f"Session authentication cookie '{c.name}'",
                )

        # 5. Check API Key Query Parameter
        for q in req.query_string:
            if q.name.lower() in API_KEY_PARAM_NAMES and not api_key_query_cred:
                api_key_query_cred = CredentialRequirement(
                    name=q.name,
                    location=CredentialLocation.QUERY,
                    env_var_name="HAR2MCP_API_KEY",
                    placeholder=q.value,
                    description=f"API key query parameter '{q.name}'",
                )

    # Determine primary auth mechanism
    primary_type = AuthType.NONE
    credentials: list[CredentialRequirement] = []

    if has_bearer and bearer_credential:
        primary_type = AuthType.BEARER_TOKEN
        credentials.append(bearer_credential)
    elif cookie_credentials:
        primary_type = AuthType.COOKIE_SESSION
        credentials.extend(cookie_credentials.values())
    elif api_key_header_cred:
        primary_type = AuthType.API_KEY_HEADER
        credentials.append(api_key_header_cred)
    elif api_key_query_cred:
        primary_type = AuthType.API_KEY_QUERY
        credentials.append(api_key_query_cred)

    # Build .env template mapping
    env_template: dict[str, str] = {}
    for cred in credentials:
        prefix_str = f"{cred.header_prefix} " if cred.header_prefix else ""
        env_template[cred.env_var_name] = f"{prefix_str}{cred.placeholder or '<YOUR_VALUE>'}"

    if csrf_cred:
        env_template[csrf_cred.env_var_name] = csrf_cred.placeholder or "<YOUR_CSRF_TOKEN>"

    return AuthSpec(
        primary_type=primary_type,
        credentials=credentials,
        csrf_credential=csrf_cred,
        provenance=provenance_traces,
        has_refresh_flow=refresh_endpoint is not None,
        refresh_endpoint=refresh_endpoint,
        login_endpoint=login_endpoint,
        requires_csrf=csrf_cred is not None,
        env_var_template=env_template,
    )
