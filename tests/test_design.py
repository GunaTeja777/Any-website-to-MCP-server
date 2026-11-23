"""Unit tests for the design module: MCP tool synthesis, validation, and repair loop."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from har2mcp.cluster.models import EndpointCluster, ObservedQueryParams
from har2mcp.design import (
    ToolDesigner,
    ToolParameter,
    ToolSpec,
    deterministic_design_tools,
)
from har2mcp.llm import FakeLLM
from har2mcp.parser import Request, Response
from har2mcp.schema.models import EndpointSchema


def make_dummy_cluster(
    method: str, path: str, path_params: list[str] | None = None
) -> EndpointCluster:
    """Build lightweight cluster for testing tool designer."""
    return EndpointCluster(
        method=method,
        path_template=path,
        path_params=path_params or [],
        sample_requests=[Request(method=method, url=f"https://api.example.com{path}")],
        sample_responses=[Response(status=200)],
        observed_query_params=ObservedQueryParams(),
        call_count=1,
    )


def test_tool_spec_validation_snake_case() -> None:
    """Test that tool names and parameter names must strictly be snake_case."""
    valid_tool = ToolSpec(
        name="get_user_profile",
        description="Fetch user profile",
        operation_type="read",
        parameters=[ToolParameter(name="user_id", description="ID of user")],
    )
    assert valid_tool.name == "get_user_profile"
    assert valid_tool.is_write is False

    with pytest.raises((ValueError, ValidationError), match="snake_case"):
        ToolSpec(name="GetUserProfile", description="Invalid PascalCase")

    with pytest.raises((ValueError, ValidationError), match="snake_case"):
        ToolParameter(name="User-ID", description="Invalid kebab case")


def test_tool_designer_successful_llm_synthesis() -> None:
    """Test ToolDesigner synthesizes valid tools using FakeLLM."""
    mock_design = {
        "server_name": "user_api_server",
        "description": "User management server",
        "tools": [
            {
                "name": "get_user",
                "description": "Fetch user by ID",
                "operation_type": "read",
                "parameters": [
                    {
                        "name": "user_id",
                        "type": "string",
                        "description": "Unique user ID",
                        "required": True,
                        "location": "path",
                    }
                ],
                "endpoints": [
                    {
                        "endpoint_id": "GET_/v1/users/{user_id}",
                        "method": "GET",
                        "path_template": "/v1/users/{user_id}",
                    }
                ],
            }
        ],
    }

    llm = FakeLLM(responses=json.dumps(mock_design), cache_dir=None)
    designer = ToolDesigner(llm_client=llm)

    cluster = make_dummy_cluster("GET", "/v1/users/{user_id}", path_params=["user_id"])
    schema = EndpointSchema(
        endpoint_id="GET_/v1/users/{user_id}",
        method="GET",
        path_template="/v1/users/{user_id}",
    )

    spec = designer.design([cluster], [schema])
    assert spec.server_name == "user_api_server"
    assert len(spec.tools) == 1
    assert spec.tools[0].name == "get_user"
    assert len(spec.read_tools) == 1
    assert len(spec.write_tools) == 0


def test_tool_designer_repair_loop_on_validation_failure() -> None:
    """Test that ToolDesigner catches validation errors and recovers on re-prompt."""
    # Attempt 1: invalid tool name "Invalid Tool Name"
    bad_output = {
        "server_name": "test_server",
        "description": "Test server",
        "tools": [
            {
                "name": "Invalid Tool Name",  # Not snake_case!
                "description": "Bad tool name",
            }
        ],
    }
    # Attempt 2: corrected snake_case name "list_items"
    good_output = {
        "server_name": "test_server",
        "description": "Test server",
        "tools": [
            {
                "name": "list_items",
                "description": "Corrected snake_case tool",
                "operation_type": "read",
            }
        ],
    }

    llm = FakeLLM(
        responses=[json.dumps(bad_output), json.dumps(good_output)],
        cache_dir=None,
    )
    designer = ToolDesigner(llm_client=llm, max_retries=2)

    cluster = make_dummy_cluster("GET", "/v1/items")
    schema = EndpointSchema(
        endpoint_id="GET_/v1/items",
        method="GET",
        path_template="/v1/items",
    )

    spec = designer.design([cluster], [schema])
    assert len(spec.tools) == 1
    assert spec.tools[0].name == "list_items"
    # Verifies repair loop was triggered and re-prompted LLM
    assert len(llm.calls) == 2
    assert "Your output failed validation" in llm.calls[1]["messages"][-1]["content"]


def test_tool_designer_exhausts_retries() -> None:
    """Test that ToolDesigner raises ValueError when LLM repeatedly fails."""
    bad_output = {"invalid_key": 123}
    llm = FakeLLM(responses=json.dumps(bad_output), cache_dir=None)
    designer = ToolDesigner(llm_client=llm, max_retries=2)

    cluster = make_dummy_cluster("GET", "/v1/items")
    schema = EndpointSchema(
        endpoint_id="GET_/v1/items",
        method="GET",
        path_template="/v1/items",
    )

    with pytest.raises(ValueError, match="Failed to synthesize valid ToolSpec"):
        designer.design([cluster], [schema])


def test_deterministic_design_fallback() -> None:
    """Test deterministic tool synthesis when LLM client is None."""
    c1 = make_dummy_cluster("GET", "/v1/users/{user_id}", path_params=["user_id"])
    c2 = make_dummy_cluster("POST", "/v1/users")
    c3 = make_dummy_cluster("DELETE", "/v1/users/{user_id}", path_params=["user_id"])

    s1 = EndpointSchema(
        endpoint_id="GET_/v1/users/{user_id}",
        method="GET",
        path_template="/v1/users/{user_id}",
    )
    s2 = EndpointSchema(
        endpoint_id="POST_/v1/users", method="POST", path_template="/v1/users"
    )
    s3 = EndpointSchema(
        endpoint_id="DELETE_/v1/users/{user_id}",
        method="DELETE",
        path_template="/v1/users/{user_id}",
    )

    spec = deterministic_design_tools(
        [c1, c2, c3], [s1, s2, s3], server_name="users_service"
    )
    assert len(spec.tools) == 3

    tool_names = [t.name for t in spec.tools]
    assert "get_user" in tool_names
    assert "create_user" in tool_names
    assert "delete_user" in tool_names

    assert len(spec.read_tools) == 1
    assert len(spec.write_tools) == 2
