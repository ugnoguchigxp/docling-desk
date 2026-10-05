import { describe, expect, it } from "vitest";
import type { ColumnState, GridApi } from "ag-grid-community";
import type { Table } from "../lib/types";
import {
  columnName,
  columns,
  dataRows,
  matches,
  numeric,
  selection,
  sortState,
  type Row,
} from "./table-model";
describe("read-only displayed selection", () => {
  it("copies reordered visible columns and sorted rows, quoting multiline TSV without evaluating formulas", () => {
    const rows: Row[] = [
      { _sourceRow: 5, c0: "=SUM(A1)", c1: "a\tb" },
      { _sourceRow: 2, c0: "<img onerror=x>", c1: 'line\n"two"' },
    ];
    const api = {
      getAllDisplayedColumns: () =>
        ["c1", "c0", "_sourceRow"].map((id) => ({ getColId: () => id })),
      getDisplayedRowAtIndex: (i: number) => ({ data: rows[i] }),
    } as unknown as GridApi<Row>;
    const range = selection(api, { row: 0, col: "c1" }, { row: 1, col: "c0" });
    expect(range?.text).toBe(
      '"a\tb"\t=SUM(A1)\n"line\n""two"""\t<img onerror=x>',
    );
    expect(range?.sourceRow).toBe(2);
    expect(range?.rows).toBe(2);
    expect(
      selection(api, { row: 0, col: "c0" }, { row: 0, col: "c0" })?.text,
    ).toBe("=SUM(A1)");
    expect(selection(api, null, { row: 0, col: "c0" })).toBeNull();
    expect(
      selection(api, { row: 0, col: "missing" }, { row: 0, col: "c0" }),
    ).toBeNull();
  });
  it("treats blanks separately from zero in numeric filtering", () => {
    expect(matches("", { op: "gte", value: "0" })).toBe(false);
    expect(matches("0", { op: "gte", value: "0" })).toBe(true);
    expect(matches("1,200", { op: "gte", value: "1000" })).toBe(true);
    expect(matches("  ", { op: "empty", value: "" })).toBe(true);
    expect(matches("a", { op: "empty", value: "" })).toBe(false);
    expect(matches(" a ", { op: "notempty", value: "" })).toBe(true);
    expect(matches("Abc", { op: "equal", value: "abc" })).toBe(true);
    expect(matches("abc", { op: "lte", value: "2" })).toBe(false);
    expect(matches("2", { op: "lte", value: "2" })).toBe(true);
    expect(matches("日本語", { op: "contains", value: "本語" })).toBe(true);
    expect(columnName(0)).toBe("A");
    expect(columnName(25)).toBe("Z");
    expect(columnName(26)).toBe("AA");
    expect(numeric([])).toBe(false);
    expect(numeric(["+1,234.5", "-2"])).toBe(true);
    expect(numeric(["1,2"])).toBe(false);
    const empty = {
      getAllDisplayedColumns: () => [{ getColId: () => "c0" }],
      getDisplayedRowAtIndex: () => undefined,
    } as unknown as GridApi<Row>;
    expect(
      selection(empty, { row: 0, col: "c0" }, { row: 0, col: "c0" }),
    ).toBeNull();
    expect(
      sortState([
        { colId: "b", sort: "desc", sortIndex: 2 },
        { colId: "a", sort: "asc", sortIndex: 1 },
        { colId: "c" },
      ] as ColumnState[]),
    ).toMatchObject([{ colId: "a" }, { colId: "b" }]);
  });
  it("sizes wide sheets from header text and data rows", () => {
    const table: Table = {
      ref: "t",
      label: "表",
      source_label: "1",
      pages: [1],
      columns: 2,
      header_rows: 1,
      merged: false,
      rows: [
        ["名前", ""],
        ["1,000", "あ\nいう"],
      ],
    };
    expect(dataRows(table, 1)[0]._sourceRow).toBe(2);
    expect(columns(table, 1).map((c) => c.minWidth)).toEqual([100, 100]);
    const wide: Table = {
      ...table,
      columns: 7,
      rows: [
        ["", "", "", "", "", "", "見出し"],
        ["1", "x", "", "", "", "", "値"],
      ],
    };
    const sized = columns(wide, 1);
    expect(sized[0].title).toBe("列 A");
    expect(sized[0].isNumber).toBe(true);
    expect(sized[6].isNumber).toBe(false);
    expect(sized[0].minWidth).toBeGreaterThanOrEqual(110);
  });
});
