"""The Apps Script API, `scripts.run`, called with the caller's own access token.

Floating (over-grid) images exist only in Apps Script: the Sheets REST API can
neither create, list nor move them. The script lives in `apps_script/` and is
deployed as an API executable in the server's own Cloud project; its id comes
from GSHEET_MCP_APPS_SCRIPT_ID. `scripts.run` takes the script id, not a
deployment id, and runs the latest versioned API-executable deployment.

No new OAuth scope: the script's manifest asks only for `spreadsheets`, which
every caller's token already carries.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

from . import auth, reply

ENV = "GSHEET_MCP_APPS_SCRIPT_ID"
_RUN_URL = "https://script.googleapis.com/v1/scripts/{id}:run"
TIMEOUT = 120.0   # a near-limit image takes about 21 s, almost all of it the upload

NOT_CONFIGURED = (f"Floating images are not configured on this server: {ENV} is unset. "
                  "They need the server's Apps Script project (apps_script/ in the "
                  "gsheet-mcp repo, set up as in docs/deploy.md). Every other tool works "
                  "without it.")

# What a failed `scripts.run` most likely means, by HTTP status, for whoever runs
# the server; the script's own errors arrive as HTTP 200 instead.
_STATUS_HINTS = {
    401: "the caller's Google token was refused; sign in to this MCP server again",
    403: ("the caller may not run the script: check that the Apps Script API is enabled "
          "in the server's Cloud project and that the script is deployed as an API "
          "executable with access ANYONE"),
    404: (f"no API-executable script with this id: check {ENV} (the script id from "
          "Project Settings, not a deployment id) and that a versioned API-executable "
          "deployment exists"),
}


class ScriptError(RuntimeError):
    """`scripts.run` failed: the script threw, or the call never ran it."""


def script_id() -> str:
    value = (os.environ.get(ENV) or "").strip()
    if not value:
        raise reply.Refusal(NOT_CONFIGURED)
    return value


def _post(url: str, body: bytes, token: str) -> tuple[int, dict[str, Any]]:
    """One HTTPS POST; (status, JSON body). The network seam tests replace."""
    request = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as e:
        text = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(text)
        except ValueError:
            return e.code, {"error": {"message": text[:500]}}


def _script_message(error: dict[str, Any]) -> str:
    """The script's own exception text, without Apps Script's `Exception: ` prefix."""
    details = error.get("details") or [{}]
    message = (details[0] or {}).get("errorMessage") or error.get("message") or "unknown error"
    for prefix in ("Exception: ", "Error: "):
        if message.startswith(prefix):
            message = message[len(prefix):]
    return message


def run(function: str, parameters: list[Any]) -> Any:
    """Run one of the script's functions as the caller; its return value.

    Raises ScriptError with a message an agent can act on: the script's own
    exception (which `scripts.run` returns as HTTP 200 with an `error`), or what
    a non-200 status most likely means."""
    target = script_id()
    token = auth.caller_credentials().token
    body = json.dumps({"function": function, "parameters": parameters}).encode()
    try:
        status, payload = _post(_RUN_URL.format(id=target), body, token)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise ScriptError(f"the Apps Script API did not answer: {e}") from None
    error = payload.get("error")
    if status == 200 and error:
        raise ScriptError(_script_message(error))
    if status != 200:
        message = (error or {}).get("message") or f"HTTP {status}"
        hint = _STATUS_HINTS.get(status)
        raise ScriptError(f"Apps Script API {status}: {message}" + (f" ({hint})" if hint else ""))
    return (payload.get("response") or {}).get("result")
