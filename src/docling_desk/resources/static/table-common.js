'use strict';
// Trusted UI: document values and column labels are always inserted as text.
window.TableTools = (() => {
  const make = (tag, cls, text) => {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = String(text);
    return node;
  };
  const button = (text, action, cls = '') => {
    const node = make('button', cls, text);
    node.type = 'button';
    node.addEventListener('click', action);
    return node;
  };
  const textNode = value => make('span', '', value ?? '');
  const dataRows = (table, headers) => table.rows.slice(headers).map((row, i) =>
    Object.fromEntries([['_sourceRow', i + headers + 1], ...row.map((value, c) => [`c${c}`, value])]));
  function columnName(index) {
    let result = '';
    for (let n = index + 1; n > 0; n = Math.floor((n - 1) / 26)) result = String.fromCharCode(65 + (n - 1) % 26) + result;
    return result;
  }
  function numeric(values) {
    const nonempty = values.map(v => String(v ?? '').trim()).filter(Boolean);
    return nonempty.length > 0 && nonempty.every(v => /^[+-]?(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?$/.test(v));
  }
  function columns(table, headers) {
    const rows = dataRows(table, headers);
    const result = [{title: '元行', field: '_sourceRow', width: 64, minWidth: 64, visible: false, frozen: true,
      sorter: 'number', hozAlign: 'right'}];
    for (let c = 0; c < table.columns; c++) {
      const title = Array.from({length: headers}, (_, r) => table.rows[r][c]).filter(Boolean).join(' / ') || `列 ${columnName(c)}`;
      const values = rows.map(row => row[`c${c}`]);
      const isNumber = numeric(values);
      const textWidth = value => Math.max(0, ...String(value ?? '').split('\n').map(line =>
        Array.from(line).reduce((sum, char) => sum + (char.charCodeAt(0) > 255 ? 13 : 7), 0)));
      // Wide documents need readable prose columns, rather than 24 equally narrow columns.
      const minWidth = table.columns > 6 ? Math.max(110, Math.min(280,
        Math.max(textWidth(title) + 85, ...values.slice(0, 80).map(value => textWidth(value) + 24)))) : 100;
      result.push({title, field: `c${c}`, minWidth, hozAlign: isNumber ? 'right' : 'left', sorter: isNumber ? 'number' : 'string'});
    }
    return result;
  }
  function matches(value, condition) {
    const text = String(value ?? '');
    if (condition.op === 'empty') return !text.trim();
    if (condition.op === 'notempty') return !!text.trim();
    if (condition.op === 'equal') return text.toLocaleLowerCase('ja') === condition.value.toLocaleLowerCase('ja');
    if (condition.op === 'gte' || condition.op === 'lte') {
      const number = Number(text.replaceAll(',', ''));
      return !!text.trim() && Number.isFinite(number) && (condition.op === 'gte' ? number >= Number(condition.value) : number <= Number(condition.value));
    }
    return text.toLocaleLowerCase('ja').includes(condition.value.toLocaleLowerCase('ja'));
  }
  const operations = {contains: '含む', equal: '一致', empty: '空欄', notempty: '空欄以外', gte: '以上', lte: '以下'};

  function createView(container, {table, original, onSource, onExpand}) {
    const life = new AbortController();
    let grid = null, ready = false, destroyed = false, popup = null, popupAnchor = null, popupRect = null, originalMode = false;
    let headers = table.header_rows, expanded = false, dialog = null, placeholder = null;
    const filters = new Map(), frozen = new Set(), widths = new Map();
    let savedSort = [], order = Array.from({length: table.columns}, (_, c) => `c${c}`), showSource = false;
    const card = make('section', 'table-card'); card.dataset.ref = table.ref;
    const head = make('div', 'table-card-head'), identity = make('div', 'table-identity');
    const title = make('strong', 'table-title', table.label), dimensions = make('span', 'table-dimensions');
    identity.append(title, dimensions); head.append(identity);
    const actions = make('div', 'table-head-actions');
    let dataButton, originalButton;
    if (original) {
      const modes = make('div', 'table-modes'); modes.setAttribute('aria-label', '表の表示');
      originalButton = button('原形', () => setMode(true)); dataButton = button('データ', () => setMode(false));
      modes.append(originalButton, dataButton); actions.append(modes);
    }
    const expandButton = button('広く表示', () => setExpanded(!expanded), 'table-expand');
    expandButton.prepend(TableIcons.create('expand'));
    expandButton.setAttribute('aria-expanded', 'false'); actions.append(expandButton); head.append(actions); card.append(head);
    const caption = original?.querySelector(':scope > caption');
    if (caption?.textContent.trim()) card.append(make('p', 'table-caption', caption.textContent));
    const toolbar = make('div', 'table-controls');
    const searchLabel = make('label', 'table-search'), search = make('input');
    search.type = 'search'; search.placeholder = '表内を検索'; search.setAttribute('aria-label', `${table.label}の全列を検索`);
    const searchIcon = TableIcons.create('search'); searchIcon.classList.add('table-search-icon');
    searchLabel.append(searchIcon, search);
    const filterButton = button('絞り込み', () => openFilter(filterButton));
    const settingsButton = button('表示設定', () => settings(settingsButton));
    toolbar.append(searchLabel, filterButton, settingsButton); card.append(toolbar);
    const chips = make('div', 'table-conditions'); chips.setAttribute('aria-label', '適用中の条件'); card.append(chips);
    const host = make('div', 'table-grid'); host.setAttribute('aria-label', `${table.label}のデータ`); card.append(host);
    const inspector = make('div', 'table-cell-inspector'), address = make('span', 'table-cell-address', 'セルを選択');
    const selectedValue = make('textarea'); selectedValue.readOnly = true; selectedValue.rows = 2;
    selectedValue.placeholder = 'セルを選ぶと全文を確認できます'; selectedValue.setAttribute('aria-label', '選択セルの全文');
    const copyButton = button('選択をコピー', () => {const selection = grid?.getSelection(); if (selection) copy(selection.text);});
    copyButton.disabled = true; inspector.append(address, selectedValue, copyButton); card.append(inspector);
    const native = make('div', 'table-native'); native.hidden = true;
    if (original) native.append(original); card.append(native);
    const footer = make('div', 'table-card-footer'), status = make('span', 'table-status');
    status.setAttribute('role', 'status');
    const sourceButton = button('出典を見る', () => sourceInfo(sourceButton), 'table-text-button');
    sourceButton.append(TableIcons.create('right'));
    footer.append(status, sourceButton); card.append(footer);
    const notice = make('p', 'table-notice'); notice.hidden = true; notice.setAttribute('role', 'status'); card.append(notice);
    if (table.merged) card.append(make('p', 'table-merged-note', '結合セルの値は左上に表示します。結合状態は原形で確認できます。'));
    container.append(card);

    function say(message) {notice.textContent = message; notice.hidden = !message;}
    function closePopup(restore = false) {
      popup?.remove(); popup = null;
      if (popupAnchor?.isConnected) {popupAnchor.setAttribute('aria-expanded', 'false'); if (restore) popupAnchor.focus();}
      popupAnchor = null;
    }
    function openPopup(anchor, role = 'dialog', point) {
      closePopup(); popupAnchor = anchor;
      popup = make('div', 'table-popup'); popup.setAttribute('role', role);
      popup.setAttribute('aria-label', role === 'menu' ? '表の操作メニュー' : '表の設定');
      // Dialog top layer is essential inside an expanded native dialog.
      (dialog?.open ? dialog : document.body).append(popup);
      anchor?.setAttribute('aria-expanded', 'true');
      const rect = anchor?.getBoundingClientRect();
      popupRect = rect;
      const x = point?.x ?? rect?.left ?? 12, y = point?.y ?? rect?.bottom + 6 ?? 12;
      popup.style.left = `${Math.max(8, Math.min(x, innerWidth - 292))}px`;
      popup.style.top = `${Math.max(8, Math.min(y, innerHeight - 180))}px`;
      popup.addEventListener('keydown', event => {
        if (event.key === 'Escape') {event.preventDefault(); event.stopPropagation(); closePopup(true);}
        if (role === 'menu' && ['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
          event.preventDefault();
          const items = [...popup.querySelectorAll('button:not(:disabled)')], current = items.indexOf(document.activeElement);
          const next = event.key === 'Home' ? 0 : event.key === 'End' ? items.length - 1 : (current + (event.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length;
          items[next]?.focus();
        }
        if (event.key === 'Tab') {
          const items = [...popup.querySelectorAll('button:not(:disabled),input:not(:disabled),select:not(:disabled),textarea')];
          const first = items[0], last = items.at(-1);
          if (event.shiftKey && document.activeElement === first) {event.preventDefault(); last?.focus();}
          else if (!event.shiftKey && document.activeElement === last) {event.preventDefault(); first?.focus();}
        }
      });
      return popup;
    }
    function fitPopup() {
      if (!popup) return;
      const rect = popup.getBoundingClientRect();
      popup.style.top = `${Math.max(8, Math.min(rect.top, innerHeight - rect.height - 8))}px`;
      popup.querySelector('button,input,select,textarea')?.focus();
    }
    function name(field) {return grid?.getColumn(field)?.getDefinition().title || field;}
    function currentSort() {return ready ? grid.getSorters().map(s => ({column: s.field, dir: s.dir})) : savedSort;}
    function update() {
      if (!ready || destroyed) return;
      dimensions.textContent = `${table.rows.length - headers}行 × ${table.columns}列`;
      status.textContent = originalMode ? `原形 · ${table.rows.length}行` : `${grid.getRows('active').length} / ${table.rows.length - headers}行を表示`;
      filterButton.disabled = originalMode || !table.columns;
      search.disabled = originalMode || !table.columns;
      chips.replaceChildren();
      if (search.value.trim()) chips.append(button(`検索：${search.value.trim()} ×`, () => {search.value = ''; applyFilters();}, 'table-chip'));
      for (const sort of currentSort()) chips.append(button(`${name(sort.column)}：${sort.dir === 'asc' ? '昇順' : '降順'} ×`, () => {
        const rest = currentSort().filter(s => s.column !== sort.column); if (rest.length) grid.setSort(rest); else grid.clearSort();
      }, 'table-chip'));
      for (const [field, condition] of filters) {
        chips.append(button(`${name(field)}：${operations[condition.op]} ${condition.value || ''} ×`, () => {filters.delete(field); applyFilters();}, 'table-chip'));
      }
      if (chips.childNodes.length) chips.append(button('リセット', reset, 'table-text-button'));
      chips.hidden = originalMode || !chips.childNodes.length;
      for (const column of grid.getColumns()) column.getElement()?.classList.toggle('table-column-active', currentSort().some(s => s.column === column.getField()) || filters.has(column.getField()));
    }
    function applyFilters() {
      if (!ready) return;
      const query = search.value.trim().toLocaleLowerCase('ja');
      grid.setFilter(row => (!query || Object.entries(row).some(([key, value]) => key !== '_sourceRow' && String(value).toLocaleLowerCase('ja').includes(query))) && [...filters].every(([field, condition]) => matches(row[field], condition)));
      update();
    }
    function reset() {search.value = ''; filters.clear(); if (ready) {grid.clearSort(); applyFilters();} say('');}
    function sortColumn(field, dir, add = false) {
      if (!ready) return;
      grid.setSort([...(add ? currentSort().filter(s => s.column !== field) : []), {column: field, dir}]); closePopup(true);
    }
    function openFilter(anchor, initialField) {
      if (!ready || originalMode) return;
      const pane = openPopup(anchor);
      pane.append(make('strong', 'table-popup-title', '列を絞り込む'));
      const field = make('select'), op = make('select'), value = make('input'); value.type = 'text';
      field.setAttribute('aria-label', '絞り込む列'); op.setAttribute('aria-label', '絞り込み条件'); value.setAttribute('aria-label', '絞り込み値');
      for (const column of grid.getColumns().filter(c => c.getField() !== '_sourceRow')) {
        const option = make('option', '', column.getDefinition().title); option.value = column.getField(); field.append(option);
      }
      if (initialField) field.value = initialField;
      function loadOperators() {
        op.replaceChildren();
        const definition = grid.getColumn(field.value)?.getDefinition();
        for (const [key, label] of Object.entries(operations)) {
          if (['gte', 'lte'].includes(key) && definition?.sorter !== 'number') continue;
          const option = make('option', '', label); option.value = key; op.append(option);
        }
        const condition = filters.get(field.value); op.value = condition?.op || 'contains'; value.value = condition?.value || ''; changeOperator();
      }
      function changeOperator() {value.disabled = ['empty', 'notempty'].includes(op.value); value.placeholder = ['gte', 'lte'].includes(op.value) ? '数値を入力' : '文字を入力';}
      field.addEventListener('change', loadOperators); op.addEventListener('change', changeOperator); loadOperators();
      pane.append(field, op, value);
      const error = make('p', 'table-popup-error'); error.setAttribute('role', 'alert'); pane.append(error);
      function apply() {
        const text = value.value.trim();
        if (['gte', 'lte'].includes(op.value) && (!text || !Number.isFinite(Number(text)))) {error.textContent = '数値を入力してください。'; value.focus(); return;}
        if (!text && !value.disabled) filters.delete(field.value); else filters.set(field.value, {op: op.value, value: value.disabled ? '' : text});
        applyFilters(); closePopup(true);
      }
      value.addEventListener('keydown', event => {if (event.key === 'Enter') apply();});
      const bar = make('div', 'table-popup-actions'); bar.append(button('解除', () => {filters.delete(field.value); applyFilters(); closePopup(true);}), button('適用', apply, 'table-primary'));
      pane.append(bar); fitPopup();
    }
    function settings(anchor) {
      const pane = openPopup(anchor); pane.append(make('strong', 'table-popup-title', '表示設定'));
      const label = make('label', 'table-setting', '見出し行数'), select = make('select'); select.setAttribute('aria-label', '見出し行数');
      for (let n = 0; n <= table.rows.length; n++) {const option = make('option', '', n ? `${n}行` : 'なし'); option.value = n; select.append(option);}
      select.value = headers; label.append(select); pane.append(label);
      select.addEventListener('change', () => {headers = Number(select.value); build();});
      const sourceLabel = make('label', 'table-setting'), check = make('input'); check.type = 'checkbox'; check.checked = showSource;
      sourceLabel.append(check, textNode('元行番号を表示')); pane.append(sourceLabel);
      check.addEventListener('change', () => {showSource = check.checked; const column = grid?.getColumn('_sourceRow'); if (column) {if (showSource) column.show(); else column.hide(); grid.redraw(true);}});
      pane.append(make('p', 'table-popup-hint', '列の境界をドラッグすると幅を変更できます。列見出しのドラッグで順序を変えられます。'));
      pane.append(button('列幅・列順・固定を戻す', () => {remember(); frozen.clear(); widths.clear(); order = Array.from({length: table.columns}, (_, c) => `c${c}`); build(false); closePopup(true);}));
      pane.append(button('閉じる', () => closePopup(true))); fitPopup();
    }
    async function copy(text) {
      closePopup();
      try {
        if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(text);
        else throw new Error('clipboard unavailable');
        say('コピーしました。');
      } catch {
        // Opaque sandbox frames may deny Clipboard API. Keep a manual copy path.
        const pane = openPopup(copyButton); pane.append(make('strong', 'table-popup-title', '選択した内容をコピー'));
        const area = make('textarea'); area.value = text; area.readOnly = true; area.setAttribute('aria-label', 'コピーする内容');
        pane.append(area, make('p', 'table-popup-hint', '⌘C または Ctrl+C でコピーしてください。'), button('閉じる', () => closePopup(true)));
        fitPopup(); area.focus(); area.select();
      }
    }
    function columnMenu(anchor, field, point, cell) {
      if (!ready || originalMode) return;
      const pane = openPopup(anchor, 'menu', point);
      pane.append(make('strong', 'table-popup-title', name(field)));
      const item = (label, action) => {const b = button(label, action); b.setAttribute('role', 'menuitem'); pane.append(b);};
      if (cell) {
        const selected = grid.getSelection();
        if (selected && (selected.rows > 1 || selected.columns > 1)) item('選択範囲をコピー', () => copy(selected.text));
        item('セルの全文を見る', () => {closePopup(); selectedValue.focus();});
        item('セルをコピー', () => copy(String(cell.getValue() ?? '')));
        item('行をコピー', () => copy(grid.getColumns().filter(c => c.isVisible()).map(c => cell.getRow().getData()[c.getField()] ?? '').join('\t')));
        pane.append(make('p', 'table-popup-hint', `元の行番号：${cell.getRow().getData()._sourceRow}`));
      }
      if (field !== '_sourceRow') {
        item('昇順に並べ替え', () => sortColumn(field, 'asc'));
        item('降順に並べ替え', () => sortColumn(field, 'desc'));
        if (currentSort().length && !currentSort().some(s => s.column === field)) item('並べ替えに追加（昇順）', () => sortColumn(field, 'asc', true));
        item('この列を絞り込む', () => openFilter(anchor, field));
        item(frozen.has(field) ? '列の固定を解除' : '列を左側に固定', () => {if (frozen.has(field)) frozen.delete(field); else frozen.add(field); build(); closePopup(true);});
      }
      if (currentSort().length || filters.size || search.value) item('並び・絞り込みを戻す', () => {reset(); closePopup(true);});
      fitPopup();
    }
    function sourceInfo(anchor) {
      const pane = openPopup(anchor); pane.append(make('strong', 'table-popup-title', '表の出典'));
      pane.append(make('p', '', table.source_label), make('p', 'table-popup-hint', `${table.label} · ${table.ref}`), make('p', 'table-popup-hint', '元行番号は抽出時の表の行番号です。「表示設定」で表示できます。'));
      if (original) pane.append(button('原形で確認', () => {setMode(true); closePopup(true);}));
      if (onSource) pane.append(button('原本プレビューを見る', () => {closePopup(); onSource();}));
      pane.append(button('閉じる', () => closePopup(true))); fitPopup();
    }
    function remember() {
      if (!ready) return;
      savedSort = currentSort();
      order = grid.getColumns().map(c => c.getField()).filter(f => f !== '_sourceRow');
      for (const column of grid.getColumns()) widths.set(column.getField(), column.getWidth());
    }
    function build(preserve = true) {
      if (destroyed) return;
      if (preserve) remember();
      ready = false; grid?.destroy(); host.replaceChildren(); address.textContent = 'セルを選択'; selectedValue.value = ''; copyButton.disabled = true;
      const definitions = columns(table, headers), byField = new Map(definitions.map(c => [c.field, c]));
      const ordered = [byField.get('_sourceRow'), ...order.filter(f => frozen.has(f)).map(f => byField.get(f)), ...order.filter(f => !frozen.has(f)).map(f => byField.get(f))];
      for (const definition of ordered) {
        if (widths.has(definition.field)) definition.width = widths.get(definition.field);
        if (definition.field === '_sourceRow') definition.visible = showSource;
        else {
          definition.frozen = frozen.has(definition.field);
        }
      }
      grid = TabulensGrid.create(host, {data: dataRows(table, headers), columns: ordered, maxHeight: expanded ? '60vh' : 360,
        onReady: () => {
          if (destroyed) return;
          ready = true; if (savedSort.length) grid.setSort(savedSort); applyFilters(); update();
        },
        onChange: update,
        onLayout: () => {if (ready) remember();},
        onHeaderMenu: (anchor, field, point) => columnMenu(anchor, field, point),
        onCellMenu: (anchor, field, point, cell) => columnMenu(anchor, field, point, cell),
        onCopy: copy,
        onSelection: selection => {
          copyButton.disabled = !selection;
          address.textContent = selection ? `${columnName(Number(selection.field.slice(1)))}${selection.sourceRow}${selection.rows > 1 || selection.columns > 1 ? ` · ${selection.rows}行 × ${selection.columns}列` : ''}` : 'セルを選択';
          selectedValue.value = selection?.value || '';
        },
      });
    }
    function setMode(value) {
      if (!original) return;
      originalMode = value; native.hidden = !value; host.hidden = value; inspector.hidden = value;
      for (const control of [search, filterButton]) control.disabled = value;
      originalButton.setAttribute('aria-pressed', String(value)); dataButton.setAttribute('aria-pressed', String(!value));
      closePopup(); if (!value && ready) grid.redraw(true); update();
    }
    function setExpanded(value) {
      if (expanded === value || destroyed) return;
      closePopup(); expanded = value;
      if (value) {
        placeholder = make('div', 'table-placeholder'); placeholder.style.height = `${card.getBoundingClientRect().height}px`; card.before(placeholder);
        dialog = make('dialog', 'table-focus'); dialog.setAttribute('aria-label', `${table.label}を広く表示`);
        document.body.append(dialog); dialog.append(card); dialog.showModal();
        dialog.addEventListener('cancel', event => {event.preventDefault(); setExpanded(false);});
        expandButton.replaceChildren(TableIcons.create('close'), textNode('文書に戻る'));
      } else {
        placeholder.replaceWith(card); placeholder = null; dialog.close(); dialog.remove(); dialog = null; expandButton.replaceChildren(TableIcons.create('expand'), textNode('広く表示'));
      }
      expandButton.setAttribute('aria-expanded', String(value)); onExpand?.(value);
      if (ready) {grid.setMaxHeight(value ? '60vh' : 360); requestAnimationFrame(() => {if (!destroyed) grid.redraw(true);});}
      if (!value) expandButton.focus();
    }
    search.addEventListener('input', applyFilters);
    document.addEventListener('pointerdown', event => {if (popup && !popup.contains(event.target) && event.target !== popupAnchor && !popupAnchor?.contains(event.target)) closePopup();}, {signal: life.signal});
    document.addEventListener('keydown', event => {if (popup && event.key === 'Escape') {event.preventDefault(); event.stopPropagation(); closePopup(true);}}, {signal: life.signal});
    document.addEventListener('scroll', event => {
      if (popup?.getAttribute('role') !== 'menu' || popup.contains(event.target)) return;
      const rect = popupAnchor?.getBoundingClientRect();
      // Grid focus can scroll an unrelated viewport after the menu opens.
      // Dismiss only when the anchor actually moves away from the menu.
      if (!popupAnchor?.isConnected || !rect || Math.abs(rect.left - popupRect.left) > 1 || Math.abs(rect.top - popupRect.top) > 1) closePopup();
    }, {capture: true, signal: life.signal});
    window.addEventListener('resize', () => closePopup(), {signal: life.signal});
    window.addEventListener('beforeprint', () => {closePopup(); setExpanded(false);}, {signal: life.signal});
    if (original) {originalButton.setAttribute('aria-pressed', 'false'); dataButton.setAttribute('aria-pressed', 'true');}
    build();
    return {
      redraw: () => {if (ready) grid.redraw(true);},
      close: () => {closePopup(); setExpanded(false);},
      destroy: () => {setExpanded(false); destroyed = true; life.abort(); closePopup(); grid?.destroy(); card.remove();},
    };
  }
  return {dataRows, columns, createView};
})();
