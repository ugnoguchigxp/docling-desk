'use strict';
// Trusted, local SVG paths. No imported document text is interpreted as markup.
window.TableIcons = (() => {
  const paths = {
    down: 'm6 9 6 6 6-6', right: 'm9 6 6 6-6 6', left: 'm15 6-6 6 6 6',
    asc: 'M12 19V5m-5 5 5-5 5 5', desc: 'M12 5v14m-5-5 5 5 5-5',
    sort: 'M8 19V5m-4 4 4-4 4 4m4-4v14m-4-4 4 4 4-4',
    search: 'M21 21l-5-5M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0',
    expand: 'M14 3h7v7m0-7-7 7M10 21H3v-7m0 7 7-7',
    close: 'm6 6 12 12M6 18 18 6',
    filter: 'M4 5h16l-6 7v6l-4 2v-8Z',
    move: 'M12 3v18M3 12h18m-12-6 3-3 3 3m-6 12 3 3 3-3M6 9l-3 3 3 3m12-6 3 3-3 3',
    pin: 'm16 3 5 5-4 1-3 5v3l-7-7h3l5-3ZM3 21l7-7',
    hide: 'm3 3 18 18M10 5c5-1 9 3 12 7-1 2-3 4-5 5M6 6c-2 2-4 4-5 6 3 4 7 7 12 7l2-1',
    blocked: 'M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0M6 6l12 12',
  };
  function create(name) {
    const ns = 'http://www.w3.org/2000/svg', svg = document.createElementNS(ns, 'svg');
    for (const [key, value] of Object.entries({viewBox: '0 0 24 24', width: '16', height: '16', fill: 'none', stroke: 'currentColor', 'stroke-width': '1.75', 'stroke-linecap': 'round', 'stroke-linejoin': 'round', 'aria-hidden': 'true', focusable: 'false'})) svg.setAttribute(key, value);
    svg.classList.add('table-svg-icon');
    const path = document.createElementNS(ns, 'path'); path.setAttribute('d', paths[name]); svg.append(path);
    return svg;
  }
  function grid(name) {
    const span = document.createElement('span'); span.className = 'table-grid-icon'; span.setAttribute('aria-hidden', 'true');
    span.append(create(name)); return span;
  }
  return {create, grid};
})();
// Read-only port of TabuLens apps/web/src/App.tsx: column defaults, virtualised
// client-side rows and active-cell tracking. Attribution: vendor/tabulens-LICENSE.txt.
// React, workbook recalculation, draft-row extension and ML are intentionally absent.
window.TabulensGrid = (() => {
  const localeText = {noRowsToShow: '該当する行がありません。', noMatchingRows: '該当する行がありません。', loadingOoo: '読み込み中…'};

  function create(host, {data, columns, maxHeight, onReady, onChange, onLayout, onHeaderMenu, onCellMenu, onSelection, onCopy}) {
    let api, predicate = () => true, anchor = null, end = null, contextCell = null, extending = false, dead = false;
    const definitions = new Map(columns.map(column => [column.field, column]));
    const life = new AbortController();
    const element = (field, row) => host.querySelector(row == null
      ? `.ag-header-cell[col-id="${field}"]` : `.ag-row[row-index="${row}"] .ag-cell[col-id="${field}"]`);
    const orderedColumns = () => api.getAllDisplayedColumns().filter(c => c.getColId() !== '_sourceRow');
    function inRange(row, field) {
      if (!anchor || !end) return false;
      const keys = orderedColumns().map(c => c.getColId()), col = keys.indexOf(field);
      const a = keys.indexOf(anchor.col), b = keys.indexOf(end.col);
      return a >= 0 && b >= 0 && col >= Math.min(a, b) && col <= Math.max(a, b)
        && row >= Math.min(anchor.row, end.row) && row <= Math.max(anchor.row, end.row);
    }
    function selection() {
      if (!anchor || !end) return null;
      const keys = orderedColumns().map(c => c.getColId()), a = keys.indexOf(anchor.col), b = keys.indexOf(end.col);
      if (a < 0 || b < 0) return null;
      const fields = keys.slice(Math.min(a, b), Math.max(a, b) + 1), rows = [];
      for (let r = Math.min(anchor.row, end.row); r <= Math.max(anchor.row, end.row); r++) {
        const row = api.getDisplayedRowAtIndex(r)?.data;
        if (row) rows.push(fields.map(field => String(row[field] ?? '')));
      }
      const active = api.getDisplayedRowAtIndex(end.row)?.data;
      const tsv = value => /[\t\r\n"]/.test(value) ? `"${value.replaceAll('"', '""')}"` : value;
      const text = rows.length === 1 && fields.length === 1 ? rows[0][0] : rows.map(row => row.map(tsv).join('\t')).join('\n');
      return {text, rows: rows.length, columns: fields.length,
        field: end.col, sourceRow: active?._sourceRow, value: String(active?.[end.col] ?? '')};
    }
    function notifySelection() {
      api.refreshCells({force: true}); onSelection?.(selection());
    }
    function clearSelection() {anchor = end = null; notifySelection();}
    function focus(row, col, extend = false) {
      if (row == null || col === '_sourceRow' || !api.getDisplayedRowAtIndex(row)) return;
      end = {row, col}; if (!extend || !anchor) anchor = {...end}; notifySelection();
    }
    function rowView(node) {return {getData: () => node.data};}
    function cellView(node, field) {
      return {getValue: () => node.data[field], getField: () => field,
        getRow: () => rowView(node), getElement: () => element(field, node.rowIndex)};
    }
    function columnView(column) {
      const field = column.getColId();
      return {getField: () => field, getDefinition: () => definitions.get(field),
        getWidth: () => column.getActualWidth(), isVisible: () => column.isVisible(),
        getElement: () => element(field), show: () => api.setColumnsVisible([field], true),
        hide: () => api.setColumnsVisible([field], false)};
    }
    class HeaderLabel {
      init(params) {
        this.gui = document.createElement('div'); this.gui.className = 'table-column-title';
        const letter = document.createElement('small'); letter.className = 'table-column-letter';
        let labelIndex = Number(params.column.getColId().slice(1)) + 1, labelText = '';
        while (labelIndex > 0) {labelText = String.fromCharCode(65 + (labelIndex - 1) % 26) + labelText; labelIndex = Math.floor((labelIndex - 1) / 26);}
        letter.textContent = labelText;
        const label = document.createElement('span'); label.textContent = params.displayName;
        const menu = document.createElement('button'); menu.type = 'button'; menu.append(TableIcons.create('down'));
        menu.className = 'table-column-menu'; menu.setAttribute('aria-label', `${params.displayName}の列メニュー`);
        menu.setAttribute('aria-haspopup', 'menu');
        menu.addEventListener('click', event => {event.stopPropagation(); onHeaderMenu(menu, params.column.getColId());});
        this.gui.append(letter, label, menu);
      }
      getGui() {return this.gui;}
    }
    const columnDefs = columns.map(column => ({
      field: column.field, headerName: column.title, headerTooltip: column.title,
      minWidth: column.minWidth, width: column.width, flex: column.width ? undefined : 1,
      hide: column.visible === false, pinned: column.frozen ? 'left' : null,
      sortable: column.field !== '_sourceRow', suppressMovable: column.field === '_sourceRow',
      suppressNavigable: column.field === '_sourceRow', lockPinned: column.field === '_sourceRow',
      headerComponentParams: column.field === '_sourceRow' ? undefined : {innerHeaderComponent: HeaderLabel},
      headerClass: params => params.column.getSort() ? 'table-column-active' : '',
      comparator: (a, b, _nodeA, _nodeB, descending) => {
        const emptyA = !String(a ?? '').trim(), emptyB = !String(b ?? '').trim();
        if (emptyA !== emptyB) return (emptyA ? 1 : -1) * (descending ? -1 : 1);
        return column.sorter === 'number'
          ? Number(String(a ?? '').replaceAll(',', '')) - Number(String(b ?? '').replaceAll(',', ''))
          : String(a ?? '').localeCompare(String(b ?? ''), 'ja');
      },
      cellStyle: {textAlign: column.hozAlign === 'right' ? 'right' : 'left'},
      // Explicit text nodes protect imported HTML and formula-like strings.
      cellRenderer: params => {const span = document.createElement('span'); span.textContent = params.value == null ? '' : String(params.value); return span;},
      tooltipValueGetter: params => params.value == null ? '' : String(params.value),
      cellClassRules: {'table-cell-selected': params => inRange(params.node.rowIndex, column.field)},
    }));
    host.style.maxHeight = typeof maxHeight === 'number' ? `${maxHeight}px` : maxHeight;
    host.style.height = `${Math.max(1, data.length) * 38 + 64}px`;
    host.classList.add('ag-theme-quartz');
    api = agGrid.createGrid(host, {
      theme: 'legacy', localeText, rowData: data, columnDefs,
      icons: Object.fromEntries(Object.entries({
        sortAscending: 'asc', sortDescending: 'desc', sortUnSort: 'sort',
        sortAbsoluteAscending: 'asc', sortAbsoluteDescending: 'desc',
        filter: 'filter', filterActive: 'filter', menu: 'down', menuAlt: 'down',
        columnMovePin: 'pin', columnMoveHide: 'hide', columnMoveMove: 'move',
        columnMoveLeft: 'left', columnMoveRight: 'right',
        dropNotAllowed: 'blocked',
      }).map(([key, name]) => [key, () => TableIcons.grid(name)])),
      // Ported from TabuLens: retain virtualisation and smooth row navigation.
      rowBuffer: 20, debounceVerticalScrollbar: true, suppressColumnVirtualisation: false,
      rowModelType: 'clientSide', animateRows: false, suppressScrollOnNewData: true,
      defaultColDef: {editable: false, sortable: true, resizable: true, suppressMovable: false,
        valueFormatter: params => params.value == null ? '' : String(params.value)},
      rowHeight: 38, headerHeight: 46, tooltipShowDelay: 500,
      getRowId: params => String(params.data._sourceRow),
      isExternalFilterPresent: () => true, doesExternalFilterPass: node => predicate(node.data),
      onFirstDataRendered: () => onReady?.(),
      onModelUpdated: () => {if (api && !dead) {host.style.height = `${Math.max(1, api.getDisplayedRowCount()) * 38 + 64}px`; onChange?.();}},
      onSortChanged: () => {if (api) {clearSelection(); api.refreshHeader(); onChange?.();}},
      onFilterChanged: () => {if (api) clearSelection();},
      onColumnMoved: event => {if (event.finished) {clearSelection(); onLayout?.();}},
      onColumnResized: event => {if (event.finished) onLayout?.();},
      onCellFocused: event => {
        const field = event.column?.getColId();
        if (!field || !api) return;
        const current = api.getFocusedCell();
        // AG Grid dispatches focus asynchronously. Do not undo a range already
        // established by pointerdown / Shift+arrow, or replay an obsolete focus.
        if (current?.rowIndex !== event.rowIndex || current?.column.getColId() !== field) return;
        if (contextCell?.row === event.rowIndex && contextCell?.col === field) {contextCell = null; return;}
        if (end?.row === event.rowIndex && end?.col === field) return;
        focus(event.rowIndex, field, extending);
      },
      onCellContextMenu: event => {
        event.event.preventDefault();
        if (!inRange(event.node.rowIndex, event.column.getColId())) focus(event.node.rowIndex, event.column.getColId());
        const cell = cellView(event.node, event.column.getColId());
        onCellMenu(cell.getElement(), cell.getField(), {x: event.event.clientX, y: event.event.clientY}, cell);
      },
    });
    // Community has no range clipboard module. This small read-only selection uses
    // displayed rows/columns, so sorting, filtering and reordering also apply to copy.
    host.addEventListener('pointerdown', event => {
      const cell = event.target.closest('.ag-cell'); if (!cell) return;
      extending = event.shiftKey;
      const row = Number(cell.closest('.ag-row').getAttribute('row-index')), field = cell.getAttribute('col-id');
      contextCell = null;
      if (event.button === 2 && inRange(row, field)) {contextCell = {row, col: field}; return;}
      focus(row, field, extending);
    }, {capture: true, signal: life.signal});
    host.addEventListener('keydown', event => {
      const cell = event.target.closest('.ag-cell'); if (!cell) return;
      contextCell = null;
      extending = event.shiftKey;
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'c' && selection()) {
        event.preventDefault(); event.stopPropagation(); onCopy?.(selection().text);
      }
      if (event.shiftKey && ['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key) && end) {
        event.preventDefault(); event.stopPropagation();
        const keys = orderedColumns().map(c => c.getColId());
        const row = Math.max(0, Math.min(api.getDisplayedRowCount() - 1, end.row + (event.key === 'ArrowDown' ? 1 : event.key === 'ArrowUp' ? -1 : 0)));
        const col = keys[Math.max(0, Math.min(keys.length - 1, keys.indexOf(end.col) + (event.key === 'ArrowRight' ? 1 : event.key === 'ArrowLeft' ? -1 : 0)))];
        focus(row, col, true); api.ensureIndexVisible(row); api.ensureColumnVisible(col); api.setFocusedCell(row, col);
      }
      if (event.key === 'Escape') {clearSelection();}
      if (event.key === 'ContextMenu' || (event.shiftKey && event.key === 'F10')) {
        event.preventDefault(); event.stopPropagation();
        const focused = api.getFocusedCell(), node = focused && api.getDisplayedRowAtIndex(focused.rowIndex);
        if (node) {const view = cellView(node, focused.column.getColId()); onCellMenu(cell, view.getField(), undefined, view);}
      }
    }, {capture: true, signal: life.signal});
    host.addEventListener('keyup', () => {extending = false;}, {signal: life.signal});
    host.addEventListener('contextmenu', event => {
      const header = event.target.closest('.ag-header-cell'); if (!header) return;
      event.preventDefault(); onHeaderMenu(header.querySelector('button') || header, header.getAttribute('col-id'), {x: event.clientX, y: event.clientY});
    }, {signal: life.signal});
    // Empty tables don't always raise firstDataRendered.
    queueMicrotask(() => {if (!dead && !data.length) onReady?.();});
    return {
      getColumns: () => api.getAllGridColumns().map(columnView),
      getColumn: field => {const c = api.getColumn(field); return c ? columnView(c) : null;},
      getRows: () => {const rows = []; api.forEachNodeAfterFilterAndSort(node => rows.push(rowView(node))); return rows;},
      getSorters: () => api.getColumnState().filter(c => c.sort).sort((a, b) => a.sortIndex - b.sortIndex).map(c => ({field: c.colId, dir: c.sort})),
      setSort: sorts => api.applyColumnState({state: sorts.map((s, i) => ({colId: s.column, sort: s.dir, sortIndex: i})), defaultState: {sort: null}}),
      clearSort: () => api.applyColumnState({defaultState: {sort: null}}),
      setFilter: filter => {predicate = filter; api.onFilterChanged();},
      redraw: () => api.refreshCells({force: true}),
      setMaxHeight: height => {host.style.maxHeight = typeof height === 'number' ? `${height}px` : height;},
      getSelection: selection,
      destroy: () => {dead = true; life.abort(); api.destroy();},
    };
  }
  return {create};
})();
