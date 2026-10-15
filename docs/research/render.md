---
title: Can the server render a sheet range as an image, and what scope does it need
summary: yes, with the spreadsheets scope alone; the editor export link returns a range as PDF, rasterised locally; no PNG format exists; Drive and Apps Script routes compared; also why the Docs MCP render fails
verified: 2026-10-15
ticket: https://github.com/luutuankiet/gsheet-mcp/issues/5
---

# Rendering a sheet range as an image

**Answer.** Yes, and it needs no Drive scope. The editor's own download link,
`https://docs.google.com/spreadsheets/d/<id>/export?format=pdf&gid=<gid>&range=A1:J10`,
called with a bearer token holding only `spreadsheets` (plus `openid`,
`email`), returns that range of a private sheet as a PDF in about 2 s. The
server rasterises a page with pypdfium2 in under 0.15 s. There is no
`format=png`; PDF then rasterise is the route.

| method | scope needed | verified? | fidelity | cost |
|---|---|---|---|---|
| export link, `format=pdf`, `gid` + `range` | `spreadsheets` | ✅ 200 on a private sheet; `drive` also 200; none or `cloud-platform` 401 | colours, merges, rich text, links, images in cells, checkboxes, the chart, native Table banding; dropdown chips lose their pill, rating stars and link-chip icons are off, frozen panes are not drawn | 1.8 to 3.0 s; 18 KB to 700 KB (images embed at full size) |
| export link, `format=png` | none works | ✅ 400 with `spreadsheets` | n/a | n/a |
| export link, `format=csv` / `xlsx` | `spreadsheets` | ✅ 200 | values only / full workbook | csv 5 KB 1.0 s; xlsx 1.4 MB 2.2 s |
| export link, `spreadsheets.readonly`, `drive.readonly`, `drive.file` | expected yes for the first two | ❌ not minted (see Method) | same PDF | same |
| Drive `files.export` `mimeType=application/pdf` | any Drive scope | ✅ 200 with `drive`, 403 with `spreadsheets` | whole workbook, every tab, no `gid` or `range` | 2.2 MB, 20.8 s |
| Drive `thumbnailLink` | any Drive scope | ❌ not tried | first page of the file only, low resolution | n/a |
| Apps Script (`scripts.run` or a web app) | the scopes the script uses; `getAs('application/pdf')` exports the whole spreadsheet | ❌ not tried | same engine as the export link | a script deployment to own |
| Sheets API v4 | n/a | ✅ no render or thumbnail method exists | n/a | n/a |

Rasterising one PDF page (pypdfium2, US Letter portrait) and the image
token cost of the result (width x height / 750, after the 1568 px cap):

| longest side | pixels | raster time | image tokens |
|---|---|---|---|
| 384 | 297 x 384 | 0.06 s | about 150 |
| 768 | 594 x 768 | 0.04 s | about 610 |
| 1024 | 792 x 1024 | 0.05 s | about 1,080 |
| 1568 | 1212 x 1568 | 0.09 s | about 2,530 |

A landscape page costs the same per pixel (1024 x 724 is about 990
tokens). At 1024 px a dense communication sheet is readable cell by cell; at
384 px only layout and colour survive.

## Method

All probes ran on 2026-10-15 from scripts kept locally (not committed,
because they hold internal sample content): `tmp/20261015/render/probe.py`
mints down-scoped access tokens from one Workspace identity's refresh token
(the token endpoint accepts a `scope` subset on a refresh grant), confirms
each with `tokeninfo`, and fetches each route. `raster.py` rasterises pages.

- Target: a **private copy** of `$SAMPLE_SHEET_URL`, made with Drive
  `files.copy` into the identity's own Drive and trashed afterwards, so a
  200 cannot come from link sharing. The original sample is public, so an
  anonymous export of it would succeed and prove nothing.
- Tokens minted: no token, `spreadsheets`, `drive`, `cloud-platform`.
  `spreadsheets.readonly`, `drive.readonly`, `drive.file` and `documents`
  were refused with `invalid_scope`: down-scoping only returns scopes granted
  at consent, and no available identity was granted those. Their rows above
  are inference, not measurement.
- Baseline: the live tab in a browser (public sheet), screenshotted next
  to the PDF renders.

Answers, private copy, same URLs for every token:

| token | `pdf` + range | `pdf` whole tab | `png` | `csv` | `xlsx` | Drive `files.export` |
|---|---|---|---|---|---|---|
| none | 401 | 401 | 401 | 401 | 401 | 403 |
| `spreadsheets` | 200 | 200 | 400 | 200 | 200 | 403 insufficient scopes |
| `drive` | 200 | 429 | 429 | 429 | 429 | 200 |
| `cloud-platform` | 401 | 401 | 401 | 401 | 401 | 403 insufficient scopes |

The `drive` row hit **429** on the four requests after the first: the
probe sends them back to back, and the earlier `spreadsheets` round had
just made five exports. The export host rate-limits bursts per user. The
server should cache by revision, serialise exports per caller and back off
on 429.

## Export options

Accepted (200) and visibly applied: `portrait=false` (landscape), `fitw=true`
(fit to width), `gridlines=false|true`, `size=A4`, `scale=4` (fit to page),
`top_margin`, `bottom_margin`, `left_margin`, `right_margin` (inches),
`sheetnames=false`, `printtitle=false`, `pagenum=UNDEFINED`. Accepted, effect
not checked: `fzr=true` (repeat frozen rows on each page). None of this is a
documented API; the parameters mirror the editor's print dialog.

What to know when using it:

- **Without options the range sits small in a corner.** Default US Letter
  portrait with normal scale put `A1:J10` of a wide tab in the top half of
  the page. `fitw=true`, small margins and `portrait=false` filled the page.
- **A tall range paginates.** `A1:P40` of the chart tab gave 3 pages even
  with `fitw=true`. To get one image, either `scale=4` (fit to page, text
  shrinks) or render several pages, as the Docs MCP does.
- **Size follows embedded images.** Ranges holding images in cells were
  700 KB; a text-only Table range was 18 KB.

## Fidelity against the live tab

| feature | in the PDF render |
|---|---|
| fill colours, fonts, bold and coloured rich text | ✅ match |
| merged header bands and merged blocks | ✅ match |
| hyperlinks | ✅ blue underlined; plain links that are chips in the editor lose the chip icon |
| images in cells and the over-grid logo | ✅ drawn |
| donut chart | ✅ drawn, with labels and percentages |
| checkboxes | ✅ drawn, ticked state visible |
| native Table (header colour, banded rows) | ✅ match |
| dropdown chips | ⚠️ the coloured pill is gone; the value prints as pale text in the chip's colour, nearly invisible on white |
| rating (stars) chip | ⚠️ live shows 3 of 5 filled; the PDF shows 5 stars in one colour |
| frozen rows and columns | ❌ not drawn (print has no frozen panes); `fzr` only repeats rows |
| hidden rows and columns | not checked; print omits hidden rows, so a render must say so |

So the render answers "what colour, what layout, is the chart there", and a
read of cell values answers "which option is chosen in this dropdown".

## Recommendation for the server

1. Sign in with `spreadsheets` only. Renders need nothing more, so the
   OAuth app keeps one sensitive scope and no restricted Drive scope.
2. `render_range(sheet, tab, range, width)` fetches the export link with
   `fitw=true`, `portrait` chosen from the range's aspect, small margins,
   `sheetnames=false`, `printtitle=false`, `pagenum=UNDEFINED`, then
   rasterises page 1 (or up to N pages) at a default of about 1024 px,
   returning native image content.
3. Cache by spreadsheet, gid, range, options and a revision marker; refuse
   to send a `gid` not just seen in a fresh `spreadsheets.get`, in case the
   link behaves like the Docs one (an unknown tab id silently returns the
   first tab there; not checked for Sheets).
4. Back off on 429; never fan renders out in parallel for one caller.
5. Say in the reply which features a render does not show (chips, frozen
   panes) so agents do not misread a pale value as empty.

## The Docs MCP render failure

Filed as https://github.com/luutuankiet/google-docs-mcp/issues/44. In
short: the Docs MCP signs in with `documents` only, and the Docs export
link treats a `documents` token as anonymous (401 on any private doc),
while it accepts a `spreadsheets` token for a Sheet and a `drive` token for
anything. The Sheets symmetry that its render ticket relied on holds for
Sheets but not for Docs. The Slides MCP is unaffected because the Slides
API has a documented `getThumbnail` method; its sign-in also carries
`drive.readonly`. The proposed fix is adding `drive.readonly` to the Docs
MCP sign-in, which shares a consent screen that already lists it.

## Sources

- Live probes above, 2026-10-15, and the render samples kept locally in
  `tmp/20261015/render/` (gitignored).
- Drive `files.export`, scopes and size limit:
  https://developers.google.com/drive/api/reference/rest/v3/files/export
- Sheets API v4 method list (no render method):
  https://developers.google.com/sheets/api/reference/rest
- Slides `getThumbnail` and its scopes:
  https://developers.google.com/slides/api/reference/rest/v1/presentations.pages/getThumbnail
- Down-scoping on refresh (`scope` parameter on a refresh grant):
  https://developers.google.com/identity/protocols/oauth2/web-server#offline
- The Docs MCP's own record: `docs/reference/pdf-export-scope.md` in
  `$DOCS_MCP_REPO`.
