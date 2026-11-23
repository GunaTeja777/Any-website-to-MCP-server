"""Tool designer engine generating validated MCP tool specifications using an LLM."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Sequence
from typing import Literal

from pydantic import ValidationError

from har2mcp.cluster.models import EndpointCluster
from har2mcp.cluster.normalizer import singularize
from har2mcp.design.models import (
    DesignSpec,
    ToolEndpointMapping,
    ToolParameter,
    ToolSpec,
)
from har2mcp.design.prompts import SYSTEM_DESIGN_PROMPT, format_endpoints_context
from har2mcp.llm.client import BaseLLMClient, LLMMessage, LLMRole
from har2mcp.schema.models import EndpointSchema

logger = logging.getLogger("har2mcp.design")


def derive_deterministic_tool_name(
    method: str, path_template: str, is_graphql: bool
) -> str:
    """Generate a clean snake_case tool name deterministically from HTTP route cues."""
    if is_graphql and "#" in path_template:
        op_name = path_template.split("#", 1)[1]
        # Convert PascalCase / camelCase to snake_case
        s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", op_name)
        return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1).lower()

    clean_path = path_template.split("?")[0].strip("/")
    segments = [
        s
        for s in clean_path.split("/")
        if s and not s.startswith("v1") and not s.startswith("v2") and s != "api"
    ]
    if not segments:
        return f"{method.lower()}_root"

    last_seg = segments[-1]
    is_item_lookup = last_seg.startswith("{") and last_seg.endswith("}")

    if is_item_lookup and len(segments) >= 2:
        parent = singularize(segments[-2].replace("{", "").replace("}", ""))
        if method == "GET":
            return f"get_{parent}"
        if method in {"DELETE"}:
            return f"delete_{parent}"
        if method in {"PUT", "PATCH"}:
            return f"update_{parent}"
        return f"{method.lower()}_{parent}"

    clean_seg = last_seg.replace("{", "").replace("}", "")
    if method == "GET":
        return f"list_{clean_seg}" if clean_seg.endswith("s") else f"get_{clean_seg}"
    if method == "POST":
        return f"create_{singularize(clean_seg)}"
    if method == "DELETE":
        return f"delete_{singularize(clean_seg)}"
    if method in {"PUT", "PATCH"}:
        return f"update_{singularize(clean_seg)}"

    return f"{method.lower()}_{clean_seg}"


def deterministic_design_tools(
    clusters: Sequence[EndpointCluster],
    schemas: Sequence[EndpointSchema],
    server_name: str = "generated_api_server",
) -> DesignSpec:
    """Synthesize tool specifications deterministically without requiring an LLM."""
    schema_map = {s.endpoint_id: s for s in schemas}
    tools: list[ToolSpec] = []
    used_names: set[str] = set()

    for cluster in clusters:
        endpoint_id = f"{cluster.method}_{cluster.path_template}"
        base_name = derive_deterministic_tool_name(
            cluster.method, cluster.path_template, cluster.is_graphql
        )
        tool_name = base_name
        counter = 2
        while tool_name in used_names:
            tool_name = f"{base_name}_{counter}"
            counter += 1
        used_names.add(tool_name)

        is_write = cluster.method in {"POST", "PUT", "PATCH", "DELETE"}
        op_type: Literal["read", "write"] = "write" if is_write else "read"
        description = (
            f"Execute {cluster.method} request on {cluster.path_template} "
            f"({'mutates state' if is_write else 'read-only'})."
        )

        parameters: list[ToolParameter] = []

        # Path params
        for p in cluster.path_params:
            parameters.append(
                ToolParameter(
                    name=p,
                    type="string",
                    description=f"Path identifier for {p}",
                    required=True,
                    location="path",
                )
            )

        # Query params
        for q in cluster.observed_query_params.variable:
            is_req = q not in cluster.observed_query_params.optional
            parameters.append(
                ToolParameter(
                    name=q,
                    type="string",
                    description=f"Query parameter {q}",
                    required=is_req,
                    location="query",
                )
            )

        # Request body fields if available
        sch = schema_map.get(endpoint_id)
        if (
            sch
            and sch.request_body_schema
            and sch.request_body_schema.get("type") == "object"
        ):
            props = sch.request_body_schema.get("properties", {})
            req_fields = set(sch.request_body_schema.get("required", []))
            for field_name, field_def in props.items():
                if len(parameters) < 10:  # Keep parameter list concise
                    field_type = field_def.get("type", "string")
                    if isinstance(field_type, list):
                        field_type = field_type[0]
                    parameters.append(
                        ToolParameter(
                            name=field_name,
                            type=str(field_type),
                            description=f"Request payload field {field_name}",
                            required=field_name in req_fields,
                            location="body",
                        )
                    )

        mapping = ToolEndpointMapping(
            endpoint_id=endpoint_id,
            method=cluster.method,
            path_template=cluster.path_template,
        )

        tools.append(
            ToolSpec(
                name=tool_name,
                description=description,
                operation_type=op_type,
                is_write=is_write,
                parameters=parameters,
                endpoints=[mapping],
                response_description="Parsed JSON API response",
            )
        )

    return DesignSpec(
        server_name=server_name,
        description=f"Generated MCP server for {server_name}",
        tools=tools,
    )


class ToolDesigner:
    """Orchestrates LLM tool synthesis with schema validation and repair loop."""

    def __init__(
        self,
        llm_client: BaseLLMClient | None = None,
        max_retries: int = 2,
    ) -> None:
        """Initialize with LLM client and retry threshold."""
        self.llm_client = llm_client
        self.max_retries = max_retries

    def design(
        self,
        clusters: Sequence[EndpointCluster],
        schemas: Sequence[EndpointSchema],
        server_name: str = "generated_api_server",
    ) -> DesignSpec:
        """Synthesize MCP ToolSpecs from clusters with validation & retry loop.

        Args:
            clusters: Normalized endpoint clusters.
            schemas: Inferred endpoint schemas.
            server_name: Target server identifier name.

        Returns:
            Validated DesignSpec model.

        Raises:
            ValueError: If LLM output fails schema validation after retries.
        """
        if not clusters:
            return DesignSpec(
                server_name=server_name,
                description="Empty server without endpoints",
                tools=[],
            )

        if not self.llm_client:
            logger.info(
                "No LLM client supplied; falling back to deterministic tool design."
            )
            return deterministic_design_tools(
                clusters, schemas, server_name=server_name
            )

        user_content = format_endpoints_context(clusters, schemas)
        json_schema = DesignSpec.model_json_schema()

        messages: list[LLMMessage] = [
            LLMMessage(role=LLMRole.USER, content=user_content),
        ]

        last_error_msg = ""
        for attempt in range(self.max_retries + 1):
            try:
                response = self.llm_client.complete(
                    system=SYSTEM_DESIGN_PROMPT,
                    messages=messages,
                    json_schema=json_schema,
                )
                raw_data = json.loads(response.content)
                spec = DesignSpec.model_validate(raw_data)
                logger.info(
                    "Tool design success (attempt %d): %d tools (%d read, %d write)",
                    attempt + 1,
                    len(spec.tools),
                    len(spec.read_tools),
                    len(spec.write_tools),
                )
                return spec

            except (json.JSONDecodeError, ValidationError, ValueError) as err:
                last_error_msg = str(err)
                logger.warning(
                    "DesignSpec validation failed on attempt %d/%d: %s",
                    attempt + 1,
                    self.max_retries + 1,
                    err,
                )

                if attempt < self.max_retries:
                    # Append assistant reply + corrective user prompt for repair loop
                    messages.append(
                        LLMMessage(
                            role=LLMRole.ASSISTANT,
                            content=response.content
                            if "response" in locals()
                            else "{}",
                        )
                    )
                    reprompt = (
                        f"Your output failed validation:\n{err}\n\n"
                        "Please fix the errors and return strictly valid JSON "
                        "conforming to DesignSpec schema "
                        "(valid snake_case names, operation_type)."
                    )
                    messages.append(LLMMessage(role=LLMRole.USER, content=reprompt))

        raise ValueError(
            f"Failed to synthesize valid ToolSpec after {self.max_retries} retries. "
            f"Last validation error: {last_error_msg}"
        )


def design_tools(
    clusters: Sequence[EndpointCluster],
    schemas: Sequence[EndpointSchema],
    llm_client: BaseLLMClient | None = None,
    server_name: str = "generated_api_server",
) -> DesignSpec:
    """Convenience helper to execute tool design."""
    designer = ToolDesigner(llm_client=llm_client)
    return designer.design(clusters, schemas, server_name=server_name)
