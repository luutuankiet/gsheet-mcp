# gsheet-mcp cookbook

## Scan first

Call `scan_sheet` before any read. One call covers the whole workbook:

- per tab: size, frozen rows and columns, hidden rows and columns, native
  Tables, charts, the basic filter and filter views, floating images, comment
  threads, and its regions
- spreadsheet level: named ranges and comment counts

Each region is a *table region* (header rows, column names and types, total
rows, footnotes) or a *communication block* (one line: first text, counts of
merges, images and checkboxes). Each gets an alias `<tab>#<n>` that every
other tool accepts while the scan is cached (10 minutes; any write clears it).

- At most 20 regions per tab; `scan_sheet(tab=...)` lists one tab in full.
- Hidden tabs are listed but not scanned; `include_hidden=true` scans them.
- Tabs over 500k cells are read head and tail only and marked
  `extent estimated`.

Then read by alias: `read_range(range="comms#1")`. A read is paged at about
8k tokens; pass the header's `next:` cell as `cursor` for the next page.

## The read notation

Communication regions are cell-addressed lines under `── row N`, non-empty
cells only. A merge shows its range on its top-left cell. Table regions are
Markdown tables whose `row` column holds the real sheet row number.

```text
'comms' · cells 1-41 of 41 (~426 tokens)
styles:
  s1 fill #1F3864 · text #FFFFFF · bold · centred
v1 dropdown: On track | At risk | Blocked

region A1:G9 · communication
── row 1
B1:F1 {s1}: Weekly status · lorem ipsum
```

- `{s1}` points at the `styles:` legend: effective fill, text colour, bold
  and centring. `detail="formats"` adds borders, number formats, column
  widths, row heights and conditional format rules.
- Rich text is inline Markdown: `**bold**`, `*italic*`, `[text](url)`,
  `~~strike~~`; a link whose text is its URL is `<url>`.
- Formulas follow the value: `G12: 42 ← =SUM(G2:G11)`. Inside a table region
  only with `detail="formulas"`.
- Notes sit indented under their cell as `note: <text>`. Comments only with
  `comments=true`.
- Hidden rows and columns are always announced, e.g.
  `rows 12-40 hidden by filter (29 rows)`; their content is skipped unless
  `include_hidden=true`.
- A cell is capped at 500 characters (`…[+N chars]`); `max_chars=0` lifts it.

| thing | shown as |
|---|---|
| checkbox | `[x]` or `[ ]` |
| dropdown | `On track ▾v1`, with the legend line `v1 dropdown: On track \| At risk \| Blocked` once per rule; a value outside the list is marked: `Done ▾v1!` |
| smart chip | `@[Ada](mailto:ada@example.com)` |
| rating | `★★★☆☆ (3)` |
| `IMAGE()` | `=IMAGE("https://example.com/logo.png")` |
| floating image | `H3 [image 96x40 over H3:I5, floating]` |

Floating images never come back from plain REST; the scan finds them in the
xlsx export. A cell under one reads empty.

## The plan path

A plan is a dry run that stores what it would do and returns a `plan_id`,
valid for 1 hour, usable once, only by you and only by the tool that made it.

| tool | plan |
|---|---|
| `write_cells` | optional up to 200 cells; `dry_run=true` for a diff, then call with `plan_id` alone |
| `edit_sheet` | always: `operations` returns the plan; call again with `plan_id` alone |
| `run_sheet_script` | always: the dry run is the default; apply with `plan_id` and `dry_run=false` |
| `batch_update` | always, as `run_sheet_script` |
| `write_cells` with `upload` | always: landing a CLI result is planned |

1. Dry run. Read the diff, the warnings (`B4: drops 2 rich text runs (bold,
   link)`, a value over a formula) and the raw requests.
2. Apply the `plan_id`. Applying uses the plan up, even when it is refused.
3. Read the receipt. `write_cells`, scripts and raw requests re-read the
   touched cells (up to 50) in the read notation. `edit_sheet` reports size
   changes instead, because its operations move or delete cells.
4. On a refusal listing changed cells (`B4: "old" → "new"`), nothing was
   applied: re-read, then make a new plan.

A sheet script, for loops:

```js
// input: {"rows": 10}
for (let i = 0; i < input.rows; i++) sheet.write(`comms!C${i + 3}`, i * 2)
```

`sheet.read("'comms'!A1:C9")` with a string literal is read for you before
the script runs; computed ranges go in `reads` (at most 20). Scripts have no
network and stop after 30 s or 5,000 operations.

## When to render

`render_range` exports the range as PDF and returns page images, about 1,080
image tokens a page at `standard`. Render when the look is the question:

- the read ends with a pointer line, e.g.
  `this range has 1 floating image, 2 fill styles · render_range("'comms'")`
- charts, floating images or colour-coded fills matter to the answer
- you changed layout (merges, widths, fills) and want to see it

Do not render for the words: `read_range` is far cheaper. A render loses
dropdown pill colours, partial star ratings and frozen panes. Renders are
cached for 10 minutes and repeated exports can hit Google's rate limit, so
render once, at `minimal` or `low` quality when that is enough.

## When to pull with the gsheet CLI

Pull instead of reading when the question is SQL-shaped: joins, group-bys,
sums over thousands of rows, or a tab too big to read in pages.
`query_range` answers small filter, sort and count questions live; anything
heavier goes to DuckDB.

1. `pull_tables(spreadsheet, tab?, range?)` returns signed CSV links (valid
   15 minutes) and a `manifest <url>` line.
2. `npx @luutuankiet/gsheet pull <manifest url>` loads every region into
   `.gsheet/workspace.duckdb`: a raw text table plus a clean view with typed
   columns, totals and spacer rows moved to `<name>_dropped`, and cast
   failures in `_cast_failures`.
3. `npx @luutuankiet/gsheet query "<sql>"`.
4. Before trusting a pull again, `table_status(spreadsheet, tab, since=<fingerprint>)`
   or `gsheet status`. It compares the size and the first and last 500 rows
   only; the middle is not compared, so pull again to be sure.
5. To write a result back: create the tab first with `edit_sheet` add_tab
   (an upload never creates one), then `upload_slot`, then `gsheet push <sql
   or file.sql> --to <tab or range> --upload <url>`, then the
   `write_cells(..., upload=<handle>)` call it prints, which returns a plan to
   apply. The target range must be empty.

Run `npx @luutuankiet/gsheet --help` for the whole manual. The CLI holds no
Google credential; it only follows the links the tools hand it.

## The guard's limits

Every planned write fingerprints the cells it touches at the dry run and
compares again just before applying; a structural write also checks the tab's
row and column counts and the values in the affected rows. On any change the
whole batch is refused.

The guard narrows the race to about one round trip. It is not a lock:

- Plain REST has no compare-and-set, so a person can still edit between the
  final read and the write.
- A write without a plan only re-reads just before writing; with no earlier
  fingerprint, nothing is compared.
- Row and column operations are guarded on the rows named, not the rows that
  shift; at most 5,000 cells are fingerprinted.
- `table_status` sees only head and tail.

When people are editing the same sheet, keep plans small and apply them soon
after the dry run.
