"""Thin wrapper around the Gmail API. Returns Pydantic models.

GmailClient takes an authenticated ``Resource`` from ``auth.py`` and exposes
methods that map 1:1 to Gmail API operations.  Every ``.execute()`` call uses
``num_retries=3`` so transient 503s and rate-limit 429s are handled
automatically with exponential backoff (built into googleapiclient).

Nothing in this module imports tool-layer code.  Tool handlers import *this*.
"""

import base64
import html as html_mod
import logging
import re
from typing import Any

from googleapiclient.discovery import Resource  # type: ignore[import-untyped]  # noqa: F401
from pydantic import BaseModel

log = logging.getLogger(__name__)

# Every .execute() call passes this so the google-api-python-client retries
# transient HTTP errors (429, 5xx) with built-in exponential backoff.
_RETRIES = 3


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class Label(BaseModel):
    """A Gmail label."""

    id: str
    name: str
    type: str  # "system" or "user"


class MessageSummary(BaseModel):
    """Lightweight message returned by search/list — no body."""

    id: str
    thread_id: str
    subject: str
    sender: str  # "From" header
    date: str
    snippet: str
    labels: list[str]  # human-readable label names


class Message(BaseModel):
    """Full message including decoded body text."""

    id: str
    thread_id: str
    subject: str
    sender: str
    to: str
    cc: str
    date: str
    labels: list[str]
    body: str


class FilterCriteria(BaseModel):
    """Human-readable filter criteria."""

    from_: str | None = None
    to: str | None = None
    subject: str | None = None
    query: str | None = None
    has_attachment: bool | None = None
    size: int | None = None
    size_comparison: str | None = None  # "larger" or "smaller"


class FilterAction(BaseModel):
    """Human-readable filter action."""

    add_labels: list[str] | None = None
    remove_labels: list[str] | None = None
    archive: bool | None = None
    mark_read: bool | None = None
    star: bool | None = None
    forward: str | None = None
    delete: bool | None = None
    never_spam: bool | None = None
    never_important: bool | None = None


class Filter(BaseModel):
    """A Gmail filter with human-readable criteria and actions."""

    id: str
    criteria: FilterCriteria
    action: FilterAction


class BatchResult(BaseModel):
    """Result of a batch operation."""

    messages_affected: int


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class GmailClient:
    """Thin wrapper around the Gmail API.

    All methods return Pydantic models.  The constructor accepts the
    ``Resource`` object returned by ``auth.get_gmail_service()``.
    """

    def __init__(self, service: Resource) -> None:
        # Resource is dynamically built by googleapiclient — it has no real
        # type stubs, so every chained call (.users().messages() etc.) would
        # be Unknown.  Storing as Any makes attribute access return Any
        # instead of Unknown, which pyright accepts cleanly.
        self._svc: Any = service
        # Label cache: populated on first call to list_labels().
        self._label_cache: dict[str, Label] | None = None  # id -> Label
        self._label_name_to_id: dict[str, str] | None = None  # name -> id

    # ------------------------------------------------------------------
    # Labels
    # ------------------------------------------------------------------

    def list_labels(self) -> list[Label]:
        """List all labels and refresh the internal cache."""
        resp: dict[str, Any] = (
            self._svc.users()            .labels()
            .list(userId="me")
            .execute(num_retries=_RETRIES)
        )
        raw_labels: list[dict[str, Any]] = resp.get("labels", [])
        labels = [
            Label(id=l["id"], name=l["name"], type=l.get("type", "user"))
            for l in raw_labels
        ]
        # Rebuild caches.
        self._label_cache = {lb.id: lb for lb in labels}
        self._label_name_to_id = {lb.name: lb.id for lb in labels}
        return labels

    def resolve_label_id(self, name: str) -> str:
        """Resolve a human-readable label name to its ID.

        Rebuilds the cache on a miss (handles labels created after startup).
        """
        if self._label_name_to_id is None:
            self.list_labels()
        assert self._label_name_to_id is not None
        lid = self._label_name_to_id.get(name)
        if lid is not None:
            return lid
        # Cache miss — rebuild and retry once.
        self.list_labels()
        assert self._label_name_to_id is not None
        lid = self._label_name_to_id.get(name)
        if lid is None:
            raise ValueError(f"Unknown label: {name!r}")
        return lid

    def resolve_label_name(self, label_id: str) -> str:
        """Resolve a label ID to its human-readable name."""
        if self._label_cache is None:
            self.list_labels()
        assert self._label_cache is not None
        label = self._label_cache.get(label_id)
        if label is not None:
            return label.name
        # Cache miss — rebuild.
        self.list_labels()
        assert self._label_cache is not None
        label = self._label_cache.get(label_id)
        return label.name if label is not None else label_id

    # ------------------------------------------------------------------
    # Messages — search / read
    # ------------------------------------------------------------------

    def search_messages(
        self, query: str, max_results: int = 20
    ) -> list[MessageSummary]:
        """Search for messages matching a Gmail query string.

        Returns lightweight summaries (no body).  Paginates internally up to
        *max_results*.
        """
        message_ids: list[str] = []
        page_token: str | None = None

        while len(message_ids) < max_results:
            kwargs: dict[str, Any] = {
                "userId": "me",
                "q": query,
                "maxResults": min(max_results - len(message_ids), 500),
            }
            if page_token is not None:
                kwargs["pageToken"] = page_token

            resp: dict[str, Any] = (
                self._svc.users()                .messages()
                .list(**kwargs)
                .execute(num_retries=_RETRIES)
            )
            for m in resp.get("messages", []):
                message_ids.append(m["id"])
            page_token = resp.get("nextPageToken")
            if page_token is None:
                break

        # Fetch metadata for each message.
        summaries: list[MessageSummary] = []
        for mid in message_ids:
            raw: dict[str, Any] = (
                self._svc.users()                .messages()
                .get(
                    userId="me",
                    id=mid,
                    format="metadata",
                    metadataHeaders=["Subject", "From", "Date"],
                )
                .execute(num_retries=_RETRIES)
            )
            headers = _headers_dict(raw)
            label_ids: list[str] = raw.get("labelIds", [])
            summaries.append(
                MessageSummary(
                    id=raw["id"],
                    thread_id=raw["threadId"],
                    subject=headers.get("Subject", "(no subject)"),
                    sender=headers.get("From", ""),
                    date=headers.get("Date", ""),
                    snippet=raw.get("snippet", ""),
                    labels=[self.resolve_label_name(lid) for lid in label_ids],
                )
            )
        return summaries

    def get_message(self, message_id: str) -> Message:
        """Fetch a single message with full headers and decoded body text."""
        raw: dict[str, Any] = (
            self._svc.users()            .messages()
            .get(userId="me", id=message_id, format="full")
            .execute(num_retries=_RETRIES)
        )
        headers = _headers_dict(raw)
        label_ids: list[str] = raw.get("labelIds", [])
        body = _extract_body(raw.get("payload", {}))
        return Message(
            id=raw["id"],
            thread_id=raw["threadId"],
            subject=headers.get("Subject", "(no subject)"),
            sender=headers.get("From", ""),
            to=headers.get("To", ""),
            cc=headers.get("Cc", ""),
            date=headers.get("Date", ""),
            labels=[self.resolve_label_name(lid) for lid in label_ids],
            body=body,
        )

    # ------------------------------------------------------------------
    # Messages — modify / archive / trash / delete
    # ------------------------------------------------------------------

    def modify_labels(
        self,
        message_id: str,
        add_labels: list[str] | None = None,
        remove_labels: list[str] | None = None,
    ) -> None:
        """Add and/or remove labels on a single message.

        Labels are specified by human-readable name.
        """
        body: dict[str, list[str]] = {}
        if add_labels:
            body["addLabelIds"] = [self.resolve_label_id(n) for n in add_labels]
        if remove_labels:
            body["removeLabelIds"] = [
                self.resolve_label_id(n) for n in remove_labels
            ]
        self._svc.users().messages().modify(            userId="me", id=message_id, body=body
        ).execute(num_retries=_RETRIES)

    def archive_message(self, message_id: str) -> None:
        """Archive a message (remove the INBOX label)."""
        self.modify_labels(message_id, remove_labels=["INBOX"])

    def trash_message(self, message_id: str) -> None:
        """Move a message to the trash (recoverable)."""
        self._svc.users().messages().trash(            userId="me", id=message_id
        ).execute(num_retries=_RETRIES)

    def delete_message(self, message_id: str) -> None:
        """Permanently delete a message. This cannot be undone."""
        self._svc.users().messages().delete(            userId="me", id=message_id
        ).execute(num_retries=_RETRIES)

    # ------------------------------------------------------------------
    # Messages — batch modify
    # ------------------------------------------------------------------

    def batch_modify_labels(
        self,
        message_ids: list[str],
        add_labels: list[str] | None = None,
        remove_labels: list[str] | None = None,
    ) -> None:
        """Add/remove labels on a batch of messages (max 1000 per call)."""
        body: dict[str, Any] = {"ids": message_ids}
        if add_labels:
            body["addLabelIds"] = [self.resolve_label_id(n) for n in add_labels]
        if remove_labels:
            body["removeLabelIds"] = [
                self.resolve_label_id(n) for n in remove_labels
            ]
        self._svc.users().messages().batchModify(            userId="me", body=body
        ).execute(num_retries=_RETRIES)

    # ------------------------------------------------------------------
    # Filters
    # ------------------------------------------------------------------

    def list_filters(self) -> list[Filter]:
        """List all Gmail filters with human-readable criteria and actions."""
        resp: dict[str, Any] = (
            self._svc.users()            .settings()
            .filters()
            .list(userId="me")
            .execute(num_retries=_RETRIES)
        )
        raw_filters: list[dict[str, Any]] = resp.get("filter", [])
        return [self._parse_filter(f) for f in raw_filters]

    def create_filter(
        self, criteria: FilterCriteria, action: FilterAction
    ) -> Filter:
        """Create a new Gmail filter. Returns the created filter."""
        body: dict[str, Any] = {
            "criteria": self._criteria_to_api(criteria),
            "action": self._action_to_api(action),
        }
        resp: dict[str, Any] = (
            self._svc.users()            .settings()
            .filters()
            .create(userId="me", body=body)
            .execute(num_retries=_RETRIES)
        )
        return self._parse_filter(resp)

    def delete_filter(self, filter_id: str) -> None:
        """Delete a Gmail filter by ID."""
        self._svc.users().settings().filters().delete(            userId="me", id=filter_id
        ).execute(num_retries=_RETRIES)

    # ------------------------------------------------------------------
    # Filter helpers
    # ------------------------------------------------------------------

    def _parse_filter(self, raw: dict[str, Any]) -> Filter:
        """Convert a raw API filter dict into a Filter model."""
        criteria = raw.get("criteria", {})
        action = raw.get("action", {})
        return Filter(
            id=raw["id"],
            criteria=FilterCriteria(
                from_=criteria.get("from"),
                to=criteria.get("to"),
                subject=criteria.get("subject"),
                query=criteria.get("query"),
                has_attachment=criteria.get("hasAttachment"),
                size=criteria.get("size"),
                size_comparison=criteria.get("sizeComparison"),
            ),
            action=FilterAction(
                add_labels=[
                    self.resolve_label_name(lid)
                    for lid in action.get("addLabelIds", [])
                ],
                remove_labels=[
                    self.resolve_label_name(lid)
                    for lid in action.get("removeLabelIds", [])
                ],
                archive="INBOX" in action.get("removeLabelIds", []),
                mark_read="UNREAD" in action.get("removeLabelIds", []),
                star="STARRED" in action.get("addLabelIds", []),
                forward=action.get("forward"),
                delete=None,
                never_spam=action.get("neverSpam"),
                never_important=action.get("neverMarkAsImportant"),
            ),
        )

    def _criteria_to_api(self, c: FilterCriteria) -> dict[str, Any]:
        """Convert FilterCriteria to the Gmail API format."""
        d: dict[str, Any] = {}
        if c.from_ is not None:
            d["from"] = c.from_
        if c.to is not None:
            d["to"] = c.to
        if c.subject is not None:
            d["subject"] = c.subject
        if c.query is not None:
            d["query"] = c.query
        if c.has_attachment is not None:
            d["hasAttachment"] = c.has_attachment
        if c.size is not None:
            d["size"] = c.size
        if c.size_comparison is not None:
            d["sizeComparison"] = c.size_comparison
        return d

    def _action_to_api(self, a: FilterAction) -> dict[str, Any]:
        """Convert FilterAction to the Gmail API format."""
        d: dict[str, Any] = {}
        add_ids: list[str] = []
        remove_ids: list[str] = []

        if a.add_labels:
            add_ids.extend(self.resolve_label_id(n) for n in a.add_labels)
        if a.star:
            add_ids.append("STARRED")
        if a.remove_labels:
            remove_ids.extend(self.resolve_label_id(n) for n in a.remove_labels)
        if a.archive:
            remove_ids.append("INBOX")
        if a.mark_read:
            remove_ids.append("UNREAD")

        if add_ids:
            d["addLabelIds"] = add_ids
        if remove_ids:
            d["removeLabelIds"] = remove_ids
        if a.forward is not None:
            d["forward"] = a.forward
        if a.never_spam is not None:
            d["neverSpam"] = a.never_spam
        if a.never_important is not None:
            d["neverMarkAsImportant"] = a.never_important
        return d


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _headers_dict(raw_message: dict[str, Any]) -> dict[str, str]:
    """Extract a {name: value} dict from a raw message's payload headers."""
    headers: list[dict[str, str]] = (
        raw_message.get("payload", {}).get("headers", [])
    )
    return {h["name"]: h["value"] for h in headers}


def _extract_body(payload: dict[str, Any]) -> str:
    """Recursively walk a MIME payload and return the best body text.

    Preference order:
        1. text/plain
        2. text/html (with tags stripped)

    Handles nested multipart/mixed > multipart/alternative > leaf parts.
    """
    mime_type: str = payload.get("mimeType", "")

    # Leaf node — decode and return.
    if not mime_type.startswith("multipart/"):
        data: str = payload.get("body", {}).get("data", "")
        if not data:
            return ""
        decoded = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
        if mime_type == "text/plain":
            return decoded
        if mime_type == "text/html":
            return _strip_html(decoded)
        # Other types (images, PDFs, etc.) — skip.
        return ""

    # Multipart node — recurse into parts.
    parts: list[dict[str, Any]] = payload.get("parts", [])

    # For multipart/alternative, prefer text/plain over text/html.
    if mime_type == "multipart/alternative":
        plain = ""
        html = ""
        for part in parts:
            child_mime: str = part.get("mimeType", "")
            if child_mime == "text/plain" or child_mime.startswith("multipart/"):
                result = _extract_body(part)
                if result and not plain:
                    plain = result
            elif child_mime == "text/html":
                result = _extract_body(part)
                if result and not html:
                    html = result
        return plain or html

    # For multipart/mixed, multipart/related, etc. — concatenate text parts.
    texts: list[str] = []
    for part in parts:
        result = _extract_body(part)
        if result:
            texts.append(result)
    return "\n".join(texts)


def _strip_html(html: str) -> str:
    """Strip HTML tags and decode entities, returning plain text."""
    # Remove <style> and <script> blocks entirely.
    text = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL | re.IGNORECASE)
    # Replace <br> and block-level tags with newlines.
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</(p|div|tr|li|h[1-6])>", "\n", text, flags=re.IGNORECASE)
    # Strip remaining tags.
    text = re.sub(r"<[^>]+>", "", text)
    # Decode HTML entities.
    text = html_mod.unescape(text)
    # Collapse excessive whitespace but keep single newlines.
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
