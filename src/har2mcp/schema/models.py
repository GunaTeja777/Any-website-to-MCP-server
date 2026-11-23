"""Data models for inferred JSON Schemas, parameter specifications, and pagination."""

from __future__ import annotations

import json
import re
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class PaginationType(StrEnum):
    """Recognized pagination mechanisms."""

    PAGE_LIMIT = "page_limit"
    OFFSET_LIMIT = "offset_limit"
    CURSOR = "cursor"
    LINK_HEADER = "link_header"
    NONE = "none"


class PaginationInfo(BaseModel):
    """Pagination metadata observed on an endpoint."""

    model_config = ConfigDict(frozen=True)

    type: PaginationType = Field(..., description="Pagination strategy type.")
    page_param: str | None = Field(default=None, description="Page number parameter.")
    limit_param: str | None = Field(
        default=None, description="Page size / limit parameter."
    )
    offset_param: str | None = Field(
        default=None, description="Offset / skip parameter."
    )
    cursor_param: str | None = Field(
        default=None, description="Request cursor parameter."
    )
    response_cursor_field: str | None = Field(
        default=None,
        description="Response body field holding next cursor.",
    )
    link_header_present: bool = Field(
        default=False,
        description="Whether RFC 5988 Link header rel=next was observed.",
    )
    description: str = Field(default="", description="Human-readable description.")


class EndpointSchema(BaseModel):
    """Complete inferred schema set for a single endpoint cluster."""

    model_config = ConfigDict(frozen=True)

    endpoint_id: str = Field(..., description="Normalized unique endpoint ID.")
    method: str = Field(..., description="HTTP method.")
    path_template: str = Field(..., description="Normalized path template.")
    sample_count: int = Field(default=1, description="Number of samples analyzed.")
    confidence_score: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Statistical confidence score in schema accuracy.",
    )
    path_params_schema: dict[str, Any] = Field(
        default_factory=dict,
        description="JSON Schema for path variables.",
    )
    query_params_schema: dict[str, Any] = Field(
        default_factory=dict,
        description="JSON Schema for query string parameters.",
    )
    request_body_schema: dict[str, Any] | None = Field(
        default=None,
        description="JSON Schema for HTTP request body payload.",
    )
    response_body_schema: dict[str, Any] | None = Field(
        default=None,
        description="JSON Schema for HTTP response payload.",
    )
    pagination: PaginationInfo | None = Field(
        default=None,
        description="Detected pagination characteristics.",
    )

    def to_json_debug(self, indent: int = 2) -> str:
        """Serialize schema to formatted JSON for debugging.

        Args:
            indent: Indentation level.

        Returns:
            Formatted JSON string.
        """
        return json.dumps(self.model_dump(mode="json"), indent=indent)

    def dump_json(self, out_dir: Path | str) -> Path:
        """Write schema JSON file into destination directory for debugging.

        Args:
            out_dir: Target output folder path.

        Returns:
            Path to the written file.
        """
        folder = Path(out_dir)
        folder.mkdir(parents=True, exist_ok=True)
        safe_name = re.sub(r"[^a-zA-Z0-9_-]", "_", self.endpoint_id).strip("_")
        out_file = folder / f"{safe_name}.json"
        out_file.write_text(self.to_json_debug(), encoding="utf-8")
        return out_file
