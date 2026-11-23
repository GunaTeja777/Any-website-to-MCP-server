"""Pydantic data models for API endpoint clustering and parameter analysis."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from har2mcp.parser.models import Entry, Request, Response


class ObservedQueryParams(BaseModel):
    """Categorization of observed query parameters across endpoint requests."""

    model_config = ConfigDict(frozen=True)

    stable: dict[str, str] = Field(
        default_factory=dict,
        description="Query parameters with constant values across all samples.",
    )
    variable: list[str] = Field(
        default_factory=list,
        description="Query parameters observed with varying or dynamic values.",
    )
    optional: list[str] = Field(
        default_factory=list,
        description="Query parameters that appear only in a subset of requests.",
    )


class EndpointCluster(BaseModel):
    """Represents a grouped API endpoint with normalized path and observed traffic."""

    model_config = ConfigDict(frozen=True)

    method: str = Field(..., description="HTTP method (GET, POST, etc.).")
    path_template: str = Field(
        ...,
        description="Normalized path template with {param} placeholders.",
    )
    sample_requests: list[Request] = Field(
        default_factory=list,
        description="Sample request instances observed for this endpoint.",
    )
    sample_responses: list[Response] = Field(
        default_factory=list,
        description="Sample response instances observed for this endpoint.",
    )
    observed_query_params: ObservedQueryParams = Field(
        default_factory=ObservedQueryParams,
        description="Analyzed stable and variable query parameters.",
    )
    call_count: int = Field(
        default=0,
        description="Total number of observed calls matching this endpoint.",
    )
    path_params: list[str] = Field(
        default_factory=list,
        description="Names of path parameters extracted from path_template.",
    )
    is_graphql: bool = Field(
        default=False,
        description="Whether this cluster represents a GraphQL operation.",
    )
    operation_name: str | None = Field(
        default=None,
        description="GraphQL operation name when applicable.",
    )
    hosts: set[str] = Field(
        default_factory=set,
        description="Target hostnames observed for this endpoint.",
    )
    entries: list[Entry] = Field(
        default_factory=list,
        description="All recorded Entry exchanges mapped to this cluster.",
    )
