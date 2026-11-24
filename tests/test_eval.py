"""Unit and integration tests for evaluation metrics and benchmark runner."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
from eval.ground_truth import load_ground_truth, parse_openapi_spec
from eval.metrics import (
    calculate_endpoint_metrics,
    calculate_schema_field_metrics,
    compute_evaluation_summary,
    normalize_signature,
)
from eval.models import EvaluationCaseResult, ExpectedEndpoint
from eval.reporter import format_markdown_table, write_reports
from eval.run_eval import evaluate_single_case

EVAL_DIR = Path(__file__).parent.parent / "eval"
CASES_DIR = EVAL_DIR / "cases"
CASE_01_DIR = CASES_DIR / "case_01_synthetic_cloud"


def test_normalize_signature() -> None:
    """Test signature normalization matching template placeholders."""
    sig1 = normalize_signature("GET", "/v1/users/{user_id}")
    sig2 = normalize_signature("get", "/v1/users/{id}")
    sig3 = normalize_signature("GET", "/v1/users/:uid")
    assert sig1 == sig2 == sig3 == "GET /v1/users/{_}"


def test_calculate_endpoint_metrics_perfect_match() -> None:
    """Test precision, recall, and F1 calculation for matching endpoints."""
    expected = [
        ExpectedEndpoint(method="GET", path="/items"),
        ExpectedEndpoint(method="POST", path="/items"),
    ]
    predicted = [("GET", "/items"), ("POST", "/items")]
    p, r, f1 = calculate_endpoint_metrics(predicted, expected)
    assert p == 1.0
    assert r == 1.0
    assert f1 == 1.0


def test_calculate_endpoint_metrics_partial_match() -> None:
    """Test precision and recall when extra or missing endpoints occur."""
    expected = [
        ExpectedEndpoint(method="GET", path="/items"),
        ExpectedEndpoint(method="GET", path="/users/{id}"),
    ]
    # Predicted /items and extra /unknown, missing /users/{id}
    predicted = [("GET", "/items"), ("GET", "/unknown")]
    p, r, f1 = calculate_endpoint_metrics(predicted, expected)
    assert p == 0.5  # 1 TP out of 2 predicted
    assert r == 0.5  # 1 TP out of 2 expected
    assert f1 == 0.5


def test_calculate_schema_field_metrics() -> None:
    """Test field-level schema accuracy precision, recall, and F1."""
    expected = [
        ExpectedEndpoint(
            method="GET",
            path="/users",
            fields=["id", "name", "email"],
        )
    ]
    # Predicted 2 matching fields ("id", "name") + 1 extra ("age"), missing "email"
    predicted_fields = {
        "GET /users": {"id", "name", "age"},
    }
    p, r, f1 = calculate_schema_field_metrics(predicted_fields, expected)
    assert p == round(2 / 3, 4)  # 2 TP out of 3 predicted
    assert r == round(2 / 3, 4)  # 2 TP out of 3 expected
    assert f1 == round(2 / 3, 4)


def test_parse_openapi_spec() -> None:
    """Test parsing an OpenAPI 3.0 document dictionary into GroundTruth model."""
    openapi_dict = {
        "openapi": "3.0.0",
        "info": {"title": "Test API", "version": "1.0", "description": "Test doc"},
        "paths": {
            "/api/v1/orders": {
                "get": {
                    "parameters": [{"name": "status", "in": "query"}],
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "properties": {
                                            "orders": {"type": "array"},
                                            "total": {"type": "integer"},
                                        }
                                    }
                                }
                            }
                        }
                    },
                }
            }
        },
        "components": {
            "securitySchemes": {"BearerAuth": {"type": "http", "scheme": "bearer"}}
        },
    }
    gt = parse_openapi_spec(openapi_dict, name="test_openapi")
    assert gt.name == "test_openapi"
    assert gt.expected_auth == "bearer_token"
    assert len(gt.endpoints) == 1
    ep = gt.endpoints[0]
    assert ep.method == "GET"
    assert ep.path == "/api/v1/orders"
    assert "status" in ep.fields
    assert "orders" in ep.fields
    assert "total" in ep.fields


def test_load_ground_truth_case_01() -> None:
    """Test loading ground truth from case_01_synthetic_cloud directory."""
    gt = load_ground_truth(CASE_01_DIR)
    assert gt is not None
    assert gt.name == "case_01_synthetic_cloud"
    assert gt.expected_auth == "bearer_token"
    assert len(gt.endpoints) == 3


def test_reporter_formatting_and_writing() -> None:
    """Test formatting markdown table and writing JSON/MD reports."""
    case_res = EvaluationCaseResult(
        case_name="case_sample",
        endpoint_precision=1.0,
        endpoint_recall=1.0,
        endpoint_f1=1.0,
        schema_field_precision=0.9,
        schema_field_recall=0.9,
        schema_field_f1=0.9,
        auth_detected_correctly=True,
        detected_auth_type="bearer_token",
        tools_passing_first_try=3,
        tools_passing_after_repair=3,
        total_tools=3,
        total_llm_cost=0.0123,
        total_time_seconds=1.5,
    )
    summary = compute_evaluation_summary([case_res])
    assert summary.total_cases == 1
    assert summary.completed_cases == 1
    assert summary.mean_endpoint_f1 == 1.0
    assert summary.auth_accuracy_pct == 100.0

    table_md = format_markdown_table(summary)
    assert "HAR2MCP Benchmark Evaluation Results" in table_md
    assert "case_sample" in table_md

    with tempfile.TemporaryDirectory() as tmpdir:
        md_file, json_file = write_reports(summary, tmpdir)
        assert md_file.exists()
        assert json_file.exists()
        data = json.loads(json_file.read_text(encoding="utf-8"))
        assert data["completed_cases"] == 1


@pytest.mark.anyio
async def test_evaluate_single_case_synthetic() -> None:
    """Run full evaluation on case_01_synthetic_cloud."""
    res = await evaluate_single_case(CASE_01_DIR, allow_writes=False)
    assert res.error is None
    assert res.endpoint_precision > 0.0
    assert res.endpoint_recall > 0.0
    assert res.auth_detected_correctly is True
    assert res.tools_passing_first_try > 0
