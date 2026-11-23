"""Unit tests for the HAR parser, data models, and traffic filter."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from har2mcp.parser import (
    Entry,
    Request,
    Response,
    ResponseContent,
    is_domain_blocked,
    load_blocklist,
    load_har,
    parse_har,
)
from har2mcp.parser.filter import (
    extract_domain,
    get_apex_domain,
    infer_primary_domain,
    is_static_asset,
)


def test_load_har_valid_file(sample_har_path: Path) -> None:
    """Test loading a valid HAR file into strongly-typed Pydantic models."""
    root = load_har(sample_har_path)
    assert root.log.version == "1.2"
    assert len(root.log.entries) == 1

    entry = root.log.entries[0]
    assert entry.request.method == "GET"
    assert "https://api.example.com/v1/users" in entry.request.url
    assert entry.request.get_header("authorization") == "Bearer secret-token-xyz"
    assert entry.request.get_cookie("session_id") == "secret-cookie-value"
    assert entry.request.get_query_param("limit") == "10"
    assert entry.response.status == 200
    assert entry.response.get_header("content-type") == "application/json"


def test_load_har_invalid_source(tmp_path: Path) -> None:
    """Test loading invalid inputs raises appropriate errors."""
    with pytest.raises(ValueError, match="Invalid HAR document"):
        load_har({"not_a_har": {}})

    with pytest.raises(ValueError, match="Unsupported HAR source"):
        load_har(12345)  # type: ignore[arg-type]


def test_blocklist_matching() -> None:
    """Test tracking domain detection including subdomains."""
    blocked = load_blocklist()
    assert is_domain_blocked("google-analytics.com", blocked)
    assert is_domain_blocked("www.google-analytics.com", blocked)
    assert is_domain_blocked("api.mixpanel.com", blocked)
    assert not is_domain_blocked("api.example.com", blocked)
    assert not is_domain_blocked("", blocked)


def test_custom_blocklist_file(tmp_path: Path) -> None:
    """Test custom blocklist JSON and line-delimited loading."""
    custom_json = tmp_path / "custom_block.json"
    custom_json.write_text(
        '["custom-tracker.com", "telemetry.bad.org"]', encoding="utf-8"
    )
    blocked = load_blocklist(custom_json)
    assert "custom-tracker.com" in blocked
    assert "telemetry.bad.org" in blocked
    assert "google-analytics.com" in blocked  # default retained

    custom_txt = tmp_path / "custom_block.txt"
    custom_txt.write_text("# Comment\ncustom-txt-tracker.net\n\n", encoding="utf-8")
    blocked_txt = load_blocklist(custom_txt)
    assert "custom-txt-tracker.net" in blocked_txt


def test_domain_extraction_helpers() -> None:
    """Test domain and apex extraction utility functions."""
    assert extract_domain("https://api.example.com:8080/v1/path") == "api.example.com"
    assert extract_domain("not-a-valid-url") == ""
    assert get_apex_domain("api.sub.example.com") == "example.com"
    assert get_apex_domain("example.com") == "example.com"
    assert get_apex_domain("localhost") == "localhost"


def test_static_asset_filtering() -> None:
    """Test dropping static media, script, and style files."""
    req = Request(method="GET", url="https://example.com/assets/logo.png")
    res = Response(status=200, content=ResponseContent(mimeType="image/png"))
    assert is_static_asset(req.url, req, res, "image") is True

    req_css = Request(method="GET", url="https://example.com/app.css")
    res_css = Response(status=200, content=ResponseContent(mimeType="text/css"))
    assert is_static_asset(req_css.url, req_css, res_css, "stylesheet") is True

    req_api = Request(method="GET", url="https://example.com/api/v1/data")
    res_api = Response(status=200, content=ResponseContent(mimeType="application/json"))
    assert is_static_asset(req_api.url, req_api, res_api, "fetch") is False


def test_mixed_traffic_filtering(synthetic_mixed_traffic_path: Path) -> None:
    """Test that mixed traffic drops assets, tracking, and third parties by default."""
    entries = parse_har(synthetic_mixed_traffic_path)

    # In synthetic_mixed_traffic:
    # 1. api.example.com/v1/data (KEEP)
    # 2. logo.png (DROP)
    # 3. app.min.css (DROP)
    # 4. vendor.chunk.js (DROP)
    # 5. inter.woff2 (DROP)
    # 6. google-analytics.com (DROP)
    # 7. mixpanel.com (DROP)
    # 8. unrelated-third-party.com (DROP by default)
    # 9. partner-api.com (DROP by default)
    assert len(entries) == 1
    assert entries[0].request.url == "https://api.example.com/v1/data"


def test_mixed_traffic_include_domain(synthetic_mixed_traffic_path: Path) -> None:
    """Test including explicit third-party domains via include_domains."""
    entries = parse_har(
        synthetic_mixed_traffic_path,
        include_domains=["partner-api.com"],
    )
    assert len(entries) == 2
    urls = [e.request.url for e in entries]
    assert "https://api.example.com/v1/data" in urls
    assert "https://partner-api.com/v1/items" in urls
    assert "https://unrelated-third-party.com/api/test" not in urls


def test_edge_cases_handling(synthetic_edge_cases_path: Path) -> None:
    """Test base64 decoding, missing bodies, and GraphQL handling."""
    entries = parse_har(synthetic_edge_cases_path)
    assert len(entries) == 5

    # 1. Base64 decoded entry
    b64_entry = next(e for e in entries if "/b64data" in e.request.url)
    assert b64_entry.response.content.encoding is None
    assert "decoded_successfully" in (b64_entry.response.content.text or "")
    parsed_b64 = json.loads(b64_entry.response.content.text or "{}")
    assert parsed_b64["secret_key"] == "top_secret_b64_9999"

    # 2. 204 No Content (missing body)
    delete_entry = next(e for e in entries if e.request.method == "DELETE")
    assert delete_entry.response.status == 204
    assert delete_entry.response.content.text is None

    # 3. Request without postData
    status_entry = next(e for e in entries if "/status" in e.request.url)
    assert status_entry.request.post_data is None

    # 4. GraphQL request & response
    gql_entry = next(e for e in entries if "/graphql" in e.request.url)
    assert gql_entry.request.post_data is not None
    assert "query GetUser" in (gql_entry.request.post_data.text or "")

    # 5. Non-standard JSON MIME (application/vnd.api+json)
    articles_entry = next(e for e in entries if "/articles" in e.request.url)
    assert (
        articles_entry.response.get_header("content-type") == "application/vnd.api+json"
    )


def test_infer_primary_domain() -> None:
    """Test inferring primary domain from entry URLs."""
    entries = [
        Entry(
            request=Request(method="GET", url="https://api.service.io/v1/a"),
            response=Response(status=200),
        ),
        Entry(
            request=Request(method="GET", url="https://api.service.io/v1/b"),
            response=Response(status=200),
        ),
        Entry(
            request=Request(method="GET", url="https://cdn.other.com/item"),
            response=Response(status=200),
        ),
    ]
    primary = infer_primary_domain(entries)
    assert primary == "api.service.io"
