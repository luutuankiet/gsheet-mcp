"""A per-spreadsheet cache generation, so one write clears every cached scan
and render of that spreadsheet (spec, "Landscape scan" and "render_range").

    current(spreadsheet_id) -> the token cache keys fold in
    bump(spreadsheet_id)    -> called by every write through our tools

Cache keys are hashed in the state store, so they cannot be listed or
deleted by spreadsheet. Folding this token into each key does the same job:
after a write the old keys are never asked for again and expire on their own.

The token is random rather than a counter, so a token that expired from the
store and came back as the default can never match a key made after a write.
"""
from __future__ import annotations

import secrets

from . import state_store

DEFAULT = "0"
# Longer than every cache that folds the token in (scan and renders: 10 minutes),
# so a cache entry made before a write cannot outlive the token that hides it.
TTL_SECONDS = 30 * 60


def _key(spreadsheet_id: str) -> str:
    return f"generation|{spreadsheet_id}"


def current(spreadsheet_id: str) -> str:
    raw = state_store.current().get_cached(_key(spreadsheet_id))
    return raw.decode() if raw else DEFAULT


def bump(spreadsheet_id: str) -> None:
    state_store.current().put_cached(_key(spreadsheet_id), secrets.token_hex(8).encode(),
                                     ttl_seconds=TTL_SECONDS)
