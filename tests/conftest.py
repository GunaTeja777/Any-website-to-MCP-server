"""Pytest fixtures for har2mcp test suite."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner


@pytest.fixture
def fixtures_dir() -> Path:
    """Return path to project fixtures directory."""
    return Path(__file__).resolve().parent / "fixtures"


@pytest.fixture
def sample_har_path() -> Path:
    """Return path to root minimal fixture."""
    root_fixtures = Path(__file__).resolve().parents[1] / "fixtures"
    har_file = root_fixtures / "sample_minimal.har"
    assert har_file.exists(), f"Fixture file not found: {har_file}"
    return har_file


@pytest.fixture
def synthetic_api_with_secrets_path(fixtures_dir: Path) -> Path:
    """Return path to synthetic API HAR containing secrets."""
    path = fixtures_dir / "synthetic_api_with_secrets.har"
    assert path.exists(), f"Fixture file not found: {path}"
    return path


@pytest.fixture
def synthetic_mixed_traffic_path(fixtures_dir: Path) -> Path:
    """Return path to synthetic mixed traffic HAR."""
    path = fixtures_dir / "synthetic_mixed_traffic.har"
    assert path.exists(), f"Fixture file not found: {path}"
    return path


@pytest.fixture
def synthetic_edge_cases_path(fixtures_dir: Path) -> Path:
    """Return path to synthetic edge cases HAR."""
    path = fixtures_dir / "synthetic_edge_cases.har"
    assert path.exists(), f"Fixture file not found: {path}"
    return path


@pytest.fixture
def cli_runner() -> CliRunner:
    """Return a Click CliRunner instance for CLI testing."""
    return CliRunner()
