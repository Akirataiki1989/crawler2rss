"""Durable, opt-in JSON logging transport for the logs ingestion API.

This module is deliberately independent of the crawler's logger setup. Construct
``LogsApiClient.from_env()`` and call ``emit``/``flush`` from a separately
reviewed integration when desired.
"""

from __future__ import annotations

import json
import os
import secrets
import re
import sqlite3
import socket
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

SCHEMA_VERSION = 1
MAX_BATCH = 100
MAX_SPOOL_BYTES = 300 * 1024 * 1024
MAX_SPOOL_EVENTS = 100_000
REQUIRED_FIELDS = frozenset({
    "schema_version", "event_id", "occurred_at", "level", "service_id",
    "service_name", "hostname", "event", "message",
})
OPTIONAL_FIELDS = frozenset({
    "container", "image_tag", "user_account", "run_id", "trace_id", "context",
    "artifact_links", "error_type", "error_code", "error_message", "stack_trace",
    "cause_event_id",
})
ALLOWED_FIELDS = REQUIRED_FIELDS | OPTIONAL_FIELDS
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _ulid(now_ms: int | None = None) -> str:
    """Generate a 26-character, time sortable ULID without dependencies."""
    milliseconds = int(time.time() * 1000) if now_ms is None else now_ms
    if not 0 <= milliseconds < 2**48:
        raise ValueError("ULID timestamp out of range")
    value = (milliseconds << 80) | secrets.randbits(80)
    chars = []
    for _ in range(26):
        chars.append(_CROCKFORD[value & 31])
        value >>= 5
    return "".join(reversed(chars))


def new_ulid() -> str:
    """Create a producer-side ULID for a run or trace identifier."""
    return _ulid()


def _utc_timestamp(value: str | datetime | None = None) -> str:
    if value is None:
        dt = datetime.now(timezone.utc)
    elif isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("occurred_at must be RFC3339") from exc
    else:
        raise ValueError("occurred_at must be a datetime or RFC3339 string")
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError("occurred_at must include a timezone")
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def make_event(level: str, event: str, message: str, *, service_id: str = "crawler2rss",
               service_name: str = "Crawler2RSS", hostname: str | None = None,
               occurred_at: str | datetime | None = None, **fields) -> dict:
    """Build and validate an API v1 log event; unknown payload keys are rejected."""
    event = {
        "schema_version": SCHEMA_VERSION,
        "event_id": _ulid(),
        "occurred_at": _utc_timestamp(occurred_at),
        "level": level.upper(),
        "service_id": service_id,
        "service_name": service_name,
        "hostname": hostname or socket.gethostname(),
        "event": event,
        "message": message,
        **fields,
    }
    validate_event(event)
    return event


def validate_event(event: dict) -> None:
    if not isinstance(event, dict):
        raise ValueError("event must be an object")
    unknown = set(event) - ALLOWED_FIELDS
    missing = REQUIRED_FIELDS - set(event)
    if unknown or missing:
        raise ValueError(f"invalid event fields (unknown={sorted(unknown)}, missing={sorted(missing)})")
    if event["schema_version"] != SCHEMA_VERSION:
        raise ValueError("schema_version must be 1")
    if not isinstance(event["event_id"], str) or len(event["event_id"]) != 26 or any(c not in _CROCKFORD for c in event["event_id"]):
        raise ValueError("event_id must be a ULID")
    if not isinstance(event["occurred_at"], str):
        raise ValueError("occurred_at must be UTC RFC3339")
    try:
        parsed = datetime.fromisoformat(event["occurred_at"].replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("occurred_at must be UTC RFC3339") from exc
    if parsed.utcoffset() != timezone.utc.utcoffset(parsed) or not event["occurred_at"].endswith("Z"):
        raise ValueError("occurred_at must be UTC RFC3339")
    if event["level"] not in {"TRACE", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        raise ValueError("unsupported log level")
    for key in ("service_id", "service_name", "hostname", "message"):
        if not isinstance(event[key], str):
            raise ValueError(f"{key} must be a string")
    if not isinstance(event["event"], str) or not re.fullmatch(r"[a-z0-9]+(?:\.[a-z0-9_]+)+", event["event"]):
        raise ValueError("event must use lower-case dot notation")
    if "context" in event and not isinstance(event["context"], dict):
        raise ValueError("context must be an object")
    if "artifact_links" in event and not isinstance(event["artifact_links"], list):
        raise ValueError("artifact_links must be an array")
    for key in OPTIONAL_FIELDS - {"context", "artifact_links"}:
        if key in event and not isinstance(event[key], str):
            raise ValueError(f"{key} must be a string")


class LogsApiClient:
    """SQLite-backed transport. No network request is made without a token."""

    def __init__(self, url: str | None, token: str | None, spool_path: str,
                 *, opener=None, max_bytes: int = MAX_SPOOL_BYTES,
                 max_events: int = MAX_SPOOL_EVENTS):
        self.url, self.token, self.spool_path = url, token, spool_path
        self._opener = opener or urlopen
        self.max_bytes, self.max_events = max_bytes, max_events
        parent = os.path.dirname(os.path.abspath(spool_path))
        os.makedirs(parent, exist_ok=True)
        with self._database() as db:
            db.execute("CREATE TABLE IF NOT EXISTS events (event_id TEXT PRIMARY KEY, payload TEXT NOT NULL, level TEXT NOT NULL, created INTEGER NOT NULL)")

    @classmethod
    def from_env(cls, *, opener=None):
        return cls(os.environ.get("LOGS_API_URL"), os.environ.get("LOGS_INGEST_TOKEN"),
                   os.environ.get("LOGS_SPOOL_PATH", "logs-api-spool.sqlite3"), opener=opener)

    def emit(self, level: str, event: str, message: str, *, immediate: bool | None = None, **fields) -> bool:
        """Validate, persist, and optionally immediately send one structured log."""
        defaults = {
            "service_id": os.environ.get("LOGS_SERVICE_ID", "crawler2rss"),
            "service_name": os.environ.get("LOGS_SERVICE_NAME", "Crawler2RSS"),
            "hostname": os.environ.get("LOGS_HOSTNAME", socket.gethostname()),
        }
        for env_name, field in (("LOGS_CONTAINER", "container"), ("LOGS_IMAGE_TAG", "image_tag")):
            value = os.environ.get(env_name)
            if value:
                defaults[field] = value
        return self._store(make_event(level, event, message, **defaults, **fields), immediate=immediate)

    def _connect(self):
        db = sqlite3.connect(self.spool_path, timeout=10)
        db.execute("PRAGMA journal_mode=WAL")
        return db

    @contextmanager
    def _database(self):
        db = self._connect()
        try:
            with db:
                yield db
        finally:
            db.close()

    def _store(self, event: dict, *, immediate: bool | None = None) -> bool:
        validate_event(event)
        payload = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
        with self._database() as db:
            db.execute("INSERT OR IGNORE INTO events VALUES (?, ?, ?, ?)",
                       (event["event_id"], payload, event["level"], int(time.time())))
            self._evict(db)
        if immediate is None:
            immediate = event["level"] in {"WARNING", "ERROR", "CRITICAL"}
        if immediate:
            self.flush()
        return True

    def _evict(self, db):
        # Drop oldest entries at the lowest severity first. ERROR/CRITICAL are protected.
        while True:
            count, size = db.execute("SELECT COUNT(*), COALESCE(SUM(length(payload)), 0) FROM events").fetchone()
            if count <= self.max_events and size <= self.max_bytes:
                return
            row = db.execute("SELECT event_id FROM events WHERE level IN ('TRACE','DEBUG','INFO','WARNING') ORDER BY CASE level WHEN 'TRACE' THEN 0 WHEN 'DEBUG' THEN 1 WHEN 'INFO' THEN 2 ELSE 3 END, created, rowid LIMIT 1").fetchone()
            if row is None:
                return
            db.execute("DELETE FROM events WHERE event_id=?", (row[0],))

    def pending(self, limit: int = MAX_BATCH) -> list[dict]:
        with self._database() as db:
            rows = db.execute("SELECT payload FROM events ORDER BY created, rowid LIMIT ?", (min(limit, MAX_BATCH),)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def flush(self) -> int:
        if not self.url or not self.token:
            return 0
        batch = self.pending()
        if not batch:
            return 0
        body = json.dumps({"logs": batch}, ensure_ascii=False).encode("utf-8")
        request = Request(self.url, data=body, headers={
            "Authorization": f"Bearer {self.token}", "Content-Type": "application/json",
        }, method="POST")
        try:
            response = self._opener(request, timeout=10)
            with response:
                result = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, OSError, ValueError):
            return 0
        # Only explicit accepted/duplicate IDs are acknowledged; malformed responses
        # leave all rows available for a safe retry with the original event IDs.
        if not isinstance(result, dict):
            return 0
        accepted = result.get("accepted_event_ids", [])
        duplicates = result.get("duplicate_event_ids", [])
        if not isinstance(accepted, list) or not isinstance(duplicates, list):
            return 0
        if any(not isinstance(x, str) for x in accepted + duplicates):
            return 0
        acknowledged = set(accepted + duplicates)
        batch_ids = {event["event_id"] for event in batch}
        acknowledged &= batch_ids
        if acknowledged:
            with self._database() as db:
                db.executemany("DELETE FROM events WHERE event_id=?", ((event_id,) for event_id in acknowledged))
        return len(acknowledged)
