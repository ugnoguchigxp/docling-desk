'use strict';
const $ = id => document.getElementById(id);
const views = ['preview', 'tables', 'structure', 'rag'];
const labels = {queued: '待機中', running: '抽出中', success: '抽出完了', partial: '一部抽出', failed: '失敗'};
let selected = null, view = 'preview', currentJobs = [], detailKey = null, previewUrl = 'about:blank';
let structureLoaded = false, structureController = null, refreshing = false, openedFrom = null;
let refreshError = null;

async function json(url, options) {
  const response = await fetch(url, options), data = await response.json();
  if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
  return data;
}
function fileUrl(id, name, download = false) { return `/files/${id}/${name}${download ? '?download=true' : ''}`; }
function message(text, viewer = false) {
  const target = $(viewer ? 'viewerMessage' : 'message'); target.textContent = text; target.hidden = !text;
}
function closeMenus() { document.querySelectorAll('.toolbar-menu').forEach(menu => { menu.open = false; }); }
function updateAddress(push = false, fromLibrary = false) {
  const url = new URL(location.href); url.search = ''; url.hash = '';
  if (LibraryBrowser.currentFolder) url.searchParams.set('folder', LibraryBrowser.currentFolder);
  if (selected) { url.searchParams.set('job', selected); url.searchParams.set('view', view); }
  const state = {fromLibrary};
  if (push && url.href !== location.href) history.pushState(state, '', url);
  else if (!push) history.replaceState({...history.state, ...state}, '', url);
}
function setScreen() {
  $('library').hidden = !!selected; $('detail').hidden = !selected; closeMenus();
  if (!selected) {
    window.TranslationBrowser?.setJob(null);
    window.ExplanationBrowser?.setJob(null);
    SlideBrowser.hide(); TableBrowser.hide(); RagBrowser.hide();
    $('original').src = 'about:blank'; $('slideControls').hidden = true; message('', true);
    document.title = '資料一覧 · 文書プレビュー';
  }
}
function openJob(job) {
  openedFrom = document.activeElement;
  selected = job.id; view = 'preview'; updateAddress(true, true); setScreen(); show(job);
  $('backToFiles').focus();
}
function openLibrary(push = true) {
  selected = null; setScreen(); if (push) updateAddress(true); renderLibrary();
  if (openedFrom?.isConnected) openedFrom.focus(); else $('fileSearch').focus();
}
function applyRoute() {
  const params = new URLSearchParams(location.search);
  selected = params.get('job'); view = views.includes(params.get('view')) ? params.get('view') : 'preview';
  LibraryBrowser.route(params.get('folder'));
  setScreen();
  const job = currentJobs.find(job => job.id === selected);
  if (job) show(job); else if (selected) { $('filename').textContent = '読み込み中…'; }
  else if (openedFrom?.isConnected) openedFrom.focus();
}
window.addEventListener('popstate', applyRoute);
$('backToFiles').addEventListener('click', () => openLibrary());

function resetView() {
  if ($('explainOpen')) $('explainOpen').hidden = view !== 'preview';
  if ($('explanationDisclosure')) $('explanationDisclosure').hidden = view !== 'preview' || !['success','partial'].includes(currentJobs.find(j => j.id === selected)?.state);
  for (const name of views) $(name).hidden = name !== view;
  document.querySelectorAll('[data-view]').forEach(button => {
    const active = button.dataset.view === view;
    button.classList.toggle('active', active); button.setAttribute('aria-selected', String(active));
    button.tabIndex = active ? 0 : -1;
  });
  if (view === 'tables') TableBrowser.show(); else TableBrowser.hide();
  if (view === 'preview') {
    const job = currentJobs.find(job => job.id === selected);
    if (!job?.slide_layout && $('original').getAttribute('src') !== previewUrl) $('original').src = previewUrl;
    SlideBrowser.show();
  } else { SlideBrowser.hide(); }
  if (view === 'rag') RagBrowser.show(); else RagBrowser.hide();
  if (view === 'structure') loadStructure();
}
function changeView(next) { view = next; closeMenus(); updateAddress(false, !!history.state?.fromLibrary); resetView(); }
for (const button of document.querySelectorAll('[data-view]')) {
  button.addEventListener('click', () => changeView(button.dataset.view));
  button.addEventListener('keydown', event => {
    const index = views.indexOf(button.dataset.view);
    let next;
    if (event.key === 'ArrowRight') next = views[(index + 1) % views.length];
    if (event.key === 'ArrowLeft') next = views[(index + views.length - 1) % views.length];
    if (event.key === 'Home') next = views[0];
    if (event.key === 'End') next = views[views.length - 1];
    if (next) { event.preventDefault(); changeView(next); $(`tab-${next}`).focus(); }
  });
}
function card(title, body, meta) {
  const container = document.createElement('article'), heading = document.createElement('strong');
  const paragraph = document.createElement('p'), details = document.createElement('pre');
  container.className = 'card'; heading.textContent = title; paragraph.textContent = body; details.textContent = meta;
  container.append(heading, paragraph, details); return container;
}
async function loadStructure() {
  const job = currentJobs.find(job => job.id === selected);
  if (!job || !['success', 'partial'].includes(job.state) || structureLoaded || structureController) return;
  const id = job.id, request = new AbortController(); structureController = request;
  $('structure').textContent = '構造・参照を読み込んでいます…';
  try {
    const elements = await json(fileUrl(id, 'elements.json'), {signal: request.signal});
    if (selected !== id || structureController !== request) return;
    const fragment = document.createDocumentFragment();
    for (const element of elements) fragment.append(card(`${element.label} · ${element.ref}`, element.text || '画像・図要素（本文はJSONで参照）', `ページ: ${element.pages.join(', ') || '位置情報なし'}\n親: ${element.parent || 'なし'}\nキャプション参照: ${element.captions.join(', ') || 'なし'}\n${JSON.stringify(element.provenance, null, 2)}`));
    $('structure').replaceChildren(fragment); structureLoaded = true;
  } catch (error) { if (error.name !== 'AbortError' && selected === id) $('structure').textContent = error.message; }
  finally { if (structureController === request) structureController = null; }
}
function show(job) {
  if (selected !== job.id) return;
  window.TranslationBrowser?.setJob(job);
  window.ExplanationBrowser?.setJob(job);
  const key = `${job.id}:${job.state}:${job.filename}:${job.preview || ''}`;
  if (key === detailKey) { document.title = `${job.filename} · 文書プレビュー`; resetView(); return; }
  structureController?.abort(); structureController = null; structureLoaded = false;
  detailKey = key; TableBrowser.setJob(job); SlideBrowser.setJob(job); RagBrowser.setJob(job);
  $('filename').textContent = job.filename; $('filename').title = job.filename;
  document.title = `${job.filename} · 文書プレビュー`;
  $('stats').textContent = `${labels[job.state]}${job.synthetic ? ' · 合成テスト資料' : ''} · ${job.pages} ページ/スライド/シート · 表 ${job.tables} · 図 ${job.pictures} · ${job.chunks} 文脈${job.rag_policy ? ` / ${job.search_chunks} 検索用` : ''}${job.duration !== null ? ` · ${job.duration}秒` : ''}`;
  $('originalDownload').href = fileUrl(job.id, 'original' + (job.original_filename || job.filename).substring((job.original_filename || job.filename).lastIndexOf('.')).toLowerCase(), true);
  message(job.error || (['queued', 'running'].includes(job.state) ? `${labels[job.state]}です。完了するとプレビューを表示します。` : ''), true);
  const done = ['success', 'partial'].includes(job.state);
  $('downloads').replaceChildren(); $('original').hidden = !job.preview; $('noPreview').hidden = !!job.preview || !done;
  const workbook = job.filename.toLowerCase().endsWith('.xlsx');
  const pdf = job.filename.toLowerCase().endsWith('.pdf');
  previewUrl = job.preview ? (workbook ? `/view/${job.id}/workbook` : pdf ? `/view/${job.id}/pdf` : fileUrl(job.id, job.preview)) : 'about:blank';
  $('original').classList.toggle('drop-through-preview', !workbook && !pdf);
  $('original').setAttribute('sandbox', workbook ? 'allow-scripts allow-downloads' : pdf ? 'allow-scripts' : '');
  $('originalOpen').hidden = !job.preview; $('originalOpen').href = job.preview ? previewUrl : '#';
  $('structure').replaceChildren();
  if (done) {
    for (const [name, label] of [['document.json', 'Docling JSON'], ['text.txt', '本文'], ['rag.jsonl', 'RAG文脈 JSONL'], ['rag-index.jsonl', 'RAG検索用 JSONL'], ['rag-docling.jsonl', '従来RAG JSONL'], ['rag-policy.json', '分割方針'], ['elements.json', '要素・座標']]) {
      const link = document.createElement('a'); link.href = fileUrl(job.id, name, true); link.textContent = label; $('downloads').append(link);
    }
  }
  resetView();
}
function renderLibrary() { LibraryBrowser.render(); }
async function refresh() {
  if (refreshing) return; refreshing = true;
  try {
    const library = await json('/api/library');
    currentJobs = library.jobs; LibraryBrowser.update(library); renderLibrary();
    if (refreshError) {
      for (const id of ['message', 'viewerMessage']) if ($(id).textContent === refreshError) { $(id).textContent = ''; $(id).hidden = true; }
      refreshError = null;
    }
    if (selected) {
      const job = currentJobs.find(job => job.id === selected);
      if (job) {
        if (`${job.id}:${job.state}:${job.filename}:${job.preview || ''}` !== detailKey) show(job);
      } else message('資料が見つかりません。資料一覧へ戻って選び直してください。', true);
    }
  } catch (error) { refreshError = error.message; message(error.message, !!selected); }
  finally { refreshing = false; }
}
for (const id of ['fileSearch', 'fileType', 'fileSort']) $(id).addEventListener(id === 'fileSearch' ? 'input' : 'change', renderLibrary);
let uploading = false, uploadDestination = null, fileDragDepth = 0, lastFileDrop = 0, lastFileDrag = 0;
function uploadFolder() {
  return selected ? currentJobs.find(job => job.id === selected)?.folder_id ?? LibraryBrowser.currentFolder : LibraryBrowser.currentFolder;
}
function addFile() {
  uploadDestination = uploadFolder();
  $('uploadMessage').hidden = true; $('uploadLocation').textContent = `保存先：${LibraryBrowser.pathLabel(uploadDestination)}`; $('uploadDialog').showModal();
}
async function uploadFiles(files, destination, fromDialog = false, rejected = []) {
  if (uploading) { message('アップロード中です。終了してから、もう一度ドロップしてください。', !!selected); return; }
  if (!files.length && !rejected.length) return;
  uploading = true;
  const button = $('upload').querySelector('[type=submit]'), failures = [...rejected];
  button.disabled = true; $('closeUpload').disabled = true;
  let added = 0;
  try {
    for (const [index, file] of files.entries()) {
      const status = `アップロード中… ${index + 1} / ${files.length}件`;
      if (fromDialog) { $('uploadMessage').textContent = status; $('uploadMessage').hidden = false; }
      else message(status, !!selected);
      if (!/\.(pdf|pptx|xlsx)$/i.test(file.name)) { failures.push(`${file.name}：PDF / PPTX / XLSXを選んでください。`); continue; }
      if (file.size > 50 * 1024 * 1024) { failures.push(`${file.name}：上限50 MiBを超えています。`); continue; }
      const body = new FormData(); body.set('file', file); if (destination) body.set('folder_id', destination);
      try { await json('/api/upload', {method:'POST', body}); added++; }
      catch (error) { failures.push(`${file.name}：${error.message}`); }
    }
    await refresh();
    const result = [added ? `${added}件の資料を追加しました。抽出状況は資料一覧で確認できます。` : '資料を追加できませんでした。', ...failures].join('\n');
    message(result, !!selected);
    if (fromDialog) {
      if (failures.length) { $('uploadMessage').textContent = result; $('uploadMessage').hidden = false; }
      else { $('uploadDialog').close(); $('upload').reset(); }
    }
  } catch (error) { message(error.message, !!selected); if (fromDialog) { $('uploadMessage').textContent = error.message; $('uploadMessage').hidden = false; } }
  finally { uploading = false; button.disabled = false; $('closeUpload').disabled = false; }
}
$('addFile').addEventListener('click', addFile); $('emptyAdd').addEventListener('click', addFile);
$('closeUpload').addEventListener('click', () => { if (!uploading) $('uploadDialog').close(); });
$('uploadDialog').addEventListener('cancel', event => { if (uploading) event.preventDefault(); });
$('upload').addEventListener('submit', event => {
  event.preventDefault(); uploadFiles([...$('upload').elements.file.files], uploadDestination, true);
});
function isFileDrag(event) { return [...(event.dataTransfer?.types || [])].includes('Files'); }
function hideFileDrop() { fileDragDepth = 0; $('fileDropOverlay').hidden = true; }
function showFileDrop() {
  lastFileDrag = Date.now();
  $('fileDropTitle').textContent = uploading ? 'アップロード中です。完了までお待ちください' : 'ここにドロップして抽出を開始';
  $('fileDropLocation').textContent = `保存先：${LibraryBrowser.pathLabel(uploadFolder())}`;
  $('fileDropOverlay').hidden = false;
}
// Capture file events before the library's internal move handlers and browser navigation.
window.addEventListener('dragenter', event => {
  if (!isFileDrag(event)) return;
  event.preventDefault(); fileDragDepth++; showFileDrop();
}, true);
window.addEventListener('dragover', event => {
  if (!isFileDrag(event)) return;
  event.preventDefault(); event.dataTransfer.dropEffect = uploading ? 'none' : 'copy'; showFileDrop();
}, true);
window.addEventListener('dragleave', event => {
  if (!isFileDrag(event)) return;
  fileDragDepth = Math.max(0, fileDragDepth - 1);
  if (!fileDragDepth || event.clientX <= 0 || event.clientY <= 0 || event.clientX >= innerWidth || event.clientY >= innerHeight) hideFileDrop();
}, true);
window.addEventListener('drop', event => {
  if (!isFileDrag(event)) return;
  event.preventDefault(); event.stopImmediatePropagation(); lastFileDrop = Date.now(); hideFileDrop();
  const destination = uploadFolder(), files = [], rejected = [];
  // A dropped directory is not a supported document; never submit its zero-byte File.
  const items = [...(event.dataTransfer.items || [])].filter(item => item.kind === 'file');
  if (items.length) {
    for (const item of items) {
      const entry = item.webkitGetAsEntry?.();
      if (entry?.isDirectory) { rejected.push(`${entry.name}：フォルダーではなく、ファイルをドロップしてください。`); continue; }
      const file = item.getAsFile(); if (file) files.push(file);
    }
  } else files.push(...event.dataTransfer.files);
  uploadFiles(files, destination, $('uploadDialog').open, rejected);
}, true);
window.addEventListener('message', event => {
  if (event.data?.type !== 'docling-file-drop' || ![...document.querySelectorAll('iframe')].some(frame => frame.contentWindow === event.source)) return;
  if (event.data.action === 'leave' && Number.isFinite(event.data.time) && event.data.time >= lastFileDrag) hideFileDrop();
  if (event.data.action === 'enter' && Number.isFinite(event.data.time) && event.data.time > lastFileDrop) showFileDrop();
  if (event.data.action === 'drop' && Array.isArray(event.data.files) && event.data.files.every(file => file instanceof File) && Array.isArray(event.data.rejected) && event.data.rejected.every(reason => typeof reason === 'string')) {
    lastFileDrop = Date.now(); hideFileDrop(); uploadFiles(event.data.files, uploadFolder(), $('uploadDialog').open, event.data.rejected);
  }
});
window.addEventListener('dragend', hideFileDrop, true);
window.addEventListener('blur', hideFileDrop);
document.addEventListener('keydown', event => { if (event.key === 'Escape') hideFileDrop(); });
for (const menu of document.querySelectorAll('.toolbar-menu')) menu.addEventListener('toggle', () => {
  if (menu.open) for (const other of document.querySelectorAll('.toolbar-menu')) if (other !== menu) other.open = false;
});
document.addEventListener('click', event => { if (!event.target.closest('.toolbar-menu')) closeMenus(); else if (event.target.closest('.menu-panel a')) closeMenus(); });
document.addEventListener('keydown', event => { if (event.key === 'Escape') closeMenus(); });
document.addEventListener('table-source', event => {
  if (selected !== event.detail.jobId) return;
  SlideBrowser.selectPage(event.detail.page); changeView('preview');
  $('original').contentWindow?.postMessage({type:'docling-sheet-select', jobId:selected, number:event.detail.page}, '*');
  $('original').contentWindow?.postMessage({type:'docling-pdf-select', jobId:selected, number:event.detail.page}, '*');
});
LibraryBrowser.init(); applyRoute(); refresh(); setInterval(refresh, 2000);
