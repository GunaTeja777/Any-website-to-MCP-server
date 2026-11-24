# auth_reports_profile_api MCP Server

Standalone Model Context Protocol (MCP) server generated automatically by **HAR2MCP**.

## Overview
This server exposes 2 tools allowing AI assistants (Claude Desktop, Cursor, etc.) to securely interact with the internal API of **auth_reports_profile_api**.

> **Notice**: Read-only server (`GET`/`HEAD`). Mutating operations are omitted.

---

## Exposed Tools

| Tool Name | Operation | Description |
|-----------|-----------|-------------|
| `list_reports` | `read` | Retrieves a list of reports. This tool is useful when an agent needs to view available reports or their current statuses. It can optionally filter reports by an API key and limit the number of results returned. |
| `get_user_profile` | `read` | Fetches the profile information for the currently authenticated user. This tool is useful when an agent needs to retrieve details about the user, such as their email, name, or active session ID. |

---

## Detailed Tool Reference

### `list_reports`
- **Operation**: `READ` (HTTP `GET`)
- **Endpoint**: `/v1/reports`
- **Description**: Retrieves a list of reports. This tool is useful when an agent needs to view available reports or their current statuses. It can optionally filter reports by an API key and limit the number of results returned.
- **Parameters**:
  - `api_key` (`str`, optional): An optional API key to filter the reports.
  - `limit` (`int`, optional): An optional maximum number of reports to return.

### `get_user_profile`
- **Operation**: `READ` (HTTP `GET`)
- **Endpoint**: `/v1/user/profile`
- **Description**: Fetches the profile information for the currently authenticated user. This tool is useful when an agent needs to retrieve details about the user, such as their email, name, or active session ID.
- **Parameters**: None


---

## Getting Started

### 1. Install Dependencies
```bash
pip install -e .
```

### 2. Configure Credentials
Copy `.env.example` to `.env` and configure your credentials:
```bash
cp .env.example .env
```

Required environment variables:
- `HAR2MCP_AUTH_TOKEN`: Authorization header with Bearer (injected as header)

### 3. Run Server
```bash
python server.py
```

### 4. Client Integration (Claude Desktop / Cursor)
Add to your `claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "auth-reports-profile-api": {
      "command": "python",
      "args": ["server.py"],
      "env": {
        "API_BASE_URL": "https://api.example.com"
      }
    }
  }
}
```