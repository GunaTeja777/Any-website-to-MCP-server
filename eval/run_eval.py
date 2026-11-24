"""Evaluation benchmark runner executing HAR2MCP across ground-truth datasets."""
# ruff: noqa: E402

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import tempfile
import time
from pathlib import Path

# Ensure repository root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from eval.ground_truth import load_ground_truth
from eval.metrics import (
    calculate_endpoint_metrics,
    calculate_schema_field_metrics,
    compute_evaluation_summary,
    extract_fields_from_schema,
    normalize_signature,
)
from eval.models import EvaluationCaseResult, EvaluationSummary
from eval.reporter import format_markdown_table, write_reports
from har2mcp.auth.detector import detect_auth_spec
from har2mcp.cluster.engine import cluster_entries
from har2mcp.codegen.generator import generate_server_project
from har2mcp.design.engine import deterministic_design_tools
from har2mcp.llm.client import NoOpLLMClient, OpenAILLMClient
from har2mcp.parser.loader import parse_har
from har2mcp.redact.engine import redact
from har2mcp.schema.inferrer import infer_schemas
from har2mcp.verify.repair import RepairLoop
from har2mcp.verify.verifier import ServerVerifier

logger = logging.getLogger("eval")


def is_placeholder_case(case_dir: Path) -> bool:
    """Check if case directory contains unpopulated placeholder files."""
    gt_file = case_dir / "ground_truth.json"
    if gt_file.exists():
        text = gt_file.read_text(encoding="utf-8", errors="ignore")
        if "TODO_EXPECTED_AUTH" in text or "PLACEHOLDER" in text:
            return True

    har_file = case_dir / "recording.har"
    if har_file.exists():
        text = har_file.read_text(encoding="utf-8", errors="ignore")
        if "PLACEHOLDER" in text:
            return True

    return False


async def evaluate_single_case(
    case_dir: Path,
    allow_writes: bool = False,
    run_repair: bool = True,
) -> EvaluationCaseResult:
    """Run full HAR2MCP pipeline on a single evaluation dataset case."""
    case_name = case_dir.name
    start_time = time.perf_counter()

    # Find HAR file in directory
    har_files = list(case_dir.glob("*.har"))
    if not har_files:
        return EvaluationCaseResult(
            case_name=case_name,
            error="No .har file found in case directory",
        )

    har_path = har_files[0]

    # Load Ground Truth
    ground_truth = load_ground_truth(case_dir)
    if not ground_truth:
        return EvaluationCaseResult(
            case_name=case_name,
            error="No valid ground_truth.json or openapi.json found",
        )

    total_cost = 0.0

    try:
        # 1. Parse HAR
        entries = parse_har(har_path)
        if not entries:
            return EvaluationCaseResult(
                case_name=case_name,
                error="No API traffic parsed from HAR",
            )

        # 2. Redact
        redact_res = redact(entries)

        # 3. Cluster
        clusters = cluster_entries(redact_res.entries)

        # 4. Infer schemas
        schemas = infer_schemas(clusters)
        has_pagination = any(s.pagination for s in schemas)

        # 5. Detect Auth
        auth_spec = detect_auth_spec(redact_res.entries)
        detected_auth = auth_spec.primary_type.value
        expected_auth = ground_truth.expected_auth
        auth_correct = (
            expected_auth is not None and detected_auth.lower() == expected_auth.lower()
        ) or (expected_auth is None)

        # 6. Tool Design
        design_spec = deterministic_design_tools(clusters, schemas)

        # Compute endpoint recall and precision
        predicted_endpoints = [
            (ep.method, ep.path_template)
            for tool in design_spec.tools
            for ep in tool.endpoints
        ]
        ep_precision, ep_recall, ep_f1 = calculate_endpoint_metrics(
            predicted_endpoints, ground_truth.endpoints
        )

        # Compute schema field accuracy (field-level F1)
        pred_fields_by_endpoint: dict[str, set[str]] = {}
        for s in schemas:
            sig = normalize_signature(s.method, s.path_template)
            fields = extract_fields_from_schema(s)
            pred_fields_by_endpoint[sig] = fields

        field_p, field_r, field_f1 = calculate_schema_field_metrics(
            pred_fields_by_endpoint, ground_truth.endpoints
        )

        # 7. Codegen & Verification
        with tempfile.TemporaryDirectory() as tmpdir:
            generate_server_project(
                tool_specs=design_spec.tools,
                auth_spec=auth_spec,
                out_dir=tmpdir,
                server_name=case_name,
                allow_writes=allow_writes,
                has_pagination=has_pagination,
            )

            verifier = ServerVerifier(
                server_dir=tmpdir,
                entries=redact_res.entries,
                tool_specs=design_spec.tools,
            )
            report = await verifier.verify()
            passing_first = report.passed_tools
            passing_after_repair = passing_first

            # 8. Repair loop if failures and repair enabled
            if not report.all_passed and run_repair:
                openai_key = os.environ.get("OPENAI_API_KEY")
                llm = (
                    OpenAILLMClient(api_key=openai_key)
                    if openai_key
                    else NoOpLLMClient()
                )
                loop = RepairLoop(
                    server_dir=tmpdir,
                    entries=redact_res.entries,
                    tool_specs=design_spec.tools,
                    llm_client=llm,
                    redaction_map={s.placeholder: s for s in redact_res.secrets},
                    max_iterations=3,
                )
                repair_rep = await loop.run()
                passing_after_repair = repair_rep.final_report.passed_tools
                total_cost += getattr(llm, "estimated_cost_usd", 0.0)

        elapsed = time.perf_counter() - start_time

        return EvaluationCaseResult(
            case_name=case_name,
            endpoint_precision=ep_precision,
            endpoint_recall=ep_recall,
            endpoint_f1=ep_f1,
            schema_field_precision=field_p,
            schema_field_recall=field_r,
            schema_field_f1=field_f1,
            auth_detected_correctly=auth_correct,
            detected_auth_type=detected_auth,
            expected_auth_type=expected_auth,
            tools_passing_first_try=passing_first,
            tools_passing_after_repair=passing_after_repair,
            total_tools=report.total_tools,
            total_llm_cost=round(total_cost, 4),
            total_time_seconds=round(elapsed, 2),
        )

    except Exception as exc:
        logger.exception("Error evaluating case %s: %s", case_name, exc)
        return EvaluationCaseResult(
            case_name=case_name,
            error=str(exc),
            total_time_seconds=round(time.perf_counter() - start_time, 2),
        )


async def run_benchmark(
    cases_dir: Path,
    output_dir: Path,
    allow_writes: bool = False,
) -> EvaluationSummary:
    """Discover all test cases, execute evaluation, and generate reports."""
    case_dirs = sorted([d for d in cases_dir.iterdir() if d.is_dir()])
    print(f"Discovered {len(case_dirs)} test case directories in {cases_dir}")

    results: list[EvaluationCaseResult] = []
    for cd in case_dirs:
        # Check if case is an unpopulated placeholder
        if is_placeholder_case(cd):
            print(f"[-] Skipping placeholder: {cd.name} (awaiting ground truth)")
            continue

        print(f"[+] Running evaluation for: {cd.name}...")
        res = await evaluate_single_case(cd, allow_writes=allow_writes)
        results.append(res)
        status = "OK" if not res.error else f"FAIL ({res.error})"
        ep_info = f"Endpoint F1: {res.endpoint_f1 * 100:.1f}%"
        field_info = f"Field F1: {res.schema_field_f1 * 100:.1f}%"
        auth_info = f"Auth: {res.detected_auth_type}"
        print(f"    -> {status} | {ep_info} | {field_info} | {auth_info}")

    summary = compute_evaluation_summary(results)
    md_file, json_file = write_reports(summary, output_dir)
    print("\nBenchmark complete!")
    print(f"Reports saved to:\n  - {md_file}\n  - {json_file}\n")
    print(format_markdown_table(summary))
    return summary


def main() -> None:
    """CLI entrypoint for evaluation script."""
    parser = argparse.ArgumentParser(description="HAR2MCP Benchmark Evaluation Suite")
    parser.add_argument(
        "--cases-dir",
        type=Path,
        default=Path(__file__).parent / "cases",
        help="Path to evaluation test cases folder.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).parent,
        help="Directory where results.md and results.json will be saved.",
    )
    parser.add_argument(
        "--allow-writes",
        action="store_true",
        help="Allow mutation tools during evaluation.",
    )

    args = parser.parse_args()
    asyncio.run(
        run_benchmark(
            cases_dir=args.cases_dir,
            output_dir=args.output_dir,
            allow_writes=args.allow_writes,
        )
    )


if __name__ == "__main__":
    main()
