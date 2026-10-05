'use strict';
const LibraryBrowser = (() => {
  let data = {folders: [], jobs: []}, currentFolder = null;
  let checked = new Set(), visible = [], renderKey = null, navigationKey = null, pending = null, destination = null, busy = false, dragged = null;
  const key = item => `${item.kind}:${item.id}`;
  const name = item => item.kind === 'folder' ? item.name : item.filename;
  const activeFolders = () => data.folders;
  const allItems = () => [...data.folders.map(folder => ({...folder, kind:'folder'})), ...data.jobs.map(job => ({...job, kind:'file'}))];
  const selectedItems = () => allItems().filter(item => checked.has(key(item)));
  const folder = id => data.folders.find(item => item.id === id);
  function chain(id) {
    const result = [], seen = new Set();
    while (id && !seen.has(id)) { seen.add(id); const item = folder(id); if (!item) break; result.unshift(item); id = item.parent_id; }
    return result;
  }
  function pathLabel(id = currentFolder) { return ['資料一覧', ...chain(id).map(item => item.name)].join(' / '); }
  function icon(id) { const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg'), use = document.createElementNS(svg.namespaceURI, 'use'); svg.classList.add('icon'); svg.setAttribute('aria-hidden', 'true'); use.setAttribute('href', `#i-${id}`); svg.append(use); return svg; }
  function button(text, handler, className = '') { const el = document.createElement('button'); el.type = 'button'; el.textContent = text; el.className = className; el.addEventListener('click', handler); return el; }
  function go(id) {
    currentFolder = id; checked.clear(); $('fileSearch').value = ''; renderKey = null;
    updateAddress(true); render();
  }
  function breadcrumbs(target, id, onSelect, drops = false) {
    const fragment = document.createDocumentFragment(), path = [{id:null, name:'資料一覧'}, ...chain(id)];
    for (const [index, item] of path.entries()) {
      if (index) { const separator = document.createElement('span'); separator.textContent = '›'; separator.setAttribute('aria-hidden', 'true'); fragment.append(separator); }
      const link = button(item.name, () => onSelect(item.id));
      link.title = item.name; if (index === path.length - 1) link.setAttribute('aria-current', 'page');
      if (drops) dropTarget(link, item.id);
      fragment.append(link);
    }
    target.replaceChildren(fragment);
  }
  function dropTarget(el, id) {
    el.addEventListener('dragover', event => { if (dragged && !busy) { event.preventDefault(); event.dataTransfer.dropEffect = 'move'; el.classList.add('drop-target'); } });
    el.addEventListener('dragleave', () => el.classList.remove('drop-target'));
    el.addEventListener('drop', event => {
      event.preventDefault(); event.stopPropagation(); el.classList.remove('drop-target');
      if (!dragged || busy) return;
      const items = dragged; dragged = null; perform({action:'move', items, destination:id}, '移動しました。');
    });
  }
  function updateSelection() {
    const items = selectedItems(), count = items.length;
    $('selectionToolbar').hidden = !count; $('selectionCount').textContent = `${count}件選択`;
    $('selectionRename').disabled = count !== 1;
    $('selectionDownload').disabled = count !== 1 || items[0]?.kind !== 'file';
    $('selectAll').checked = visible.length > 0 && visible.every(item => checked.has(key(item)));
    $('selectAll').indeterminate = visible.some(item => checked.has(key(item))) && !$('selectAll').checked;
    $('selectAll').disabled = !visible.length || busy;
    for (const row of $('jobs').children) { const selected = checked.has(row.dataset.key); row.classList.toggle('selected', selected); row.querySelector('input').checked = selected; }
    for (const el of $('selectionToolbar').querySelectorAll('button')) { if (!['selectionRename','selectionDownload'].includes(el.id)) el.disabled = busy; }
    if (busy) { $('selectionRename').disabled = true; $('selectionDownload').disabled = true; }
  }
  function setChecked(item, value) { if (value) checked.add(key(item)); else checked.delete(key(item)); updateSelection(); }
  function itemMenu(item) {
    const menu = document.createElement('details'), summary = document.createElement('summary'), panel = document.createElement('div');
    menu.className = 'item-menu'; summary.className = 'icon-button'; summary.append(icon('more')); summary.setAttribute('aria-label', `${name(item)}の操作`); summary.title = '操作'; panel.className = 'item-menu-panel';
    const choose = action => { menu.open = false; start(action, [item]); };
    {
      panel.append(button('開く', () => item.kind === 'folder' ? go(item.id) : openJob(item)));
      panel.append(button('名前を変更', () => choose('rename')), button('移動先', () => choose('move')), button('コピー先', () => choose('copy')));
      if (item.kind === 'file') { const link = document.createElement('a'); link.textContent = '原本を保存'; link.href = fileUrl(item.id, 'original' + (item.original_filename || item.filename).slice((item.original_filename || item.filename).lastIndexOf('.')).toLowerCase(), true); panel.append(link); }
      panel.append(button('削除', () => choose('delete'), 'danger'));
    }
    menu.append(summary, panel); menu.addEventListener('click', event => event.stopPropagation());
    menu.addEventListener('toggle', () => { if (menu.open) document.querySelectorAll('.item-menu').forEach(other => { if (other !== menu) other.open = false; }); });
    return menu;
  }
  function render() {
    const navigation = JSON.stringify([currentFolder, chain(currentFolder)]);
    if (navigation !== navigationKey) {
      navigationKey = navigation;
      breadcrumbs($('breadcrumbs'), currentFolder, go, true);
    }
    $('libraryHint').textContent = 'ファイルを画面へドロップすると抽出を開始します。資料の操作はチェックボックス・「…」・右クリックから。フォルダーへのドラッグで移動できます。';
    const query = $('fileSearch').value.trim().toLocaleLowerCase('ja'), type = $('fileType').value, sort = $('fileSort').value;
    const entries = allItems().filter(item => (item.kind === 'folder' ? item.parent_id : item.folder_id) === currentFolder);
    visible = entries.filter(item => name(item).toLocaleLowerCase('ja').includes(query) && (!type || item.kind === 'folder' || item.filename.toLowerCase().endsWith('.' + type)));
    visible.sort((a,b) => (a.kind === b.kind ? 0 : a.kind === 'folder' ? -1 : 1) || (sort === 'name' ? name(a).localeCompare(name(b), 'ja') : sort === 'oldest' ? a.created - b.created : b.created - a.created) || a.id.localeCompare(b.id));
    checked = new Set([...checked].filter(value => visible.some(item => key(item) === value)));
    const signature = JSON.stringify([currentFolder, visible]);
    if (signature !== renderKey) {
      renderKey = signature;
      const fragment = document.createDocumentFragment(), date = new Intl.DateTimeFormat('ja-JP', {month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'});
      for (const item of visible) {
        const row = document.createElement('tr'); row.dataset.key = key(item);
        const selectCell = document.createElement('td'), checkbox = document.createElement('input'); checkbox.type = 'checkbox'; checkbox.setAttribute('aria-label', `${name(item)}を選択`); checkbox.addEventListener('change', () => setChecked(item, checkbox.checked)); selectCell.append(checkbox); selectCell.addEventListener('click', event => event.stopPropagation());
        const file = document.createElement('td'), open = button('', () => {} , 'file-open'), label = document.createElement('span'); label.className = 'file-name'; label.textContent = name(item); label.title = name(item);
        if (item.kind === 'folder') { const symbol = icon('folder'); symbol.classList.add('folder-kind'); open.append(symbol); }
        else { const kind = document.createElement('span'), extension = item.filename.split('.').pop().toLowerCase(); kind.className = 'file-kind ' + extension; kind.textContent = extension.toUpperCase(); kind.setAttribute('aria-hidden', 'true'); open.append(kind); }
        open.append(label); open.setAttribute('aria-label', `${name(item)}を開く`); file.append(open);
        const stateCell = document.createElement('td'), state = document.createElement('span'), pages = document.createElement('td'), created = document.createElement('td'), actions = document.createElement('td');
        if (item.kind === 'folder') { state.textContent = 'フォルダー'; pages.textContent = '—'; }
        else { state.className = 'file-state ' + item.state; state.textContent = labels[item.state]; pages.textContent = item.pages || '—'; }
        stateCell.append(state); created.textContent = date.format(new Date(item.created * 1000)); created.title = new Date(item.created * 1000).toLocaleString('ja-JP'); actions.append(itemMenu(item)); row.append(selectCell, file, stateCell, pages, created, actions);
        for (const [language, counts] of Object.entries(item.translations || {})) { const note = document.createElement('span'); note.className = 'translation-summary'; note.textContent = `${language === 'en' ? '英訳' : '日本語訳'} ${counts.saved}/${item.pages || '—'}${counts.active ? ' · 翻訳中' : counts.failed ? ' · 再実行可' : ''}`; stateCell.append(note); }
        row.addEventListener('click', event => { if (busy) return; if (event.ctrlKey || event.metaKey || event.shiftKey) setChecked(item, !checked.has(key(item))); else if (item.kind === 'folder') go(item.id); else openJob(item); });
        row.addEventListener('contextmenu', event => { event.preventDefault(); document.querySelectorAll('.item-menu').forEach(menu => { menu.open = false; }); row.querySelector('.item-menu').open = true; });
        {
          open.draggable = true;
          open.addEventListener('dragstart', event => { if (busy) { event.preventDefault(); return; } dragged = checked.has(key(item)) ? selectedItems().map(({kind,id}) => ({kind,id})) : [{kind:item.kind,id:item.id}]; event.dataTransfer.setData('text/plain', name(item)); event.dataTransfer.effectAllowed = 'move'; });
          open.addEventListener('dragend', () => { dragged = null; document.querySelectorAll('.drop-target').forEach(el => el.classList.remove('drop-target')); });
          if (item.kind === 'folder') dropTarget(row, item.id);
        }
        fragment.append(row);
      }
      $('jobs').replaceChildren(fragment);
    }
    $('fileCount').textContent = `${visible.length}件${visible.length !== entries.length ? ` / ${entries.length}件` : ''}`;
    $('libraryEmpty').hidden = !!visible.length; $('emptyText').textContent = entries.length ? '一致する項目がありません。' : 'このフォルダーは空です。'; $('emptyAdd').hidden = !!entries.length;
    updateSelection();
  }
  function excluded(id, items) { return items.some(item => item.kind === 'folder' && (item.id === id || chain(id).some(parent => parent.id === item.id))); }
  function renderDestination() {
    breadcrumbs($('destinationBreadcrumbs'), destination, id => { destination = id; renderDestination(); });
    const choices = activeFolders().filter(item => item.parent_id === destination).sort((a,b) => a.name.localeCompare(b.name,'ja'));
    $('destinationFolders').replaceChildren();
    for (const item of choices) {
      const link = button(item.name, () => { destination = item.id; renderDestination(); }, 'destination-folder'); link.prepend(icon('folder')); link.append(icon('next')); link.disabled = excluded(item.id, pending.items); $('destinationFolders').append(link);
    }
    $('destinationHint').textContent = choices.length ? 'フォルダーを開いて保存先を選んでください。' : 'このフォルダーに下位フォルダーはありません。';
    $('submitOperation').textContent = pending.action === 'move' ? 'ここに移動' : 'ここにコピー';
    $('submitOperation').disabled = busy || excluded(destination, pending.items);
  }
  function start(action, items = selectedItems()) {
    if (busy || (!items.length && action !== 'create')) return;
    if (action === 'delete') { perform({action, items:items.map(({kind,id}) => ({kind,id}))}, '削除しました。'); return; }
    pending = {action, items, parent:currentFolder}; destination = currentFolder;
    const titles = {create:'新しいフォルダー', rename:'名前を変更', move:'移動先を選択', copy:'コピー先を選択'};
    $('libraryDialogTitle').textContent = titles[action]; $('operationError').hidden = true;
    $('operationDescription').textContent = items.length ? items.length === 1 ? name(items[0]) : `${items.length}件の項目` : `作成先：${pathLabel()}`;
    const naming = ['create','rename'].includes(action); $('operationNameLabel').hidden = !naming; $('operationName').required = naming; $('operationName').value = action === 'rename' ? name(items[0]) : '';
    $('destinationBrowser').hidden = !['move','copy'].includes(action); $('submitOperation').textContent = {create:'作成',rename:'保存'}[action] || ''; $('submitOperation').disabled = false;
    if (['move','copy'].includes(action)) renderDestination();
    $('libraryDialog').showModal(); if (naming) { $('operationName').focus(); $('operationName').select(); }
  }
  async function perform(request, success, dialog = false) {
    if (busy) return; busy = true; updateSelection(); $('submitOperation').disabled = true;
    try {
      await json(request.action === 'create' ? '/api/folders' : '/api/library/operations', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(request.action === 'create' ? {name:request.name,parent_id:request.parent} : request)});
      checked.clear(); if (dialog) $('libraryDialog').close(); message(success); await refresh();
    } catch (error) { if (dialog) { $('operationError').textContent = error.message; $('operationError').hidden = false; } else message(error.message); await refresh(); }
    finally { busy = false; $('submitOperation').disabled = false; updateSelection(); }
  }
  function update(snapshot) {
    data = snapshot;
    if (currentFolder && !folder(currentFolder)) { currentFolder = null; checked.clear(); renderKey = null; updateAddress(false); message('フォルダーが見つからないため、資料一覧へ戻りました。'); }
  }
  function route(id) { currentFolder = id; checked.clear(); renderKey = null; render(); }
  function init() {
    $('newFolder').addEventListener('click', () => start('create'));
    $('selectionClear').addEventListener('click', () => { checked.clear(); updateSelection(); });
    for (const action of ['Rename','Move','Copy','Delete']) $(`selection${action}`).addEventListener('click', () => start(action.toLowerCase()));
    $('selectionDownload').addEventListener('click', () => { const item = selectedItems()[0]; if (item?.kind === 'file') { const link = document.createElement('a'); link.href = fileUrl(item.id, 'original' + (item.original_filename || item.filename).slice((item.original_filename || item.filename).lastIndexOf('.')).toLowerCase(), true); link.click(); } });
    $('selectAll').addEventListener('change', () => { checked = $('selectAll').checked ? new Set(visible.map(key)) : new Set(); updateSelection(); });
    for (const id of ['closeLibraryDialog','cancelOperation']) $(id).addEventListener('click', () => { if (!busy) $('libraryDialog').close(); });
    $('libraryDialog').addEventListener('cancel', event => { if (busy) event.preventDefault(); });
    $('libraryForm').addEventListener('submit', event => {
      event.preventDefault(); if (!pending) return;
      const request = {action:pending.action, items:pending.items.map(({kind,id}) => ({kind,id})), name:$('operationName').value, destination, parent:pending.parent};
      perform(request, {create:'フォルダーを作成しました。',rename:'名前を変更しました。',move:'移動しました。',copy:'コピーしました。'}[pending.action], true);
    });
    document.addEventListener('click', event => { if (!event.target.closest('.item-menu')) document.querySelectorAll('.item-menu').forEach(menu => { menu.open = false; }); });
    document.addEventListener('keydown', event => { if (event.key === 'Escape') document.querySelectorAll('.item-menu').forEach(menu => { menu.open = false; }); });
  }
  return {init, update, render, route, pathLabel, get currentFolder() {return currentFolder;}};
})();
