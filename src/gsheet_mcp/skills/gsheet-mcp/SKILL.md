---
name: gsheet-mcp
description: Reading and editing Google Sheets through the gsheet-mcp server - which tool fits a job, scan first, the read notation, the plan path for writes, when to render, when to pull tables into DuckDB with the gsheet CLI, and what the stale-write guard does not promise. Skip this skill if you only glance at one small range.
---
# Working on Google Sheets with gsheet-mcp

The server works on a live Google Sheet from its URL. Every tool takes the
spreadsheet's URL or id. The live sheet is always the source of truth: a scan
is cached for 10 minutes, and any write through these tools clears it.

## Addressing

- A tab plus an A1 range: `'table for comms'!B2:F8`. Quote a tab name with
  spaces or punctuation. A whole tab is `'comms'`; a missing range means the
  whole used area.
- A scan alias such as `comms#1` names one region. It is valid only while the
  scan is cached, so scan again when an alias is refused.

## Pick the tool

| the job | tool |
|---|---|
| What tabs, regions, tables, charts, images and comments a workbook has | `scan_sheet` (start here) |
| Read a region's content, structure and formatting | `read_range` |
| Find text, links, notes or formulas | `grep_sheet` (Python regex) |
| A small live question on one table: filter, pick columns, sort, count | `query_range` |
| See how a range looks: colours, merges, charts, images | `render_range` (opt-in) |
| Write values, formulas, rich text or a format patch | `write_cells` |
| Freeze, hide, merge, insert, sort, filter, tabs, validation, notes, comments | `edit_sheet` |
| Loops and logic over many cells | `run_sheet_script` |
| Anything no typed tool covers (charts, pivots, conditional formats) | `batch_update` (raw requests) |
| Tables into a local DuckDB file for SQL | `pull_tables`, then the `gsheet` CLI |
| Has a pulled tab changed since the pull | `table_status` |
| Land a CLI query result back in the sheet | `upload_slot`, then `write_cells` with `upload` |
| Who is signed in, with which scopes | `auth_status` |
| This skill | `install_skill` |

## The loop

1. `scan_sheet` once: it maps every tab in about 2k tokens and names each
   region with an alias.
2. `read_range` or `grep_sheet` on the regions you need, not whole tabs.
3. Write with `write_cells` or `edit_sheet`. Risky writes go through a dry
   run that returns a `plan_id`; apply that plan.
4. Read the receipt: it re-reads the touched cells in the read notation.

The [cookbook](sheets-cookbook.md) covers each step: scan first, the plan
path, when to render, when to pull with the `gsheet` CLI, the guard's limits
and the full read notation table.

## Rules worth knowing before a write

- A value write never touches styling: it sends values only.
- Types come from JSON: a string is text, a number a number, `true`/`false` a
  checkbox state, a string starting `=` a formula, an ISO date into a
  date-formatted cell a date. `null` leaves a cell alone; `""` clears it.
- Writes take the same inline Markdown reads show (`**bold**`, `*italic*`,
  `~~strike~~`, `[text](url)`).
- Refused: a covered cell of a merge, a non-boolean in a checkbox, a value
  outside a strict dropdown. Warned: plain text over rich text, a value over
  a formula, `=IMAGE(url)` (a person must allow external data once).
- No image inserts. New comments are unanchored, because no public API anchors
  a new comment to a cell.
