"""Data models for server verification, structural comparisons, and repair logs."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ToolVerificationResult(BaseModel):
    """Result of verifying an MCP tool execution against replay data."""

    model_config = ConfigDict(frozen=True)

    tool_name: str = Field(..., description="Name of verified MCP tool.")
    passed: bool = Field(
        ..., description="Whether tool output matched expected schema."
    )
    error_message: str | None = Field(
        default=None, description="Diagnostic error text if failed."
    )
    expected_type: str | None = Field(
        default=None, description="Expected root data type, e.g. 'dict', 'list'."
    )
    actual_type: str | None = Field(
        default=None, description="Actual received data type."
    )
    missing_keys: list[str] = Field(
        default_factory=list,
        description="Keys present in recorded response but absent from tool output.",
    )
    extra_info: dict[str, Any] = Field(
        default_factory=dict, description="Additional context or diagnostics."
    )


class VerificationReport(BaseModel):
    """Aggregated verification results and scoring across all exposed tools."""

    model_config = ConfigDict(frozen=True)

    results: list[ToolVerificationResult] = Field(
        default_factory=list, description="Per-tool verification results."
    )
    total_tools: int = Field(default=0, description="Total tools evaluated.")
    passed_tools: int = Field(default=0, description="Number of tools passing checks.")
    failed_tools: int = Field(default=0, description="Number of tools failing checks.")
    score: float = Field(
        default=0.0, description="Overall compliance score between 0.0 and 1.0."
    )
    all_passed: bool = Field(
        default=True, description="Whether 100% of verified tools passed."
    )


class RepairIteration(BaseModel):
    """Log record representing a single repair iteration for a failing tool."""

    model_config = ConfigDict(frozen=True)

    iteration: int = Field(..., description="1-based iteration counter (max 3).")
    tool_name: str = Field(..., description="Tool undergoing repair.")
    failing_error: str = Field(..., description="Error message triggering repair.")
    patch_applied: bool = Field(
        default=False, description="Whether LLM suggested a patch."
    )
    passed_after_patch: bool = Field(
        default=False, description="Whether re-verification passed after patch."
    )
    patch_summary: str = Field(
        default="", description="Summary of applied code changes."
    )


class RepairReport(BaseModel):
    """Comprehensive log of the repair loop operations and final status."""

    model_config = ConfigDict(frozen=True)

    initial_report: VerificationReport = Field(
        ..., description="Verification report prior to repair attempts."
    )
    final_report: VerificationReport = Field(
        ..., description="Verification report after repair cycle completion."
    )
    iterations: list[RepairIteration] = Field(
        default_factory=list, description="Ordered history of repair attempts."
    )
    repaired: bool = Field(
        default=False, description="Whether all tools passed after repairs."
    )
