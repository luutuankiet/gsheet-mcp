"""The state store's keyed cache, which holds renders: bytes under a key until a deadline."""
from __future__ import annotations

import pytest

from gsheet_mcp import state_store
from tests.unit.test_state_store import Clock


class _Blob:
    def __init__(self, bucket, name):
        self.bucket, self.name, self.metadata = bucket, name, None

    def upload_from_string(self, data, content_type):
        self.bucket.objects[self.name] = (data, dict(self.metadata or {}))

    def download_as_bytes(self):
        return self.bucket.objects[self.name][0]


class _Bucket:
    def __init__(self):
        self.objects: dict[str, tuple[bytes, dict]] = {}

    def blob(self, name):
        return _Blob(self, name)

    def get_blob(self, name):
        if name not in self.objects:
            return None
        blob = _Blob(self, name)
        blob.metadata = self.objects[name][1]
        return blob


class _Storage:
    def __init__(self):
        self.the_bucket = _Bucket()

    def bucket(self, name):
        return self.the_bucket


@pytest.fixture(params=["memory", "hosted"])
def both(request):
    clock = Clock()
    if request.param == "memory":
        return state_store.MemoryStateStore(clock=clock), clock
    return state_store.HostedStateStore(database="d", bucket="b", clock=clock,
                                        storage_client=_Storage()), clock


def test_a_cached_entry_is_served_until_its_deadline(both):
    store, clock = both
    assert store.get_cached("render|a") is None
    store.put_cached("render|a", b"%PDF", ttl_seconds=600)
    assert store.get_cached("render|a") == b"%PDF"
    assert store.get_cached("render|b") is None
    clock.now += 599
    assert store.get_cached("render|a") == b"%PDF"
    clock.now += 1
    assert store.get_cached("render|a") is None


def test_hosted_cache_entries_live_under_the_object_prefix():
    storage = _Storage()
    store = state_store.HostedStateStore(database="d", bucket="b", clock=Clock(),
                                        storage_client=storage)
    store.put_cached("render|caller|sheet|101|B2:F8", b"x", ttl_seconds=1)
    [name] = storage.the_bucket.objects
    assert name.startswith(state_store.OBJECT_PREFIX + "cache/")
    assert "B2:F8" not in name
