"""Deterministic JSON Schema inference, sample merging, and post-processing."""

from __future__ import annotations

import json
import re
from typing import Any

from har2mcp.cluster.models import EndpointCluster
from har2mcp.cluster.normalizer import UUID_PATTERN
from har2mcp.schema.models import EndpointSchema
from har2mcp.schema.pagination import detect_pagination

ISO8601_PATTERN: re.Pattern[str] = re.compile(
    r"^\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?)?$"
)

COMMON_ENUM_VALUES: frozenset[str] = frozenset(
    {
        "active",
        "inactive",
        "pending",
        "completed",
        "failed",
        "success",
        "error",
        "canceled",
        "cancelled",
        "open",
        "closed",
        "draft",
        "published",
        "archived",
        "admin",
        "user",
        "guest",
        "member",
        "owner",
        "read",
        "write",
        "asc",
        "desc",
        "enabled",
        "disabled",
        "public",
        "private",
        "internal",
    }
)


def calculate_confidence(sample_count: int) -> float:
    """Calculate statistical confidence score bounded in [0.0, 1.0].

    Args:
        sample_count: Number of observed requests/responses.

    Returns:
        Confidence score rounded to 2 decimal places.
    """
    if sample_count <= 0:
        return 0.0
    score = 1.0 - (0.5 / (sample_count**0.65))
    return float(round(max(0.1, min(1.0, score)), 2))


def is_iso8601(val: str) -> bool:
    """Check if string conforms to ISO8601 date or date-time."""
    return bool(ISO8601_PATTERN.match(val.strip()))


def is_epoch_timestamp(val: int | float) -> bool:
    """Check if numeric value falls into valid seconds or milliseconds epoch range."""
    # Seconds range: ~2001 to ~2048 (1_000_000_000 to 2_500_000_000)
    if 1_000_000_000 <= val <= 2_500_000_000:
        return True
    # Milliseconds range: ~2001 to ~2048 (1_000_000_000_000 to 2_500_000_000_000)
    return 1_000_000_000_000 <= val <= 2_500_000_000_000


def is_id_field(key_name: str) -> bool:
    """Check if key name implies an identifier."""
    lower = key_name.lower()
    return lower == "id" or lower.endswith("_id") or lower.endswith("id")


def infer_json_schema(
    samples: list[Any],
    key_name: str = "",
) -> dict[str, Any] | None:
    """Recursively infer a consolidated JSON Schema across observed data samples.

    Args:
        samples: List of observed Python values (dict, list, str, int, float, etc.).
        key_name: Current property key name being analyzed.

    Returns:
        Consolidated and post-processed JSON Schema dictionary, or None if no samples.
    """
    if not samples:
        return None

    total_count = len(samples)
    non_null = [s for s in samples if s is not None]
    has_null = len(non_null) < total_count

    if not non_null:
        return {"type": "null"}

    # 1. Type inference
    types_found: set[str] = set()
    for s in non_null:
        if isinstance(s, bool):
            types_found.add("boolean")
        elif isinstance(s, int):
            types_found.add("integer")
        elif isinstance(s, float):
            types_found.add("number")
        elif isinstance(s, str):
            types_found.add("string")
        elif isinstance(s, list):
            types_found.add("array")
        elif isinstance(s, dict):
            types_found.add("object")
        else:
            types_found.add("string")

    # If both integer and number are found, normalize to number
    if "integer" in types_found and "number" in types_found:
        types_found.remove("integer")

    schema: dict[str, Any] = {}

    # Assign type (or union)
    type_list = sorted(types_found)
    if has_null:
        schema["nullable"] = True
        type_list.append("null")

    schema["type"] = type_list[0] if len(type_list) == 1 else type_list

    # 2. String post-processing: Enums, Timestamps, IDs
    if "string" in types_found and len(types_found) == 1:
        str_vals = [s for s in non_null if isinstance(s, str)]
        distinct_vals = sorted(set(str_vals))

        # Check ISO timestamp
        if str_vals and all(is_iso8601(v) for v in str_vals):
            schema["format"] = "date-time"
        # Check UUID
        elif str_vals and all(UUID_PATTERN.match(v) for v in str_vals):
            schema["format"] = "uuid"
        # Check Enum
        elif str_vals and 1 <= len(distinct_vals) <= 8:
            all_short = all(len(v) <= 40 and not is_iso8601(v) for v in distinct_vals)
            has_enum_signals = any(
                v.lower() in COMMON_ENUM_VALUES for v in distinct_vals
            ) or (len(non_null) >= 2 and len(distinct_vals) < len(non_null))
            if all_short and has_enum_signals:
                schema["enum"] = distinct_vals

        if is_id_field(key_name):
            schema["x-is-id"] = True

    # 3. Numeric post-processing: Epoch timestamps & IDs
    if ("integer" in types_found or "number" in types_found) and len(types_found) == 1:
        num_vals = [s for s in non_null if isinstance(s, (int, float))]
        if num_vals and all(is_epoch_timestamp(v) for v in num_vals):
            schema["format"] = "epoch"
        if is_id_field(key_name):
            schema["x-is-id"] = True

    # 4. Array post-processing: nested item inference
    if "array" in types_found and len(types_found) == 1:
        all_items: list[Any] = []
        for lst in non_null:
            if isinstance(lst, list):
                all_items.extend(lst)

        if all_items:
            item_schema = infer_json_schema(all_items, key_name=key_name)
            schema["items"] = item_schema or {}
        else:
            schema["items"] = {}

    # 5. Object post-processing: properties and required fields
    if "object" in types_found and len(types_found) == 1:
        dict_samples = [s for s in non_null if isinstance(s, dict)]
        all_keys: set[str] = set()
        for d in dict_samples:
            all_keys.update(d.keys())

        properties: dict[str, Any] = {}
        required: list[str] = []

        for k in sorted(all_keys):
            field_values: list[Any] = []
            present_in_samples = 0
            for d in dict_samples:
                if k in d:
                    present_in_samples += 1
                    field_values.append(d[k])

            # Marked required ONLY if present in all valid object samples
            if present_in_samples == len(dict_samples):
                required.append(k)

            prop_schema = infer_json_schema(field_values, key_name=k)
            properties[k] = prop_schema or {}

        schema["properties"] = properties
        if required:
            schema["required"] = required

    return schema


def infer_path_params_schema(path_params: list[str]) -> dict[str, Any]:
    """Generate JSON Schema for path variables."""
    if not path_params:
        return {"type": "object", "properties": {}, "required": []}

    properties: dict[str, Any] = {}
    for p in path_params:
        prop_def: dict[str, Any] = {"type": "string"}
        if is_id_field(p):
            prop_def["x-is-id"] = True
        properties[p] = prop_def

    return {
        "type": "object",
        "properties": properties,
        "required": list(path_params),
    }


def infer_query_params_schema(cluster: EndpointCluster) -> dict[str, Any]:
    """Generate JSON Schema for observed query parameters."""
    properties: dict[str, Any] = {}
    required: list[str] = []

    # Map all observed query values
    observed_values: dict[str, list[str]] = {}
    for req in cluster.sample_requests:
        for q in req.query_string:
            observed_values.setdefault(q.name, []).append(q.value)

    all_keys = sorted(
        set(cluster.observed_query_params.stable.keys())
        | set(cluster.observed_query_params.variable)
        | set(cluster.observed_query_params.optional)
    )

    for key in all_keys:
        vals = observed_values.get(key, [])
        is_optional = key in cluster.observed_query_params.optional
        if not is_optional:
            required.append(key)

        prop: dict[str, Any] = {"type": "string"}
        if vals:
            if all(v.isdigit() for v in vals):
                prop["type"] = "integer"
            elif all(v.lower() in {"true", "false"} for v in vals):
                prop["type"] = "boolean"

        if key in cluster.observed_query_params.stable:
            prop["default"] = cluster.observed_query_params.stable[key]

        properties[key] = prop

    return {
        "type": "object",
        "properties": properties,
        "required": required,
    }


def _parse_payload(text: str | None) -> Any:
    """Parse JSON payload or return None."""
    if not text:
        return None
    trimmed = text.strip()
    if (trimmed.startswith("{") and trimmed.endswith("}")) or (
        trimmed.startswith("[") and trimmed.endswith("]")
    ):
        try:
            return json.loads(trimmed)
        except Exception:
            return None
    return None


def infer_endpoint_schema(cluster: EndpointCluster) -> EndpointSchema:
    """Synthesize complete request/response schemas, confidence, and pagination.

    Args:
        cluster: Input endpoint cluster.

    Returns:
        Validated EndpointSchema model.
    """
    endpoint_id = f"{cluster.method}_{cluster.path_template}"

    # 1. Parse sample request bodies
    req_samples = [
        _parse_payload(r.post_data.text)
        for r in cluster.sample_requests
        if r.post_data and r.post_data.text
    ]
    valid_req_samples = [s for s in req_samples if s is not None]
    req_body_schema = (
        infer_json_schema(valid_req_samples) if valid_req_samples else None
    )

    # 2. Parse sample response bodies
    res_samples = [
        _parse_payload(r.content.text)
        for r in cluster.sample_responses
        if r.content and r.content.text
    ]
    valid_res_samples = [s for s in res_samples if s is not None]
    res_body_schema = (
        infer_json_schema(valid_res_samples) if valid_res_samples else None
    )

    # 3. Infer path and query schemas
    path_schema = infer_path_params_schema(cluster.path_params)
    query_schema = infer_query_params_schema(cluster)

    # 4. Detect pagination & score confidence
    pagination_info = detect_pagination(cluster)
    confidence = calculate_confidence(cluster.call_count)

    return EndpointSchema(
        endpoint_id=endpoint_id,
        method=cluster.method,
        path_template=cluster.path_template,
        sample_count=cluster.call_count,
        confidence_score=confidence,
        path_params_schema=path_schema,
        query_params_schema=query_schema,
        request_body_schema=req_body_schema,
        response_body_schema=res_body_schema,
        pagination=pagination_info,
    )


def infer_schemas(clusters: list[EndpointCluster]) -> list[EndpointSchema]:
    """Infer schemas for an entire collection of endpoint clusters.

    Args:
        clusters: List of endpoint clusters.

    Returns:
        List of inferred EndpointSchema models.
    """
    return [infer_endpoint_schema(c) for c in clusters]
