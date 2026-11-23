"""GraphQL transaction detection and operationName extraction."""

from __future__ import annotations

import json
import re
from urllib.parse import urlparse

from har2mcp.parser.models import Entry

GRAPHQL_PATH_ENDINGS: tuple[str, ...] = ("/graphql", "/gql")

OPERATION_REGEX: re.Pattern[str] = re.compile(
    r"\b(?:query|mutation|subscription)\s+([A-Za-z0-9_]+)",
    re.IGNORECASE,
)


def is_graphql_entry(entry: Entry) -> bool:
    """Determine whether a transaction targets a GraphQL endpoint.

    Args:
        entry: Recorded HTTP exchange.

    Returns:
        True if the request matches GraphQL path or payload signatures.
    """
    path = urlparse(entry.request.url).path.rstrip("/").lower()
    for ending in GRAPHQL_PATH_ENDINGS:
        if path == ending or path.endswith(ending):
            return True

    req_ct = (
        (entry.request.post_data.mime_type if entry.request.post_data else "")
        or entry.request.get_header("content-type")
        or ""
    ).lower()
    res_ct = (
        entry.response.content.mime_type
        or entry.response.get_header("content-type")
        or ""
    ).lower()

    return "graphql" in req_ct or "graphql" in res_ct


def extract_graphql_operation(entry: Entry) -> str | None:
    """Extract or infer the operationName for a GraphQL entry.

    Args:
        entry: Recorded HTTP exchange.

    Returns:
        Operation name string, or None if not determinable.
    """
    # 1. Check POST body JSON
    req = entry.request
    query_str: str | None = None

    if req.post_data and req.post_data.text:
        trimmed = req.post_data.text.strip()
        if trimmed.startswith("{") and trimmed.endswith("}"):
            try:
                body = json.loads(trimmed)
                if isinstance(body, dict):
                    op_name = body.get("operationName")
                    if op_name and isinstance(op_name, str) and op_name.strip():
                        return op_name.strip()
                    query_str = body.get("query")
            except Exception:
                pass
        else:
            query_str = trimmed

    # 2. Check query string parameters
    op_param = req.get_query_param("operationName")
    if op_param and op_param.strip():
        return op_param.strip()

    if not query_str:
        query_param = req.get_query_param("query")
        if query_param:
            query_str = query_param

    # 3. Regex-scan query text
    if query_str:
        match = OPERATION_REGEX.search(query_str)
        if match:
            return match.group(1).strip()

        # Fallback to anonymous classification
        lowered = query_str.strip().lower()
        if lowered.startswith("mutation"):
            return "AnonymousMutation"
        if lowered.startswith("query"):
            return "AnonymousQuery"
        return "AnonymousOperation"

    return None
