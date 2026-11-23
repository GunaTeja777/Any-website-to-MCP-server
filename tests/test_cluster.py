"""Unit tests for the endpoint clustering, path normalization, and GraphQL splitting."""

from __future__ import annotations

from har2mcp.cluster import (
    cluster_entries,
    looks_like_id,
)
from har2mcp.parser import (
    Entry,
    PostData,
    QueryParam,
    Request,
    Response,
    ResponseContent,
)


def make_entry(
    method: str,
    url: str,
    post_body: str | None = None,
    query_params: list[tuple[str, str]] | None = None,
) -> Entry:
    """Helper to build lightweight Entry instances for clustering tests."""
    q_params = [QueryParam(name=k, value=v) for k, v in (query_params or [])]
    post_data = (
        PostData(mimeType="application/json", text=post_body)
        if post_body is not None
        else None
    )
    req = Request(
        method=method,
        url=url,
        queryString=q_params,
        postData=post_data,
    )
    res = Response(status=200, content=ResponseContent(mimeType="application/json"))
    return Entry(request=req, response=res)


def test_tricky_nested_id_collapsing() -> None:
    """Test that nested IDs and dynamic slugs collapse to one template."""
    entry_1 = make_entry(
        "GET", "https://api.example.com/api/v2/users/123/orders/abc-uuid"
    )
    entry_2 = make_entry(
        "GET", "https://api.example.com/api/v2/users/456/orders/def-uuid"
    )

    clusters = cluster_entries([entry_1, entry_2])

    assert len(clusters) == 1
    cluster = clusters[0]
    assert cluster.method == "GET"
    assert cluster.path_template == "/api/v2/users/{user_id}/orders/{order_id}"
    assert cluster.path_params == ["user_id", "order_id"]
    assert cluster.call_count == 2
    assert len(cluster.sample_requests) == 2
    assert len(cluster.entries) == 2


def test_graphql_splitting_by_operation_name() -> None:
    """Test that /graphql endpoint splits into distinct pseudo-endpoints."""
    body_user = (
        '{"operationName": "GetUserDetails", '
        '"query": "query GetUserDetails { user { id } }"}'
    )
    body_orders = (
        '{"operationName": "ListUserOrders", '
        '"query": "query ListUserOrders { orders { id } }"}'
    )
    body_query_only = '{"query": "mutation UpdateSettings { settings { theme } }"}'

    entry_1 = make_entry("POST", "https://api.example.com/graphql", post_body=body_user)
    entry_2 = make_entry(
        "POST", "https://api.example.com/graphql", post_body=body_orders
    )
    entry_3 = make_entry(
        "POST", "https://api.example.com/graphql", post_body=body_query_only
    )
    entry_4 = make_entry("POST", "https://api.example.com/graphql", post_body=body_user)

    clusters = cluster_entries([entry_1, entry_2, entry_3, entry_4])

    assert len(clusters) == 3
    templates = {c.path_template: c for c in clusters}

    assert "/graphql#GetUserDetails" in templates
    assert "/graphql#ListUserOrders" in templates
    assert "/graphql#UpdateSettings" in templates

    user_cluster = templates["/graphql#GetUserDetails"]
    assert user_cluster.call_count == 2
    assert user_cluster.is_graphql is True
    assert user_cluster.operation_name == "GetUserDetails"


def test_query_param_stable_vs_variable_classification() -> None:
    """Test separating query parameters into stable vs variable sets."""
    entry_1 = make_entry(
        "GET",
        "https://api.example.com/v1/items?version=2&page=1&limit=50&active=true",
        query_params=[
            ("version", "2"),
            ("page", "1"),
            ("limit", "50"),
            ("active", "true"),
        ],
    )
    entry_2 = make_entry(
        "GET",
        "https://api.example.com/v1/items?version=2&page=2&limit=50",
        query_params=[("version", "2"), ("page", "2"), ("limit", "50")],
    )

    clusters = cluster_entries([entry_1, entry_2])
    assert len(clusters) == 1
    query_info = clusters[0].observed_query_params

    # 'version' had same value '2' in all requests -> stable
    assert query_info.stable.get("version") == "2"

    # 'page' varied between '1' and '2' -> variable
    assert "page" in query_info.variable

    # 'limit' is in DYNAMIC_QUERY_NAMES -> variable
    assert "limit" in query_info.variable

    # 'active' only appeared in entry_1 -> optional
    assert "active" in query_info.optional


def test_distinct_methods_create_separate_clusters() -> None:
    """Test that GET and DELETE on the same path form separate clusters."""
    entry_get = make_entry("GET", "https://api.example.com/v1/users/123")
    entry_del = make_entry("DELETE", "https://api.example.com/v1/users/123")

    clusters = cluster_entries([entry_get, entry_del])
    assert len(clusters) == 2

    methods = {c.method for c in clusters}
    assert methods == {"GET", "DELETE"}
    assert all(c.path_template == "/v1/users/{user_id}" for c in clusters)


def test_static_routes_do_not_collapse() -> None:
    """Test that static segments like /teams and /users do not wrongly collapse."""
    entry_users = make_entry("GET", "https://api.example.com/v1/users/123")
    entry_teams = make_entry("GET", "https://api.example.com/v1/teams/123")
    entry_me = make_entry("GET", "https://api.example.com/v1/users/me")

    clusters = cluster_entries([entry_users, entry_teams, entry_me])
    assert len(clusters) == 3

    templates = {c.path_template for c in clusters}
    assert "/v1/users/{user_id}" in templates
    assert "/v1/teams/{team_id}" in templates
    assert "/v1/users/me" in templates


def test_id_pattern_detection() -> None:
    """Test individual heuristic ID pattern recognizers."""
    assert looks_like_id("12345") is True
    assert looks_like_id("550e8400-e29b-41d4-a716-446655440000") is True
    assert looks_like_id("507f1f77bcf86cd799439011") is True  # mongo objectId
    assert looks_like_id("usr_98765xyz") is True
    assert looks_like_id("<TOKEN_1>") is True
    assert looks_like_id("abc-uuid") is True
    assert looks_like_id("users") is False
    assert looks_like_id("settings") is False
    assert looks_like_id("profile") is False


def test_empty_entries_clustering() -> None:
    """Test clustering an empty list returns empty list."""
    assert cluster_entries([]) == []
