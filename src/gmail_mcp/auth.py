"""OAuth 2.0 flow, token persistence, and auto-refresh.

First run:  opens a browser for user consent, exchanges the authorization code
            for access + refresh tokens, and persists them to disk.
Subsequent: loads the stored token and auto-refreshes if expired.

Environment variables (file paths):
    GMAIL_MCP_CREDENTIALS  path to OAuth client-secret JSON  (default: ./config/credentials.json)
    GMAIL_MCP_TOKEN        path to stored OAuth token JSON    (default: ./config/token.json)

Environment variables (inline JSON — for remote/CI environments):
    GMAIL_MCP_CREDENTIALS_JSON  raw JSON content of the OAuth client-secret file
    GMAIL_MCP_TOKEN_JSON        raw JSON content of the stored OAuth token file

    When the _JSON variants are set, auth.py materializes them to disk at the
    configured file paths on startup. This lets you pass secrets via env vars
    (e.g. in Claude Code web sessions) without manually creating config files.
"""

import json
import logging
import os
import sys
from pathlib import Path

from google.auth.transport.requests import Request  # type: ignore[import-untyped]
from google.oauth2.credentials import Credentials  # type: ignore[import-untyped]
from google_auth_oauthlib.flow import InstalledAppFlow  # type: ignore[import-untyped]
from googleapiclient.discovery import build  # type: ignore[import-untyped]
from googleapiclient.discovery import Resource  # type: ignore[import-untyped]

log = logging.getLogger(__name__)

# Gmail API scopes required by this server.
SCOPES: list[str] = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.settings.basic",
]

# Default file paths (overridable via env vars).
_DEFAULT_CREDENTIALS_PATH = "./config/credentials.json"
_DEFAULT_TOKEN_PATH = "./config/token.json"


def _credentials_path() -> Path:
    """Return the path to the OAuth client-secret JSON file."""
    return Path(os.environ.get("GMAIL_MCP_CREDENTIALS", _DEFAULT_CREDENTIALS_PATH))


def _token_path() -> Path:
    """Return the path to the stored OAuth token JSON file."""
    return Path(os.environ.get("GMAIL_MCP_TOKEN", _DEFAULT_TOKEN_PATH))


def _materialize_env_secrets() -> None:
    """Write inline JSON env vars to disk so the rest of auth.py can read files as normal.

    Checks GMAIL_MCP_CREDENTIALS_JSON and GMAIL_MCP_TOKEN_JSON.  If set,
    writes their contents to the configured file paths, creating parent
    directories as needed.  Runs once at the start of get_credentials().
    """
    for env_var, path_fn in [
        ("GMAIL_MCP_CREDENTIALS_JSON", _credentials_path),
        ("GMAIL_MCP_TOKEN_JSON", _token_path),
    ]:
        raw = os.environ.get(env_var)
        if raw:
            path = path_fn()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(raw)
            log.info("Materialized %s → %s", env_var, path)


def _load_stored_token() -> Credentials | None:
    """Load a previously-stored OAuth token from disk.

    Returns None if the file doesn't exist or can't be parsed.
    """
    path = _token_path()
    if not path.exists():
        log.info("No stored token at %s", path)
        return None

    try:
        creds: Credentials = Credentials.from_authorized_user_file(str(path), SCOPES)  # pyright: ignore[reportUnknownMemberType]
        log.info("Loaded stored token from %s", path)
        return creds
    except (json.JSONDecodeError, ValueError, KeyError) as exc:
        log.warning("Failed to load stored token: %s", exc)
        return None


def _save_token(creds: Credentials) -> None:
    """Persist an OAuth token to disk so subsequent runs skip the browser flow."""
    path = _token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(creds.to_json())  # pyright: ignore[reportUnknownMemberType]
    log.info("Saved token to %s", path)


def _run_oauth_flow() -> Credentials:
    """Run the OAuth 2.0 installed-app (desktop) flow.

    Opens a browser for user consent, waits for the redirect, and exchanges the
    authorization code for tokens.
    """
    cred_path = _credentials_path()
    if not cred_path.exists():
        print(
            f"ERROR: OAuth client-secret file not found at {cred_path}\n"
            "Download it from the Google Cloud Console and place it there.\n"
            "See PROJECT_PLAN.md Phase 0 for instructions.",
            file=sys.stderr,
        )
        raise FileNotFoundError(f"OAuth client-secret file not found: {cred_path}")

    flow: InstalledAppFlow = InstalledAppFlow.from_client_secrets_file(str(cred_path), SCOPES)  # pyright: ignore[reportUnknownMemberType]
    creds = flow.run_local_server(port=0)  # pyright: ignore[reportUnknownMemberType]
    assert isinstance(creds, Credentials)
    log.info("OAuth flow completed successfully")
    return creds


def get_credentials() -> Credentials:
    """Return valid OAuth 2.0 credentials, refreshing or running the flow as needed.

    Order of operations:
        1. Try to load a stored token from disk.
        2. If the token exists but is expired, refresh it.
        3. If no token exists (or refresh fails), run the full browser flow.
        4. Persist the resulting token to disk.
    """
    _materialize_env_secrets()

    creds = _load_stored_token()

    if creds is not None and creds.valid:  # pyright: ignore[reportUnknownMemberType]
        return creds

    if creds is not None and creds.expired and creds.refresh_token:  # pyright: ignore[reportUnknownMemberType]
        try:
            creds.refresh(Request())  # pyright: ignore[reportUnknownMemberType]
            log.info("Refreshed expired token")
            _save_token(creds)
            return creds
        except Exception as exc:
            log.warning("Token refresh failed, re-running OAuth flow: %s", exc)

    # No valid token — run the full flow.
    creds = _run_oauth_flow()
    _save_token(creds)
    return creds


def get_gmail_service() -> Resource:
    """Return an authenticated Gmail API service object.

    This is the main entry point for the rest of the codebase. It handles
    credential loading, refresh, and the initial OAuth flow transparently.
    """
    creds = get_credentials()
    service: Resource = build("gmail", "v1", credentials=creds)  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    return service  # pyright: ignore[reportUnknownVariableType]
