# Copied from google-docs-mcp@2fb4652 tests/unit/test_http_mode.py; renamed, Docs client cases dropped.
"""HTTP mode: the per-caller credential seam, the settings check, the CLI and
the fastmcp 4 behaviour we depend on. No network.
"""
from __future__ import annotations

import json
import sys

import httpx2 as httpx
import pytest
from fastmcp.server.auth.auth import AccessToken
from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser

from gsheet_mcp import auth, cli, http_mode

GOOD_ENV = {
    "GSHEET_MCP_GOOGLE_CLIENT_ID": "id.apps.googleusercontent.com",
    "GSHEET_MCP_GOOGLE_CLIENT_SECRET": "secret",
    "GSHEET_MCP_BASE_URL": "https://gsheet-mcp.example.run.app/",
    "GSHEET_MCP_FIRESTORE_DATABASE": "gsheet-mcp-auth",
}


def _caller(token: str, **claims) -> AuthenticatedUser:
    return AuthenticatedUser(AccessToken(token=token, client_id="c", scopes=["openid"],
                                         expires_at=2_000_000_000, claims=claims))


# ---- the credential seam --------------------------------------------


def test_without_caller_raises():
    with pytest.raises(auth.NotSignedInError, match="re-authenticate"):
        auth.caller_credentials()


def test_caller_credentials_carry_the_caller_token():
    reset = auth_context_var.set(_caller("caller-token"))
    try:
        assert auth.caller_credentials().token == "caller-token"
    finally:
        auth_context_var.reset(reset)


def test_caller_id_is_the_google_sub_claim():
    reset = auth_context_var.set(_caller("t", sub="1234", email="a@example.com"))
    try:
        assert auth.caller_id() == "1234"
    finally:
        auth_context_var.reset(reset)


def test_auth_status_reports_caller_not_token():
    reset = auth_context_var.set(_caller("secret-token", email="a@example.com"))
    try:
        info = auth.credentials_info()
    finally:
        auth_context_var.reset(reset)
    assert info["signed_in"] is True and info["email"] == "a@example.com"
    assert "secret-token" not in json.dumps(info)


# ---- settings -------------------------------------------------------


def test_good_settings_pass_and_default_port():
    settings, problems = http_mode.check_settings(GOOD_ENV)
    assert problems == []
    assert settings.port == 8080
    assert settings.base_url == "https://gsheet-mcp.example.run.app"


def test_every_problem_reported_at_once_without_secret_values():
    env = {**GOOD_ENV, "GSHEET_MCP_GOOGLE_CLIENT_SECRET": "",
           "GSHEET_MCP_BASE_URL": "http://example.com",
           "GSHEET_MCP_FIRESTORE_DATABASE": "(default)", "PORT": "nope"}
    settings, problems = http_mode.check_settings(env)
    assert settings is None
    assert [name for name, _ in problems] == [
        "GSHEET_MCP_GOOGLE_CLIENT_SECRET", "GSHEET_MCP_BASE_URL",
        "GSHEET_MCP_FIRESTORE_DATABASE", "PORT"]
    text = http_mode.format_problems(problems)
    assert text.startswith("gsheet-mcp serve-http: cannot start, 4 settings need fixing:")
    assert "http://example.com" not in text


@pytest.mark.parametrize("db", ["ab", "Docs", "1abc", "has_underscore"])
def test_invalid_database_ids_rejected(db):
    _, problems = http_mode.check_settings({**GOOD_ENV, "GSHEET_MCP_FIRESTORE_DATABASE": db})
    assert [name for name, _ in problems] == ["GSHEET_MCP_FIRESTORE_DATABASE"]


def test_localhost_http_allowed_for_local_testing():
    _, problems = http_mode.check_settings({**GOOD_ENV,
                                            "GSHEET_MCP_BASE_URL": "http://localhost:8080"})
    assert problems == []


def test_bucket_is_optional():
    settings, problems = http_mode.check_settings(GOOD_ENV)
    assert problems == [] and settings.bucket is None
    settings, problems = http_mode.check_settings(
        {**GOOD_ENV, "GSHEET_MCP_BUCKET": " my-bucket "})
    assert problems == [] and settings.bucket == "my-bucket"


def test_state_store_uses_the_auth_database_and_the_bucket():
    from gsheet_mcp import state_store

    settings, _ = http_mode.check_settings({**GOOD_ENV, "GSHEET_MCP_BUCKET": "my-bucket"})
    store = http_mode.build_state_store(settings)
    assert isinstance(store, state_store.HostedStateStore)
    assert (store.database, store.bucket) == ("gsheet-mcp-auth", "my-bucket")


def test_scopes_are_spreadsheets_drive_openid_and_email_only():
    assert sorted(http_mode.GOOGLE_SCOPES) == [
        "email", "https://www.googleapis.com/auth/drive",
        "https://www.googleapis.com/auth/spreadsheets", "openid"]


def test_auth_store_collections_are_prefixed_gsheet_mcp():
    from key_value.aio._utils.compound import prefix_collection

    assert prefix_collection(prefix=http_mode.COLLECTION_PREFIX,
                             collection="clients") == "gsheet_mcp__clients"


def test_serve_exits_2_before_opening_a_port(capsys):
    assert http_mode.serve(env={}) == 2
    err = capsys.readouterr().err
    assert "4 settings need fixing" in err and "GSHEET_MCP_FIRESTORE_DATABASE" in err


# ---- CLI ------------------------------------------------------------


@pytest.mark.parametrize("argv", [["serve-htp"], [], ["--some-client-flag"]])
def test_anything_but_serve_http_exits_2(monkeypatch, capsys, argv):
    monkeypatch.setattr(sys, "argv", ["gsheet-mcp", *argv])
    assert cli.main() == 2
    assert "serve-http" in capsys.readouterr().err


def test_serve_http_help_lists_settings(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["gsheet-mcp", "serve-http", "--help"])
    assert cli.main() == 0
    out = capsys.readouterr().out
    for name in (*GOOD_ENV, "GSHEET_MCP_BUCKET", "PORT"):
        assert name in out


def test_serve_http_takes_no_arguments(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["gsheet-mcp", "serve-http", "--port", "1"])
    assert cli.main() == 2


# ---- sign-in proxy --------------------------------------------------


async def test_validation_cached_until_access_token_expires():
    now = [1000.0]
    calls = []

    async def verify(token):
        calls.append(token)
        return AccessToken(token=token, client_id="c", scopes=[], expires_at=1060)

    cache = http_mode.ValidationCache(verify, clock=lambda: now[0])
    await cache.verify_token("t")
    await cache.verify_token("t")
    assert calls == ["t"]
    now[0] = 1061
    await cache.verify_token("t")
    assert calls == ["t", "t"]


async def test_failed_validation_is_not_cached():
    calls = []

    async def verify(token):
        calls.append(token)

    cache = http_mode.ValidationCache(verify)
    assert await cache.verify_token("t") is None
    assert await cache.verify_token("t") is None
    assert calls == ["t", "t"]


def test_auth_store_sanitizes_url_client_ids(monkeypatch):
    # claude.ai signs in with a CIMD client ID, which is a URL.
    import key_value.aio.stores.firestore as fs

    made = {}
    monkeypatch.setattr(fs, "FirestoreStore", lambda **kw: made.update(kw) or object())
    settings, _ = http_mode.check_settings(GOOD_ENV)
    http_mode.build_auth_store(settings)
    key = made["key_sanitization_strategy"].sanitize(
        "https://claude.ai/oauth/mcp-oauth-client-metadata")
    assert "/" not in key
    assert made["key_sanitization_strategy"].sanitize("deploy-smoke-test") == "deploy-smoke-test"


def test_provider_wiring():
    from key_value.aio.stores.memory import MemoryStore

    settings, _ = http_mode.check_settings(GOOD_ENV)
    provider = http_mode.build_provider(settings, MemoryStore())
    assert provider._redirect_path == "/auth/callback"


def test_provider_installs_cache_on_proxy_verifier():
    from key_value.aio.stores.memory import MemoryStore

    settings, _ = http_mode.check_settings(GOOD_ENV)
    provider = http_mode.build_provider(settings, MemoryStore())
    bound = provider._token_validator.verify_token
    assert getattr(bound, "__self__", None).__class__ is http_mode.ValidationCache


# ---- fastmcp 4 behaviour we pin ------------------------------------


async def _initialize(app) -> dict:
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as c:
            r = await c.post("/mcp", headers={"Accept": "application/json, text/event-stream"},
                             json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                   "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                                              "clientInfo": {"name": "t", "version": "0"}}})
            get = await c.get("/mcp", headers={"Accept": "text/event-stream"})
    return {"init": r.json(), "get_status": get.status_code}


async def test_http_does_not_advertise_list_changed():
    # server.py writes a private fastmcp attribute to turn this off; this guards it.
    from gsheet_mcp.server import mcp

    out = await _initialize(mcp.http_app(stateless_http=True, json_response=True))
    assert out["init"]["result"]["capabilities"]["tools"]["listChanged"] is False
    assert out["get_status"] == 405


async def test_tool_descriptions_keep_returns_sections():
    # fastmcp would otherwise drop `Returns:` from descriptions agents read.
    from gsheet_mcp.server import mcp

    tools = {t.name: t for t in await mcp.list_tools()}
    for name, tool in tools.items():
        assert tool.description == tool.fn.__doc__, name


async def test_server_reports_gsheet_mcp_version_not_fastmcps():
    from gsheet_mcp import __version__
    from gsheet_mcp.server import mcp

    out = await _initialize(mcp.http_app(stateless_http=True, json_response=True))
    assert out["init"]["result"]["serverInfo"]["version"] == __version__


async def test_server_instructions_route_agents():
    from gsheet_mcp.server import INSTRUCTIONS, mcp

    out = await _initialize(mcp.http_app(stateless_http=True, json_response=True))
    assert out["init"]["result"]["instructions"] == INSTRUCTIONS


async def test_each_tool_call_logs_its_name_and_nothing_else(capsys):
    from gsheet_mcp.server import mcp

    await mcp.call_tool("auth_status", {})
    lines = [x for x in capsys.readouterr().err.splitlines()
             if x.startswith("gsheet-mcp call ")]
    record = json.loads(lines[-1].removeprefix("gsheet-mcp call "))
    assert record["tool"] == "auth_status" and record["ok"] is True
    assert set(record) == {"tool", "ms", "ok"}


# ---- health check ---------------------------------------------------


async def test_health_answers_without_sign_in():
    # /health, not /healthz: Cloud Run's front end answers /healthz itself with
    # a 404 and never forwards it.
    from key_value.aio.stores.memory import MemoryStore

    from gsheet_mcp import __version__
    from gsheet_mcp.server import mcp

    settings, _ = http_mode.check_settings(GOOD_ENV)
    mcp.auth = http_mode.build_provider(settings, MemoryStore())
    try:
        app = mcp.http_app(stateless_http=True, json_response=True)
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as c:
                health = await c.get("/health")
                mcp_status = (await c.post("/mcp", json={})).status_code
    finally:
        mcp.auth = None
    assert health.status_code == 200
    assert health.json() == {"status": "ok", "version": __version__}
    assert mcp_status == 401
