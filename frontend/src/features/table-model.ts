import type { ColumnState, GridApi } from "ag-grid-community";
import type { Table } from "../lib/types";
export type Row = Record<string, string | number> & { _sourceRow: number };
export interface Point {
  row: number;
  col: string;
}
export interface Selection {
  text: string;
  rows: number;
  columns: number;
  field: string;
  sourceRow: number;
  value: string;
}
export interface Condition {
  op: string;
  value: string;
}
export const operations: Record<string, string> = {
  contains: "含む",
  equal: "一致",
  empty: "空欄",
  notempty: "空欄以外",
  gte: "以上",
  lte: "以下",
};
export function columnName(index: number) {
  let result = "";
  for (let n = index + 1; n > 0; n = Math.floor((n - 1) / 26))
    result = String.fromCharCode(65 + ((n - 1) % 26)) + result;
  return result;
}
export function dataRows(table: Table, headers: number): Row[] {
  return table.rows.slice(headers).map((row, i) => ({
    _sourceRow: i + headers + 1,
    ...Object.fromEntries(row.map((value, c) => [`c${c}`, value])),
  }));
}
export function numeric(values: (string | number)[]) {
  const nonempty = values.map((v) => String(v ?? "").trim()).filter(Boolean);
  return (
    nonempty.length > 0 &&
    nonempty.every((v) => /^[+-]?(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?$/.test(v))
  );
}
export function matches(
  value: string | number | undefined,
  condition: Condition,
) {
  const text = String(value ?? "");
  if (condition.op === "empty") return !text.trim();
  if (condition.op === "notempty") return !!text.trim();
  if (condition.op === "equal")
    return (
      text.toLocaleLowerCase("ja") === condition.value.toLocaleLowerCase("ja")
    );
  if (condition.op === "gte" || condition.op === "lte") {
    const n = Number(text.replaceAll(",", ""));
    return (
      !!text.trim() &&
      Number.isFinite(n) &&
      (condition.op === "gte"
        ? n >= Number(condition.value)
        : n <= Number(condition.value))
    );
  }
  return text
    .toLocaleLowerCase("ja")
    .includes(condition.value.toLocaleLowerCase("ja"));
}
export function columns(table: Table, headers: number) {
  const rows = dataRows(table, headers);
  return Array.from({ length: table.columns }, (_, c) => {
    const title =
        Array.from({ length: headers }, (_, r) => table.rows[r][c])
          .filter(Boolean)
          .join(" / ") || `列 ${columnName(c)}`,
      values = rows.map((row) => row[`c${c}`]),
      textWidth = (v: string | number) =>
        Math.max(
          0,
          ...String(v ?? "")
            .split("\n")
            .map((line) =>
              Array.from(line).reduce(
                (sum, char) => sum + (char.charCodeAt(0) > 255 ? 13 : 7),
                0,
              ),
            ),
        );
    return {
      field: `c${c}`,
      title,
      isNumber: numeric(values),
      minWidth:
        table.columns > 6
          ? Math.max(
              110,
              Math.min(
                280,
                Math.max(
                  textWidth(title) + 85,
                  ...values.slice(0, 80).map((v) => textWidth(v) + 24),
                ),
              ),
            )
          : 100,
    };
  });
}
export function selection(
  api: GridApi<Row>,
  anchor: Point | null,
  end: Point | null,
): Selection | null {
  if (!anchor || !end) return null;
  const keys = api
      .getAllDisplayedColumns()
      .map((c) => c.getColId())
      .filter((c) => c !== "_sourceRow"),
    a = keys.indexOf(anchor.col),
    b = keys.indexOf(end.col);
  if (a < 0 || b < 0) return null;
  const fields = keys.slice(Math.min(a, b), Math.max(a, b) + 1),
    rows: string[][] = [];
  for (
    let r = Math.min(anchor.row, end.row);
    r <= Math.max(anchor.row, end.row);
    r++
  ) {
    const data = api.getDisplayedRowAtIndex(r)?.data;
    if (data) rows.push(fields.map((f) => String(data[f] ?? "")));
  }
  const data = api.getDisplayedRowAtIndex(end.row)?.data;
  if (!data || !rows.length) return null;
  const tsv = (v: string) =>
    /[\t\r\n"]/.test(v) ? `"${v.replaceAll('"', '""')}"` : v;
  return {
    text:
      rows.length === 1 && fields.length === 1
        ? rows[0][0]
        : rows.map((r) => r.map(tsv).join("\t")).join("\n"),
    rows: rows.length,
    columns: fields.length,
    field: end.col,
    sourceRow: data._sourceRow,
    value: String(data[end.col] ?? ""),
  };
}
export const sortState = (state: ColumnState[]) =>
  state
    .filter((s) => s.sort)
    .sort((a, b) => (a.sortIndex || 0) - (b.sortIndex || 0));
