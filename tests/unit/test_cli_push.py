"""`gsheet push` end to end against the fake Sheets API: pull_tables, `gsheet
pull`, `gsheet push` to a local upload server, then `write_cells` makes a plan
that writes nothing until it is applied. Skips without node or cli/node_modules."""
from __future__ import annotations

import http.server
import json
import re
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

from gsheet_mcp import sheets_api
from tests.conftest import call
from tests.fake_sheets import CANARY_ID

CLI = Path(__file__).resolve().parents[2] / "cli"
pytestmark = pytest.mark.skipif(
    shutil.which("node") is None or not (CLI / "node_modules").is_dir(),
    reason="needs node and cli/node_modules (npm ci --prefix cli)")


def gsheet(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["node", str(CLI / "bin" / "gsheet.js"), *args], cwd=cwd,
                          capture_output=True, text=True, timeout=120)


@pytest.fixture
def upload_server():
    """A stand-in for the signed PUT URL: stores each PUT body by path."""
    puts: dict[str, tuple[bytes, str]] = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_PUT(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            puts[self.path] = (body, self.headers["Content-Type"])
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}", puts
    server.shutdown()


async def _pulled_here(client, store, cwd: Path) -> dict:
    """pull_tables through the tool layer, then `gsheet pull` on a local copy of its manifest."""
    text, is_error = await call(client, "pull_tables", spreadsheet=CANARY_ID, tab="native-table")
    assert not is_error, text
    url = re.search(r"^manifest memory://(\S+)$", text, re.M).group(1)
    manifest = json.loads(store.objects[url][0])
    for tab in manifest["tabs"]:                       # memory:// links become local files
        csv = cwd / f"{tab['gid']}-download.csv"
        csv.write_bytes(store.objects[tab["csv"].removeprefix("memory://")][0])
        tab["csv"] = str(csv)
    (cwd / "manifest.json").write_text(json.dumps(manifest))
    pulled = gsheet(cwd, "pull", "manifest.json")
    assert pulled.returncode == 0, pulled.stderr
    assert ".Orders from 'native-table'!A1:F31" in pulled.stdout, pulled.stdout
    return manifest


async def test_gsheet_push_uploads_and_write_cells_plans_without_writing_until_applied(
        fake, signed_in, client, store, tmp_path, upload_server):
    base, puts = upload_server
    upload_text, is_error = await call(client, "upload_slot")
    assert not is_error, upload_text
    handle = re.search(r"upload handle (\S+)", upload_text).group(1)

    # No workspace yet: push refuses rather than making an empty one.
    refused = gsheet(tmp_path, "push", "SELECT 1 AS x", "--to", "'formulas'!B12",
                     "--upload", f"{base}/bucket/{handle}")
    assert refused.returncode == 1 and "no workspace here" in refused.stderr and not puts

    manifest = await _pulled_here(client, store, tmp_path)
    schema = re.sub(r"[^a-z0-9]+", "_", manifest["title"].lower()).strip("_")
    sql = (f'SELECT \'north\' AS region, 12.5 AS total, '
           f'(SELECT count(*) FROM {schema}."Orders") AS orders')
    pushed = gsheet(tmp_path, "push", sql, "--to", "'formulas'!B12", "--upload", f"{base}/bucket/{handle}")
    assert pushed.returncode == 0, pushed.stderr
    assert f"handle {handle}" in pushed.stdout and "Nothing in the sheet has changed" in pushed.stdout
    call_line = f'write_cells(spreadsheet="{CANARY_ID}", range="\'formulas\'!B12", upload="{handle}")'
    assert call_line in pushed.stdout, pushed.stdout
    body, kind = puts[f"/bucket/{handle}"]
    assert kind == "text/csv"
    rows = [line.split(",") for line in body.decode().splitlines()]
    assert rows[0] == ["region", "total", "orders"] and rows[1][:2] == ["north", "12.5"]
    orders = rows[1][2]
    assert int(orders) > 0
    assert not fake.batch_updates

    # What the signed PUT would have done: the CSV is in the bucket under the handle.
    store.objects[handle] = (body, kind)
    planned, is_error = await call(client, "write_cells", spreadsheet=CANARY_ID,
                                   range="'formulas'!B12", upload=handle)
    assert not is_error, planned
    plan_id = re.search(r"plan (\S+)", planned).group(1)
    assert plan_id and not fake.batch_updates
    assert sheets_api.values_get(CANARY_ID, "'formulas'!B12:D13") in ([], [[]], None)

    landed, is_error = await call(client, "write_cells", spreadsheet=CANARY_ID, plan_id=plan_id)
    assert not is_error, landed
    assert len(fake.batch_updates) == 1
    assert sheets_api.values_get(CANARY_ID, "'formulas'!B12:D13") == [
        ["region", "total", "orders"], ["north", "12.5", orders]]
