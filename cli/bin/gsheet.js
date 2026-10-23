#!/usr/bin/env node
// The gdoc binary: pull a Google Doc into a working copy, push edits back.
// `gdoc --help` is the manual; the commands live in src/commands.js.

import { run } from "../src/cli.js";

process.exitCode = await run(process.argv.slice(2));
