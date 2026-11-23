"""Code generator engine producing standalone FastMCP server projects from ToolSpecs."""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader

from har2mcp.auth.models import AuthSpec
from har2mcp.codegen.models import (
    RenderedTool,
    ServerProjectConfig,
    build_rendered_tool,
    extract_base_url_from_endpoints,
    sanitize_project_name,
)
from har2mcp.design.models import ToolSpec

logger = logging.getLogger(__name__)

DEFAULT_TEMPLATES_DIR = Path(__file__).parent / "templates"


class ServerGenerator:
    """Renders and writes complete FastMCP server projects using Jinja2 templates."""

    def __init__(self, template_dir: Path | None = None) -> None:
        """Initialize Jinja2 template environment."""
        self.template_dir = template_dir or DEFAULT_TEMPLATES_DIR
        if not self.template_dir.exists():
            raise FileNotFoundError(
                f"Templates directory not found at: {self.template_dir}"
            )

        self.jinja_env = Environment(
            loader=FileSystemLoader(str(self.template_dir)),
            autoescape=False,
            trim_blocks=True,
            lstrip_blocks=True,
        )

    def build_config(
        self,
        tool_specs: list[ToolSpec],
        auth_spec: AuthSpec,
        server_name: str = "api-mcp-server",
        base_url: str | None = None,
        allow_writes: bool = False,
        has_pagination: bool = False,
        version: str = "0.1.0",
    ) -> ServerProjectConfig:
        """Filter tools according to write rules and build ServerProjectConfig."""
        # Enforce read-only default unless allow_writes is True
        filtered_specs = [
            tool
            for tool in tool_specs
            if allow_writes or (tool.operation_type != "write" and not tool.is_write)
        ]

        rendered_tools: list[RenderedTool] = [
            build_rendered_tool(tool) for tool in filtered_specs
        ]

        resolved_base_url = base_url or extract_base_url_from_endpoints(filtered_specs)
        project_name = sanitize_project_name(server_name)

        return ServerProjectConfig(
            server_name=server_name,
            project_name=project_name,
            description=f"FastMCP server for {server_name}",
            base_url=resolved_base_url,
            tools=rendered_tools,
            auth_spec=auth_spec,
            allow_writes=allow_writes,
            has_pagination=has_pagination,
            version=version,
        )

    def render_files(self, config: ServerProjectConfig) -> dict[str, str]:
        """Render all template files into in-memory string contents."""
        context: dict[str, Any] = {
            "server_name": config.server_name,
            "project_name": config.project_name,
            "description": config.description,
            "base_url": config.base_url,
            "tools": config.tools,
            "auth_spec": config.auth_spec,
            "allow_writes": config.allow_writes,
            "has_pagination": config.has_pagination,
            "version": config.version,
        }

        templates = {
            "server.py": "server.py.jinja",
            "pyproject.toml": "pyproject.toml.jinja",
            ".env.example": "env.example.jinja",
            "README.md": "README.md.jinja",
            "Dockerfile": "Dockerfile.jinja",
        }

        rendered: dict[str, str] = {}
        for filename, tmpl_name in templates.items():
            template = self.jinja_env.get_template(tmpl_name)
            rendered[filename] = template.render(context)

        return rendered

    def generate(
        self,
        tool_specs: list[ToolSpec],
        auth_spec: AuthSpec,
        out_dir: Path | str,
        server_name: str = "api-mcp-server",
        base_url: str | None = None,
        allow_writes: bool = False,
        has_pagination: bool = False,
        version: str = "0.1.0",
        format_code: bool = True,
    ) -> list[Path]:
        """Generate full server project into out_dir."""
        output_path = Path(out_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        config = self.build_config(
            tool_specs=tool_specs,
            auth_spec=auth_spec,
            server_name=server_name,
            base_url=base_url,
            allow_writes=allow_writes,
            has_pagination=has_pagination,
            version=version,
        )

        rendered_files = self.render_files(config)
        generated_paths: list[Path] = []

        for filename, content in rendered_files.items():
            file_path = output_path / filename
            file_path.write_text(content, encoding="utf-8")
            generated_paths.append(file_path)

        if format_code:
            self._format_python_files(output_path)

        return generated_paths

    def _format_python_files(self, project_dir: Path) -> None:
        """Attempt to format generated python files with ruff if available."""
        server_py = project_dir / "server.py"
        if not server_py.exists():
            return

        # Check if ruff executable is in path or python -m ruff works
        ruff_bin = shutil.which("ruff")
        base_cmd = [ruff_bin] if ruff_bin else [sys.executable, "-m", "ruff"]

        try:
            subprocess.run(
                [*base_cmd, "check", "--fix", str(server_py)],
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
            subprocess.run(
                [*base_cmd, "format", str(server_py)],
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except Exception as exc:
            logger.debug("Ruff format skipped or failed: %s", exc)


def generate_server_project(
    tool_specs: list[ToolSpec],
    auth_spec: AuthSpec,
    out_dir: Path | str,
    server_name: str = "api-mcp-server",
    base_url: str | None = None,
    allow_writes: bool = False,
    has_pagination: bool = False,
) -> list[Path]:
    """Convenience helper to generate an MCP server project."""
    generator = ServerGenerator()
    return generator.generate(
        tool_specs=tool_specs,
        auth_spec=auth_spec,
        out_dir=out_dir,
        server_name=server_name,
        base_url=base_url,
        allow_writes=allow_writes,
        has_pagination=has_pagination,
    )
