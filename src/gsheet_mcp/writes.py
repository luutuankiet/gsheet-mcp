"""`write_cells`'s engine (spec, "Writes"): values, formulas, Markdown rich
text and format patches over one or more ranges, behind the stale-write guard.

    run(spreadsheet_id, writes)          -> receipt (refused over PLAN_THRESHOLD cells)
    plan(spreadsheet_id, writes)         -> dry run: diff, warnings and a plan_id
    apply(spreadsheet_id, plan_id, payload) -> re-read, compare, write, receipt
    after_write(spreadsheet_id)          -> clears the scan and render caches

`writes` is a list of {"range", "values", "format"}. Every value goes through
`updateCells` with the `userEnteredValue` mask (plus `textFormatRuns` only for
cells whose runs are written or dropped), so cell styling is kept.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass, field
from typing import Any

from . import (
    a1,
    addressing,
    auth,
    generation,
    guard,
    notation,
    reader,
    reply,
    scan,
    sheets_api,
    state_store,
)

PLAN_THRESHOLD = 200
RECEIPT_CELLS = 50
DIFF_LINES = 50
OP = "write_cells"
_ISO_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2}))?)?$")
_DATE_TYPES = {"DATE", "DATE_TIME", "TIME"}
_EPOCH = dt.datetime(1899, 12, 30)
# Implementation default: a format patch is split into field paths inside
# these objects only; any other key (a colour, numberFormat) is one whole field.
_SPLIT = {"textFormat", "borders"}


def _caller() -> str | None:
    try:
        return auth.caller_id()
    except auth.NotSignedInError:
        return None


def after_write(spreadsheet_id: str) -> None:
    """Every write through our tools: forget the scan and every cached render."""
    generation.bump(spreadsheet_id)
    scan.clear(spreadsheet_id)


# ---- Markdown rich text -----------------------------------------------------


def markdown_runs(text: str) -> tuple[str, list[dict[str, Any]]]:
    """Inline Markdown (`**bold**`, `*italic*`, `~~strike~~`, `[text](url)`,
    `<url>`, `\\` escapes) as plain text and Google text runs. Text that
    leaves a marker open is taken literally (implementation default)."""
    out: list[str] = []
    runs: list[dict[str, Any]] = []
    state = {"bold": False, "italic": False, "strikethrough": False}

    def emit(piece: str, link: str | None) -> None:
        if not piece:
            return
        fmt = {k: True for k, on in state.items() if on}
        if link:
            fmt["link"] = {"uri": link}
        start = sum(len(p) for p in out)
        if not runs or runs[-1]["format"] != fmt:
            runs.append({"startIndex": start, "format": fmt})
        out.append(piece)

    def scan_text(s: str, link: str | None) -> bool:
        i, buf = 0, ""
        while i < len(s):
            ch = s[i]
            if ch == "\\" and i + 1 < len(s):
                buf += s[i + 1]
                i += 2
                continue
            marker = ("bold" if s.startswith("**", i) else "strikethrough" if s.startswith("~~", i)
                      else "italic" if ch == "*" else None)
            if marker:
                emit(buf, link)
                buf = ""
                state[marker] = not state[marker]
                i += 1 if marker == "italic" else 2
                continue
            if link is None and ch == "[":
                m = re.match(r"\[((?:\\.|[^\]\\])*)\]\(([^)\s]+)\)", s[i:])
                if m:
                    emit(buf, link)
                    buf = ""
                    if not scan_text(m.group(1), m.group(2)):
                        return False
                    i += m.end()
                    continue
            if link is None and ch == "<":
                m = re.match(r"<(https?://[^>\s]+)>", s[i:])
                if m:
                    emit(buf, link)
                    buf = ""
                    emit(m.group(1), m.group(1))
                    i += m.end()
                    continue
            buf += ch
            i += 1
        emit(buf, link)
        return True

    if not scan_text(text, None) or any(state.values()):
        return text, []
    if all(not r["format"] for r in runs):
        return "".join(out), []
    if runs and runs[0]["startIndex"] == 0:
        runs[0].pop("startIndex")
    return "".join(out), runs


def run_kinds(runs: list[dict[str, Any]]) -> list[str]:
    """The kinds of styling in `runs`; a link's own underline and colour count as the link."""
    kinds: list[str] = []
    for run in runs:
        fmt = run.get("format") or {}
        linked = bool(fmt.get("link"))
        for key, word in (("bold", "bold"), ("italic", "italic"), ("strikethrough", "strike"),
                          ("underline", "underline"), ("link", "link")):
            if fmt.get(key) and word not in kinds and not (linked and key == "underline"):
                kinds.append(word)
        if not linked and ("foregroundColor" in fmt or "foregroundColorStyle" in fmt) \
                and "colour" not in kinds:
            kinds.append("colour")
    return kinds


# ---- the request --------------------------------------------------------------


@dataclass
class Write:
    rng: a1.Range                         # with its tab title, the footprint
    sheet_id: int
    values: list[list[Any]] | None
    fmt: dict[str, Any] | None


@dataclass
class Prepared:
    writes: list[Write]
    touched: list[str]
    requests: list[dict[str, Any]]
    fingerprint: dict[str, list[str]]
    diff: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    one_tab: bool = True


def _grid_of(values: Any) -> list[list[Any]]:
    if isinstance(values, list):
        rows = values if values and all(isinstance(r, list) for r in values) else [values]
    else:
        rows = [[values]]
    width = max(len(r) for r in rows) if rows else 0
    return [list(r) + [None] * (width - len(r)) for r in rows]


def _normalise(spreadsheet_id: str, writes: list[dict[str, Any]]) -> list[Write]:
    if not writes:
        raise reply.Refusal("Nothing to write: pass range with values and/or format, or "
                            "writes=[{range, values, format}, ...].")
    tabs = sheets_api.get_tabs(spreadsheet_id)
    out = []
    for w in writes:
        unknown = set(w) - {"range", "values", "format"}
        if unknown:
            raise reply.Refusal(f"A write takes range, values and format, not "
                                f"{', '.join(sorted(unknown))}.")
        if not w.get("range"):
            raise reply.Refusal("Every write needs a range.")
        if w.get("values") is None and not w.get("format"):
            raise reply.Refusal(f"{w['range']}: give values, a format patch, or both.")
        rng = addressing.resolve(spreadsheet_id, w["range"])
        try:
            props = sheets_api.find_tab(tabs, rng.tab)
        except sheets_api.SheetsApiError as e:
            raise reply.Refusal(str(e)) from None
        grid = props.get("gridProperties", {})
        r0, c0 = rng.start_row or 0, rng.start_col or 0
        values = _grid_of(w["values"]) if w.get("values") is not None else None
        if values is not None:
            h, wd = len(values), len(values[0]) if values else 0
            given_h = None if rng.end_row is None else rng.end_row - r0
            given_w = None if rng.end_col is None else rng.end_col - c0
            if not (given_h == given_w == 1 or rng.whole_tab) and (
                    (given_h is not None and given_h != h) or (given_w is not None and given_w != wd)):
                raise reply.Refusal(f"{w['range']} is not {h} rows x {wd} columns like its "
                                    f"values; pass its top-left cell instead.")
            foot = a1.Range(props["title"], r0, r0 + h, c0, c0 + wd)
        else:
            foot = a1.Range(props["title"], r0, rng.end_row or grid.get("rowCount", 0),
                            c0, rng.end_col or grid.get("columnCount", 0))
        if foot.end_row > grid.get("rowCount", 0) or foot.end_col > grid.get("columnCount", 0):
            raise reply.Refusal(f"{foot} does not fit: tab \"{props['title']}\" is "
                                f"{grid.get('rowCount', 0)}x{grid.get('columnCount', 0)}.")
        fmt = w.get("format")
        if fmt is not None and not isinstance(fmt, dict):
            raise reply.Refusal(f"{w['range']}: format is an object such as "
                                '{"textFormat": {"bold": true}}.')
        out.append(Write(foot, props["sheetId"], values, fmt or None))
    return out


def _format_patch(fmt: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    """Field paths and the cell's `userEnteredFormat`; `null` clears its field."""
    fmt = fmt.get("userEnteredFormat", fmt) if set(fmt) == {"userEnteredFormat"} else fmt
    paths: list[str] = []
    value: dict[str, Any] = {}
    for key, v in fmt.items():
        if key in _SPLIT and isinstance(v, dict):
            sub = {}
            for k2, v2 in v.items():
                paths.append(f"userEnteredFormat.{key}.{k2}")
                if v2 is not None:
                    sub[k2] = v2
            if sub:
                value[key] = sub
        else:
            paths.append(f"userEnteredFormat.{key}")
            if v is not None:
                value[key] = v
    return paths, value


def _date_serial(text: str) -> float | None:
    m = _ISO_DATE.match(text)
    if not m:
        return None
    try:
        when = dt.datetime(*(int(g) for g in m.groups() if g is not None))
    except ValueError:
        return None
    days = (when - _EPOCH).total_seconds() / 86400
    return int(days) if days.is_integer() else days


def _cell_value(value: Any, old: dict[str, Any], name: str, warnings: list[str],
                refusals: list[str]) -> tuple[dict[str, Any], bool]:
    """The new cell and whether its runs go in the mask."""
    old_runs = old.get("textFormatRuns") or []
    if notation.is_checkbox(old) and not isinstance(value, bool):
        refusals.append(f"{name} is a checkbox and takes only true or false, not "
                        f"{json.dumps(value, ensure_ascii=False)}")
    allowed = notation.dropdown_values(old)
    if allowed is not None and not allowed[0].startswith("from ") and value not in ("", None) \
            and str(value) not in allowed:
        rule = old.get("dataValidation") or {}
        msg = f"{name}: {json.dumps(value, ensure_ascii=False)} is not in its dropdown list " \
              f"({' | '.join(allowed)})"
        (refusals if rule.get("strict") else warnings).append(msg)
    if isinstance(value, bool):
        new: dict[str, Any] = {"userEnteredValue": {"boolValue": value}}
    elif isinstance(value, int | float):
        new = {"userEnteredValue": {"numberValue": value}}
    elif isinstance(value, str) and value.startswith("="):
        new = {"userEnteredValue": {"formulaValue": value}}
        if value.upper().startswith("=IMAGE("):
            warnings.append(f"{name}: =IMAGE shows only after a person allows external data, "
                            "once per file")
    elif isinstance(value, str):
        number_type = ((old.get("userEnteredFormat") or {}).get("numberFormat") or {}).get("type")
        serial = _date_serial(value) if number_type in _DATE_TYPES else None
        if serial is not None:
            new = {"userEnteredValue": {"numberValue": serial}}
        elif value == "":
            new = {}                                     # clears the value
        else:
            text, runs = markdown_runs(value)
            new = {"userEnteredValue": {"stringValue": text}}
            if runs:
                new["textFormatRuns"] = runs
    else:
        refusals.append(f"{name}: a value is text, a number, true/false, or a formula "
                        f"starting with =, not {json.dumps(value)}")
        new = {}
    old_formula = notation.formula(old)
    if old_formula and "formulaValue" not in new.get("userEnteredValue", {}):
        warnings.append(f"{name}: overwrites the formula {old_formula}")
    styled_runs = [r for r in old_runs if r.get("format")]
    if styled_runs and "textFormatRuns" not in new:
        warnings.append(f"{name}: drops {len(styled_runs)} rich text "
                        f"{'run' if len(styled_runs) == 1 else 'runs'} "
                        f"({', '.join(run_kinds(styled_runs))})")
    with_runs = "textFormatRuns" in new or bool(old_runs)
    if with_runs:
        new.setdefault("textFormatRuns", [])
    return new, with_runs


def _merges(book: dict[str, Any]) -> dict[str, list[scan.Box]]:
    return {s["properties"]["title"]: [scan._grid_box(m) for m in s.get("merges") or []]
            for s in book.get("sheets") or []}


def _flush(requests: list[dict[str, Any]], segment: list[tuple[int, dict[str, Any], bool]],
           sheet_id: int, row_index: int) -> None:
    """One `updateCells` for a run of adjacent cells in a row, then empty `segment`.
    The mask names `textFormatRuns` only when a cell in it writes or drops runs."""
    if not segment:
        return
    mask = "userEnteredValue,textFormatRuns" if any(s[2] for s in segment) \
        else "userEnteredValue"
    requests.append({"updateCells": {
        "start": {"sheetId": sheet_id, "rowIndex": row_index, "columnIndex": segment[0][0]},
        "rows": [{"values": [s[1] for s in segment]}], "fields": mask}})
    segment.clear()


def prepare(spreadsheet_id: str, raw: list[dict[str, Any]]) -> Prepared:
    writes = _normalise(spreadsheet_id, raw)
    book, cells = guard.read(spreadsheet_id, [w.rng for w in writes])
    merges = _merges(book)
    one_tab = len({w.rng.tab for w in writes}) == 1
    touched: list[str] = []
    requests: list[dict[str, Any]] = []
    warnings: list[str] = []
    refusals: list[str] = []
    diff: list[str] = []
    for w in writes:
        title = w.rng.tab or ""
        if w.values is not None:
            for dr, row in enumerate(w.values):
                segment: list[tuple[int, dict[str, Any], bool]] = []
                row_index = w.rng.start_row + dr
                for dc, value in enumerate(row):
                    r, c = row_index, w.rng.start_col + dc
                    if value is None:
                        _flush(requests, segment, w.sheet_id, row_index)
                        continue
                    handle = guard.handle(title, r, c)
                    name = guard.short(handle, one_tab)
                    cover = next((m for m in merges.get(title, []) if m.contains(r, c)
                                  and (m.r0, m.c0) != (r, c)), None)
                    if cover:
                        refusals.append(f"{name} is covered by the merge {cover.a1()}; write "
                                        f"to its top-left cell "
                                        f"{a1.column_letters(cover.c0)}{cover.r0 + 1}")
                    old = cells.get(handle) or {}
                    new, with_runs = _cell_value(value, old, name, warnings, refusals)
                    if handle not in touched:
                        touched.append(handle)
                    before = guard.shown(old)
                    after = ("" if not new.get("userEnteredValue") else
                             str(next(iter(new["userEnteredValue"].values()))))
                    if isinstance(value, str) and "stringValue" in new.get("userEnteredValue", {}):
                        after = value
                    if before != after:
                        diff.append(f"{name}: {json.dumps(before, ensure_ascii=False)} → "
                                    f"{json.dumps(after, ensure_ascii=False)}")
                    if segment and segment[-1][2] != with_runs:
                        _flush(requests, segment, w.sheet_id, row_index)
                    segment.append((c, new, with_runs))
                _flush(requests, segment, w.sheet_id, row_index)
        if w.fmt:
            paths, value = _format_patch(w.fmt)
            if not paths:
                raise reply.Refusal(f"{w.rng}: the format patch names no field.")
            height, width = w.rng.end_row - w.rng.start_row, w.rng.end_col - w.rng.start_col
            requests.append({"updateCells": {
                "start": {"sheetId": w.sheet_id, "rowIndex": w.rng.start_row,
                          "columnIndex": w.rng.start_col},
                "rows": [{"values": [{"userEnteredFormat": value} if value else {}
                                     for _ in range(width)]} for _ in range(height)],
                "fields": ",".join(paths)}})
            for r in range(w.rng.start_row, w.rng.end_row):
                for c in range(w.rng.start_col, w.rng.end_col):
                    handle = guard.handle(title, r, c)
                    if handle not in touched:
                        touched.append(handle)
            diff.append(f"{guard.short(str(w.rng), one_tab)}: format {', '.join(paths)}")
    if refusals:
        raise reply.Refusal("Nothing was written:\n" + "\n".join(f"  {r}" for r in refusals))
    return Prepared(writes, touched, requests, guard.fingerprint(cells, touched), diff,
                    warnings, one_tab)


# ---- the three ways in ----------------------------------------------------------


def _send(spreadsheet_id: str, prepared: Prepared) -> str:
    sheets_api.batch_update(spreadsheet_id, prepared.requests)
    after_write(spreadsheet_id)
    return receipt(spreadsheet_id, prepared)


def run(spreadsheet_id: str, raw: list[dict[str, Any]]) -> str:
    """A small write without a plan: re-read just before writing, then write."""
    prepared = prepare(spreadsheet_id, raw)
    if len(prepared.touched) > PLAN_THRESHOLD:
        raise reply.Refusal(f"This write touches {len(prepared.touched)} cells; over "
                            f"{PLAN_THRESHOLD} needs a plan. Call write_cells with the same "
                            f"writes and dry_run=true, then apply its plan_id.")
    return _send(spreadsheet_id, prepared)


def plan(spreadsheet_id: str, raw: list[dict[str, Any]]) -> str:
    prepared = prepare(spreadsheet_id, raw)
    plan_id = state_store.current().save_plan(
        {"op": OP, "writes": raw, "fingerprint": prepared.fingerprint},
        spreadsheet_id=spreadsheet_id, caller=_caller())
    cells, requests = len(prepared.touched), len(prepared.requests)
    lines = [f"plan {plan_id} (valid 1 hour, single use): {cells} "
             f"{'cell' if cells == 1 else 'cells'}, {requests} "
             f"{'request' if requests == 1 else 'requests'}, nothing written yet"]
    lines += ["  " + d for d in prepared.diff[:DIFF_LINES]]
    if len(prepared.diff) > DIFF_LINES:
        lines.append(f"  +{len(prepared.diff) - DIFF_LINES} more changes")
    lines += [f"warning: {w}" for w in prepared.warnings]
    lines.append(f'apply: write_cells with plan_id="{plan_id}"; it re-reads these cells '
                 "first and refuses the whole batch if any changed")
    return "\n".join(lines)


def apply(spreadsheet_id: str, plan_id: str, payload: dict[str, Any]) -> str:
    store = state_store.current()
    store.expire_plan(plan_id)                 # single use, applied or refused
    prepared = prepare(spreadsheet_id, payload["writes"])
    guard.check(payload["fingerprint"], prepared.fingerprint, "this write", prepared.one_tab)
    return _send(spreadsheet_id, prepared)


def load(spreadsheet_id: str, plan_id: str) -> dict[str, Any]:
    payload = state_store.current().load_plan(plan_id, spreadsheet_id=spreadsheet_id,
                                              caller=_caller())
    if payload is None:
        raise reply.Refusal(f"No plan {plan_id} for you on this spreadsheet: it is unknown, "
                            f"used, expired (1 hour) or another caller's. Make a new plan.")
    return payload


# ---- the receipt ---------------------------------------------------------------


def receipt(spreadsheet_id: str, prepared: Prepared) -> str:
    """The touched cells re-read, in the read notation with its legends, up to 50."""
    lines = [f"wrote {len(prepared.touched)} "
             f"{'cell' if len(prepared.touched) == 1 else 'cells'}"
             + (f", showing the first {RECEIPT_CELLS}" if len(prepared.touched) > RECEIPT_CELLS
                else "")]
    lines += reread(spreadsheet_id, [w.rng for w in prepared.writes],
                    prepared.touched[:RECEIPT_CELLS], prepared.one_tab)
    lines += [f"warning: {w}" for w in prepared.warnings]
    return "\n".join(lines)


def reread(spreadsheet_id: str, ranges: list[a1.Range], handles: list[str],
           one_tab: bool) -> list[str]:
    """`handles` re-read over `ranges`, one line each in the read notation,
    then the style and dropdown legends they use."""
    book = sheets_api.get_spreadsheet(spreadsheet_id, [str(r) for r in ranges],
                                      fields=reader.READ_FIELDS)
    readers = {s["properties"]["title"]: reader.Reader(
        s, detail="default", include_hidden=True, max_chars=notation.MAX_CELL_CHARS,
        images=[], comments=None) for s in book.get("sheets") or []}
    legends = reader.Legends("default")
    for r in readers.values():
        r.legends = legends
    lines: list[str] = []
    styles: set[str] = set()
    rules: set[str] = set()
    for handle in handles:
        rng = a1.parse(handle)
        rd = readers.get(rng.tab or "")
        name = guard.short(handle, one_tab)
        cell = rd.cells.get((rng.start_row, rng.start_col)) if rd else None
        if not cell:
            lines.append(f"{name}: (empty)")
            continue
        text, used = rd.value(cell, with_formula=True)
        rules |= used
        style = legends.style(cell)
        if style:
            styles.add(style)
        lines.append(f"{name}{f' {{{style}}}' if style else ''}: {text}")
    if styles:
        lines.append("styles:")
        lines += [f"  {n} {' · '.join(p)}" for p, n in legends.styles.items() if n in styles]
    lines += [f"{n} dropdown: {' | '.join(v)}" for v, n in legends.rules.items() if n in rules]
    return lines
