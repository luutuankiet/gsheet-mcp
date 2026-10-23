# Copied from google-docs-mcp@2fb4652 src/<package>/sandbox/worker.py; renamed, the doc model is a generic model.
"""Sandbox worker: runs one script in an embedded V8 isolate.

Spawned once per script run as `python -m gsheet_mcp.sandbox.worker` and
spoken to over JSON lines on stdin/stdout. The parent owns the timeout and
kills this process when it fires. That is the only reliable stop: code after
an `await` runs in V8 microtasks outside any eval timeout, so an in-process
guard cannot interrupt it. A V8 crash also stays in here.

The isolate has no filesystem, network, module loader or timers; its only
way out is the messages below. The script sees `input` and `model` (whatever
the calling tool hands it) and records operations; it never calls Google.

  parent -> worker   {"op": "start", "script", "input", "model", "max_ops"}
  worker -> parent   {"op": "done", "result", "ops", "logs"}
                     {"op": "error", "error": {kind, name, message, line?, column?}, "logs"}

`kind` is `syntax`, `script` or `memory`.
"""
from __future__ import annotations

import json
import re
import sys
from importlib import resources
from typing import Any

MEMORY_LIMIT_BYTES = 1024 * 1024 * 1024
_LOC_RE = re.compile(r"(?:script\.js|<anonymous>):(\d+)(?::(\d+))?")
# The user's source carries sourceURL=script.js; the prelude is <anonymous>.
# A helper that throws puts its own frame first, so prefer the script's.
_SCRIPT_LOC_RE = re.compile(r"script\.js:(\d+)(?::(\d+))?")
# The wrapper puts one line in front of the user's source.
LINE_OFFSET = 1


def _send(obj: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def _recv() -> dict[str, Any]:
    line = sys.stdin.readline()
    if not line:
        raise SystemExit(0)
    return json.loads(line)


def _locate(text: str) -> dict[str, int]:
    m = _SCRIPT_LOC_RE.search(text or "") or _LOC_RE.search(text or "")
    if not m:
        return {}
    out = {"line": max(int(m.group(1)) - LINE_OFFSET, 1)}
    if m.group(2):
        out["column"] = int(m.group(2))
    return out


def main() -> None:
    import py_mini_racer as pmr

    start = _recv()
    mr = pmr.MiniRacer()
    mr.set_hard_memory_limit(MEMORY_LIMIT_BYTES)
    prelude = resources.files("gsheet_mcp.sandbox").joinpath("prelude.js").read_text("utf-8")
    mr.eval(prelude)
    names = mr.eval("Object.keys(helpers).join(',')")
    wrapped = (
        "globalThis.__main = async function (input) { 'use strict'; "
        f"const {{{names}}} = globalThis.helpers; "
        "return await (async () => {\n"
        f"{start['script']}\n"
        "})(); };\n//# sourceURL=script.js"
    )
    try:
        mr.eval(wrapped)
    except pmr.JSEvalException as e:
        msg = str(e)
        first = next((ln for ln in msg.splitlines() if "Error" in ln),
                     msg.splitlines()[0] if msg else "")
        first = re.sub(r"^\S*(?:script\.js|<anonymous>):\d+:\s*", "", first)
        _send({"op": "error", "logs": [], "error": {
            "kind": "syntax", "name": "SyntaxError", "message": first.strip(), **_locate(msg)}})
        return

    mr.eval(
        f"__start({json.dumps(json.dumps(start.get('input')))}, "
        f"{json.dumps(json.dumps(start.get('model')))}, {int(start['max_ops'])})"
    )
    try:
        state = json.loads(mr.eval("__poll()"))
    except pmr.JSOOMException:
        _send({"op": "error", "logs": [], "error": {
            "kind": "memory", "name": "RangeError",
            "message": f"script exceeded the {MEMORY_LIMIT_BYTES // (1024 * 1024)} MB memory limit"}})
        return
    status = state["status"]
    logs = state.get("logs", [])
    if status == "done":
        _send({"op": "done", "result": state["result"], "ops": state.get("ops", []),
               "logs": logs})
        return
    if status == "error":
        err = state["error"]
        _send({"op": "error", "logs": logs, "error": {
            "kind": "script", "name": err.get("name", "Error"), "message": err.get("message", ""),
            **_locate(err.get("stack", ""))}})
        return
    # Still "running" after every microtask drained: the script awaited
    # something that can never resolve here.
    _send({"op": "error", "logs": logs, "error": {
        "kind": "script", "name": "Error",
        "message": "script is awaiting something that never resolves; nothing in a "
                   "script needs await"}})


if __name__ == "__main__":
    main()
