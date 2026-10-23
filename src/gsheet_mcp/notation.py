# Copied from google-docs-mcp@2fb4652 src/<package>/render.py; inline runs kept without Markdown escaping, cut and token_estimate taken from tools/read.py, the cell notation added.
"""The text notation for one cell: rich text as inline Markdown, checkboxes,
dropdowns, chips, ratings, formulas and the size cap. `read_range` builds its
lines from these; receipts and grep reuse them."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

MAX_CELL_CHARS = 500
RATING = re.compile(r"^[★☆]+$")


def cut(text: str, limit: int = MAX_CELL_CHARS) -> str:
    """Cap a cell's text at `limit` characters; 0 lifts the cap."""
    if not limit or len(text) <= limit:
        return text
    return f"{text[:limit]}…[+{len(text) - limit:,} chars]"


def token_estimate(chars: int) -> str:
    """About four characters a token, rounded so it reads at a glance."""
    tokens = chars // 4
    if tokens < 1000:
        return f"~{tokens} tokens"
    if tokens < 10_000:
        return f"~{tokens / 1000:.1f}k tokens".replace(".0k", "k")
    return f"~{round(tokens / 1000)}k tokens"


# ---- inline runs --------------------------------------------------------------


@dataclass
class _Seg:
    text: str
    bold: bool = False
    italic: bool = False
    strike: bool = False
    link: str | None = None

    def key(self) -> tuple:
        return (self.bold, self.italic, self.strike, self.link)


def _split_whitespace(segs: list[_Seg]) -> list[_Seg]:
    """Move leading and trailing spaces out of bold, italic and strikethrough,
    because `** bold **` is not emphasis in Markdown."""
    out: list[_Seg] = []
    for s in segs:
        if not (s.bold or s.italic or s.strike):
            out.append(s)
            continue
        core = s.text.strip(" \t")
        if not core:
            out.append(_Seg(s.text, link=s.link))
            continue
        lead = s.text[:len(s.text) - len(s.text.lstrip(" \t"))]
        trail = s.text[len(s.text.rstrip(" \t")):]
        if lead:
            out.append(_Seg(lead, link=s.link))
        out.append(_Seg(core, s.bold, s.italic, s.strike, s.link))
        if trail:
            out.append(_Seg(trail, link=s.link))
    merged: list[_Seg] = []
    for s in out:
        if merged and merged[-1].key() == s.key():
            merged[-1].text += s.text
        else:
            merged.append(s)
    return merged


_STYLE_ORDER = (("bold", "**"), ("italic", "*"), ("strike", "~~"))


def _inline(segs: list[_Seg]) -> str:
    """Markdown for a list of segments that share no link."""
    out: list[str] = []
    open_styles: list[str] = []
    for s in segs:
        wanted = [name for name, _ in _STYLE_ORDER if getattr(s, name)]
        keep = 0
        while keep < len(open_styles) and keep < len(wanted) and open_styles[keep] == wanted[keep]:
            keep += 1
        for name in reversed(open_styles[keep:]):
            out.append(dict(_STYLE_ORDER)[name])
        open_styles = open_styles[:keep]
        for name in wanted[keep:]:
            out.append(dict(_STYLE_ORDER)[name])
            open_styles.append(name)
        out.append(s.text)
    for name in reversed(open_styles):
        out.append(dict(_STYLE_ORDER)[name])
    return "".join(out)


def _render_runs(segs: list[_Seg]) -> str:
    segs = _split_whitespace(segs)
    out: list[str] = []
    i = 0
    while i < len(segs):
        url = segs[i].link
        j = i
        while j < len(segs) and segs[j].link == url:
            j += 1
        group = segs[i:j]
        if url is None:
            out.append(_inline(group))
        else:
            text = "".join(s.text for s in group)
            styled = any(s.bold or s.italic or s.strike for s in group)
            if text == url and not styled:
                out.append(f"<{url}>")
            else:
                target = url.replace("(", "%28").replace(")", "%29").replace(" ", "%20")
                out.append(f"[{_inline(group)}]({target})")
        i = j
    return "".join(out)


def rich_text(cell: dict[str, Any]) -> str:
    """The cell's text with its runs as inline Markdown. Cell-wide bold and
    colour are styles, not Markdown; colour and underline on runs are dropped."""
    text = str(cell.get("formattedValue", ""))
    runs = cell.get("textFormatRuns") or []
    chips = cell.get("chipRuns") or []
    link = cell.get("hyperlink")
    if not runs and not chips:
        if link:
            return _render_runs([_Seg(text, link=link)])
        return text
    starts = sorted({0, len(text), *(r.get("startIndex", 0) for r in runs),
                     *(c.get("startIndex", 0) for c in chips)})
    segs: list[_Seg] = []
    for a, b in zip(starts, starts[1:], strict=False):
        if a >= b:
            continue
        run = next((r for r in reversed(runs) if r.get("startIndex", 0) <= a), {})
        chip = next((c for c in reversed(chips) if c.get("startIndex", 0) <= a), {})
        fmt = run.get("format") or {}
        piece = text[a:b]
        chip_text = _chip(chip.get("chip") or {}, piece)
        if chip_text:
            segs.append(_Seg(chip_text))
            continue
        segs.append(_Seg(piece, bool(fmt.get("bold")), bool(fmt.get("italic")),
                         bool(fmt.get("strikethrough")),
                         (fmt.get("link") or {}).get("uri") or link))
    return _render_runs(segs)


def _chip(chip: dict[str, Any], text: str) -> str:
    """`@[name](url)` for a person or file chip."""
    if person := chip.get("personProperties"):
        return f"@[{text.lstrip('@')}](mailto:{person.get('email', '')})"
    if rich := chip.get("richLinkProperties"):
        return f"@[{text}]({rich.get('uri', '')})"
    return ""


# ---- the value of one cell ---------------------------------------------------------


def dropdown_values(cell: dict[str, Any]) -> tuple[str, ...] | None:
    """The list of a dropdown rule, or None when the cell has no dropdown."""
    condition = (cell.get("dataValidation") or {}).get("condition") or {}
    kind = condition.get("type")
    values = tuple(str(v.get("userEnteredValue", "")) for v in condition.get("values") or [])
    if kind == "ONE_OF_LIST":
        return values
    if kind == "ONE_OF_RANGE":
        return ("from " + (values[0] if values else "a range"),)
    return None


def is_checkbox(cell: dict[str, Any]) -> bool:
    condition = (cell.get("dataValidation") or {}).get("condition") or {}
    return condition.get("type") == "BOOLEAN"


def formula(cell: dict[str, Any]) -> str | None:
    return (cell.get("userEnteredValue") or {}).get("formulaValue")


def value(cell: dict[str, Any], dropdown_ref: str | None = None) -> str:
    """The cell as read: `[x]`, `On track ▾v1`, `★★★☆☆ (3)`, `=IMAGE(url)`,
    or its rich text. Formulas, notes and the cap are added by the caller."""
    effective = cell.get("effectiveValue") or {}
    shown = str(cell.get("formattedValue", ""))
    if is_checkbox(cell):
        return "[x]" if effective.get("boolValue") or shown.upper() == "TRUE" else "[ ]"
    if dropdown_ref is not None:
        allowed = dropdown_values(cell) or ()
        outside = (allowed and not allowed[0].startswith("from ")
                   and shown and shown not in allowed)
        return f"{shown} ▾{dropdown_ref}{'!' if outside else ''}"
    if "numberValue" in effective and RATING.match(shown):
        return f"{shown} ({effective['numberValue']:g})"
    text = formula(cell) or ""
    if not shown and text.upper().startswith("=IMAGE("):
        return text
    return rich_text(cell)
