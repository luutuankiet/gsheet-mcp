# gsheet-mcp

A Google Sheets MCP server and companion CLI so a coding agent can read, search, query and write a Google Sheet from its URL. Planning lives in the wayfinder map issue (label `wayfinder:map`).

Local identifiers (repo, GCP project, sample sheet) live in `.env`; see `.env.example` for the variable names used across docs and issues.

## Tools

Every tool takes a spreadsheet URL or id. Call `install_skill` for the agent skill: which tool fits a job, the read notation, the plan path, when to render and when to pull.

| tool | does |
|---|---|
| `scan_sheet` | maps every tab: size, frozen and hidden rows, native Tables, charts, filters, floating images, comments and regions, each region with an alias |
| `read_range` | a range's content, structure and formatting as compact text, one region at a time |
| `grep_sheet` | Python regex over values, links, notes, formulas and, opt-in, comments |
| `query_range` | filter, pick columns, sort and count on one table region, live |
| `render_range` | the range as page images, opt-in |
| `write_cells` | values, formulas, Markdown rich text and format patches; styling is kept |
| `edit_sheet` | typed structural operations in one atomic batch, always planned |
| `run_sheet_script` | a sandboxed JavaScript program for loops and logic, always planned |
| `batch_update` | raw Sheets `batchUpdate` requests, always planned |
| `pull_tables` | each tab's CSV export behind signed links, for the `gsheet` CLI and DuckDB |
| `table_status` | a head-and-tail fingerprint of a tab, to tell whether a pull is stale |
| `upload_slot` | a signed upload URL so a CLI query result can land back through `write_cells` |
| `auth_status` | who is signed in, and with which scopes |
| `install_skill` | the agent skill bundle |

Writes that are risky return a dry-run plan first. Applying a plan re-reads the touched cells and refuses the batch if anything changed; this narrows the race to about one round trip and is not a lock.

The companion CLI, `npx @luutuankiet/gsheet --help`, loads pulled tables into `.gsheet/workspace.duckdb` for SQL.
