# Copied from google-docs-mcp@2fb4652 src/<package>/transfer.py; bundles dropped, CSV pulls and CSV upload slots kept.
"""Transfer: pulled tabs leave as CSV behind signed GET links, and CLI query
results come back through signed PUT upload slots (spec, "Tables to DuckDB").

    hand_out_stream(chunks)  -> (signed GET URL, handle, bytes) of a CSV written as it streams
    hand_out_json(data)      -> signed GET URL of a JSON document (a pull's manifest)
    new_slot()               -> UploadSlot, a signed PUT for a CSV, valid 24 hours
    read_upload(upload)      -> (handle, bytes) of what the CLI uploaded

Every URL is a plain storage.googleapis.com V4 signed URL (the state store
signs them), so a sandboxed agent allowlists one domain.
"""
from __future__ import annotations

import json
import re
from collections.abc import Iterator
from datetime import UTC, datetime

from . import reply, state_store

PULL_URL_TTL_SECONDS = 900      # long enough to run `gsheet pull` on it, short enough to leak little

_HANDLE = re.compile(r"^gsheet-mcp/[A-Za-z0-9_-]+\.csv$")
_STORAGE_URL = re.compile(r"^https://storage\.googleapis\.com/[^/?]+/([^?]+)")


def iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _store():
    store = state_store.current()
    try:
        store.check_object_hosting()
    except state_store.ObjectHostingUnavailable as e:
        raise reply.Refusal(str(e)) from None
    return store


def new_slot() -> state_store.UploadSlot:
    try:
        return _store().upload_slot(content_type=state_store.CSV_CONTENT_TYPE)
    except state_store.ObjectHostingUnavailable as e:
        raise reply.Refusal(str(e)) from None


def hand_out_stream(chunks: Iterator[bytes]) -> tuple[state_store.HostedObject, int]:
    """Store a CSV as it streams in; return its signed link and its size."""
    try:
        return _store().put_stream(chunks, content_type=state_store.CSV_CONTENT_TYPE,
                                   url_ttl_seconds=PULL_URL_TTL_SECONDS)
    except state_store.ObjectHostingUnavailable as e:
        raise reply.Refusal(str(e)) from None


def hand_out_json(data: object) -> state_store.HostedObject:
    """Store a JSON document behind a signed link valid as long as the CSV links."""
    body = json.dumps(data, ensure_ascii=False, indent=1).encode()
    try:
        hosted, _ = _store().put_stream(iter([body]), content_type=state_store.JSON_CONTENT_TYPE,
                                        url_ttl_seconds=PULL_URL_TTL_SECONDS)
    except state_store.ObjectHostingUnavailable as e:
        raise reply.Refusal(str(e)) from None
    return hosted


def handle_of(upload: str) -> str:
    """The object name upload_slot returned, or the upload URL itself."""
    upload = upload.strip()
    if m := _STORAGE_URL.match(upload):
        upload = m.group(1)
    upload = upload.removeprefix("memory://").split("?")[0]
    if not _HANDLE.match(upload):
        raise reply.Refusal(f"{upload!r} is not an upload handle. Pass the handle upload_slot "
                            f"returned (gsheet-mcp/<name>.csv).")
    return upload


def read_upload(upload: str) -> tuple[str, bytes]:
    handle = handle_of(upload)
    data = _store().get_object(handle)
    if data is None:
        raise reply.Refusal(
            f"Nothing was uploaded at {handle}, or it is gone (uploads are deleted within a "
            f"day). Upload again; if the upload URL expired, call upload_slot for a fresh one.")
    return handle, data
