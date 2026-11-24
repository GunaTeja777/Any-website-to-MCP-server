"""Data models for HAR2MCP evaluation benchmarks and ground-truth comparison."""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ExpectedEndpoint(BaseModel):
    """Specification of an expected API endpoint from ground truth."""

    model_config = ConfigDict(frozen=True)

    method: str = Field(..., description="HTTP method, e.g. 'GET', 'POST'.")
    path: str = Field(..., description="Path template, e.g. '/api/v1/users/{id}'.")
    fields: list[str] = Field(
        default_factory=list,
        description="Expected request/response schema field names.",
    )

    @property
    def normalized_signature(self) -> str:
        """Normalized representation (e.g. 'GET /api/v1/users/{_}')."""
        normalized_path = re.sub(r"\{[^}]+\}", "{_}", self.path.rstrip("/"))
        normalized_path = re.sub(r":([a-zA-Z0-9_]+)", "{_}", normalized_path)
        return f"{self.method.upper()} {normalized_path or '/'}"


class GroundTruth(BaseModel):
    """Ground-truth specification for a test case."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(..., description="Test case identifier.")
    expected_auth: str | None = Field(
        default=None,
        description="Expected auth mechanism (e.g. 'bearer_token').",
    )
    endpoints: list[ExpectedEndpoint] = Field(
        default_factory=list,
        description="List of expected API endpoints.",
    )
    description: str = Field(
        default="", description="Optional human-readable notes about the case."
    )


class EvaluationCaseResult(BaseModel):
    """Evaluation metrics for an individual test case."""

    model_config = ConfigDict(frozen=True)

    case_name: str = Field(..., description="Case identifier.")
    endpoint_precision: float = Field(default=0.0)
    endpoint_recall: float = Field(default=0.0)
    endpoint_f1: float = Field(default=0.0)

    schema_field_precision: float = Field(default=0.0)
    schema_field_recall: float = Field(default=0.0)
    schema_field_f1: float = Field(default=0.0)

    auth_detected_correctly: bool = Field(default=False)
    detected_auth_type: str = Field(default="none")
    expected_auth_type: str | None = Field(default=None)

    tools_passing_first_try: int = Field(default=0)
    tools_passing_after_repair: int = Field(default=0)
    total_tools: int = Field(default=0)

    total_llm_cost: float = Field(default=0.0)
    total_time_seconds: float = Field(default=0.0)

    error: str | None = Field(
        default=None, description="Error message if pipeline execution failed."
    )
    details: dict[str, Any] = Field(
        default_factory=dict, description="Diagnostic details."
    )


class EvaluationSummary(BaseModel):
    """Aggregated evaluation metrics across all evaluated cases."""

    model_config = ConfigDict(frozen=True)

    total_cases: int = Field(default=0)
    completed_cases: int = Field(default=0)
    failed_cases: int = Field(default=0)

    mean_endpoint_precision: float = Field(default=0.0)
    mean_endpoint_recall: float = Field(default=0.0)
    mean_endpoint_f1: float = Field(default=0.0)

    mean_schema_field_f1: float = Field(default=0.0)
    auth_accuracy_pct: float = Field(default=0.0)

    total_tools_passing_first_try: int = Field(default=0)
    total_tools_passing_after_repair: int = Field(default=0)
    total_tools_evaluated: int = Field(default=0)

    total_llm_cost: float = Field(default=0.0)
    total_time_seconds: float = Field(default=0.0)

    case_results: list[EvaluationCaseResult] = Field(default_factory=list)
