// Copied from google-docs-mcp@2fb4652 cli/src/cli.js; gdoc commands replaced by pull, status, query and push, help pages inline.
// Argument parsing and dispatch for the gsheet binary; bin/gsheet.js calls run().

import { readFileSync } from "node:fs";

import { pull, push, query, status } from "./commands.js";
import { CliError } from "./errors.js";

export const VERSION = JSON.parse(readFileSync(new URL("../package.json", import.meta.url), "utf-8")).version;

export const MAIN = `gsheet: Google Sheets tables in a local DuckDB workspace (.gsheet/workspace.duckdb).

gsheet holds no credential. Call the gsheet-mcp tools and hand gsheet the links they return.

  gsheet pull <manifest link or file> [--tab T] [--range alias|A1] [--as name]
  gsheet status [<fingerprint>] [--tab T]
  gsheet query "<sql>"
  gsheet push <sql or file.sql> --to <tab or range> --upload <url> [--spreadsheet id]

gsheet help <command> for one command.
`;

export const COMMANDS = {
  pull: `gsheet pull <manifest link or file> [--tab T] [--range alias|A1] [--as name]

Takes the "manifest <url>" line of a pull_tables reply. Downloads each tab to
.gsheet/<spreadsheet id>/<gid>.csv and loads every region: <name>_raw (text,
rows as in the sheet), the clean view <name>, and <name>_dropped. Schema: the
spreadsheet title as a SQL name. Table names: a native Table keeps its name, a
tab with one region takes the tab name, several take <tab>_<n> in reading order.
--as renames when exactly one region loads. One retry on HTTP 429.
`,
  status: `gsheet status [<fingerprint>] [--tab T]

Without a fingerprint, lists each pulled tab with the fingerprint it was pulled
at and the table_status call to make. With the fingerprint table_status
returned, says "changed: ..." or "unchanged at head and tail". Never pulls.
`,
  query: `gsheet query "<sql>"

Runs SQL on the workspace and prints up to 50 rows and the row count.
`,
  push: `gsheet push <sql or file.sql> --to <tab or range> --upload <url> [--spreadsheet id]

Runs the SQL and PUTs the result as CSV (header row first) to the upload_slot
URL. Prints the upload handle and the write_cells call that plans the landing;
nothing in the sheet changes until that plan is applied.
`,
};

const VALUE_FLAGS = {
  pull: ["--tab", "--range", "--as"],
  status: ["--tab"],
  query: [],
  push: ["--to", "--upload", "--spreadsheet"],
};

function parse(command, args) {
  const flags = {};
  const positional = [];
  for (let i = 0; i < args.length; i++) {
    const a = args[i];
    const [name, inline] = a.startsWith("--") ? a.split(/=(.*)/s) : [a];
    if (VALUE_FLAGS[command].includes(name)) {
      const value = inline ?? args[++i];
      if (value === undefined) throw new CliError(`${name} needs a value; see gsheet help ${command}`, 2);
      flags[name.slice(2)] = value;
    } else if (a.startsWith("-") && a !== "-") {
      throw new CliError(`unknown option ${a} for gsheet ${command}; see gsheet help ${command}`, 2);
    } else positional.push(a);
  }
  return { flags, positional };
}

/**
 * Run gsheet with `argv` (the arguments after the binary). `io` supplies
 * `out` and `err` (string writers), `cwd`, `fetch` and `sleep`; returns the exit code.
 */
export async function run(argv, io = {}) {
  const full = {
    out: (s) => process.stdout.write(s),
    err: (s) => process.stderr.write(s),
    cwd: process.cwd(),
    fetch: globalThis.fetch,
    ...io,
  };
  const [command, ...args] = argv;
  if (command === undefined || command === "--help" || command === "-h" || command === "help") {
    const page = command === "help" && args[0] ? COMMANDS[args[0]] : undefined;
    full.out(page ?? MAIN);
    return 0;
  }
  if (command === "--version" || command === "-v") {
    full.out(`${VERSION}\n`);
    return 0;
  }
  if (!(command in COMMANDS)) {
    full.err(`gsheet: unknown command ${JSON.stringify(command)}. Commands: ${Object.keys(COMMANDS).join(", ")}.\n` +
      "Run gsheet --help for the manual.\n");
    return 2;
  }
  if (args.includes("--help") || args.includes("-h")) {
    full.out(COMMANDS[command]);
    return 0;
  }
  try {
    const { flags, positional } = parse(command, args);
    if (positional.length > 1) throw new CliError(`too many arguments for gsheet ${command}; quote the SQL. See gsheet help ${command}`, 2);
    const [first] = positional;
    switch (command) {
      case "pull": return await pull({ source: first, tab: flags.tab, range: flags.range, as: flags.as }, full);
      case "status": return await status({ fingerprint: first, tab: flags.tab }, full);
      case "query": return await query({ sql: first }, full);
      case "push": return await push({ sql: first, to: flags.to, uploadUrl: flags.upload, spreadsheet: flags.spreadsheet }, full);
    }
  } catch (e) {
    if (e instanceof CliError) {
      full.err(`gsheet ${command}: ${e.message}\n`);
      return e.code ?? 1;
    }
    throw e;
  }
  return 0;
}
