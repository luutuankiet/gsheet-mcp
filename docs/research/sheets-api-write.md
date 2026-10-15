# What the Sheets API lets a writer do, and how writes are guarded

Research for issue #4. Sources: the Sheets API v4 discovery document (revision `20261007`), the Sheets API guides and limits page, the reference page of Google's own hosted Sheets MCP server, the Drive API v3 discovery document, and the Apps Script `SpreadsheetApp` reference. Every "live" claim was tested on 2026-10-15 on a throwaway spreadsheet that was created for the test and then trashed; the sample sheet was never touched. Raw responses and the probe script live in the local `tmp/20261015/sheets-api-write/` folder (not committed).

## Capability table

"Atomic?" means: can it ride in one `spreadsheets.batchUpdate` with everything else, so the whole batch lands or nothing does. All `batchUpdate` requests are atomic together (see "Atomicity" below); the values endpoints are a separate call and cannot share a batch with formatting.

| capability | request | atomic? | gotchas |
|---|---|---|---|
| Write values or formulas | `values.update` / `values.batchUpdate` with `valueInputOption` `RAW` or `USER_ENTERED`; or `updateCells` with `userEnteredValue.{stringValue, numberValue, boolValue, formulaValue}` | values endpoints: one call, own transaction; `updateCells`: yes | `RAW` stores `=1+1` as text; `USER_ENTERED` parses it like typing, so `2026-10-15` becomes date serial `46310` (live). `updateCells` is unambiguous: the field name says what it is. Writing a new `userEnteredValue` erases the cell's text runs and chip runs. |
| Rich text runs | `updateCells` with `textFormatRuns[]` (`startIndex` + `TextFormat`) and field mask `textFormatRuns` | yes | Runs only on plain strings, never formulas, numbers or bools. Indices are UTF-16 code units. Writing runs replaces all prior runs. A run inherits the cell format unless it overrides. |
| Partial-cell hyperlinks | `textFormatRuns[].format.link.uri` | yes | Live: two links in one cell work; Sheets adds underline and the theme link colour on its own. The read-only `CellData.hyperlink` is then empty, because it only reports a single cell-wide link. A run link clears any cell-level link set in the same request. Whole-cell link alternatives: `=HYPERLINK()` or `userEnteredFormat.textFormat.link`. |
| Smart chips | `updateCells` with `chipRuns[]` (`personProperties.email`, `richLinkProperties.uri`) and an `@` placeholder in the text | yes | Only person chips and Drive-file rich links are writable; other rich link types are read only. Drive chips need a `drive`, `drive.file` or `drive.readonly` scope. URI max 2000 bytes. |
| Merges | `mergeCells` (`MERGE_ALL`, `MERGE_COLUMNS`, `MERGE_ROWS`), `unmergeCells` | yes | Only the top-left cell keeps its value. Live: read-back lists the merge under `sheets.merges`. |
| Colours | `repeatCell` or `updateCells` with `userEnteredFormat.backgroundColorStyle`, `textFormat.foregroundColorStyle` | yes | Use the `*Style` fields; the plain `backgroundColor` / `foregroundColor` are deprecated. `userEnteredFormat` merges with the existing format, scoped by the field mask. Conditional formats are separate (`addConditionalFormatRule`) and override the visible colour. |
| Borders | `updateBorders` (top, bottom, left, right, innerHorizontal, innerVertical) | yes | Omitted sides are left alone; `style: NONE` erases. |
| Freeze | `updateSheetProperties` with `gridProperties.frozenRowCount` / `frozenColumnCount` | yes | Freezing through a merged block fails in the UI; expect the same from the API. |
| Hide and unhide rows or columns | `updateDimensionProperties` with `properties.hiddenByUser` | yes | Rows hidden by a filter are a different flag (`hiddenByFilter`, read only). Collapsed groups are a third mechanism (`updateDimensionGroup`). |
| Insert, delete, move rows or columns | `insertDimension`, `deleteDimension`, `moveDimension`, `appendDimension`, `insertRange` / `deleteRange` (shift cells) | yes | Each request sees the shifts made by the requests before it in the same batch, so later indices must account for earlier inserts (same rule as the Docs API). |
| Sort a range | `sortRange` with `sortSpecs[]` | yes | Sorts the stored rows for everyone (not a view). Live: sorted rows 2 to 4 by quantity. |
| Native Tables | `addTable`, `updateTable` (field mask), `deleteTable`; `appendCells` with `tableId` to add rows | yes | Live: `addTable` reply returns `tableId` and default banding colours. A `DROPDOWN` column must carry a `ONE_OF_LIST` rule. Insert or delete table rows with `insertRange` / `deleteRange` covering whole table rows. `deleteBanding` strips table styling but keeps data. Column types include `FILES_CHIP`, `PEOPLE_CHIP`, `RATINGS_CHIP`. |
| Basic filter | `setBasicFilter`, `clearBasicFilter` | yes | One basic filter per sheet; it hides rows for every viewer. |
| Filter views | `addFilterView`, `updateFilterView`, `duplicateFilterView`, `deleteFilterView` | yes | Live: reply returns `filterViewId`. Filter views change nothing for other viewers. |
| Checkboxes | `setDataValidation` or `updateCells.dataValidation` with condition `BOOLEAN` | yes | Zero, one or two condition values pick what counts as checked and unchecked. |
| Dropdowns | `setDataValidation` with `ONE_OF_LIST` (literal values) or `ONE_OF_RANGE` (A1 range) and `showCustomUi: true` | yes | `ONE_OF_LIST` does not accept formulas. Table `DROPDOWN` columns render as chip dropdowns. |
| Image in a cell | `=IMAGE(url)` formula only; no request inserts a `CellImage` | yes (as a formula) | Live surprise: on a new file, `=IMAGE()` written by the API evaluates to `#REF!` with "Please use a desktop web browser to allow access to fetch data from external urls." A human must allow external data once per file. True in-cell images need Apps Script `SpreadsheetApp.newCellImage()`. |
| Image over cells | none in the Sheets API | n/a | No `batchUpdate` request creates an over-grid image. Apps Script `Sheet.insertImage(url or blob, column, row, offsetX, offsetY)` does. |
| Charts | `addChart`, `updateChartSpec`, `updateEmbeddedObjectPosition`, `deleteEmbeddedObject` | yes | Charts are fully writable by the API; Apps Script is not needed for them. |
| Notes and comments | `updateCells.note`; new comment requests `insertComment`, `addCommentReply` (also resolve and reopen), `updateCommentPost`, `deleteComment`, `deleteCommentReply` | yes | Comments in `batchUpdate` are new in this discovery revision; the reply has `commentUpdateState`. Not tested live. |
| Developer metadata | `createDeveloperMetadata`, `updateDeveloperMetadata`, `deleteDeveloperMetadata`; `values.batchUpdateByDataFilter` writes by metadata | yes | Anchors on a whole row, column, sheet or spreadsheet only (no cell). Visibility `DOCUMENT` or `PROJECT`. |
| Protection | `addProtectedRange`, `updateProtectedRange`, `deleteProtectedRange` | yes | `warningOnly` ranges warn humans; they do not stop the API. |

## Atomicity

The limits page states: "All Sheets requests are applied atomically. That is, if any request is not valid then the entire update is unsuccessful and none of the (potentially dependent) changes are applied." The `BatchUpdateSpreadsheetRequest.requests` description says the same and adds that requests apply in order.

Live: a batch of `updateCells` (write A5) followed by `deleteSheet` on a missing sheet returned 400 `Invalid requests[1].deleteSheet: No sheet with id: 999999`, and A5 stayed empty. A batch of eleven requests (values, rich text links, checkbox, dropdown, sort, table, freeze, hide, merge, border, colour, filter view, metadata) landed together.

`values.batchUpdate` is its own call. To make values and formatting land together, write values through `updateCells` inside the one `batchUpdate`.

## Stale-write guard

| candidate | verdict | evidence |
|---|---|---|
| `writeControl.requiredRevisionId` on Google's hosted Sheets MCP (`sheetsmcp.googleapis.com`, tool `update_spreadsheet`) | ✅ works | Live: `get_spreadsheet` returns a top-level `revisionId`; it is stable across reads; it changes on every write (values, format-only, developer metadata value). A batch sent with the current id succeeded; the same id after one format-only write returned 400 "The required revision ID '...' does not match the latest revision." |
| `writeControl` on public REST v4 `spreadsheets.batchUpdate` | ❌ silently ignored | Not in the public discovery schema. Live: `fields=revisionId` returns `{}`, and a batch carrying a stale `requiredRevisionId` returned 200 and applied. Same for `values.batchUpdate`. |
| Drive `files.version` | ❌ useless | Discovery says it reflects every change, but live it stayed at `4`, with `modifiedTime` frozen at creation, across a dozen Sheets writes over several minutes. |
| Drive `headRevisionId` | ❌ absent | Discovery: "only available for files with binary content". Not returned for a Sheet. |
| Drive `revisions.list` | ❌ too coarse | Live: ids `1` and `12` only; Sheets coalesces edits, and the docs warn the list may be incomplete. |
| Developer metadata as a lock | ⚠️ advisory only | A writer can stamp a `SPREADSHEET` metadata value and check it before writing, but check and write are two calls, so it is not a compare-and-set. Updating it also bumps `revisionId`. |
| Read-compare-write on the cells you touch | ⚠️ workable fallback | Read the target range (values and formats) just before the batch and refuse if it changed since the agent last read it. Narrows the race window but does not close it. |

The public REST surface therefore has no compare-and-set. The only real guard found is the hosted Sheets MCP's `requiredRevisionId`, which is the same contract as the Docs API's `writeControl.requiredRevisionId`.

## Quotas

From the limits page: write requests 300 per minute per project and 60 per minute per user per project; reads the same. A whole `batchUpdate`, including every subrequest, counts as one request. One request may run up to 180 seconds; a 2 MB payload is the recommended ceiling. 429 means back off exponentially. The page says exceeding quota "is planned to incur charges ... later in 2026". The hosted Sheets MCP draws from the same read and write buckets, one unit per tool call.

## What only Apps Script can do

- Put a real image inside a cell: `SpreadsheetApp.newCellImage()` with `CellImageBuilder.setSourceUrl`, `setAltTextTitle`, `setAltTextDescription`.
- Put an image over the grid: `Sheet.insertImage(url or blob, column, row[, offsetX, offsetY])`.
- Everything else asked about here (rich text, links, chips, merges, colours, borders, freeze, hide, sort, Tables, filters, filter views, checkboxes, dropdowns, charts, comments) has a REST request.

## Open questions

- Is `revisionId` per user and time-limited as in the Docs API? Not tested; only one identity was used.
- Will the hosted MCP's `writeControl` ever reach public REST? The connector's tool page is the only place it is documented.
- Does the external-data consent for `=IMAGE()` carry over when a file is copied from one that already has it?
