"""The script sandbox runs a script in its own worker process and reports back."""
from __future__ import annotations

import pytest

from gsheet_mcp.sandbox.process import ScriptFailure, Worker


def _run(script: str, *, input=None, model=None, timeout: float = 10) -> dict:
    worker = Worker()
    try:
        worker.send({"op": "start", "script": script, "input": input, "model": model,
                     "max_ops": 10})
        return worker.next(timeout, "script ran too long")
    finally:
        worker.kill()


def test_script_sees_input_and_model_and_returns_json():
    out = _run("console.log('hi'); __push({op: 'x'}); return [input.n + 1, model.title];",
               input={"n": 1}, model={"title": "Canary"})
    assert out["op"] == "done"
    assert out["result"] == '[2,"Canary"]'
    assert out["ops"] == [{"op": "x"}]
    assert out["logs"] == ["hi"]


def test_syntax_error_names_the_script_line():
    out = _run("const = 1;")
    assert out["op"] == "error" and out["error"]["kind"] == "syntax"


def test_runaway_script_is_killed_at_the_timeout():
    with pytest.raises(ScriptFailure) as e:
        _run("while (true) {}", timeout=1)
    assert e.value.kind == "timeout"
