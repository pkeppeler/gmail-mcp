# Gmail MCP Server — Project Plan

## Goal
Build a local Gmail MCP server in Python to learn MCP internals hands-on, and to use it for bulk inbox cleanup, filter auditing, and filter creation via Claude Code.

## Scope: v1

**In scope**: message read/write/search, filter CRUD, bulk operations, OAuth auth, stdio transport.
**Out of scope**: send/compose/draft, settings management, SSE transport, multi-account.

## Dependencies

- `google-auth` + `google-auth-oauthlib` — OAuth 2.0 flow
- `google-api-python-client` — Gmail API client
- No MCP SDK until Phase 11

---

## Phase 0: GCP Setup ✅
**What**: Manual setup in Google Cloud Console. No code.

### Step 1: Create a Google Cloud project

1. Go to [**Create a New Project**](https://console.cloud.google.com/projectcreate)
2. Project name: `gmail-mcp-server` (or whatever you like)
3. Click **Create** and wait for it to provision

### Step 2: Enable the Gmail API

1. Open the [**Gmail API page**](https://console.cloud.google.com/apis/library/gmail.googleapis.com) (make sure your new project is selected in the top-left dropdown)
2. Click **Enable**

### Step 3: Configure the OAuth consent screen

1. Go to [**Google Auth Platform**](https://console.cloud.google.com/auth/overview) (this is the new OAuth consent screen)
2. If prompted, click **Get Started**
3. Fill in the required fields:
   - App name: `Gmail MCP Server`
   - User support email: your email
   - Developer contact email: your email
4. Select User type: **External**
5. Click **Create** (or **Save**)

### Step 4: Add API scopes

1. In the left sidebar, click [**Data Access**](https://console.cloud.google.com/auth/scopes)
2. Click **Add or Remove Scopes**
3. Filter by "Gmail API" and select these two scopes:
   - `https://www.googleapis.com/auth/gmail.modify`
   - `https://www.googleapis.com/auth/gmail.settings.basic`
4. Click **Update**, then **Save**

### Step 5: Add yourself as a test user

1. In the left sidebar, click [**Audience**](https://console.cloud.google.com/auth/audience)
2. Under **Test users**, click **Add Users**
3. Add your own Gmail address
4. Click **Save**

### Step 6: Create OAuth credentials

1. In the left sidebar, click [**Clients**](https://console.cloud.google.com/auth/clients) (or go to [**Credentials**](https://console.cloud.google.com/apis/credentials))
2. Click **Create Client** (or **Create Credentials** > **OAuth client ID**)
3. Application type: **Desktop app**
4. Name: `Gmail MCP Server` (or anything you like)
5. Click **Create**
6. Click **Download JSON** on the confirmation dialog
7. Save the downloaded file as `config/credentials.json` in this project

### Verify

Open `config/credentials.json` and confirm it has `installed.client_id`, `installed.client_secret`, and `installed.redirect_uris` fields.

**Done when**: `credentials.json` is in place with those fields populated.

---

## Phase 1: Project Scaffolding
**What**: directory structure, dependency config, gitignore. Pure boilerplate.

- `pyproject.toml` with dependencies
- `src/gmail_mcp/` package with empty `__init__.py` files
- `src/gmail_mcp/tools/` subpackage
- `config/` directory
- `.gitignore` covering `config/token.json`, `config/credentials.json`, `__pycache__`, `.venv`, `*.egg-info`

**Done when**: `uv sync` succeeds and `uv run python -c "import gmail_mcp"` doesn't error.

**Learning**: nothing new. Just getting the project bootable.

---

## Phase 2: OAuth Auth Module
**What**: `auth.py` — the OAuth 2.0 Desktop App flow.

- First run: open browser, user consents, receive authorization code, exchange for access + refresh tokens, persist to `GMAIL_MCP_TOKEN` path
- Subsequent runs: load stored token, auto-refresh if expired
- Expose `get_gmail_service()` that returns an authenticated `googleapiclient` service object
- Environment variable config for credential and token paths

**Done when**: run a standalone test script that calls `get_gmail_service()` and prints your email address via `service.users().getProfile(userId='me').execute()`.

**Learning**: OAuth 2.0 authorization code flow mechanics. How refresh tokens work. How `google-auth-oauthlib` wraps the flow.

---

## Phase 3: Gmail Client Wrapper
**What**: `gmail_client.py` — thin layer over the raw Gmail API.

- Takes an authenticated service object from `auth.py`
- Methods that map to Gmail API endpoints but return clean Python dicts
- Label listing with name/ID/type
- Message listing (by query) and single message fetch (with header extraction)
- MIME body extraction: recursive walk of multipart structure, prefer `text/plain`, fall back to stripped `text/html`
- Filter listing, creation, deletion
- Batch message modification (label add/remove on a list of message IDs)
- Internal pagination where the API requires it

**Done when**: you can run a test script that searches your inbox, reads a message, and prints the decoded body text. Also lists your filters.

**Learning**: Gmail API surface. How MIME structures work (this is the gnarliest part — email bodies nest arbitrarily). How the Gmail API handles pagination via `nextPageToken`. The difference between `messages.list` (returns IDs only) and `messages.get` (returns full message).

---

## Phase 4: JSON-RPC 2.0 Server
**What**: `server.py` — the raw protocol layer. This is the MCP learning core.

- Read one JSON object per line from stdin
- Parse and validate JSON-RPC 2.0 structure (check for `jsonrpc`, `method`, `id`)
- Distinguish requests (have `id`, need response) from notifications (no `id`, no response)
- Route by method name to handler functions
- Implement `initialize` handler: return `protocolVersion`, `capabilities` (with `tools: {}`), `serverInfo`
- Implement `notifications/initialized`: accept silently, no response
- Implement `tools/list`: return tool manifest (built dynamically from registered handlers)
- Implement `tools/call`: look up tool by `params.name`, call handler with `params.arguments`, return result
- Standard JSON-RPC error codes: `-32700` (parse error), `-32600` (invalid request), `-32601` (method not found), `-32602` (invalid params)
- Write responses as single-line JSON to stdout
- All logging to stderr

**Done when**: you can pipe JSON-RPC messages via stdin and get correct responses. Test the initialize handshake, tools/list (returns empty manifest), and error cases (bad JSON, unknown method). No Gmail tools registered yet.

**Learning**: JSON-RPC 2.0 protocol mechanics. MCP handshake flow. How tool manifests work (name + description + JSON Schema for inputs). How Claude Code communicates with MCP servers. This is the phase where MCP stops being abstract.

---

## Phase 5: First Tool — `gmail_list_labels`
**What**: register the simplest possible tool and validate the full pipeline.

- Implement `gmail_list_labels` tool handler in `tools/messages.py`
- Register it in the server's tool manifest
- Wire up auth: server initializes the Gmail client on startup
- Tool takes no parameters, returns list of `{id, name, type}`

**Done when**: pipe `{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"0.1.0"}}}` followed by `{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}` followed by `{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"gmail_list_labels","arguments":{}}}` and get back your actual Gmail labels.

**Learning**: how tool registration works end to end. How `tools/call` dispatches to a handler. How tool results are wrapped in the MCP content format (`{"content": [{"type": "text", "text": "..."}]}`). This is the first time real data flows through the full stack: stdin → JSON-RPC → MCP → tool handler → Gmail API → response.

---

## Phase 6: Message Read Tools
**What**: `gmail_search` and `gmail_read` in `tools/messages.py`.

- `gmail_search`: takes `query` (Gmail search syntax) and `max_results`, returns list of message summaries with id, subject, from, date, snippet, labels
- `gmail_read`: takes `message_id`, returns full message with headers and decoded body
- Label name resolution: tools accept human-readable label names in search results. Build a cached label name↔ID mapping, rebuild on cache miss.

**Done when**: you can search your inbox via JSON-RPC, pick a message ID from the results, and read its full body. Test with a multipart email to verify MIME parsing works.

**Learning**: how tool descriptions affect usability (the `gmail_search` description needs to include query syntax examples so Claude knows the syntax). How to structure tool output for LLM consumption (structured JSON, not prose). MIME parsing edge cases.

---

## Phase 7: Message Write Tools
**What**: `gmail_modify_labels`, `gmail_archive`, `gmail_trash`, `gmail_delete` in `tools/messages.py`.

- `gmail_modify_labels`: add/remove labels by human-readable name
- `gmail_archive`: remove INBOX label
- `gmail_trash`: move to trash (recoverable)
- `gmail_delete`: permanent delete (tool description must warn about this)

**Done when**: you can search for a message, archive it, verify it's gone from inbox, trash it, verify it's in trash. Test label modification with a user-created label.

**Learning**: how destructive tool descriptions should be worded so Claude uses them appropriately. The difference between trash (recoverable) and delete (permanent) and how to surface that distinction in tool design.

---

## Phase 8: Filter CRUD
**What**: `tools/filters.py` — list, create, update, delete filters.

- `gmail_list_filters`: list all filters with human-readable criteria and actions
- `gmail_create_filter`: create from criteria + action objects, resolve label names to IDs
- `gmail_update_filter`: delete old + create new (Gmail API limitation). Not atomic. Description must say this.
- `gmail_delete_filter`: delete by filter ID

**Done when**: you can list existing filters, create a new test filter (e.g., "from:test@example.com → add label 'Test'"), verify it appears in the list, update it, and delete it.

**Learning**: how to handle API limitations gracefully in tool design (the non-atomic update). How filter criteria and actions map to the Gmail API's filter schema.

---

## Phase 9: Bulk Operations
**What**: `tools/bulk.py` — batch operations on messages matching a query.

- `gmail_bulk_modify_labels`: add/remove labels on all matching messages
- `gmail_bulk_trash`: trash all matching messages
- `gmail_bulk_archive`: archive all matching messages
- All must paginate internally (Gmail batch limit: 100 per request)
- Return count of affected messages, not the full list
- Add basic exponential backoff on 429 (rate limit) responses

**Done when**: you can bulk archive all messages from a specific sender, verify they're gone from inbox, and the tool returns the correct count. Test with a query that returns >100 messages to verify pagination works.

**Learning**: Gmail batch API mechanics. Rate limiting and backoff. How to design bulk tools that are useful without being dangerous (returning counts, not silently operating on thousands of messages without feedback).

---

## Phase 10: Claude Code Integration
**What**: register the server with Claude Code and test it end-to-end.

Add to `~/.claude/claude_code_config.json`:
```json
{
  "mcpServers": {
    "gmail": {
      "command": "python",
      "args": ["-m", "gmail_mcp.server"],
      "cwd": "/path/to/gmail-mcp-server",
      "env": {
        "GMAIL_MCP_CREDENTIALS": "./config/credentials.json",
        "GMAIL_MCP_TOKEN": "./config/token.json"
      }
    }
  }
}
```

Test scenarios:
- "List my Gmail labels" — smoke test
- "Search for unread emails from the last week" — verify search works through Claude
- "Show me all my current Gmail filters" — verify filter listing
- "Archive all emails from newsletters@example.com older than 30 days" — verify bulk operations
- Watch Claude's tool selection: does it pick the right tool? Does the description wording cause any confusion?

**Done when**: Claude Code can use all your tools naturally without you having to correct its tool selection. If it's picking wrong tools or misunderstanding parameters, iterate on tool descriptions.

**Learning**: how tool description quality directly affects whether Claude picks the right tool. This is context engineering — you'll see firsthand how wording changes in descriptions alter Claude's behavior. This is the payoff phase where MCP stops being an exercise and starts being useful.

---

## Phase 11: SDK Refactor
**What**: replace `server.py` with the `mcp` Python SDK. Everything else stays the same.

- `uv add mcp`
- Rewrite `server.py` to use `@server.tool()` decorators and the SDK's stdio transport
- All tool implementations in `tools/` remain unchanged
- The diff should be confined to `server.py` and `pyproject.toml`

**Done when**: all the same Claude Code test scenarios from Phase 10 still work identically. The tool behavior is unchanged; only the protocol plumbing is different.

**Learning**: what the SDK abstracts away (JSON-RPC parsing, method routing, tool manifest generation, content wrapping). You'll have a direct before/after comparison. The SDK should feel like syntactic sugar over what you already built, not magic.
