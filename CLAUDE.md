# Gmail MCP Server

Local Gmail MCP server. Raw JSON-RPC 2.0 over stdio. Python.

## Project Plan

See [PROJECT_PLAN.md](PROJECT_PLAN.md) for the phased implementation plan (Phases 0–11). Reference it when starting a new phase, deciding what to build next, or checking "done when" criteria for the current phase.

## Project Structure

```
gmail-mcp-server/
├── CLAUDE.md
├── pyproject.toml
├── src/
│   └── gmail_mcp/
│       ├── __init__.py
│       ├── server.py          # JSON-RPC 2.0 dispatcher, MCP method handlers
│       ├── auth.py            # OAuth 2.0 flow, token persistence, auto-refresh
│       ├── gmail_client.py    # Thin wrapper around Gmail API, returns Pydantic models
│       └── tools/
│           ├── __init__.py
│           ├── messages.py    # gmail_search, gmail_read, gmail_list_labels, gmail_modify_labels, gmail_archive, gmail_trash, gmail_delete
│           ├── filters.py     # gmail_list_filters, gmail_create_filter, gmail_update_filter, gmail_delete_filter
│           └── bulk.py        # gmail_bulk_modify_labels, gmail_bulk_trash, gmail_bulk_archive
├── config/
│   ├── credentials.json       # OAuth client secret (gitignored)
│   └── token.json             # Stored refresh token (gitignored)
└── tests/
```

## Environment Variables

- `GMAIL_MCP_CREDENTIALS` — path to OAuth client_secret JSON (default: `./config/credentials.json`)
- `GMAIL_MCP_TOKEN` — path to stored OAuth token (default: `./config/token.json`)

## Protocol: JSON-RPC 2.0 over stdio

One JSON object per line on stdin. One JSON object per line on stdout. Log to stderr.

### Message Formats

```json
// Request
{"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "gmail_search", "arguments": {"query": "is:unread"}}}

// Success response
{"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": "..."}]}}

// Error response
{"jsonrpc": "2.0", "id": 1, "error": {"code": -32601, "message": "Method not found"}}

// Notification (no id, no response)
{"jsonrpc": "2.0", "method": "notifications/initialized"}
```

### MCP Methods

**`initialize`** — Client sends `protocolVersion`, `capabilities`, `clientInfo`. Respond with:
```json
{
  "protocolVersion": "2024-11-05",
  "capabilities": {"tools": {}},
  "serverInfo": {"name": "gmail-mcp-server", "version": "0.1.0"}
}
```

**`notifications/initialized`** — No response. Client confirms handshake.

**`tools/list`** — Return `{"tools": [...]}` with each tool's `name`, `description`, and `inputSchema` (JSON Schema).

**`tools/call`** — `params.name` is the tool name, `params.arguments` is the tool input. Return `{"content": [{"type": "text", "text": "<JSON string>"}]}` on success. Return `{"content": [{"type": "text", "text": "<error message>"}], "isError": true}` on failure.

## Tool Specifications

### Message Tools

**`gmail_search`**
- Params: `query` (string, Gmail search syntax), `max_results` (int, default 20)
- Returns: list of `{id, threadId, subject, from, date, snippet, labels}`
- Description must include query syntax examples: `from:`, `subject:`, `is:unread`, `older_than:`, `has:attachment`, `label:`, `-label:`

**`gmail_read`**
- Params: `message_id` (string)
- Returns: `{id, threadId, subject, from, to, cc, date, labels, body}`
- Body: walk MIME tree recursively. Prefer `text/plain`. Fall back to `text/html` with HTML tags stripped. Handle `multipart/mixed` > `multipart/alternative` > `text/plain` + `text/html`.

**`gmail_list_labels`**
- Params: none
- Returns: list of `{id, name, type}` where type is `system` or `user`

**`gmail_modify_labels`**
- Params: `message_id` (string), `add_labels` (list of strings, optional), `remove_labels` (list of strings, optional)
- Label names are human-readable. Resolve to IDs internally.

**`gmail_archive`**
- Params: `message_id` (string)
- Implementation: remove `INBOX` label

**`gmail_trash`**
- Params: `message_id` (string)
- Uses `messages.trash()` endpoint

**`gmail_delete`**
- Params: `message_id` (string)
- Uses `messages.delete()` endpoint. Permanent. Tool description must warn about this.

### Filter Tools

**`gmail_list_filters`**
- Params: none
- Returns: list of `{id, criteria, action}` with criteria/action expanded to readable form

**`gmail_create_filter`**
- Params: `criteria` (object with optional: `from`, `to`, `subject`, `query`, `has_attachment`, `size`, `size_comparison`), `action` (object with optional: `add_labels`, `remove_labels`, `archive`, `mark_read`, `star`, `forward`, `delete`, `never_spam`, `never_important`)
- Label names in actions are human-readable. Resolve to IDs.

**`gmail_update_filter`**
- Params: `filter_id` (string), `criteria` (object), `action` (object)
- Gmail API has no update endpoint. Delete old filter, create new one. Not atomic. Description must say this.

**`gmail_delete_filter`**
- Params: `filter_id` (string)

### Bulk Tools

**`gmail_bulk_modify_labels`**
- Params: `query` (string), `add_labels` (list, optional), `remove_labels` (list, optional)
- Returns: `{messages_modified: int}`

**`gmail_bulk_trash`**
- Params: `query` (string)
- Returns: `{messages_trashed: int}`

**`gmail_bulk_archive`**
- Params: `query` (string)
- Returns: `{messages_archived: int}`

All bulk tools paginate internally. Gmail batch limit is 100 messages per request.

## Constraints

- **Label resolution**: always accept human-readable names, resolve to IDs via cached label mapping. Rebuild cache on cache miss.
- **Rate limits**: Gmail API quota is 250 units/second/user. Reads ~5 units, modifications ~10-50. Add basic exponential backoff on 429 responses.
- **MIME tree**: emails nest arbitrarily. `gmail_read` must handle recursive multipart structures, not just top-level parts.
- **Batch limit**: 100 messages per Gmail batch request. Bulk operations loop with pagination.
- **Error format**: include Gmail API error message and HTTP status in tool error responses.

## Type Safety

- **Pydantic models everywhere** — use Pydantic `BaseModel` subclasses for tool inputs, tool outputs, Gmail API response shapes, and internal data structures. No raw dicts.
- **Strict type checking** — pyright in strict mode (`typeCheckingMode = "strict"` in `pyproject.toml`). All code must pass with zero errors.
- **CI enforcement** — GitHub Actions runs `uv run pyright` on every PR and push to main. PRs that fail type checking cannot merge.
- **Editor feedback** — Pylance (VS Code) provides real-time type checking using the same pyright config. No separate pre-commit hook.

## Code Style

- Straightforward, well-commented. This is a learning project.
- Keep JSON-RPC protocol mechanics visible in `server.py`. No meta-programming or magic dispatch.
- `gmail_client.py` returns clean Pydantic models. Tools should not import `googleapiclient` directly.
- Tool handlers are plain functions that take a Pydantic model and return a Pydantic model.
