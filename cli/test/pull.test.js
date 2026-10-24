// gsheet pull: table names (several regions, native Tables) and the one retry on HTTP 429.

import assert from "node:assert/strict";
import { readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { test } from "node:test";

import { columnNumber, parseCsv, toCsv } from "../src/csv.js";
import { tableNames } from "../src/names.js";
import { bulkCsv, gsheet, manifest, sql, workdir } from "./helpers.js";

const tablesIn = async (dir, schema) => (await sql(dir,
  `SELECT table_name AS t FROM information_schema.tables WHERE table_schema = '${schema}' ORDER BY 1`)).map((r) => r.t);

/** Put a header row plus `rows` made-up rows into `grid` at A1 cell `at`, `width` columns wide. */
function block(grid, at, width, rows, name) {
  const [, letters, row] = at.match(/^([A-Z]+)(\d+)$/);
  const c0 = columnNumber(letters) - 1;
  const lines = [Array.from({ length: width }, (_, j) => `${name} col ${j + 1}`),
    ...Array.from({ length: rows }, (_, i) => Array.from({ length: width }, (_, j) => (j === 0 ? `${name}-${i + 1}` : String(i * width + j))))];
  lines.forEach((cells, i) => {
    const r = grid[Number(row) - 1 + i];
    while (r.length < c0) r.push("");
    r.splice(c0, cells.length, ...cells);
  });
}

const cleanWide = bulkCsv("clean-wide-50k.csv");

test("clean-wide-50k: three regions load as clean_wide_50k_1..3 in reading order", { skip: !cleanWide && "generate.py could not write the CSV" }, async (t) => {
  const dir = workdir(t);
  // The generated CSV is the A1:L50001 table only; the two small tables beside
  // it were added by hand in the live sheet, so made-up ones stand in for them.
  const grid = parseCsv(readFileSync(cleanWide, "utf-8"));
  block(grid, "Q18", 8, 18, "side");
  block(grid, "O55", 8, 18, "below");
  const csv = join(dir, "clean-wide-50k.csv");
  writeFileSync(csv, toCsv(grid));
  const tab = "clean-wide-50k";
  const m = manifest(dir, { title: "bulk", tabs: [{ tab, csv: `file://${csv}`, regions: [
    // Not in reading order on purpose: names follow the sheet, not the manifest.
    { alias: "t3", range: `'${tab}'!O55:V73`, kind: "table", header_rows: 1 },
    { alias: "t1", range: `'${tab}'!A1:L50001`, kind: "table", header_rows: 1 },
    { alias: "t2", range: `'${tab}'!Q18:X36`, kind: "table", header_rows: 1 },
  ] }] });

  const pulled = await gsheet(["pull", m], dir);
  assert.equal(pulled.code, 0, pulled.err);
  const tables = await tablesIn(dir, "bulk");
  for (const name of ["clean_wide_50k_1", "clean_wide_50k_2", "clean_wide_50k_3"]) {
    assert.ok(tables.includes(name), `${name} in ${tables}`);
    assert.ok(tables.includes(`${name}_raw`) && tables.includes(`${name}_dropped`));
  }
  const counts = await sql(dir, `SELECT (SELECT count(*) FROM bulk.clean_wide_50k_1) AS a,
    (SELECT count(*) FROM bulk.clean_wide_50k_2) AS b, (SELECT min(side_col_1) FROM bulk.clean_wide_50k_2) AS b1,
    (SELECT count(*) FROM bulk.clean_wide_50k_3) AS c, (SELECT min(below_col_1) FROM bulk.clean_wide_50k_3) AS c1`);
  assert.deepEqual(counts.map((r) => [Number(r.a), Number(r.b), r.b1, Number(r.c), r.c1]),
    [[50000, 18, "side-1", 18, "below-1"]]);
  const sources = await sql(dir, `SELECT table_name AS t, "range" AS r FROM main._sources ORDER BY 1`);
  assert.deepEqual(sources.map((s) => s.t), ["clean_wide_50k_1", "clean_wide_50k_2", "clean_wide_50k_3"]);
});

test("a native Table keeps its name; other regions on the tab are numbered", async (t) => {
  const dir = workdir(t);
  const csv = join(dir, "native.csv");
  writeFileSync(csv, toCsv([
    ["Order", "Region", "Total", "", "Note"],
    ["A-1", "North", "10.50", "", "x"],
    ["A-2", "South", "4", "", "y"],
    ["A-3", "West", "1,200.00", "", ""],
  ]));
  const tab = "native-table";
  const m = manifest(dir, { title: "canary", tabs: [{ tab, csv, regions: [
    { alias: "t1", range: `'${tab}'!A1:C4`, kind: "table", header_rows: 1, table_name: "Orders" },
    { alias: "t2", range: `'${tab}'!E1:E3`, kind: "table", header_rows: 1 },
  ] }] });
  const pulled = await gsheet(["pull", m], dir);
  assert.equal(pulled.code, 0, pulled.err);
  assert.match(pulled.out, /canary\.Orders from 'native-table'!A1:C4 · 3 rows/);
  assert.deepEqual(await tablesIn(dir, "canary"), ["Orders", "Orders_dropped", "Orders_raw", "_cast_failures", "native_table_2",
    "native_table_2_dropped", "native_table_2_raw"]);
  const q = await gsheet(["query", "SELECT sum(total) AS total FROM canary.Orders"], dir);
  assert.equal(q.code, 0, q.err);
  assert.match(q.out, /1214\.5/);
});

test("tableNames: a Table name that is not a plain SQL name becomes one", () => {
  assert.deepEqual(tableNames("Sales 2026", [{ range: "C1:D2", table_name: "Q3 sales" }, { range: "A1:B2" }]),
    ["q3_sales", "sales_2026_1"]);
  assert.deepEqual(tableNames("Sales 2026", [{ range: "A1:B2" }]), ["sales_2026"]);
});

// ------------------------------------------------------------------- 429 ---

const MANIFEST_URL = "https://storage.example.test/bucket/gsheet-mcp/pull.json";
const CSV_URL = "https://storage.example.test/bucket/gsheet-mcp/tab.csv";

function fakeFetch(script) {
  const calls = [];
  const fetch = async (url) => {
    calls.push(url);
    const step = script.shift() ?? 200;
    if (step !== 200) return new Response("slow down", { status: step.status ?? step, headers: step.headers });
    if (url === MANIFEST_URL) {
      return new Response(JSON.stringify({ format: "gsheet-pull/1", spreadsheet_id: "s1", title: "t",
        pulled_at: "2026-10-23T10:00:00Z", tabs: [{ tab: "a", gid: 0, fingerprint: "2x1:0:0", csv: CSV_URL,
          regions: [{ alias: "t1", range: "'a'!A1:A2", kind: "table", header_rows: 1 }] }] }));
    }
    return new Response("n\n1\n");
  };
  return { fetch, calls };
}

test("HTTP 429: one retry after the backoff, then the pull goes on", async (t) => {
  const dir = workdir(t);
  const slept = [];
  const { fetch, calls } = fakeFetch([{ status: 429, headers: { "Retry-After": "7" } }, 200, 429, 200]);
  const r = await gsheet(["pull", MANIFEST_URL], dir, { fetch, sleep: async (ms) => slept.push(ms) });
  assert.equal(r.code, 0, r.err);
  assert.deepEqual(calls, [MANIFEST_URL, MANIFEST_URL, CSV_URL, CSV_URL]);
  assert.deepEqual(slept, [7000, 5000], "Retry-After when given, else 5 s");
  assert.deepEqual(await sql(dir, "SELECT n::VARCHAR AS n FROM t.a"), [{ n: "1" }]);
});

test("HTTP 429 twice: exit code 1 and a message, no third try", async (t) => {
  const dir = workdir(t);
  const slept = [];
  const { fetch, calls } = fakeFetch([429, 429, 200]);
  const r = await gsheet(["pull", MANIFEST_URL], dir, { fetch, sleep: async (ms) => slept.push(ms) });
  assert.equal(r.code, 1);
  assert.equal(calls.length, 2);
  assert.equal(slept.length, 1);
  assert.match(r.err, /^gsheet pull: downloading the manifest was refused twice with HTTP 429/);
});

test("Retry-After is capped at 60 s", async (t) => {
  const dir = workdir(t);
  const slept = [];
  const { fetch } = fakeFetch([{ status: 429, headers: { "Retry-After": "3600" } }]);
  const r = await gsheet(["pull", MANIFEST_URL], dir, { fetch, sleep: async (ms) => slept.push(ms) });
  assert.equal(r.code, 0, r.err);
  assert.deepEqual(slept, [60000]);
});
