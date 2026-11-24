"""Formatters and output writers for evaluation benchmark results."""

from __future__ import annotations

from pathlib import Path

from eval.models import EvaluationSummary


def format_markdown_table(summary: EvaluationSummary) -> str:
    """Generate a clean GitHub Flavored Markdown evaluation report table."""
    lines: list[str] = [
        "# HAR2MCP Benchmark Evaluation Results",
        "",
        "## Overall Metrics",
        "",
        "| Metric | Value |",
        "|:-------|------:|",
        f"| **Evaluated Cases** | {summary.completed_cases} / {summary.total_cases} completed |",  # noqa: E501
        f"| **Mean Endpoint Precision** | {summary.mean_endpoint_precision * 100:.1f}% |",  # noqa: E501
        f"| **Mean Endpoint Recall** | {summary.mean_endpoint_recall * 100:.1f}% |",
        f"| **Mean Endpoint F1** | {summary.mean_endpoint_f1 * 100:.1f}% |",
        f"| **Mean Schema Field F1** | {summary.mean_schema_field_f1 * 100:.1f}% |",
        f"| **Auth Detection Accuracy** | {summary.auth_accuracy_pct:.1f}% |",
        f"| **Tools (First Try)** | {summary.total_tools_passing_first_try} / {summary.total_tools_evaluated} |",  # noqa: E501
        f"| **Tools (Repaired)** | {summary.total_tools_passing_after_repair} / {summary.total_tools_evaluated} |",  # noqa: E501
        f"| **Total LLM Cost** | ${summary.total_llm_cost:.4f} |",
        f"| **Execution Time** | {summary.total_time_seconds:.2f}s |",
        "",
        "## Case-by-Case Breakdown",
        "",
        "| Case | Endpoints (P/R/F1) | Field F1 | Auth | 1st | Repaired | Cost | Time |",  # noqa: E501
        "|:-----|:-------------------|:---------|:-----|:----|:---------|:-----|:-----|",
    ]

    for c in summary.case_results:
        if c.error:
            lines.append(
                f"| `{c.case_name}` | *Error* | *Error* | *Error* | - | - | ${c.total_llm_cost:.4f} | {c.total_time_seconds:.2f}s |"  # noqa: E501
            )
            continue

        p_r_f1 = (
            f"{c.endpoint_precision * 100:.0f}% / "
            f"{c.endpoint_recall * 100:.0f}% / "
            f"{c.endpoint_f1 * 100:.0f}%"
        )
        field_f1 = f"{c.schema_field_f1 * 100:.1f}%"
        auth_sym = "Yes" if c.auth_detected_correctly else "No"
        auth_str = f"{auth_sym} ({c.detected_auth_type})"

        t_first = f"{c.tools_passing_first_try}/{c.total_tools}"
        t_repair = f"{c.tools_passing_after_repair}/{c.total_tools}"
        cost_str = f"${c.total_llm_cost:.4f}"
        time_str = f"{c.total_time_seconds:.2f}s"

        lines.append(
            f"| `{c.case_name}` | {p_r_f1} | {field_f1} | {auth_str} | {t_first} | {t_repair} | {cost_str} | {time_str} |"  # noqa: E501
        )

    lines.append("")
    return "\n".join(lines)


def write_reports(
    summary: EvaluationSummary,
    output_dir: Path | str,
    md_filename: str = "results.md",
    json_filename: str = "results.json",
) -> tuple[Path, Path]:
    """Write markdown and JSON reports to disk."""
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    md_path = out_path / md_filename
    json_path = out_path / json_filename

    md_content = format_markdown_table(summary)
    md_path.write_text(md_content, encoding="utf-8")

    json_content = summary.model_dump_json(indent=2)
    json_path.write_text(json_content, encoding="utf-8")

    return md_path, json_path
