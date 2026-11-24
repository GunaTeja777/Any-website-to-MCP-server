"""Automated repair loop using LLM to patch failing tool implementations."""

from __future__ import annotations

import contextlib
import json
import logging
import re
import shutil
import subprocess
import sys
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from har2mcp.design.models import ToolSpec
from har2mcp.llm.client import BaseLLMClient, LLMMessage, LLMRole
from har2mcp.parser.models import Entry
from har2mcp.redact.models import SecretMetadata
from har2mcp.verify.models import RepairIteration, RepairReport
from har2mcp.verify.verifier import (
    ServerVerifier,
    _parse_expected_response,
    find_matching_entry,
)

logger = logging.getLogger(__name__)

SYSTEM_REPAIR_PROMPT = """You are an expert engineer specializing in FastMCP and httpx.
Your job is to repair a failing MCP tool function.

Rules:
1. Return ONLY valid JSON matching this schema:
   {
     "tool_name": "<tool_name>",
     "repaired_code": "<full python code of the repaired @mcp.tool() function>",
     "explanation": "<brief summary of the fix>"
   }
2. The repaired code must start with `@mcp.tool()` and define the async function.
3. Keep the same function name and parameter names.
4. Ensure path variables, query params, or body payload are properly dispatched.
5. Do NOT include any unredacted secrets or credentials.
"""


class RepairedToolResponse(BaseModel):
    """Schema for LLM proposed tool repair patch."""

    model_config = ConfigDict(frozen=True)

    tool_name: str = Field(..., description="Name of repaired tool.")
    repaired_code: str = Field(..., description="Complete replacement function code.")
    explanation: str = Field(default="", description="Summary of applied fix.")


def redact_text_for_llm(
    text: str, redaction_map: dict[str, SecretMetadata] | None = None
) -> str:
    """Ensure no raw secret or sensitive value reaches the LLM during repair."""
    sanitized = text
    if redaction_map:
        for secret_meta in redaction_map.values():
            if secret_meta.original_value and len(secret_meta.original_value) > 3:
                sanitized = sanitized.replace(
                    secret_meta.original_value, secret_meta.placeholder
                )

    # Secondary regex scrub for common tokens / keys
    sanitized = re.sub(
        r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}",
        "<REDACTED_JWT>",
        sanitized,
    )
    return sanitized


def extract_tool_code(server_code: str, tool_name: str) -> tuple[str | None, int, int]:
    """Locate and extract the function definition of a specific tool from server.py."""
    escaped_name = re.escape(tool_name)
    pat_str = (
        rf"(@mcp\.tool\(\)\s*async\s+def\s+{escaped_name}\s*\(.*?\n)"
        r"(?=@mcp\.tool|if __name__|$)"
    )
    pattern = re.compile(pat_str, re.DOTALL)
    match = pattern.search(server_code)
    if not match:
        return None, -1, -1
    return match.group(1).rstrip(), match.start(1), match.end(1)


class RepairLoop:
    """Manages the verification-repair-reverification cycle for MCP servers."""

    def __init__(
        self,
        server_dir: Path | str,
        entries: list[Entry],
        tool_specs: list[ToolSpec],
        llm_client: BaseLLMClient,
        redaction_map: dict[str, SecretMetadata] | None = None,
        max_iterations: int = 3,
    ) -> None:
        self.server_dir = Path(server_dir)
        self.entries = entries
        self.tool_specs = tool_specs
        self.llm_client = llm_client
        self.redaction_map = redaction_map
        self.max_iterations = max_iterations

    async def run(self) -> RepairReport:
        """Execute verification and up to max_iterations repair cycles."""
        verifier = ServerVerifier(self.server_dir, self.entries, self.tool_specs)

        # 1. Initial verification
        initial_report = await verifier.verify()
        if initial_report.all_passed:
            logger.info("All tools passed initial verification. No repairs needed.")
            return RepairReport(
                initial_report=initial_report,
                final_report=initial_report,
                iterations=[],
                repaired=True,
            )

        current_report = initial_report
        iterations_log: list[RepairIteration] = []
        server_py = self.server_dir / "server.py"

        for iter_num in range(1, self.max_iterations + 1):
            failing_tools = [r for r in current_report.results if not r.passed]
            if not failing_tools:
                logger.info("All tools passing after iteration %d.", iter_num - 1)
                break

            logger.info(
                "Repair iteration %d/%d: %d failing tools",
                iter_num,
                self.max_iterations,
                len(failing_tools),
            )

            # Repair the first failing tool
            target_failure = failing_tools[0]
            target_name = target_failure.tool_name

            target_tool_spec = next(
                (t for t in self.tool_specs if t.name == target_name), None
            )
            if not target_tool_spec:
                continue

            matching_entry = find_matching_entry(target_tool_spec, self.entries)
            expected_obj = (
                _parse_expected_response(matching_entry.response.content.text)
                if matching_entry
                else None
            )

            server_code = server_py.read_text(encoding="utf-8")
            tool_code, start_idx, end_idx = extract_tool_code(server_code, target_name)
            if not tool_code:
                logger.warning("Could not extract tool code for %s", target_name)
                continue

            # Redact prompt contents
            safe_tool_code = redact_text_for_llm(tool_code, self.redaction_map)
            safe_expected = redact_text_for_llm(
                json.dumps(expected_obj, indent=2) if expected_obj else "null",
                self.redaction_map,
            )
            safe_error = redact_text_for_llm(
                target_failure.error_message or "Unknown error", self.redaction_map
            )

            user_prompt = (
                f"The MCP tool '{target_name}' failed verification during testing.\n\n"
                f"Current Tool Code:\n```python\n{safe_tool_code}\n```\n\n"
                f"Expected Output Structure:\n```json\n{safe_expected}\n```\n\n"
                f"Verification Error / Discrepancy:\n{safe_error}\n\n"
                "Please fix the tool code so that it correctly calls the API "
                "and matches the expected output."
            )

            try:
                response = self.llm_client.complete(
                    system=SYSTEM_REPAIR_PROMPT,
                    messages=[LLMMessage(role=LLMRole.USER, content=user_prompt)],
                    json_schema=RepairedToolResponse.model_json_schema(),
                )

                parsed_json = json.loads(response.content)
                repair_patch = RepairedToolResponse.model_validate(parsed_json)

                # Apply patch to server.py
                new_server_code = (
                    server_code[:start_idx]
                    + repair_patch.repaired_code
                    + "\n\n"
                    + server_code[end_idx:].lstrip()
                )
                server_py.write_text(new_server_code, encoding="utf-8")
                self._format_server_file(server_py)

                # Re-verify
                post_report = await verifier.verify()
                tool_now_passed = any(
                    r.tool_name == target_name and r.passed for r in post_report.results
                )

                iterations_log.append(
                    RepairIteration(
                        iteration=iter_num,
                        tool_name=target_name,
                        failing_error=safe_error,
                        patch_applied=True,
                        passed_after_patch=tool_now_passed,
                        patch_summary=repair_patch.explanation,
                    )
                )

                current_report = post_report
                if current_report.all_passed:
                    break

            except Exception as exc:
                logger.error("Error during repair iteration %d: %s", iter_num, exc)
                iterations_log.append(
                    RepairIteration(
                        iteration=iter_num,
                        tool_name=target_name,
                        failing_error=safe_error,
                        patch_applied=False,
                        passed_after_patch=False,
                        patch_summary=f"Repair failed with exception: {exc}",
                    )
                )

        return RepairReport(
            initial_report=initial_report,
            final_report=current_report,
            iterations=iterations_log,
            repaired=current_report.all_passed,
        )

    def _format_server_file(self, server_py: Path) -> None:
        """Format server.py with ruff if available."""
        ruff_bin = shutil.which("ruff")
        cmd = [ruff_bin or sys.executable, "format", str(server_py)]
        if not ruff_bin:
            cmd = [sys.executable, "-m", "ruff", "format", str(server_py)]
        with contextlib.suppress(Exception):
            subprocess.run(cmd, check=False, capture_output=True, timeout=5)
