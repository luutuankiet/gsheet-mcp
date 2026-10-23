// Argument parsing and dispatch for the gdoc binary; bin/gdoc.js calls run().

import { readFileSync, statSync } from "node:fs";
import { resolve } from "node:path";

import { BundleError } from "./bundle.js";
import { diff, merge, pull, push, status } from "./commands.js";
import { diagram, diagramEdit } from "./diagram.js";
import { COMMANDS, MAIN } from "./help.js";
import { CliError } from "./workingcopy.js";

export const VERSION = JSON.parse(readFileSync(new URL("../package.json", import.meta.url), "utf-8")).version;

const isDir = (path) => {
  try {
    return statSync(path).isDirectory();
  } catch {
    return false;
  }
};

function parse(command, args) {
  const flags = { dryRun: false, uploadUrl: undefined, noUpload: false, ops: undefined, opsFile: undefined };
  const positional = [];
  for (let i = 0; i < args.length; i++) {
    const a = args[i];
    if (a === "--dry-run" && command === "push") flags.dryRun = true;
    else if (a === "--upload" && command === "push") {
      flags.uploadUrl = args[++i];
      if (!flags.uploadUrl) throw new CliError("--upload needs the upload URL from a push_doc reply", 2);
    } else if (a.startsWith("--upload=") && command === "push") flags.uploadUrl = a.slice("--upload=".length);
    else if (a === "--no-upload" && command === "diagram") flags.noUpload = true;
    else if ((a === "--ops" || a === "--ops-file") && command === "diagram") {
      const value = args[++i];
      if (value === undefined) throw new CliError(`${a} needs a value; see gdoc diagram --help`, 2);
      flags[a === "--ops" ? "ops" : "opsFile"] = value;
    } else if (a.startsWith("-") && a !== "-") throw new CliError(`unknown option ${a} for gdoc ${command}; see gdoc ${command} --help`, 2);
    else positional.push(a);
  }
  return { flags, positional };
}

/**
 * Run gdoc with `argv` (the arguments after the binary). `io` supplies
 * `out` and `err` (string writers), `cwd`, `now` and `fetch`; returns the exit code.
 */
export async function run(argv, io = {}) {
  const full = {
    out: (s) => process.stdout.write(s),
    err: (s) => process.stderr.write(s),
    cwd: process.cwd(),
    now: () => new Date(),
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
    full.err(`gdoc: unknown command ${JSON.stringify(command)}. Commands: ${Object.keys(COMMANDS).join(", ")}.\n` +
      "Run gdoc --help for the manual.\n");
    return 2;
  }
  if (args.includes("--help") || args.includes("-h")) {
    full.out(COMMANDS[command]);
    return 0;
  }
  try {
    const { flags, positional } = parse(command, args);
    const isEdit = command === "diagram" && positional[0] === "edit";
    if (isEdit) positional.shift();
    const max = { pull: 2, merge: 2, push: 1, status: 1, diff: 2, diagram: 1 }[command];
    if (positional.length > max) throw new CliError(`too many arguments for gdoc ${command}; see gdoc ${command} --help`, 2);
    switch (command) {
      case "pull": return await pull({ url: positional[0], dir: positional[1] }, full);
      case "merge": return await merge({ url: positional[0], dir: positional[1] }, full);
      case "push": return await push({ dir: positional[0], ...flags }, full);
      case "status": return await status({ dir: positional[0] }, full);
      case "diagram":
        if (isEdit) return await diagramEdit({ url: positional[0], ...flags }, full);
        if (positional.length !== 1) throw new CliError("needs one <file.json>, or - for stdin; see gdoc diagram --help", 2);
        return await diagram({ file: positional[0], noUpload: flags.noUpload }, full);
      case "diff": {
        // One argument is a folder if one exists by that name, else a tab.
        let [dir, tab] = positional;
        if (positional.length === 1 && !isDir(resolve(full.cwd, dir))) [dir, tab] = [undefined, dir];
        return await diff({ dir, tab }, full);
      }
    }
  } catch (e) {
    if (e instanceof CliError || e instanceof BundleError) {
      full.err(`gdoc ${command}: ${e.message}\n`);
      return e.code ?? 1;
    }
    throw e;
  }
  return 0;
}
