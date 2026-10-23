# Copied from google-docs-mcp@2fb4652 tests/conftest.py; renamed, Docs fakes swapped for the fake Sheets API, fake auth added.
"""Shared fixtures: the fake Sheets API, a fake signed-in caller, the in-memory
state store, an MCP client."""
from __future__ import annotations

import io

import pytest
from fastmcp import Client
from fastmcp.server.auth.auth import AccessToken
from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser

from gsheet_mcp import http_mode, sheets_api, state_store
from tests.fake_sheets import FakeDrive, FakeSheets

CALLER_EMAIL = "canary@example.com"
TOKEN_EXPIRY = 2_000_000_000


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch, offline) -> FakeSheets:
    """Put FakeSheets, holding the canary as `canary`, where the real Sheets and
    Drive clients and the export link would be."""
    api = FakeSheets()
    api.load_canary()
    monkeypatch.setattr(sheets_api, "_sheets_service", lambda: api)
    monkeypatch.setattr(sheets_api, "_drive_service", lambda: FakeDrive(api))
    monkeypatch.setattr(sheets_api, "_download", api.download)
    monkeypatch.setattr(sheets_api, "_open_download",
                        lambda url, timeout: io.BytesIO(api.download(url, timeout)))
    return api


@pytest.fixture(autouse=True)
def offline(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """No test reaches Google (the `fake` fixture installs FakeSheets over this),
    and no test waits out a real backoff; the waits asked for are returned."""
    def no_service():
        raise AssertionError("a test reached the Sheets or Drive API without FakeSheets")

    def no_download(url: str, timeout: float) -> bytes:
        raise AssertionError(f"a test reached an export link without FakeSheets: {url}")

    waits: list[float] = []
    monkeypatch.setattr(sheets_api, "_sheets_service", no_service)
    monkeypatch.setattr(sheets_api, "_drive_service", no_service)
    monkeypatch.setattr(sheets_api, "_download", no_download)
    monkeypatch.setattr(sheets_api, "_open_download", no_download)
    monkeypatch.setattr(sheets_api, "_sleep", waits.append)
    return waits


@pytest.fixture
def signed_in():
    """Stand in for the sign-in proxy: the request carries a caller's Google
    token with every scope gsheet-mcp asks for, as the auth store would hand back."""
    token = AccessToken(token="caller-secret-token", client_id="test-client",
                        scopes=list(http_mode.GOOGLE_SCOPES), expires_at=TOKEN_EXPIRY,
                        claims={"sub": "1234", "email": CALLER_EMAIL})
    reset = auth_context_var.set(AuthenticatedUser(token))
    try:
        yield token
    finally:
        auth_context_var.reset(reset)


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> state_store.MemoryStateStore:
    memory = state_store.MemoryStateStore()
    monkeypatch.setattr(state_store, "_current", memory)
    return memory


@pytest.fixture
async def client(store):
    """An MCP client talking to the server in process, as an agent's client would."""
    from gsheet_mcp.server import mcp

    async with Client(mcp) as c:
        yield c


async def call(client: Client, tool: str, **arguments) -> tuple[str, bool]:
    """Call a tool the way an agent does; return (all text blocks joined, is_error).

    Fails if the reply carries structuredContent, which Claude Code would show
    instead of the text.
    """
    result = await client.call_tool(tool, arguments, raise_on_error=False)
    assert result.structured_content is None, f"{tool} replied with structuredContent"
    text = "\n".join(block.text for block in result.content if block.type == "text")
    return text, result.is_error
