# Canary reference sheet

The canary is a public spreadsheet, `CANARY_SHEET_ID` in `.env`. Anyone with the link can edit it. It holds only lorem ipsum and synthetic numbers, and every feature gsheet-mcp reads or writes appears on it at least once.

Because anyone can edit the canary, tests compare against the files here, never against the live sheet. Destructive live checks run on a copy.

| file | what it is |
|---|---|
| `build.py` | builds a new canary through `batchUpdate`, writes `manifest.json`, then prints the steps that must be done by hand |
| `build.py --capture` | after the steps by hand, saves `tests/fixtures/canary.json` (one lean-mask `spreadsheets.get` reply) and `tests/fixtures/canary.xlsx` (the xlsx export, which carries the floating image) |
| `manifest.json` | every feature, listed with its tab and range and whether it was made by the API or by hand |
| `canary-image.png` | the 96x40 picture that floats over `comms!H3:I5` |

## Tabs

| tab | exercises |
|---|---|
| `comms` | merged header bands, frozen rows and column, fills, rich text with links, `HYPERLINK()`, checkboxes, dropdown chips, a note, an anchored comment with a reply, a floating image, `IMAGE()`, emoji, a cell over 500 characters, a rating chip |
| `dashboard` | donut chart, checkbox grid, merged blocks, banding, a conditional format |
| `native-table` | native Table `Orders` with typed and untyped columns, a filter view, sorted rows |
| `multi-region` | two tables side by side, one table continued after a 2-row gap (the scan should join it), one after a 4-row gap (the scan should split it), loose numbers, a touching total row, a footnote, the named range `quarterly_targets` |
| `hidden` | hidden rows, a hidden column, a row hidden by a basic filter |
| `formulas` | formulas across tabs and over a named range, `#REF!` and `#DIV/0!`, a warning-only protected range |
| `hidden-tab` | a hidden tab with a small grid |

## Steps by hand

No Sheets REST request can do these four, so `build.py` prints them and `manifest.json` marks them `by hand`:

1. Place an image floating over `comms!H3:I5`.
2. Add a rating chip in `comms!G3`.
3. Colour the dropdown chips in `comms!E3:E8`.
4. Anchor a comment with a reply on `comms!F4`.

## Larger data

Tables at full size, 50k and 200k rows, are on a separate view-only sheet. It is built by `fixtures/bulk/build.py`, and its ground truth is in `fixtures/bulk/golden-data-ground-truth.json`.
