"""Traffic filtering logic to isolate API-like exchanges from raw HAR entries."""

from __future__ import annotations

import base64
import json
from collections import Counter
from urllib.parse import urlparse

from har2mcp.parser.blocklist import is_domain_blocked, load_blocklist
from har2mcp.parser.models import Entry, Request, Response

STATIC_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".svg",
        ".webp",
        ".ico",
        ".bmp",
        ".tiff",
        ".css",
        ".scss",
        ".less",
        ".woff",
        ".woff2",
        ".ttf",
        ".eot",
        ".otf",
        ".js",
        ".mjs",
        ".jsx",
        ".ts",
        ".tsx",
        ".map",
        ".mp4",
        ".mp3",
        ".wav",
        ".webm",
        ".ogg",
        ".avi",
        ".pdf",
        ".zip",
        ".tar",
        ".gz",
    }
)

STATIC_MIME_PREFIXES: tuple[str, ...] = (
    "image/",
    "font/",
    "audio/",
    "video/",
    "text/css",
    "application/javascript",
    "text/javascript",
    "application/x-javascript",
)

STATIC_RESOURCE_TYPES: frozenset[str] = frozenset(
    {"image", "stylesheet", "font", "script", "media"}
)

JSON_MIME_INDICATORS: tuple[str, ...] = (
    "application/json",
    "application/ld+json",
    "application/graphql",
    "application/graphql-response+json",
    "application/vnd.api+json",
    "application/problem+json",
    "text/json",
    "+json",
)


def extract_domain(url: str) -> str:
    """Extract lowercase hostname from URL.

    Args:
        url: Absolute or relative URL.

    Returns:
        Extracted hostname in lowercase, or empty string.
    """
    try:
        parsed = urlparse(url)
        return (parsed.hostname or "").lower()
    except Exception:
        return ""


def get_apex_domain(hostname: str) -> str:
    """Return apex domain (e.g. 'sub.example.com' -> 'example.com').

    Args:
        hostname: Lowercase domain string.

    Returns:
        Base apex domain.
    """
    parts = hostname.split(".")
    if len(parts) >= 2:
        return ".".join(parts[-2:])
    return hostname


def is_static_asset(
    url: str,
    request: Request,
    response: Response,
    resource_type: str | None,
) -> bool:
    """Determine whether request represents a static media, style, or script asset.

    Args:
        url: Request URL.
        request: Recorded HTTP request.
        response: Recorded HTTP response.
        resource_type: DevTools resource type (e.g. 'image', 'stylesheet').

    Returns:
        True if the exchange corresponds to static asset traffic.
    """
    if resource_type and resource_type.lower() in STATIC_RESOURCE_TYPES:
        return True

    parsed_path = urlparse(url).path.lower()
    for ext in STATIC_EXTENSIONS:
        if parsed_path.endswith(ext):
            return True

    res_content_type = (
        response.content.mime_type or response.get_header("content-type") or ""
    ).lower()
    return any(res_content_type.startswith(prefix) for prefix in STATIC_MIME_PREFIXES)


def is_api_traffic(entry: Entry) -> bool:
    """Determine whether entry represents an API transaction.

    Checks DevTools resource types, Content-Type headers, URL patterns,
    and payload characteristics.

    Args:
        entry: HAR entry candidate.

    Returns:
        True if the entry is an API transaction.
    """
    req = entry.request
    res = entry.response

    # Browser DevTools flags
    if entry.resource_type and entry.resource_type.lower() in {"xhr", "fetch"}:
        return True

    # Check XHR header
    x_requested = req.get_header("x-requested-with")
    if x_requested and x_requested.lower() == "xmlhttprequest":
        return True

    # Check response / request content types
    res_mime = (res.content.mime_type or res.get_header("content-type") or "").lower()
    req_mime = (
        (req.post_data.mime_type if req.post_data else None)
        or req.get_header("content-type")
        or ""
    ).lower()

    for indicator in JSON_MIME_INDICATORS:
        if indicator in res_mime or indicator in req_mime:
            return True

    # Check URL path cues
    path = urlparse(req.url).path.lower()
    if "/api/" in path or "/graphql" in path or path.startswith("/api"):
        return True

    # Check if response payload parses as JSON
    if res.content.text:
        stripped = res.content.text.strip()
        if (stripped.startswith("{") and stripped.endswith("}")) or (
            stripped.startswith("[") and stripped.endswith("]")
        ):
            try:
                json.loads(stripped)
                return True
            except (json.JSONDecodeError, UnicodeDecodeError):
                pass

    return False


def decode_entry_body(entry: Entry) -> Entry:
    """Decode base64-encoded response payload if present.

    Args:
        entry: Source entry.

    Returns:
        Entry with decoded UTF-8 response text when applicable.
    """
    res = entry.response
    if res.content.encoding == "base64" and res.content.text:
        try:
            raw_bytes = base64.b64decode(res.content.text)
            decoded_text = raw_bytes.decode("utf-8", errors="replace")
            new_content = res.content.model_copy(
                update={"text": decoded_text, "encoding": None}
            )
            new_res = res.model_copy(update={"content": new_content})
            return entry.model_copy(update={"response": new_res})
        except Exception:
            return entry
    return entry


def infer_primary_domain(entries: list[Entry]) -> str | None:
    """Infer the primary website domain from candidate API entries.

    Args:
        entries: Raw or candidate entries.

    Returns:
        The most common domain or first entry domain.
    """
    domain_counts: Counter[str] = Counter()
    for entry in entries:
        dom = extract_domain(entry.request.url)
        if dom:
            domain_counts[dom] += 1

    if not domain_counts:
        return None

    # Pick the most frequent domain
    most_common, _ = domain_counts.most_common(1)[0]
    return most_common


def filter_entries(
    entries: list[Entry],
    primary_domain: str | None = None,
    include_domains: list[str] | set[str] | None = None,
    blocklist_domains: set[str] | None = None,
) -> list[Entry]:
    """Filter raw HAR entries down to clean, API-like traffic.

    Args:
        entries: Raw HAR log entries.
        primary_domain: Target domain. Inferred if not specified.
        include_domains: Additional third-party domains permitted.
        blocklist_domains: Explicit tracking/ad domains to drop.

    Returns:
        Filtered list of API entries with response bodies decoded.
    """
    blocked = blocklist_domains if blocklist_domains is not None else load_blocklist()
    allowed_domains = {d.strip().lower() for d in (include_domains or []) if d}

    # First pass: decode base64 bodies and remove obvious static assets & tracking
    preliminary: list[Entry] = []
    for raw_entry in entries:
        entry = decode_entry_body(raw_entry)
        req = entry.request
        res = entry.response

        # Exclude cancelled/aborted browser requests (status 0)
        if res.status <= 0:
            continue

        domain = extract_domain(req.url)
        if not domain or is_domain_blocked(domain, blocked):
            continue

        if is_static_asset(req.url, req, res, entry.resource_type):
            continue

        if not is_api_traffic(entry):
            continue

        preliminary.append(entry)

    if not preliminary:
        return []

    # Resolve primary domain if not provided
    active_primary = (
        primary_domain.strip().lower()
        if primary_domain
        else infer_primary_domain(preliminary)
    )
    primary_apex = get_apex_domain(active_primary) if active_primary else ""

    # Second pass: enforce primary & third-party domain constraints
    results: list[Entry] = []
    for entry in preliminary:
        domain = extract_domain(entry.request.url)
        entry_apex = get_apex_domain(domain)

        # Allow if domain matches primary domain or apex, or is in include_domains
        is_primary = (domain == active_primary) or (
            bool(primary_apex) and entry_apex == primary_apex
        )
        is_included = domain in allowed_domains or entry_apex in allowed_domains

        if is_primary or is_included:
            results.append(entry)

    return results
