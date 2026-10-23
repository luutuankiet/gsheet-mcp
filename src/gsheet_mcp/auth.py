# Copied from google-docs-mcp@2fb4652 src/<package>/auth.py; renamed.
"""The current caller's Google credentials.

Each request carries the caller's own Google access token, which fastmcp's
sign-in proxy puts in request context. There is no stdio mode and no local
token file: gsheet-mcp never runs a consent flow itself.
"""
from __future__ import annotations

from google.oauth2.credentials import Credentials

_SIGN_IN_AGAIN = ("No signed-in caller on this request. Ask the user to re-authenticate "
                  "this MCP server in their MCP client, then retry.")


class NotSignedInError(RuntimeError):
    """No caller access token in the request context."""


def _caller_access_token():
    from fastmcp.server.dependencies import get_access_token

    return get_access_token()


def caller_credentials() -> Credentials:
    """The current caller's Google credentials: access token only, no refresh.

    Refreshing the caller's Google grant is the sign-in proxy's job, not ours.
    """
    token = _caller_access_token()
    if token is None or not token.token:
        raise NotSignedInError(_SIGN_IN_AGAIN)
    return Credentials(token=token.token)


def caller_id() -> str:
    """Who is calling, for binding server state to them.

    The Google account's stable `sub` claim (email as a fallback).
    """
    token = _caller_access_token()
    if token is None:
        raise NotSignedInError(_SIGN_IN_AGAIN)
    claims = token.claims or {}
    who = claims.get("sub") or getattr(token, "subject", None) or claims.get("email")
    if not who:
        raise NotSignedInError("The signed-in caller has no Google account id; "
                               "re-authenticate this MCP server, then retry.")
    return str(who)


def caller_email() -> str | None:
    """The signed-in caller's email, or None when there is none to name."""
    token = _caller_access_token()
    return (token.claims or {}).get("email") if token is not None else None


def credentials_info() -> dict[str, object]:
    """Diagnostic: who is signed in, without exposing the token."""
    token = _caller_access_token()
    if token is None:
        return {"signed_in": False}
    info: dict[str, object] = {"signed_in": True}
    if email := (token.claims or {}).get("email"):
        info["email"] = email
    info["scopes"] = list(token.scopes or [])
    info["token_expiry"] = token.expires_at
    return info
