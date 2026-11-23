"""Data models for MCP tool specifications, parameters, and design schemas."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

SNAKE_CASE_REGEX: re.Pattern[str] = re.compile(r"^[a-z][a-z0-9_]*$")


class ToolParameter(BaseModel):
    """Specification of an individual parameter accepted by an MCP tool."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(..., description="Parameter name in snake_case.")
    type: str = Field(
        default="string", description="Type: string, integer, boolean, etc."
    )
    description: str = Field(..., description="Clear explanation for AI assistant.")
    required: bool = Field(default=True, description="Whether parameter is required.")
    default: Any | None = Field(default=None, description="Default value if optional.")
    location: Literal["path", "query", "body"] = Field(
        default="query",
        description="Location mapped in the underlying HTTP request.",
    )
    enum: list[str] | None = Field(default=None, description="Permitted enum values.")

    @field_validator("name")
    @classmethod
    def validate_name_snake_case(cls, v: str) -> str:
        """Ensure parameter name adheres to snake_case."""
        cleaned = v.strip()
        if not SNAKE_CASE_REGEX.match(cleaned):
            raise ValueError(
                f"Parameter name '{v}' must be snake_case (e.g. 'user_id')."
            )
        return cleaned


class ToolEndpointMapping(BaseModel):
    """Mapping between an MCP tool and an underlying API endpoint."""

    model_config = ConfigDict(frozen=True)

    endpoint_id: str = Field(..., description="ID of mapped endpoint cluster.")
    method: str = Field(..., description="HTTP method.")
    path_template: str = Field(..., description="Path template of endpoint.")
    parameter_mappings: dict[str, str] = Field(
        default_factory=dict,
        description="Mapping from tool parameter name to API parameter or body field.",
    )


class ToolSpec(BaseModel):
    """Specification of an MCP tool exposed to AI agents."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(..., description="Tool name in snake_case.")
    description: str = Field(
        ..., description="Clear explanation of tool utility for agents."
    )
    operation_type: Literal["read", "write"] = Field(
        default="read",
        description="Tool operation type: 'read' (GET) or 'write' (mutating).",
    )
    is_write: bool = Field(
        default=False,
        description="Whether this tool mutates state.",
    )
    parameters: list[ToolParameter] = Field(
        default_factory=list,
        description="List of arguments accepted by this tool.",
    )
    endpoints: list[ToolEndpointMapping] = Field(
        default_factory=list,
        description="Underlying API endpoint(s) invoked by this tool.",
    )
    response_description: str = Field(
        default="",
        description="Description of data returned by this tool.",
    )

    @field_validator("name")
    @classmethod
    def validate_tool_name(cls, v: str) -> str:
        """Ensure tool name is clean snake_case."""
        cleaned = v.strip()
        if not SNAKE_CASE_REGEX.match(cleaned):
            raise ValueError(
                f"Tool name '{v}' must be valid snake_case (e.g. 'get_user_profile')."
            )
        return cleaned

    def model_post_init(self, __context: Any) -> None:
        """Synchronize is_write flag with operation_type."""
        if self.operation_type == "write" and not self.is_write:
            object.__setattr__(self, "is_write", True)
        elif self.operation_type == "read" and self.is_write:
            object.__setattr__(self, "is_write", False)


class DesignSpec(BaseModel):
    """Consolidated server design containing generated tool specifications."""

    model_config = ConfigDict(frozen=True)

    server_name: str = Field(..., description="Identifier name of the MCP server.")
    description: str = Field(
        ..., description="High-level description of server capabilities."
    )
    tools: list[ToolSpec] = Field(
        default_factory=list,
        description="List of proposed MCP tool specifications.",
    )

    @property
    def read_tools(self) -> list[ToolSpec]:
        """Filter read-only tools."""
        return [t for t in self.tools if not t.is_write]

    @property
    def write_tools(self) -> list[ToolSpec]:
        """Filter mutating tools."""
        return [t for t in self.tools if t.is_write]
