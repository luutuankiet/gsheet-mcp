"""The scaffold's one tool, `auth_status`, over HTTP and through the tool layer."""
from __future__ import annotations

import httpx2 as httpx

from gsheet_mcp import http_mode
from tests.conftest import CALLER_EMAIL, call

HEADERS = {"Accept": "application/json, text/event-stream"}


async def test_http_mode_lists_auth_status_with_its_annotations():
    from gsheet_mcp.server import mcp

    app = mcp.http_app(stateless_http=True, json_response=True)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as c:
            r = await c.post("/mcp", headers=HEADERS,
                             json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    tools = {t["name"]: t for t in r.json()["result"]["tools"]}
    assert tools["auth_status"]["annotations"] == {
        "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
        "openWorldHint": True}
    assert "outputSchema" not in tools["auth_status"]


async def test_auth_status_reports_the_caller_and_the_four_scopes(signed_in, client):
    text, is_error = await call(client, "auth_status")
    assert not is_error
    assert text.splitlines()[0] == f"signed in as {CALLER_EMAIL}"
    assert "scopes (4):" in text
    for scope in http_mode.GOOGLE_SCOPES:
        assert f"  {scope}" in text
    assert "token expires 2033-05-18 03:33 UTC" in text
    assert signed_in.token not in text


async def test_auth_status_without_a_caller_says_to_sign_in(client):
    text, is_error = await call(client, "auth_status")
    assert not is_error
    assert text.startswith("not signed in")
