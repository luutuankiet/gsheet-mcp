// The four gsheet commands: pull, status, query, push (spec "CLI: gsheet").
//
// Every command takes `io` from cli.js: out, err, cwd, fetch, sleep.

import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";

import { cut, parseCsv, parseRange, toCsv } from "./csv.js";
import { CliError } from "./errors.js";
import { ident, tableNames } from "./names.js";
import { download, FRESH_UPLOAD, upload, uploadHandle } from "./transfer.js";
import { DB_FILE, lit, loadRegion, open, recordSource } from "./workspace.js";

export const MANIFEST_FORMAT = "gsheet-pull/1";
export const QUERY_ROWS = 50;
const HEAD_ROWS = 500;
const TAIL_ROWS = 500;

const dbPath = (io) => resolve(io.cwd, DB_FILE);

async function openExisting(io) {
  if (!existsSync(dbPath(io))) {
    throw new CliError(`no workspace here (${DB_FILE}); run gsheet pull <manifest link> first.`);
  }
  return open(dbPath(io));
}

// ------------------------------------------------------------------ pull ---

async function readManifest(source, io) {
  const bytes = await download(source, { ...io, what: "downloading the manifest" });
  let m;
  try {
    m = JSON.parse(bytes.toString("utf-8"));
  } catch {
    throw new CliError(`${source} is not a pull manifest (not JSON). Pass the manifest link from a pull_tables reply.`, 2);
  }
  if (m?.format !== MANIFEST_FORMAT) {
    throw new CliError(`${source} is not a ${MANIFEST_FORMAT} manifest (format ${JSON.stringify(m?.format)}).`, 2);
  }
  return m;
}

/** The regions of one manifest tab to load, each with its table name. */
function pick(tab, { range }) {
  const names = tableNames(tab.tab, tab.regions);
  let picked = tab.regions.map((r, i) => ({ ...r, table: names[i] }));
  if (range) {
    const hit = picked.filter((r) => r.alias === range || r.range === range ||
      r.range.replace(/^.*!/, "") === range.replace(/^.*!/, ""));
    // A range that is no region of the pull is cut as given, header in its first row.
    picked = hit.length ? hit : [{ alias: null, range: range.includes("!") ? range : `'${tab.tab}'!${range}`,
      kind: "range", header_rows: 1, table: `${ident(tab.tab, "tab")}_${ident(range.replace(/^.*!/, ""), "range")}` }];
  }
  return picked;
}

export async function pull({ source, tab, range, as }, io) {
  if (!source) throw new CliError("needs the manifest link from a pull_tables reply; see gsheet pull --help", 2);
  const m = await readManifest(source, io);
  const tabs = m.tabs.filter((t) => tab === undefined || t.tab === tab || String(t.gid) === tab);
  if (!tabs.length) {
    throw new CliError(`no tab ${JSON.stringify(tab)} in this pull; it has ${m.tabs.map((t) => JSON.stringify(t.tab)).join(", ")}.`, 2);
  }
  const jobs = tabs.flatMap((t) => pick(t, { range }).map((r) => ({ t, r })));
  if (as !== undefined) {
    if (jobs.length !== 1) throw new CliError(`--as names one table, but this pull loads ${jobs.length}; add --tab or --range.`, 2);
    jobs[0].r.table = ident(as, "table");
  }
  const schema = ident(m.title || m.spreadsheet_id, "sheet");
  const db = await open(dbPath(io));
  try {
    io.out(`pulled "${m.title}" (${m.spreadsheet_id}) into schema ${schema}\n`);
    const csvs = new Map();
    for (const { t, r } of jobs) {
      if (!csvs.has(t.gid)) {
        const file = join(".gsheet", m.spreadsheet_id, `${t.gid}.csv`);
        const bytes = await download(t.csv, { ...io, what: `downloading tab "${t.tab}"` });
        mkdirSync(dirname(resolve(io.cwd, file)), { recursive: true });
        writeFileSync(resolve(io.cwd, file), bytes);
        csvs.set(t.gid, { file, rows: parseCsv(bytes.toString("utf-8")) });
      }
      const { file, rows } = csvs.get(t.gid);
      const rng = parseRange(r.range);
      const cells = cut(rows, rng);
      if (!cells.length) {
        io.out(`  skipped ${r.range}: no rows in the CSV there\n`);
        continue;
      }
      const res = await loadRegion(db, { schema, table: r.table, cells, headerRows: r.header_rows ?? 1,
        firstRow: rng.r0, firstColumn: rng.c0 });
      await recordSource(db, { spreadsheetId: m.spreadsheet_id, title: m.title, schema, table: r.table, tab: t.tab,
        gid: t.gid, range: r.range, headerRows: r.header_rows ?? 1, fingerprint: t.fingerprint, csvFile: file,
        pulledAt: m.pulled_at.replace("T", " ").replace("Z", "") });
      const typed = res.typed.map(([c, ty]) => `${c} ${ty}`).join(", ") || "all text";
      io.out(`  ${schema}.${r.table} from ${r.range} · ${res.rows} rows · ${res.dropped} dropped · ` +
        `${res.failures} cast failures · ${typed}\n`);
    }
  } finally {
    db.close();
  }
  return 0;
}

// ---------------------------------------------------------------- status ---

const fpParts = (fp) => {
  const parts = String(fp).trim().split(":");
  if (parts.length !== 3) throw new CliError(`${JSON.stringify(fp)} is not a fingerprint like 40x8:1a2b3c4d5e6f:9f8e7d6c5b4a.`, 2);
  return parts;
};

export async function status({ fingerprint, tab }, io) {
  const db = await openExisting(io);
  let pulled;
  try {
    pulled = (await db.rows(`SELECT DISTINCT spreadsheet_id, title, tab, fingerprint, max(pulled_at)::VARCHAR AS at
      FROM main._sources ${tab ? `WHERE tab = ${lit(tab)}` : ""} GROUP BY ALL ORDER BY title, tab`)).objects;
  } finally {
    db.close();
  }
  if (!pulled.length) throw new CliError(tab ? `tab ${JSON.stringify(tab)} was never pulled here.` : "nothing pulled yet.");
  if (fingerprint === undefined) {
    for (const p of pulled) {
      io.out(`tab "${p.tab}" of "${p.title}" · fingerprint ${p.fingerprint} · pulled ${p.at}\n` +
        `  table_status(spreadsheet="${p.spreadsheet_id}", tab="${p.tab}")  then  gsheet status <its fingerprint> --tab "${p.tab}"\n`);
    }
    return 0;
  }
  if (pulled.length > 1) throw new CliError("several tabs are pulled here; name one with --tab.", 2);
  const [p] = pulled;
  const [size0, head0, tail0] = fpParts(p.fingerprint);
  const [size1, head1, tail1] = fpParts(fingerprint);
  const changes = [];
  if (size0 !== size1) changes.push(`size ${size0} → ${size1}`);
  if (head0 !== head1) changes.push(`first ${HEAD_ROWS} rows`);
  if (tail0 !== tail1) changes.push(`last ${TAIL_ROWS} rows`);
  io.out(`tab "${p.tab}" · pulled ${p.fingerprint} · now ${fingerprint}\n` + (changes.length
    ? `changed: ${changes.join(", ")}; pull it again (pull_tables, then gsheet pull)\n`
    : "unchanged at head and tail (the rows between were not compared)\n"));
  return 0;
}

// ----------------------------------------------------------------- query ---

const cellText = (v) => (v === null || v === undefined ? "" : typeof v === "object" ? JSON.stringify(v) : String(v));

export async function query({ sql }, io) {
  if (!sql) throw new CliError('needs SQL, like gsheet query "SELECT 1"', 2);
  const db = await openExisting(io);
  try {
    const { columns, rows } = await db.rows(sql);
    const shown = rows.slice(0, QUERY_ROWS).map((r) => r.map(cellText));
    const widths = columns.map((c, j) => Math.min(40, Math.max(c.length, ...shown.map((r) => r[j].length))));
    const line = (cells) => cells.map((c, j) => c.slice(0, widths[j]).padEnd(widths[j])).join(" | ").trimEnd();
    io.out([line(columns), widths.map((w) => "-".repeat(w)).join("-+-"), ...shown.map(line)].join("\n") + "\n");
    io.out(`(${rows.length} row${rows.length === 1 ? "" : "s"}${rows.length > QUERY_ROWS ? `, first ${QUERY_ROWS} shown` : ""})\n`);
  } catch (e) {
    if (e instanceof CliError) throw e;
    throw new CliError(e.message);
  } finally {
    db.close();
  }
  return 0;
}

// ------------------------------------------------------------------ push ---

export async function push({ sql, to, uploadUrl, spreadsheet }, io) {
  if (!sql) throw new CliError("needs SQL or a .sql file; see gsheet push --help", 2);
  if (!to) throw new CliError("needs --to <tab or range>: where the result lands (an existing tab, or an empty range in one; create a tab first with edit_sheet add_tab).", 2);
  if (!uploadUrl) {
    throw new CliError("needs --upload <url>. Call the upload_slot MCP tool for a signed upload URL, " +
      "then run gsheet push again with --upload <that URL>.", 2);
  }
  const path = resolve(io.cwd, sql.trim());
  const isFile = /\.sql$/i.test(sql.trim());
  if (isFile && !existsSync(path)) throw new CliError(`file not found: ${sql.trim()} (looked in ${io.cwd}).`, 2);
  const text = isFile ? readFileSync(path, "utf-8") : sql;
  const db = await openExisting(io);
  let columns, rows, id = spreadsheet;
  try {
    ({ columns, rows } = await db.rows(text));
    if (!id) {
      const ids = (await db.rows("SELECT DISTINCT spreadsheet_id FROM main._sources")).rows.map((r) => r[0]);
      if (ids.length !== 1) {
        throw new CliError(`name the spreadsheet with --spreadsheet <id>; ${ids.length ? `this workspace holds ${ids.length}` : "nothing is pulled here"}.`, 2);
      }
      [id] = ids;
    }
  } catch (e) {
    if (e instanceof CliError) throw e;
    throw new CliError(e.message);
  } finally {
    db.close();
  }
  const csv = toCsv([columns, ...rows.map((r) => r.map(cellText))]);
  await upload(uploadUrl, Buffer.from(csv, "utf-8"), io);
  const handle = uploadHandle(uploadUrl);
  io.out(`uploaded ${rows.length + 1} rows x ${columns.length} columns (header row included) · handle ${handle}\n` +
    "Nothing in the sheet has changed. Plan the landing with:\n" +
    `  write_cells(spreadsheet="${id}", range=${JSON.stringify(to)}, upload="${handle}")\n` +
    `It replies with a plan id; write_cells(spreadsheet="${id}", plan_id=<that id>) applies it. ${FRESH_UPLOAD.replace(/^Call/, "If the URL expired, call")}\n`);
  return 0;
}

