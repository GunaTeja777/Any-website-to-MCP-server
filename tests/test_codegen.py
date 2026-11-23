"""Integration and unit tests for server codegen using FastMCP and MCP Client."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import pytest

from har2mcp.auth.detector import detect_auth_spec
from har2mcp.auth.models import (
    AuthSpec,
    AuthType,
    CredentialLocation,
    CredentialRequirement,
)
from har2mcp.cluster.engine import cluster_entries
from har2mcp.codegen.generator import ServerGenerator, generate_server_project
from har2mcp.codegen.models import (
    RenderedParameter,
    build_rendered_tool,
    sanitize_project_name,
)
from har2mcp.design.engine import deterministic_design_tools
from har2mcp.design.models import ToolEndpointMapping, ToolParameter, ToolSpec
from har2mcp.parser.loader import load_har, parse_har
from har2mcp.redact.engine import redact
from har2mcp.schema.inferrer import infer_schemas

FIXTURES_DIR = Path(__file__).parent / "fixtures"
SECRETS_HAR_PATH = FIXTURES_DIR / "synthetic_api_with_secrets.har"


def test_sanitize_project_name() -> None:
    """Test project name sanitization for pyproject.toml."""
    assert sanitize_project_name("My Super API!") == "my-super-api"
    assert sanitize_project_name("___api__v1___") == "api-v1"
    assert sanitize_project_name("") == "mcp-server"


def test_rendered_parameter_signature() -> None:
    """Test Python signature generation for required and optional parameters."""
    req_param = RenderedParameter(
        name="user_id",
        api_name="userId",
        type_annotation="str",
        description="Target user ID",
        required=True,
    )
    assert req_param.python_signature == "user_id: str"

    opt_param_with_default = RenderedParameter(
        name="limit",
        api_name="limit",
        type_annotation="int",
        description="Page limit",
        required=False,
        default_value=25,
    )
    assert opt_param_with_default.python_signature == "limit: int = 25"

    opt_param_no_default = RenderedParameter(
        name="cursor",
        api_name="cursor",
        type_annotation="str",
        description="Pagination cursor",
        required=False,
        default_value=None,
    )
    assert opt_param_no_default.python_signature == "cursor: str | None = None"


def test_rendered_tool_parameter_sorting() -> None:
    """Test that parameters are sorted so non-default args precede default args."""
    tool = ToolSpec(
        name="search_items",
        description="Search inventory items.",
        operation_type="read",
        parameters=[
            ToolParameter(
                name="optional_flag",
                type="boolean",
                description="Optional flag",
                required=False,
                default=False,
            ),
            ToolParameter(
                name="query_text",
                type="string",
                description="Query search text",
                required=True,
            ),
        ],
        endpoints=[
            ToolEndpointMapping(
                endpoint_id="GET_/search",
                method="GET",
                path_template="/search",
            )
        ],
    )
    rendered = build_rendered_tool(tool)
    assert rendered.parameters[0].name == "query_text"
    assert rendered.parameters[1].name == "optional_flag"


def test_codegen_generates_all_five_files() -> None:
    """Test that all 5 expected project files are generated."""
    tool = ToolSpec(
        name="get_status",
        description="Check API status.",
        operation_type="read",
        parameters=[],
        endpoints=[
            ToolEndpointMapping(
                endpoint_id="GET_/status",
                method="GET",
                path_template="/status",
            )
        ],
    )
    auth = AuthSpec(
        primary_type=AuthType.API_KEY_HEADER,
        credentials=[
            CredentialRequirement(
                name="X-API-Key",
                location=CredentialLocation.HEADER,
                env_var_name="MY_API_KEY",
                description="API Key Header",
            )
        ],
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        gen = ServerGenerator()
        paths = gen.generate(
            tool_specs=[tool],
            auth_spec=auth,
            out_dir=tmpdir,
            server_name="status-server",
            base_url="https://api.status.io",
            has_pagination=True,
        )
        filenames = {p.name for p in paths}
        assert filenames == {
            "server.py",
            "pyproject.toml",
            ".env.example",
            "README.md",
            "Dockerfile",
        }

        # Check README contains tools and env vars
        readme_content = (Path(tmpdir) / "README.md").read_text(encoding="utf-8")
        assert "get_status" in readme_content
        assert "MY_API_KEY" in readme_content

        # Check .env.example
        env_content = (Path(tmpdir) / ".env.example").read_text(encoding="utf-8")
        assert "MY_API_KEY=" in env_content
        assert "API_BASE_URL=https://api.status.io" in env_content

        # Check Dockerfile
        docker_content = (Path(tmpdir) / "Dockerfile").read_text(encoding="utf-8")
        assert "ENTRYPOINT" in docker_content


def test_codegen_write_tools_omitted_by_default() -> None:
    """Verify write tools are omitted unless allow_writes=True."""
    read_tool = ToolSpec(
        name="read_data",
        description="Read data.",
        operation_type="read",
        endpoints=[
            ToolEndpointMapping(
                endpoint_id="GET_/data", method="GET", path_template="/data"
            )
        ],
    )
    write_tool = ToolSpec(
        name="delete_data",
        description="Delete data.",
        operation_type="write",
        is_write=True,
        endpoints=[
            ToolEndpointMapping(
                endpoint_id="DELETE_/data", method="DELETE", path_template="/data"
            )
        ],
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        gen = ServerGenerator()
        gen.generate(
            tool_specs=[read_tool, write_tool],
            auth_spec=AuthSpec(primary_type=AuthType.NONE),
            out_dir=tmpdir,
            allow_writes=False,
        )

        server_code = (Path(tmpdir) / "server.py").read_text(encoding="utf-8")
        assert "async def read_data" in server_code
        assert "async def delete_data" not in server_code


def test_codegen_allow_writes_includes_mutating_tools() -> None:
    """Verify write tools are included when allow_writes=True."""
    write_tool = ToolSpec(
        name="create_post",
        description="Create post.",
        operation_type="write",
        is_write=True,
        parameters=[
            ToolParameter(
                name="title",
                type="string",
                description="Post title",
                required=True,
                location="body",
            )
        ],
        endpoints=[
            ToolEndpointMapping(
                endpoint_id="POST_/posts", method="POST", path_template="/posts"
            )
        ],
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        gen = ServerGenerator()
        gen.generate(
            tool_specs=[write_tool],
            auth_spec=AuthSpec(primary_type=AuthType.NONE),
            out_dir=tmpdir,
            allow_writes=True,
        )

        server_code = (Path(tmpdir) / "server.py").read_text(encoding="utf-8")
        assert "async def create_post" in server_code


def test_generated_server_passes_ruff() -> None:
    """Verify that generated server.py passes ruff check."""
    tool = ToolSpec(
        name="get_item",
        description="Fetch a single item.",
        operation_type="read",
        parameters=[
            ToolParameter(
                name="item_id",
                type="integer",
                description="Target item ID",
                required=True,
                location="path",
            ),
            ToolParameter(
                name="verbose",
                type="boolean",
                description="Verbose flag",
                required=False,
                default=False,
                location="query",
            ),
        ],
        endpoints=[
            ToolEndpointMapping(
                endpoint_id="GET_/items/{item_id}",
                method="GET",
                path_template="/items/{item_id}",
            )
        ],
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        gen = ServerGenerator()
        gen.generate(
            tool_specs=[tool],
            auth_spec=AuthSpec(primary_type=AuthType.NONE),
            out_dir=tmpdir,
            has_pagination=True,
        )

        server_py = Path(tmpdir) / "server.py"
        result = subprocess.run(
            [sys.executable, "-m", "ruff", "check", str(server_py)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, f"Ruff check failed: {result.stdout}\n{result.stderr}"


@pytest.mark.anyio
async def test_codegen_from_fixture_har_end_to_end() -> None:
    """Generate server from fixture HAR, verify python -c 'import server', and query via MCP Client."""
    # 1. Parse and filter traffic
    filtered = parse_har(SECRETS_HAR_PATH)
    assert len(filtered) > 0

    # 2. Redact secrets
    redact_result = redact(filtered)

    # 3. Cluster endpoints
    clusters = cluster_entries(redact_result.entries)
    assert len(clusters) > 0

    # 4. Infer schemas
    schemas = infer_schemas(clusters)

    # 5. Detect auth
    auth_spec = detect_auth_spec(redact_result.entries)

    # 6. Propose tools (deterministic design)
    design_spec = deterministic_design_tools(clusters, schemas)
    assert len(design_spec.tools) > 0

    # 7. Generate server project (read-only default: allow_writes=False)
    with tempfile.TemporaryDirectory() as tmpdir:
        generated_paths = generate_server_project(
            tool_specs=design_spec.tools,
            auth_spec=auth_spec,
            out_dir=tmpdir,
            server_name="cloud-app-server",
            allow_writes=False,
        )
        assert len(generated_paths) == 5
        server_py = Path(tmpdir) / "server.py"
        assert server_py.exists()

        # 8. Test: python -c "import server" in subprocess
        env = {**os.environ, "PYTHONPATH": str(tmpdir)}
        proc = subprocess.run(
            [sys.executable, "-c", "import server; print(server.mcp.name)"],
            cwd=tmpdir,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 0, f"import server failed: {proc.stderr}"
        assert "cloud-app-server" in proc.stdout

        # 9. Test: Start server via stdio and query tools via MCP ClientSession
        params = StdioServerParameters(
            command=sys.executable,
            args=[str(server_py)],
            env=env,
        )

        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                init_res = await session.initialize()
                assert init_res.serverInfo.name == "cloud-app-server"

                tools_res = await session.list_tools()
                tool_names = [t.name for t in tools_res.tools]
                assert len(tool_names) > 0

                # In read-only mode, the login endpoint (POST /v1/auth/login) must not be present
                for tool in tool_names:
                    assert "login" not in tool.lower()
