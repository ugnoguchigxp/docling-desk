import { useViewerSource } from "../viewer/data-source";
import * as contracts from "../lib/contracts";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type MouseEvent,
} from "react";
import { useQuery } from "@tanstack/react-query";
import { AgGridReact } from "ag-grid-react";
import {
  AllCommunityModule,
  ModuleRegistry,
  type ColDef,
  type ColumnState,
  type GridApi,
  type IRowNode,
  type IHeaderParams,
} from "ag-grid-community";
import {
  Button,
  CheckboxField,
  Dialog,
  Popup,
  SelectField,
  StatusMessage,
  TextField,
  Toolbar,
  type PopupPosition,
} from "../components/ui";
import { request } from "../lib/api";
import { isDocument } from "../lib/formats";
import { useAsyncScope } from "../lib/async-scope";
import { isDone, type Job, type Table } from "../lib/types";
import {
  columns,
  columnName,
  dataRows,
  matches,
  operations,
  selection,
  sortState,
  type Condition,
  type Point,
  type Row,
  type Selection,
} from "./table-model";
import { gridIcon, TableIcon } from "./TableIcons";
ModuleRegistry.registerModules([AllCommunityModule]);
const gridIcons = Object.fromEntries(
  Object.entries({
    sortAscending: "asc",
    sortDescending: "desc",
    sortUnSort: "sort",
    sortAbsoluteAscending: "asc",
    sortAbsoluteDescending: "desc",
    filter: "filter",
    filterActive: "filter",
    menu: "down",
    menuAlt: "down",
    columnMovePin: "pin",
    columnMoveHide: "hide",
    columnMoveMove: "move",
    columnMoveLeft: "left",
    columnMoveRight: "right",
    dropNotAllowed: "blocked",
  }).map(([key, name]) => [key, () => gridIcon(name)]),
);
const defaultColumn: ColDef<Row> = {
  editable: false,
  sortable: true,
  resizable: true,
  suppressMovable: false,
  valueFormatter: (p) => String(p.value ?? ""),
};
const localeText = {
  noRowsToShow: "該当する行がありません。",
  loadingOoo: "読み込み中…",
};
const rowId = (p: { data: Row }) => String(p.data._sourceRow);
const hasExternalFilter = () => true;
interface Menu {
  mode: "menu" | "filter" | "settings" | "source" | "copy";
  position: PopupPosition;
  field?: string;
  row?: Row;
  text?: string;
}
export function TableCard({
  table,
  visible,
  onSource,
}: {
  table: Table;
  visible: boolean;
  onSource: () => void;
}) {
  const { start: startCopy, invalidate } = useAsyncScope(visible);
  const [headers, setHeaders] = useState(table.header_rows),
    [search, setSearch] = useState(""),
    [filters, setFilters] = useState<Record<string, Condition>>({}),
    [showSource, setShowSource] = useState(false),
    [expanded, setExpanded] = useState(false),
    [menu, setMenu] = useState<Menu | null>(null),
    [selected, setSelected] = useState<Selection | null>(null),
    [notice, setNotice] = useState(""),
    [count, setCount] = useState(table.rows.length - table.header_rows),
    [state, setState] = useState<ColumnState[]>([]),
    [filterField, setFilterField] = useState("c0"),
    [operator, setOperator] = useState("contains"),
    [value, setValue] = useState(""),
    [error, setError] = useState("");
  const api = useRef<GridApi<Row> | null>(null),
    host = useRef<HTMLDivElement>(null),
    inspector = useRef<HTMLTextAreaElement>(null),
    copyButton = useRef<HTMLButtonElement>(null),
    anchor = useRef<Point | null>(null),
    end = useRef<Point | null>(null),
    extending = useRef(false),
    context = useRef<Point | null>(null),
    searchRef = useRef(search),
    filtersRef = useRef(filters),
    saved = useRef<ColumnState[]>(state);
  searchRef.current = search;
  filtersRef.current = filters;
  saved.current = state;
  const rows = useMemo(() => dataRows(table, headers), [table, headers]),
    defs = useMemo(() => columns(table, headers), [table, headers]);
  const remember = useCallback(() => {
    if (api.current && !api.current.isDestroyed())
      setState(api.current.getColumnState());
  }, []);
  const close = useCallback(() => setMenu(null), []);
  useEffect(() => {
    if (!visible) {
      setMenu(null);
      setExpanded(false);
    }
  }, [visible]);
  const inRange = useCallback(
    (row: number | null | undefined, field: string) => {
      const a = anchor.current,
        b = end.current,
        grid = api.current;
      if (row == null || !a || !b || !grid) return false;
      const keys = grid
          .getAllDisplayedColumns()
          .map((c) => c.getColId())
          .filter((c) => c !== "_sourceRow"),
        x = keys.indexOf(a.col),
        y = keys.indexOf(b.col),
        i = keys.indexOf(field);
      return (
        x >= 0 &&
        y >= 0 &&
        i >= Math.min(x, y) &&
        i <= Math.max(x, y) &&
        row >= Math.min(a.row, b.row) &&
        row <= Math.max(a.row, b.row)
      );
    },
    [],
  );
  const notify = useCallback(() => {
    const grid = api.current;
    if (!grid || grid.isDestroyed()) return;
    invalidate();
    setNotice("");
    grid.refreshCells({ force: true });
    setSelected(selection(grid, anchor.current, end.current));
  }, [invalidate]);
  const clear = useCallback(() => {
    anchor.current = end.current = null;
    notify();
  }, [notify]);
  function focus(row: number | null | undefined, col: string, extend = false) {
    if (
      row == null ||
      col === "_sourceRow" ||
      !api.current?.getDisplayedRowAtIndex(row)
    )
      return;
    end.current = { row, col };
    if (!extend || !anchor.current) anchor.current = { row, col };
    notify();
  }
  function openMenu(
    mode: Menu["mode"],
    button: HTMLElement,
    field?: string,
    point?: { x: number; y: number },
    row?: Row,
  ) {
    setError("");
    setMenu({ mode, position: { anchor: button, ...point }, field, row });
    if (mode === "filter") {
      const f = field || defs[0]?.field || "c0",
        condition = filters[f];
      setFilterField(f);
      setOperator(condition?.op || "contains");
      setValue(condition?.value || "");
    }
  }
  function Header(params: IHeaderParams<Row>) {
    const field = params.column.getColId();
    return (
      <div className="table-column-title">
        <small className="table-column-letter">
          {columnName(Number(field.slice(1)))}
        </small>
        <span>{params.displayName}</span>
        <Button
          className="table-column-menu"
          aria-label={`${params.displayName}の列メニュー`}
          aria-haspopup="menu"
          onClick={(e) => {
            e.stopPropagation();
            openMenu("menu", e.currentTarget, field);
          }}
        >
          <TableIcon name="down" />
        </Button>
      </div>
    );
  }
  // Keep the header component identity stable while state changes outside AG Grid.
  const header = useRef(Header);
  header.current = Header;
  const StableHeader = useMemo(
    () =>
      function StableHeader(p: IHeaderParams<Row>) {
        return header.current(p);
      },
    [],
  );
  const externalFilter = useCallback(
    (node: IRowNode<Row>) =>
      !!node.data &&
      (!searchRef.current.trim() ||
        Object.entries(node.data).some(
          ([k, v]) =>
            k !== "_sourceRow" &&
            String(v)
              .toLocaleLowerCase("ja")
              .includes(searchRef.current.trim().toLocaleLowerCase("ja")),
        )) &&
      Object.entries(filtersRef.current).every(([f, c]) =>
        matches(node.data![f], c),
      ),
    [],
  );
  const columnDefs = useMemo<ColDef<Row>[]>(
    () => [
      {
        field: "_sourceRow",
        headerName: "元行",
        width: 64,
        minWidth: 64,
        hide: !showSource,
        pinned: "left",
        sortable: false,
        suppressMovable: true,
        suppressNavigable: true,
        lockPinned: true,
        cellStyle: { textAlign: "right" },
      },
      ...defs.map((d) => ({
        field: d.field,
        headerName: d.title,
        headerTooltip: d.title,
        minWidth: d.minWidth,
        flex: 1,
        headerComponentParams: { innerHeaderComponent: StableHeader },
        headerClass: () =>
          sortState(saved.current).some((s) => s.colId === d.field)
            ? "table-column-active"
            : "",
        comparator: (
          a: unknown,
          b: unknown,
          _a: unknown,
          _b: unknown,
          descending: boolean,
        ) => {
          const x = String(a ?? ""),
            y = String(b ?? ""),
            emptyX = !x.trim(),
            emptyY = !y.trim();
          if (emptyX !== emptyY)
            return (emptyX ? 1 : -1) * (descending ? -1 : 1);
          return d.isNumber
            ? Number(x.replaceAll(",", "")) - Number(y.replaceAll(",", ""))
            : x.localeCompare(y, "ja");
        },
        cellStyle: { textAlign: d.isNumber ? "right" : "left" } as const,
        cellRenderer: (p: { value: unknown }) => (
          <span>{String(p.value ?? "")}</span>
        ),
        tooltipValueGetter: (p: { value?: unknown }) => String(p.value ?? ""),
        cellClassRules: {
          "table-cell-selected": (p: { node: { rowIndex: number | null } }) =>
            inRange(p.node.rowIndex, d.field),
        },
      })),
    ],
    [defs, showSource, StableHeader, inRange],
  ); // Selection reads current refs, not a captured range.
  useEffect(() => {
    if (api.current && !api.current.isDestroyed()) {
      api.current.onFilterChanged();
      clear();
    }
  }, [search, filters, clear]);
  useEffect(() => {
    if (api.current && !api.current.isDestroyed())
      api.current.setColumnsVisible(["_sourceRow"], showSource);
  }, [showSource]);
  function sort(field: string, dir: "asc" | "desc", add = false) {
    const grid = api.current;
    if (!grid) return;
    grid.applyColumnState({
      state: [
        ...(add
          ? sortState(grid.getColumnState()).filter((s) => s.colId !== field)
          : []),
        {
          colId: field,
          sort: dir,
          sortIndex: add ? sortState(grid.getColumnState()).length : 0,
        },
      ],
      defaultState: { sort: null },
    });
    setMenu(null);
  }
  function reset() {
    setSearch("");
    setFilters({});
    api.current?.applyColumnState({ defaultState: { sort: null } });
    setNotice("");
  }
  async function copy(text: string) {
    const isCurrent = startCopy();
    setMenu(null);
    try {
      await navigator.clipboard.writeText(text);
      if (isCurrent()) setNotice("コピーしました。");
    } catch {
      if (isCurrent() && copyButton.current)
        setMenu({
          mode: "copy",
          position: { anchor: copyButton.current },
          text,
        });
    }
  }
  function applyFilter() {
    const text = value.trim();
    if (
      ["gte", "lte"].includes(operator) &&
      (!text || !Number.isFinite(Number(text)))
    ) {
      setError("数値を入力してください。");
      return;
    }
    setFilters((old) => {
      const next = { ...old };
      if (!text && !["empty", "notempty"].includes(operator))
        delete next[filterField];
      else
        next[filterField] = {
          op: operator,
          value: ["empty", "notempty"].includes(operator) ? "" : text,
        };
      return next;
    });
    setMenu(null);
  }
  function contextMenu(e: MouseEvent) {
    const grid = api.current;
    if (!grid || !(e.target instanceof Element)) return;
    const cell = e.target.closest<HTMLElement>(".ag-cell"),
      head = e.target.closest<HTMLElement>(".ag-header-cell");
    if (cell) {
      e.preventDefault();
      const row = Number(cell.closest(".ag-row")?.getAttribute("row-index")),
        field = cell.getAttribute("col-id")!;
      if (!inRange(row, field)) focus(row, field);
      openMenu(
        "menu",
        cell,
        field,
        { x: e.clientX, y: e.clientY },
        grid.getDisplayedRowAtIndex(row)?.data,
      );
    } else if (head) {
      e.preventDefault();
      openMenu(
        "menu",
        head.querySelector("button") || head,
        head.getAttribute("col-id")!,
        { x: e.clientX, y: e.clientY },
      );
    }
  }
  const sorts = sortState(state),
    title = (field: string) =>
      defs.find((d) => d.field === field)?.title || field;
  const card = (
    <section className="table-card" data-ref={table.ref}>
      <div className="table-card-head">
        <div className="table-identity">
          <strong className="table-title">{table.label}</strong>
          <span className="table-dimensions">
            {`${rows.length}行 × ${table.columns}列`}
          </span>
        </div>
        <div className="table-head-actions">
          <Button
            className="table-expand"
            aria-expanded={expanded}
            onClick={() => {
              remember();
              setMenu(null);
              setExpanded((v) => !v);
            }}
          >
            <TableIcon name={expanded ? "close" : "expand"} />
            {expanded ? "文書に戻る" : "広く表示"}
          </Button>
        </div>
      </div>
      <Toolbar className="table-controls">
        <label className="table-search">
          <TableIcon name="search" className="table-search-icon" />
          <TextField
            type="search"
            placeholder="表内を検索"
            aria-label={`${table.label}の全列を検索`}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            disabled={!table.columns}
          />
        </label>
        <Button
          onClick={(e) => openMenu("filter", e.currentTarget)}
          disabled={!table.columns}
        >
          絞り込み
        </Button>
        <Button onClick={(e) => openMenu("settings", e.currentTarget)}>
          表示設定
        </Button>
      </Toolbar>
      <div
        className="table-conditions"
        aria-label="適用中の条件"
        hidden={!search.trim() && !sorts.length && !Object.keys(filters).length}
      >
        {search.trim() && (
          <Button className="table-chip" onClick={() => setSearch("")}>
            検索：{search.trim()} ×
          </Button>
        )}
        {sorts.map((s) => (
          <Button
            key={s.colId}
            className="table-chip"
            onClick={() =>
              api.current?.applyColumnState({
                state: [{ colId: s.colId, sort: null }],
              })
            }
          >
            {title(s.colId)}：{s.sort === "asc" ? "昇順" : "降順"} ×
          </Button>
        ))}
        {Object.entries(filters).map(([field, c]) => (
          <Button
            key={field}
            className="table-chip"
            onClick={() =>
              setFilters((old) => {
                const n = { ...old };
                delete n[field];
                return n;
              })
            }
          >
            {title(field)}：{operations[c.op]} {c.value} ×
          </Button>
        ))}
        <Button className="table-text-button" onClick={reset}>
          リセット
        </Button>
      </div>
      <div
        className="table-grid ag-theme-quartz"
        aria-label={`${table.label}のデータ`}
        ref={host}
        style={{
          maxHeight: expanded ? "60vh" : 360,
          height: Math.max(1, count) * 38 + 64,
        }}
        onContextMenu={contextMenu}
        onPointerDownCapture={(e) => {
          if (!(e.target instanceof Element)) return;
          const cell = e.target.closest(".ag-cell");
          if (!cell) return;
          extending.current = e.shiftKey;
          const row = Number(
              cell.closest(".ag-row")?.getAttribute("row-index"),
            ),
            col = cell.getAttribute("col-id")!;
          context.current = null;
          if (e.button === 2 && inRange(row, col)) {
            context.current = { row, col };
            return;
          }
          focus(row, col, e.shiftKey);
        }}
        onKeyDownCapture={(e) => {
          if (!(e.target instanceof Element) || !e.target.closest(".ag-cell"))
            return;
          const grid = api.current;
          if (!grid) return;
          context.current = null;
          extending.current = e.shiftKey;
          if (
            (e.ctrlKey || e.metaKey) &&
            e.key.toLowerCase() === "c" &&
            selected
          ) {
            e.preventDefault();
            e.stopPropagation();
            void copy(selected.text);
          }
          if (
            e.shiftKey &&
            ["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(
              e.key,
            ) &&
            end.current
          ) {
            e.preventDefault();
            e.stopPropagation();
            const keys = grid
                .getAllDisplayedColumns()
                .map((c) => c.getColId())
                .filter((c) => c !== "_sourceRow"),
              row = Math.max(
                0,
                Math.min(
                  grid.getDisplayedRowCount() - 1,
                  end.current.row +
                    (e.key === "ArrowDown" ? 1 : e.key === "ArrowUp" ? -1 : 0),
                ),
              ),
              col =
                keys[
                  Math.max(
                    0,
                    Math.min(
                      keys.length - 1,
                      keys.indexOf(end.current.col) +
                        (e.key === "ArrowRight"
                          ? 1
                          : e.key === "ArrowLeft"
                            ? -1
                            : 0),
                    ),
                  )
                ];
            focus(row, col, true);
            grid.ensureIndexVisible(row);
            grid.ensureColumnVisible(col);
            grid.setFocusedCell(row, col);
          }
          if (e.key === "Escape") clear();
          if (e.key === "ContextMenu" || (e.shiftKey && e.key === "F10")) {
            e.preventDefault();
            e.stopPropagation();
            const p = grid.getFocusedCell();
            if (p)
              openMenu(
                "menu",
                e.target as HTMLElement,
                p.column.getColId(),
                undefined,
                grid.getDisplayedRowAtIndex(p.rowIndex)?.data,
              );
          }
        }}
        onKeyUp={() => {
          extending.current = false;
        }}
      >
        <AgGridReact<Row>
          theme="legacy"
          localeText={localeText}
          rowData={rows}
          columnDefs={columnDefs}
          icons={gridIcons}
          rowBuffer={20}
          debounceVerticalScrollbar
          suppressColumnVirtualisation={false}
          rowModelType="clientSide"
          animateRows={false}
          suppressScrollOnNewData
          defaultColDef={defaultColumn}
          rowHeight={38}
          headerHeight={46}
          tooltipShowDelay={500}
          getRowId={rowId}
          isExternalFilterPresent={hasExternalFilter}
          doesExternalFilterPass={externalFilter}
          onGridReady={(e) => {
            api.current = e.api;
            if (saved.current.length)
              e.api.applyColumnState({
                state: saved.current,
                applyOrder: true,
              });
            e.api.onFilterChanged();
          }}
          onGridPreDestroyed={() => {
            api.current = null;
          }}
          onModelUpdated={(e) => setCount(e.api.getDisplayedRowCount())}
          onSortChanged={() => {
            clear();
            remember();
            api.current?.refreshHeader();
          }}
          onFilterChanged={clear}
          onColumnMoved={(e) => {
            if (e.finished) {
              clear();
              remember();
            }
          }}
          onColumnResized={(e) => {
            if (e.finished) remember();
          }}
          onColumnPinned={remember}
          onCellFocused={(e) => {
            const grid = api.current,
              col =
                typeof e.column === "string" ? e.column : e.column?.getColId();
            if (!grid || !col) return;
            const p = grid.getFocusedCell();
            if (p?.rowIndex !== e.rowIndex || p.column.getColId() !== col)
              return;
            if (
              context.current?.row === e.rowIndex &&
              context.current.col === col
            ) {
              context.current = null;
              return;
            }
            if (end.current?.row === e.rowIndex && end.current.col === col)
              return;
            focus(e.rowIndex, col, extending.current);
          }}
        />
      </div>
      <div className="table-cell-inspector">
        <span className="table-cell-address">
          {selected
            ? `${columnName(Number(selected.field.slice(1)))}${selected.sourceRow}${selected.rows > 1 || selected.columns > 1 ? ` · ${selected.rows}行 × ${selected.columns}列` : ""}`
            : "セルを選択"}
        </span>
        <textarea
          readOnly
          rows={2}
          ref={inspector}
          placeholder="セルを選ぶと全文を確認できます"
          aria-label="選択セルの全文"
          value={selected?.value || ""}
        />
        <Button
          ref={copyButton}
          disabled={!selected}
          onClick={() => selected && void copy(selected.text)}
        >
          選択をコピー
        </Button>
      </div>
      <div className="table-native" hidden />
      <div className="table-card-footer">
        <span className="table-status" role="status">
          {`${count} / ${rows.length}行を表示`}
        </span>
        <Button
          className="table-text-button"
          onClick={(e) => openMenu("source", e.currentTarget)}
        >
          出典を見る
          <TableIcon name="right" />
        </Button>
      </div>
      <StatusMessage className="table-notice" hidden={!notice}>
        {notice}
      </StatusMessage>
      {table.merged && (
        <p className="table-merged-note">
          結合セルの値は左上に表示します。結合状態は原形で確認できます。
        </p>
      )}
    </section>
  );
  const menuItem = (label: string, action: () => void) => (
    <Button role="menuitem" onClick={action}>
      {label}
    </Button>
  );
  const popup = (
    <Popup
      position={menu?.position || null}
      onClose={close}
      menu={menu?.mode === "menu"}
    >
      {menu?.mode === "menu" && (
        <>
          <strong className="table-popup-title">
            {title(menu.field || "")}
          </strong>
          {menu.row && (
            <>
              {selected &&
                (selected.rows > 1 || selected.columns > 1) &&
                menuItem("選択範囲をコピー", () => void copy(selected.text))}
              {menuItem("セルの全文を見る", () => {
                setMenu(null);
                inspector.current?.focus();
              })}
              {menuItem(
                "セルをコピー",
                () => void copy(String(menu.row?.[menu.field || ""] ?? "")),
              )}
              {menuItem(
                "行をコピー",
                () =>
                  void copy(
                    api.current
                      ?.getAllDisplayedColumns()
                      .map((c) => menu.row?.[c.getColId()] ?? "")
                      .join("\t") || "",
                  ),
              )}
              <p className="table-popup-hint">
                {`元の行番号：${menu.row._sourceRow}`}
              </p>
            </>
          )}
          {menu.field !== "_sourceRow" && (
            <>
              {menuItem("昇順に並べ替え", () => sort(menu.field!, "asc"))}
              {menuItem("降順に並べ替え", () => sort(menu.field!, "desc"))}
              {sorts.length > 0 &&
                !sorts.some((s) => s.colId === menu.field) &&
                menuItem("並べ替えに追加（昇順）", () =>
                  sort(menu.field!, "asc", true),
                )}
              {menuItem("この列を絞り込む", () =>
                openMenu("filter", menu.position.anchor, menu.field),
              )}
              {menuItem(
                state.find((s) => s.colId === menu.field)?.pinned
                  ? "列の固定を解除"
                  : "列を左側に固定",
                () => {
                  api.current?.applyColumnState({
                    state: [
                      {
                        colId: menu.field!,
                        pinned: state.find((s) => s.colId === menu.field)
                          ?.pinned
                          ? null
                          : "left",
                      },
                    ],
                  });
                  remember();
                  setMenu(null);
                },
              )}
            </>
          )}
          {(sorts.length > 0 || Object.keys(filters).length > 0 || search) &&
            menuItem("並び・絞り込みを戻す", () => {
              reset();
              setMenu(null);
            })}
        </>
      )}
      {menu?.mode === "filter" && (
        <>
          <strong className="table-popup-title">列を絞り込む</strong>
          <SelectField
            aria-label="絞り込む列"
            value={filterField}
            onChange={(e) => {
              const f = e.target.value,
                c = filters[f];
              setFilterField(f);
              setOperator(c?.op || "contains");
              setValue(c?.value || "");
            }}
          >
            {defs.map((d) => (
              <option key={d.field} value={d.field}>
                {d.title}
              </option>
            ))}
          </SelectField>
          <SelectField
            aria-label="絞り込み条件"
            value={operator}
            onChange={(e) => setOperator(e.target.value)}
          >
            {Object.entries(operations)
              .filter(
                ([key]) =>
                  !["gte", "lte"].includes(key) ||
                  defs.find((d) => d.field === filterField)?.isNumber,
              )
              .map(([k, v]) => (
                <option key={k} value={k}>
                  {v}
                </option>
              ))}
          </SelectField>
          <TextField
            type="text"
            aria-label="絞り込み値"
            disabled={["empty", "notempty"].includes(operator)}
            placeholder={
              ["gte", "lte"].includes(operator) ? "数値を入力" : "文字を入力"
            }
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") applyFilter();
            }}
          />
          <StatusMessage className="table-popup-error" role="alert">
            {error}
          </StatusMessage>
          <Toolbar className="table-popup-actions">
            <Button
              onClick={() => {
                setFilters((old) => {
                  const next = { ...old };
                  delete next[filterField];
                  return next;
                });
                setMenu(null);
              }}
            >
              解除
            </Button>
            <Button className="table-primary" onClick={applyFilter}>
              適用
            </Button>
          </Toolbar>
        </>
      )}
      {menu?.mode === "settings" && (
        <>
          <strong className="table-popup-title">表示設定</strong>
          <label className="table-setting">
            見出し行数
            <SelectField
              aria-label="見出し行数"
              value={headers}
              onChange={(e) => {
                remember();
                setHeaders(Number(e.target.value));
                clear();
              }}
            >
              {Array.from({ length: table.rows.length + 1 }, (_, n) => (
                <option key={n} value={n}>
                  {n ? `${n}行` : "なし"}
                </option>
              ))}
            </SelectField>
          </label>
          <label className="table-setting">
            <CheckboxField
              checked={showSource}
              onChange={(e) => setShowSource(e.target.checked)}
            />
            元行番号を表示
          </label>
          <p className="table-popup-hint">
            列の境界をドラッグすると幅を変更できます。列見出しのドラッグで順序を変えられます。
          </p>
          <Button
            onClick={() => {
              api.current?.resetColumnState();
              setState(api.current?.getColumnState() || []);
              setShowSource(false);
              setMenu(null);
            }}
          >
            列幅・列順・固定を戻す
          </Button>
          <Button onClick={close}>閉じる</Button>
        </>
      )}
      {menu?.mode === "source" && (
        <>
          <strong className="table-popup-title">表の出典</strong>
          <p>{table.source_label}</p>
          <p className="table-popup-hint">{`${table.label} · ${table.ref}`}</p>
          <p className="table-popup-hint">
            元行番号は抽出時の表の行番号です。「表示設定」で表示できます。
          </p>
          <Button
            onClick={() => {
              setMenu(null);
              onSource();
            }}
          >
            原本プレビューを見る
          </Button>
          <Button onClick={close}>閉じる</Button>
        </>
      )}
      {menu?.mode === "copy" && (
        <>
          <strong className="table-popup-title">選択した内容をコピー</strong>
          <textarea
            readOnly
            aria-label="コピーする内容"
            value={menu.text}
            onFocus={(e) => e.target.select()}
          />
          <p className="table-popup-hint">
            ⌘C または Ctrl+C でコピーしてください。
          </p>
          <Button onClick={close}>閉じる</Button>
        </>
      )}
    </Popup>
  );
  return (
    <>
      {!expanded && card}
      <Dialog
        className="table-focus"
        aria-label={`${table.label}を広く表示`}
        open={expanded}
        onClose={() => {
          remember();
          setMenu(null);
          setExpanded(false);
        }}
      >
        {expanded && card}
      </Dialog>
      {popup}
    </>
  );
}
export function Tables({
  job,
  visible,
  onSource,
}: {
  job: Job;
  visible: boolean;
  onSource: (page: number) => void;
}) {
  const source = useViewerSource();
  const q = useQuery({
    queryKey: ["tables", job.id],
    queryFn: ({ signal }) =>
      request<Table[]>(
        source.url(`/api/jobs/${job.id}/tables`),
        { signal },
        contracts.tables,
      ),
    enabled: visible && isDone(job),
  });
  const [chosen, setChosen] = useState<string | null>(null),
    [visited, setVisited] = useState(new Set<string>());
  const table =
    q.data?.find((t) => t.ref === chosen) ||
    q.data?.reduce(
      (best, t) =>
        t.rows.length * t.columns > best.rows.length * best.columns ? t : best,
      q.data[0],
    );
  useEffect(() => {
    if (table)
      setVisited((old) =>
        old.has(table.ref) ? old : new Set([...old, table.ref]),
      );
  }, [table]);
  return (
    <div
      id="tables"
      role="tabpanel"
      aria-labelledby="tab-tables"
      hidden={!visible}
    >
      <div className="table-browser-bar">
        <label>
          抽出表
          <SelectField
            id="tablePicker"
            aria-label="抽出表"
            value={table?.ref || ""}
            onChange={(e) => setChosen(e.target.value)}
          >
            {q.data?.map((t) => (
              <option key={t.ref} value={t.ref}>
                {`${t.source_label} / ${t.label}（${t.rows.length}行 × ${t.columns}列）`}
              </option>
            ))}
          </SelectField>
        </label>
      </div>
      <StatusMessage id="tableStatus">
        {q.error?.message ||
          (q.isPending
            ? "抽出済みの表を読み込んでいます…"
            : q.data?.length
              ? ""
              : "この文書から抽出された表はありません。")}
      </StatusMessage>
      <div id="tableGrid">
        {q.data
          ?.filter((t) => visited.has(t.ref))
          .map((t) => (
            <div key={t.ref} hidden={t.ref !== table?.ref}>
              <TableCard
                table={t}
                visible={visible && t.ref === table?.ref}
                onSource={() => {
                  if (isDocument(job.original_filename || job.filename))
                    onSource(1);
                  else if (t.pages.length) onSource(t.pages[0]);
                }}
              />
            </div>
          ))}
      </div>
      <p className="table-help">
        列見出しで並べ替え。列メニュー・右クリックで絞り込みや固定、矢印キーでセル移動、Shift＋クリック／矢印キーで範囲選択、⌘C／Ctrl+Cでコピーできます。
      </p>
    </div>
  );
}
