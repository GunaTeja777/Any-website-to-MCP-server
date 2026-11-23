"""Tracking, analytics, and advertising domain blocklist for HAR filtering."""

from __future__ import annotations

import json
from pathlib import Path

DEFAULT_TRACKING_DOMAINS: frozenset[str] = frozenset(
    {
        "google-analytics.com",
        "googletagmanager.com",
        "googleadservices.com",
        "doubleclick.net",
        "facebook.net",
        "connect.facebook.net",
        "facebook.com",
        "segment.io",
        "segment.com",
        "mixpanel.com",
        "sentry.io",
        "hotjar.com",
        "mouseflow.com",
        "clarity.ms",
        "datadoghq.com",
        "browser-intake-datadoghq.com",
        "newrelic.com",
        "nr-data.net",
        "amplitude.com",
        "branch.io",
        "appsflyer.com",
        "criteo.com",
        "adnxs.com",
        "rubiconproject.com",
        "intercom.io",
        "zendesk.com",
        "driftt.com",
        "fullstory.com",
        "logrocket.com",
        "bugsnag.com",
        "rollbar.com",
        "optimizely.com",
        "heap.io",
        "heapanalytics.com",
    }
)


def load_blocklist(custom_path: Path | str | None = None) -> set[str]:
    """Load blocked domain set from default plus optional custom JSON or text file.

    Args:
        custom_path: Path to custom blocklist file (JSON list or line-delimited text).

    Returns:
        Set of lowercase blocked domains.
    """
    domains = set(DEFAULT_TRACKING_DOMAINS)
    if not custom_path:
        return domains

    path = Path(custom_path)
    if not path.is_file():
        return domains

    content = path.read_text(encoding="utf-8").strip()
    if not content:
        return domains

    if content.startswith("["):
        try:
            parsed = json.loads(content)
            if isinstance(parsed, list):
                domains.update(str(item).strip().lower() for item in parsed if item)
        except json.JSONDecodeError:
            pass
    else:
        for line in content.splitlines():
            line_str = line.strip().lower()
            if line_str and not line_str.startswith("#"):
                domains.add(line_str)

    return domains


def is_domain_blocked(domain: str, blocked_domains: set[str] | frozenset[str]) -> bool:
    """Check if domain or any parent domain is present in blocklist.

    Args:
        domain: Hostname or domain string to inspect.
        blocked_domains: Set of blocked base domains.

    Returns:
        True if the domain matches or is a subdomain of a blocked domain.
    """
    normalized = domain.strip().lower()
    if not normalized:
        return False

    if normalized in blocked_domains:
        return True

    # Check parent subdomains (e.g. "api.mixpanel.com" -> "mixpanel.com")
    parts = normalized.split(".")
    for i in range(1, len(parts) - 1):
        parent = ".".join(parts[i:])
        if parent in blocked_domains:
            return True

    return False
