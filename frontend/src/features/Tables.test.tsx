import { useEffect, type ReactNode } from "react";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { GridApi } from "ag-grid-community";
import type { Job, Table } from "../lib/types";
import type { Row } from "./table-model";
import { TableCard, Tables } from "./Tables";

interface ColumnSnapshot {
  colId: string;
  sort?: "asc" | "desc" | null;
  sortIndex?: number | null;
  pinned?: "left" | null;
}
interface ColumnDef {
  field?: string;
  headerName?: string;
  headerClass?: () => string;
  comparator?: (
    a: unknown,
    b: unknown,
    nodeA: unknown,
    nodeB: unknown,
    descending: boolean,
  ) => number;
  cellRenderer?: (params: { value: unknown }) => ReactNode;
  tooltipValueGetter?: (params: { value?: unknown }) => string;
  cellClassRules?: Record<
    string,
    (params: { node: { rowIndex: number | null } }) => boolean
  >;
  headerComponentParams?: {
    innerHeaderComponent?: (params: {
      column: { getColId: () => string };
      displayName: string;
    }) => ReactNode;
  };
}
interface MockApi {
  setFocusedCell: (row: number, col: string) => void;
  getColumnState: () => ColumnSnapshot[];
}
interface MockGridProps {
  rowData: Row[];
  columnDefs?: ColumnDef[];
  icons?: Record<string, () => Node>;
  defaultColDef?: {
    valueFormatter?: (params: { value: unknown }) => string;
  };
  onGridReady: (event: { api: GridApi<Row> }) => void;
  onGridPreDestroyed?: () => void;
  onModelUpdated?: (event: { api: { getDisplayedRowCount: () => number } }) => void;
  onSortChanged?: () => void;
  onFilterChanged?: () => void;
  onColumnMoved?: (event: { finished?: boolean }) => void;
  onColumnResized?: (event: { finished?: boolean }) => void;
  onColumnPinned?: () => void;
  onCellFocused?: (event: {
    rowIndex: number | null;
    column: string | { getColId: () => string } | null;
  }) => void;
  doesExternalFilterPass?: (node: { data?: Row }) => boolean;
  isExternalFilterPresent?: () => boolean;
  getRowId?: (params: { data: Row }) => string;
}

const grid = vi.hoisted(() => ({
  destroyed: false,
  columnIds: null as null | string[],
  columns: [] as ColumnSnapshot[],
  focused: null as null | {
    rowIndex: number;
    column: { getColId: () => string };
  },
  props: null as null | MockGridProps,
  api: null as null | MockApi,
}));

// Keep real selection/copy handlers; replace the grid's layout-dependent renderer.
vi.mock("ag-grid-react", () => ({
  AgGridReact: function Grid(props: MockGridProps) {
    grid.props = props;
    useEffect(() => {
      const api = {
        isDestroyed: () => grid.destroyed,
        getColumnState: () => grid.columns.map((column) => ({ ...column })),
        applyColumnState: (options: {
          state?: ColumnSnapshot[];
          defaultState?: { sort: null };
          applyOrder?: boolean;
        }) => {
          if (options.defaultState?.sort === null)
            for (const column of grid.columns) column.sort = null;
          for (const next of options.state ?? []) {
            const current = grid.columns.find(
              (column) => column.colId === next.colId,
            );
            if (current) Object.assign(current, next);
            else grid.columns.push({ ...next });
          }
        },
        onFilterChanged: () => undefined,
        setColumnsVisible: () => undefined,
        refreshCells: () => undefined,
        refreshHeader: () => undefined,
        resetColumnState: () => {
          grid.columns = [];
        },
        getAllDisplayedColumns: () =>
          (grid.columnIds ??
            Object.keys(grid.props?.rowData[0] ?? { c0: "" })
          ).map((id) => ({ getColId: () => id })),
        getDisplayedRowAtIndex: (index: number) => {
          const row = grid.props?.rowData[index];
          return row ? { data: row } : undefined;
        },
        getDisplayedRowCount: () => grid.props?.rowData.length ?? 0,
        ensureIndexVisible: () => undefined,
        ensureColumnVisible: () => undefined,
        setFocusedCell: (row: number, col: string) => {
          grid.focused = { rowIndex: row, column: { getColId: () => col } };
        },
        getFocusedCell: () => grid.focused,
      };
      grid.api = api;
      props.onGridReady({ api: api as unknown as GridApi<Row> });
      props.onModelUpdated?.({ api });
      Object.values(props.icons ?? {}).forEach((icon) => icon());
      props.defaultColDef?.valueFormatter?.({ value: null });
      props.defaultColDef?.valueFormatter?.({ value: "value" });
      props.isExternalFilterPresent?.();
      const first = props.rowData[0];
      if (first) props.getRowId?.({ data: first });
      return () => props.onGridPreDestroyed?.();
    }, [props.onGridReady, props.rowData]);
    const headers = (props.columnDefs ?? []).map((def) => {
      const Header = def.headerComponentParams?.innerHeaderComponent;
      const id = def.field ?? "";
      return (
        <div key={id || "column"} className="ag-header-cell" col-id={id}>
          {Header ? (
            <Header
              column={{ getColId: () => id }}
              displayName={def.headerName ?? ""}
            />
          ) : null}
        </div>
      );
    });
    return (
      <>
        {headers}
        <div className="ag-header-cell" data-testid="blank-header" />
        {props.rowData.map((row, index) => (
          <div key={index} className="ag-row" row-index={index}>
            {Object.entries(row)
              .filter(([key]) => key !== "_sourceRow")
              .map(([key, value]) => (
                <div key={key} className="ag-cell" col-id={key} role="gridcell">
                  {String(value)}
                </div>
              ))}
          </div>
        ))}
      </>
    );
  },
}));

const dialogMethods = ["showModal", "close"] as const;
const originalDialogMethods = dialogMethods.map((name) =>
  Object.getOwnPropertyDescriptor(HTMLDialogElement.prototype, name),
);
const originalClipboard = Object.getOwnPropertyDescriptor(
  navigator,
  "clipboard",
);
beforeEach(() => {
  grid.destroyed = false;
  grid.columnIds = null;
  grid.columns = [];
  grid.focused = null;
  Object.defineProperties(HTMLDialogElement.prototype, {
    showModal: {
      configurable: true,
      value() {
        this.setAttribute("open", "");
      },
    },
    close: {
      configurable: true,
      value() {
        this.removeAttribute("open");
      },
    },
  });
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  dialogMethods.forEach((name, index) => {
    const original = originalDialogMethods[index];
    if (original)
      Object.defineProperty(HTMLDialogElement.prototype, name, original);
    else Reflect.deleteProperty(HTMLDialogElement.prototype, name);
  });
  if (originalClipboard)
    Object.defineProperty(navigator, "clipboard", originalClipboard);
  else Reflect.deleteProperty(navigator, "clipboard");
});

function sample(overrides: Partial<Table> = {}): Table {
  return {
    ref: "#/tables/0",
    label: "表1",
    source_label: "3ページ",
    pages: [3],
    rows: [
      ["名前", "金額"],
      ["Alpha", "1,200"],
      ["Beta", "50"],
    ],
    columns: 2,
    header_rows: 1,
    merged: true,
    ...overrides,
  };
}
function column(field: string) {
  const found = grid.props?.columnDefs?.find((def) => def.field === field);
  if (!found?.comparator || !found.cellClassRules || !found.headerClass)
    throw new Error(field);
  return found;
}
function clipboard(writeText: () => Promise<void>) {
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: { writeText: vi.fn(writeText) },
  });
}
function job(overrides: Partial<Job> = {}): Job {
  return {
    id: "job",
    filename: "file.pdf",
    folder_id: null,
    state: "success",
    created: 1,
    duration: null,
    pages: 1,
    tables: 1,
    pictures: 0,
    chunks: 0,
    search_chunks: 0,
    rag_policy: null,
    error: null,
    preview: null,
    slide_layout: false,
    ...overrides,
  };
}
function extracted(overrides: Partial<Table>): Table {
  return {
    ref: "small",
    label: "小さい表",
    source_label: "1ページ",
    pages: [2],
    rows: [["a"]],
    columns: 1,
    header_rows: 0,
    merged: false,
    ...overrides,
  };
}

it("does not show an old copy fallback after selecting a different table cell", async () => {
  let reject!: (error: Error) => void;
  const pending = new Promise<void>((_resolve, fail) => {
    reject = fail;
  });
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: { writeText: vi.fn().mockReturnValue(pending) },
  });
  const table: Table = {
    ref: "#/tables/0",
    label: "表1",
    source_label: "文書全体",
    pages: [],
    rows: [["First"], ["Second"]],
    columns: 1,
    header_rows: 0,
    merged: false,
  };
  render(<TableCard table={table} visible onSource={() => {}} />);
  fireEvent.pointerDown(screen.getByRole("gridcell", { name: "First" }));
  fireEvent.click(screen.getByRole("button", { name: "選択をコピー" }));
  fireEvent.pointerDown(screen.getByRole("gridcell", { name: "Second" }));
  await act(async () => {
    reject(new Error("denied"));
    await pending.catch(() => {});
  });
  expect(screen.getByRole("textbox", { name: "選択セルの全文" })).toHaveValue(
    "Second",
  );
  expect(screen.queryByRole("textbox", { name: "コピーする内容" })).toBeNull();
});

it("filters, sorts, pins, and resets table columns", () => {
  render(<TableCard table={sample()} visible onSource={() => {}} />);
  const amount = column("c1");
  const name = column("c0");
  amount.comparator?.("1,200", "50", null, null, false);
  amount.comparator?.("", "50", null, null, false);
  amount.comparator?.("50", "", null, null, true);
  amount.comparator?.("", "", null, null, false);
  name.comparator?.("Beta", "Alpha", null, null, false);
  name.comparator?.("", "Alpha", null, null, true);
  expect(amount.cellRenderer?.({ value: null })).toMatchObject({
    props: { children: "" },
  });
  expect(amount.tooltipValueGetter?.({ value: "50" })).toBe("50");
  expect(amount.tooltipValueGetter?.({})).toBe("");
  expect(name.headerClass?.()).toBe("");
  const pass = grid.props?.doesExternalFilterPass;
  expect(pass?.({})).toBe(false);
  expect(
    pass?.({ data: { _sourceRow: 2, c0: "Alpha", c1: "1,200" } }),
  ).toBe(true);

  const search = screen.getByRole("searchbox", { name: "表1の全列を検索" });
  expect(screen.getByLabelText("適用中の条件")).toHaveAttribute("hidden");
  fireEvent.change(search, { target: { value: "   " } });
  fireEvent.click(screen.getByRole("button", { name: "名前の列メニュー" }));
  fireEvent.click(screen.getByRole("menuitem", { name: "並び・絞り込みを戻す" }));
  fireEvent.change(search, { target: { value: " Alpha " } });
  expect(screen.getByLabelText("適用中の条件")).not.toHaveAttribute("hidden");
  expect(pass?.({ data: { _sourceRow: 9, c0: "Alpha", c1: "50" } })).toBe(true);
  expect(pass?.({ data: { _sourceRow: 9, c0: "Beta", c1: "50" } })).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: "検索：Alpha ×" }));

  fireEvent.click(screen.getByRole("button", { name: "絞り込み" }));
  fireEvent.change(screen.getByLabelText("絞り込む列"), {
    target: { value: "c1" },
  });
  fireEvent.change(screen.getByLabelText("絞り込み条件"), {
    target: { value: "gte" },
  });
  fireEvent.click(screen.getByRole("button", { name: "適用" }));
  expect(screen.getByRole("alert")).toHaveTextContent("数値を入力してください。");
  fireEvent.change(screen.getByLabelText("絞り込み値"), {
    target: { value: "abc" },
  });
  fireEvent.keyDown(screen.getByLabelText("絞り込み値"), { key: "a" });
  fireEvent.keyDown(screen.getByLabelText("絞り込み値"), { key: "Enter" });
  expect(screen.getByRole("alert")).toHaveTextContent("数値を入力してください。");
  fireEvent.change(screen.getByLabelText("絞り込み値"), {
    target: { value: " 10 " },
  });
  fireEvent.click(screen.getByRole("button", { name: "適用" }));
  expect(screen.getByRole("button", { name: "金額：以上 10 ×" })).toBeInTheDocument();
  expect(pass?.({ data: { _sourceRow: 2, c0: "Alpha", c1: "1,200" } })).toBe(
    true,
  );
  expect(pass?.({ data: { _sourceRow: 3, c0: "Beta", c1: "5" } })).toBe(false);

  fireEvent.click(screen.getByRole("button", { name: "絞り込み" }));
  fireEvent.change(screen.getByLabelText("絞り込む列"), {
    target: { value: "c1" },
  });
  expect(screen.getByLabelText("絞り込み条件")).toHaveValue("gte");
  fireEvent.change(screen.getByLabelText("絞り込む列"), {
    target: { value: "c0" },
  });
  expect(screen.getByLabelText("絞り込み条件")).toHaveValue("contains");
  fireEvent.change(screen.getByLabelText("絞り込み値"), { target: { value: "" } });
  fireEvent.click(screen.getByRole("button", { name: "適用" }));
  fireEvent.click(screen.getByRole("button", { name: "金額の列メニュー" }));
  fireEvent.click(screen.getByRole("menuitem", { name: "この列を絞り込む" }));
  fireEvent.change(screen.getByLabelText("絞り込み条件"), {
    target: { value: "empty" },
  });
  expect(screen.getByLabelText("絞り込み値")).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "適用" }));
  fireEvent.click(screen.getByRole("button", { name: /金額：空欄/ }));
  fireEvent.click(screen.getByRole("button", { name: "絞り込み" }));
  fireEvent.change(screen.getByLabelText("絞り込み条件"), {
    target: { value: "lte" },
  });
  fireEvent.change(screen.getByLabelText("絞り込み値"), { target: { value: "50" } });
  fireEvent.click(screen.getByRole("button", { name: "解除" }));

  fireEvent.click(screen.getByRole("button", { name: "名前の列メニュー" }));
  fireEvent.click(screen.getByRole("menuitem", { name: "昇順に並べ替え" }));
  act(() => grid.props?.onSortChanged?.());
  expect(screen.getByRole("button", { name: "名前：昇順 ×" })).toBeInTheDocument();
  expect(name.headerClass?.()).toBe("table-column-active");
  fireEvent.click(screen.getByRole("button", { name: "金額の列メニュー" }));
  fireEvent.click(screen.getByRole("menuitem", { name: "並べ替えに追加（昇順）" }));
  fireEvent.click(screen.getByRole("button", { name: "金額の列メニュー" }));
  fireEvent.click(screen.getByRole("menuitem", { name: "降順に並べ替え" }));
  act(() => grid.props?.onSortChanged?.());
  fireEvent.click(screen.getByRole("button", { name: "金額：降順 ×" }));
  fireEvent.click(screen.getByRole("button", { name: "名前の列メニュー" }));
  fireEvent.click(screen.getByRole("menuitem", { name: "列を左側に固定" }));
  fireEvent.click(screen.getByRole("button", { name: "名前の列メニュー" }));
  fireEvent.click(screen.getByRole("menuitem", { name: "列の固定を解除" }));

  fireEvent.change(search, { target: { value: "Beta" } });
  fireEvent.click(screen.getByRole("button", { name: "名前の列メニュー" }));
  fireEvent.click(screen.getByRole("menuitem", { name: "並び・絞り込みを戻す" }));
  fireEvent.click(screen.getByRole("button", { name: "リセット" }));
});

it("copies a selection and drives pointer, keyboard, and column events", async () => {
  const onSource = vi.fn();
  const view = render(<TableCard table={sample()} visible onSource={onSource} />);
  const alpha = screen.getByRole("gridcell", { name: "Alpha" });
  const beta = screen.getByRole("gridcell", { name: "Beta" });
  const amount = screen.getByRole("gridcell", { name: "1,200" });
  clipboard(() => Promise.resolve());
  fireEvent.pointerDown(alpha);
  fireEvent.click(screen.getByRole("button", { name: "選択をコピー" }));
  expect(await screen.findByText("コピーしました。")).toBeInTheDocument();

  let resolveCopy!: () => void;
  const pending = new Promise<void>((resolve) => {
    resolveCopy = resolve;
  });
  clipboard(() => pending);
  fireEvent.click(screen.getByRole("button", { name: "選択をコピー" }));
  fireEvent.pointerDown(beta);
  await act(async () => resolveCopy());
  expect(screen.queryByText("コピーしました。")).toBeNull();

  clipboard(() => Promise.reject(new Error("denied")));
  fireEvent.pointerDown(alpha);
  fireEvent.keyDown(alpha, { key: "c", ctrlKey: true });
  const fallback = await screen.findByRole("textbox", { name: "コピーする内容" });
  fireEvent.focus(fallback);
  fireEvent.click(screen.getByRole("button", { name: "閉じる" }));
  fireEvent.keyDown(alpha, { key: "c", metaKey: true });
  expect(await screen.findByRole("textbox", { name: "コピーする内容" })).toHaveValue(
    "Alpha",
  );
  fireEvent.pointerDown(document.body);

  clipboard(() => Promise.resolve());
  fireEvent.pointerDown(alpha);
  fireEvent.pointerDown(beta, { shiftKey: true });
  fireEvent.contextMenu(beta, { clientX: 30, clientY: 40 });
  fireEvent.click(screen.getByRole("menuitem", { name: "選択範囲をコピー" }));
  fireEvent.contextMenu(alpha, { clientX: 12, clientY: 16 });
  fireEvent.click(screen.getByRole("menuitem", { name: "セルをコピー" }));
  fireEvent.contextMenu(amount, { clientX: 18, clientY: 22 });
  fireEvent.click(screen.getByRole("menuitem", { name: "行をコピー" }));
  fireEvent.contextMenu(alpha);
  fireEvent.click(screen.getByRole("menuitem", { name: "セルの全文を見る" }));
  expect(document.activeElement).toBe(
    screen.getByRole("textbox", { name: "選択セルの全文" }),
  );

  fireEvent.contextMenu(screen.getByRole("button", { name: "名前の列メニュー" }), {
    clientX: 8,
    clientY: 8,
  });
  fireEvent.pointerDown(document.body);
  fireEvent.contextMenu(view.container.querySelector('[col-id="_sourceRow"]')!);
  fireEvent.pointerDown(document.body);
  fireEvent.contextMenu(screen.getByTestId("blank-header"));
  fireEvent.pointerDown(document.body);
  const gridHost = screen.getByLabelText("表1のデータ");
  fireEvent.contextMenu(gridHost);
  fireEvent.pointerDown(gridHost);
  fireEvent.keyDown(gridHost, { key: "c", ctrlKey: true });

  fireEvent.pointerDown(alpha);
  fireEvent.keyDown(alpha, { key: "ContextMenu" });
  grid.api?.setFocusedCell(0, "c0");
  fireEvent.keyDown(alpha, { key: "ContextMenu" });
  fireEvent.pointerDown(document.body);
  fireEvent.keyDown(alpha, { key: "F10", shiftKey: true });
  fireEvent.pointerDown(document.body);
  fireEvent.keyDown(alpha, { key: "F10" });
  fireEvent.keyDown(alpha, { key: "ArrowRight", shiftKey: true });
  fireEvent.keyDown(alpha, { key: "ArrowLeft", shiftKey: true });
  fireEvent.keyDown(alpha, { key: "ArrowDown", shiftKey: true });
  fireEvent.keyDown(alpha, { key: "ArrowUp", shiftKey: true });
  fireEvent.keyDown(alpha, { key: "ArrowDown" });
  fireEvent.keyDown(alpha, { key: "a" });
  fireEvent.keyUp(alpha);
  fireEvent.keyDown(alpha, { key: "Escape" });
  fireEvent.keyDown(alpha, { key: "c", ctrlKey: true });

  fireEvent.pointerDown(alpha);
  const selected = column("c0").cellClassRules?.["table-cell-selected"];
  const other = column("c1").cellClassRules?.["table-cell-selected"];
  selected?.({ node: { rowIndex: null } });
  selected?.({ node: { rowIndex: 0 } });
  other?.({ node: { rowIndex: 0 } });
  selected?.({ node: { rowIndex: 1 } });
  fireEvent.pointerDown(amount);
  other?.({ node: { rowIndex: 0 } });
  selected?.({ node: { rowIndex: 0 } });
  fireEvent.pointerDown(beta);
  selected?.({ node: { rowIndex: 0 } });
  grid.columnIds = ["c0"];
  fireEvent.pointerDown(alpha);
  fireEvent.pointerDown(amount, { shiftKey: true });
  other?.({ node: { rowIndex: 0 } });
  grid.columnIds = ["c1"];
  selected?.({ node: { rowIndex: 0 } });
  grid.columnIds = null;

  fireEvent.pointerDown(alpha);
  grid.api?.setFocusedCell(0, "c0");
  act(() =>
    grid.props?.onCellFocused?.({
      rowIndex: 0,
      column: { getColId: () => "c0" },
    }),
  );
  grid.api?.setFocusedCell(0, "c0");
  fireEvent.pointerDown(alpha, { button: 2 });
  act(() =>
    grid.props?.onCellFocused?.({ rowIndex: 0, column: "c0" }),
  );
  act(() =>
    grid.props?.onCellFocused?.({
      rowIndex: 0,
      column: { getColId: () => "c0" },
    }),
  );
  act(() => grid.props?.onCellFocused?.({ rowIndex: 1, column: "c0" }));
  act(() => grid.props?.onCellFocused?.({ rowIndex: 0, column: null }));
  fireEvent.pointerDown(alpha, { shiftKey: true });
  grid.api?.setFocusedCell(1, "c1");
  act(() =>
    grid.props?.onCellFocused?.({
      rowIndex: 1,
      column: { getColId: () => "c1" },
    }),
  );
  fireEvent.pointerDown(beta, { button: 2 });
  fireEvent.keyUp(alpha);

  act(() => {
    grid.props?.onColumnMoved?.({ finished: false });
    grid.props?.onColumnMoved?.({ finished: true });
    grid.props?.onColumnResized?.({ finished: false });
    grid.props?.onColumnResized?.({ finished: true });
    grid.props?.onColumnPinned?.();
    grid.props?.onFilterChanged?.();
    grid.props?.onSortChanged?.();
    grid.props?.onModelUpdated?.({ api: { getDisplayedRowCount: () => 0 } });
    grid.props?.onModelUpdated?.({ api: { getDisplayedRowCount: () => 2 } });
  });

  fireEvent.click(screen.getByRole("button", { name: "表示設定" }));
  fireEvent.change(screen.getByLabelText("見出し行数"), { target: { value: "0" } });
  fireEvent.change(screen.getByLabelText("見出し行数"), { target: { value: "2" } });
  fireEvent.click(screen.getByRole("checkbox", { name: "元行番号を表示" }));
  fireEvent.click(screen.getByRole("button", { name: "列幅・列順・固定を戻す" }));
  fireEvent.click(screen.getByRole("button", { name: "表示設定" }));
  fireEvent.click(screen.getByRole("button", { name: "閉じる" }));
  fireEvent.click(screen.getByRole("button", { name: "出典を見る" }));
  fireEvent.click(screen.getByRole("button", { name: "原本プレビューを見る" }));
  expect(onSource).toHaveBeenCalledOnce();
  fireEvent.click(screen.getByRole("button", { name: "出典を見る" }));
  fireEvent.click(screen.getByRole("button", { name: "閉じる" }));

  fireEvent.click(screen.getByRole("button", { name: "名前 / Alphaの列メニュー" }));
  fireEvent.click(screen.getByRole("menuitem", { name: "列を左側に固定" }));
  fireEvent.click(screen.getByRole("button", { name: "広く表示" }));
  expect(screen.getByRole("dialog", { name: "表1を広く表示" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "文書に戻る" }));
  fireEvent.click(screen.getByRole("button", { name: "広く表示" }));
  fireEvent(
    screen.getByRole("dialog", { name: "表1を広く表示" }),
    new Event("cancel", { bubbles: true, cancelable: true }),
  );
  expect(screen.queryByRole("dialog", { name: "表1を広く表示" })).toBeNull();
  expect(screen.getByText(/結合セルの値は左上に表示します/)).toBeInTheDocument();
});

it("closes menus when the card hides and ignores a destroyed grid", () => {
  const view = render(<TableCard table={sample()} visible onSource={() => {}} />);
  fireEvent.click(screen.getByRole("button", { name: "表示設定" }));
  expect(screen.getByLabelText("見出し行数")).toBeInTheDocument();
  view.rerender(<TableCard table={sample()} visible={false} onSource={() => {}} />);
  expect(screen.queryByLabelText("見出し行数")).toBeNull();
  view.rerender(<TableCard table={sample()} visible onSource={() => {}} />);
  fireEvent.click(screen.getByRole("button", { name: "広く表示" }));
  view.rerender(<TableCard table={sample()} visible={false} onSource={() => {}} />);
  expect(screen.queryByRole("dialog", { name: "表1を広く表示" })).toBeNull();

  view.rerender(<TableCard table={sample()} visible onSource={() => {}} />);
  grid.destroyed = true;
  fireEvent.change(screen.getByRole("searchbox", { name: "表1の全列を検索" }), {
    target: { value: "A" },
  });
  act(() => grid.props?.onColumnPinned?.());
  const alpha = screen.getByRole("gridcell", { name: "Alpha" });
  fireEvent.pointerDown(alpha);
  fireEvent.keyDown(alpha, { key: "Escape" });
  act(() => grid.props?.onGridPreDestroyed?.());
  column("c0").cellClassRules?.["table-cell-selected"]?.({
    node: { rowIndex: 0 },
  });
  fireEvent.click(screen.getByRole("button", { name: "名前の列メニュー" }));
  fireEvent.click(screen.getByRole("menuitem", { name: "昇順に並べ替え" }));
});

it("opens the filter popup for a table with no columns", () => {
  render(
    <TableCard
      table={sample({
        columns: 0,
        rows: [],
        header_rows: 0,
        merged: false,
        label: "空",
      })}
      visible
      onSource={() => {}}
    />,
  );
  expect(screen.getByRole("searchbox", { name: "空の全列を検索" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "絞り込み" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "表示設定" }));
  expect(screen.getByLabelText("見出し行数")).toBeInTheDocument();
});

it("browses extracted tables and reports failures", async () => {
  const tables = [
    extracted({ ref: "small", rows: [["a"]], columns: 1 }),
    extracted({
      ref: "big",
      label: "大きい表",
      source_label: "2ページ",
      pages: [5],
      rows: [
        ["a", "b"],
        ["c", "d"],
        ["e", "f"],
      ],
      columns: 2,
    }),
    extracted({
      ref: "same",
      label: "同じ表",
      rows: [["a"]],
      columns: 1,
      pages: [],
    }),
  ];
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json(tables)),
  );
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const onSource = vi.fn();
  const view = render(
    <QueryClientProvider client={client}>
      <Tables job={job()} visible onSource={onSource} />
    </QueryClientProvider>,
  );
  expect(await screen.findByRole("option", { name: /大きい表/ })).toBeInTheDocument();
  expect(screen.getByLabelText("抽出表")).toHaveValue("big");
  fireEvent.change(screen.getByLabelText("抽出表"), { target: { value: "small" } });
  fireEvent.change(screen.getByLabelText("抽出表"), { target: { value: "big" } });
  fireEvent.click(screen.getByRole("button", { name: "出典を見る" }));
  fireEvent.click(screen.getByRole("button", { name: "原本プレビューを見る" }));
  expect(onSource).toHaveBeenCalledWith(5);

  view.rerender(
    <QueryClientProvider client={client}>
      <Tables
        job={job({ filename: "notes.pdf", original_filename: "notes.docx" })}
        visible
        onSource={onSource}
      />
    </QueryClientProvider>,
  );
  fireEvent.click(screen.getByRole("button", { name: "出典を見る" }));
  fireEvent.click(screen.getByRole("button", { name: "原本プレビューを見る" }));
  expect(onSource).toHaveBeenLastCalledWith(1);

  view.rerender(
    <QueryClientProvider client={client}>
      <Tables job={job()} visible onSource={onSource} />
    </QueryClientProvider>,
  );
  fireEvent.change(screen.getByLabelText("抽出表"), { target: { value: "same" } });
  onSource.mockClear();
  fireEvent.click(screen.getByRole("button", { name: "出典を見る" }));
  fireEvent.click(screen.getByRole("button", { name: "原本プレビューを見る" }));
  expect(onSource).not.toHaveBeenCalled();
  client.clear();
});

it("shows loading, empty, and error states for the table list", async () => {
  const fetchMock = vi.fn(() => new Promise(() => undefined));
  vi.stubGlobal("fetch", fetchMock);
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const view = render(
    <QueryClientProvider client={client}>
      <Tables job={job({ state: "queued" })} visible onSource={() => {}} />
    </QueryClientProvider>,
  );
  expect(screen.getByText("抽出済みの表を読み込んでいます…")).toBeInTheDocument();
  expect(fetchMock).not.toHaveBeenCalled();

  view.rerender(
    <QueryClientProvider client={client}>
      <Tables job={job()} visible={false} onSource={() => {}} />
    </QueryClientProvider>,
  );
  expect(document.getElementById("tables")).toHaveAttribute("hidden");
  expect(fetchMock).not.toHaveBeenCalled();

  fetchMock.mockImplementation(
    () => new Promise(() => undefined) as Promise<Response>,
  );
  view.rerender(
    <QueryClientProvider client={client}>
      <Tables job={job({ id: "pending" })} visible onSource={() => {}} />
    </QueryClientProvider>,
  );
  expect(screen.getByText("抽出済みの表を読み込んでいます…")).toBeInTheDocument();

  fetchMock.mockResolvedValue(Response.json([]));
  view.rerender(
    <QueryClientProvider client={client}>
      <Tables job={job({ id: "empty" })} visible onSource={() => {}} />
    </QueryClientProvider>,
  );
  expect(
    await screen.findByText("この文書から抽出された表はありません。"),
  ).toBeInTheDocument();

  fetchMock.mockResolvedValue(
    Response.json({ detail: "表を読めません" }, { status: 500 }),
  );
  view.rerender(
    <QueryClientProvider client={client}>
      <Tables job={job({ id: "broken" })} visible onSource={() => {}} />
    </QueryClientProvider>,
  );
  expect(await screen.findByText("表を読めません")).toBeInTheDocument();
  client.clear();
});
