# Copied from google-docs-mcp@2fb4652 src/<package>/state_store.py; renamed, doc_id is spreadsheet_id.
"""The server's short-lived state: dry-run plans and short-lived objects.

One interface, two implementations. Tests use `MemoryStateStore`;
`serve-http` uses `HostedStateStore`, which keeps plans in the Firestore
database the auth store already uses and objects (scans, renders, pulls, uploads)
in a GCS bucket behind V4 signed URLs, so a plan made on one Cloud Run
instance can be applied on another.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

PLAN_TTL_SECONDS = 3600
PLANS_COLLECTION = "gsheet_mcp__plans"
OBJECT_PREFIX = "gsheet-mcp/"
OBJECT_URL_TTL_SECONDS = 300
UPLOAD_SLOT_TTL_SECONDS = 24 * 3600
BUNDLE_CONTENT_TYPE = "application/gzip"   # what the gsheet CLI uploads; the PUT is signed for it
CSV_CONTENT_TYPE = "text/csv"              # pulls hand CSV out; CLI query results come back as CSV
_EXTENSIONS = {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif",
               "application/gzip": ".tar.gz", CSV_CONTENT_TYPE: ".csv"}
_CLOUD_PLATFORM = "https://www.googleapis.com/auth/cloud-platform"

_NO_BUCKET_MESSAGE = (
    "GSHEET_MCP_BUCKET is not set on this server, so it has nowhere to "
    "keep a short-lived file. Ask the deployer to set it.")


class ObjectHostingUnavailable(RuntimeError):
    """No bucket to keep a short-lived object in."""


class ObjectSigningUnavailable(ObjectHostingUnavailable):
    """The server's credentials can neither sign a URL nor name a service account to sign as."""


@dataclass(frozen=True)
class HostedObject:
    url: str     # short-lived URL the object can be fetched from
    handle: str  # what delete_object takes


@dataclass(frozen=True)
class UploadSlot:
    url: str            # V4 signed PUT, Content-Type application/gzip
    handle: str         # the object name; what the uploading tool takes back
    expires_at: float   # epoch seconds


def _new_plan_id() -> str:
    return secrets.token_urlsafe(18)


def _new_handle(content_type: str) -> str:
    return f"{OBJECT_PREFIX}{secrets.token_urlsafe(24)}{_EXTENSIONS.get(content_type, '')}"


def _cache_name(key: str) -> str:
    """A cache entry's object name: the key hashed, so a key may hold any text."""
    return f"{OBJECT_PREFIX}cache/{hashlib.sha256(key.encode()).hexdigest()}"


def _plan_record(payload, spreadsheet_id, caller, expires_at) -> dict[str, Any]:
    # Payload as JSON text: Firestore rejects arrays nested in arrays.
    return {"payload": json.dumps(payload), "spreadsheet_id": spreadsheet_id, "caller": caller or "",
            "expires_at": expires_at}


def _plan_payload(record, spreadsheet_id, caller, now: float) -> dict | None:
    """The plan, or None when it is expired or belongs to another spreadsheet or caller."""
    if (record is None or record["expires_at"] <= now or record["spreadsheet_id"] != spreadsheet_id
            or record["caller"] != (caller or "")):
        return None
    return json.loads(record["payload"])


class MemoryStateStore:
    """Process memory, for tests: plans and objects, with fake `memory://` URLs."""

    def __init__(self, clock=time.time):
        self._clock = clock
        self._plans: dict[str, dict[str, Any]] = {}
        self.objects: dict[str, tuple[bytes, str]] = {}
        self.cached: dict[str, tuple[bytes, float]] = {}

    def save_plan(self, payload: dict[str, Any], *, spreadsheet_id: str, caller: str | None,
                  ttl_seconds: float = PLAN_TTL_SECONDS) -> str:
        plan_id = _new_plan_id()
        self._plans[plan_id] = _plan_record(payload, spreadsheet_id, caller,
                                            self._clock() + ttl_seconds)
        return plan_id

    def load_plan(self, plan_id: str, *, spreadsheet_id: str, caller: str | None) -> dict | None:
        return _plan_payload(self._plans.get(plan_id), spreadsheet_id, caller, self._clock())

    def expire_plan(self, plan_id: str) -> None:
        self._plans.pop(plan_id, None)

    def check_object_hosting(self) -> None:
        return None

    def put_object(self, data: bytes, *, content_type: str,
                   url_ttl_seconds: float = OBJECT_URL_TTL_SECONDS) -> HostedObject:
        handle = _new_handle(content_type)
        self.objects[handle] = (data, content_type)
        return HostedObject(url=f"memory://{handle}", handle=handle)

    def put_stream(self, chunks: Iterator[bytes], *, content_type: str,
                   url_ttl_seconds: float = OBJECT_URL_TTL_SECONDS) -> tuple[HostedObject, int]:
        """`put_object` from a stream of chunks; also returns the byte count."""
        data = b"".join(chunks)
        return self.put_object(data, content_type=content_type,
                               url_ttl_seconds=url_ttl_seconds), len(data)

    def get_object(self, handle: str) -> bytes | None:
        hit = self.objects.get(handle)
        return hit[0] if hit else None

    def upload_slot(self, *, ttl_seconds: float = UPLOAD_SLOT_TTL_SECONDS,
                    content_type: str = BUNDLE_CONTENT_TYPE) -> UploadSlot:
        """A slot nothing is uploaded to yet; a test PUTs by setting `objects[handle]`."""
        handle = _new_handle(content_type)
        expires_at = self._clock() + ttl_seconds
        return UploadSlot(url=f"memory://{handle}?expires={int(expires_at)}", handle=handle,
                          expires_at=expires_at)

    def delete_object(self, handle: str) -> None:
        self.objects.pop(handle, None)

    # ---- keyed cache (renders, scans): bytes under a key, until a deadline

    def put_cached(self, key: str, data: bytes, *, ttl_seconds: float) -> None:
        self.cached[_cache_name(key)] = (data, self._clock() + ttl_seconds)

    def get_cached(self, key: str) -> bytes | None:
        """The bytes put under `key`, or None when there are none or they expired."""
        hit = self.cached.get(_cache_name(key))
        return hit[0] if hit and hit[1] > self._clock() else None

    def drop_cached(self, key: str) -> None:
        self.cached.pop(_cache_name(key), None)


class HostedStateStore:
    """`serve-http`: plans in Firestore, short-lived objects in a GCS bucket.

    Plans go in the named database the auth store uses, collection
    `gsheet_mcp__plans`. `expires_at` is a Firestore timestamp so a TTL
    policy can delete old plans; until it does, load treats them as not found.
    Clients are built on first use, so plans work without a bucket and objects
    without Firestore.
    """

    def __init__(self, *, database: str, bucket: str | None, clock=time.time,
                 firestore_client=None, storage_client=None):
        self.database = database
        self.bucket = bucket
        self._clock = clock
        self._firestore = firestore_client
        self._storage = storage_client

    # ---- plans

    def _plans(self):
        if self._firestore is None:
            from google.cloud import firestore

            self._firestore = firestore.Client(database=self.database)
        return self._firestore.collection(PLANS_COLLECTION)

    def save_plan(self, payload: dict[str, Any], *, spreadsheet_id: str, caller: str | None,
                  ttl_seconds: float = PLAN_TTL_SECONDS) -> str:
        plan_id = _new_plan_id()
        record = _plan_record(payload, spreadsheet_id, caller, self._clock() + ttl_seconds)
        record["expires_at"] = datetime.fromtimestamp(record["expires_at"], tz=UTC)
        self._plans().document(plan_id).set(record)
        return plan_id

    def load_plan(self, plan_id: str, *, spreadsheet_id: str, caller: str | None) -> dict | None:
        snapshot = self._plans().document(plan_id).get()
        if not snapshot.exists:
            return None
        record = snapshot.to_dict()
        record["expires_at"] = record["expires_at"].timestamp()
        return _plan_payload(record, spreadsheet_id, caller, self._clock())

    def expire_plan(self, plan_id: str) -> None:
        self._plans().document(plan_id).delete()

    # ---- objects

    def check_object_hosting(self) -> None:
        """Raise ObjectHostingUnavailable now, before any work, when there is no bucket."""
        if not self.bucket:
            raise ObjectHostingUnavailable(_NO_BUCKET_MESSAGE)

    def _bucket(self):
        self.check_object_hosting()
        if self._storage is None:
            from google.cloud import storage

            self._storage = storage.Client()
        return self._storage.bucket(self.bucket)

    def put_object(self, data: bytes, *, content_type: str,
                   url_ttl_seconds: float = OBJECT_URL_TTL_SECONDS) -> HostedObject:
        """Upload under a random name; return a V4 signed GET URL."""
        bucket = self._bucket()
        handle = _new_handle(content_type)
        blob = bucket.blob(handle)
        blob.upload_from_string(data, content_type=content_type)
        try:
            url = blob.generate_signed_url(
                version="v4", method="GET",
                expiration=timedelta(seconds=url_ttl_seconds),
                **_signing_kwargs(self._storage))
        except Exception:
            # The caller never gets a handle, so nothing else would delete it.
            blob.delete()
            raise
        return HostedObject(url=url, handle=handle)

    def put_stream(self, chunks: Iterator[bytes], *, content_type: str,
                   url_ttl_seconds: float = OBJECT_URL_TTL_SECONDS) -> tuple[HostedObject, int]:
        """Write chunks straight into a new object as they come (a resumable
        upload, never the whole object in memory); return a V4 signed GET URL
        and the byte count."""
        bucket = self._bucket()
        handle = _new_handle(content_type)
        blob = bucket.blob(handle)
        size = 0
        try:
            with blob.open("wb", content_type=content_type) as out:
                for chunk in chunks:
                    out.write(chunk)
                    size += len(chunk)
            url = blob.generate_signed_url(
                version="v4", method="GET",
                expiration=timedelta(seconds=url_ttl_seconds),
                **_signing_kwargs(self._storage))
        except Exception:
            blob.delete()
            raise
        return HostedObject(url=url, handle=handle), size

    def get_object(self, handle: str) -> bytes | None:
        """The object's bytes, or None when there is none by that name."""
        from google.api_core.exceptions import NotFound

        try:
            return self._bucket().blob(handle).download_as_bytes()
        except NotFound:
            return None

    def upload_slot(self, *, ttl_seconds: float = UPLOAD_SLOT_TTL_SECONDS,
                    content_type: str = BUNDLE_CONTENT_TYPE) -> UploadSlot:
        """A V4 signed PUT for a new object name, for the gsheet CLI to upload to.

        Signed for `content_type` (a bundle or `text/csv`), the header the gsheet CLI
        must send; GCS answers 403 SignatureDoesNotMatch to any other. The URL is
        on storage.googleapis.com, path style, like every URL this store signs.
        """
        bucket = self._bucket()
        handle = _new_handle(content_type)
        expires_at = self._clock() + ttl_seconds
        url = bucket.blob(handle).generate_signed_url(
            version="v4", method="PUT", expiration=timedelta(seconds=ttl_seconds),
            content_type=content_type, **_signing_kwargs(self._storage))
        return UploadSlot(url=url, handle=handle, expires_at=expires_at)

    def delete_object(self, handle: str) -> None:
        from google.api_core.exceptions import NotFound

        try:
            self._bucket().blob(handle).delete()
        except NotFound:
            pass

    # ---- keyed cache (renders, scans): bytes under a key, until a deadline

    def put_cached(self, key: str, data: bytes, *, ttl_seconds: float) -> None:
        """One object per key, its deadline in the object's metadata. The
        bucket's one-day lifecycle deletes it in the end."""
        blob = self._bucket().blob(_cache_name(key))
        blob.metadata = {"expires_at": str(self._clock() + ttl_seconds)}
        blob.upload_from_string(data, content_type="application/octet-stream")

    def get_cached(self, key: str) -> bytes | None:
        """The bytes put under `key`, or None when there are none or they expired."""
        from google.api_core.exceptions import NotFound

        blob = self._bucket().get_blob(_cache_name(key))
        if blob is None or float((blob.metadata or {}).get("expires_at", 0)) <= self._clock():
            return None
        try:
            return blob.download_as_bytes()
        except NotFound:
            return None

    def drop_cached(self, key: str) -> None:
        from google.api_core.exceptions import NotFound

        try:
            self._bucket().blob(_cache_name(key)).delete()
        except NotFound:
            pass


def _signing_kwargs(storage_client) -> dict[str, str]:
    """How to sign a URL with whatever credentials the storage client holds.

    A key file or impersonated credentials sign locally. Cloud Run's
    metadata-server credentials hold no key, so signing goes through the IAM
    signBlob API as the service account itself, which needs
    roles/iam.serviceAccountTokenCreator on that account.
    """
    from google.auth.credentials import Signing
    from google.auth.transport.requests import Request

    creds = storage_client._credentials
    if isinstance(creds, Signing):
        return {}
    if not hasattr(creds, "service_account_email"):
        raise ObjectSigningUnavailable(
            "This server cannot sign a URL: it runs with credentials that hold no "
            "private key and belong to no service account (for example a user "
            "account). Run it as a service account with "
            "roles/iam.serviceAccountTokenCreator on itself.")
    # The storage client's token is scoped to storage only; signBlob refuses it.
    signer = creds.with_scopes([_CLOUD_PLATFORM]) if hasattr(creds, "with_scopes") else creds
    if not signer.valid:
        signer.refresh(Request())
    return {"service_account_email": signer.service_account_email,
            "access_token": signer.token}


_current: MemoryStateStore | HostedStateStore = MemoryStateStore()


def current() -> MemoryStateStore | HostedStateStore:
    """The store tools use: in memory until `serve-http` installs a hosted one."""
    return _current


def install(store: MemoryStateStore | HostedStateStore) -> None:
    """Called once by `serve-http` before the port opens, and by tests."""
    global _current
    _current = store
