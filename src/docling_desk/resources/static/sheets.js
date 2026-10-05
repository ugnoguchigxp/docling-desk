'use strict';
(() => {
  const {jobId, sheets} = JSON.parse(document.getElementById('sheetData').textContent);
  const stage = document.getElementById('sheetStage'), tabs = document.getElementById('sheetTabs');
  const frames = new Map(), buttons = new Map(), sizes = new Map();
  const snapshots = new Map();
  let language = 'original', revision = '', requestNumber = 0;
  function viewKey() { return `${language}:${revision}`; }
  function updateFrame(frame) {
    if (frame.dataset.view === viewKey()) return;
    frame.dataset.request = String(++requestNumber);
    frame.contentWindow?.postMessage({type:'docling-sheet-state-request', request:frame.dataset.request}, '*');
  }
  let selected = sheets[0].number, fit = true, ratio = 1, zoom = 1;
  const minimumZoom = 0.1, maximumZoom = 4;
  const out = document.getElementById('sheetZoomOut'), level = document.getElementById('sheetZoomLevel');
  const inside = document.getElementById('sheetZoomIn'), fitButton = document.getElementById('sheetFit');
  function scale() {
    const frame = frames.get(selected), size = sizes.get(selected);
    if (!frame || !size || !stage.clientWidth || !stage.clientHeight) return;
    ratio = fit ? Math.min(maximumZoom, stage.clientWidth / size.width, stage.clientHeight / size.height) : zoom;
    frame.contentWindow.postMessage({type: 'docling-sheet-zoom', ratio, fit}, '*');
    level.textContent = `${Math.round(ratio * 100)}%`;
    out.disabled = ratio <= minimumZoom; inside.disabled = ratio >= maximumZoom;
    fitButton.setAttribute('aria-pressed', String(fit));
  }
  function changeZoom(factor) {
    if (!sizes.has(selected)) return;
    zoom = Math.min(maximumZoom, Math.max(minimumZoom, ratio * factor)); fit = false; scale();
  }
  out.addEventListener('click', () => changeZoom(1 / 1.25));
  inside.addEventListener('click', () => changeZoom(1.25));
  document.getElementById('sheetActualSize').addEventListener('click', () => {zoom = 1; fit = false; scale();});
  fitButton.addEventListener('click', () => {fit = true; scale();});
  new ResizeObserver(scale).observe(stage);
  function select(number, focus = false) {
    const sheet = sheets.find(s => s.number === number); if (!sheet) return;
    selected = number;
    if (!frames.has(number)) {
      const frame = document.createElement('iframe'); frame.id = `sheet-${number}`;
      frame.title = `${sheet.name}・原本プレビュー`; frame.setAttribute('sandbox', 'allow-scripts');
      frame.setAttribute('role', 'tabpanel'); frame.setAttribute('aria-labelledby', `sheet-tab-${number}`);
      frame.src = `/view/${jobId}/sheets/${number}`;
      frame.dataset.view = 'original:';
      frame.addEventListener('load', () => {
        const state = snapshots.get(number);
        if (state) frame.contentWindow.postMessage({type:'docling-sheet-restore', ...state, ratio, fit}, '*');
        updateFrame(frame); if (selected === number) scale();
      });
      frames.set(number, frame); stage.append(frame);
    }
    for (const [id, frame] of frames) frame.hidden = id !== number;
    for (const [id, button] of buttons) {
      button.setAttribute('aria-selected', String(id === number)); button.tabIndex = id === number ? 0 : -1;
    }
    scale();
    parent.postMessage({type:'docling-unit-current', jobId, number:selected}, '*');
    if (focus) {buttons.get(number).focus(); buttons.get(number).scrollIntoView({block: 'nearest', inline: 'nearest'});}
  }
  sheets.forEach((sheet, index) => {
    const button = document.createElement('button'); button.type = 'button'; button.textContent = sheet.name;
    button.id = `sheet-tab-${sheet.number}`; button.setAttribute('role', 'tab'); button.setAttribute('aria-controls', `sheet-${sheet.number}`);
    button.addEventListener('click', () => select(sheet.number));
    button.addEventListener('keydown', event => {
      let next;
      if (event.key === 'ArrowRight') next = (index + 1) % sheets.length;
      if (event.key === 'ArrowLeft') next = (index + sheets.length - 1) % sheets.length;
      if (event.key === 'Home') next = 0;
      if (event.key === 'End') next = sheets.length - 1;
      if (next !== undefined) {event.preventDefault(); select(sheets[next].number, true);}
    });
    buttons.set(sheet.number, button); tabs.append(button);
  });
  window.addEventListener('message', event => {
    if (event.source === parent && event.data?.jobId === jobId && event.data.type === 'docling-unit-request') parent.postMessage({type:'docling-unit-current', jobId, number:selected}, '*');
    if (event.data?.type === 'docling-sheet-state') {
      const entry = [...frames].find(([, frame]) => frame.contentWindow === event.source);
      if (!entry || event.data.request !== entry[1].dataset.request || entry[1].dataset.view === viewKey()) return;
      snapshots.set(entry[0], {left:event.data.left, top:event.data.top});
      entry[1].dataset.view = viewKey();
      entry[1].src = `/view/${jobId}/sheets/${entry[0]}?language=${language}`;
      return;
    }
    if (event.data?.type === 'docling-sheet-size') {
      const entry = [...frames].find(([, frame]) => frame.contentWindow === event.source);
      const {width, height} = event.data;
      if (entry && Number.isFinite(width) && Number.isFinite(height) && width > 0 && height > 0) {
        sizes.set(entry[0], {width, height}); if (entry[0] === selected) scale();
      }
      return;
    }
    if (event.source !== parent || event.data?.jobId !== jobId) return;
    if (event.data.type === 'docling-sheet-select') select(event.data.number);
    if (event.data.type === 'docling-translation-language' && ['original','en','ja'].includes(event.data.language)) {
      language = event.data.language; revision = event.data.revision || '';
      for (const frame of frames.values()) updateFrame(frame);
      parent.postMessage({type:'docling-unit-current', jobId, number:selected}, '*');
    }
  });
  select(sheets[0].number);
})();
