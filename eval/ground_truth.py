"""Parsers for loading ground-truth specifications and OpenAPI documents."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from eval.models import ExpectedEndpoint, GroundTruth

logger = logging.getLogger(__name__)


def parse_openapi_spec(data: dict[str, Any], name: str = "openapi_spec") -> GroundTruth:
    """Extract expected endpoints and fields from an OpenAPI 3.x or Swagger 2.0 dict."""
    expected_endpoints: list[ExpectedEndpoint] = []
    paths: dict[str, Any] = data.get("paths", {})

    for path, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue

        for method in ("get", "post", "put", "delete", "patch", "head", "options"):
            if method not in path_item or not isinstance(path_item[method], dict):
                continue

            op = path_item[method]
            field_names: set[str] = set()

            # 1. Query and path parameters
            for param in op.get("parameters", []):
                if isinstance(param, dict) and "name" in param:
                    field_names.add(param["name"])

            # 2. Request body schema fields
            req_body = op.get("requestBody", {})
            if isinstance(req_body, dict):
                content = req_body.get("content", {})
                for media in content.values():
                    schema = media.get("schema", {})
                    _collect_schema_properties(schema, field_names)

            # 3. Response schema fields (200, 201, default)
            responses = op.get("responses", {})
            for code, resp in responses.items():
                if str(code).startswith("2") and isinstance(resp, dict):
                    content = resp.get("content", {})
                    for media in content.values():
                        schema = media.get("schema", {})
                        _collect_schema_properties(schema, field_names)

            expected_endpoints.append(
                ExpectedEndpoint(
                    method=method.upper(),
                    path=path,
                    fields=sorted(field_names),
                )
            )

    # Inferred auth from security schemes
    detected_auth: str | None = None
    components = data.get("components", {})
    security_schemes = components.get("securitySchemes", {}) or data.get(
        "securityDefinitions", {}
    )
    for scheme in security_schemes.values():
        if isinstance(scheme, dict):
            stype = scheme.get("type", "").lower()
            if stype == "http" and scheme.get("scheme", "").lower() == "bearer":
                detected_auth = "bearer_token"
                break
            if stype == "apikey":
                loc = scheme.get("in", "").lower()
                detected_auth = "api_key_query" if loc == "query" else "api_key_header"
                break

    return GroundTruth(
        name=name,
        expected_auth=detected_auth,
        endpoints=expected_endpoints,
        description=data.get("info", {}).get("description", ""),
    )


def _collect_schema_properties(
    schema: dict[str, Any], fields: set[str], seen: set[int] | None = None
) -> None:
    """Recursively collect property keys from an OpenAPI JSON Schema object."""
    if seen is None:
        seen = set()
    schema_id = id(schema)
    if schema_id in seen:
        return
    seen.add(schema_id)

    props = schema.get("properties")
    if isinstance(props, dict):
        for k, sub_schema in props.items():
            fields.add(k)
            if isinstance(sub_schema, dict):
                _collect_schema_properties(sub_schema, fields, seen)

    items = schema.get("items")
    if isinstance(items, dict):
        _collect_schema_properties(items, fields, seen)

    for combiner in ("allOf", "anyOf", "oneOf"):
        options = schema.get(combiner)
        if isinstance(options, list):
            for opt in options:
                if isinstance(opt, dict):
                    _collect_schema_properties(opt, fields, seen)


def load_ground_truth(case_dir: Path | str) -> GroundTruth | None:
    """Load ground truth from ground_truth.json or openapi.json in test case folder."""
    directory = Path(case_dir)
    if not directory.exists() or not directory.is_dir():
        return None

    gt_file = directory / "ground_truth.json"
    if gt_file.exists():
        try:
            content = gt_file.read_text(encoding="utf-8")
            data = json.loads(content)
            # Check if this is an OpenAPI document saved as ground_truth.json
            if "openapi" in data or "swagger" in data:
                return parse_openapi_spec(data, name=directory.name)
            return GroundTruth.model_validate(data)
        except Exception as exc:
            logger.error("Failed to parse %s: %s", gt_file, exc)
            return None

    openapi_file = directory / "openapi.json"
    if openapi_file.exists():
        try:
            content = openapi_file.read_text(encoding="utf-8")
            data = json.loads(content)
            return parse_openapi_spec(data, name=directory.name)
        except Exception as exc:
            logger.error("Failed to parse %s: %s", openapi_file, exc)
            return None

    return None
