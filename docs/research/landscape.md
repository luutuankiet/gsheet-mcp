# Landscape: existing Sheets MCP servers and DuckDB tooling

WIP draft. Sections marked TODO are not researched yet. Everything else was tested live, read only, against the reference sheet `$SAMPLE_SHEET_URL` on 2026-10-15.

## Summary table (partial)

| tool | read format | structured | messy sheets | token economy | steal / avoid |
| :--- | :--- | :--- | :--- | :--- | :--- |
| Google Sheets connector `get_values` | JSON `values` grid of formatted strings, trailing blanks trimmed | fine for small ranges; no types, no header knowledge | merges become empty strings, links, images, chips, colours, chart all invisible | "table for comms" about 1.5k tokens, "table for comms 2" about 7k, "structured table" (5,787 rows) about 540k | steal: formatted values (a star rating reads as stars). avoid: whole-tab reads with no size guard |
| Google Sheets connector `get_spreadsheet` | raw `spreadsheets.get` JSON, any field mask | exposes native Tables (`sheets.tables` with column names), banded ranges | exposes merges, frozen counts, hidden tabs, charts; but `includeGridData` is unusable (see sizes below) | metadata with a field mask about 12k tokens for 7 tabs, mostly merge rectangles | steal: field masks, `sheets.tables`. avoid: unmasked grid data |
| Google Drive connector `read_file_content` / `get_file_metadata` | per tab: `# <tab title>` then a fenced CSV block, hidden tabs included | compact | blank cells appear to be dropped from rows, so columns shift; merges, formatting lost | smallest per row of anything tested | steal: markdown heading per tab plus fenced block. avoid: dropping blanks |
| Raw Sheets API, `includeGridData=true` | full CellData JSON | yes | everything the API knows: rich text runs, links in runs, checkbox and dropdown validation, colours | "table for comms 2" is 57 MB (about 14M tokens) because 38,380 mostly empty formatted cells; "structured table" 305 MB | steal: field masks per feature. avoid: ever returning this to a model |
| Drive xlsx export | one xlsx with every tab | DuckDB `read_xlsx` reads it | only API path found that carries images: in-cell images and the logo exported as anchored PNGs, the chart as a PNG | 1.4 MB file, local only | steal: as the pull format for images and for DuckDB |
| DuckDB `read_xlsx` (excel extension, DuckDB 1.4.1) | SQL table | header and type sniffing work, but sniffing on early rows failed at row 4,664 on "structured table" (text in a column sniffed as DOUBLE) | without a range it stops at the first empty row and the first empty header cell; with `range` it reads any sub-rectangle; merged blocks give NULL except top-left; dates come as serial numbers, the star rating as `3.0` | local, zero model tokens | steal: `range` per detected table, `all_varchar` then cast. avoid: default sniffing on big tabs |
| DuckDB community `gsheets` extension | SQL table via `read_gsheet`, `COPY ... (FORMAT gsheet)` | header, `all_varchar`, `sheet`, `range`, gid and range from URL; write with overwrite or append | values only, same blind spots as `get_values` | local | steal: URL parsing (gid and range). avoid: auth model (see below) |
| Community MCP servers | TODO | TODO | TODO | TODO | TODO |
| Companion CLIs | TODO | TODO | TODO | TODO | TODO |

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

## b. Community MCP servers: TODO

Candidates by stars on 2026-10-15: `taylorwilsdon/google_workspace_mcp` (3,339), `aaronsb/google-workspace-mcp` (191), `mkummer225/google-sheets-mcp` (137), `atc07/sheets-mcp` (35), `xing5/mcp-google-sheets` (named in the ticket, not found by the search, check directly).

## c2. Table detection heuristics: TODO

## d. Companion CLIs: TODO

## Evidence

Scripts and raw outputs (local, gitignored): `tmp/20261015/landscape/` in the worktree that ran them: `fetch.sh`, `analyse.py`, `duck_xlsx.sql`, `duck_gsheets.sql`, `run_gsheets.sh`, `raw/`.
