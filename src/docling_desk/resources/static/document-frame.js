'use strict';
// Trusted, app-owned bridge. Source-document scripts never run in these frames.
(() => {
  const jobId = document.body.dataset.job;
  let scheduled = false, last = '', naturalWidth = 0;
  const send = data => parent.postMessage({jobId, ...data}, '*');
  function selectedText() {
    const selection = getSelection();
    return selection && !selection.isCollapsed ? selection.toString().slice(0, 2_000_000) : '';
  }
  function reportSelection() {
    scheduled = false;
    if (!document.body.dataset.unit) return;
    const text = selectedText();
    if (text === last) return;
    last = text; send({type:'docling-text-selection', unitId:document.body.dataset.unit, text});
  }
  document.addEventListener('selectionchange', () => {
    if (!scheduled) {scheduled = true; requestAnimationFrame(reportSelection);}
  });
  // A worksheet cell is a useful selection target even when its text is
  // surrounded by padding. Preserve native word/substring and drag selection.
  document.addEventListener('click', event => {
    if (!document.body.dataset.unit?.startsWith('sheet-') || event.detail !== 1 || event.shiftKey || event.metaKey || event.ctrlKey || event.altKey) return;
    const cell = event.target.closest?.('table.worksheet td');
    const selection = getSelection();
    if (!cell || event.target.closest('a,button,input,select,textarea') || !selection?.isCollapsed || !cell.textContent.trim()) return;
    const range = document.createRange();
    range.selectNodeContents(cell);
    selection.removeAllRanges(); selection.addRange(range);
    reportSelection();
  });
  window.addEventListener('message', event => {
    const d = event.data;
    if (!d || d.jobId !== jobId) return;
    if (event.source === parent) {
      if (d.type === 'docling-selection-clear') {
        last = '';
        getSelection()?.removeAllRanges();
        document.querySelectorAll('iframe').forEach(f => f.contentWindow.postMessage(d, '*'));
      }
      if (d.type === 'docling-document-request') reportDocument();
      if (d.type === 'docling-word-zoom' && naturalWidth && Number.isFinite(d.ratio) && d.ratio >= 0.1 && d.ratio <= 4) document.documentElement.style.zoom = String(d.ratio);
      return;
    }
    if (d.type !== 'docling-text-selection' || typeof d.unitId !== 'string' || typeof d.text !== 'string' || d.text.length > 2_000_000) return;
    if (![...document.querySelectorAll('iframe')].some(f => f.contentWindow === event.source && !f.closest('[hidden]'))) return;
    send({type:d.type, unitId:d.unitId, text:d.text});
  });
  document.addEventListener('keydown', event => {
    if (!document.body.dataset.unit?.startsWith('slide-') || event.shiftKey || event.metaKey || event.ctrlKey || event.altKey || selectedText()) return;
    if (['ArrowLeft','ArrowRight'].includes(event.key)) {
      event.preventDefault(); send({type:'docling-slide-key', key:event.key});
    }
  });
  function reportDocument() {
    if (!document.body.dataset.unit?.startsWith('document-')) return;
    if (!naturalWidth) {
      naturalWidth = Math.max(0, ...[...document.body.children].filter(e => !['STYLE','SCRIPT','LINK'].includes(e.tagName)).map(e => Math.max(e.scrollWidth, e.getBoundingClientRect().width)));
    }
    // A document loaded under a hidden React tab has no measurable layout yet.
    if (!naturalWidth) return;
    send({type:'docling-document-size', width:naturalWidth});
    send({type:'docling-unit-current', number:1});
  }
  Promise.all([document.fonts.ready, ...[...document.images].map(img => img.decode().catch(() => {}))]).then(reportDocument);
})();
