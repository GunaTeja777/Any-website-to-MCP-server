"""Main CLI entrypoint for har2mcp."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

import click
from pydantic import BaseModel, ConfigDict, Field

from har2mcp import __version__
from har2mcp.auth.detector import detect_auth_spec
from har2mcp.cluster.engine import cluster_entries
from har2mcp.codegen.generator import generate_server_project
from har2mcp.design.engine import ToolDesigner, deterministic_design_tools
from har2mcp.llm.client import (
    GeminiLLMClient,
    NoOpLLMClient,
    OpenAILLMClient,
    get_default_llm_client,
)
from har2mcp.parser.loader import parse_har
from har2mcp.redact.engine import redact
from har2mcp.schema.inferrer import infer_schemas
from har2mcp.verify.repair import RepairLoop
from har2mcp.verify.verifier import ServerVerifier

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


class GenerateConfig(BaseModel):
    """Configuration model for the `generate` command."""

    model_config = ConfigDict(frozen=True)

    har_path: Path = Field(..., description="Path to the source HAR recording file.")
    out_dir: Path = Field(..., description="Target directory for generated MCP server.")
    allow_writes: bool = Field(
        default=False,
        description=(
            "Whether to generate tools for state-mutating requests "
            "(POST, PUT, DELETE, PATCH)."
        ),
    )
    verify: bool = Field(
        default=False,
        description="Whether to run verification against a local replay mock server.",
    )
    repair: bool = Field(
        default=False,
        description="Run LLM-driven repair loop on verification failure.",
    )


@click.group(
    context_settings={"help_option_names": ["-h", "--help"]},
    invoke_without_command=False,
)
@click.version_option(version=__version__, prog_name="har2mcp")
def cli() -> None:
    """HAR2MCP: Generate working MCP servers from browser HAR network captures."""


@cli.command("generate")
@click.argument(
    "har_file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.option(
    "-o",
    "--out",
    "out_dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=Path("./out"),
    show_default=True,
    help="Output directory where the MCP server project will be generated.",
)
@click.option(
    "--allow-writes",
    is_flag=True,
    default=False,
    help=(
        "Allow generation of mutation tools (POST, PUT, DELETE, PATCH). "
        "Defaults to read-only (GET, HEAD)."
    ),
)
@click.option(
    "--verify",
    is_flag=True,
    default=False,
    help="Spin up server and verify tool outputs against local HAR replay mock.",
)
@click.option(
    "--repair",
    is_flag=True,
    default=False,
    help="Run LLM self-repair loop on verification failure (up to 3 iterations).",
)
def generate(
    har_file: Path,
    out_dir: Path,
    allow_writes: bool,
    verify: bool,
    repair: bool,
) -> None:
    """Generate a working MCP server from a HAR file."""
    config = GenerateConfig(
        har_path=har_file,
        out_dir=out_dir,
        allow_writes=allow_writes,
        verify=verify,
        repair=repair,
    )

    click.echo(
        f"HAR2MCP v{__version__} - Scaffolding mode\n"
        f"Target HAR: {config.har_path}\n"
        f"Output Directory: {config.out_dir}\n"
        f"Allow Writes: {config.allow_writes}\n"
        f"Analyzing {config.har_path}..."
    )

    # 1. Parse & filter HAR traffic
    entries = parse_har(config.har_path)
    if not entries:
        click.echo("Error: No API-like traffic found in HAR file.", err=True)
        return
    click.echo(f"  [1/6] Filtered traffic: {len(entries)} API entries retained")

    # 2. Redact sensitive credentials
    redact_result = redact(entries)
    n_placeholders = len(redact_result.placeholder_map)
    click.echo(f"  [2/6] Redacted secrets: {n_placeholders} placeholders created")

    # 3. Cluster endpoints
    clusters = cluster_entries(redact_result.entries)
    click.echo(f"  [3/6] Clustered endpoints: {len(clusters)} distinct route patterns")

    # 4. Infer schemas
    schemas = infer_schemas(clusters)
    has_pagination = any(s.pagination for s in schemas)
    click.echo(
        f"  [4/6] Inferred schemas: {len(schemas)} (pagination: {has_pagination})"
    )

    # 5. Detect auth
    auth_spec = detect_auth_spec(redact_result.entries)
    click.echo(f"  [5/6] Detected auth type: {auth_spec.primary_type.value}")

    # 6. Tool Design
    openai_key = os.environ.get("OPENAI_API_KEY")
    if openai_key:
        llm = OpenAILLMClient(api_key=openai_key)
        designer = ToolDesigner(llm_client=llm)
        design_spec = designer.design(clusters, schemas)
    else:
        design_spec = deterministic_design_tools(clusters, schemas)

    server_name = design_spec.server_name or "api-mcp-server"
    click.echo(
        f"  [6/6] Designed tools: {len(design_spec.tools)} tools for {server_name}"
    )

    # Generate MCP server project files
    generated_paths = generate_server_project(
        tool_specs=design_spec.tools,
        auth_spec=auth_spec,
        out_dir=config.out_dir,
        server_name=server_name,
        allow_writes=config.allow_writes,
        has_pagination=has_pagination,
    )
    click.echo(f"\nServer project generated successfully in: {config.out_dir}")
    for p in generated_paths:
        click.echo(f"  - {p.name}")

    # Verification / Repair
    if config.verify or config.repair:
        click.echo("\n--- Verification Suite ---")
        asyncio.run(
            _run_verify_and_repair(
                config=config,
                entries=redact_result.entries,
                tool_specs=design_spec.tools,
                redaction_map={s.placeholder: s for s in redact_result.secrets},
            )
        )


async def _run_verify_and_repair(
    config: GenerateConfig,
    entries: list[Any],
    tool_specs: list[Any],
    redaction_map: dict[str, Any],
) -> None:
    """Run verification and optional repair loop asynchronously."""
    verifier = ServerVerifier(config.out_dir, entries, tool_specs)
    report = await verifier.verify()

    score_pct = report.score * 100
    click.echo(
        f"Score: {score_pct:.0f}% ({report.passed_tools}/{report.total_tools} passed)"
    )
    for r in report.results:
        status_sym = "PASS" if r.passed else "FAIL"
        err_info = f" - {r.error_message}" if r.error_message else ""
        click.echo(f"  [{status_sym}] {r.tool_name}{err_info}")

    if not report.all_passed and config.repair:
        click.echo("\n--- Initiating Self-Repair Loop (max 3 iterations) ---")
        openai_key = os.environ.get("OPENAI_API_KEY")
        llm_client = (
            OpenAILLMClient(api_key=openai_key) if openai_key else NoOpLLMClient()
        )

        repair_loop = RepairLoop(
            server_dir=config.out_dir,
            entries=entries,
            tool_specs=tool_specs,
            llm_client=llm_client,
            redaction_map=redaction_map,
            max_iterations=3,
        )
        repair_res = await repair_loop.run()
        for item in repair_res.iterations:
            res_sym = "REPAIRED" if item.passed_after_patch else "PATCH_FAILED"
            click.echo(f"  Iter {item.iteration}: [{res_sym}] {item.tool_name}")

        click.echo(
            f"Final Verification Score: {repair_res.final_report.score * 100:.0f}%"
        )


def main() -> None:
    """CLI execution entrypoint."""
    cli()
