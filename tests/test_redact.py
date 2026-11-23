"""Unit tests for credential redaction, placeholder mapping, and security scrub."""

from __future__ import annotations

import json
from pathlib import Path

from har2mcp.parser import parse_har
from har2mcp.redact import (
    RedactionEngine,
    RedactionResult,
    SecretCategory,
    SecretLocation,
    redact,
)


def test_redact_synthetic_api_with_secrets(
    synthetic_api_with_secrets_path: Path,
) -> None:
    """Test redaction of Authorization, Cookies, CSRF, query keys, and JSON bodies."""
    entries = parse_har(synthetic_api_with_secrets_path)
    assert len(entries) == 3

    # Collect known raw secrets before redaction
    known_secrets = [
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIiwiaWF0IjoxNTE2MjM5MDIyfQ.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c",
        "csrf_secret_token_987654321",
        "sess_cookie_secret_abcdef12345",
        "super_secret_password_xyz999",
        "cs_secret_val_8888",
        "auth_token_secret_value_7777",
        "my_api_key_secret_3333",
    ]

    result: RedactionResult = redact(entries)
    assert len(result.entries) == 3

    # 1. Authorization header: should preserve scheme 'Bearer ' with stable placeholder
    req_0 = result.entries[0].request
    auth_header = req_0.get_header("authorization") or ""
    assert auth_header.startswith("Bearer <TOKEN_")
    token_placeholder = auth_header.split()[1]
    assert result.placeholder_map[token_placeholder] == known_secrets[0]

    # 2. X-CSRF-Token header
    csrf_header = req_0.get_header("x-csrf-token") or ""
    assert csrf_header.startswith("<CSRF_")
    assert result.placeholder_map[csrf_header] == "csrf_secret_token_987654321"

    # 3. Cookie header and cookie object
    cookie_header = req_0.get_header("cookie") or ""
    assert "session_id=<COOKIE_" in cookie_header
    assert req_0.get_cookie("session_id") == "<COOKIE_1>"
    assert result.placeholder_map["<COOKIE_1>"] == "sess_cookie_secret_abcdef12345"

    # 4. Request JSON body fields (password, client_secret) in entry 1
    req_1 = result.entries[1].request
    assert req_1.post_data is not None
    post_json = json.loads(req_1.post_data.text or "{}")
    assert post_json["username"] == "admin_user"
    assert post_json["password"].startswith("<SECRET_")
    assert post_json["client_secret"].startswith("<SECRET_")
    assert (
        result.placeholder_map[post_json["password"]] == "super_secret_password_xyz999"
    )
    assert result.placeholder_map[post_json["client_secret"]] == "cs_secret_val_8888"

    # 5. Query parameter api_key in entry 2
    req_2 = result.entries[2].request
    api_key_val = req_2.get_query_param("api_key") or ""
    assert api_key_val.startswith("<API_KEY_")
    assert result.placeholder_map[api_key_val] == "my_api_key_secret_3333"
    assert f"api_key={api_key_val}" in req_2.url
    assert "my_api_key_secret_3333" not in req_2.url

    # CRUCIAL TEST: Assert that NO original secret survives in redacted output
    dumped_output = json.dumps([e.model_dump() for e in result.entries])
    for secret in known_secrets:
        assert secret not in dumped_output, (
            f"Leakage detected! Secret '{secret}' found in output: {dumped_output}"
        )


def test_placeholder_stability() -> None:
    """Test that identical secret values reuse the exact same placeholder."""
    engine = RedactionEngine()

    p1 = engine.get_or_register(
        "secret_val_123",
        SecretCategory.TOKEN,
        SecretLocation.HEADER,
        "token_1",
    )
    p2 = engine.get_or_register(
        "secret_val_123",
        SecretCategory.TOKEN,
        SecretLocation.BODY,
        "token_2",
    )
    assert p1 == p2 == "<TOKEN_1>"


def test_jwt_regex_scrubbing_in_body() -> None:
    """Test that arbitrary JWT-shaped strings embedded in text bodies are scrubbed."""
    raw_jwt = (
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
        "eyJ1c2VyX2lkIjoiOTk5OSJ9."
        "4flKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
    )
    engine = RedactionEngine()
    body_text = f'{{"note": "User JWT token is {raw_jwt}"}}'
    scrubbed = engine.redact_body_text(body_text) or ""

    assert raw_jwt not in scrubbed
    assert "<TOKEN_" in scrubbed


def test_env_mappings_and_metadata(synthetic_api_with_secrets_path: Path) -> None:
    """Test helper functions generating environment variables and server maps."""
    entries = parse_har(synthetic_api_with_secrets_path)
    result = redact(entries)

    env_template = result.get_env_template()
    server_map = result.get_server_placeholder_map()

    assert len(env_template) == len(result.secrets)
    assert len(server_map) == len(result.secrets)

    for placeholder, env_name in server_map.items():
        assert placeholder.startswith("<") and placeholder.endswith(">")
        assert env_name.startswith("HAR2MCP_")
        assert env_name in env_template
