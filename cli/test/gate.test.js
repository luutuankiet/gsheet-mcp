// The acceptance gate of spec "CLI: gsheet", run offline: gsheet pull on the CSV
// fixtures/bulk/generate.py writes, then the totals against the ground truth.
// A live run against $BULK_SHEET_ID is opt-in and not part of this file.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { test } from "node:test";

import { BULK, bulkCsv, gsheet, manifest, sql, workdir } from "./helpers.js";

const TAB = "messy-sales-200k-raw";
const csv = bulkCsv("messy-sales-200k.csv");
const truth = JSON.parse(readFileSync(join(BULK, "golden-data-ground-truth.json"), "utf-8"))["messy-sales-200k.csv"].answers;

// Region, status and the like are typed by hand in the sheet: "north ", "NORTH", "Canceled".
const norm = (col) => `replace(lower(regexp_replace(${col}, '\\s', '', 'g')), 'canceled', 'cancelled')`;
const lowerKeys = (o) => Object.fromEntries(Object.entries(o).map(([k, v]) => [k.toLowerCase().replace(/\s/g, ""), v]));
const byKey = (rows) => Object.fromEntries(rows.map((r) => [r.k, Number(r.v)]));

test("acceptance gate: messy-sales-200k-raw through gsheet pull and the clean view", { skip: !csv && "generate.py could not write the CSV" }, async (t) => {
  const dir = workdir(t);
  const m = manifest(dir, { title: "bulk", tabs: [{ tab: TAB, csv,
    regions: [{ alias: "t1", range: `'${TAB}'!A4:T200064`, kind: "table", label: "Order", header_rows: 2 }] }] });

  const pulled = await gsheet(["pull", m], dir);
  assert.equal(pulled.code, 0, pulled.err);
  assert.match(pulled.out, /bulk\.messy_sales_200k_raw from .* · 200000 rows · /);

  const [total] = await sql(dir, "SELECT sum(revenue)::DECIMAL(18,2)::VARCHAR AS v, count(*) AS n FROM bulk.messy_sales_200k_raw");
  assert.equal(total.v, "66304772.44", "198914317.32 would mean the subtotals were counted");
  assert.equal(Number(total.n), truth.data_rows);

  const region = await sql(dir, `SELECT ${norm("location_region")} AS k, round(sum(revenue) * 100)::BIGINT AS v, count(*) AS n
    FROM bulk.messy_sales_200k_raw GROUP BY 1`);
  assert.deepEqual(byKey(region), lowerKeys(truth.revenue_by_region_cents));
  assert.deepEqual(byKey(region.map((r) => ({ k: r.k, v: r.n }))), lowerKeys(truth.rows_by_region));

  const status = await sql(dir, `SELECT ${norm("fulfilment_status")} AS k, count(*) AS v FROM bulk.messy_sales_200k_raw GROUP BY 1`);
  assert.deepEqual(byKey(status), lowerKeys(truth.rows_by_status));

  const [blank] = await sql(dir, "SELECT count(*) FILTER (WHERE date IS NULL) AS v FROM bulk.messy_sales_200k_raw");
  assert.equal(Number(blank.v), truth.order_date_blank_rows);
  assert.equal(truth.order_date_blank_rows, 2012);

  // The same number through `gsheet query`, as an agent would read it.
  const q = await gsheet(["query", "SELECT sum(revenue)::DECIMAL(18,2) AS revenue FROM bulk.messy_sales_200k_raw"], dir);
  assert.equal(q.code, 0, q.err);
  assert.match(q.out, /66304772\.44/);
  assert.match(q.out, /\(1 row\)/);
});
