# Copied from google-docs-mcp@2fb4652 src/<package>/http_mode.py; renamed, scopes swapped for spreadsheets and drive.
"""`gsheet-mcp serve-http`: settings check, sign-in proxy, auth store, runner.

Each caller signs in with Google through fastmcp's `GoogleProvider`;
gsheet-mcp issues its own server token and keeps every caller's Google
grant in a named Firestore database. Everything here is checked before the
port opens.
"""
from __future__ import annotations

import os
import re
import sys
import time
from dataclasses import dataclass
from typing import Any

SETTINGS_HELP = """\
gsheet-mcp serve-http: run gsheet-mcp as a remote MCP server over HTTP.

Each caller signs in with their own Google account. Settings come from
environment variables only:

  setting                          required  default
  GSHEET_MCP_GOOGLE_CLIENT_ID      yes       none
  GSHEET_MCP_GOOGLE_CLIENT_SECRET  yes       none
  GSHEET_MCP_BASE_URL              yes       none (the service's public https URL)
  GSHEET_MCP_FIRESTORE_DATABASE    yes       none (a database only gsheet-mcp uses)
  GSHEET_MCP_BUCKET                no        none (short-lived objects: scans, renders, pulls)
  PORT                             no        8080

The Google OAuth client's redirect URI must be <GSHEET_MCP_BASE_URL>/auth/callback.
"""

REDIRECT_PATH = "/auth/callback"
# Never narrowed on refresh: a grant missing one of these is refused at sign-in.
GOOGLE_SCOPES = [
    "openid",
    "email",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]
# The key-value store joins prefix and collection with "__": `gsheet_mcp__<collection>`.
COLLECTION_PREFIX = "gsheet_mcp"

_DB_ID_RE = re.compile(r"[a-z][a-z0-9-]{3,62}")
_REQUIRED = (
    "GSHEET_MCP_GOOGLE_CLIENT_ID",
    "GSHEET_MCP_GOOGLE_CLIENT_SECRET",
    "GSHEET_MCP_BASE_URL",
    "GSHEET_MCP_FIRESTORE_DATABASE",
)


@dataclass(frozen=True)
class Settings:
    client_id: str
    client_secret: str
    base_url: str
    firestore_database: str
    port: int
    bucket: str | None = None


def check_settings(env: dict[str, str]) -> tuple[Settings | None, list[tuple[str, str]]]:
    """Every problem at once, as (setting, reason). Never echoes a secret value."""
    problems: list[tuple[str, str]] = []
    values = {name: (env.get(name) or "").strip() for name in _REQUIRED}
    for name, value in values.items():
        if not value:
            problems.append((name, "missing"))

    base_url = values["GSHEET_MCP_BASE_URL"].rstrip("/")
    if base_url and not (
        base_url.startswith("https://")
        or re.match(r"http://localhost(:\d+)?$", base_url)
    ):
        problems.append(("GSHEET_MCP_BASE_URL",
                         "must start with https:// (http://localhost only for local testing)"))

    db = values["GSHEET_MCP_FIRESTORE_DATABASE"]
    if db == "(default)":
        problems.append(("GSHEET_MCP_FIRESTORE_DATABASE",
                         '"(default)" is not allowed; use a database only gsheet-mcp uses'))
    elif db and not _DB_ID_RE.fullmatch(db):
        problems.append(("GSHEET_MCP_FIRESTORE_DATABASE",
                         f'"{db}" is not a valid database id (lowercase letters, digits and '
                         "hyphens, 4 to 63 characters, starting with a letter)"))

    port_raw = (env.get("PORT") or "8080").strip()
    port = 0
    if port_raw.isdigit() and 0 < int(port_raw) < 65536:
        port = int(port_raw)
    else:
        problems.append(("PORT", "must be a port number"))

    if problems:
        return None, problems
    return Settings(
        client_id=values["GSHEET_MCP_GOOGLE_CLIENT_ID"],
        client_secret=values["GSHEET_MCP_GOOGLE_CLIENT_SECRET"],
        base_url=base_url,
        firestore_database=db,
        port=port,
        bucket=(env.get("GSHEET_MCP_BUCKET") or "").strip() or None,
    ), []


def format_problems(problems: list[tuple[str, str]]) -> str:
    noun = "setting needs" if len(problems) == 1 else "settings need"
    width = max(len(name) for name, _ in problems)
    lines = [f"gsheet-mcp serve-http: cannot start, {len(problems)} {noun} fixing:"]
    lines += [f"  {name.ljust(width)}  {reason}" for name, reason in problems]
    lines.append("Run `gsheet-mcp serve-http --help` for every setting.")
    return "\n".join(lines)


class ValidationCache:
    """Remembers a successful Google validation until that access token expires.

    Without it every tool call costs two Google round trips (tokeninfo and
    userinfo). The price: a revoked caller keeps working for up to an hour.
    """

    def __init__(self, verify, clock=time.time):
        self._verify = verify
        self._clock = clock
        self._entries: dict[str, Any] = {}

    async def verify_token(self, token: str):
        now = self._clock()
        hit = self._entries.get(token)
        if hit is not None and hit.expires_at and hit.expires_at > now:
            return hit
        validated = await self._verify(token)
        # Expired entries are dropped on each write so the cache stays small.
        self._entries = {k: v for k, v in self._entries.items() if v.expires_at > now}
        if validated is not None and validated.expires_at and validated.expires_at > now:
            self._entries[token] = validated
        return validated


def build_auth_store(settings: Settings):
    """The auth store: a named Firestore database, collections prefixed `gsheet_mcp__`."""
    from key_value.aio.stores.firestore import FirestoreStore
    from key_value.aio.stores.firestore.store import (
        FirestoreV1CollectionSanitizationStrategy,
        FirestoreV1KeySanitizationStrategy,
    )
    from key_value.aio.wrappers.prefix_collections import PrefixCollectionsWrapper

    # Off by default in the store. Without it a client whose ID is a URL (CIMD,
    # e.g. claude.ai) fails every lookup: Firestore document IDs can't hold "/".
    store = FirestoreStore(
        database=settings.firestore_database,
        key_sanitization_strategy=FirestoreV1KeySanitizationStrategy(),
        collection_sanitization_strategy=FirestoreV1CollectionSanitizationStrategy(),
    )
    return PrefixCollectionsWrapper(store, prefix=COLLECTION_PREFIX)


def build_state_store(settings: Settings):
    """Plans in the auth database, short-lived objects in GSHEET_MCP_BUCKET (if set)."""
    from .state_store import HostedStateStore

    return HostedStateStore(database=settings.firestore_database, bucket=settings.bucket)


def build_provider(settings: Settings, client_storage):
    from fastmcp.server.auth.providers.google import GoogleProvider

    provider = GoogleProvider(
        client_id=settings.client_id,
        client_secret=settings.client_secret,
        base_url=settings.base_url,
        redirect_path=REDIRECT_PATH,
        required_scopes=GOOGLE_SCOPES,
        client_storage=client_storage,
    )
    # fastmcp has no validation cache; it calls this verifier on every request.
    # Private attribute, so fastmcp stays pinned below 5 and a test guards it.
    provider._token_validator.verify_token = ValidationCache(
        provider._token_validator.verify_token).verify_token
    return provider


def serve(env: dict[str, str] | None = None) -> int:
    settings, problems = check_settings(dict(os.environ) if env is None else env)
    if settings is None:
        print(format_problems(problems), file=sys.stderr)
        return 2

    from . import state_store
    from .server import mcp

    mcp.auth = build_provider(settings, build_auth_store(settings))
    state_store.install(build_state_store(settings))
    mcp.run(
        transport="http",
        host="0.0.0.0",
        port=settings.port,
        stateless_http=True,
        json_response=True,
        show_banner=False,
    )
    return 0
