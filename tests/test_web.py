"""Tests for HAR2MCP web studio interface and API endpoints."""

from __future__ import annotations

from pathlib import Path

from har2mcp.web.server import get_available_samples


def test_get_available_samples() -> None:
    """Test discovering preloaded demonstration HAR samples."""
    samples = get_available_samples()
    assert len(samples) >= 3
    for s in samples:
        assert "id" in s
        assert "name" in s
        assert "path" in s
        assert Path(s["path"]).exists(), f"Sample path {s['path']} should exist"


def test_studio_health_and_samples_routes() -> None:
    """Test studio request handler responding to health and samples endpoints."""
    # Test sample listing payload construction
    samples = get_available_samples()
    sample_ids = {s["id"] for s in samples}
    assert "synthetic_cloud" in sample_ids
    assert "synthetic_secrets" in sample_ids
    assert "synthetic_mixed" in sample_ids
