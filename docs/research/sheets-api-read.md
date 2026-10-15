# What the Sheets API gives a reader of a messy sheet

Research for issue #3. Sources: the Google Sheets API v4 reference (`developers.google.com/workspace/sheets/api/reference/rest/v4/...`), the Sheets API release notes (`developers.google.com/workspace/sheets/release-notes`), and targeted live reads of the sample sheet `$SAMPLE_SHEET_URL` on 2026-10-15. Rows marked **live** were seen in a real response; rows marked **docs** come from the reference only.

**Short answer.** One `spreadsheets.get` with a field mask and `ranges` returns nearly everything a reader needs: merges, frozen counts, hidden flags, Tables, filters, banding, charts, conditional format rules and named ranges at the sheet level, and per cell the value, formula, formatted text, colours, rich text runs with partial links, notes, data validation (checkboxes and dropdown chips) and smart chips. Images are the hole: an inserted in-cell image and an image placed over the grid are both absent from every response, and an image cell reads as empty. Charts come back as a spec, never as a picture. Filter views are listed but never mark rows as hidden.

## Feature table

| feature | where in response | cheapest field mask | gaps |
| :--- | :--- | :--- | :--- |
| Merged cells | `sheets[].merges[]` (GridRange list) | `sheets.merges` | Only the top-left cell holds a value; the other cells of the merge come back empty. `sheetId` is omitted when it is `0` (**live**). |
| Fill colour | `CellData.effectiveFormat.backgroundColorStyle` (and `userEnteredFormat`) | `sheets.data.rowData.values.effectiveFormat.backgroundColorStyle` | Zero channels are omitted, so black reads as `rgbColor: {}` (**live**). Banding and Table colours are not baked into the cell; read them from `bandedRanges` or `tables`. The legacy `backgroundColor` field duplicates the `*Style` field. |
| Text colour, bold, italic | `effectiveFormat.textFormat` (`foregroundColorStyle`, `bold`, ...) | `sheets.data.rowData.values.effectiveFormat.textFormat` | Same zero-omission rule. |
| Theme colours | `ColorStyle.themeColor` (`ACCENT1`..`ACCENT6`, `TEXT`, `BACKGROUND`, `LINK`); map in `properties.spreadsheetTheme` | `properties.spreadsheetTheme` plus the cell mask | A cell may carry a theme reference instead of RGB; resolve it through `spreadsheetTheme`. Only `rgbColor` appeared on the sample sheet (**live**). |
| Rich text runs | `CellData.textFormatRuns[]` (`startIndex` in UTF-16 code units, `format`) | `sheets.data.rowData.values.textFormatRuns` | A run lasts until the next run's `startIndex`. Runs exist only on literal strings, never on formulas, numbers or booleans (**docs**). |
| Hyperlink, whole cell | `CellData.hyperlink` and `effectiveFormat.textFormat.link.uri` | `sheets.data.rowData.values.hyperlink` | `hyperlink` is read-only and empty when a cell holds more than one link (**docs**). `formattedValue` is the display text, not the URL (**live**). |
| Hyperlink, partial | `textFormatRuns[].format.link.uri` | `sheets.data.rowData.values.textFormatRuns.format.link` plus `.startIndex` | `hyperlink` stays absent even when one run covers most of the cell (**live**, a cell with a linked URL line then a plain line). |
| Smart chips (people, Drive files, YouTube, Maps, Calendar) | `CellData.chipRuns[]`: `chip.personProperties.email` or `chip.richLinkProperties.uri`/`mimeType`; each chip is a 1-character `@` placeholder in the text | `sheets.data.rowData.values.chipRuns` | Only Drive file chips and people chips are writable; other rich links are read-only (**docs**, GA 2025-06-17). None on the sample sheet. Rating, finance and place chips have no `chipRuns` shape. |
| Rating chip | `userEnteredValue.numberValue` plus `formattedValue` such as `★★★☆☆` | `sheets.data.rowData.values.formattedValue` | No validation rule or number format reveals it is a rating (**live**); the stars in `formattedValue` are the only signal. In a Table, `columnType: RATINGS_CHIP` says so. |
| Frozen rows and columns | `sheets[].properties.gridProperties.frozenRowCount` / `frozenColumnCount` | `sheets.properties.gridProperties` | None. |
| Hidden by user | `rowMetadata[]` / `columnMetadata[]` `.hiddenByUser`, `pixelSize` | `sheets.data.rowMetadata,sheets.data.columnMetadata` | Must request a range to get metadata; `false` is omitted. Hidden tabs are `sheets[].properties.hidden` (4 of 7 tabs on the sample sheet are hidden, **live**). |
| Hidden by filter | `DimensionProperties.hiddenByFilter` (read-only) | same as above | Reflects the basic filter only. A filter view is per user and never sets this flag (**docs**). |
| Native Tables | `sheets[].tables[]`: `tableId`, `name`, `range`, `rowsProperties` colours, `columnProperties[]` (`columnName`, `columnType`, `dataValidationRule` for dropdown columns) | `sheets.tables` | The same Table is also reported in `bandedRanges` with the same id (**live**); pass `excludeTablesInBandedRanges=true` to drop the copy. `columnType` was absent for every column of the sample Table, so a reader cannot tell a typed column from an untyped one there. Table filters are not in `tables`. API write support announced 2025-04-29. |
| Basic filter | `sheets[].basicFilter` (`range`, `sortSpecs`, `filterSpecs`) | `sheets.basicFilter` | None for reading; the rows it hides carry `hiddenByFilter`. |
| Filter views | `sheets[].filterViews[]` (`filterViewId`, `title`, `range`, `namedRangeId`, `tableId`, `sortSpecs`, `filterSpecs`) | `sheets.filterViews` | Listed, but which view a user has open is invisible, and rows they hide are not flagged. |
| Banding | `sheets[].bandedRanges[]` (`rowProperties` / `columnProperties` header, first, second, footer colours) | `sheets.bandedRanges` | Includes Table banding unless excluded (above). |
| Checkboxes | `dataValidation.condition.type: BOOLEAN`, value in `userEnteredValue.boolValue` | `sheets.data.rowData.values.dataValidation` plus the value | `values.get` returns `TRUE`/`FALSE` strings with no hint it is a checkbox (**live**). |
| Dropdowns and dropdown chips | `dataValidation.condition.type: ONE_OF_LIST` / `ONE_OF_RANGE`, `values[].userEnteredValue`, `showCustomUi`, `strict` | `sheets.data.rowData.values.dataValidation` | The live response also carried `customUiMode: "CHIP"` and per-option `dropdownStyle.backgroundColorStyle` / `foregroundColorStyle` (**live**), neither listed in the reference page. Treat them as present but undocumented. Validation repeats on every cell; there is no sheet-level list. |
| `=IMAGE()` | `userEnteredValue.formulaValue` holds the formula and URL | `sheets.data.rowData.values.userEnteredValue` | No pixels; `effectiveValue` is empty for an image (**docs**, not verified on the sample sheet, which has no `=IMAGE()`). |
| Inserted in-cell image | nowhere | none | The cell returns no keys at all, identical to an empty cell (**live**, the image cell in "table for comms" and the logo in "table for comms 2"). |
| Image over the grid | nowhere | none | No embedded-object list for images exists; only charts are embedded objects (**live**: the image straddling two cells left no trace). |
| Charts | `sheets[].charts[]`: `chartId`, `spec` (type, source ranges, `hiddenDimensionStrategy`), `position.overlayPosition` (anchor cell, offsets, pixel size), `border` | `sheets.charts` | Spec only, no rendered image and no computed data labels; the donut's 1.9 % / 98.1 % labels are not in the response (**live**). |
| Conditional formats | `sheets[].conditionalFormats[]` (ranges, boolean or gradient rule) | `sheets.conditionalFormats` | Rules are sheet level; their effect shows only in `effectiveFormat`, never which rule fired. None on the sample sheet. |
| Notes | `CellData.note` (plain string) | `sheets.data.rowData.values.note` | No author or time. |
| Comments | top-level `comments[]` (CommentThread: `anchorId`, `headPost`, `replies`, `status`, `plainTextQuote`) with `commentsViewMode=INCLUDED` | `comments` plus the query parameter | GA 2026-09-30. Returns 403 if the caller cannot see comments. |
| Named ranges | top-level `namedRanges[]` (`namedRangeId`, `name`, `range`) | `namedRanges` | None. |
| Developer metadata | top-level, `sheets[].developerMetadata`, and `DimensionProperties.developerMetadata` | `developerMetadata,sheets.developerMetadata` | Visibility `PROJECT` metadata is visible only to the project that wrote it. |
| Protected ranges, groups, slicers | `sheets[].protectedRanges`, `rowGroups`, `columnGroups`, `slicers` | each by name | None found on the sample sheet. |
| Formulas versus values | `userEnteredValue.formulaValue` (formula), `effectiveValue` (computed), `formattedValue` (display) all in one `CellData` | pick the ones you need | `values.get` gives one view per call: `valueRenderOption` `FORMATTED_VALUE` (default), `UNFORMATTED_VALUE`, `FORMULA`; `dateTimeRenderOption` `SERIAL_NUMBER` (default) or `FORMATTED_STRING`, ignored with `FORMATTED_VALUE`. Dates are serial numbers in `userEnteredValue` (`46023` showed as `01/2026`, **live**). |

## How the reads differ

- **`values.get` / `values.batchGet`** returns a 2D array of strings or numbers only: no merges, colours, links, validation, notes or chips. Trailing empty rows and columns are dropped, merged areas show one value in the top-left cell, and hidden rows are returned like any other row with no flag. Cheapest way to pull a structured table into DuckDB.
- **`spreadsheets.get` without grid data** (`fields` limited to `sheets.properties`, `sheets.merges`, `sheets.tables`, `sheets.charts`, ...) is the landscape scan: tab list with hidden and frozen state, every Table, chart, filter, banding and named range, with no cells. The sample sheet's full scan was one call.
- **`spreadsheets.get` with grid data** (a `sheets.data...` mask plus `ranges`) is the communication-sheet read. `includeGridData` is ignored when a field mask is set, so the mask alone decides. Several ranges in one call come back ordered by tab position, not by request order (**live**); match them by `startRow` / `startColumn` and tab.

## Response size

Cell masks dominate cost. On the sample sheet, 98 cells (A1:N7 of "table for comms") with values, background and text colour, runs and links came back at roughly 15k tokens, mostly repeated `effectiveFormat` blocks on every cell, including empty ones. The structured Table is 5,787 rows by 14 columns (81k cells); a grid read of it with formats would be several megabytes, while `values.get` of the same range is plain text. Rules that follow:

1. Never read grid data without `ranges`; never use `includeGridData=true` without a mask.
2. Read structured tables with `values.batchGet`, and read formats only for communication sheets or a small window.
3. Ask for `effectiveFormat.backgroundColorStyle` and `textFormat` leaves rather than all of `effectiveFormat`, which also carries padding, alignment, wrap and borders.
4. Default values are omitted, so an absent key means "default", never "unknown".

## Invisible to the API

- Inserted in-cell images and over-grid images (no field at all).
- Rendered charts, and labels a chart computes.
- Which filter view a user has open, and rows a filter view hides.
- That a number is a rating chip, outside a typed Table column.
- Which conditional rule produced a colour.
- What a user sees on screen at a given scroll or zoom.

A page render (export or screenshot) is the only way to see images, charts and the final look.

## Practical notes for this repo

- The `$GCPX_ALIAS` token currently lacks the Sheets and Drive scopes: `spreadsheets.get` returns `403 ACCESS_TOKEN_SCOPE_INSUFFICIENT`. Adding the scope needs a human consent step.
- The official Google Sheets connector (the Sheets MCP server, developer preview since 2026-07-13) accepts `fields` only as flat dotted paths (`sheets.data.rowData.values.note`); the REST API's parenthesised form `properties(title,locale)` is rejected by it.
- Google now ships its own Sheets MCP server with `get_spreadsheet` (fields, ranges, comments) and `update_spreadsheet`; it returns raw API JSON and makes no attempt to shape it for a reader.
