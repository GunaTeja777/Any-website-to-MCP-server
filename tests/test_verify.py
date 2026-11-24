"""Tests for verification mock replay server, structural comparator, and repair loop."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import httpx
import pytest
from click.testing import CliRunner

from har2mcp.auth.detector import detect_auth_spec
from har2mcp.cli.main import cli
from har2mcp.cluster.engine import cluster_entries
from har2mcp.codegen.generator import generate_server_project
from har2mcp.design.engine import deterministic_design_tools
from har2mcp.llm.client import FakeLLM
from har2mcp.parser.loader import parse_har
from har2mcp.parser.models import Entry, Header, Request, Response, ResponseContent
from har2mcp.redact.engine import redact
from har2mcp.redact.models import SecretCategory, SecretLocation, SecretMetadata
from har2mcp.schema.inferrer import infer_schemas
from har2mcp.verify.comparator import compare_structure, extract_payload
from har2mcp.verify.models import VerificationReport
from har2mcp.verify.repair import (
    RepairLoop,
    extract_tool_code,
    redact_text_for_llm,
)
from har2mcp.verify.replay_server import ReplayServer
from har2mcp.verify.verifier import ServerVerifier

FIXTURES_DIR = Path(__file__).parent / "fixtures"
SECRETS_HAR_PATH = FIXTURES_DIR / "synthetic_api_with_secrets.har"


def make_dummy_entry(
    method: str,
    url: str,
    status: int = 200,
    body: str = '{"status": "ok"}',
    content_type: str = "application/json",
) -> Entry:
    """Create a synthetic HAR entry for testing."""
    return Entry(
        startedDateTime="2026-11-24T00:00:00.000Z",
        time=50.0,
        request=Request(
            method=method,
            url=url,
            httpVersion="HTTP/1.1",
            cookies=[],
            headers=[Header(name="Accept", value="application/json")],
            queryString=[],
            headersSize=100,
            bodySize=0,
        ),
        response=Response(
            status=status,
            statusText="OK",
            httpVersion="HTTP/1.1",
            cookies=[],
            headers=[Header(name="Content-Type", value=content_type)],
            content=ResponseContent(
                size=len(body),
                mimeType=content_type,
                text=body,
            ),
            redirectURL="",
            headersSize=100,
            bodySize=len(body),
        ),
    )


def test_replay_server_exact_and_404() -> None:
    """Test that ReplayServer matches requests and handles missing paths."""
    entry = make_dummy_entry(
        "GET", "https://api.test.com/v1/users/42", body='{"id": 42}'
    )
    with ReplayServer([entry]) as server, httpx.Client(trust_env=False) as client:
        # 1. Match recorded response
        resp = client.get(f"{server.base_url}/v1/users/42")
        assert resp.status_code == 200
        assert resp.json() == {"id": 42}

        # 2. Missing path returns 404
        missing_resp = client.get(f"{server.base_url}/v1/unknown")
        assert missing_resp.status_code == 404
        assert "error" in missing_resp.json()


def test_compare_structure_dicts_and_primitives() -> None:
    """Test schema-level structural comparison for objects and primitives."""
    # Matching dicts
    match, err, missing = compare_structure(
        {"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}
    )
    assert match is True
    assert not missing

    # Missing key
    match, err, missing = compare_structure({"id": 1, "email": "a@b.com"}, {"id": 2})
    assert match is False
    assert "email" in missing

    # Type mismatch in dict value
    match, err, missing = compare_structure({"age": 25}, {"age": "twenty-five"})
    assert match is False
    assert "structural mismatch" in (err or "").lower()

    # Lists
    match, _, _ = compare_structure([{"k": "v"}], [{"k": "other"}])
    assert match is True

    match, err, _ = compare_structure([{"k": "v"}], "not-a-list")
    assert match is False


def test_extract_payload_json_and_strings() -> None:
    """Test payload parsing from raw strings and JSON."""
    assert extract_payload('{"a": 123}') == {"a": 123}
    assert extract_payload("[1, 2, 3]") == [1, 2, 3]
    assert extract_payload("plain string") == "plain string"
    assert extract_payload(None) is None


def test_redact_text_for_llm_scrubs_secrets_and_jwt() -> None:
    """Verify raw secrets and JWT patterns are redacted before prompt dispatch."""
    raw_secret = "super_secret_password_xyz999"
    placeholder = "<SECRET_1>"
    sec_meta = SecretMetadata(
        placeholder=placeholder,
        original_value=raw_secret,
        category=SecretCategory.SECRET,
        location=SecretLocation.BODY,
        key_name="password",
        env_var_name="SECRET_VAL",
    )
    jwt_token = (
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
        "eyJzdWIiOiIxMjM0NTY3ODkwIn0."
        "do_not_leak_this"
    )

    sample_text = f"User logged in with {raw_secret} and token {jwt_token}"
    redacted = redact_text_for_llm(sample_text, {placeholder: sec_meta})

    assert raw_secret not in redacted
    assert placeholder in redacted
    assert "do_not_leak_this" not in redacted
    assert "<REDACTED_JWT>" in redacted


def test_extract_tool_code_finds_function() -> None:
    """Verify tool function extraction from server source code."""
    sample_server = (
        "import os\n\n"
        "@mcp.tool()\n"
        "async def get_user(user_id: str) -> Any:\n"
        '    """Get user."""\n'
        '    return await make_request_with_retry("GET", f"/users/{user_id}")\n\n'
        "@mcp.tool()\n"
        "async def list_items() -> Any:\n"
        '    """List items."""\n'
        '    return await make_request_with_retry("GET", "/items")\n\n'
        'if __name__ == "__main__":\n'
        "    mcp.run()\n"
    )
    code, start, end = extract_tool_code(sample_server, "get_user")
    assert code is not None
    assert "async def get_user" in code
    assert "get_user" in sample_server[start:end]

    # Non-existent tool returns None
    missing_code, _, _ = extract_tool_code(sample_server, "non_existent")
    assert missing_code is None


@pytest.mark.anyio
async def test_server_verifier_end_to_end() -> None:
    """Test full server verification against fixture HAR."""
    filtered = parse_har(SECRETS_HAR_PATH)
    redact_result = redact(filtered)
    clusters = cluster_entries(redact_result.entries)
    schemas = infer_schemas(clusters)
    auth_spec = detect_auth_spec(redact_result.entries)
    design_spec = deterministic_design_tools(clusters, schemas)

    with tempfile.TemporaryDirectory() as tmpdir:
        generate_server_project(
            tool_specs=design_spec.tools,
            auth_spec=auth_spec,
            out_dir=tmpdir,
            server_name="test-verifier-server",
            allow_writes=False,
        )

        verifier = ServerVerifier(
            server_dir=tmpdir,
            entries=redact_result.entries,
            tool_specs=design_spec.tools,
        )
        report: VerificationReport = await verifier.verify()
        assert report.total_tools > 0
        assert report.passed_tools > 0
        assert report.score > 0.0


@pytest.mark.anyio
async def test_repair_loop_fixes_broken_tool() -> None:
    """Test RepairLoop prompts LLM, patches code, and passes re-verification."""
    filtered = parse_har(SECRETS_HAR_PATH)
    redact_result = redact(filtered)
    clusters = cluster_entries(redact_result.entries)
    schemas = infer_schemas(clusters)
    auth_spec = detect_auth_spec(redact_result.entries)
    design_spec = deterministic_design_tools(clusters, schemas)

    with tempfile.TemporaryDirectory() as tmpdir:
        generate_server_project(
            tool_specs=design_spec.tools,
            auth_spec=auth_spec,
            out_dir=tmpdir,
            server_name="test-repair-server",
            allow_writes=False,
        )

        server_py = Path(tmpdir) / "server.py"
        content = server_py.read_text(encoding="utf-8")

        # Artificially break the first tool by changing its path to a broken 404 URL
        first_tool = [t for t in design_spec.tools if t.operation_type == "read"][0]
        broken_content = content.replace(
            f'path = "{first_tool.endpoints[0].path_template}"',
            'path = "/broken/404/not_found"',
        )
        server_py.write_text(broken_content, encoding="utf-8")

        # Mock LLM response with repaired function
        ep_path = first_tool.endpoints[0].path_template
        fixed_code = (
            f"@mcp.tool()\n"
            f"async def {first_tool.name}() -> Any:\n"
            f'    """Repaired function."""\n'
            f'    return await make_request_with_retry("GET", "{ep_path}")'
        )
        fake_llm_reply = json.dumps(
            {
                "tool_name": first_tool.name,
                "repaired_code": fixed_code,
                "explanation": "Fixed broken endpoint path to valid route.",
            }
        )
        llm = FakeLLM(responses=fake_llm_reply, cache_dir=None)

        loop = RepairLoop(
            server_dir=tmpdir,
            entries=redact_result.entries,
            tool_specs=design_spec.tools,
            llm_client=llm,
            redaction_map={s.placeholder: s for s in redact_result.secrets},
            max_iterations=3,
        )

        repair_report = await loop.run()
        assert len(repair_report.iterations) >= 1
        assert repair_report.iterations[0].patch_applied is True
        assert repair_report.iterations[0].passed_after_patch is True
        assert repair_report.repaired is True

        # Assert no secrets in LLM call
        assert len(llm.calls) == 1
        user_msg = llm.calls[0]["messages"][-1]["content"]
        assert "super_secret_password" not in user_msg


def test_cli_generate_verify_flag() -> None:
    """Test CLI generate command with --verify flag."""
    runner = CliRunner()
    with tempfile.TemporaryDirectory() as tmpdir:
        result = runner.invoke(
            cli,
            [
                "generate",
                str(SECRETS_HAR_PATH),
                "--out",
                str(tmpdir),
                "--verify",
            ],
        )
        assert result.exit_code == 0
        assert "Verification Suite" in result.output
        assert "Score:" in result.output
