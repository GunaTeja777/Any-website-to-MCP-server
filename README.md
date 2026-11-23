# HAR2MCP

> Turn browser network recordings (.har) into working Model Context Protocol (MCP) servers.

HAR2MCP analyzes browser HAR exports from real website sessions, identifies internal API endpoints, redacts sensitive credentials, and generates a fully functional, type-safe MCP server ready for AI assistants like Claude Desktop, Cursor, and Antigravity.

## Core Principles

- **Deterministic Code First**: Deterministic parsing, clustering, and schema synthesis; LLMs are utilized only when semantic judgment is required (tool naming, descriptions, repair loop).
- **Zero-Secret LLM Leakage**: All credentials (cookies, session tokens, authorization headers, passwords) are redacted locally before any data is passed to an LLM.
- **Read-Only by Default**: Generated servers only expose read methods (`GET`, `HEAD`). State-mutating operations require an explicit `--allow-writes` flag.
- **Provider-Agnostic LLM Layer**: Swappable LLM clients via a single unified interface (`src/har2mcp/llm/client.py`).
- **High Reliability**: 100% type-annotated, Pydantic-validated, and accompanied by comprehensive pytest coverage.

## Project Structure

```text
har2mcp/
├── .github/
│   └── workflows/
│       └── ci.yml             # GitHub Actions CI for ruff lint + pytest
├── docs/
│   └── ARCHITECTURE.md        # Pipeline architecture & specifications
├── fixtures/
│   └── sample_minimal.har     # Fixtures for testing
├── examples/                  # Example outputs and usage recipes
├── src/
│   └── har2mcp/
│       ├── __init__.py
│       ├── __main__.py
│       ├── auth/              # Credential & session detection
│       ├── cli/               # CLI entrypoint and commands
│       ├── cluster/           # URL normalization and grouping
│       ├── codegen/           # FastMCP server code generation
│       ├── design/            # Tool naming & design synthesis
│       ├── llm/               # Provider-agnostic LLM interface
│       ├── parser/            # HAR ingestion & traffic filtering
│       ├── redact/            # Deterministic secret redaction
│       ├── schema/            # Request/response schema inference
│       └── verify/            # Syntax validation & repair loop
├── tests/                     # Unit and integration test suites
├── pyproject.toml             # Project metadata, dependencies & tool configs
└── README.md
```

## Quick Start

### Installation

Requires Python 3.11+ and [`uv`](https://github.com/astral-sh/uv):

```bash
# Clone the repository
git clone https://github.com/username/har2mcp.git
cd har2mcp

# Create environment and install in editable mode with development tools
uv sync --all-extras
```

### CLI Usage

```bash
# Display CLI help
uv run har2mcp --help

# Generate an MCP server from a HAR file (read-only by default)
uv run har2mcp generate path/to/recording.har --out ./out

# Generate with mutation tools enabled (POST, PUT, DELETE, PATCH)
uv run har2mcp generate path/to/recording.har --out ./out --allow-writes
```

### Development

```bash
# Run tests
uv run pytest

# Check formatting and linting
uv run ruff check .
uv run ruff format --check .

# Auto-format code
uv run ruff format .
```

## License

MIT
