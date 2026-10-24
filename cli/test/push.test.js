// gsheet push: what it does with its SQL argument before anything is run or uploaded.

import assert from "node:assert/strict";
import { test } from "node:test";

import { gsheet, workdir } from "./helpers.js";

const UPLOAD_URL = "https://storage.example.test/bucket/gsheet-mcp/up.csv";

test("a .sql path that names no file is refused as file not found, never run as SQL", async (t) => {
  const dir = workdir(t);
  const calls = [];
  const fetch = async (url) => { calls.push(url); return new Response(""); };
  const r = await gsheet(["push", "queries/missing.sql", "--to", "Out", "--upload", UPLOAD_URL], dir, { fetch });
  assert.equal(r.code, 2);
  assert.match(r.err, /^gsheet push: file not found: queries\/missing\.sql/);
  assert.doesNotMatch(r.err, /Parser Error|syntax error/i);
  assert.deepEqual(calls, [], "nothing is uploaded");
});
