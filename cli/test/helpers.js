// Shared by the node tests: a throwaway workspace, a manifest file and a run of the real CLI.

import { spawnSync } from "node:child_process";
import { existsSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { run } from "../src/cli.js";
import { DB_FILE, open } from "../src/workspace.js";

export const REPO = resolve(fileURLToPath(new URL("../..", import.meta.url)));
export const BULK = join(REPO, "fixtures", "bulk");

/** A fresh empty folder for one test's workspace, removed when the test ends. */
export function workdir(t) {
  const dir = mkdtempSync(join(tmpdir(), "gsheet-cli-"));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  return dir;
}

/** Write a gsheet-pull/1 manifest into `dir` and return its path. */
export function manifest(dir, { title = "test sheet", id = "sheet-id", tabs }) {
  const path = join(dir, "manifest.json");
  writeFileSync(path, JSON.stringify({
    format: "gsheet-pull/1", spreadsheet_id: id, title, pulled_at: "2026-10-23T10:00:00Z",
    tabs: tabs.map((t, i) => ({ gid: i, rows: 0, cols: 0, fingerprint: "1x1:000000000000:000000000000", ...t })),
  }));
  return path;
}

/** Run gsheet with `argv` in `cwd`; returns { code, out, err }. */
export async function gsheet(argv, cwd, io = {}) {
  let out = "";
  let err = "";
  const code = await run(argv, { cwd, out: (s) => { out += s; }, err: (s) => { err += s; }, ...io });
  return { code, out, err };
}

/** The rows of `sql` against the workspace in `cwd`, as objects. */
export async function sql(cwd, text) {
  const db = await open(join(cwd, DB_FILE));
  try {
    return (await db.rows(text)).objects;
  } finally {
    db.close();
  }
}

/** The path of a generated bulk CSV, running fixtures/bulk/generate.py once if it is missing; null if that fails. */
export function bulkCsv(name) {
  const path = join(BULK, name);
  if (!existsSync(path)) spawnSync("python3", [join(BULK, "generate.py")], { stdio: "ignore" });
  return existsSync(path) ? path : null;
}
