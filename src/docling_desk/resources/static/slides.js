'use strict';
// The active source slide uses one frame; the thumbnail rail uses cached tiny images.
window.SlideBrowser = (() => {
  const el = id => document.getElementById(id), minimumZoom = 0.1, maximumZoom = 4;
  let job = null, doc = null, controller = null, visible = false, page = null, requestedPage = 1;
  let fit = true, zoom = 1, ratio = 1, thumbnailsVisible = true, epoch = 0, loading = 0, queue = [], queued = new Set();
  let thumbnailButtons = [];
  let language = 'original';
  const resize = new ResizeObserver(() => scale()); resize.observe(el('slideStage'));
  window.addEventListener('resize', scale);
  const thumbnails = new IntersectionObserver(entries => {
    if (!visible || !thumbnailsVisible) return;
    for (const entry of entries) {
      const image = entry.target;
      if (entry.isIntersecting && image.dataset.url && !queued.has(image)) { queue.push(image); queued.add(image); }
    }
    loadThumbnails();
  }, {root:el('slideThumbnails'), rootMargin:'120px 0px'});

  function loadThumbnails() {
    while (visible && thumbnailsVisible && loading < 2 && queue.length) {
      const image = queue.shift(); queued.delete(image); if (!image.isConnected || !image.dataset.url) continue;
      const generation = epoch; loading++;
      const complete = success => {
        image.parentElement.classList.remove('loading');
        if (!success) image.parentElement.classList.add('failed');
        if (generation === epoch) { loading--; loadThumbnails(); }
      };
      image.addEventListener('load', () => complete(true), {once:true});
      image.addEventListener('error', () => complete(false), {once:true});
      image.src = image.dataset.url; delete image.dataset.url; thumbnails.unobserve(image);
    }
  }
  function observeThumbnails() {
    if (!visible || !thumbnailsVisible) return;
    for (const image of el('slideThumbnails').querySelectorAll('img[data-url]')) thumbnails.observe(image);
    loadThumbnails();
  }
  function buildThumbnails() {
    const fragment = document.createDocumentFragment(); thumbnailButtons = [];
    for (const slide of doc.slides) {
      const button = document.createElement('button'), box = document.createElement('span'), image = document.createElement('img'), number = document.createElement('span');
      button.type = 'button'; button.className = 'slide-thumbnail'; button.dataset.page = slide.number;
      button.setAttribute('aria-label', `スライド ${slide.number}`); button.setAttribute('aria-current', 'false');
      button.tabIndex = -1;
      box.className = 'thumbnail-image loading'; box.style.aspectRatio = `${slide.width}/${slide.height}`;
      image.alt = `スライド ${slide.number}のプレビュー`; image.decoding = 'async';
      image.dataset.url = `/api/jobs/${job.id}/slides/${slide.number}/thumbnail`;
      number.textContent = slide.number; box.append(image); button.append(box, number);
      button.addEventListener('click', () => selectPage(slide.number));
      const index = thumbnailButtons.length;
      button.addEventListener('keydown', event => {
        let next;
        if (event.key === 'ArrowDown') next = Math.min(thumbnailButtons.length - 1, index + 1);
        if (event.key === 'ArrowUp') next = Math.max(0, index - 1);
        if (event.key === 'Home') next = 0;
        if (event.key === 'End') next = thumbnailButtons.length - 1;
        if (next !== undefined) {
          event.preventDefault(); selectPage(Number(thumbnailButtons[next].dataset.page)); thumbnailButtons[next].focus();
        }
      });
      fragment.append(button); thumbnailButtons.push(button);
    }
    el('slideThumbnails').replaceChildren(fragment); observeThumbnails();
  }
  function scale() {
    if (!page || !visible || el('slides').hidden) return;
    const stage = el('slideStage'), surface = el('slideSurface'), canvas = el('slideCanvas');
    stage.style.overflow = fit ? 'hidden' : 'auto';
    const width = stage.clientWidth, height = stage.clientHeight; if (width <= 0 || height <= 0) return;
    ratio = fit ? Math.min(width / page.width, height / page.height) : zoom;
    const scaledWidth = page.width * ratio, scaledHeight = page.height * ratio;
    surface.style.width = `${scaledWidth}px`; surface.style.height = `${scaledHeight}px`;
    canvas.style.transform = `scale(${ratio})`;
    canvas.style.left = `${Math.max(0, (stage.clientWidth - scaledWidth) / 2)}px`;
    canvas.style.top = `${Math.max(0, (stage.clientHeight - scaledHeight) / 2)}px`;
    el('slideZoomPreset').options[0].textContent = `${Math.round(ratio * 100)}%`; el('slideZoomPreset').value = 'current';
    el('slideZoomOut').disabled = ratio <= minimumZoom; el('slideZoomIn').disabled = ratio >= maximumZoom;
    el('slideFit').setAttribute('aria-pressed', String(fit));
    if (fit) { stage.scrollLeft = 0; stage.scrollTop = 0; }
  }
  function setZoom(value) { if (!page || !visible) return; zoom = Math.min(maximumZoom, Math.max(minimumZoom, value)); fit = false; scale(); }
  function setJob(next) {
    controller?.abort(); controller = null; job = next; doc = null; page = null; requestedPage = 1;
    language = 'original';
    fit = true; zoom = ratio = 1; thumbnailsVisible = true; epoch++; loading = 0; queue = []; queued = new Set();
    thumbnails.disconnect(); thumbnailButtons = [];
    el('slidePicker').replaceChildren(); el('slideCanvas').replaceChildren(); el('slideThumbnails').replaceChildren();
    el('slideThumbnails').hidden = false; el('slideThumbnailsToggle').setAttribute('aria-pressed', 'true');
    el('slides').hidden = true; el('slideControls').hidden = true; el('previewStatus').textContent = ''; el('previewStatus').hidden = true;
  }
  function hide() {
    visible = false; thumbnails.disconnect(); queue = []; queued = new Set();
    el('slideControls').hidden = true; el('slideCanvas').replaceChildren();
  }
  function fallback(message) {
    el('slides').hidden = true; el('slideControls').hidden = true;
    el('original').hidden = !job?.preview;
    if (job?.preview) el('original').src = `/files/${job.id}/${job.preview}`;
    el('previewStatus').textContent = message; el('previewStatus').hidden = false;
  }
  function render() {
    page = doc?.slides.find(slide => slide.number === requestedPage);
    if (!page?.preview) { fallback('スライド別表示を取得できないため、文書全体のプレビューを表示します。'); return; }
    el('slidePicker').value = page.number;
    el('original').hidden = true; el('original').src = 'about:blank'; el('slides').hidden = false; el('slideControls').hidden = false;
    el('slideStage').scrollLeft = 0; el('slideStage').scrollTop = 0;
    const canvas = el('slideCanvas'), frame = document.createElement('iframe');
    canvas.replaceChildren(); canvas.style.width = `${page.width}px`; canvas.style.height = `${page.height}px`;
    frame.title = `原本プレビュー・スライド ${page.number}`; frame.setAttribute('sandbox', '');
    frame.src = `/files/${job.id}/${page.preview}`; frame.style.width = `${page.width}px`; frame.style.height = `${page.height}px`;
    canvas.append(frame); el('originalOpen').href = frame.src;
    if (language !== 'original') { frame.src = `/view/${job.id}/slides/${page.number}?language=${language}`; el('originalOpen').href = frame.src; }
    document.dispatchEvent(new CustomEvent('slide-selected', {detail:{jobId:job.id, number:page.number}}));
    el('previewStatus').textContent = ''; el('previewStatus').hidden = true;
    const index = doc.slides.indexOf(page); el('slidePrevious').disabled = index === 0; el('slideNext').disabled = index === doc.slides.length - 1;
    for (const button of thumbnailButtons) {
      const selected = Number(button.dataset.page) === page.number;
      button.setAttribute('aria-current', String(selected)); button.tabIndex = selected ? 0 : -1;
    }
    const current = thumbnailButtons[index];
    if (thumbnailsVisible && current) current.scrollIntoView({block:'nearest', inline:'nearest'});
    observeThumbnails(); scale();
  }
  async function show() {
    visible = true;
    if (!job?.slide_layout) { el('slideControls').hidden = true; return; }
    if (doc) { render(); return; } if (controller) return;
    const id = job.id, request = new AbortController(); controller = request;
    el('previewStatus').textContent = 'スライドを読み込んでいます…'; el('previewStatus').hidden = false;
    try {
      const response = await fetch(`/files/${id}/slides.json`, {signal:request.signal, cache:'no-store'});
      if (!response.ok) throw new Error('スライド別表示を読み込めませんでした。文書全体のプレビューを表示します。');
      const data = await response.json(); if (job.id !== id || controller !== request) return; doc = data;
      for (const slide of doc.slides) {
        const option = document.createElement('option'); option.value = slide.number; option.textContent = `${slide.number} / ${doc.slides.length}`; el('slidePicker').append(option);
      }
      buildThumbnails(); if (visible) render();
    } catch (error) { if (error.name !== 'AbortError' && job.id === id && visible) fallback(error.message); }
    finally { if (controller === request) controller = null; }
  }
  function selectPage(number) { requestedPage = number; if (visible && doc) render(); }
  function move(step) { const index = doc?.slides.findIndex(slide => slide.number === requestedPage), next = doc?.slides[index + step]; if (next) selectPage(next.number); }
  el('slidePicker').addEventListener('change', () => selectPage(Number(el('slidePicker').value)));
  el('slidePrevious').addEventListener('click', () => move(-1)); el('slideNext').addEventListener('click', () => move(1));
  el('slideZoomOut').addEventListener('click', () => setZoom(ratio / 1.25)); el('slideZoomIn').addEventListener('click', () => setZoom(ratio * 1.25));
  el('slideZoomPreset').addEventListener('change', () => { if (el('slideZoomPreset').value !== 'current') setZoom(Number(el('slideZoomPreset').value)); });
  el('slideFit').addEventListener('click', () => { fit = true; scale(); });
  el('slideThumbnailsToggle').addEventListener('click', () => {
    thumbnailsVisible = !thumbnailsVisible; el('slideThumbnails').hidden = !thumbnailsVisible;
    el('slideThumbnailsToggle').setAttribute('aria-pressed', String(thumbnailsVisible));
    if (thumbnailsVisible) observeThumbnails(); else { thumbnails.disconnect(); queue = []; queued = new Set(); }
    scale();
  });
  el('slideStage').addEventListener('keydown', event => {
    if (event.target !== el('slideStage') || !fit) return;
    if (event.key === 'ArrowLeft') { event.preventDefault(); move(-1); }
    if (event.key === 'ArrowRight') { event.preventDefault(); move(1); }
  });
  function setLanguage(next, reload = false) {
    const changed = language !== next; language = next;
    if ((!changed && !reload) || !page || !visible) return;
    const stage = el('slideStage'), top = stage.scrollTop, left = stage.scrollLeft;
    const frame = el('slideCanvas').querySelector('iframe'); if (!frame) return;
    frame.src = language === 'original' ? `/files/${job.id}/${page.preview}` : `/view/${job.id}/slides/${page.number}?language=${language}`;
    el('originalOpen').href = frame.src;
    frame.addEventListener('load', () => { stage.scrollTop = top; stage.scrollLeft = left; }, {once:true});
  }
  return {setJob, show, hide, selectPage, setLanguage, current: () => ({jobId:job?.id, number:page?.number || null})};
})();
