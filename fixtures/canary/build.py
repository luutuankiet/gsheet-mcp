#!/usr/bin/env python3
"""Builds the public canary spreadsheet for gsheet-mcp and writes its manifest.

Every value is lorem ipsum or synthetic, so the sheet can be public. The script
creates the spreadsheet, fills seven tabs through `batchUpdate`, shares it as
anyone-with-the-link-can-edit, writes `manifest.json` next to this file and
prints the few steps no API request can do, to be done by hand in the browser.

    python3 fixtures/canary/build.py            # build a new canary
    python3 fixtures/canary/build.py --capture  # after the hand steps: save fixtures

The Google identity is the gcpx alias in `.env`; the new id is added to `.env`
as CANARY_SHEET_ID. Standard library only.
"""
import io
import json
import re
import struct
import subprocess
import sys
import urllib.request
import zipfile
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SHEETS = "https://sheets.googleapis.com/v4/spreadsheets"
DRIVE = "https://www.googleapis.com/drive/v3/files"
TITLE = "gsheet-mcp canary (public, lorem ipsum)"
IMAGE_URL = "https://www.gstatic.com/images/branding/product/2x/sheets_2020q4_48dp.png"

TABS = [  # (sheetId, title, rows, cols, frozen rows, frozen cols, hidden)
    (101, "comms", 30, 12, 2, 1, False),
    (102, "dashboard", 30, 20, 0, 0, False),
    (103, "native-table", 40, 8, 1, 0, False),
    (104, "multi-region", 40, 14, 0, 0, False),
    (105, "hidden", 20, 6, 1, 0, False),
    (106, "formulas", 20, 6, 0, 0, False),
    (107, "hidden-tab", 10, 4, 0, 0, True),
]
SID = {t[1]: t[0] for t in TABS}
FEATURES = []  # manifest rows: tab, range, feature, how it was made


def env():
    out = {}
    for line in (ROOT / ".env").read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"')
    return out


ENV = env()
TOKEN = subprocess.run(
    ["gcpx", "exec", ENV["GCPX_ALIAS"], "--", "gcloud", "auth", "print-access-token", "--quiet"],
    capture_output=True, text=True, check=True).stdout.strip()


def call(method, url, body=None, raw=False):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {TOKEN}", "x-goog-user-project": ENV["GCP_PROJECT"],
        "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            payload = r.read()
    except urllib.error.HTTPError as e:
        sys.exit(f"{method} {url} -> {e.code}\n{e.read().decode()[:2000]}")
    return payload if raw else json.loads(payload or b"{}")


# ---- A1 helpers ---------------------------------------------------------------

def col_index(letters):
    n = 0
    for ch in letters:
        n = n * 26 + ord(ch) - 64
    return n - 1


def cell_ref(ref):
    letters = "".join(c for c in ref if c.isalpha())
    return int(ref[len(letters):]) - 1, col_index(letters)


def grid(tab, a1):
    a, _, b = a1.partition(":")
    r0, c0 = cell_ref(a)
    r1, c1 = cell_ref(b or a)
    return {"sheetId": SID[tab], "startRowIndex": r0, "endRowIndex": r1 + 1,
            "startColumnIndex": c0, "endColumnIndex": c1 + 1}


def note(tab, a1, feature, how="api"):
    FEATURES.append({"tab": tab, "range": a1, "feature": feature, "made": how})


# ---- cells --------------------------------------------------------------------

def rgb(h):
    h = h.lstrip("#")
    return {"red": int(h[0:2], 16) / 255, "green": int(h[2:4], 16) / 255, "blue": int(h[4:6], 16) / 255}


def cell(v=None, fmt=None, runs=None, note_text=None, dv=None):
    c = {}
    if isinstance(v, bool):
        c["userEnteredValue"] = {"boolValue": v}
    elif isinstance(v, (int, float)):
        c["userEnteredValue"] = {"numberValue": v}
    elif isinstance(v, str) and v.startswith("="):
        c["userEnteredValue"] = {"formulaValue": v}
    elif v is not None:
        c["userEnteredValue"] = {"stringValue": v}
    if fmt:
        c["userEnteredFormat"] = fmt
    if runs:
        c["textFormatRuns"] = runs
    if note_text:
        c["note"] = note_text
    if dv:
        c["dataValidation"] = dv
    return c


def rich(*parts):
    """parts: (text, {bold, italic, link}) -> (string, textFormatRuns)."""
    text, runs = "", []
    for seg, f in parts:
        fmt = {}
        if f.get("bold"):
            fmt["bold"] = True
        if f.get("italic"):
            fmt["italic"] = True
        if f.get("link"):
            fmt["link"] = {"uri": f["link"]}
        runs.append({"startIndex": len(text), "format": fmt})
        text += seg
    return text, runs


def write(tab, a1, rows):
    r0, c0 = cell_ref(a1)
    return {"updateCells": {
        "start": {"sheetId": SID[tab], "rowIndex": r0, "columnIndex": c0},
        "rows": [{"values": [x if isinstance(x, dict) else cell(x) for x in row]} for row in rows],
        "fields": "userEnteredValue,userEnteredFormat,textFormatRuns,note,dataValidation"}}


def merge(tab, a1):
    return {"mergeCells": {"range": grid(tab, a1), "mergeType": "MERGE_ALL"}}


def hide(tab, dim, start, end):
    return {"updateDimensionProperties": {
        "range": {"sheetId": SID[tab], "dimension": dim, "startIndex": start, "endIndex": end},
        "properties": {"hiddenByUser": True}, "fields": "hiddenByUser"}}


def size(tab, dim, start, end, px):
    return {"updateDimensionProperties": {
        "range": {"sheetId": SID[tab], "dimension": dim, "startIndex": start, "endIndex": end},
        "properties": {"pixelSize": px}, "fields": "pixelSize"}}


BAND = {"backgroundColor": rgb("#1f3864"), "horizontalAlignment": "CENTER",
        "textFormat": {"bold": True, "foregroundColor": rgb("#ffffff"), "fontSize": 12}}
HEAD = {"backgroundColor": rgb("#d9e2f3"), "textFormat": {"bold": True}}
CHECK = {"condition": {"type": "BOOLEAN"}}
STATUS = {"condition": {"type": "ONE_OF_LIST", "values": [
    {"userEnteredValue": s} for s in ("On track", "At risk", "Blocked")]},
    "showCustomUi": True, "strict": True}
LONG = ("Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor "
        "incididunt ut labore et dolore magna aliqua. ") * 6


# ---- tabs ---------------------------------------------------------------------

def comms():
    t = "comms"
    owners = ["Ada Example", "Bo Sample", "Cy Placeholder", "Di Testcase", "Ed Mockwell", "Fi Dummy"]
    status = ["On track", "At risk", "Blocked", "On track", "On track", "At risk"]
    work = [
        rich(("Lorem ", {}), ("ipsum", {"bold": True}), (" plan, see ", {}),
             ("the brief", {"link": "https://example.com/brief"})),
        rich(("Dolor ", {"italic": True}), ("migration", {}), (" (", {}),
             ("ticket", {"link": "https://example.com/t/1"}), (")", {})),
        rich(("Sit amet rollout", {"bold": True})),
        rich(("Consectetur review", {})),
        rich(("Adipiscing ", {}), ("docs", {"link": "https://example.com/docs"}),
             (" and ", {}), ("faq", {"link": "https://example.com/faq"})),
        rich(("Elit cleanup", {"italic": True})),
    ]
    remarks = ["✅ shipped to staging", "⚠️ waiting on vendor", "🛑 blocked by access",
               "🚀 ahead of plan", "📎 notes attached", LONG]
    rows = []
    for i in range(6):
        text, runs = work[i]
        rows.append([
            cell(owners[i], {"textFormat": {"bold": True}}),
            cell(text, runs=runs, note_text="Owner note: lorem ipsum." if i == 0 else None), cell(),
            cell(i % 2 == 0, dv=CHECK),
            cell(status[i], dv=STATUS),
            cell(remarks[i], {"wrapStrategy": "WRAP"} if i == 5 else None),
        ])
    reqs = [
        write(t, "A1", [[cell("W43", BAND), cell("Weekly status · lorem ipsum", BAND)] + [cell(None, BAND)] * 4]),
        write(t, "A2", [[cell("Owner", HEAD), cell("Workstream", HEAD), cell(None, HEAD),
                         cell("Done", HEAD), cell("Status", HEAD), cell("Notes", HEAD)]]),
        write(t, "A3", rows),
        write(t, "A9", [[cell("Plan link"), cell('=HYPERLINK("https://example.com/plan","Plan")')]]),
        write(t, "A10", [[cell(f'=IMAGE("{IMAGE_URL}")')]]),
        merge(t, "B1:F1"), merge(t, "B2:C2"),
        *[merge(t, f"B{r}:C{r}") for r in range(3, 9)],
        size(t, "ROWS", 9, 10, 60), size(t, "COLUMNS", 5, 6, 320),
    ]
    note(t, "B1:F1", "merged header band, dark fill, white bold centred text; A1 is a single band cell in the frozen column")
    note(t, "B2:C2", "merged column header")
    note(t, "A1:L2", "2 frozen rows and 1 frozen column")
    note(t, "B3:C8", "rich text runs with bold, italic and links; each row merged across B:C")
    note(t, "B3", "cell note")
    note(t, "D3:D8", "checkboxes, alternating ticked")
    note(t, "E3:E8", "dropdown chips On track / At risk / Blocked")
    note(t, "F3:F7", "emoji in text")
    note(t, "F8", f"long text, {len(LONG)} characters")
    note(t, "B9", "HYPERLINK() formula")
    note(t, "A10", "IMAGE() formula in a cell, row height 60")
    return reqs


def dashboard():
    t = "dashboard"
    cats = [("Bugs", 24), ("Features", 15), ("Chores", 8), ("Docs", 5), ("Spikes", 3)]
    reqs = [
        write(t, "A1", [[cell("Ticket dashboard · synthetic", BAND)] + [cell(None, BAND)] * 7]),
        merge(t, "A1:H1"),
        write(t, "A3", [["Category", "Count"]] + [[c, n] for c, n in cats]),
        write(t, "D3", [[cell(f"Week {w}", HEAD) for w in range(1, 6)]]
              + [[cell((r * 5 + w) % 3 != 0, dv=CHECK) for w in range(5)] for r in range(5)]),
        write(t, "A10", [[cell("Lorem ipsum summary block", {"backgroundColor": rgb("#fff2cc")})]
                         + [cell(None, {"backgroundColor": rgb("#fff2cc")})] * 3]),
        merge(t, "A10:D12"),
        {"addBanding": {"bandedRange": {"range": grid(t, "A3:B8"), "rowProperties": {
            "headerColor": rgb("#38761d"), "firstBandColor": rgb("#ffffff"),
            "secondBandColor": rgb("#d9ead3")}}}},
        {"addConditionalFormatRule": {"index": 0, "rule": {"ranges": [grid(t, "B4:B8")], "booleanRule": {
            "condition": {"type": "NUMBER_GREATER", "values": [{"userEnteredValue": "20"}]},
            "format": {"backgroundColor": rgb("#f4cccc")}}}}},
        {"addChart": {"chart": {"spec": {"title": "Tickets by category", "pieChart": {
            "legendPosition": "RIGHT_LEGEND", "pieHole": 0.5,
            "domain": {"sourceRange": {"sources": [grid(t, "A4:A8")]}},
            "series": {"sourceRange": {"sources": [grid(t, "B4:B8")]}}}},
            "position": {"overlayPosition": {"anchorCell": {
                "sheetId": SID[t], "rowIndex": 1, "columnIndex": 9}}}}}},
    ]
    note(t, "A1:H1", "merged title band")
    note(t, "A3:B8", "banding over a small table")
    note(t, "B4:B8", "conditional format: red fill when over 20")
    note(t, "D3:H8", "checkbox grid, 5 by 5, under week headers")
    note(t, "A10:D12", "merged block over three rows")
    note(t, "J2", "donut chart (pie, hole 0.5) over A4:B8")
    return reqs


def native_table():
    t = "native-table"
    items = ["lorem", "ipsum", "dolor", "sit", "amet", "consectetur", "adipiscing", "elit"]
    rows = []
    for i in range(30):
        qty = (i * 37) % 97 + 1
        rows.append((f"ORD-{1000 + i}", items[i % 8], qty, round(qty * 1.75 + i, 2), 46000 + i * 3,
                     "rush" if i % 5 == 0 else ""))
    rows.sort(key=lambda r: -r[2])  # sorted by qty, largest first
    date = {"numberFormat": {"type": "DATE", "pattern": "yyyy-mm-dd"}}
    money = {"numberFormat": {"type": "CURRENCY", "pattern": "$#,##0.00"}}
    body = [[cell(a), cell(b), cell(c), cell(d, money), cell(e, date), cell(f or None)] for a, b, c, d, e, f in rows]
    reqs = [write(t, "A1", [["id", "item", "qty", "price", "ordered", "remark"]] + body)]
    later = [
        {"addTable": {"table": {"name": "Orders", "range": grid(t, "A1:F31"), "columnProperties": [
            {"columnIndex": 0, "columnName": "id", "columnType": "TEXT"},
            {"columnIndex": 1, "columnName": "item"},
            {"columnIndex": 2, "columnName": "qty", "columnType": "DOUBLE"},
            {"columnIndex": 3, "columnName": "price", "columnType": "CURRENCY"},
            {"columnIndex": 4, "columnName": "ordered", "columnType": "DATE"},
            {"columnIndex": 5, "columnName": "remark"}]}}},
        {"addFilterView": {"filter": {"title": "qty over 50", "range": grid(t, "A1:F31"), "filterSpecs": [
            {"columnIndex": 2, "filterCriteria": {"condition": {
                "type": "NUMBER_GREATER", "values": [{"userEnteredValue": "50"}]}}}]}}},
    ]
    note(t, "A1:F31", "native Table Orders; id, qty, price, ordered typed; item, remark untyped")
    note(t, "A2:F31", "rows sorted by qty, largest first")
    note(t, "A1:F31", "filter view: qty over 50")
    return reqs, later


def multi_region():
    t = "multi-region"
    a_head = [cell(h, HEAD) for h in ("sku", "region", "units", "revenue")]
    a_rows = [[f"SKU-{i:03d}", ["North", "South", "East", "West"][i % 4], 10 + i, 100.0 + i * 12.5] for i in range(10)]
    a_more = [[f"SKU-{i:03d}", ["North", "South", "East", "West"][i % 4], 10 + i, 100.0 + i * 12.5] for i in range(10, 17)]
    b_rows = [["Q1", 120, 95, 0.79], ["Q2", 130, 101, 0.78], ["Q3", 125, 110, 0.88],
              ["Q4", 140, 133, 0.95], ["Q5", 150, 120, 0.8], ["Q6", 160, 151, 0.94], ["Q7", 155, 140, 0.9]]
    d_rows = [[f"Team {c}", 3 + i, 5 - i % 3, "lorem"] for i, c in enumerate("ABCDEFG")]
    pct = {"numberFormat": {"type": "PERCENT", "pattern": "0%"}}
    reqs = [
        write(t, "A1", [a_head] + a_rows),
        write(t, "A14", a_more),
        write(t, "G1", [[cell(h, HEAD) for h in ("quarter", "target", "actual", "hit")]]
              + [[q, a, b, cell(c, pct)] for q, a, b, c in b_rows]),
        write(t, "G9", [[cell("Total", {"textFormat": {"bold": True}}), "=SUM(H2:H8)", "=SUM(I2:I8)",
                         cell("=I9/H9", pct)]]),
        write(t, "A25", [[cell(h, HEAD) for h in ("team", "open", "closed", "remark")]] + d_rows),
        write(t, "L3", [[42]]),
        write(t, "L15", [[3.14]]),
        write(t, "A34", [["* all figures are synthetic, lorem ipsum"]]),
        {"addNamedRange": {"namedRange": {"name": "quarterly_targets", "range": grid(t, "G1:J9")}}},
    ]
    note(t, "A1:D11", "table A: header plus 10 rows")
    note(t, "A14:D20", "table A continued after a 2-row gap, same columns; the scan should join it to A1:D11")
    note(t, "G1:J8", "table B beside table A, two blank columns apart")
    note(t, "G9:J9", "total row touching table B")
    note(t, "G1:J9", "named range quarterly_targets")
    note(t, "A25:D32", "table D after a 4-row gap with its own header; the scan should split it from table A")
    note(t, "L3", "loose number 42")
    note(t, "L15", "loose number 3.14")
    note(t, "A34", "footnote")
    return reqs


def hidden():
    t = "hidden"
    rows = [[f"row {i}", i * 10, f"secret-ish {i}", "keep" if i != 9 else "drop", i % 3] for i in range(1, 13)]
    reqs = [
        write(t, "A1", [[cell(h, HEAD) for h in ("label", "value", "hidden col", "status", "group")]] + rows),
        hide(t, "ROWS", 4, 6),
        hide(t, "COLUMNS", 2, 3),
        {"setBasicFilter": {"filter": {"range": grid(t, "A1:E13"), "filterSpecs": [
            {"columnIndex": 3, "filterCriteria": {"hiddenValues": ["drop"]}}]}}},
    ]
    note(t, "A5:E6", "rows 5 and 6 hidden by the user")
    note(t, "C1:C13", "column C hidden by the user")
    note(t, "A1:E13", "basic filter on status; row 10 (status drop) hidden by the filter")
    return reqs


def formulas():
    t = "formulas"
    reqs = [
        write(t, "A1", [[cell("input", HEAD), cell("formula", HEAD), cell("what", HEAD)],
                        [5, "=A2*2", "same-row arithmetic"],
                        [7, "=SUM(A2:A3)", "SUM over a range"],
                        [None, "='multi-region'!C2", "reference to another tab"],
                        [None, "=ROWS(quarterly_targets)", "rows in a named range"],
                        [None, '=INDIRECT("nowhere!A1")', "a #REF! error"],
                        [None, "=1/0", "a #DIV/0! error"],
                        [None, '=TEXT(DATE(2026,10,23),"yyyy-mm-dd")', "a date built in a formula"]]),
        {"addProtectedRange": {"protectedRange": {"range": grid(t, "A1:B3"),
                                                  "description": "inputs, warning only", "warningOnly": True}}},
    ]
    note(t, "B2:B3", "formulas over the same tab")
    note(t, "B4", "formula reading another tab")
    note(t, "B5", "formula reading a named range (ROWS, gives 9)")
    note(t, "B6", "#REF! error")
    note(t, "B7", "#DIV/0! error")
    note(t, "A1:B3", "protected range, warning only")
    return reqs


def hidden_tab():
    t = "hidden-tab"
    note(t, "A1:B3", "small grid on a hidden tab")
    return [write(t, "A1", [["key", "value"], ["lorem", 1], ["ipsum", 2]])]


HAND_STEPS = [
    ("comms", "H3:I5", "image floating over empty cells: Insert > Image > Image over cells, "
     "upload fixtures/canary/canary-image.png, drag it over H3:I5, set its alt text to 'canary logo'"),
    ("comms", "G3", "rating chip: select G3, Insert > Smart chips > Rating, set 3 stars"),
    ("comms", "E3:E8", "dropdown chip colours: Data validation on E3:E8, colour On track green, "
     "At risk yellow, Blocked red"),
    ("comms", "F4", "anchored comment: right-click F4 > Comment, 'lorem question?', then reply 'ipsum answer.'"),
]


def png(path, w=96, h=40):
    """A small striped PNG for the floating image, no imaging library needed."""
    rows = b"".join(b"\x00" + b"".join(
        bytes((31, 56, 100) if (x // 8 + y // 8) % 2 else (217, 226, 243)) for x in range(w)) for y in range(h))

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


def add_env(key, value):
    text = (ROOT / ".env").read_text()
    if f"\n{key}=" not in "\n" + text:
        (ROOT / ".env").write_text(text.rstrip("\n") + f"\n{key}={value}\n")


def build():
    sheets = [{"properties": {"sheetId": s, "title": t, "hidden": hid, "gridProperties": {
        "rowCount": r, "columnCount": c, "frozenRowCount": fr, "frozenColumnCount": fc}}}
        for s, t, r, c, fr, fc, hid in TABS]
    made = call("POST", SHEETS, {"properties": {"title": TITLE}, "sheets": sheets})
    sid = made["spreadsheetId"]
    print(f"created {sid}")
    table_reqs, table_later = native_table()
    first = comms() + dashboard() + table_reqs + multi_region() + hidden() + formulas() + hidden_tab()
    call("POST", f"{SHEETS}/{sid}:batchUpdate", {"requests": first})
    call("POST", f"{SHEETS}/{sid}:batchUpdate", {"requests": table_later})
    note("hidden-tab", "-", "the whole tab is hidden")
    call("POST", f"{DRIVE}/{sid}/permissions", {"type": "anyone", "role": "writer"})
    add_env("CANARY_SHEET_ID", sid)
    png(HERE / "canary-image.png")
    for tab, rng, what in HAND_STEPS:
        note(tab, rng, what.split(":")[0], how="by hand")
    (HERE / "manifest.json").write_text(json.dumps(
        {"title": TITLE, "tabs": [t[1] for t in TABS], "features": FEATURES}, indent=1) + "\n")
    print(f"built {len(FEATURES)} features on {len(TABS)} tabs; id saved to .env as CANARY_SHEET_ID")
    print("by hand, in the browser:")
    for i, (tab, rng, what) in enumerate(HAND_STEPS, 1):
        print(f"  {i}. [{tab}!{rng}] {what}")


LEAN = ("properties.title,namedRanges,sheets(properties,merges,charts,tables,bandedRanges,basicFilter,"
        "filterViews,conditionalFormats,protectedRanges,"
        "data(rowMetadata(hiddenByUser,hiddenByFilter,pixelSize),columnMetadata(hiddenByUser,pixelSize),"
        "rowData.values(userEnteredValue,effectiveValue,formattedValue,userEnteredFormat,"
        "textFormatRuns,chipRuns,note,hyperlink,dataValidation)))")


def capture():
    sid = env()["CANARY_SHEET_ID"]
    out = ROOT / "tests" / "fixtures"
    out.mkdir(parents=True, exist_ok=True)
    got = call("GET", f"{SHEETS}/{sid}?includeGridData=true&fields={urllib.request.quote(LEAN, safe='(),.')}")
    got["spreadsheetId"] = "canary"
    text = EMAIL.sub("owner@example.com", json.dumps(got, indent=1, ensure_ascii=False))
    (out / "canary.json").write_text(text + "\n")
    xlsx = scrub_xlsx(call("GET", f"https://docs.google.com/spreadsheets/d/{sid}/export?format=xlsx", raw=True))
    (out / "canary.xlsx").write_bytes(xlsx)
    print(f"tests/fixtures/canary.json {len(text)} bytes, canary.xlsx {len(xlsx)} bytes")


EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")


def scrub_xlsx(data):
    """Replaces the owner's name and email in comment authors, so the fixture names nobody."""
    src = zipfile.ZipFile(io.BytesIO(data))
    names = set()
    for n in src.namelist():
        if n.endswith(".xml"):
            x = src.read(n).decode("utf-8")
            names |= set(re.findall(r'displayName="([^"]+)"', x)) | set(re.findall(r"<author>([^<]+)</author>", x))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            body = src.read(info.filename)
            if info.filename.endswith((".xml", ".rels")):
                x = EMAIL.sub("owner@example.com", body.decode("utf-8"))
                for name in names:
                    x = x.replace(name, "Canary Owner")
                body = x.encode("utf-8")
            dst.writestr(info, body)
    return buf.getvalue()


if __name__ == "__main__":
    capture() if "--capture" in sys.argv else build()
