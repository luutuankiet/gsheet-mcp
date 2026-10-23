"""`put_stream` on the hosted store: chunks go straight into one GCS object,
and the object is not left behind when the link cannot be signed."""
from __future__ import annotations

import io

import pytest
from google.auth.credentials import Signing

from gsheet_mcp import state_store


class _Writer(io.BytesIO):
    def __init__(self, blob):
        super().__init__()
        self.blob = blob

    def close(self):
        self.blob.bucket.objects[self.blob.name] = self.getvalue()
        super().close()


class _Blob:
    def __init__(self, bucket, name):
        self.bucket, self.name = bucket, name

    def open(self, mode, content_type):
        assert mode == "wb" and content_type == "text/csv"
        return _Writer(self)

    def generate_signed_url(self, **kw):
        if not self.bucket.can_sign:
            raise PermissionError("iam.serviceAccounts.signBlob denied")
        return f"https://storage.googleapis.com/objects/{self.name}?X-Goog-Signature=x"

    def delete(self):
        self.bucket.objects.pop(self.name, None)


class _Storage:
    def __init__(self, can_sign: bool):
        self.objects: dict[str, bytes] = {}
        self.can_sign = can_sign
        self._credentials = type("KeyCredentials", (Signing,), {
            "sign_bytes": None, "signer": None, "signer_email": None})()

    def bucket(self, name):
        return self

    def blob(self, name):
        return _Blob(self, name)


def _store(storage):
    return state_store.HostedStateStore(database="db", bucket="objects", storage_client=storage)


def test_put_stream_writes_every_chunk_into_one_object_and_signs_a_link():
    storage = _Storage(can_sign=True)
    hosted, size = _store(storage).put_stream(iter([b"a,b\n", b"1,2\n"]),
                                              content_type="text/csv")
    assert size == 8 and storage.objects == {hosted.handle: b"a,b\n1,2\n"}
    assert hosted.handle.endswith(".csv") and hosted.url.startswith("https://storage.")


def test_put_stream_deletes_the_object_when_signing_fails():
    storage = _Storage(can_sign=False)
    with pytest.raises(PermissionError, match="signBlob"):
        _store(storage).put_stream(iter([b"a,b\n"]), content_type="text/csv")
    assert storage.objects == {}
