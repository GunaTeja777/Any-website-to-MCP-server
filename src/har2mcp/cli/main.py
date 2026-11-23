"""Main CLI entrypoint for har2mcp."""

from __future__ import annotations

from pathlib import Path

import click
from pydantic import BaseModel, ConfigDict, Field

from har2mcp import __version__


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
def generate(har_file: Path, out_dir: Path, allow_writes: bool) -> None:
    """Generate a working MCP server from a HAR file."""
    config = GenerateConfig(
        har_path=har_file,
        out_dir=out_dir,
        allow_writes=allow_writes,
    )
    click.echo(
        f"HAR2MCP v{__version__} - Scaffolding mode\n"
        f"Target HAR: {config.har_path}\n"
        f"Output Directory: {config.out_dir}\n"
        f"Allow Writes: {config.allow_writes}\n"
        "Generation pipeline scaffold initialized."
    )


def main() -> None:
    """CLI execution entrypoint."""
    cli()
