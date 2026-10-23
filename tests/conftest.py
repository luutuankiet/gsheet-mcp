# Copied from google-docs-mcp@2fb4652 tests/conftest.py; renamed, Docs fakes dropped, fake auth added.
"""Shared fixtures: a fake signed-in caller, the in-memory state store, an MCP client."""
from __future__ import annotations

import pytest
from fastmcp import Client
from fastmcp.server.auth.auth import AccessToken
from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser

from gsheet_mcp import http_mode, state_store

CALLER_EMAIL = "canary@example.com"
TOKEN_EXPIRY = 2_000_000_000


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
