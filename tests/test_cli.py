"""Unit tests for the HAR2MCP CLI."""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from har2mcp import __version__
from har2mcp.cli.main import cli


def test_cli_version(cli_runner: CliRunner) -> None:
    """Test `har2mcp --version` output."""
    result = cli_runner.invoke(cli, ["--version"])
    assert result.exit_code == 0
    assert f"version {__version__}" in result.output


def test_cli_help(cli_runner: CliRunner) -> None:
    """Test `har2mcp --help` displays commands."""
    result = cli_runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "generate" in result.output
    assert "HAR2MCP" in result.output


def test_cli_generate_help(cli_runner: CliRunner) -> None:
    """Test `har2mcp generate --help` options."""
    result = cli_runner.invoke(cli, ["generate", "--help"])
    assert result.exit_code == 0
    assert "--out" in result.output
    assert "--allow-writes" in result.output


def test_cli_generate_success(
    cli_runner: CliRunner,
    sample_har_path: Path,
    tmp_path: Path,
) -> None:
    """Test `har2mcp generate <file.har> --out ./out` invocation."""
    out_dir = tmp_path / "out"
    result = cli_runner.invoke(
        cli,
        ["generate", str(sample_har_path), "--out", str(out_dir)],
    )
    assert result.exit_code == 0
    assert "Scaffolding mode" in result.output
    assert str(sample_har_path) in result.output
    assert str(out_dir) in result.output
    assert "Allow Writes: False" in result.output


def test_cli_generate_allow_writes(
    cli_runner: CliRunner,
    sample_har_path: Path,
    tmp_path: Path,
) -> None:
    """Test `har2mcp generate` with `--allow-writes`."""
    out_dir = tmp_path / "out"
    result = cli_runner.invoke(
        cli,
        ["generate", str(sample_har_path), "--out", str(out_dir), "--allow-writes"],
    )
    assert result.exit_code == 0
    assert "Allow Writes: True" in result.output


def test_cli_generate_missing_file(cli_runner: CliRunner, tmp_path: Path) -> None:
    """Test `har2mcp generate` fails gracefully when file does not exist."""
    non_existent = tmp_path / "does_not_exist.har"
    result = cli_runner.invoke(cli, ["generate", str(non_existent)])
    assert result.exit_code != 0
    assert "does not exist" in result.output
