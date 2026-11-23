"""Unit tests for schema module: JSON schema inference and pagination."""

from __future__ import annotations

import json
from pathlib import Path

from har2mcp.cluster.models import EndpointCluster, ObservedQueryParams
from har2mcp.parser import (
    Header,
    PostData,
    QueryParam,
    Request,
    Response,
    ResponseContent,
)
from har2mcp.schema import (
    PaginationType,
    calculate_confidence,
    detect_pagination,
    infer_json_schema,
    infer_schemas,
)


def test_optional_vs_required_fields_inference() -> None:
    """Test that fields are marked required only if present in all samples."""
    sample_1 = {"id": 101, "name": "Product A", "description": "High quality"}
    sample_2 = {"id": 102, "name": "Product B"}  # description is missing

    schema = infer_json_schema([sample_1, sample_2])
    assert schema is not None
    assert schema["type"] == "object"

    props = schema["properties"]
    assert "id" in props
    assert "name" in props
    assert "description" in props

    required = schema["required"]
    assert "id" in required
    assert "name" in required
    assert "description" not in required


def test_enum_detection() -> None:
    """Test low-cardinality strings are classified as enums."""
    samples = [
        {"role": "admin", "status": "active"},
        {"role": "user", "status": "pending"},
        {"role": "guest", "status": "active"},
        {"role": "admin", "status": "inactive"},
    ]

    schema = infer_json_schema(samples)
    assert schema is not None

    status_prop = schema["properties"]["status"]
    assert "enum" in status_prop
    assert set(status_prop["enum"]) == {"active", "pending", "inactive"}

    role_prop = schema["properties"]["role"]
    assert "enum" in role_prop
    assert set(role_prop["enum"]) == {"admin", "user", "guest"}


def test_nullable_field_handling() -> None:
    """Test fields holding None values in some samples are marked nullable."""
    samples = [
        {"id": 1, "avatar_url": "https://cdn.example.com/a.png"},
        {"id": 2, "avatar_url": None},
    ]

    schema = infer_json_schema(samples)
    assert schema is not None

    avatar_prop = schema["properties"]["avatar_url"]
    assert avatar_prop.get("nullable") is True
    assert "null" in avatar_prop["type"]
    assert "string" in avatar_prop["type"]


def test_timestamps_and_ids_detection() -> None:
    """Test ISO8601 date-time, epoch timestamps, and ID annotations."""
    samples = [
        {
            "id": "550e8400-e29b-41d4-a716-446655440000",
            "created_at": "2026-11-24T00:00:00Z",
            "updated_epoch": 1732406400,
            "user_id": "usr_999",
        }
    ]

    schema = infer_json_schema(samples)
    assert schema is not None
    props = schema["properties"]

    assert props["id"]["format"] == "uuid"
    assert props["created_at"]["format"] == "date-time"
    assert props["updated_epoch"]["format"] == "epoch"
    assert props["user_id"]["x-is-id"] is True


def test_nested_array_of_objects() -> None:
    """Test merging and schema inference for nested arrays of objects."""
    samples = [
        {"items": [{"item_id": "a1", "qty": 2}]},
        {"items": [{"item_id": "a2", "qty": 5, "extra": "bonus"}]},
    ]

    schema = infer_json_schema(samples)
    assert schema is not None
    items_prop = schema["properties"]["items"]
    assert items_prop["type"] == "array"

    item_schema = items_prop["items"]
    assert item_schema["type"] == "object"
    assert "item_id" in item_schema["required"]
    assert "qty" in item_schema["required"]
    assert "extra" not in item_schema["required"]


def test_pagination_page_limit_detection() -> None:
    """Test detection of page/limit pagination strategy."""
    req = Request(
        method="GET",
        url="https://api.example.com/v1/users?page=1&limit=25",
        queryString=[
            QueryParam(name="page", value="1"),
            QueryParam(name="limit", value="25"),
        ],
    )
    cluster = EndpointCluster(
        method="GET",
        path_template="/v1/users",
        sample_requests=[req],
        observed_query_params=ObservedQueryParams(
            stable={"limit": "25"},
            variable=["page"],
            optional=[],
        ),
        call_count=3,
    )

    pagination = detect_pagination(cluster)
    assert pagination is not None
    assert pagination.type == PaginationType.PAGE_LIMIT
    assert pagination.page_param == "page"
    assert pagination.limit_param == "limit"


def test_pagination_cursor_detection() -> None:
    """Test detection of cursor-based pagination."""
    req = Request(
        method="GET",
        url="https://api.example.com/v1/feed?cursor=cur_123",
        queryString=[QueryParam(name="cursor", value="cur_123")],
    )
    res = Response(
        status=200,
        content=ResponseContent(
            mimeType="application/json",
            text='{"items": [], "next_cursor": "cur_456"}',
        ),
    )
    cluster = EndpointCluster(
        method="GET",
        path_template="/v1/feed",
        sample_requests=[req],
        sample_responses=[res],
        observed_query_params=ObservedQueryParams(
            stable={},
            variable=["cursor"],
            optional=[],
        ),
        call_count=2,
    )

    pagination = detect_pagination(cluster)
    assert pagination is not None
    assert pagination.type == PaginationType.CURSOR
    assert pagination.cursor_param == "cursor"
    assert pagination.response_cursor_field == "next_cursor"


def test_pagination_link_header_detection() -> None:
    """Test detection of RFC 5988 Link header pagination."""
    req = Request(method="GET", url="https://api.example.com/v1/repos")
    res = Response(
        status=200,
        headers=[
            Header(
                name="Link",
                value='<https://api.example.com/v1/repos?page=2>; rel="next"',
            )
        ],
        content=ResponseContent(mimeType="application/json", text="[]"),
    )
    cluster = EndpointCluster(
        method="GET",
        path_template="/v1/repos",
        sample_requests=[req],
        sample_responses=[res],
        call_count=1,
    )

    pagination = detect_pagination(cluster)
    assert pagination is not None
    assert pagination.type == PaginationType.LINK_HEADER
    assert pagination.link_header_present is True


def test_confidence_score_calculation() -> None:
    """Test statistical confidence score scales monotonically with sample count."""
    score_0 = calculate_confidence(0)
    score_1 = calculate_confidence(1)
    score_2 = calculate_confidence(2)
    score_5 = calculate_confidence(5)
    score_20 = calculate_confidence(20)

    assert score_0 == 0.0
    assert 0.4 <= score_1 <= 0.6
    assert score_1 < score_2 < score_5 < score_20 <= 1.0


def test_endpoint_schema_json_dump_debug(tmp_path: Path) -> None:
    """Test inferring complete endpoint schema and writing debugging JSON dump."""
    req = Request(
        method="POST",
        url="https://api.example.com/v1/users/42/roles",
        postData=PostData(
            mimeType="application/json",
            text='{"role": "admin", "expires_in": 3600}',
        ),
    )
    res = Response(
        status=201,
        content=ResponseContent(
            mimeType="application/json",
            text='{"success": true, "assigned_at": "2026-11-24T00:00:00Z"}',
        ),
    )
    cluster = EndpointCluster(
        method="POST",
        path_template="/v1/users/{user_id}/roles",
        path_params=["user_id"],
        sample_requests=[req],
        sample_responses=[res],
        call_count=2,
    )

    schemas = infer_schemas([cluster])
    assert len(schemas) == 1
    endpoint_schema = schemas[0]

    assert endpoint_schema.method == "POST"
    assert endpoint_schema.confidence_score > 0.6
    assert endpoint_schema.path_params_schema["required"] == ["user_id"]
    assert endpoint_schema.request_body_schema is not None
    assert "role" in endpoint_schema.request_body_schema["properties"]
    assert endpoint_schema.response_body_schema is not None
    assert (
        endpoint_schema.response_body_schema["properties"]["assigned_at"]["format"]
        == "date-time"
    )

    # Test JSON dump
    debug_str = endpoint_schema.to_json_debug()
    parsed_debug = json.loads(debug_str)
    assert parsed_debug["endpoint_id"] == "POST_/v1/users/{user_id}/roles"
    assert parsed_debug["confidence_score"] == endpoint_schema.confidence_score

    dumped_file = endpoint_schema.dump_json(tmp_path / "debug_schemas")
    assert dumped_file.is_file()
    assert "POST__v1_users__user_id__roles.json" in dumped_file.name
