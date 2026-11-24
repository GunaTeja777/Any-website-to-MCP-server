"""Local HTTP mock/replay server returning recorded HAR responses for verification."""

from __future__ import annotations

import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

from har2mcp.parser.models import Entry

logger = logging.getLogger(__name__)


class RecordedResponse:
    """Captured HTTP response details extracted from HAR entry."""

    def __init__(
        self,
        status: int,
        content_type: str,
        body: bytes,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status = status
        self.content_type = content_type
        self.body = body
        self.headers = headers or {}


class ReplayServer:
    """Threaded HTTP server replaying recorded responses matching method and path."""

    def __init__(self, entries: list[Entry], host: str = "127.0.0.1") -> None:
        """Index recorded responses from entries and prepare local server."""
        self.host = host
        self.exact_map: dict[tuple[str, str, str], RecordedResponse] = {}
        self.path_map: dict[tuple[str, str], RecordedResponse] = {}
        self._index_entries(entries)

        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.port: int = 0

    def _index_entries(self, entries: list[Entry]) -> None:
        """Populate response lookup tables from HAR entries."""
        for entry in entries:
            req = entry.request
            res = entry.response
            method = req.method.upper()

            parsed = urlparse(req.url)
            path = parsed.path or "/"

            # Normalize query parameters to sorted key-value pairs
            sorted_query = (
                "&".join(sorted(parsed.query.split("&"))) if parsed.query else ""
            )

            body_str = res.content.text or ""
            body_bytes = body_str.encode("utf-8")

            # Extract Content-Type header
            content_type = "application/json"
            for h in res.headers:
                if h.name.lower() == "content-type":
                    content_type = h.value
                    break

            recorded = RecordedResponse(
                status=res.status,
                content_type=content_type,
                body=body_bytes,
            )

            # Store exact query match and path fallback
            self.exact_map[(method, path, sorted_query)] = recorded
            if (method, path) not in self.path_map:
                self.path_map[(method, path)] = recorded

    def match_response(
        self, method: str, url_path: str, query_str: str
    ) -> RecordedResponse | None:
        """Find matching recorded response for incoming request."""
        m = method.upper()
        sorted_query = "&".join(sorted(query_str.split("&"))) if query_str else ""

        # 1. Try exact (method, path, sorted_query)
        if (m, url_path, sorted_query) in self.exact_map:
            return self.exact_map[(m, url_path, sorted_query)]

        # 2. Try (method, path)
        if (m, url_path) in self.path_map:
            return self.path_map[(m, url_path)]

        # 3. Fuzzy match path templates where segment count and static parts match
        req_parts = [p for p in url_path.strip("/").split("/") if p]
        for (stored_m, stored_p), resp in self.path_map.items():
            if stored_m != m:
                continue
            stored_parts = [p for p in stored_p.strip("/").split("/") if p]
            if len(req_parts) == len(stored_parts):
                # Check if non-variable segments match
                match = True
                for rp, sp in zip(req_parts, stored_parts, strict=False):
                    if sp.startswith("{") and sp.endswith("}"):
                        continue
                    if rp != sp:
                        match = False
                        break
                if match:
                    return resp

        return None

    @property
    def base_url(self) -> str:
        """Return base URL of the active replay server (e.g. http://127.0.0.1:54321)."""
        if not self._server:
            raise RuntimeError("ReplayServer has not been started.")
        return f"http://{self.host}:{self.port}"

    def start(self) -> None:
        """Bind to a free port and start listening in a background daemon thread."""
        outer = self

        class _Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:
                """Suppress default stdout access logging."""

            def _handle_request(self) -> None:
                parsed = urlparse(self.path)
                resp = outer.match_response(self.command, parsed.path, parsed.query)
                if resp is not None:
                    self.send_response(resp.status)
                    self.send_header("Content-Type", resp.content_type)
                    self.send_header("Content-Length", str(len(resp.body)))
                    self.end_headers()
                    self.wfile.write(resp.body)
                else:
                    err_text = (
                        f'{{"error": "No response for {self.command} {parsed.path}"}}'
                    )
                    msg = err_text.encode()
                    self.send_response(404)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(msg)))
                    self.end_headers()
                    self.wfile.write(msg)

            def do_GET(self) -> None:
                self._handle_request()

            def do_POST(self) -> None:
                self._handle_request()

            def do_PUT(self) -> None:
                self._handle_request()

            def do_DELETE(self) -> None:
                self._handle_request()

            def do_PATCH(self) -> None:
                self._handle_request()

            def do_HEAD(self) -> None:
                self._handle_request()

        self._server = ThreadingHTTPServer((self.host, 0), _Handler)
        self.port = self._server.server_port

        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        logger.debug("ReplayServer running at %s", self.base_url)

    def stop(self) -> None:
        """Shut down HTTP server and release port."""
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None

    def __enter__(self) -> ReplayServer:
        self.start()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.stop()
