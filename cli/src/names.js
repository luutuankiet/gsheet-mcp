// File and folder names in a working copy: `02.1-data-model.gdoc.md`, `canary--184D13nr`.
//
// Mirrors src/google_docs_mcp/bundle/names.py and is pinned by
// fixtures/bundles/names.json: fold to ASCII (accents dropped, `đ` read as
// `d`), lowercase, every run of other characters becomes one `-`, cut to 40
// characters. The top-level part of a tab number is padded to two digits so
// tab files list in doc order.

const NUMBER = /^\d+(\.\d+)*$/;
export const SLUG_MAX = 40;

export function slug(text, fallback) {
  const folded = text.replace(/đ/g, "d").replace(/Đ/g, "D")
    .normalize("NFKD").replace(/\p{M}/gu, "").toLowerCase();
  const out = folded.replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "")
    .slice(0, SLUG_MAX).replace(/-+$/, "");
  return out || fallback;
}

/** `("2.1", "Data model")` -> `02.1-data-model.gdoc.md`. */
export function tabFileName(number, name) {
  if (!NUMBER.test(number)) throw new Error(`${JSON.stringify(number)} is not a tab number like 2 or 2.1`);
  const [top, ...rest] = number.split(".");
  const padded = [top.padStart(2, "0"), ...rest].join(".");
  return `${padded}-${slug(name, "tab")}.gdoc.md`;
}

/** The working copy folder: title slug plus the first 8 characters of the doc id. */
export function folderName(title, docId) {
  return `${slug(title, "doc")}--${docId.slice(0, 8)}`;
}
