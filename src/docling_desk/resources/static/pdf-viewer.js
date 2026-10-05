'use strict';
(() => {
  const el = id => document.getElementById(id), stage = el('pdfStage'), container = el('pdfPages');
  const pages = [...document.querySelectorAll('.pdf-page')], jobId = document.body.dataset.job;
  let mode = 'width', zoom = 1, current = 1, frame = null, language = 'original', revision = '';
  const visible = new Set(), minimum = 0.1, maximum = 4;
  const rail = el('pdfThumbnails'), thumbButtons = [...rail.querySelectorAll('.pdf-thumbnail')];
  let thumbnailLoading = 0, thumbnailQueue = [], lastHighlighted = 0;
  const thumbnailQueued = new Set();
  function loadThumbnails() {
    while (!rail.hidden && thumbnailLoading < 2 && thumbnailQueue.length) {
      const image = thumbnailQueue.shift(); thumbnailQueued.delete(image);
      if (!image.dataset.url) continue;
      thumbnailLoading++;
      const complete = success => {
        image.parentElement.classList.toggle('failed', !success);
        thumbnailLoading--; loadThumbnails();
      };
      image.addEventListener('load', () => complete(true), {once:true});
      image.addEventListener('error', () => complete(false), {once:true});
      image.src = image.dataset.url; delete image.dataset.url; thumbnailObserver.unobserve(image);
    }
  }
  const thumbnailObserver = new IntersectionObserver(entries => {
    if (rail.hidden) return;
    for (const entry of entries) {
      const image = entry.target;
      if (entry.isIntersecting && image.dataset.url && !thumbnailQueued.has(image)) {
        thumbnailQueued.add(image); thumbnailQueue.push(image);
      }
    }
    loadThumbnails();
  }, {root:rail, rootMargin:'120px 0px'});
  function observeThumbnails() {
    rail.querySelectorAll('img[data-url]').forEach(image => thumbnailObserver.observe(image));
    loadThumbnails();
  }
  thumbButtons.forEach((button, index) => {
    button.addEventListener('click', () => selectPage(Number(button.dataset.number)));
    button.addEventListener('keydown', event => {
      let next;
      if (event.key === 'ArrowDown') next = Math.min(thumbButtons.length - 1, index + 1);
      if (event.key === 'ArrowUp') next = Math.max(0, index - 1);
      if (event.key === 'Home') next = 0;
      if (event.key === 'End') next = thumbButtons.length - 1;
      if (next !== undefined) {
        event.preventDefault(); selectPage(Number(thumbButtons[next].dataset.number)); thumbButtons[next].focus();
      }
    });
  });
  el('pdfThumbnailsToggle').addEventListener('click', () => {
    rail.hidden = !rail.hidden;
    el('pdfThumbnailsToggle').setAttribute('aria-pressed', String(!rail.hidden));
    if (rail.hidden) { thumbnailObserver.disconnect(); thumbnailQueue = []; thumbnailQueued.clear(); }
    else { observeThumbnails(); thumbButtons[current - 1]?.scrollIntoView({block:'nearest'}); }
    layout();
  });
  function ratio(page) {
    if (mode === 'manual') return zoom;
    const padding = parseFloat(getComputedStyle(container).paddingLeft) * 2;
    const width = Math.max(1, stage.clientWidth - padding) / Number(page.dataset.width);
    return mode === 'page' ? Math.min(width, Math.max(1, stage.clientHeight - 36) / Number(page.dataset.height)) : width;
  }
  function controls() {
    parent.postMessage({type:'docling-unit-current', jobId, number:current}, '*');
    const percentage = Math.round(ratio(pages[current - 1]) * 100);
    el('pdfPage').value = current;
    el('pdfPrevious').disabled = current === 1; el('pdfNext').disabled = current === pages.length;
    el('pdfZoom').options[0].textContent = `${percentage}%`;
    el('pdfZoom').value = 'current';
    el('pdfZoomOut').disabled = ratio(pages[current - 1]) <= minimum;
    el('pdfZoomIn').disabled = ratio(pages[current - 1]) >= maximum;
    el('pdfWidth').setAttribute('aria-pressed', String(mode === 'width'));
    el('pdfFit').setAttribute('aria-pressed', String(mode === 'page'));
    if (current !== lastHighlighted) {
      thumbButtons.forEach(button => {
        const selected = Number(button.dataset.number) === current;
        button.setAttribute('aria-current', String(selected)); button.tabIndex = selected ? 0 : -1;
      });
      if (!rail.hidden) thumbButtons[current - 1]?.scrollIntoView({block:'nearest', inline:'nearest'});
      lastHighlighted = current;
    }
  }
  function renderVisible() {
    for (const page of visible) {
      const frame = page.querySelector('iframe');
      const url = `${frame.dataset.url}?language=${language}&revision=${encodeURIComponent(revision)}`;
      if (frame.getAttribute('src') !== url) frame.src = url;
    }
  }
  function layout(preserve = true) {
    const anchor = pages[current - 1], oldHeight = anchor.offsetHeight;
    const offset = preserve && oldHeight ? (stage.scrollTop - anchor.offsetTop) / oldHeight : 0;
    const horizontal = stage.scrollWidth ? (stage.scrollLeft + stage.clientWidth / 2) / stage.scrollWidth : 0.5;
    let width = 0;
    for (const page of pages) {
      const scale = ratio(page), w = Number(page.dataset.width) * scale;
      page.style.width = `${w}px`; page.style.height = `${Number(page.dataset.height) * scale}px`;
      width = Math.max(width, w);
    }
    const padding = parseFloat(getComputedStyle(container).paddingLeft) * 2;
    container.style.width = `${width + padding}px`;
    if (preserve) stage.scrollTop = anchor.offsetTop + offset * anchor.offsetHeight;
    stage.scrollLeft = mode === 'manual' ? horizontal * stage.scrollWidth - stage.clientWidth / 2 : 0;
    controls(); renderVisible();
  }
  function selectPage(number) {
    if (!Number.isInteger(number) || number < 1 || number > pages.length) { controls(); return; }
    current = number; stage.scrollTop = Math.max(0, pages[number - 1].offsetTop - 18); controls();
  }
  function changeZoom(value) { zoom = Math.min(maximum, Math.max(minimum, value)); mode = 'manual'; layout(); }
  function fit(next) { mode = next; layout(false); selectPage(current); }
  el('pdfPrevious').addEventListener('click', () => selectPage(current - 1));
  el('pdfNext').addEventListener('click', () => selectPage(current + 1));
  el('pdfPage').addEventListener('change', () => selectPage(Number(el('pdfPage').value)));
  el('pdfPage').addEventListener('keydown', event => { if (event.key === 'Enter') selectPage(Number(el('pdfPage').value)); });
  el('pdfZoomOut').addEventListener('click', () => changeZoom(ratio(pages[current - 1]) / 1.25));
  el('pdfZoomIn').addEventListener('click', () => changeZoom(ratio(pages[current - 1]) * 1.25));
  el('pdfZoom').addEventListener('change', () => { if (el('pdfZoom').value !== 'current') changeZoom(Number(el('pdfZoom').value) / 100); });
  el('pdfWidth').addEventListener('click', () => fit('width'));
  el('pdfFit').addEventListener('click', () => fit('page'));
  stage.addEventListener('scroll', () => {
    if (frame !== null) return;
    frame = requestAnimationFrame(() => {
      frame = null;
      // Scroll animation frames can run after the parent becomes visible but
      // before ResizeObserver restores its page. Keep the selection until
      // that restoration has finished, even if geometry is visible again.
      if (collapsed || !stage.clientWidth || !stage.clientHeight) return;
      const top = stage.scrollTop, bottom = top + stage.clientHeight;
      let best = 0;
      for (const page of pages) {
        const overlap = Math.max(0, Math.min(bottom, page.offsetTop + page.offsetHeight) - Math.max(top, page.offsetTop));
        if (overlap > best) { best = overlap; current = Number(page.dataset.number); }
      }
      controls();
    });
  });
  stage.addEventListener('keydown', event => {
    if (event.key === '+' || event.key === '=') { event.preventDefault(); changeZoom(ratio(pages[current - 1]) * 1.25); }
    if (event.key === '-') { event.preventDefault(); changeZoom(ratio(pages[current - 1]) / 1.25); }
    if (event.key === 'Home') { event.preventDefault(); selectPage(1); }
    if (event.key === 'End') { event.preventDefault(); selectPage(pages.length); }
  });
  window.addEventListener('message', event => {
    if (event.source === parent && event.data?.jobId === jobId && event.data.type === 'docling-unit-request') parent.postMessage({type:'docling-unit-current', jobId, number:current}, '*');
    if (event.source === parent && event.data?.jobId === jobId && event.data.type === 'docling-translation-language' && ['original','en','ja'].includes(event.data.language)) {
      language = event.data.language; revision = event.data.revision || ''; renderVisible(); return;
    }
    if (event.source !== parent || event.data?.type !== 'docling-pdf-select' || event.data.jobId !== jobId) return;
    selectPage(Number(event.data.number));
  });
  const observer = new IntersectionObserver(entries => {
    for (const entry of entries) { if (entry.isIntersecting) visible.add(entry.target); else visible.delete(entry.target); }
    renderVisible();
  }, {root: stage, rootMargin: '300px'});
  if (!pages.length) return;
  layout(false); pages.forEach(page => observer.observe(page));
  observeThumbnails();
  let collapsed = false;
  new ResizeObserver(() => {
    if (!stage.clientWidth || !stage.clientHeight) { collapsed = true; return; }
    if (collapsed) {
      collapsed = false; layout(false); selectPage(current);
    } else layout();
  }).observe(stage);
})();
