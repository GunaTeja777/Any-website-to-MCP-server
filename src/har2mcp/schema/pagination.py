"""Pagination pattern detection for endpoint clusters."""

from __future__ import annotations

import json
import re
from typing import Any

from har2mcp.cluster.models import EndpointCluster
from har2mcp.schema.models import PaginationInfo, PaginationType

PAGE_PARAM_NAMES: frozenset[str] = frozenset(
    {"page", "p", "page_number", "page_no", "pageno", "pagenum", "page_num"}
)
LIMIT_PARAM_NAMES: frozenset[str] = frozenset(
    {"limit", "per_page", "perpage", "page_size", "pagesize", "count", "size"}
)
OFFSET_PARAM_NAMES: frozenset[str] = frozenset({"offset", "skip", "start", "from"})
CURSOR_PARAM_NAMES: frozenset[str] = frozenset(
    {
        "cursor",
        "next_cursor",
        "next_token",
        "nexttoken",
        "after",
        "starting_after",
        "page_token",
        "pagetoken",
    }
)
RESPONSE_CURSOR_KEYS: tuple[str, ...] = (
    "next_cursor",
    "next_token",
    "nextCursor",
    "nextToken",
    "nextPageToken",
    "cursor",
    "starting_after",
)

LINK_NEXT_REGEX: re.Pattern[str] = re.compile(r'<([^>]+)>;\s*rel=["\']?next["\']?')


def _extract_response_cursor(responses: list[Any]) -> str | None:
    """Check JSON response bodies for cursor indicator fields."""
    for res in responses:
        if getattr(res, "content", None) and res.content.text:
            try:
                body = json.loads(res.content.text)
                if isinstance(body, dict):
                    for key in RESPONSE_CURSOR_KEYS:
                        if key in body and body[key]:
                            return key
                    # Check nested meta/pagination dictionaries
                    for container in ("meta", "pagination", "page_info", "pageInfo"):
                        if isinstance(body.get(container), dict):
                            for key in RESPONSE_CURSOR_KEYS:
                                if key in body[container] and body[container][key]:
                                    return f"{container}.{key}"
            except Exception:
                pass
    return None


def detect_pagination(cluster: EndpointCluster) -> PaginationInfo | None:
    """Examine cluster queries and responses to identify pagination strategies.

    Args:
        cluster: Grouped endpoint cluster.

    Returns:
        PaginationInfo model if a pattern is recognized, else None.
    """
    query_names = {
        name.lower(): name
        for name in (
            list(cluster.observed_query_params.stable.keys())
            + cluster.observed_query_params.variable
            + cluster.observed_query_params.optional
        )
    }

    # 1. Check RFC 5988 Link headers in responses
    has_link_header = False
    for res in cluster.sample_responses:
        link_val = res.get_header("link")
        if link_val and LINK_NEXT_REGEX.search(link_val):
            has_link_header = True
            break

    # 2. Check cursor query parameters
    matched_cursor = next(
        (query_names[k] for k in CURSOR_PARAM_NAMES if k in query_names), None
    )
    res_cursor_field = _extract_response_cursor(cluster.sample_responses)

    if matched_cursor or (has_link_header and not query_names):
        if matched_cursor or res_cursor_field:
            return PaginationInfo(
                type=PaginationType.CURSOR,
                cursor_param=matched_cursor,
                response_cursor_field=res_cursor_field,
                link_header_present=has_link_header,
                description=f"Cursor-based pagination via query '{matched_cursor}'",
            )
        if has_link_header:
            return PaginationInfo(
                type=PaginationType.LINK_HEADER,
                link_header_present=True,
                description="RFC 5988 Link header pagination (rel=next)",
            )

    # 3. Check offset/limit
    matched_offset = next(
        (query_names[k] for k in OFFSET_PARAM_NAMES if k in query_names), None
    )
    matched_limit = next(
        (query_names[k] for k in LIMIT_PARAM_NAMES if k in query_names), None
    )

    if matched_offset:
        return PaginationInfo(
            type=PaginationType.OFFSET_LIMIT,
            offset_param=matched_offset,
            limit_param=matched_limit,
            link_header_present=has_link_header,
            description=(
                f"Offset pagination with offset='{matched_offset}'"
                + (f" and limit='{matched_limit}'" if matched_limit else "")
            ),
        )

    # 4. Check page/limit
    matched_page = next(
        (query_names[k] for k in PAGE_PARAM_NAMES if k in query_names), None
    )
    if matched_page:
        return PaginationInfo(
            type=PaginationType.PAGE_LIMIT,
            page_param=matched_page,
            limit_param=matched_limit,
            link_header_present=has_link_header,
            description=(
                f"Page-based pagination with page='{matched_page}'"
                + (f" and limit='{matched_limit}'" if matched_limit else "")
            ),
        )

    if has_link_header:
        return PaginationInfo(
            type=PaginationType.LINK_HEADER,
            link_header_present=True,
            description="RFC 5988 Link header pagination (rel=next)",
        )

    return None
