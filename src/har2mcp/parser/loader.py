"""HAR file loading and end-to-end parsing pipeline."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from har2mcp.parser.blocklist import load_blocklist
from har2mcp.parser.filter import filter_entries
from har2mcp.parser.models import Entry, HARRoot


def load_har(source: Path | str | dict[str, Any]) -> HARRoot:
    """Parse HAR JSON document into strongly-typed HARRoot model.

    Args:
        source: File path, JSON string, or pre-parsed dictionary.

    Returns:
        Validated HARRoot model instance.

    Raises:
        ValueError: If source format is invalid or missing 'log' root.
    """
    data: dict[str, Any]
    if isinstance(source, dict):
        data = source
    elif isinstance(source, Path) or (isinstance(source, str) and Path(source).is_file()):
        content = Path(source).read_text(encoding="utf-8")
        data = json.loads(content)
    elif isinstance(source, str):
        data = json.loads(source)
    else:
        raise ValueError(f"Unsupported HAR source type: {type(source)}")

    if "log" not in data:
        raise ValueError("Invalid HAR document: root key 'log' missing.")

    return HARRoot.model_validate(data)


def parse_har(
    file_path: Path | str,
    include_domains: list[str] | set[str] | None = None,
    blocklist_file: Path | str | None = None,
    primary_domain: str | None = None,
) -> list[Entry]:
    """Ingest a HAR file and return filtered, decoded API entries.

    Args:
        file_path: Path to .har file.
        include_domains: Allowed third-party domain list.
        blocklist_file: Optional path to custom tracking blocklist file.
        primary_domain: Target domain to retain. Inferred if None.

    Returns:
        Clean list of API-like Entry models.
    """
    root = load_har(file_path)
    blocked_domains = load_blocklist(blocklist_file)
    return filter_entries(
        entries=root.log.entries,
        primary_domain=primary_domain,
        include_domains=include_domains,
        blocklist_domains=blocked_domains,
    )
