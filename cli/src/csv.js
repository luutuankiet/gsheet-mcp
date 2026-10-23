// CSV in and out: RFC 4180 records (quoted fields may hold commas, quotes and
// newlines; a quoted newline stays inside one sheet row), and A1 ranges for
// cutting a tab's export down to one region.

import { CliError } from "./errors.js";

/** Every record of `text` as an array of strings. */
export function parseCsv(text) {
  const rows = [];
  let row = [];
  let field = "";
  let quoted = false;
  let i = text.charCodeAt(0) === 0xfeff ? 1 : 0;
  const n = text.length;
  for (; i < n; i++) {
    const ch = text[i];
    if (quoted) {
      if (ch === '"') {
        if (text[i + 1] === '"') {
          field += '"';
          i++;
        } else quoted = false;
      } else field += ch;
    } else if (ch === '"') quoted = true;
    else if (ch === ",") {
      row.push(field);
      field = "";
    } else if (ch === "\n" || ch === "\r") {
      if (ch === "\r" && text[i + 1] === "\n") i++;
      row.push(field);
      rows.push(row);
      row = [];
      field = "";
    } else field += ch;
  }
  if (field !== "" || row.length) {
    row.push(field);
    rows.push(row);
  }
  return rows;
}

const quote = (v) => (/[",\r\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v);

/** Rows of strings as CSV text, `\n` line ends. */
export function toCsv(rows) {
  return rows.map((r) => r.map((v) => quote(v ?? "")).join(",")).join("\n") + "\n";
}

/** `A` -> 1, `AA` -> 27. */
export function columnNumber(letters) {
  let n = 0;
  for (const ch of letters.toUpperCase()) n = n * 26 + (ch.charCodeAt(0) - 64);
  return n;
}

/** 1 -> `a`, 27 -> `aa`: the letter a blank header falls back to (`col_<letter>`). */
export function columnLetter(n) {
  let s = "";
  for (; n > 0; n = Math.floor((n - 1) / 26)) s = String.fromCharCode(97 + ((n - 1) % 26)) + s;
  return s;
}

/**
 * `'My tab'!B3:F40` -> { tab, r0: 3, r1: 40, c0: 2, c1: 6 } (1-based, inclusive).
 * An open end (`A5:F`, `B:D`) is `Infinity`.
 */
export function parseRange(range) {
  const bang = range.lastIndexOf("!");
  let tab = bang >= 0 ? range.slice(0, bang) : null;
  if (tab && tab.startsWith("'") && tab.endsWith("'")) tab = tab.slice(1, -1).replace(/''/g, "'");
  const cells = bang >= 0 ? range.slice(bang + 1) : range;
  const m = /^([A-Za-z]*)(\d*)(?::([A-Za-z]*)(\d*))?$/.exec(cells);
  if (!m || (!m[1] && !m[2])) throw new CliError(`${JSON.stringify(range)} is not an A1 range like 'Tab'!A1:F40`, 2);
  const [, a, r, b, s] = m;
  const two = m[3] !== undefined || m[4] !== undefined;
  return {
    tab,
    c0: a ? columnNumber(a) : 1,
    r0: r ? Number(r) : 1,
    c1: two ? (b ? columnNumber(b) : Infinity) : a ? columnNumber(a) : Infinity,
    r1: two ? (s ? Number(s) : Infinity) : r ? Number(r) : Infinity,
  };
}

/** The cells of `rows` (a whole tab, from A1) inside `rng`, each row padded to the range width. */
export function cut(rows, rng) {
  const width = Number.isFinite(rng.c1) ? rng.c1 - rng.c0 + 1
    : Math.max(0, ...rows.map((r) => r.length)) - rng.c0 + 1;
  const last = Math.min(rows.length, rng.r1);
  const out = [];
  for (let i = rng.r0; i <= last; i++) {
    const src = rows[i - 1] ?? [];
    const row = new Array(width);
    for (let j = 0; j < width; j++) row[j] = src[rng.c0 - 1 + j] ?? "";
    out.push(row);
  }
  return out;
}
