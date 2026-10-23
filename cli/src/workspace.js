// The workspace database, `.gsheet/workspace.duckdb` (spec "CLI: gsheet").
//
// One schema per spreadsheet (a slug of its title) and `main._sources`. Each
// pulled region becomes:
//   <name>_raw      every column text, rows exactly as in the sheet, plus `_row` (sheet row)
//   <name>          the clean view: joined header names, typed columns, dropped rows left out
//   <name>_dropped  spacer rows, suspected totals and footnotes, with a `reason`
// and its cast failures land in `<schema>._cast_failures`.
//
// Casting lives in SQL macros stored in the database, so the clean views and
// the type decisions use the same rules.

import { mkdirSync } from "node:fs";
import { dirname } from "node:path";

import { DuckDBInstance } from "@duckdb/node-api";

import { columnLetter } from "./csv.js";
import { slug } from "./names.js";

export const DB_FILE = ".gsheet/workspace.duckdb";
export const TYPED_SHARE = 0.98;

/** A SQL identifier from any text: the shared slug with `_` for `-`. */
export const ident = (text, fallback) => {
  const s = slug(String(text ?? ""), fallback).replace(/-/g, "_");
  return /^[0-9]/.test(s) ? `_${s}` : s;
};
export const q = (name) => `"${String(name).replace(/"/g, '""')}"`;
export const lit = (text) => `'${String(text).replace(/'/g, "''")}'`;

// The casting rules. gs_text strips padding, a leading apostrophe, `$` and
// inner spaces. Numbers: thousands separators go; (default) a comma decimal
// such as `1.234,50` or `12,5` is read European, accounting parentheses mean
// negative, and `12%` is 0.12.
const MACROS = [
  `CREATE OR REPLACE MACRO main.gs_text(s) AS
     regexp_replace(regexp_replace(trim(coalesce(s, '')), '^''', ''), '[\\s$]', '', 'g')`,
  `CREATE OR REPLACE MACRO main.gs_decimal(c) AS
     CASE WHEN regexp_full_match(c, '[-+]?\\d{1,3}(\\.\\d{3})+,\\d+|[-+]?\\d+,\\d{1,2}')
          THEN replace(replace(c, '.', ''), ',', '.') ELSE replace(c, ',', '') END`,
  `CREATE OR REPLACE MACRO main.gs_num(s) AS
     CASE WHEN gs_text(s) = '' THEN NULL ELSE
       TRY_CAST(gs_decimal(regexp_replace(gs_text(s), '[()%]', '', 'g')) AS DECIMAL(38, 10))
       * CASE WHEN regexp_full_match(gs_text(s), '\\(.*\\)') THEN -1 ELSE 1 END
       * CASE WHEN contains(gs_text(s), '%') THEN 0.01 ELSE 1 END END`,
  // Dates: ISO; d/m/yyyy or m/d/yyyy by the column's day_first; `9 Jul 2026`;
  // (default) a 5-digit spreadsheet serial counted from 1899-12-30.
  `CREATE OR REPLACE MACRO main.gs_date(s, day_first) AS
     CASE
       WHEN regexp_full_match(trim(coalesce(s, '')), '\\d{4}-\\d{1,2}-\\d{1,2}') THEN TRY_CAST(trim(s) AS DATE)
       WHEN regexp_full_match(trim(coalesce(s, '')), '\\d{1,2}[/.-]\\d{1,2}[/.-]\\d{4}') THEN
         CAST(CASE WHEN day_first THEN TRY_STRPTIME(regexp_replace(trim(s), '[.-]', '/', 'g'), '%d/%m/%Y')
                   ELSE TRY_STRPTIME(regexp_replace(trim(s), '[.-]', '/', 'g'), '%m/%d/%Y') END AS DATE)
       WHEN regexp_full_match(trim(coalesce(s, '')), '\\d{1,2} [A-Za-z]{3,9},? \\d{4}') THEN
         CAST(coalesce(TRY_STRPTIME(replace(trim(s), ',', ''), '%d %b %Y'),
                       TRY_STRPTIME(replace(trim(s), ',', ''), '%d %B %Y')) AS DATE)
       WHEN regexp_full_match(trim(coalesce(s, '')), '[1-9]\\d{4}') THEN
         CAST(DATE '1899-12-30' + CAST(trim(s) AS INTEGER) AS DATE)
     END`,
  `CREATE OR REPLACE MACRO main.gs_bool(s) AS
     CASE lower(trim(coalesce(s, ''))) WHEN 'true' THEN true WHEN 'false' THEN false END`,
  `CREATE TABLE IF NOT EXISTS main._sources (
     spreadsheet_id VARCHAR, title VARCHAR, schema_name VARCHAR, table_name VARCHAR, tab VARCHAR,
     gid BIGINT, "range" VARCHAR, header_rows INTEGER, fingerprint VARCHAR, csv_file VARCHAR,
     pulled_at TIMESTAMP)`,
];

export async function open(path) {
  mkdirSync(dirname(path), { recursive: true });
  const instance = await DuckDBInstance.create(path);
  const conn = await instance.connect();
  for (const sql of MACROS) await conn.run(sql);
  return {
    conn,
    async rows(sql) {
      const reader = await conn.runAndReadAll(sql);
      return { columns: reader.columnNames(), rows: reader.getRowsJson(), objects: reader.getRowObjectsJson() };
    },
    run: (sql) => conn.run(sql),
    close() {
      conn.closeSync();
      instance.closeSync();
    },
  };
}

// --------------------------------------------------------------- headers ---

/** Column names from the header rows: non-blank parts joined, blanks `col_<letter>`, repeats `_2`. */
export function headerNames(headerRows, width, firstColumn = 1) {
  const seen = new Map();
  const names = [];
  for (let j = 0; j < width; j++) {
    const parts = headerRows.map((r) => (r[j] ?? "").trim()).filter(Boolean);
    let name = parts.length ? ident(parts.join(" "), "") : "";
    if (!name) name = `col_${columnLetter(firstColumn + j)}`;
    let unique = name;
    for (let k = 2; seen.has(unique); k++) unique = `${name}_${k}`;
    seen.set(unique, true);
    names.push(unique);
  }
  return names;
}

// ---------------------------------------------------------- dropped rows ---

const TOTAL = /^\s*(grand\s*total|sub-?\s*total|total)\b/i;

/**
 * Why each data row is left out of the clean view, by index into `data`:
 * `spacer` (every cell blank), `total` (a cell starting with Total, Subtotal
 * or Grand total) or `footnote` (default: a row with at most one filled cell
 * below the last row with two or more).
 */
export function dropReasons(data) {
  const filled = data.map((r) => r.reduce((n, v) => n + (v.trim() ? 1 : 0), 0));
  let lastDense = -1;
  filled.forEach((n, i) => {
    if (n >= 2 && !data[i].some((v) => TOTAL.test(v))) lastDense = i;
  });
  const reasons = new Map();
  data.forEach((r, i) => {
    if (filled[i] === 0) reasons.set(i, "spacer");
    else if (r.some((v) => TOTAL.test(v))) reasons.set(i, "total");
    else if (i > lastDense && filled[i] <= 1 && data[0].length > 1) reasons.set(i, "footnote");
  });
  return reasons;
}

// ----------------------------------------------------------------- load ---

/**
 * Load one region into the workspace. `cells` are the region's rows (header
 * rows first), `firstRow` its sheet row and `firstColumn` its column number.
 * Returns { schema, table, rows, dropped, typed: [[column, type]], failures }.
 */
export async function loadRegion(db, { schema, table, cells, headerRows, firstRow, firstColumn }) {
  const width = cells[0]?.length ?? 0;
  const head = cells.slice(0, headerRows);
  const data = cells.slice(headerRows);
  const names = headerNames(head, width, firstColumn);
  const raw = `${q(schema)}.${q(`${table}_raw`)}`;
  const letters = names.map((_, j) => columnLetter(firstColumn + j));

  await db.run(`CREATE SCHEMA IF NOT EXISTS ${q(schema)}`);
  await db.run(`DROP VIEW IF EXISTS ${q(schema)}.${q(table)}`);
  await db.run(`CREATE OR REPLACE TABLE ${raw} (_row INTEGER, ${letters.map((l) => `${q(l)} VARCHAR`).join(", ")})`);
  const appender = await db.conn.createAppender(`${table}_raw`, schema);
  cells.forEach((row, i) => {
    appender.appendInteger(firstRow + i);
    for (const v of row) appender.appendVarchar(v);
    appender.endRow();
  });
  appender.closeSync();

  const reasons = dropReasons(data);
  const dropTable = `${q(schema)}.${q(`${table}_dropped`)}`;
  await db.run(`CREATE OR REPLACE TABLE ${dropTable} AS SELECT ''::VARCHAR AS reason, * FROM ${raw} WHERE false`);
  if (reasons.size) {
    const values = [...reasons].map(([i, why]) => `(${firstRow + headerRows + i}, ${lit(why)})`).join(", ");
    await db.run(`INSERT INTO ${dropTable} SELECT d.reason, r.* FROM ${raw} r
                  JOIN (VALUES ${values}) d(_row, reason) USING (_row) ORDER BY r._row`);
  }
  const kept = `FROM ${raw} WHERE _row >= ${firstRow + headerRows}
                AND _row NOT IN (SELECT _row FROM ${dropTable})`;

  // Decide each column's type on the kept rows.
  const probes = letters.map((l, j) => {
    const c = q(l);
    return `count(*) FILTER (WHERE trim(${c}) <> '') AS n${j},
      count(gs_num(${c})) AS num${j},
      coalesce(bool_and(gs_num(${c}) = trunc(gs_num(${c}))), true) AS int${j},
      coalesce(bool_or(TRY_CAST(regexp_extract(trim(${c}), '^(\\d{1,2})[/.-]\\d{1,2}[/.-]\\d{4}$', 1) AS INTEGER) > 12), false) AS dayfirst${j},
      count(gs_date(${c}, true)) AS dated${j}, count(gs_date(${c}, false)) AS datem${j},
      count(gs_bool(${c})) AS bool${j}`;
  });
  const [stats] = width ? (await db.rows(`SELECT ${probes.join(", ")} ${kept}`)).objects : [{}];

  const exprs = [];
  const typed = [];
  for (let j = 0; j < width; j++) {
    const c = q(letters[j]);
    const n = Number(stats[`n${j}`]);
    const share = (k) => n > 0 && Number(stats[k]) / n >= TYPED_SHARE;
    let expr = `CASE WHEN trim(${c}) = '' THEN NULL ELSE ${c} END`;
    let type = "VARCHAR";
    if (share(`num${j}`)) {
      type = stats[`int${j}`] ? "BIGINT" : "DECIMAL(38, 10)";
      expr = `CAST(gs_num(${c}) AS ${type})`;
    } else if (share(stats[`dayfirst${j}`] ? `dated${j}` : `datem${j}`)) {
      type = "DATE";
      expr = `gs_date(${c}, ${stats[`dayfirst${j}`] ? "true" : "false"})`;
    } else if (share(`bool${j}`)) {
      type = "BOOLEAN";
      expr = `gs_bool(${c})`;
    }
    exprs.push({ letter: letters[j], name: names[j], expr, type });
    if (type !== "VARCHAR") typed.push([names[j], type]);
  }

  const failuresTable = `${q(schema)}._cast_failures`;
  await db.run(`CREATE TABLE IF NOT EXISTS ${failuresTable}
                ("table" VARCHAR, "column" VARCHAR, "row" INTEGER, raw_value VARCHAR)`);
  await db.run(`DELETE FROM ${failuresTable} WHERE "table" = ${lit(table)}`);
  for (const e of exprs.filter((x) => x.type !== "VARCHAR")) {
    await db.run(`INSERT INTO ${failuresTable} SELECT ${lit(table)}, ${lit(e.name)}, _row, ${q(e.letter)}
                  ${kept} AND trim(${q(e.letter)}) <> '' AND (${e.expr}) IS NULL ORDER BY _row`);
  }
  await db.run(`CREATE VIEW ${q(schema)}.${q(table)} AS SELECT
                ${exprs.map((e) => `${e.expr} AS ${q(e.name)}`).join(",\n")} ${kept} ORDER BY _row`);

  const count = async (sql) => Number((await db.rows(sql)).rows[0][0]);
  return {
    schema, table, rows: data.length - reasons.size, dropped: reasons.size, typed,
    failures: await count(`SELECT count(*) FROM ${failuresTable} WHERE "table" = ${lit(table)}`),
  };
}

/** Record a pulled region in `main._sources`, replacing an earlier pull of the same table. */
export async function recordSource(db, s) {
  await db.run(`DELETE FROM main._sources WHERE schema_name = ${lit(s.schema)} AND table_name = ${lit(s.table)}`);
  await db.run(`INSERT INTO main._sources VALUES (${[s.spreadsheetId, s.title, s.schema, s.table, s.tab]
    .map(lit).join(", ")}, ${Number(s.gid) || 0}, ${lit(s.range)}, ${Number(s.headerRows)},
    ${lit(s.fingerprint ?? "")}, ${lit(s.csvFile)}, ${lit(s.pulledAt)}::TIMESTAMP)`);
}
