# Copied from google-docs-mcp@2fb4652 tests/unit/test_state_store.py; renamed, doc_id is spreadsheet_id.
"""The server state store: dry-run plans and short-lived objects.

These behaviour tests run against the in-memory store here. The Firestore store
runs the same tests against the emulator in tests/integration/, which swaps the
`make_store` fixture and imports everything else from this module.
"""
from __future__ import annotations

import pytest

from gsheet_mcp import state_store


class Clock:
    def __init__(self, now: float = 1_800_000_000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def make_store(clock):
    return lambda: state_store.MemoryStateStore(clock=clock)


@pytest.fixture
def store(make_store):
    return make_store()


PLAN = {"script": "__push({op: 'write', range: 'A1'})",
        "input": {"rows": [[1, 2], [3]]}, "options": {"tab": "1"}}


def test_saved_plan_loads_back_for_same_doc_and_caller(store):
    plan_id = store.save_plan(PLAN, spreadsheet_id="sheet-a", caller="a@example.com")
    assert store.load_plan(plan_id, spreadsheet_id="sheet-a", caller="a@example.com") == PLAN


def test_plan_expires_after_its_time_to_live(store, clock):
    plan_id = store.save_plan(PLAN, spreadsheet_id="sheet-a", caller="a@example.com", ttl_seconds=60)
    clock.now += 59
    assert store.load_plan(plan_id, spreadsheet_id="sheet-a", caller="a@example.com") == PLAN
    clock.now += 2
    assert store.load_plan(plan_id, spreadsheet_id="sheet-a", caller="a@example.com") is None


def test_plans_live_one_hour_by_default(store, clock):
    plan_id = store.save_plan(PLAN, spreadsheet_id="sheet-a", caller=None)
    clock.now += 3599
    assert store.load_plan(plan_id, spreadsheet_id="sheet-a", caller=None) == PLAN
    clock.now += 2
    assert store.load_plan(plan_id, spreadsheet_id="sheet-a", caller=None) is None


def test_plan_for_another_doc_is_not_found(store):
    plan_id = store.save_plan(PLAN, spreadsheet_id="sheet-a", caller="a@example.com")
    assert store.load_plan(plan_id, spreadsheet_id="sheet-b", caller="a@example.com") is None


def test_plan_made_by_another_caller_is_not_found(store):
    plan_id = store.save_plan(PLAN, spreadsheet_id="sheet-a", caller="a@example.com")
    assert store.load_plan(plan_id, spreadsheet_id="sheet-a", caller="b@example.com") is None
    assert store.load_plan(plan_id, spreadsheet_id="sheet-a", caller=None) is None


def test_unknown_plan_id_is_not_found(store):
    assert store.load_plan("no-such-plan", spreadsheet_id="sheet-a", caller=None) is None


def test_expired_plan_is_gone_and_expiring_twice_is_harmless(store):
    plan_id = store.save_plan(PLAN, spreadsheet_id="sheet-a", caller=None)
    store.expire_plan(plan_id)
    assert store.load_plan(plan_id, spreadsheet_id="sheet-a", caller=None) is None
    store.expire_plan(plan_id)


def test_each_plan_gets_its_own_id(store):
    first = store.save_plan(PLAN, spreadsheet_id="sheet-a", caller=None)
    second = store.save_plan({**PLAN, "script": "other"}, spreadsheet_id="sheet-a", caller=None)
    assert first != second
    assert store.load_plan(second, spreadsheet_id="sheet-a", caller=None)["script"] == "other"


# ---- objects --------------------------------------------------------


def test_memory_store_keeps_objects_until_deleted(clock):
    store = state_store.MemoryStateStore(clock=clock)
    hosted = store.put_object(b"bundle", content_type="application/gzip")
    assert hosted.url.startswith("memory://") and hosted.handle.endswith(".tar.gz")
    assert store.get_object(hosted.handle) == b"bundle"
    store.delete_object(hosted.handle)
    assert store.get_object(hosted.handle) is None
    store.delete_object(hosted.handle)


class _FakeBlob:
    def __init__(self, bucket, name):
        self.bucket, self.name = bucket, name

    def upload_from_string(self, data, content_type):
        self.bucket.objects[self.name] = data

    def generate_signed_url(self, **kw):
        raise PermissionError("iam.serviceAccounts.signBlob denied")

    def delete(self):
        del self.bucket.objects[self.name]


class _FakeBucket:
    def __init__(self):
        self.objects: dict[str, bytes] = {}

    def blob(self, name):
        return _FakeBlob(self, name)


class _FakeStorage:
    """GCS at the network boundary: holds objects; signing is refused."""

    class _credentials:  # same name as the real client's attribute signing reads
        valid = True
        service_account_email = "runner@example.iam.gserviceaccount.com"
        token = "t"

    def __init__(self):
        self.the_bucket = _FakeBucket()

    def bucket(self, name):
        return self.the_bucket


def _hosted(clock, storage=None, bucket="objects"):
    return state_store.HostedStateStore(database="gsheet-mcp-auth", bucket=bucket,
                                        clock=clock, storage_client=storage)


def test_object_is_not_left_behind_when_signing_fails(clock):
    storage = _FakeStorage()
    with pytest.raises(PermissionError, match="signBlob"):
        _hosted(clock, storage).put_object(b"\x89PNG", content_type="image/png")
    assert storage.the_bucket.objects == {}


def test_credentials_that_cannot_sign_get_a_clear_error(clock):
    # A user account's credentials: no private key and no service account to sign as.
    storage = _FakeStorage()
    storage._credentials = type("UserCredentials", (), {"valid": True, "token": "t"})
    with pytest.raises(state_store.ObjectSigningUnavailable,
                       match=r"service account.*serviceAccountTokenCreator"):
        _hosted(clock, storage).put_object(b"\x89PNG", content_type="image/png")
    assert storage.the_bucket.objects == {}


def test_metadata_credentials_sign_with_a_cloud_platform_token(clock):
    # Cloud Run's storage client holds a token scoped to storage only; the IAM
    # signBlob call refuses it with ACCESS_TOKEN_SCOPE_INSUFFICIENT.
    class MetadataCredentials:
        valid, token = True, "storage-only"
        service_account_email = "runner@example.iam.gserviceaccount.com"

        def __init__(self, scopes=None):
            self.scopes = scopes

        def with_scopes(self, scopes):
            return ScopedCredentials(scopes)

    class ScopedCredentials(MetadataCredentials):
        valid, token = False, None

        def refresh(self, request):
            self.valid, self.token = True, f"token for {' '.join(self.scopes)}"

    signed = {}

    class SigningBlob(_FakeBlob):
        def generate_signed_url(self, **kw):
            signed.update(kw)
            return "https://storage.googleapis.com/objects/x?X-Goog-Signature=s"

    storage = _FakeStorage()
    storage._credentials = MetadataCredentials()
    storage.the_bucket.blob = lambda name: SigningBlob(storage.the_bucket, name)
    hosted = _hosted(clock, storage).put_object(b"\x89PNG", content_type="image/png")
    assert hosted.url.startswith("https://storage.googleapis.com/")
    assert signed["service_account_email"] == "runner@example.iam.gserviceaccount.com"
    assert signed["access_token"] == "token for https://www.googleapis.com/auth/cloud-platform"


def test_hosted_store_without_a_bucket_names_the_missing_setting(clock):
    store = _hosted(clock, bucket=None)
    with pytest.raises(state_store.ObjectHostingUnavailable, match="GSHEET_MCP_BUCKET"):
        store.put_object(b"\x89PNG", content_type="image/png")
    with pytest.raises(state_store.ObjectHostingUnavailable, match="GSHEET_MCP_BUCKET"):
        store.delete_object("any-handle")


# ---- upload slots ------------------------------------------------------


def _key_signed_storage():
    """A real storage client holding a service account key made up here, so
    URLs are signed locally by the real library and nothing touches the network."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from google.cloud import storage
    from google.oauth2 import service_account

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption()).decode()
    creds = service_account.Credentials.from_service_account_info({
        "type": "service_account", "project_id": "p", "private_key_id": "k",
        "private_key": pem, "client_email": "runner@p.iam.gserviceaccount.com",
        "token_uri": "https://oauth2.googleapis.com/token"})
    return storage.Client(project="p", credentials=creds)


def test_upload_slot_is_a_plain_gcs_put_signed_for_gzip_for_24_hours(clock):
    from urllib.parse import parse_qs, urlsplit

    slot = _hosted(clock, _key_signed_storage(), bucket="state-bucket").upload_slot()
    url = urlsplit(slot.url)
    query = parse_qs(url.query)
    assert url.scheme == "https" and url.netloc == "storage.googleapis.com"
    # The CLI sends the object name back as the handle.
    assert url.path == f"/state-bucket/{slot.handle}"
    assert slot.handle.startswith("gsheet-mcp/") and slot.handle.endswith(".tar.gz")
    assert query["X-Goog-Expires"] == ["86400"]
    assert "content-type" in query["X-Goog-SignedHeaders"][0].split(";")
    assert slot.expires_at == clock.now + 86400


def test_memory_store_slots_hold_what_is_put_there(clock):
    store = state_store.MemoryStateStore(clock=clock)
    slot = store.upload_slot()
    assert store.get_object(slot.handle) is None
    store.objects[slot.handle] = (b"bundle", "application/gzip")
    assert store.get_object(slot.handle) == b"bundle"
    assert slot.expires_at == clock.now + 86400
