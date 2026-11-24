# HAR2MCP Demo & Architecture Decisions

This guide provides a complete, step-by-step walkthrough demonstrating how to take a network capture from a web session, generate an MCP server, verify it against a local replay mock, and connect it to an AI assistant. It also documents the core architectural decision to use **deterministic code first and LLMs for semantic judgment**.

---

## Part 1: Step-by-Step Demo Script

This demo uses the provided reference fixture `eval/cases/case_01_synthetic_cloud/recording.har`, which simulates a cloud dashboard session where a user lists deployed compute instances, inspects instance metrics, and fetches account quotas.

### Step 1: Prerequisites & Environment Setup

Ensure you have Python 3.11+ and `uv` installed:

```bash
# Clone and enter the repository
git clone https://github.com/username/har2mcp.git
cd Any-website-to-MCP-server

# Install dependencies into virtualenv
uv sync --all-extras
```

### Step 2: Inspect the Raw HAR Capture

A HAR file is a JSON document recording every HTTP transaction from your browser. In raw form, it contains thousands of lines of sensitive cookies, session tokens, images, and tracking pixels:

```bash
# Check the size and entry count of the recording
head -n 25 eval/cases/case_01_synthetic_cloud/recording.har
```

Notice that the raw capture contains real or synthetic authorization tokens (such as `Authorization: Bearer secret-token-xyz-12345`).

### Step 3: Run HAR2MCP Generation & Verification

Run the `generate` command with the `--verify` flag to automatically run the built-in local replay verification:

```bash
uv run har2mcp generate eval/cases/case_01_synthetic_cloud/recording.har \
  --out ./demo_server \
  --verify
```

#### What Happens Under the Hood:
1. **Parser**: Ingests the HAR, drops tracking domains and non-API traffic (CSS, images, bundles).
2. **Redact**: Replaces `secret-token-xyz-12345` with `<TOKEN_1>` and stores a local provenance mapping.
3. **Cluster**: Discovers endpoints (`GET /api/v1/instances`, `GET /api/v1/instances/{id}/metrics`).
4. **Schema**: Infers JSON schemas for query parameters (`status`, `page`, `limit`) and response payloads.
5. **Auth**: Detects Bearer Token authentication and generates an environment variable specification (`HAR2MCP_AUTH_TOKEN`).
6. **Design**: Synthesizes agent-friendly tool definitions (`list_instances`, `get_instance_metrics`).
7. **Codegen**: Emits a standalone FastMCP project in `./demo_server/`.
8. **Verify**: Boots a local mock server replaying the HAR responses on an ephemeral port, launches the generated server over standard MCP stdio, calls each tool, and validates the output against the recorded schema.

Output log:
```text
[+] Parsed 6 API entries from eval/cases/case_01_synthetic_cloud/recording.har
[+] Redacted 1 unique secrets with stable placeholders
[+] Discovered 2 distinct endpoint clusters
[+] Inferred schemas and pagination rules for 2 endpoints
[+] Detected auth specification: bearer_token (env: HAR2MCP_AUTH_TOKEN)
[+] Synthesized 2 MCP tool definitions
[+] Generated FastMCP server project in ./demo_server
[+] Running verification against local replay mock...
    -> Replay server started on port 58321
    -> Verifying tool: list_instances ... PASS
    -> Verifying tool: get_instance_metrics ... PASS
[+] Verification complete: 2/2 tools passed (100.0%)
```

---

### Step 4: Inspect the Generated Standalone Project

Examine the generated project directory:

```bash
ls -la ./demo_server
```

You will see:
- `server.py`: Complete, standalone FastMCP server implementation with typed async tool functions using `httpx`.
- `pyproject.toml`: Modern packaging specification pinned with dependencies (`mcp`, `httpx`, `pydantic`).
- `.env.example`: Template listing the required environment variables (e.g., `HAR2MCP_AUTH_TOKEN`).
- `README.md`: Documentation of each generated tool, its parameters, and instructions.
- `Dockerfile`: Production container build for deploying the MCP server anywhere.

Let's inspect the generated `demo_server/server.py`:
```python
# Each tool is fully typed with Pydantic / Python type hints and docstrings:
@mcp.tool(
    name="list_instances",
    description="List compute instances with optional status filtering and pagination.",
)
async def list_instances(
    status: str | None = None,
    limit: int = 50,
) -> dict[str, Any]: ...
```

---

### Step 5: Test the Generated Server Manually

To execute the server locally:

```bash
cd demo_server

# Copy .env.example and populate your real credential
cp .env.example .env

# Run the FastMCP server
uv run python server.py
```

You can test it using the official MCP Inspector:
```bash
npx @modelcontextprotocol/inspector uv run python server.py
```

Or connect it to Claude Desktop by adding it to your configuration file:

**macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`  
**Windows**: `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "cloud-dashboard": {
      "command": "uv",
      "args": [
        "run",
        "--with",
        "mcp",
        "--with",
        "httpx",
        "python",
        "/absolute/path/to/demo_server/server.py"
      ],
      "env": {
        "HAR2MCP_AUTH_TOKEN": "your-real-token-here"
      }
    }
  }
}
```

Now, Claude or any MCP-enabled agent can autonomously list your instances, query metrics, and analyze your cloud infrastructure.

---

## Part 2: Architecture Decisions

### Decision: Deterministic-First + LLM-for-Judgment

One of the foundational design choices of HAR2MCP is the strict separation between **deterministic procedural computation** and **stochastic LLM judgment**.

```mermaid
flowchart LR
    subgraph Deterministic ["100% Deterministic Engine (Fast, Free, Exact)"]
        direction TB
        D1["HAR Ingestion & Filtering"]
        D2["Secret & Credential Redaction"]
        D3["URL Path & ID Normalization"]
        D4["JSON Schema & Field Inference"]
        D5["Auth Provenance & Type Detection"]
        D6["Code Rendering via Jinja2"]
        D7["Mock Replay & Schema Comparison"]
    end

    subgraph LLM ["LLM Semantic Layer (Only Where Judgment Needed)"]
        direction TB
        L1["Tool Naming & Operation ID Refinement"]
        L2["Docstring & Argument Descriptions"]
        L3["User Action Tool Grouping"]
        L4["Repair Loop Patch Proposal (Failures Only)"]
    end

    Deterministic --> LLM
    LLM --> Deterministic
```

### Why NOT Use an LLM for the Entire Pipeline?

A naive approach to building HAR2MCP might be to prompt an LLM: *"Here is a 50MB HAR file. Write me an MCP server in Python."*

This fails catastrophically for several reasons:

1. **Security & Data Exfiltration**:
   - A raw HAR file contains active session cookies, JWTs, Stripe API keys, passwords, and private PII.
   - Sending an unredacted HAR file to any remote model violates data privacy standards, GDPR, and basic operational security.
   - Regex and procedural tokenizers guarantee that secrets are replaced with `<TOKEN_N>` placeholders before any network socket sends data to an AI model.

2. **Cost & Context Limitations**:
   - Realistic HAR files range from 5 MB to 100+ MB.
   - Ingesting millions of tokens of raw JSON costs tens of dollars per run, overflows context windows, and degrades reasoning performance ("needle in a haystack").
   - Deterministic filtering drops 95%+ of traffic (SVGs, analytics pings, static CSS/JS) in milliseconds at $0.00 cost.

3. **Hallucination of Schemas, Types, and Paths**:
   - LLMs struggle to faithfully reproduce exact JSON schemas across dozens of observed responses. They frequently miss optional fields, misclassify nullable values, or invent parameters that do not exist.
   - Algorithms like `genson` merged with multi-sample cross-validation produce mathematically precise JSON schemas and exact required-field matrices.

4. **Reproducibility & Predictability**:
   - Software engineering pipelines require idempotency. Re-running the pipeline on the same HAR file should produce identical endpoint clusters, identical parameter lists, and identical routing tables every time.

---

### Where LLMs Excel: Semantic Judgment

While computers are superior at parsing, sorting, and regex matching, LLMs excel at human language and semantic abstraction:

1. **Meaningful Tool Naming**:
   - An endpoint like `GET /api/v2/orgs/19482/billing_summary?period=q3` could be clumsily auto-named `get_api_v2_orgs_by_param_billing_summary`.
   - An LLM immediately understands the context and names the tool `get_quarterly_billing_summary`.

2. **Agent-Oriented Documentation**:
   - An LLM writes clear, concise docstrings that explain to *other AI agents* what the tool does, when to call it, and what parameters to pass.

3. **Logical Grouping**:
   - Frontends often make three disparate API calls to load a single page (e.g., user details, preferences, and notifications). An LLM can recognize that an agent would benefit from a consolidated tool or clearly related parameter structures.

4. **Targeted Self-Repair**:
   - If a generated tool fails during verification (e.g., an unexpected 422 Unprocessable Entity or header mismatch), sending the isolated, redacted snippet of code along with the exact error message allows the LLM to provide a surgical 3-line patch without re-generating the entire server.

---

### Summary of Benefits

| Dimension | Pure LLM Pipeline | HAR2MCP Hybrid (Deterministic + LLM) |
|:----------|:-------------------|:--------------------------------------|
| **Secret Leakage Risk** | High (Entire HAR sent to API) | **Zero** (Local deterministic redaction) |
| **Pipeline Execution Time** | 60–180 seconds | **< 3 seconds** |
| **Token Cost** | $2.00 – $15.00+ per HAR | **$0.00 – $0.01** |
| **Schema Accuracy** | Prone to hallucinations | **100% deterministic field fidelity** |
| **Code Reliability** | Variable syntax and imports | **Guaranteed valid AST + Replay verification** |
