"""Pytest fixtures for har2mcp test suite."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner


@pytest.fixture
def fixtures_dir() -> Path:
    """Return path to project fixtures directory."""
    return Path(__file__).resolve().parent.parent / "fixtures"


@pytest.fixture
def sample_har_path(fixtures_dir: Path) -> Path:
    """Return path to minimal valid HAR fixture."""
    har_file = fixtures_dir / "sample_minimal.har"
    assert har_file.exists(), f"Fixture file not found: {har_file}"
    return har_file


@pytest.fixture
def cli_runner() -> CliRunner:
    """Return a Click CliRunner instance for CLI testing."""
    return CliRunner()
