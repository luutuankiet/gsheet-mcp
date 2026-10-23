# Copied from google-docs-mcp@2fb4652 src/<package>/sandbox/process.py; renamed.
"""The parent side of the sandbox: one worker process per script run.

`Worker.next(timeout)` waits for the worker's next message and kills the
process if the script computes for longer than that, which is the only stop
that also covers code running as V8 microtasks.
"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
from typing import Any


class ScriptFailure(Exception):
    def __init__(self, kind: str, message: str, **extra: Any):
        super().__init__(message)
        self.kind = kind
        self.extra = extra


def parse_input(value: Any) -> Any:
    """`input` arrives native, or as a JSON string from clients that
    stringify arguments. A string that parses as JSON is parsed once."""
    if isinstance(value, str):
        s = value.strip()
        if s[:1] in ("{", "[", '"') or s in ("true", "false", "null") or _is_number(s):
            try:
                return json.loads(s)
            except ValueError:
                return value
    return value


def _is_number(s: str) -> bool:
    try:
        float(s)
        return True
    except ValueError:
        return False


class Worker:
    """One sandbox process. `next(timeout)` waits for its next message and
    kills the process if the script computes for longer than that."""

    def __init__(self) -> None:
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(p for p in sys.path if p)
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "gsheet_mcp.sandbox.worker"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", env=env,
        )
        self._q: queue.Queue[str | None] = queue.Queue()
        self._stderr: list[str] = []
        threading.Thread(target=self._pump_out, daemon=True).start()
        threading.Thread(target=self._pump_err, daemon=True).start()

    def _pump_out(self) -> None:
        assert self.proc.stdout
        for line in self.proc.stdout:
            self._q.put(line)
        self._q.put(None)

    def _pump_err(self) -> None:
        assert self.proc.stderr
        for line in self.proc.stderr:
            if sum(map(len, self._stderr)) < 4000:
                self._stderr.append(line)

    def send(self, obj: dict[str, Any]) -> None:
        assert self.proc.stdin
        try:
            self.proc.stdin.write(json.dumps(obj) + "\n")
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError) as e:
            raise ScriptFailure("sandbox", f"sandbox process exited early: {self.stderr_tail()}") from e

    def next(self, timeout: float, why: str) -> dict[str, Any]:
        try:
            line = self._q.get(timeout=max(timeout, 0.01))
        except queue.Empty:
            self.kill()
            raise ScriptFailure("timeout", why) from None
        if line is None:
            self.proc.wait(timeout=5)
            raise ScriptFailure(
                "sandbox",
                f"sandbox process exited (code {self.proc.returncode}): {self.stderr_tail()}")
        return json.loads(line)

    def stderr_tail(self) -> str:
        return "".join(self._stderr)[-800:].strip() or "no output"

    def kill(self) -> None:
        if self.proc.poll() is None:
            self.proc.kill()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
