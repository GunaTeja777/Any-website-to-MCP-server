"""Structural schema-level comparator matching tool outputs to recordings."""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


def extract_payload(raw_output: Any) -> Any:
    """Extract and parse meaningful payload from raw tool output or CallToolResult."""
    if raw_output is None:
        return None

    # Handle CallToolResult from MCP client
    if hasattr(raw_output, "content") and isinstance(raw_output.content, list):
        if getattr(raw_output, "isError", False):
            err_texts = [getattr(c, "text", str(c)) for c in raw_output.content]
            raise RuntimeError(f"Tool invocation returned error: {' '.join(err_texts)}")

        if not raw_output.content:
            return None

        first_block = raw_output.content[0]
        text_val = getattr(first_block, "text", str(first_block))
        return _try_parse_json(text_val)

    if isinstance(raw_output, str):
        return _try_parse_json(raw_output)

    return raw_output


def _try_parse_json(text: str) -> Any:
    """Attempt JSON parse; fall back to stripped string."""
    cleaned = text.strip()
    if (cleaned.startswith("{") and cleaned.endswith("}")) or (
        cleaned.startswith("[") and cleaned.endswith("]")
    ):
        try:
            return json.loads(cleaned)
        except Exception:
            return cleaned
    return cleaned


def compare_structure(expected: Any, actual: Any) -> tuple[bool, str | None, list[str]]:
    """Compare tool output structure to expected recorded data at schema level.

    Args:
        expected: Expected data from HAR recording (parsed dict/list or primitive).
        actual: Received data from tool execution.

    Returns:
        Tuple of (is_match, error_message, missing_keys).
    """
    if expected is None:
        # None expected matches None or empty
        return True, None, []

    if actual is None:
        return False, "Tool returned None/empty, but data structure was expected.", []

    # Numeric compatibility (int vs float)
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        # Avoid treating bool as int
        if isinstance(expected, bool) == isinstance(actual, bool):
            return True, None, []
        exp_name = type(expected).__name__
        act_name = type(actual).__name__
        return (
            False,
            f"Type mismatch: expected {exp_name}, got {act_name}",
            [],
        )

    # Boolean compatibility
    if isinstance(expected, bool):
        if isinstance(actual, bool):
            return True, None, []
        return False, f"Type mismatch: expected bool, got {type(actual).__name__}", []

    # String compatibility
    if isinstance(expected, str):
        if isinstance(actual, str):
            return True, None, []
        return False, f"Type mismatch: expected str, got {type(actual).__name__}", []

    # Dictionary / JSON Object comparison
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            act_name = type(actual).__name__
            return (
                False,
                f"Type mismatch: expected JSON object (dict), got {act_name}",
                [],
            )

        missing_keys: list[str] = [k for k in expected if k not in actual]
        if missing_keys:
            return (
                False,
                f"Missing expected keys: {', '.join(missing_keys)}",
                missing_keys,
            )

        # Recursively check sub-structures for matching keys
        for k in expected:
            sub_expected = expected[k]
            sub_actual = actual.get(k)
            if sub_expected is not None and sub_actual is not None:
                sub_match, sub_err, sub_missing = compare_structure(
                    sub_expected, sub_actual
                )
                if not sub_match:
                    return (
                        False,
                        f"Field '{k}' structural mismatch: {sub_err}",
                        sub_missing,
                    )

        return True, None, []

    # List / Array comparison
    if isinstance(expected, list):
        if not isinstance(actual, list):
            return (
                False,
                f"Type mismatch: expected list/array, got {type(actual).__name__}",
                [],
            )

        if expected and actual:
            # Compare first element structure as representative sample
            item_match, item_err, item_missing = compare_structure(
                expected[0], actual[0]
            )
            if not item_match:
                return (
                    False,
                    f"Array item structural mismatch: {item_err}",
                    item_missing,
                )

        return True, None, []

    # Fallback to general type equality
    if type(expected) is type(actual):
        return True, None, []

    exp_name = type(expected).__name__
    act_name = type(actual).__name__
    return (
        False,
        f"Structural mismatch: expected {exp_name}, got {act_name}",
        [],
    )
