"""Prompt templates and context serialization for LLM-assisted tool design."""

from __future__ import annotations

import json
from collections.abc import Sequence

from har2mcp.cluster.models import EndpointCluster
from har2mcp.schema.models import EndpointSchema

SYSTEM_DESIGN_PROMPT = """You are an expert Model Context Protocol (MCP) tool designer.
Your task is to analyze recorded API endpoints and their inferred schemas,
then synthesize a set of clean, high-quality MCP tools for AI assistants.

Design Rules:
1. Meaningful Actions: Create one tool per user-meaningful action, not per URL.
2. Tool Naming: Names MUST be lowercase snake_case (e.g. `list_projects`).
3. Agent Descriptions: Write clear, informative descriptions explaining what the
   tool does, when an agent should call it, and what it returns.
4. Parameters: Keep parameter lists concise with clear descriptions.
5. Operation Type: Classify each tool as 'read' (non-mutating) or 'write' (mutating).
6. Endpoint Mapping: Connect each tool to its underlying endpoint ID and path template.
7. Return strictly valid JSON conforming to the requested schema.
"""


def format_endpoints_context(
    clusters: Sequence[EndpointCluster],
    schemas: Sequence[EndpointSchema],
) -> str:
    """Serialize clusters and schemas into structured text for prompt context."""
    schema_map = {s.endpoint_id: s for s in schemas}
    lines: list[str] = ["Here are the observed API endpoints:"]

    for i, cluster in enumerate(clusters, 1):
        endpoint_id = f"{cluster.method}_{cluster.path_template}"
        sch = schema_map.get(endpoint_id)

        lines.append(
            f"\n--- Endpoint {i}: {cluster.method} {cluster.path_template} ---"
        )
        lines.append(f"Endpoint ID: {endpoint_id}")
        lines.append(f"Observed Calls: {cluster.call_count}")
        if cluster.is_graphql:
            lines.append(f"GraphQL Operation: {cluster.operation_name}")

        if cluster.path_params:
            lines.append(f"Path Parameters: {', '.join(cluster.path_params)}")

        stable_q = cluster.observed_query_params.stable
        if stable_q:
            lines.append(f"Stable Query Params: {stable_q}")

        var_q = cluster.observed_query_params.variable
        if var_q:
            lines.append(f"Variable Query Params: {', '.join(var_q)}")

        if sch and sch.pagination:
            lines.append(
                f"Pagination Pattern: {sch.pagination.type.value} "
                f"({sch.pagination.description})"
            )

        if sch and sch.request_body_schema:
            req_json = json.dumps(sch.request_body_schema, indent=2)
            lines.append(f"Request Body Schema:\n{req_json}")

        if sch and sch.response_body_schema:
            res_json = json.dumps(sch.response_body_schema, indent=2)
            lines.append(f"Response Body Schema:\n{res_json}")

    lines.append(
        "\nSynthesize a complete DesignSpec JSON object with a server_name, "
        "description, and list of tools."
    )
    return "\n".join(lines)
