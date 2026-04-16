# Gmail MCP Server

A local Gmail MCP server built from scratch in Python. Implements the Model Context Protocol over JSON-RPC 2.0 / stdio, exposing Gmail operations as tools that Claude Code (or any MCP client) can call.

Built as a learning project to understand MCP internals hands-on — the raw JSON-RPC layer is written manually before migrating to the official SDK.

## What it does

- **Search and read** Gmail messages with full MIME body extraction
- **Modify** messages: label, archive, trash, delete
- **Filter CRUD**: list, create, update, delete Gmail filters
- **Bulk operations**: batch label changes, archive, and trash across query results
- **OAuth 2.0**: browser-based consent flow with automatic token refresh

## Prerequisites

- Python 3.14+
- [uv](https://docs.astral.sh/uv/) for dependency management
- A Google Cloud project with the Gmail API enabled and OAuth Desktop App credentials (see [Project Plan](PROJECT_PLAN.md#phase-0-gcp-setup) for setup steps)

## Setup

```bash
# Clone and install
git clone https://github.com/pkeppeler/gmail-mcp.git
cd gmail-mcp
uv sync

# Add your OAuth credentials
mkdir -p config
cp /path/to/downloaded/credentials.json config/credentials.json

# First run will open a browser for OAuth consent
uv run python -m gmail_mcp.server
```

## Configuration

| Environment Variable       | Default                     | Description                        |
|----------------------------|-----------------------------|------------------------------------|
| `GMAIL_MCP_CREDENTIALS`   | `./config/credentials.json` | Path to OAuth client secret JSON   |
| `GMAIL_MCP_TOKEN`         | `./config/token.json`       | Path to stored OAuth refresh token |

## Using with Claude Code

Add to `~/.claude/claude_code_config.json`:

```json
{
  "mcpServers": {
    "gmail": {
      "command": "uv",
      "args": ["run", "python", "-m", "gmail_mcp.server"],
      "cwd": "/path/to/gmail-mcp",
      "env": {
        "GMAIL_MCP_CREDENTIALS": "./config/credentials.json",
        "GMAIL_MCP_TOKEN": "./config/token.json"
      }
    }
  }
}
```

## Tools

### Messages
| Tool                   | Description                                      |
|------------------------|--------------------------------------------------|
| `gmail_search`         | Search messages using Gmail query syntax          |
| `gmail_read`           | Read a full message with decoded body             |
| `gmail_list_labels`    | List all labels (system and user)                 |
| `gmail_modify_labels`  | Add/remove labels by human-readable name          |
| `gmail_archive`        | Archive a message (remove INBOX label)            |
| `gmail_trash`          | Move a message to trash (recoverable)             |
| `gmail_delete`         | Permanently delete a message (irreversible)       |

### Filters
| Tool                   | Description                                      |
|------------------------|--------------------------------------------------|
| `gmail_list_filters`   | List all filters with readable criteria/actions   |
| `gmail_create_filter`  | Create a filter from criteria + action            |
| `gmail_update_filter`  | Update a filter (delete + recreate, not atomic)   |
| `gmail_delete_filter`  | Delete a filter by ID                             |

### Bulk Operations
| Tool                        | Description                                 |
|-----------------------------|---------------------------------------------|
| `gmail_bulk_modify_labels`  | Add/remove labels on all messages matching a query |
| `gmail_bulk_trash`          | Trash all messages matching a query          |
| `gmail_bulk_archive`        | Archive all messages matching a query        |

## Development

```bash
# Type checking (pyright strict mode)
uv run pyright

# Run the server manually for testing
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"0.1.0"}}}' | uv run python -m gmail_mcp.server
```

## Project Plan

See [PROJECT_PLAN.md](PROJECT_PLAN.md) for the phased implementation plan covering GCP setup through SDK migration.

## References

- [Model Context Protocol Specification](https://modelcontextprotocol.io/specification/2025-03-26)
- [MCP GitHub Organization](https://github.com/modelcontextprotocol)

## License

MIT
