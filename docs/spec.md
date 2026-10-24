# gsheet-mcp: build spec

gsheet-mcp is a remote MCP server for Google Sheets. With it, Claude Code can work in a spreadsheet from a pasted URL. A companion CLI pulls structured tables into a local DuckDB file for SQL.

Every decision here was settled in a closed ticket on the map "Wayfinder map: gsheet-mcp spec" (issue 1 of this repo). Items marked **(default, review)** were not grilled. They were filled in from the closed decisions and the two prior servers, so treat them as proposals to veto.

## 1. Goals

- **Two kinds of sheet, both first class.**
  - Clean structured tables: exports, native Tables, filters, sorted rows.
  - Messy communication sheets: merged bands, fills, rich text, links, floating images, checkboxes, dropdown chips, charts, frozen and hidden rows.
- **Reads show structure, content and formatting** as compact text. A page render gives the exact look when it is asked for.
- **Precise writes.** A value edit never disturbs styling. Every risky write runs through a dry-run plan, and stale data is caught before writing.
- **Local SQL on tables** through the CLI and DuckDB, including several tables on one tab and tabs with millions of rows.

Non-goals for the first build:
- inserting images
- syncing edited local rows back to the sheet
- Google's hosted Sheets MCP endpoint, or any Developer Preview feature

## 2. Stack

The stack is copied from the Docs MCP (`$DOCS_MCP_REPO`), file by file. It is not shared as a package. Line 1 of each copied file stamps its origin, and a provenance test lists every copied file.

| part | choice |
|---|---|
| server | Python with FastMCP 4, stateless Streamable HTTP only (no stdio, no `token.json`) |
| hosting | Cloud Run in `$GCP_PROJECT`, a service of its own |
| sign-in | per-user Google OAuth, with the grant kept on the server; its own OAuth client, which stays in testing |
| scopes | `spreadsheets`, `drive`, `openid`, `email`; scopes are never narrowed on refresh |
| auth store | Firestore, a named database of its own, collection prefix `gsheet_mcp__` |
| state store | a GCS bucket of its own with a one-day lifecycle; `signBlob` signed URLs |
| scripts | the Docs MCP's mini-racer V8 worker process, one per script |
| plans | dry run returns a `plan_id`, valid 1 hour, single use, for its caller only |
| CLI | Node, `@luutuankiet/gsheet`, no Google credential, talks over signed URLs only |
| DuckDB | in the CLI only; the server image carries no DuckDB |
| names | package `gsheet_mcp`, env prefix `GSHEET_MCP_`, local folder `.gsheet/` |

Rules carried over from the prior servers:
- Every tool declares its MCP annotations (`readOnlyHint`, `destructiveHint`, `idempotentHint`).
- Tool descriptions stay short. How-to material lives in the skill.
- Text replies never set `structuredContent`, because Claude Code drops the text when they do.
- Images come back as native image content, never as base64 text.
- No `/healthz` route on Cloud Run.

## 3. Facts the design rests on

From the research and from building the reference sheets:

- **One `spreadsheets.get` covers almost every feature per cell and per tab**, if it carries a field mask and ranges. Without a lean mask, full grid data is far too big: 57 MB for 363 valued cells. The mask cuts it 7x to 146x.
- **Plain REST never returns floating images.** The editor's xlsx export carries each image and its anchor, but not its alt text. A cell under a floating image reads empty.
- **Native Table `columnType`** comes back only when a column is typed. A missing type means untyped.
- **Filter views never flag the rows they hide.** Only the basic filter and rows a user hid show as hidden.
- **Charts come back as a spec only.** Dropdown chip colours come back nowhere. A rating chip reads as a number, with stars in `formattedValue`.
- **A Drive comment anchor is an opaque id** (`{"type":"workbook-range","uid":0,"range":"<id>"}`), but the same xlsx export carries each threaded comment with its cell (`xl/threadedComments/*.xml`, `ref="F4"`). Comments are placed on cells by matching the export's thread text to the Drive thread.
- **Plain REST has no compare-and-set.** `requiredRevisionId` is accepted but ignored.
- **The editor export link** returns a range as PDF in about 2 s, with `spreadsheets` scope only. Repeated exports hit 429.
- **The per-tab CSV export** of 200k rows by 20 columns is 32 MB and takes 14 s. DuckDB `read_csv` loads it in 0.5 s.
- **`copyTo` fails with a 500 on a 4.4M-cell tab.**
- **A new spreadsheet takes the account's locale**, so anything parsed as user input depends on `properties.locale`.
- **Sheets refuses a merge across a frozen column.** A named range in a single cell gives `#VALUE!`.
- **`values.get` trims trailing blanks.** Pad rows to the tab width.

## 4. Addressing

- **The canonical handle is a tab plus an A1 range**: `'table for comms'!B2:F8`. A tab name with spaces or punctuation is quoted.
- **A scan alias** such as `clean-wide-50k#2` is valid only while the scan cache lives.
- **Every tool accepts** a spreadsheet URL or id, and either a handle or an alias.
- A whole tab is `'<tab>'`. A missing range means the whole used area.

## 5. Landscape scan: `scan_sheet`

One call covers the whole workbook, in about 2k tokens for a typical one.

**For each tab:**
- size, plus frozen and hidden rows and columns
- native Tables
- charts (type, title, anchor)
- the basic filter and filter views
- the count of floating images
- its regions

**Spreadsheet level:**
- named ranges
- the count of comment threads, open and resolved, per tab where the export places them, otherwise as `unplaced`

**How regions are found:**
- A flood fill runs over filled cells.
- Two blocks join when no more than 3 blank rows separate them and their column spans match within one column. The scan reports the blank rows inside a joined block.
- Native Tables and named ranges are exact, and they override the guess: a guessed block half or more inside one is dropped (default, review).

**Big tabs:**
- Up to 500k cells, the scan reads every value.
- Above that, it reads the first 2,000 and the last 500 rows, and marks the middle `extent estimated`.

**What a region reports:**
- *Table region:* its range, header rows (several are allowed; a two-row header joins as `Order / Order ID`), column names, per-column types from the rows read (with a `mixed` flag and shares), suspected total and subtotal rows (first text cell matches `total|subtotal|grand total`), and footnotes.
- *Communication block:* one line holding the range, the first 60 characters of text, and counts of merges, images and checkboxes.

**Limits and hidden tabs:**
- At most 20 regions per tab, then `+N more`.
- `scan_sheet(tab=...)` lists one tab in full.
- Hidden tabs are listed with their size and marked hidden. Their regions are scanned only with `include_hidden=true`.

**Images and cache:**
- The scan fetches the xlsx export once to build the floating image list.
- A workbook too large to export in time says `images: not checked (workbook too large)`.
- The result, with the image list, is cached for 10 minutes in the state store. Reads and queries reuse it. Any write through our tools clears it. Drive's `modifiedTime` does not move on Sheets writes, so a time limit is the only safe expiry.

## 6. Reads

### 6.1 `read_range`

The text shows structure, content and formatting, one region at a time, in the shape the scan gave it.

- **Communication regions** are cell-addressed lines grouped by `── row N`. Only non-empty cells appear, and a merge shows as its range on the top-left cell: `B1:C1 {s1}: Objective`.
- **Table regions** are a Markdown table with real row numbers.
- **Styles.** By default each cell shows only its effective fill, text colour, bold and centring, deduplicated into a `styles:` legend that cells reference as `{s1}`. `detail="formats"` adds borders, number formats, column widths, row heights and conditional format rules. A cell that carries only a fill is not listed.
- **Rich text** is inline Markdown: `**bold**`, `*italic*`, `[text](url)` and `~~strike~~`. A link whose text equals its URL is shown as `<url>`. Colour and underline on runs are dropped.
- **Notation:**

  | thing | shown as |
  |---|---|
  | checkbox | `[x]` or `[ ]` |
  | dropdown | `YES ▾v1`; the legend lists `v1 dropdown: YES \| NO` once per rule; a value outside the list gets `!` |
  | smart chip | `@[name](url)` |
  | rating | `★★★☆☆ (3)` |
  | `IMAGE()` | `=IMAGE(url)` |
  | floating image | `G4 [image 96x40 over G4:H4, floating]` |

- **Formulas** come inline after the value: `G12: 42 ← =SUM(G2:G11)`. In a table region they show only with `detail="formulas"`.
- **Notes** are shown by default, as an indented `note: <text>` line under their cell.
- **Comments** are opt-in with `comments=true`, shown as `F4 comment (open, 2 replies) @author: "text…"` under their cell. A thread the export cannot place is listed at the top of the read without a cell.
- **Hidden rows and columns** are always announced, never silently skipped, e.g. `rows 12-40 hidden by filter (29 rows)`. Their content is skipped unless `include_hidden=true`.
- **Size guards:**
  - Each cell is capped at 500 characters, ending `…[+N chars]`; `max_chars=0` lifts the cap.
  - Each read has a budget of about 8k tokens with a continue cursor. The header reads `cells 1-400 of 1,212 (~9k tokens) · next: B240`. The header is always there; `· next:` appears only when more remains (default, review).
- **Regions in a read.** Each region opens with `region <A1> · table` or `· communication`. Cells outside every region come last, under `loose cells` (default, review).
- **Comment text** in a read is clipped at 100 characters (default, review).
- **Render pointer.** The read ends with a line saying why a render is worth it, e.g. `this range has 1 chart, 3 floating images, 8 fill styles · render_range("…")`. A range with no charts, floating images or fill styles gets no pointer line (default, review).

### 6.2 `grep_sheet`

- **Searches:** formatted values, link URLs, notes and formulas. Comment text is searched with `comments=true`.
- **One line per hit:** `'table for comms'!D4:E4  …snippet…`, about 80 characters around the match. A hit inside a merge reports the merge range.
- **Tags:** each hit names the field it matched (`value`, `link`, `note`, `formula`, `comment`), and hits in hidden rows are tagged.
- **Cap:** 50 hits, then `+N more in <tab>`.
- **Line shape (default, review):** `<handle>  [<field> · <hidden tags>]  <snippet>`, one line per cell per field however many matches the field holds. Hidden tags are `hidden row`, `hidden column` and `hidden tab`.
- **No range (default, review):** every tab is searched, hidden tabs included, and their hits are tagged `hidden tab`. Comments the export cannot place on a cell come back as `(unplaced)` hits, only when no range is given.
- **Big tabs (default, review):** every row is searched; the scan's head-and-tail sampling does not apply.

### 6.3 `query_range` (default, review)

Live, non-SQL questions on a region:
- filter rows by column conditions
- pick columns
- sort
- count

The answer comes back as a Markdown table under the read budget. Its job is to answer small questions without a pull. Anything heavier goes to the CLI.

The details, all (default, review):
- **Conditions:** `where` is a list of `<column> <op> <value>`, with ops `=`, `!=`, `>`, `>=`, `<`, `<=`, `contains` and `~` (regex), plus `<column> is empty` and `<column> is not empty`. Numbers and ISO dates compare by value; text compares ignoring case.
- **Sort:** a comma-separated list; `-col` or `col desc` sorts descending, and empty cells sort last.
- **Count:** `count=true` returns only the header line with the match count.
- **Rows left out:** hidden rows unless `include_hidden=true`, as in `read_range`, and suspected total and subtotal rows. The reply says how many of each were left out.
- **One region:** a range holding no table region, or more than one, is refused with the candidates listed. An unknown column is refused with the column names.
- **Row numbers:** each answer row carries its real sheet row number.

### 6.4 `render_range`

- **Opt-in only.** It exports the range from the editor link as PDF, then rasterises it with pypdfium2. One page costs about 1,080 image tokens.
- **Keeps:** colours, merges, links, images, checkboxes and charts.
- **Loses:** dropdown pill colours, partial star ratings and frozen panes.
- **Rate limits:** renders are cached in the state store for 10 minutes and serialised per spreadsheet. Any write through our tools clears that spreadsheet's cached renders, as it does the scan cache. A 429 gets one retry with a backoff, then a visible refusal.

There is no tool that returns one floating image's pixels in the first build. An agent sees them by rendering the range.

## 7. Writes

### 7.1 Layers

| tool | does |
|---|---|
| `write_cells` | values, formulas, Markdown rich text, an optional format patch, over one or more ranges |
| `edit_sheet` | typed structural operations in one atomic `batchUpdate` (list below) |
| `run_sheet_script` | a V8 script for loops and logic, using a small `sheet.*` API that calls the same operations |
| `batch_update` | raw `batchUpdate` requests; the escape hatch |

**`edit_sheet` operations:**
- freeze rows and columns
- hide and unhide rows, columns and tabs
- merge and unmerge
- insert, delete and move rows and columns
- add, rename, duplicate and delete tabs
- sort a range
- set and clear the basic filter
- add, update and delete filter views
- set data validation: checkbox, or dropdown from a list
- append rows to a native Table
- set and clear notes
- add, reply to and resolve comments

The tab, filter and validation operations are **(default, review)**.

**`edit_sheet` details (default, review):**
- A call with `operations` always returns a plan and never applies directly; there is no `dry_run` flag. The dry run lists the raw requests as JSON.
- Rows are given as `'tab'!5:6` and columns as `'tab'!B:C`. Insert puts blank rows or columns at exactly those positions and inherits formatting from the one before, except at index 0. `move` takes `to` as a 1-based row number or a column letter.
- Merges are refused across a frozen row edge as well as a frozen column edge. The default merge type is `MERGE_ALL`.
- Dropdowns are strict by default, with the dropdown arrow shown. `clear_validation` removes validation.
- One note applies to every cell in its range; `clear_note` writes an empty note through the same note-only mask.
- Table rows use the `write_cells` type rules, except that ISO dates are not converted.
- `add_tab` without a size lets Sheets choose. A duplicated tab goes right after its source.
- Drive comment calls run after the batch; a failed comment does not roll the batch back.
- The structural guard reads values only for ranges: the touched cells, plus whole rows for a sort. Tab, freeze, hide, filter and filter-view operations are guarded by row and column counts only. Row and column operations are guarded on the rows given, not on the rows that shift. It fingerprints at most 5,000 cells.

**`run_sheet_script` and `batch_update` details (default, review):**
- Both default to `dry_run=true`. `dry_run=false` without a `plan_id` is refused. A plan is tied to the tool that made it, and any other tool refuses it.
- The sandbox cannot call out, so `sheet.read` ranges are read before the script runs: string-literal `sheet.read("...")` calls, plus a `reads=[...]` argument for computed ranges, at most 20 ranges. `sheet.read` returns unformatted values.
- Script limits: 30 s, 5,000 operations, return value cut at 8 KB. A script that queues nothing makes no plan and replies "no change".
- `batch_update` accepts one request object or a list.
- The raw-request guard finds affected ranges by walking each request for objects with a `sheetId`: a grid range, a single cell, or a whole tab. It fingerprints the non-empty cells there plus each affected tab's row and column counts.

### 7.2 Rules

- **Styling is kept by construction.** Every value write goes through `updateCells` with the field mask `userEnteredValue` only, never through `values.*`.
- **Types come from JSON:**
  - a string is text
  - a number is a number
  - a boolean is a checkbox state
  - a string starting with `=` is a formula
  - an ISO date string, in a cell already formatted as a date, becomes a date serial

  There is no "parse like typing" mode. The one place input is parsed is dates, and only ISO dates are taken, so the spreadsheet's `locale` is not needed (default, review).
  - `null` leaves a cell unchanged; `""` clears its value (default, review).
- **Rich text.** Writes take the same inline Markdown that reads show. When a write would overwrite a cell's text runs with plain text, both the dry run and the receipt warn: `B4: drops 2 rich text runs (bold, link)`. The details, all (default, review):
  - For those cells only, the mask also carries an empty `textFormatRuns`, so the runs really are dropped as the warning says.
  - A link's own underline and colour count as `link`, not as separate styles.
  - Markdown with an unclosed marker is written as literal text.
  - Run start indexes count Python characters, which may differ from Google's count for emoji and other characters outside the BMP.
- **Format patches** name only their own fields, e.g. `userEnteredFormat.textFormat.bold`. Clearing is explicit, with `null`. Only fields inside `textFormat` and `borders` are split into separate mask paths; any other key, such as a colour or `numberFormat`, is replaced whole (default, review).
- **Cells with structure:**
  - A write to a covered cell inside a merge is refused, with a pointer to the top-left cell.
  - A checkbox cell takes only booleans.
  - A strict dropdown refuses values not in its list; a non-strict one warns. Dropdowns whose list comes from a range are not checked (default, review).
  - Overwriting a formula with a plain value warns.
  - New rows in a native Table go through `appendCells` with its `tableId`.
- **Notes** are written through a note-only field mask.
- **Comments** are added unanchored through Drive, because no public API anchors a new comment to a cell. Replies and resolve go to an existing thread.
- **Images:** no inserts. `=IMAGE(url)` is allowed as a formula, with a warning that a person must allow external data once per file.

### 7.3 Plans and the stale-write guard

- **A plan is required for:**
  - any structural operation
  - any write over 200 cells
  - every `batch_update` and `run_sheet_script`
  - every write that lands a CLI query result

  Small value and format writes may skip the plan.
- **The guard reads, compares, then writes, on the cells a write touches:**
  - *Cell writes:* values, formulas, `userEnteredFormat` and notes.
  - *Structural writes:* also the tab's row and column counts and the values in the affected rows.
- **The dry run** stores the fingerprint with its `plan_id` and returns a diff. Applying the plan re-reads and compares. A write without a plan re-reads just before writing; with no earlier fingerprint to compare, that read only feeds the refusals and warnings (default, review).
- **A plan is used once (default, review).** Applying it uses it up, even when the guard refuses the apply. Landing a CLI upload fingerprints the target's formats as well as its values.
- **On a change**, the whole batch is refused and nothing is applied. The reply lists up to 20 changed cells as `B4: "old" → "new"` and asks for a new plan.
- **Tool descriptions and the skill** say the guard narrows the race to about one round trip. It is not a lock.

### 7.4 Receipts

- **The reply re-reads the touched cells** and shows them in the read notation, with the style legend.
- **It is capped at 50 cells**, plus counts and warnings.
- **Structural and raw receipts (default, review).** `edit_sheet` does not re-read cells, because its operations move or delete them. It reports size changes as `'tab': R rows x C columns → R' rows x C' columns` and shows Google's non-empty replies. `batch_update` and `run_sheet_script` give the `write_cells` receipt for their cell writes, plus "applied N raw requests; affected: …" with each raw reply cut at 300 characters.
- **Any write clears the scan cache and the render cache.** Each spreadsheet has a generation token in the state store that every write replaces, and both cache keys include it, so one write hides every caller's cached scan and renders for that spreadsheet (default, review).

## 8. Tables to DuckDB

### 8.1 Server side

- **`pull_tables`**
  - Streams each tab's CSV export into the state bucket and returns signed URLs with the scan's regions.
  - The server never parses the rows.
  - By default it pulls table regions and native Tables. A communication block is pulled only when it is named.
  - A region is named with `range=`, as an alias or A1. A range that matches no scanned region is pulled as given (default, review).
  - Each tab comes back with its fingerprint, for a later `table_status` call.
- **`table_status`** returns a head-and-tail fingerprint: the tab size plus a hash of the first and last 500 rows.
  - The rows are grid rows, not used rows, so data typed into blank grid rows in the middle goes unseen until the grid grows (default, review).
  - The server keeps no fingerprints. Given an earlier one in `since`, the reply says what changed, or that head and tail are unchanged and the middle was not compared (default, review).
- **`upload_slot`** returns a signed upload URL for a CLI query result. The result lands through `write_cells` into a new tab or an empty range, behind a required plan.
  - CSV carries no types: a plain decimal number lands as a number, and everything else lands as text, including `true`, `false` and a leading `=`, so no formula gets in (default, review).

### 8.2 CLI: `gsheet`

| command | does |
|---|---|
| `gsheet pull <url> [--tab] [--range] [--as <name>]` | downloads to `.gsheet/<spreadsheet id>/<gid>.csv` and loads it |
| `gsheet status` | `changed`, or `unchanged at head and tail`; it never pulls again on its own |
| `gsheet query "<sql>"` | runs SQL on the workspace database |
| `gsheet push <sql or file> --to <tab or range>` | uploads a result and prints the upload handle and the exact `write_cells(..., upload=<handle>)` call; that call returns the plan id, because the CLI holds no credential (default, review) |

- **Pull manifest (default, review):**
  - `pull_tables` stores a JSON manifest in the `gsheet-pull/1` format and prints `manifest <url>` as its second line. The manifest carries the CSV links, fingerprints and regions, and its link lasts the same 15 minutes as the CSV links.
  - No manifest is written when nothing is pulled.
  - `gsheet pull` also takes a local path or a `file:` URL for the manifest and the CSVs, so a pull can be reloaded offline.
- **CLI behaviour (default, review):**
  - A `--range` that matches no region in the manifest is cut as given, with the header in its first row, into `<tab>_<range>`.
  - `--as` works only when exactly one region loads.
  - `status` with no arguments lists the pulled tabs and the `table_status` call for each. With a fingerprint it compares against the stored one, and needs `--tab` when several tabs are pulled.
  - `query` prints at most 50 rows, cuts columns at 40 characters and adds a row count.
  - `push` writes the CSV header row first and NULL as an empty cell, and passes `--to` unchanged to `write_cells`. The spreadsheet comes from `_sources` when exactly one spreadsheet is pulled, otherwise `--spreadsheet` is required. `push` with no workspace exits 1, and without `--upload` it exits 2.

- **Database:** `.gsheet/workspace.duckdb`, with one schema per spreadsheet (a slug of its title) and a `_sources` table (spreadsheet id, tab, gid, range, pull time).
- **Each region becomes two objects:**
  - *A raw text table:* every column is text, rows exactly as in the sheet.
  - *A clean view:* header rows joined into names, duplicate names get `_2`, blank headers become `col_<letter>`. Spacer rows, suspected totals and footnotes are removed into `<name>_dropped`.
- **Casting:**
  - First, strip `$`, thousands separators, `%` and padding.
  - Then detect dates (ISO, day-first or month-first; any first part over 12 means day-first) and booleans.
  - A column is typed when at least 98% of its values cast. Failures become NULL and are listed in `_cast_failures` (table, column, row, raw value).
- **Table names:**
  - A native Table keeps its name, case included, when it is already a plain SQL name; otherwise it is turned into one (default, review).
  - A tab holding one region takes the tab slug.
  - A tab holding several regions takes `<tab slug>_<n>`, numbered in reading order (top row, then left column), counting every region of the tab in the manifest (default, review).
- **Failure:** no size cap. A 429 gets one retry with a backoff, then a visible refusal.
  - The backoff is `Retry-After`, capped at 60 s, or 5 s without it. A second 429 exits 1 with a message naming the wait used (default, review).
- **Acceptance gate:**
  - Pulling the bulk sheet's `messy-sales-200k-raw` and summing revenue in the clean view gives `66304772.44`. `198914317.32` means the subtotals were counted.
  - The totals by region and by status match `fixtures/bulk/golden-data-ground-truth.json`.
  - The 2,012 blank order dates match.

## 9. Other tools

- **`auth_status`:** who is signed in, and with which scopes.
- **`install_skill` (default, review):** returns the skill bundle, as in both prior servers. The cookbook teaches:
  - scan first, then read
  - the plan path for writes
  - when to render
  - when to pull to DuckDB instead of reading
  - the stale-write guard's limits
  - the notation table

## 10. Charts, pivots, conditional formats, validation (default, review)

- **Read:**
  - Charts are one scan line each (type, title, source ranges, anchor), plus a render pointer.
  - Pivot tables are one scan line each (source range, anchor).
  - Conditional formats show under `detail="formats"` as one line per rule.
  - Data validation shows through the dropdown and checkbox notation.
- **Write:** only checkbox and dropdown validation, through `edit_sheet`. Charts, pivots and conditional formats are written only through `batch_update` in the first build.

## 11. Testing (default, review)

- **Fake Sheets API.** The fake serves `tests/fixtures/canary.json` and `tests/fixtures/canary.xlsx`, and records every `batchUpdate`. Tests run through the tool layer, as in the Docs MCP.
- **Reference sheets** (ids in `.env`):
  - *Canary* (`$CANARY_SHEET_ID`): every feature on 7 tabs, listed in `fixtures/canary/manifest.json`.
  - *Bulk* (`$BULK_SHEET_ID`): the golden tables at full size.
  - Tests compare against the captured files, never against the live canary.
- **Live integration tests** are opt-in through an env flag. They copy the canary through Drive, run there and trash the copy.
- **Each build issue names its acceptance tests.** An issue is done when they pass in CI and the ruff check is clean.

## 12. Deployment (default, review)

- **Cloud Run:** 1 vCPU, 1 GiB, concurrency 20, request timeout 300 s, `max-instances=1`. The per-user and per-spreadsheet locks live in one process, so one instance is what makes them hold. The server holds no DuckDB and streams exports, so it needs little memory.
- **Quotas:**
  - Sheets allows 300 read requests and 300 write requests per minute per project, and 60 per minute per user.
  - The server serialises per user, retries once on 429, then refuses visibly.
- **Bucket lifecycle:** one day, for scans, renders, pulls, plans and upload slots.
- **A release gate**, copied from the Docs MCP, runs before deploy.
