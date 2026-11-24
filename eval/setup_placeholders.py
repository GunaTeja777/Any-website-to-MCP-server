"""Populate placeholder folders for eval benchmark datasets."""

from __future__ import annotations

import json
from pathlib import Path


def create_placeholders() -> None:
    base = Path(__file__).parent / "cases"
    base.mkdir(parents=True, exist_ok=True)

    template = {
        "name": "CASE_NAME_PLACEHOLDER",
        "expected_auth": "TODO_EXPECTED_AUTH (bearer_token, cookie_session, etc.)",
        "description": "PLACEHOLDER: Replace with your OpenAPI spec or endpoint list.",
        "endpoints": [
            {
                "method": "GET",
                "path": "/api/v1/resource/{id}",
                "fields": ["id", "name", "status"],
            }
        ],
    }

    har_comment = {
        "_comment": "PLACEHOLDER: Replace with actual browser network recording (.har)"
    }

    for i in range(2, 11):
        case_name = f"case_{i:02d}_placeholder"
        case_dir = base / case_name
        case_dir.mkdir(parents=True, exist_ok=True)

        gt_file = case_dir / "ground_truth.json"
        if not gt_file.exists():
            data = dict(template)
            data["name"] = case_name
            gt_file.write_text(json.dumps(data, indent=2), encoding="utf-8")

        har_file = case_dir / "recording.har"
        if not har_file.exists():
            har_file.write_text(json.dumps(har_comment, indent=2), encoding="utf-8")

    print("Created placeholders case_02 through case_10 in", base)


if __name__ == "__main__":
    create_placeholders()
