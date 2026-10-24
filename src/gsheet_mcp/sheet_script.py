# Copied from google-docs-mcp@2fb4652 src/<package>/doc_script.py; the run loop kept, the doc model replaced by sheet.* and raw requests.
"""`run_sheet_script` and `batch_update` (spec, "Layers", "Plans"): scripts and
raw requests, both always behind a plan.

    run(spreadsheet_id, script, input, reads) -> Outcome (writes, raw requests)
    plan(spreadsheet_id, tool, writes, requests, notes) -> dry run text with a plan_id
    apply(spreadsheet_id, plan_id, payload)   -> re-read, compare, one batchUpdate, receipt
    affected(spreadsheet_id, requests)        -> the ranges raw requests name

A script runs in the sandbox (`sandbox/`) and sees a small `sheet.*` API that
only records operations: `sheet.write` becomes the same writes `write_cells`
makes, `sheet.request` queues one raw Sheets request. `sheet.read` answers from
ranges read before the script starts, because the sandbox cannot call out.
Typed structural operations (`edit_sheet`) plug in as one more recorded op
kind, turned into requests here next to `request`.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from . import a1, addressing, guard, reply, sheets_api, state_store, writes
from .sandbox import process
from .structure import GUARD_CELLS

OP = "sheet_script"
CPU_TIMEOUT_S = 30.0
MAX_OPS = 5000
MAX_RETURN_BYTES = 8192
MAX_READS = 20
READ_CELLS = 100_000    # cells across one script's pre-reads (default, review)
# A string literal in any of the three quotes; the body may hold the other quotes
# (`"'my tab'!A1"`) and escaped ones (`'\'my tab\'!A1'`).
_READ_LITERAL = re.compile(r"sheet\.read\(\s*(['\"`])((?:\\.|(?!\1).)+?)\1\s*\)")
_GRID_KEYS = ("startRowIndex", "endRowIndex", "startColumnIndex", "endColumnIndex")

# Put on its own line in front of the script; the sandbox is told about that line
# (`prefix_lines`), so error line numbers stay the script's own.
SHEET_JS = (
    "const sheet = Object.freeze({"
    "read(range) { const v = model.reads[String(range)]; if (v === undefined) throw new "
    "Error(`sheet.read(${JSON.stringify(range)}): not read before the script ran; pass it in "
    "reads=[...] or call sheet.read with a string literal`); return JSON.parse(JSON.stringify(v)); },"
    "write(range, values, format) { __push({op: 'write', range: String(range), values: values "
    "=== undefined ? null : values, format: format === undefined ? null : format}); },"
    "request(r) { if (!r || typeof r !== 'object' || Array.isArray(r) || Object.keys(r).length "
    "!== 1) throw new TypeError('sheet.request takes one Sheets request, such as {addChart: "
    "{...}}'); __push({op: 'request', request: r}); },"
    "});\n"
)
PREFIX_LINES = 1


@dataclass
class Outcome:
    writes: list[dict[str, Any]]
    requests: list[dict[str, Any]]
    logs: list[str]
    result: str


def _caller() -> str | None:
    return writes._caller()


# ---- running a script -----------------------------------------------------------


def _literal_reads(script: str) -> list[str]:
    """The ranges of `sheet.read("...")` calls, as the script's `String(range)` sees them."""
    out = []
    for m in _READ_LITERAL.finditer(script):
        quote, body = m.group(1), m.group(2)
        if quote == "`" and "${" in body:
            continue    # a computed range, not a literal
        out.append(re.sub(r"\\(.)", r"\1", body))
    return out


def _reads(spreadsheet_id: str, script: str, reads: list[str] | None) -> dict[str, Any]:
    names = list(dict.fromkeys([*(reads or []), *_literal_reads(script)]))
    if len(names) > MAX_READS:
        raise reply.Refusal(f"A script reads at most {MAX_READS} ranges; it named {len(names)}.")
    if not names:
        return {}
    tabs = sheets_api.get_tabs(spreadsheet_id)
    sizes = _sizes(tabs)
    total = 0
    for name in names:
        rng = addressing.resolve(spreadsheet_id, name)
        title = sheets_api.find_tab(tabs, rng.tab)["title"]
        clipped = _clip(rng, sizes[title])
        total += _count(clipped) if clipped else 0
    if total > READ_CELLS:
        raise reply.Refusal(f"A script reads at most {READ_CELLS:,} cells before it runs; its "
                            f"reads cover {total:,}. Read a smaller range, or use pull_tables "
                            "for a whole table. Nothing was planned.")
    return {name: sheets_api.values_get(spreadsheet_id, addressing.resolve(spreadsheet_id, name),
                                        render="UNFORMATTED_VALUE") for name in names}


def run(spreadsheet_id: str, script: str, input_value: Any,
        reads: list[str] | None = None) -> Outcome:
    model = {"reads": _reads(spreadsheet_id, script, reads)}
    worker = process.Worker()
    try:
        worker.send({"op": "start", "script": SHEET_JS + script, "input": input_value,
                     "model": model, "max_ops": MAX_OPS,
                     "prefix_lines": PREFIX_LINES})
        try:
            msg = worker.next(CPU_TIMEOUT_S, f"the script ran for more than {CPU_TIMEOUT_S:g} s "
                                             "(an infinite loop?)")
        except process.ScriptFailure as e:
            raise reply.Refusal(f"Script {e.kind} error: {e}. Nothing was planned.") from None
    finally:
        worker.kill()
    logs = msg.get("logs") or []
    if msg["op"] == "error":
        err = msg["error"]
        where = (f" at line {err['line']}" + (f":{err['column']}" if err.get("column") else "")
                 if err.get("line") else "")
        tail = ("\nlogs:\n" + "\n".join(f"  {x}" for x in logs)) if logs else ""
        kind = err.get("kind", "script")
        label = "Script error" if kind == "script" else f"Script {kind} error"
        raise reply.Refusal(f"{label}{where}: "
                            f"{err.get('name', 'Error')}: {err.get('message', '')}. "
                            f"Nothing was planned.{tail}")
    out_writes, requests = [], []
    for op in msg.get("ops", []):
        if op.get("op") == "write":
            out_writes.append({"range": op["range"], "values": op.get("values"),
                               "format": op.get("format")})
        elif op.get("op") == "request":
            requests.append(op["request"])
        else:
            raise reply.Refusal(f"unknown operation {op.get('op')!r} from the sandbox")
    result = msg.get("result") or "null"
    if len(result.encode()) > MAX_RETURN_BYTES:
        result = result.encode()[:MAX_RETURN_BYTES].decode(errors="ignore") + "…"
    return Outcome(out_writes, requests, logs, result)


# ---- raw requests: what they touch, and their guard --------------------------------


def _walk(node: Any, titles: dict[int, str], out: list[a1.Range]) -> None:
    if isinstance(node, dict):
        sid = node.get("sheetId")
        if isinstance(sid, int) and sid in titles:
            title = titles[sid]
            if any(k in node for k in _GRID_KEYS):
                out.append(a1.Range.from_grid(node, title))
            elif "rowIndex" in node or "columnIndex" in node:
                r, c = node.get("rowIndex", 0), node.get("columnIndex", 0)
                out.append(a1.Range(title, r, r + 1, c, c + 1))
            else:
                out.append(a1.Range(title))
        for value in node.values():
            _walk(value, titles, out)
    elif isinstance(node, list):
        for value in node:
            _walk(value, titles, out)


def _titles(spreadsheet_id: str) -> dict[int, str]:
    tabs = sheets_api.get_tabs(spreadsheet_id)
    return {s["properties"]["sheetId"]: s["properties"]["title"] for s in tabs.get("sheets") or []}


def affected(spreadsheet_id: str, requests: list[dict[str, Any]]) -> list[a1.Range]:
    found: list[a1.Range] = []
    _walk(requests, _titles(spreadsheet_id), found)
    return list(dict.fromkeys(found))


def _sizes(tabs: dict[str, Any]) -> dict[str, list[int]]:
    """Each tab's [rowCount, columnCount], by title."""
    return {s["properties"]["title"]: [s["properties"].get("gridProperties", {}).get(k, 0)
                                       for k in ("rowCount", "columnCount")]
            for s in tabs.get("sheets") or []}


def _clip(rng: a1.Range, size: list[int]) -> a1.Range | None:
    """`rng` with concrete bounds inside a tab of `size`; None when nothing is left."""
    rows, cols = size
    sr, sc = rng.start_row or 0, rng.start_col or 0
    er = rows if rng.end_row is None else min(rng.end_row, rows)
    ec = cols if rng.end_col is None else min(rng.end_col, cols)
    if sr >= er or sc >= ec:
        return None
    return a1.Range(rng.tab, sr, er, sc, ec)


def _count(rng: a1.Range) -> int:
    return (rng.end_row - rng.start_row) * (rng.end_col - rng.start_col)


def _raw_fingerprint(spreadsheet_id: str, ranges: list[a1.Range]) -> dict[str, Any]:
    """The cells in the affected ranges plus each affected tab's grid size.

    At most `GUARD_CELLS` cells are read, shared across the ranges: smallest
    first, each gets an even share of what is left; a range larger than its
    share is read from its first rows, in row order. Deterministic, so apply
    reads the same cells again. Ranges not read in full are listed in
    `partial` as [range, cells read, cells in the range]."""
    if not ranges:
        return {"cells": {}, "grids": {}, "partial": []}
    sizes = _sizes(sheets_api.get_tabs(spreadsheet_id))
    grids = {r.tab: sizes[r.tab] for r in ranges if r.tab in sizes}
    clipped = [(r, c) for r in ranges if r.tab in sizes
               for c in [_clip(r, sizes[r.tab])] if c is not None]
    order = sorted(range(len(clipped)), key=lambda i: (_count(clipped[i][1]), i))
    remaining, bounded, partial = GUARD_CELLS, [], []
    for n, i in enumerate(order):
        original, rng = clipped[i]
        share, total = remaining // (len(order) - n), _count(rng)
        width = rng.end_col - rng.start_col
        if total <= share:
            take = rng
        elif share >= width:
            take = a1.Range(rng.tab, rng.start_row, rng.start_row + share // width,
                            rng.start_col, rng.end_col)
        elif share > 0:
            take = a1.Range(rng.tab, rng.start_row, rng.start_row + 1,
                            rng.start_col, rng.start_col + share)
        else:
            take = None
        got = _count(take) if take else 0
        remaining -= got
        if take:
            bounded.append(take)
        if got < total:
            partial.append([str(original), got, total])
    cells = guard.read(spreadsheet_id, bounded)[1] if bounded else {}
    return {"cells": guard.fingerprint(cells, list(cells)), "grids": grids, "partial": partial}


def _check_raw(old: dict[str, Any], new: dict[str, Any]) -> None:
    moved = [f"  {t}: {old['grids'].get(t)} rows×columns → {new['grids'].get(t)}"
             for t in sorted(set(old["grids"]) | set(new["grids"]))
             if old["grids"].get(t) != new["grids"].get(t)]
    if moved:
        raise reply.Refusal("Nothing was written: a tab the raw requests touch changed size "
                            "since the plan.\n" + "\n".join(moved) + "\nMake a new plan.")
    guard.check(old["cells"], new["cells"], "the raw requests' ranges", one_tab=False)


# ---- plan and apply ---------------------------------------------------------------


def plan(spreadsheet_id: str, tool: str, raw_writes: list[dict[str, Any]],
         requests: list[dict[str, Any]], notes: list[str]) -> str:
    if not raw_writes and not requests:
        return "\n".join(["no change: nothing to write, no plan made", *notes])
    prepared = writes.prepare(spreadsheet_id, raw_writes) if raw_writes else None
    ranges = affected(spreadsheet_id, requests)
    payload = {"op": OP, "tool": tool, "writes": raw_writes, "requests": requests,
               "fingerprint": prepared.fingerprint if prepared else {},
               "raw": _raw_fingerprint(spreadsheet_id, ranges)}
    plan_id = state_store.current().save_plan(payload, spreadsheet_id=spreadsheet_id,
                                              caller=_caller())
    lines = [f"plan {plan_id} (valid 1 hour, single use): {len(raw_writes)} "
             f"{'write' if len(raw_writes) == 1 else 'writes'}, {len(requests)} raw "
             f"{'request' if len(requests) == 1 else 'requests'}, nothing written yet"]
    if prepared:
        lines.append(f"  writes touch {len(prepared.touched)} cells")
        lines += ["  " + d for d in prepared.diff[:writes.DIFF_LINES]]
        if len(prepared.diff) > writes.DIFF_LINES:
            lines.append(f"  +{len(prepared.diff) - writes.DIFF_LINES} more changes")
        lines += [f"warning: {w}" for w in prepared.warnings]
    if requests:
        kinds = ", ".join(dict.fromkeys(next(iter(r), "?") for r in requests))
        lines.append(f"  raw: {kinds}; affected: {_names(ranges)}")
        for name, got, total in payload["raw"].get("partial", []):
            lines.append(f"  stale-write check partial: {name} (first {got:,} of {total:,} "
                         "cells, plus the tab's size)")
    lines += notes
    lines.append(f'apply: {tool} with plan_id="{plan_id}" and dry_run=false; it re-reads the touched cells first '
                 "and refuses the whole batch if any changed")
    return "\n".join(lines)


def _names(ranges: list[a1.Range]) -> str:
    return ", ".join(str(r) for r in ranges) or "the spreadsheet (no tab named)"


def apply(spreadsheet_id: str, plan_id: str, payload: dict[str, Any]) -> str:
    state_store.current().expire_plan(plan_id)     # single use, applied or refused
    raw_writes, requests = payload["writes"], payload["requests"]
    prepared = writes.prepare(spreadsheet_id, raw_writes) if raw_writes else None
    if prepared:
        guard.check(payload["fingerprint"], prepared.fingerprint, "this write",
                    prepared.one_tab)
    ranges = affected(spreadsheet_id, requests)
    _check_raw(payload["raw"], _raw_fingerprint(spreadsheet_id, ranges))
    body = (prepared.requests if prepared else []) + requests
    replies = sheets_api.batch_update(spreadsheet_id, body).get("replies") or []
    writes.after_write(spreadsheet_id)
    lines = []
    if prepared:
        lines.append(writes.receipt(spreadsheet_id, prepared))
    if requests:
        raw_replies = replies[len(body) - len(requests):]
        lines.append(f"applied {len(requests)} raw "
                     f"{'request' if len(requests) == 1 else 'requests'}; affected: "
                     f"{_names(ranges)}")
        for request, answer in zip(requests, raw_replies, strict=False):
            if answer:
                lines.append(f"  {next(iter(request))}: "
                             f"{json.dumps(answer, ensure_ascii=False)[:300]}")
    return "\n".join(lines)


def load(spreadsheet_id: str, plan_id: str, tool: str) -> dict[str, Any]:
    payload = writes.load(spreadsheet_id, plan_id)
    if payload.get("op") != OP or payload.get("tool") != tool:
        raise reply.Refusal(f"Plan {plan_id} was not made by {tool}; apply it with the tool "
                            "that made it.")
    return payload
