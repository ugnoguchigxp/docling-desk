'use strict';
// App-owned bridge; all scripts originating in the Quick Look document are removed.
(() => {
  let zoom = 1, lastSize = '', scheduled = false, contentReady = false, naturalReported = false, pendingZoom = null;
  function reportSize() {
    scheduled = false;
    // CSS zoom rounds text metrics. Feeding zoomed measurements back into Fit
    // causes a different ratio on identical reloads. Keep natural dimensions.
    if (!contentReady || zoom !== 1 || !document.documentElement.clientWidth || !document.documentElement.clientHeight) return;
    let width = 1, height = 1;
    for (const element of document.querySelectorAll('table.worksheet, img')) {
      const bounds = element.getBoundingClientRect();
      if (!bounds.width || !bounds.height) continue;
      width = Math.max(width, (bounds.right + window.scrollX) / zoom);
      height = Math.max(height, (bounds.bottom + window.scrollY) / zoom);
    }
    width = Math.ceil(width); height = Math.ceil(height);
    const size = `${width}:${height}`; if (size === lastSize) return;
    lastSize = size;
    parent.postMessage({type: 'docling-sheet-size', width, height}, '*');
    naturalReported = true;
    if (pendingZoom) {const state = pendingZoom; pendingZoom = null; applyZoom(state);}
  }
  function schedule() {if (!scheduled) {scheduled = true; requestAnimationFrame(reportSize);}}
  window.addEventListener('message', event => {
    if (event.source !== parent) return;
    if (event.data?.type === 'docling-sheet-state-request') {
      parent.postMessage({type:'docling-sheet-state', request:event.data.request, left:window.scrollX, top:window.scrollY}, '*'); return;
    }
    if (event.data?.type === 'docling-sheet-restore') {
      requestAnimationFrame(() => window.scrollTo(Number(event.data.left) || 0, Number(event.data.top) || 0)); return;
    }
    if (event.data?.type !== 'docling-sheet-zoom') return;
    const ratio = event.data.ratio;
    if (!Number.isFinite(ratio) || ratio <= 0 || ratio > 4) return;
    if (!naturalReported) {pendingZoom = event.data; return;}
    applyZoom(event.data);
  });
  function applyZoom(state) {
    zoom = state.ratio; document.documentElement.style.zoom = String(zoom);
    document.documentElement.style.overflow = state.fit ? 'hidden' : 'auto';
    document.body.style.overflow = state.fit ? 'hidden' : 'visible';
    if (state.fit) window.scrollTo(0, 0);
  }
  const observer = new ResizeObserver(schedule);
  for (const element of document.querySelectorAll('table.worksheet, img')) observer.observe(element);
  window.addEventListener('load', schedule); window.addEventListener('resize', schedule);
  Promise.all([document.fonts.ready, ...Array.from(document.images, image => image.decode().catch(() => {}))]).then(() => {
    contentReady = true; schedule();
  });
})();
