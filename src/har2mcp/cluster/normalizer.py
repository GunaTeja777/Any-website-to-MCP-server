"""Path segment normalization, dynamic parameter detection, and ID heuristics."""

from __future__ import annotations

import re
from collections.abc import Sequence
from urllib.parse import urlparse

INTEGER_ID_PATTERN: re.Pattern[str] = re.compile(r"^\d+$")
UUID_PATTERN: re.Pattern[str] = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
HEX_HASH_PATTERN: re.Pattern[str] = re.compile(r"^[0-9a-fA-F]{24,64}$")
PREFIXED_ID_PATTERN: re.Pattern[str] = re.compile(
    r"^(?:usr|user|ord|order|item|txn|cust|acc|doc|org|proj|msg|sub|tok|card|evt|req|res)_[a-zA-Z0-9_-]+$",
    re.IGNORECASE,
)
REDACTED_PLACEHOLDER_PATTERN: re.Pattern[str] = re.compile(r"^<[A-Z_]+_\d+>$")
SLUG_OR_ALPHANUMERIC_ID_PATTERN: re.Pattern[str] = re.compile(
    r"^(?=[a-zA-Z0-9_-]*\d)(?=[a-zA-Z0-9_-]*[a-zA-Z])[a-zA-Z0-9_-]{6,}$"
)

STATIC_RESERVED_WORDS: frozenset[str] = frozenset(
    {
        "api",
        "v1",
        "v2",
        "v3",
        "v4",
        "v5",
        "graphql",
        "gql",
        "users",
        "orders",
        "items",
        "products",
        "auth",
        "login",
        "logout",
        "profile",
        "me",
        "settings",
        "status",
        "health",
        "healthz",
        "reports",
        "data",
        "admin",
        "dashboard",
        "search",
        "export",
        "import",
        "notifications",
        "webhook",
        "webhooks",
        "config",
        "info",
        "details",
    }
)


def split_path(path: str) -> list[str]:
    """Split path into non-empty stripped segments.

    Args:
        path: Path string, e.g. '/api/v1/users'.

    Returns:
        List of segment strings without surrounding slashes.
    """
    clean_path = urlparse(path).path if "://" in path else path
    return [s.strip() for s in clean_path.strip("/").split("/") if s.strip()]


def singularize(word: str) -> str:
    """Derive singular noun from plural segment for parameter naming.

    Args:
        word: Plural noun segment, e.g. 'users', 'categories'.

    Returns:
        Singular form of the noun, e.g. 'user', 'category'.
    """
    lowered = word.lower()
    if lowered.endswith("ies") and len(lowered) > 4:
        return lowered[:-3] + "y"
    if (
        lowered.endswith("es")
        and len(lowered) > 4
        and lowered[-3] in {"s", "x", "z", "c", "h"}
    ):
        return lowered[:-2]
    if lowered.endswith("s") and not lowered.endswith("ss") and len(lowered) > 2:
        return lowered[:-1]
    return lowered


def looks_like_id(segment: str) -> bool:
    """Determine whether a path segment exhibits characteristics of an ID/slug.

    Args:
        segment: Individual path segment string.

    Returns:
        True if the segment resembles an integer, UUID, hash, or dynamic slug.
    """
    cleaned = segment.strip()
    lowered = cleaned.lower()

    if lowered in STATIC_RESERVED_WORDS:
        return False

    if INTEGER_ID_PATTERN.match(cleaned):
        return True

    if UUID_PATTERN.match(cleaned):
        return True

    if HEX_HASH_PATTERN.match(cleaned):
        return True

    if PREFIXED_ID_PATTERN.match(cleaned):
        return True

    if REDACTED_PLACEHOLDER_PATTERN.match(cleaned):
        return True

    # Hyphenated UUID-like slugs (e.g. 'abc-uuid', 'item-1234-xyz')
    if "-" in cleaned and len(cleaned) >= 6:
        parts = cleaned.split("-")
        if any(p.isdigit() or len(p) >= 4 for p in parts):
            return True
        if len(parts) >= 2:
            return True

    return bool(SLUG_OR_ALPHANUMERIC_ID_PATTERN.match(cleaned))


def derive_param_name(
    prev_segment: str | None,
    used_names: set[str],
) -> str:
    """Derive a clean, descriptive parameter name avoiding collisions in the same path.

    Args:
        prev_segment: Preceding path segment, e.g. 'users'.
        used_names: Set of already assigned parameter names in current template.

    Returns:
        Unique placeholder name without braces, e.g. 'user_id'.
    """
    base_name = "id"
    if prev_segment:
        clean_prev = re.sub(r"[^a-zA-Z0-9_]", "", prev_segment)
        if clean_prev:
            sing = singularize(clean_prev)
            base_name = f"{sing}_id"

    candidate = base_name
    counter = 2
    while candidate in used_names:
        candidate = f"{base_name}_{counter}"
        counter += 1

    used_names.add(candidate)
    return candidate


def are_paths_compatible(
    segments_a: Sequence[str],
    segments_b: Sequence[str],
) -> bool:
    """Check if two path segment sequences can be unified into the same template.

    Two paths are compatible if they have the same segment length, static segments
    match, and differing segments at any position look like IDs or dynamic tokens.

    Args:
        segments_a: First path segments.
        segments_b: Second path segments.

    Returns:
        True if paths share route structure and can be clustered together.
    """
    if len(segments_a) != len(segments_b):
        return False

    for i in range(len(segments_a)):
        val_a = segments_a[i]
        val_b = segments_b[i]

        if val_a == val_b:
            continue

        # Differing segments must qualify as variable/ID candidates, not keywords
        is_var_a = looks_like_id(val_a)
        is_var_b = looks_like_id(val_b)

        # Neither can be a reserved static word when they differ
        if (
            val_a.lower() in STATIC_RESERVED_WORDS
            or val_b.lower() in STATIC_RESERVED_WORDS
        ):
            return False

        if is_var_a or is_var_b:
            continue

        # Cross-sample differing slug segments between matching static boundaries
        continue

    return True


def normalize_path_cluster(
    paths: Sequence[str],
) -> tuple[str, list[str]]:
    """Synthesize a normalized path template using evidence across cluster paths.

    Args:
        paths: Sequence of raw or redacted paths belonging to the same endpoint.

    Returns:
        Tuple of (path_template, list of extracted parameter names).
    """
    if not paths:
        return "/", []

    segment_lists = [split_path(p) for p in paths]
    length = len(segment_lists[0])

    template_segments: list[str] = []
    param_names: list[str] = []
    used_names: set[str] = set()

    for i in range(length):
        values_at_i = {segs[i] for segs in segment_lists if i < len(segs)}

        # Evidence across multiple samples or intrinsic ID detection
        has_variance = len(values_at_i) > 1
        all_look_like_id = all(looks_like_id(v) for v in values_at_i)
        none_reserved = not any(v.lower() in STATIC_RESERVED_WORDS for v in values_at_i)

        is_param = (has_variance and none_reserved) or all_look_like_id

        if is_param:
            prev_seg = template_segments[i - 1] if i > 0 else None
            # If prev_seg was itself a param like '{user_id}', strip braces
            if prev_seg and prev_seg.startswith("{") and prev_seg.endswith("}"):
                prev_seg = prev_seg[1:-1].removesuffix("_id")

            param_name = derive_param_name(prev_seg, used_names)
            param_names.append(param_name)
            template_segments.append(f"{{{param_name}}}")
        else:
            # Consistent static segment
            first_val = next(iter(values_at_i))
            template_segments.append(first_val)

    normalized_path = "/" + "/".join(template_segments)
    return normalized_path, param_names
