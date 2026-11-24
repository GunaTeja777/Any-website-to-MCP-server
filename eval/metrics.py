"""Mathematical computation of precision, recall, F1, and schema accuracy metrics."""

from __future__ import annotations

import re
from typing import Any

from eval.models import EvaluationCaseResult, EvaluationSummary, ExpectedEndpoint


def normalize_signature(method: str, path: str) -> str:
    """Normalize method and path template for comparison."""
    norm_path = re.sub(r"\{[^}]+\}", "{_}", path.rstrip("/"))
    norm_path = re.sub(r":([a-zA-Z0-9_]+)", "{_}", norm_path)
    return f"{method.upper()} {norm_path or '/'}"


def calculate_endpoint_metrics(
    predicted_endpoints: list[tuple[str, str]],
    expected_endpoints: list[ExpectedEndpoint],
) -> tuple[float, float, float]:
    """Compute precision, recall, and F1 score for detected API endpoints."""
    if not expected_endpoints and not predicted_endpoints:
        return 1.0, 1.0, 1.0

    pred_sigs = {
        normalize_signature(method, path) for method, path in predicted_endpoints
    }
    exp_sigs = {ep.normalized_signature for ep in expected_endpoints}

    tp = len(pred_sigs & exp_sigs)
    fp = len(pred_sigs - exp_sigs)
    fn = len(exp_sigs - pred_sigs)

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = (
        (2 * precision * recall / (precision + recall))
        if (precision + recall) > 0
        else 0.0
    )

    return round(precision, 4), round(recall, 4), round(f1, 4)


def calculate_schema_field_metrics(
    predicted_fields_by_endpoint: dict[str, set[str]],
    expected_endpoints: list[ExpectedEndpoint],
) -> tuple[float, float, float]:
    """Compute micro-averaged field precision, recall, and F1 across endpoints."""
    total_tp = 0
    total_fp = 0
    total_fn = 0

    expected_lookup = {
        ep.normalized_signature: set(ep.fields) for ep in expected_endpoints
    }

    for sig, exp_fields in expected_lookup.items():
        pred_fields = predicted_fields_by_endpoint.get(sig, set())

        tp = len(pred_fields & exp_fields)
        fp = len(pred_fields - exp_fields)
        fn = len(exp_fields - pred_fields)

        total_tp += tp
        total_fp += fp
        total_fn += fn

    if total_tp == 0 and total_fp == 0 and total_fn == 0:
        return 1.0, 1.0, 1.0

    precision = total_tp / (total_tp + total_fp) if (total_tp + total_fp) > 0 else 0.0
    recall = total_tp / (total_tp + total_fn) if (total_tp + total_fn) > 0 else 0.0
    f1 = (
        (2 * precision * recall / (precision + recall))
        if (precision + recall) > 0
        else 0.0
    )

    return round(precision, 4), round(recall, 4), round(f1, 4)


def extract_fields_from_schema(schema: Any) -> set[str]:
    """Extract property field names from an EndpointSchema model or JSON dict."""
    fields: set[str] = set()
    if not schema:
        return fields

    # If EndpointSchema model
    if hasattr(schema, "request_body_schema") and isinstance(
        schema.request_body_schema, dict
    ):
        _extract_keys_recursive(schema.request_body_schema, fields)
    if hasattr(schema, "response_body_schema") and isinstance(
        schema.response_body_schema, dict
    ):
        _extract_keys_recursive(schema.response_body_schema, fields)
    if hasattr(schema, "query_params_schema") and isinstance(
        schema.query_params_schema, dict
    ):
        _extract_keys_recursive(schema.query_params_schema, fields)

    # If raw dictionary
    if isinstance(schema, dict):
        _extract_keys_recursive(schema, fields)

    return fields


def _extract_keys_recursive(
    data: dict[str, Any], fields: set[str], seen: set[int] | None = None
) -> None:
    """Helper to traverse JSON schema structures."""
    if seen is None:
        seen = set()
    data_id = id(data)
    if data_id in seen:
        return
    seen.add(data_id)

    properties = data.get("properties")
    if isinstance(properties, dict):
        for k, v in properties.items():
            fields.add(k)
            if isinstance(v, dict):
                _extract_keys_recursive(v, fields, seen)

    items = data.get("items")
    if isinstance(items, dict):
        _extract_keys_recursive(items, fields, seen)


def compute_evaluation_summary(cases: list[EvaluationCaseResult]) -> EvaluationSummary:
    """Aggregate metrics across all processed evaluation cases."""
    total = len(cases)
    if total == 0:
        return EvaluationSummary()

    completed = [c for c in cases if not c.error]
    failed = total - len(completed)

    n_comp = len(completed)
    if n_comp == 0:
        return EvaluationSummary(
            total_cases=total,
            completed_cases=0,
            failed_cases=failed,
            case_results=cases,
        )

    mean_p = sum(c.endpoint_precision for c in completed) / n_comp
    mean_r = sum(c.endpoint_recall for c in completed) / n_comp
    mean_f1 = sum(c.endpoint_f1 for c in completed) / n_comp

    mean_schema_f1 = sum(c.schema_field_f1 for c in completed) / n_comp
    auth_acc = (sum(1 for c in completed if c.auth_detected_correctly) / n_comp) * 100.0

    tools_first = sum(c.tools_passing_first_try for c in completed)
    tools_repaired = sum(c.tools_passing_after_repair for c in completed)
    total_tools = sum(c.total_tools for c in completed)

    total_cost = sum(c.total_llm_cost for c in completed)
    total_time = sum(c.total_time_seconds for c in completed)

    return EvaluationSummary(
        total_cases=total,
        completed_cases=n_comp,
        failed_cases=failed,
        mean_endpoint_precision=round(mean_p, 4),
        mean_endpoint_recall=round(mean_r, 4),
        mean_endpoint_f1=round(mean_f1, 4),
        mean_schema_field_f1=round(mean_schema_f1, 4),
        auth_accuracy_pct=round(auth_acc, 2),
        total_tools_passing_first_try=tools_first,
        total_tools_passing_after_repair=tools_repaired,
        total_tools_evaluated=total_tools,
        total_llm_cost=round(total_cost, 4),
        total_time_seconds=round(total_time, 2),
        case_results=cases,
    )
