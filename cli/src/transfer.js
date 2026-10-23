// Moving bundles through the short-lived signed URLs a tool reply hands out.
//
// gdoc holds no credential: a bundle link from pull_doc or push_doc is a
// signed GET, and the upload slot in the manifest is a signed PUT. Both are
// plain GCS V4 signed URLs on storage.googleapis.com.

import { CliError } from "./workingcopy.js";

export const BUNDLE_CONTENT_TYPE = "application/gzip";

const snippet = async (res) => (await res.text().catch(() => "")).replace(/\s+/g, " ").slice(0, 300);

function expiredHint(status, body) {
  return status === 400 || status === 403
    ? /expired|ExpiredToken|Request has expired/i.test(body)
    : false;
}

/** The bundle at a signed GET `url`. */
export async function download(url, { fetch = globalThis.fetch } = {}) {
  let parsed;
  try {
    parsed = new URL(url);
  } catch {
    throw new CliError(`${JSON.stringify(url)} is not a URL. Pass the bundle link from a pull_doc or push_doc reply.`, 2);
  }
  if (!/^https?:$/.test(parsed.protocol)) throw new CliError(`${url} is not an http(s) link to a bundle.`, 2);
  let res;
  try {
    res = await fetch(url);
  } catch (e) {
    throw new CliError(`could not reach ${parsed.host}: ${e.cause?.message ?? e.message}. ` +
      `A sandbox has to allow ${parsed.host}.`);
  }
  if (!res.ok) {
    const body = await snippet(res);
    if (expiredHint(res.status, body)) {
      throw new CliError(`the bundle link has expired (HTTP ${res.status}). Call pull_doc again for a fresh one.`);
    }
    throw new CliError(`downloading the bundle failed: HTTP ${res.status} ${body}`);
  }
  return Buffer.from(await res.arrayBuffer());
}

/** PUT a packed bundle to the signed upload `url`. */
export async function upload(url, data, { fetch = globalThis.fetch } = {}) {
  let res;
  try {
    res = await fetch(url, { method: "PUT", body: data, headers: { "Content-Type": BUNDLE_CONTENT_TYPE } });
  } catch (e) {
    throw new CliError(`could not reach ${new URL(url).host}: ${e.cause?.message ?? e.message}. ` +
      `A sandbox has to allow ${new URL(url).host}.`);
  }
  if (!res.ok) {
    const body = await snippet(res);
    if (expiredHint(res.status, body)) {
      throw new CliError(`the upload URL has expired (HTTP ${res.status}). ${FRESH_UPLOAD}`);
    }
    throw new CliError(`uploading the bundle failed: HTTP ${res.status} ${body}`);
  }
}

export const FRESH_UPLOAD = "Call the push_doc MCP tool with no upload to get a fresh upload URL, " +
  "then run gdoc push --upload <that URL>.";

/** When a V4 signed URL stops working, from its X-Goog-Date and X-Goog-Expires, as an ISO string. */
export function signedUrlExpiry(url) {
  let params;
  try {
    params = new URL(url).searchParams;
  } catch {
    return null;
  }
  const date = /^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z$/.exec(params.get("X-Goog-Date") ?? "");
  const seconds = Number(params.get("X-Goog-Expires"));
  if (!date || !Number.isInteger(seconds) || seconds <= 0) return null;
  const [, y, mo, d, h, mi, s] = date;
  const start = Date.UTC(+y, +mo - 1, +d, +h, +mi, +s);
  return new Date(start + seconds * 1000).toISOString().replace(/\.000Z$/, "Z");
}

/**
 * The handle push_doc takes for an upload: the object name in the upload URL,
 * `google-docs-mcp/AbC.tar.gz` from
 * `https://storage.googleapis.com/<bucket>/google-docs-mcp/AbC.tar.gz?X-Goog-...`.
 */
export function uploadHandle(url) {
  const u = new URL(url);
  const parts = u.pathname.split("/").slice(1).map(decodeURIComponent);
  return u.hostname.endsWith(".storage.googleapis.com") ? parts.join("/") : parts.slice(1).join("/");
}

/** `in 5h 3m`, `3m ago`. */
export function relative(iso, now) {
  const ms = Date.parse(iso) - now.getTime();
  const mins = Math.round(Math.abs(ms) / 60000);
  const text = mins >= 60 ? `${Math.floor(mins / 60)}h ${mins % 60}m` : `${mins}m`;
  return ms >= 0 ? `in ${text}` : `${text} ago`;
}
