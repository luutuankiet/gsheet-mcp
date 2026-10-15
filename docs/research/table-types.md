# Do native Table column types come back when asked for explicitly?

Research for issue #17. Sources: the Sheets API v4 discovery document (`https://sheets.googleapis.com/$discovery/rest?version=v4`, revision `20261007`), the reference page `developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/sheets` (Table section), the Tables guide `developers.google.com/workspace/sheets/api/guides/tables`, and live calls on 2026-10-15 against the sample sheet `$SAMPLE_SHEET_ID` and a throwaway spreadsheet created and trashed for the test. Rows marked **live** were seen in a real response; **docs** comes from the reference only.

**Short answer.** Yes, when a column has a type: `columnType` is returned by the plain `sheets.tables` mask, no special field or parameter needed. A Table created through the API with typed columns read back every type exactly (**live**). The sample Table returns no `columnType` because none of its 14 columns has one: the field is an enum whose default is `COLUMN_TYPE_UNSPECIFIED`, and the API omits default values, so "absent" means "unspecified", not "hidden". Asking for `sheets.tables.columnProperties.columnType` explicitly on the sample sheet returns 14 empty objects (**live**). For untyped columns the reader must infer a type from the cells; on the sample Table only the value kind is available, since no column carries a number format, validation rule or chip.

## What the API defines

`Table.columnProperties[]` is a list of `TableColumnProperties` (**docs**, discovery revision `20261007`):

| field | type | note |
| :--- | :--- | :--- |
| `columnIndex` | integer | 0-based within the Table, not the sheet. Omitted when `0` (**live**). |
| `columnName` | string | Header text. |
| `columnType` | enum `ColumnType` | `COLUMN_TYPE_UNSPECIFIED`, `DOUBLE`, `CURRENCY`, `PERCENT`, `DATE`, `TIME`, `DATE_TIME`, `TEXT`, `BOOLEAN`, `DROPDOWN`, `FILES_CHIP`, `PEOPLE_CHIP`, `FINANCE_CHIP`, `PLACE_CHIP`, `RATINGS_CHIP`. |
| `dataValidationRule` | `TableColumnDataValidationRule` | "Only set for dropdown column type." Holds one `condition` of type `ONE_OF_LIST`. |

No other Table field carries type information. `rowsProperties` holds only header, band and footer colours. The guide covers setting types through `addTable` and says nothing about reading them or about untyped columns (**docs**).

## Live tests

| test | mask | result |
| :--- | :--- | :--- |
| Sample sheet, full Table | `sheets(properties(sheetId,title),tables)` | 1 Table, 14 columns; each has only `columnName` (and `columnIndex` from 1). No `columnType`, no `dataValidationRule`. |
| Sample sheet, explicit subfields | `sheets.tables(tableId,name,range,columnProperties(columnIndex,columnName,columnType,dataValidationRule),rowsProperties)` | Same as above. |
| Sample sheet, type only | `sheets.tables.columnProperties.columnType` | 14 empty objects `{}`. |
| Throwaway sheet, `addTable` with `DOUBLE`, `PERCENT`, `DATE`, `TEXT`, `DROPDOWN` (with a `ONE_OF_LIST` rule), `BOOLEAN`, and one column with no type | `sheets.tables` | All six types came back as set; the dropdown's `dataValidationRule` came back with its list; the untyped column came back with no `columnType`. |
| Throwaway sheet, cells under the typed Table | `sheets.data.rowData.values(userEnteredFormat.numberFormat,dataValidation,effectiveValue)` | Typed columns stamp a cell number format: `NUMBER`, `PERCENT`, `DATE` (pattern `M/d/yyyy`), `TEXT`. The `BOOLEAN` column put `boolValue: false` in each cell but no cell `dataValidation`. The `DROPDOWN` cells were empty, with no cell `dataValidation`. |

Side finding: `addTable` rejected the name `T1` with "The table name is invalid ... table names must adhere to specific rules, such as not starting with a number or including special characters". Names that read like a cell reference are refused.

> Verdict: the read path is complete. A typed column always says so in `sheets.tables`. An empty `columnType` is a real "no type", which is what the sample Table has.

## Where a reader infers type for an untyped column

In order of strength. The first three are explicit settings; the last is only evidence.

1. **Table `columnType`**, when present. Authoritative, one value per column.
2. **Cell data validation** (`CellData.dataValidation.condition.type`): `BOOLEAN` means checkbox; `ONE_OF_LIST` / `ONE_OF_RANGE` means dropdown. Inside a typed Table, checkbox and dropdown columns keep their rule on the Table, not the cells (**live**), so check both places.
3. **Cell number format** (`userEnteredFormat.numberFormat.type`): `NUMBER`, `CURRENCY`, `PERCENT`, `DATE`, `TIME`, `DATE_TIME`, `SCIENTIFIC`, `TEXT`. A typed Table column writes this onto its cells (**live**), so it is also the best proxy when the type is absent. Dates without a format read as plain serial numbers.
4. **Chips** (`CellData.chipRuns[].chip`): `personProperties` means people, `richLinkProperties` means a file or link. Ratings have no chip shape; outside a typed column the star `formattedValue` is the only hint.
5. **Value kind** (`effectiveValue` key: `numberValue`, `stringValue`, `boolValue`, `errorValue`, or empty). Always available; tells number from text from boolean, nothing finer.

## The sample Table, tested

Script `infer_sample.py` read the 5,786 body rows of all 14 columns with the mask `sheets.data.rowData.values(effectiveValue,userEnteredFormat.numberFormat,dataValidation.condition.type,chipRuns.chip)` and counted signals per column (counts only, no contents).

| signal | columns carrying it |
| :--- | :--- |
| Table `columnType` | 0 of 14 |
| cell number format | 0 of 14 |
| cell data validation | 0 of 14 |
| chips | 0 of 14 |
| value kind (`effectiveValue`) | 14 of 14 |

Breakdown of value kinds: 3 columns are `numberValue` in every row, 9 are `stringValue` in every row, 1 is `stringValue` in 494 rows and empty in the rest, and 1 is empty in every row. No column mixes numbers and text.

So on this sheet the inferred types are "number" or "text", and nothing distinguishes a date stored as a serial number, a currency or a percentage from a plain number. The data looks pasted or imported into a Table that never had types set.

## Implications for a reader

- Request `sheets.tables` as is; a wider or explicit mask gains nothing.
- Report `columnType` when present. When absent, say "untyped" and fall back to the cell signals above, labelling the result as inferred.
- Inferring from values needs a cell read of the Table body, which is expensive on large Tables; a sample of rows is enough when every column is uniform, as here.
- Do not trust a cell number format alone as the user's intent inside a typed Table: the Table wrote it.

## Not covered

- What the Sheets UI shows as the type for these columns. Not checked on screen; the API says unspecified.
- `CURRENCY`, `TIME`, `DATE_TIME` and the chip column types were not created in the throwaway test.
