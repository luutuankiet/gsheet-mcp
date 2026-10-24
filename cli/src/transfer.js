// Copied from google-docs-mcp@2fb4652 cli/src/transfer.js; bundles became CSV and manifest files, local paths and one retry on HTTP 429 added.
// Moving files through the short-lived signed URLs a tool reply hands out.
//
// gsheet holds no credential: a manifest or CSV link from pull_tables is a
// signed GET, and the URL from upload_slot is a signed PUT. Both are plain
// GCS V4 signed URLs on storage.googleapis.com. A manifest or CSV may also be
// a local path or a file: URL, for a pull saved earlier.

import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { CliError } from "./errors.js";

export const CSV_CONTENT_TYPE = "text/csv";
export const RETRY_AFTER_MS = 5000;

const snippet = async (res) => (await res.text().catch(() => "")).replace(/\s+/g, " ").slice(0, 300);

function expiredHint(status, body) {
  return status === 400 || status === 403
    ? /expired|ExpiredToken|Request has expired/i.test(body)
    : false;
}

const defaultSleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** `fetch`, once more after a backoff when the answer is HTTP 429, then a refusal. */
async function fetchOnce429(url, init, { fetch, sleep = defaultSleep, what }) {
  const host = new URL(url).host;
  let waited = 0;
  for (let attempt = 0; ; attempt++) {
    let res;
    try {
      res = await fetch(url, init);
    } catch (e) {
      throw new CliError(`could not reach ${host}: ${e.cause?.message ?? e.message}. ` +
        `A sandbox has to allow ${host}.`);
    }
    if (res.status !== 429) return res;
    if (attempt === 1) {
      throw new CliError(`${what} was refused twice with HTTP 429 (too many requests), ` +
        `the second time after waiting ${waited / 1000} s. Wait a minute and run it again.`);
    }
    const after = Number(res.headers?.get?.("retry-after"));
    waited = Number.isFinite(after) && after > 0 ? Math.min(after * 1000, 60000) : RETRY_AFTER_MS;
    await sleep(waited);
  }
}

/** The bytes at a signed GET `url`, a file: URL or a local path (against `cwd`). */
export async function download(url, { fetch = globalThis.fetch, sleep, cwd = process.cwd(), what = "the download" } = {}) {
  let parsed;
  try {
    parsed = new URL(url);
  } catch {
    parsed = null;
  }
  if (!parsed || parsed.protocol === "file:" || /^[a-z]:$/i.test(parsed.protocol)) {
    const path = parsed?.protocol === "file:" ? fileURLToPath(parsed) : resolve(cwd, url);
    try {
      return await readFile(path);
    } catch (e) {
      throw new CliError(`cannot read ${path}: ${e.code ?? e.message}`, 2);
    }
  }
  if (!/^https?:$/.test(parsed.protocol)) {
    throw new CliError(`${url} is not an http(s) link, a file: URL or a path.`, 2);
  }
  const res = await fetchOnce429(url, undefined, { fetch, sleep, what });
  if (!res.ok) {
    const body = await snippet(res);
    if (expiredHint(res.status, body)) {
      throw new CliError(`the link has expired (HTTP ${res.status}). Call pull_tables again for fresh links.`);
    }
    throw new CliError(`${what} failed: HTTP ${res.status} ${body}`);
  }
  return Buffer.from(await res.arrayBuffer());
}

/** PUT a CSV to the signed upload `url`. */
export async function upload(url, data, { fetch = globalThis.fetch, sleep } = {}) {
  const res = await fetchOnce429(url, { method: "PUT", body: data, headers: { "Content-Type": CSV_CONTENT_TYPE } },
    { fetch, sleep, what: "the upload" });
  if (!res.ok) {
    const body = await snippet(res);
    if (expiredHint(res.status, body)) {
      throw new CliError(`the upload URL has expired (HTTP ${res.status}). ${FRESH_UPLOAD}`);
    }
    throw new CliError(`uploading the CSV failed: HTTP ${res.status} ${body}`);
  }
}

export const FRESH_UPLOAD = "Call the upload_slot MCP tool for a fresh upload URL, " +
  "then run gsheet push ... --upload <that URL>.";

/**
 * The handle write_cells takes for an upload: the object name in the upload
 * URL, `gsheet-mcp/AbC.csv` from
 * `https://storage.googleapis.com/<bucket>/gsheet-mcp/AbC.csv?X-Goog-...`.
 */
export function uploadHandle(url) {
  const u = new URL(url);
  const parts = u.pathname.split("/").slice(1).map(decodeURIComponent);
  if (u.protocol === "memory:") return [u.hostname, ...parts].join("/");
  return u.hostname.endsWith(".storage.googleapis.com") ? parts.join("/") : parts.slice(1).join("/");
}
