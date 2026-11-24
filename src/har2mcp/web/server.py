"""Lightweight local web server for the HAR2MCP interactive studio interface."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
import time
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from har2mcp.auth.detector import detect_auth_spec
from har2mcp.cluster.engine import cluster_entries
from har2mcp.codegen.generator import generate_server_project
from har2mcp.design.engine import ToolDesigner, deterministic_design_tools
from har2mcp.llm.client import get_default_llm_client
from har2mcp.parser.loader import parse_har
from har2mcp.redact.engine import redact
from har2mcp.schema.inferrer import infer_schemas
from har2mcp.verify.verifier import ServerVerifier

logger = logging.getLogger("har2mcp.web")

REPO_ROOT = Path(__file__).resolve().parents[3]
STATIC_DIR = Path(__file__).resolve().parent / "static"


def get_available_samples() -> list[dict[str, Any]]:
    """List preloaded demonstration HAR recordings."""
    samples = [
        {
            "id": "synthetic_cloud",
            "name": "Cloud Infrastructure API",
            "description": "Simulated multi-node compute cloud dashboard with metrics and quotas.",
            "path": str(
                REPO_ROOT
                / "eval"
                / "cases"
                / "case_01_synthetic_cloud"
                / "recording.har"
            ),
            "auth_type": "Bearer Token",
            "icon": "cloud",
        },
        {
            "id": "synthetic_secrets",
            "name": "Authenticated Session with Sensitive Credentials",
            "description": "Simulated web app with JWTs, CSRF tokens, session cookies, and API keys.",
            "path": str(REPO_ROOT / "fixtures" / "synthetic_api_with_secrets.har"),
            "auth_type": "Session Cookie & CSRF",
            "icon": "shield",
        },
        {
            "id": "synthetic_mixed",
            "name": "E-Commerce App with Static Noise",
            "description": "Real-world web traffic containing images, CSS, analytics, and JSON APIs.",
            "path": str(REPO_ROOT / "fixtures" / "synthetic_mixed_traffic.har"),
            "auth_type": "API Key Query Param",
            "icon": "shopping-bag",
        },
    ]
    return samples


class StudioRequestHandler(BaseHTTPRequestHandler):
    """HTTP request handler for HAR2MCP web interface."""

    def _send_cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(HTTPStatus.NO_CONTENT)
        self._send_cors_headers()
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        """Handle GET requests for static assets and API endpoints."""
        url_path = self.path.split("?")[0]

        if url_path in {"/", "/index.html"}:
            index_file = STATIC_DIR / "index.html"
            if not index_file.exists():
                self.send_error(
                    HTTPStatus.NOT_FOUND, "Web studio template index.html not found."
                )
                return
            content = index_file.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self._send_cors_headers()
            self.end_headers()
            self.wfile.write(content)
            return

        if url_path == "/api/samples":
            samples = get_available_samples()
            data = json.dumps({"samples": samples}).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self._send_cors_headers()
            self.end_headers()
            self.wfile.write(data)
            return

        if url_path == "/api/health":
            data = json.dumps(
                {
                    "status": "ok",
                    "gemini_enabled": bool(os.environ.get("GEMINI_API_KEY")),
                }
            ).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self._send_cors_headers()
            self.end_headers()
            self.wfile.write(data)
            return

        self.send_error(HTTPStatus.NOT_FOUND, "Resource not found")

    def do_POST(self) -> None:  # noqa: N802
        """Handle POST requests for MCP server generation and tool execution."""
        url_path = self.path.split("?")[0]

        content_length = int(self.headers.get("Content-Length", 0))
        post_body = self.rfile.read(content_length)

        try:
            req_data = json.loads(post_body.decode("utf-8")) if post_body else {}
        except json.JSONDecodeError:
            self._send_json_error(HTTPStatus.BAD_REQUEST, "Invalid JSON payload")
            return

        if url_path == "/api/generate":
            self._handle_generate(req_data)
            return

        self.send_error(HTTPStatus.NOT_FOUND, "Endpoint not found")

    def _send_json_error(self, code: int, message: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self._send_cors_headers()
        self.end_headers()
        self.wfile.write(json.dumps({"error": message}).encode("utf-8"))

    def _handle_generate(self, data: dict[str, Any]) -> None:
        """Run the complete HAR2MCP pipeline on selected or uploaded HAR."""
        start_time = time.time()
        sample_id = data.get("sample_id")
        raw_har = data.get("har_content")
        allow_writes = bool(data.get("allow_writes", False))
        use_llm = bool(data.get("use_llm", True))

        temp_har_path: Path | None = None
        har_path: Path | None = None

        if sample_id:
            available = {s["id"]: Path(s["path"]) for s in get_available_samples()}
            har_path = available.get(sample_id)
            if not har_path or not har_path.exists():
                self._send_json_error(
                    HTTPStatus.BAD_REQUEST, f"Unknown sample ID: {sample_id}"
                )
                return
        elif raw_har:
            try:
                # Save raw HAR to temp file
                tmp = tempfile.NamedTemporaryFile(
                    delete=False, suffix=".har", mode="w", encoding="utf-8"
                )
                tmp.write(raw_har if isinstance(raw_har, str) else json.dumps(raw_har))
                tmp.close()
                temp_har_path = Path(tmp.name)
                har_path = temp_har_path
            except Exception as e:
                self._send_json_error(
                    HTTPStatus.BAD_REQUEST, f"Failed to parse uploaded HAR: {e}"
                )
                return
        else:
            self._send_json_error(
                HTTPStatus.BAD_REQUEST,
                "Either 'sample_id' or 'har_content' is required.",
            )
            return

        try:
            steps: list[dict[str, Any]] = []

            # Stage 1: Parse
            t0 = time.time()
            entries = parse_har(har_path)
            steps.append(
                {
                    "stage": "1. Parser & Noise Filter",
                    "status": "success",
                    "duration_ms": round((time.time() - t0) * 1000, 1),
                    "summary": f"Retained {len(entries)} API-like requests; filtered non-API assets and tracking.",
                }
            )

            # Stage 2: Redact
            t0 = time.time()
            redact_result = redact(entries)
            n_secrets = len(redact_result.placeholder_map)
            steps.append(
                {
                    "stage": "2. Zero-Secret Redactor",
                    "status": "success",
                    "duration_ms": round((time.time() - t0) * 1000, 1),
                    "summary": f"Scrubbed {n_secrets} sensitive credentials; replaced with deterministic tokens.",
                }
            )

            # Stage 3: Cluster
            t0 = time.time()
            clusters = cluster_entries(redact_result.entries)
            steps.append(
                {
                    "stage": "3. Endpoint Clustering",
                    "status": "success",
                    "duration_ms": round((time.time() - t0) * 1000, 1),
                    "summary": f"Identified {len(clusters)} distinct route templates with normalized path IDs.",
                }
            )

            # Stage 4: Schema
            t0 = time.time()
            schemas = infer_schemas(clusters)
            has_pagination = any(s.pagination for s in schemas)
            steps.append(
                {
                    "stage": "4. Schema Inferrer",
                    "status": "success",
                    "duration_ms": round((time.time() - t0) * 1000, 1),
                    "summary": f"Synthesized JSON schemas for {len(schemas)} endpoints (pagination: {has_pagination}).",
                }
            )

            # Stage 5: Auth
            t0 = time.time()
            auth_spec = detect_auth_spec(redact_result.entries)
            primary_env = (
                auth_spec.credentials[0].env_var_name
                if auth_spec.credentials
                else "HAR2MCP_AUTH_TOKEN"
            )
            steps.append(
                {
                    "stage": "5. Auth Provenance Detector",
                    "status": "success",
                    "duration_ms": round((time.time() - t0) * 1000, 1),
                    "summary": f"Detected auth: '{auth_spec.primary_type.value}', injected via '{primary_env}'.",
                }
            )

            # Stage 6: Tool Design
            t0 = time.time()
            has_api_key = bool(
                os.environ.get("GEMINI_API_KEY")
                or os.environ.get("GOOGLE_API_KEY")
                or os.environ.get("OPENAI_API_KEY")
            )
            designer_type = (
                "Gemini 2.5 Flash"
                if os.environ.get("GEMINI_API_KEY")
                else "Deterministic"
            )
            if use_llm and has_api_key:
                try:
                    llm = get_default_llm_client()
                    designer = ToolDesigner(llm_client=llm)
                    design_spec = designer.design(clusters, schemas)
                except Exception as llm_err:
                    logger.warning(
                        "LLM design failed, falling back to deterministic: %s", llm_err
                    )
                    design_spec = deterministic_design_tools(clusters, schemas)
                    designer_type = "Deterministic (Fallback)"
            else:
                design_spec = deterministic_design_tools(clusters, schemas)
                designer_type = "Deterministic Synthesis"

            steps.append(
                {
                    "stage": f"6. Tool Designer ({designer_type})",
                    "status": "success",
                    "duration_ms": round((time.time() - t0) * 1000, 1),
                    "summary": f"Designed {len(design_spec.tools)} tools for '{design_spec.server_name}'.",
                }
            )

            # Stage 7: Codegen into temp project directory
            t0 = time.time()
            with tempfile.TemporaryDirectory() as out_temp_dir:
                out_path = Path(out_temp_dir)
                generate_server_project(
                    tool_specs=design_spec.tools,
                    auth_spec=auth_spec,
                    out_dir=out_path,
                    server_name=design_spec.server_name or "mcp_api_server",
                    allow_writes=allow_writes,
                    has_pagination=has_pagination,
                )
                steps.append(
                    {
                        "stage": "7. FastMCP Codegen",
                        "status": "success",
                        "duration_ms": round((time.time() - t0) * 1000, 1),
                        "summary": "Rendered complete package: server.py, pyproject.toml, Dockerfile, README.md.",
                    }
                )

                # Read rendered file contents
                files: dict[str, str] = {}
                for f_path in out_path.iterdir():
                    if f_path.is_file():
                        files[f_path.name] = f_path.read_text(encoding="utf-8")

                # Stage 8: Local Replay Verification
                t0 = time.time()
                verifier = ServerVerifier(
                    server_dir=out_path,
                    entries=redact_result.entries,
                    tool_specs=design_spec.tools,
                )
                report = asyncio.run(verifier.verify())
                steps.append(
                    {
                        "stage": "8. Mock Replay & Verification",
                        "status": "success" if report.all_passed else "warning",
                        "duration_ms": round((time.time() - t0) * 1000, 1),
                        "summary": f"Verified {report.passed_tools}/{report.total_tools} tools against local replay mock ({report.score * 100:.0f}% score).",
                    }
                )

            total_elapsed = round(time.time() - start_time, 2)

            # Format tool list for UI
            formatted_tools = []
            for t in design_spec.tools:
                first_ep = t.endpoints[0] if t.endpoints else None
                formatted_tools.append(
                    {
                        "name": t.name,
                        "description": t.description,
                        "is_write": t.is_write,
                        "method": first_ep.method if first_ep else "GET",
                        "path_template": first_ep.path_template if first_ep else "/",
                        "parameters": [
                            {
                                "name": p.name,
                                "type": p.type,
                                "description": p.description,
                                "required": p.required,
                                "default": p.default,
                            }
                            for p in t.parameters
                        ],
                    }
                )

            verification_data = {
                "score": report.score,
                "passed_tools": report.passed_tools,
                "total_tools": report.total_tools,
                "all_passed": report.all_passed,
                "results": [
                    {
                        "tool_name": r.tool_name,
                        "passed": r.passed,
                        "error_message": r.error_message,
                    }
                    for r in report.results
                ],
            }

            response_payload = {
                "status": "success",
                "total_time_seconds": total_elapsed,
                "server_name": design_spec.server_name or "mcp_api_server",
                "auth_type": auth_spec.primary_type.value,
                "auth_env_var": primary_env,
                "secrets_redacted": n_secrets,
                "steps": steps,
                "tools": formatted_tools,
                "verification": verification_data,
                "files": files,
            }

            resp_bytes = json.dumps(response_payload).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(resp_bytes)))
            self._send_cors_headers()
            self.end_headers()
            self.wfile.write(resp_bytes)

        except Exception as e:
            logger.exception("Error executing pipeline in web studio: %s", e)
            self._send_json_error(
                HTTPStatus.INTERNAL_SERVER_ERROR, f"Pipeline execution failed: {e}"
            )
        finally:
            if temp_har_path and temp_har_path.exists():
                try:
                    temp_har_path.unlink()
                except Exception:
                    pass


def run_server(
    host: str = "127.0.0.1", port: int = 8080, open_browser: bool = True
) -> None:
    """Launch the HAR2MCP studio web server."""
    server_address = (host, port)
    httpd = ThreadingHTTPServer(server_address, StudioRequestHandler)
    url = f"http://{host}:{port}"
    print(f"\n=======================================================")
    print(f"  ✨ HAR2MCP Studio running at: {url}")
    print(
        f"  🤖 LLM Engine: {'Gemini 2.5 Flash' if os.environ.get('GEMINI_API_KEY') else 'Deterministic'}"
    )
    print(f"  Press Ctrl+C to stop the server.")
    print(f"=======================================================\n")

    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[+] Stopping HAR2MCP Studio server...")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    run_server()
