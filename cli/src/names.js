// Copied from google-docs-mcp@2fb4652 cli/src/names.js; slug kept, tab file and folder names replaced by SQL names for tables.
// Names in the workspace database (spec "CLI: gsheet", table names).
//
// slug: fold to ASCII (accents dropped, `đ` read as `d`), lowercase, every
// run of other characters becomes one `-`, cut to 40 characters. `ident`
// turns a slug into a SQL name with `_` for `-`.

import { parseRange } from "./csv.js";

export const SLUG_MAX = 40;

export function slug(text, fallback) {
  const folded = text.replace(/đ/g, "d").replace(/Đ/g, "D")
    .normalize("NFKD").replace(/\p{M}/gu, "").toLowerCase();
  const out = folded.replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "")
    .slice(0, SLUG_MAX).replace(/-+$/, "");
  return out || fallback;
}

/** A SQL identifier from any text: the slug with `_` for `-`, never starting with a digit. */
export const ident = (text, fallback) => {
  const s = slug(String(text ?? ""), fallback).replace(/-/g, "_");
  return /^[0-9]/.test(s) ? `_${s}` : s;
};

/** A native Table's name as is (case kept) when it is already a plain SQL name, else its `ident`. */
const ownName = (name, base) => (/^[A-Za-z_][A-Za-z0-9_]*$/.test(name) ? name : ident(name, base));

/**
 * The table name of each region on one tab, in the order given: a native
 * Table keeps its name; a tab holding one region takes the tab slug; a tab
 * holding several takes `<tab slug>_<n>`, numbered in reading order (top row,
 * then left column).
 */
export function tableNames(tab, regions) {
  const base = ident(tab, "tab");
  const order = regions
    .map((r, i) => ({ i, rng: parseRange(r.range) }))
    .sort((a, b) => a.rng.r0 - b.rng.r0 || a.rng.c0 - b.rng.c0);
  const names = new Array(regions.length);
  order.forEach(({ i }, n) => {
    const own = regions[i].table_name;
    names[i] = own ? ownName(own, base) : regions.length === 1 ? base : `${base}_${n + 1}`;
  });
  return names;
}
