"""Unit tests for the auth module: credential detection and token provenance."""

from __future__ import annotations

from pathlib import Path

from har2mcp.auth import (
    AuthSpec,
    AuthType,
    CredentialLocation,
    detect_auth_spec,
    trace_token_provenance,
)
from har2mcp.parser import (
    Cookie,
    Entry,
    Header,
    QueryParam,
    Request,
    Response,
    ResponseContent,
    parse_har,
)
from har2mcp.redact import redact


def make_entry(
    method: str,
    path: str,
    req_headers: list[tuple[str, str]] | None = None,
    req_cookies: list[tuple[str, str]] | None = None,
    req_queries: list[tuple[str, str]] | None = None,
    res_headers: list[tuple[str, str]] | None = None,
    res_cookies: list[tuple[str, str]] | None = None,
    res_body: str | None = None,
) -> Entry:
    """Helper to construct Entry objects for authentication tests."""
    headers = [Header(name=k, value=v) for k, v in (req_headers or [])]
    cookies = [Cookie(name=k, value=v) for k, v in (req_cookies or [])]
    queries = [QueryParam(name=k, value=v) for k, v in (req_queries or [])]

    r_headers = [Header(name=k, value=v) for k, v in (res_headers or [])]
    r_cookies = [Cookie(name=k, value=v) for k, v in (res_cookies or [])]
    content = ResponseContent(mimeType="application/json", text=res_body)

    req = Request(
        method=method,
        url=f"https://api.example.com{path}",
        headers=headers,
        cookies=cookies,
        queryString=queries,
    )
    res = Response(status=200, headers=r_headers, cookies=r_cookies, content=content)
    return Entry(request=req, response=res)


def test_detect_bearer_token() -> None:
    """Test detecting Bearer token authentication via Authorization header."""
    entry = make_entry(
        "GET", "/v1/profile", req_headers=[("Authorization", "Bearer <TOKEN_1>")]
    )
    spec = detect_auth_spec([entry])

    assert spec.primary_type == AuthType.BEARER_TOKEN
    assert len(spec.credentials) == 1

    cred = spec.credentials[0]
    assert cred.name == "Authorization"
    assert cred.location == CredentialLocation.HEADER
    assert cred.header_prefix == "Bearer"
    assert cred.env_var_name == "HAR2MCP_AUTH_TOKEN"
    assert spec.env_var_template["HAR2MCP_AUTH_TOKEN"] == "Bearer <TOKEN_1>"


def test_detect_cookie_session() -> None:
    """Test detecting session cookie authentication."""
    entry = make_entry(
        "GET",
        "/v1/dashboard",
        req_cookies=[("session_id", "<COOKIE_1>")],
        req_headers=[("Cookie", "session_id=<COOKIE_1>")],
    )
    spec = detect_auth_spec([entry])

    assert spec.primary_type == AuthType.COOKIE_SESSION
    assert len(spec.credentials) == 1

    cred = spec.credentials[0]
    assert cred.name == "session_id"
    assert cred.location == CredentialLocation.COOKIE
    assert cred.env_var_name == "HAR2MCP_COOKIE_SESSION_ID"
    assert spec.env_var_template["HAR2MCP_COOKIE_SESSION_ID"] == "<COOKIE_1>"


def test_detect_api_key_header() -> None:
    """Test detecting API Key transmitted in request header."""
    entry = make_entry("GET", "/v1/data", req_headers=[("X-API-Key", "<TOKEN_KEY_1>")])
    spec = detect_auth_spec([entry])

    assert spec.primary_type == AuthType.API_KEY_HEADER
    assert len(spec.credentials) == 1

    cred = spec.credentials[0]
    assert cred.name == "X-API-Key"
    assert cred.location == CredentialLocation.HEADER
    assert cred.env_var_name == "HAR2MCP_API_KEY"


def test_detect_api_key_query() -> None:
    """Test detecting API Key passed in URL query parameter."""
    entry = make_entry("GET", "/v1/records", req_queries=[("api_key", "<API_KEY_1>")])
    spec = detect_auth_spec([entry])

    assert spec.primary_type == AuthType.API_KEY_QUERY
    assert len(spec.credentials) == 1

    cred = spec.credentials[0]
    assert cred.name == "api_key"
    assert cred.location == CredentialLocation.QUERY
    assert cred.env_var_name == "HAR2MCP_API_KEY"


def test_detect_csrf_token_pattern() -> None:
    """Test detecting secondary CSRF header requirement."""
    entry = make_entry(
        "POST",
        "/v1/orders",
        req_headers=[
            ("Authorization", "Bearer <TOKEN_1>"),
            ("X-CSRF-Token", "<CSRF_1>"),
        ],
    )
    spec = detect_auth_spec([entry])

    assert spec.primary_type == AuthType.BEARER_TOKEN
    assert spec.requires_csrf is True
    assert spec.csrf_credential is not None
    assert spec.csrf_credential.name == "X-CSRF-Token"
    assert spec.csrf_credential.env_var_name == "HAR2MCP_CSRF_TOKEN"
    assert "HAR2MCP_CSRF_TOKEN" in spec.env_var_template


def test_token_provenance_lifecycle() -> None:
    """Test tracing token origins (login, refresh) and subsequent request reuse."""
    # 1. Login entry emitting session cookie and access token
    entry_login = make_entry(
        "POST",
        "/api/auth/login",
        res_headers=[("Set-Cookie", "session_id=<COOKIE_1>; Path=/; HttpOnly")],
        res_cookies=[("session_id", "<COOKIE_1>")],
        res_body='{"token": "<TOKEN_1>", "refresh_token": "<TOKEN_REFRESH_1>"}',
    )
    # 2. Authenticated user call reusing session cookie and token
    entry_profile = make_entry(
        "GET",
        "/api/users/me",
        req_cookies=[("session_id", "<COOKIE_1>")],
        req_headers=[("Authorization", "Bearer <TOKEN_1>")],
    )
    # 3. Refresh endpoint returning a renewed token
    entry_refresh = make_entry(
        "POST",
        "/api/auth/token/refresh",
        res_body='{"token": "<TOKEN_2>"}',
    )
    # 4. Final call using renewed token
    entry_reports = make_entry(
        "GET",
        "/api/reports/annual",
        req_headers=[("Authorization", "Bearer <TOKEN_2>")],
    )

    entries = [entry_login, entry_profile, entry_refresh, entry_reports]
    traces = trace_token_provenance(entries)

    trace_map = {t.placeholder: t for t in traces}
    assert "<COOKIE_1>" in trace_map
    assert "<TOKEN_1>" in trace_map
    assert "<TOKEN_2>" in trace_map

    # Check login origin and reuse
    cookie_trace = trace_map["<COOKIE_1>"]
    assert cookie_trace.origin_endpoint == "POST /api/auth/login"
    assert cookie_trace.is_login_flow is True
    assert "GET /api/users/me" in cookie_trace.reused_in_endpoints

    # Check refresh origin and reuse
    refresh_trace = trace_map["<TOKEN_2>"]
    assert refresh_trace.origin_endpoint == "POST /api/auth/token/refresh"
    assert refresh_trace.is_refresh_flow is True
    assert "GET /api/reports/annual" in refresh_trace.reused_in_endpoints

    # Check overall AuthSpec synthesis
    spec = detect_auth_spec(entries)
    assert spec.has_refresh_flow is True
    assert spec.login_endpoint == "POST /api/auth/login"
    assert spec.refresh_endpoint == "POST /api/auth/token/refresh"


def test_auth_detection_on_synthetic_api_fixture(
    synthetic_api_with_secrets_path: Path,
) -> None:
    """Test running end-to-end parser -> redact -> auth detection on synthetic HAR."""
    raw_entries = parse_har(synthetic_api_with_secrets_path)
    redaction_result = redact(raw_entries)
    spec: AuthSpec = detect_auth_spec(redaction_result.entries)

    assert spec.primary_type == AuthType.BEARER_TOKEN
    assert spec.requires_csrf is True
    assert spec.csrf_credential is not None
    assert "HAR2MCP_AUTH_TOKEN" in spec.env_var_template
    assert "HAR2MCP_CSRF_TOKEN" in spec.env_var_template
    assert len(spec.provenance) >= 1
