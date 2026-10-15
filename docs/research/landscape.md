# Landscape: existing Sheets MCP servers and DuckDB tooling

Sections a and c were tested live, read only, against the reference sheet `$SAMPLE_SHEET_URL` on 2026-10-15. Sections b, c2 and d come from reading source code and papers at the commits named below.

## Summary table

| tool | read format | structured | messy sheets | token economy | steal / avoid |
| :--- | :--- | :--- | :--- | :--- | :--- |
| Google Sheets connector `get_values` | JSON `values` grid of formatted strings, trailing blanks trimmed | fine for small ranges; no types, no header knowledge | merges become empty strings, links, images, chips, colours, chart all invisible | "table for comms" about 1.5k tokens, "table for comms 2" about 7k, "structured table" (5,787 rows) about 540k | steal: formatted values (a star rating reads as stars). avoid: whole-tab reads with no size guard |
| Google Sheets connector `get_spreadsheet` | raw `spreadsheets.get` JSON, any field mask | exposes native Tables (`sheets.tables` with column names), banded ranges | exposes merges, frozen counts, hidden tabs, charts; but `includeGridData` is unusable (see sizes below) | metadata with a field mask about 12k tokens for 7 tabs, mostly merge rectangles | steal: field masks, `sheets.tables`. avoid: unmasked grid data |
| Google Drive connector `read_file_content` / `get_file_metadata` | per tab: `# <tab title>` then a fenced CSV block, hidden tabs included | compact | Drive's own CSV export keeps blank cells in place (checked, see below); the connector's rendering of it was not re-checked in full; merges, formatting lost | smallest per row of anything tested | steal: markdown heading per tab plus fenced block |
| Raw Sheets API, `includeGridData=true` | full CellData JSON | yes | everything the API knows: rich text runs, links in runs, checkbox and dropdown validation, colours | "table for comms 2" is 57 MB (about 14M tokens) because 38,380 mostly empty formatted cells; "structured table" 305 MB | steal: field masks per feature. avoid: ever returning this to a model |
| Drive xlsx export | one xlsx with every tab | DuckDB `read_xlsx` reads it | only API path found that carries images: in-cell images and the logo exported as anchored PNGs, the chart as a PNG | 1.4 MB file, local only | steal: as the pull format for images and for DuckDB |
| DuckDB `read_xlsx` (excel extension, DuckDB 1.4.1) | SQL table | header and type sniffing work, but sniffing on early rows failed at row 4,664 on "structured table" (text in a column sniffed as DOUBLE) | without a range it stops at the first empty row and the first empty header cell; with `range` it reads any sub-rectangle; merged blocks give NULL except top-left; dates come as serial numbers, the star rating as `3.0` | local, zero model tokens | steal: `range` per detected table, `all_varchar` then cast. avoid: default sniffing on big tabs |
| DuckDB community `gsheets` extension | SQL table via `read_gsheet`, `COPY ... (FORMAT gsheet)` | header, `all_varchar`, `sheet`, `range`, gid and range from URL; write with overwrite or append | values only, same blind spots as `get_values` | local | steal: URL parsing (gid and range). avoid: auth model (see below) |
| `xing5/mcp-google-sheets` (1,012 stars) | raw API JSON (`valueRanges[].values`), or full grid JSON with `include_grid_data` | `get_multiple_spreadsheet_summary`: headers plus first N rows per tab | values only; grid mode returns everything the API has | no row cap: a tab with no range reads the whole tab | steal: summary of headers plus first rows. avoid: uncapped reads, raw grid passthrough |
| `taylorwilsdon/google_workspace_mcp` (3,339 stars) | text, `Row  N: [..]` per row, rows padded to header width | `list_sheet_tables` reads native Tables with column names | opt-in add-ons: hyperlinks, notes, formulas, smart chips, detailed `#REF!`-style errors; grid metadata refused over 5,000 cells | reads clamped to 1,000 rows | steal: opt-in enrichment flags, cell cap on grid metadata, error explanations. avoid: hidden tabs not flagged |
| `atc07/sheets-mcp` (35 stars) | JSON `values`, modes values / formulas / raw / formats | `get_spreadsheet_info`: tabs, sizes, frozen panes, `hidden`, charts with source ranges, named ranges, first 3 rows | `formats` mode: one style table plus a per-row map of style ids, with merges, banding, conditional formats, widths | `max_cells` (default 2,000) with a `next_range` cursor to continue | steal: cell budget plus continuation cursor, deduplicated style table, undo stack (20 deep) for writes |
| `aaronsb/google-workspace-mcp` (191 stars) | rendered text, each row prefixed with its sheet row number (`R3: ...`) | `get` returns metadata and tab names | values only | none seen | steal: real row numbers so blank rows stay visible; a scratchpad that imports a sheet as CSV, lets the model edit, then writes it back |
| `mkummer225/google-sheets-mcp` (137 stars) | pretty-printed JSON of the values grid | `read_headings`, `read_rows`, `read_columns` | values only | none; `read_all_from_sheet` reads everything | avoid: pretty-printed JSON (indentation tokens) |
| `googleworkspace/cli` (`gws`, 31,505 stars) | any API method as JSON; output as json, table, csv | generic, generated from Google's discovery documents | whatever the API returns | not tuned for models | steal: one binary for API escape hatches. avoid: generic dumps as the main read path |
| `ontodev/cogs` (16 stars, unmaintained) | one TSV per tab plus `format.tsv` (each distinct format gets an id) and `note.tsv` | git-like `fetch`, `status`, `diff`, `push` over local TSVs | formats and notes kept beside the data | local files | steal: the working-copy model and the format-id side table. avoid: tracking by tab title (renames break the link) |

## a. Google's own connectors (tested)

- The reference sheet has **7 tabs, not 3**: four are hidden (`[Draft] Data Engineering Track`, `[template] Data Engineering Track`, `Sheet5`, `Data Consulting Track`). `get_values` never mentions them; `get_spreadsheet` and the Drive reader do. Our outline tool must announce hidden tabs.
- `get_spreadsheet` with a field mask returned the native Table on "structured table": name `Table1`, range A1:N5787, 14 named columns. Column types were not in the masked reply.
- `get_values` returns checkboxes as `TRUE`/`FALSE`, dropdown chips as their text, and long multi-line cells verbatim. Merged cells: the value sits in the top-left cell, other cells are empty strings, so a model cannot tell a merge from a blank.
- No connector exposes in-cell images or over-grid images. Grid data on "table for comms" has no key at all for the image cells.
- Drive `get_file_metadata` snippet shows the Drive text form: a markdown heading per tab and a fenced CSV block, starting with a hidden tab.
- Drive's plain `export?mimeType=text/csv` returns only the first visible tab ("table for comms"), 5.3 KB.

## c. DuckDB paths (tested)

- `gsheets` extension, installed build `f44cfdd`, source `evidence-dev/duckdb_gsheets` at `a209d8a`. Auth: `CREATE SECRET (TYPE gsheet, PROVIDER access_token | key_file | oauth)`; key_file signs a service account JWT itself (`src/sheets/auth/service_account_auth.cpp`); oauth opens a browser with scope `spreadsheets` (`src/gsheets_auth.cpp:150`). With a user ADC token every read failed with `Sheet with name ... not found`: the raw API answers 403 `SERVICE_DISABLED` for user ADC tokens unless an `x-goog-user-project` header names a quota project, and the extension sends none, so the real cause is hidden behind "not found". Lesson for our CLI: send the quota project header and surface the real API error.
- `read_xlsx` on the Drive xlsx export: see table. `range` makes it the natural loader for several tables on one tab once something else has found their rectangles.

## a2. Does the Drive text form drop blank cells? (tested)

`tmp/20261015/landscape/blank_check.py` compares Drive's CSV export of the first visible tab with `values.get` on the same tab, comparing positions only. All 7 rows match once `values.get` rows are padded: Drive keeps every blank as an empty field and pads every row to 14 columns, while `values.get` trims trailing blanks (row lengths 6 to 14). So Drive's export does not shift columns. The earlier impression that the Drive connector drops blanks came from a 5k character snippet and was not reproduced; treat it as unconfirmed. Either way, `values.get` rows need padding to the tab width before a model sees them, which `taylorwilsdon` already does.

## b. Community MCP servers (read from source)

Commits read: `xing5/mcp-google-sheets` `b2f9824`, `taylorwilsdon/google_workspace_mcp` `7897304`, `atc07/sheets-mcp` `27b33f6`, `aaronsb/google-workspace-mcp` `2e1e2d1`, `mkummer225/google-sheets-mcp` `1d52b9d`.

- **Nobody detects several tables on one tab.** Every server reads a rectangle the model names. The closest thing to structure is native Tables (`taylorwilsdon` `list_sheet_tables`, `gsheets/sheets_tools.py:1578`) and header plus first rows previews (`xing5` `server.py:761`, `atc07` `get_spreadsheet_info`).
- **Token guards differ by an order of magnitude.** `xing5` and `mkummer225` have none: a tab without a range returns in full (`xing5` `server.py:227`). `taylorwilsdon` clamps to 1,000 rows (`gsheets/sheets_helpers.py:23`) and refuses grid metadata above 5,000 cells (`sheets_helpers.py:18`). `atc07` uses a cell budget (`max_cells`, default 2,000, max 20,000) and returns `next_range` to continue (`src/server.ts:1058`); `read_ranges` splits one budget across ranges.
- **Formatting is the gap.** Only `atc07` reads it in a model-friendly shape: `mode: "formats"` returns a style table plus a per-row map of style ids, with merges, banding, conditional formats, column widths and frozen panes (`src/server.ts:1062`). `taylorwilsdon` adds hyperlinks, notes, formulas and smart chips as opt-in flags, each a separate `includeGridData` call scoped to the values' real extent (`gsheets/sheets_tools.py:226`).
- **Hidden tabs.** `atc07` marks `hidden: true` in its outline; `taylorwilsdon` `get_spreadsheet_info` asks only for title, id, size and conditional formats, so hidden tabs look like any other.
- **Write safety.** `atc07` keeps an in-memory undo stack of 20 snapshots of values and formulas (`src/server.ts:400`), refuses writes over 10,000 cells without `allow_large`, and reports formula errors after writing.
- **Images, charts as pictures, and drawings:** none of them read these.

## c2. Detecting several tables on one tab

- **Flood fill over non-empty cells.** Excel's `Range.CurrentRegion` returns the block around a cell bounded by any mix of blank rows and blank columns (Microsoft VBA reference, `Range.CurrentRegion`). Connected components over non-empty cells is the same idea and the usual baseline. It merges tables that touch and splits tables with a blank row inside, so it needs extra signals.
- **Extra signals that are cheap from the Sheets API:** merges (a title merged across a block's width), header rows that are bold or filled differently, borders, banded ranges, native Tables and named ranges (exact rectangles, no guessing), frozen rows (header depth).
- **TableSense** (Dong et al., "TableSense: Spreadsheet Table Detection with Convolutional Neural Networks", AAAI 2019; code at `microsoft/TableSense`, 50 stars, no public model): a CNN over per-cell features (value, type, format) predicts table boxes. Proves format features matter; too heavy for us.
- **SpreadsheetLLM** (Tian et al., arXiv 2407.09025, 2024): SheetCompressor keeps "structural anchor" rows and columns (where content or format changes, likely table edges) and drops repetitive rows between them, then uses an inverted index (value to list of addresses) and groups cells by number format. The abstract reports better spreadsheet table detection; summaries cite about 25 times compression. This is the closest published match to a "landscape scan": anchors and boundaries first, cells later.
- **Plan implied:** flood fill on the values grid, split and join using merges, header formatting and blank-row runs, prefer native Tables and named ranges when present, then hand each rectangle to DuckDB `read_xlsx` with `range` (section c).

## d. Companion CLIs

- **`googleworkspace/cli` (`gws`)**, commit `a3768d0`: a generic CLI generated from Google's discovery documents, output formats include json, table and csv (`crates/google-workspace-cli/src/formatter.rs:24`), with helper commands such as `gws sheets +read`. Good escape hatch, not a working copy.
- **`ontodev/cogs`**, commit `6777709`, README says it is not actively maintained and points to rclone: the only pull and push working copy found. `fetch` downloads every tab as `{title}.tsv` into `.cogs/tracked/`, plus `format.tsv` (each distinct CellFormat gets a numeric id) and `note.tsv`; `diff`, `status`, `push` compare local and remote. Tracking is by tab title, so a remote rename orphans local edits.
- **`aaronsb` scratchpad** (MCP, not a CLI): `import-sheet` turns a range into CSV lines, the model edits, `send-sheet` writes back with `USER_ENTERED` (`src/server/scratchpad/adapters/`). No diff, no conflict check.
- Others seen in search, not read: `clss` (Python dump and upload), `csv2gsheets` (one-way upload), `gspread` (7,512 stars, the Python library most of these build on), rclone (Drive files, exports Sheets as a whole file).

## What to steal, what to avoid

Steal:
1. A cell budget with a continuation cursor (`atc07`), not a row cap.
2. Formatting as a deduplicated style table plus ids per cell (`atc07` formats mode, `cogs` `format.tsv`): two tools reached the same design independently.
3. Real sheet row numbers on every rendered row (`aaronsb`) and rows padded to the tab width (`taylorwilsdon`), so blanks and merges stay visible.
4. An outline call first: tabs with `hidden`, sizes, frozen panes, native Tables, named ranges, charts with source ranges, first few rows.
5. Opt-in enrichment (links, notes, formulas, chips, error explanations) scoped to the values' real extent.
6. The `cogs` working copy: one file per tab plus side tables, `status` and `diff` before `push`, but keyed by sheet id, not title.
7. Undo snapshots and a large-write confirmation flag.

Avoid:
1. Uncapped whole-tab reads and raw grid JSON passthrough.
2. Pretty-printed JSON of values.
3. Swallowing API errors (DuckDB `gsheets` turns a 403 into "not found").
4. Silently skipping hidden tabs, images and charts: say they exist even when we cannot read them.

## Evidence

Scripts and raw outputs (local, gitignored): `tmp/20261015/landscape/` in the worktree that ran them: `fetch.sh`, `analyse.py`, `duck_xlsx.sql`, `duck_gsheets.sql`, `run_gsheets.sh`, `blank_check.py`, `raw/`.
