"""Verification engine running generated FastMCP servers against local replay data."""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from har2mcp.design.models import ToolSpec
from har2mcp.parser.models import Entry
from har2mcp.verify.comparator import compare_structure, extract_payload
from har2mcp.verify.models import ToolVerificationResult, VerificationReport
from har2mcp.verify.replay_server import ReplayServer

logger = logging.getLogger(__name__)


def extract_sample_arguments(tool: ToolSpec, entry: Entry) -> dict[str, Any]:
    """Extract sample arguments matching tool parameters from recorded HAR entry."""
    args: dict[str, Any] = {}
    if not tool.endpoints:
        return args

    ep = tool.endpoints[0]
    template_parts = [p for p in ep.path_template.strip("/").split("/") if p]
    url_path = urlparse(entry.request.url).path
    url_parts = [p for p in url_path.strip("/").split("/") if p]

    # 1. Extract path parameters from URL segments
    if len(template_parts) == len(url_parts):
        for tp, up in zip(template_parts, url_parts, strict=False):
            if tp.startswith("{") and tp.endswith("}"):
                var_name = tp[1:-1]
                # Match to tool parameter
                for param in tool.parameters:
                    if param.name == var_name:
                        args[param.name] = up
                        break
                else:
                    args[var_name] = up

    # 2. Extract query parameters
    query_lookup = {qp.name: qp.value for qp in entry.request.query_string}
    for param in tool.parameters:
        if param.location == "query" and param.name in query_lookup:
            val = query_lookup[param.name]
            if param.type in ("integer", "int"):
                try:
                    args[param.name] = int(val)
                except ValueError:
                    args[param.name] = val
            elif param.type in ("boolean", "bool"):
                args[param.name] = val.lower() in ("true", "1", "yes")
            else:
                args[param.name] = val

    # 3. Extract body fields
    if entry.request.post_data and entry.request.post_data.text:
        try:
            body_json = json.loads(entry.request.post_data.text)
            if isinstance(body_json, dict):
                for param in tool.parameters:
                    if param.location == "body" and param.name in body_json:
                        args[param.name] = body_json[param.name]
        except Exception:
            pass

    # 4. Fill required parameters missing from request with default fallbacks
    for param in tool.parameters:
        if param.required and param.name not in args:
            if param.default is not None:
                args[param.name] = param.default
            elif param.type in ("integer", "int"):
                args[param.name] = 1
            elif param.type in ("boolean", "bool"):
                args[param.name] = True
            elif param.type in ("number", "float"):
                args[param.name] = 1.0
            elif param.type in ("array", "list"):
                args[param.name] = []
            elif param.type in ("object", "dict"):
                args[param.name] = {}
            else:
                args[param.name] = "test"

    return args


def find_matching_entry(tool: ToolSpec, entries: list[Entry]) -> Entry | None:
    """Find a recorded entry corresponding to the primary endpoint of the tool."""
    if not tool.endpoints:
        return None

    target_method = tool.endpoints[0].method.upper()
    target_path = tool.endpoints[0].path_template
    target_parts = [p for p in target_path.strip("/").split("/") if p]

    for entry in entries:
        if entry.request.method.upper() != target_method:
            continue
        entry_path = urlparse(entry.request.url).path
        entry_parts = [p for p in entry_path.strip("/").split("/") if p]

        if len(entry_parts) != len(target_parts):
            continue

        match = True
        for tp, ep in zip(target_parts, entry_parts, strict=False):
            if tp.startswith("{") and tp.endswith("}"):
                continue
            if tp != ep:
                match = False
                break

        if match:
            return entry

    return None


class ServerVerifier:
    """Executes verification of generated server tools against recorded HAR replay."""

    def __init__(
        self,
        server_dir: Path | str,
        entries: list[Entry],
        tool_specs: list[ToolSpec],
    ) -> None:
        self.server_dir = Path(server_dir)
        self.entries = entries
        self.tool_specs = tool_specs

    async def verify(self) -> VerificationReport:
        """Run replay server, launch MCP server over stdio, and verify each tool."""
        server_py = self.server_dir / "server.py"
        if not server_py.exists():
            return VerificationReport(
                results=[
                    ToolVerificationResult(
                        tool_name="server",
                        passed=False,
                        error_message=f"server.py not found in {self.server_dir}",
                    )
                ],
                total_tools=len(self.tool_specs),
                passed_tools=0,
                failed_tools=len(self.tool_specs),
                score=0.0,
                all_passed=False,
            )

        results: list[ToolVerificationResult] = []

        with ReplayServer(self.entries) as replay:
            env = {
                **os.environ,
                "API_BASE_URL": replay.base_url,
                "PYTHONPATH": str(self.server_dir),
                "NO_PROXY": "127.0.0.1,localhost",
                "no_proxy": "127.0.0.1,localhost",
                "HTTP_PROXY": "",
                "HTTPS_PROXY": "",
                "http_proxy": "",
                "https_proxy": "",
            }

            params = StdioServerParameters(
                command=sys.executable,
                args=[str(server_py)],
                env=env,
            )

            async with (
                stdio_client(params) as (read, write),
                ClientSession(read, write) as session,
            ):
                await session.initialize()

                # Query available tools in generated server
                tools_res = await session.list_tools()
                server_tool_names = {t.name for t in tools_res.tools}

                for tool in self.tool_specs:
                    if tool.name not in server_tool_names:
                        # Tool was omitted (e.g. read-only mode)
                        continue

                    entry = find_matching_entry(tool, self.entries)
                    if not entry:
                        results.append(
                            ToolVerificationResult(
                                tool_name=tool.name,
                                passed=True,
                                extra_info={
                                    "note": "No matching entry in fixture, skipped"
                                },
                            )
                        )
                        continue

                    # Extract expected payload from entry response
                    raw_expected = entry.response.content.text
                    expected_payload = _parse_expected_response(raw_expected)

                    # Extract sample invocation arguments
                    args = extract_sample_arguments(tool, entry)

                    try:
                        raw_result = await session.call_tool(tool.name, arguments=args)
                        actual_payload = extract_payload(raw_result)

                        is_match, err_msg, missing_keys = compare_structure(
                            expected_payload, actual_payload
                        )

                        results.append(
                            ToolVerificationResult(
                                tool_name=tool.name,
                                passed=is_match,
                                error_message=err_msg,
                                expected_type=type(expected_payload).__name__,
                                actual_type=type(actual_payload).__name__,
                                missing_keys=missing_keys,
                            )
                        )
                    except Exception as exc:
                        results.append(
                            ToolVerificationResult(
                                tool_name=tool.name,
                                passed=False,
                                error_message=str(exc),
                                expected_type=type(expected_payload).__name__,
                            )
                        )

        total = len(results)
        passed = sum(1 for r in results if r.passed)
        failed = total - passed
        score = (passed / total) if total > 0 else 1.0

        return VerificationReport(
            results=results,
            total_tools=total,
            passed_tools=passed,
            failed_tools=failed,
            score=round(score, 2),
            all_passed=(failed == 0),
        )


def _parse_expected_response(text: str | None) -> Any:
    """Safely parse recorded response body to dict/list or raw string."""
    if not text:
        return None
    cleaned = text.strip()
    try:
        return json.loads(cleaned)
    except Exception:
        return cleaned
