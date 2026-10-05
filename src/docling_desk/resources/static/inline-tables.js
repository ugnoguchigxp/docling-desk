'use strict';
// Runs only around allowlisted document markup in the sandboxed viewer.
(() => {
  const payload = JSON.parse(document.getElementById('table-data').textContent);
  const byRef = new Map(payload.tables.map((table, index) => [table.ref, {table, index}]));
  const observer = new IntersectionObserver(entries => {
    for (const entry of entries) if (entry.isIntersecting) {
      entry.target.dispatchEvent(new Event('inline-activate')); observer.unobserve(entry.target);
    }
  }, {rootMargin: '300px'});
  for (const source of document.querySelectorAll('table[data-docling-ref]')) {
    const projected = byRef.get(source.dataset.doclingRef); if (!projected) continue;
    const section = document.createElement('div'); section.className = 'inline-table'; source.before(section);
    // Keep the original table (including merged cells) intact, in the same document location.
    section.append(source);
    section.addEventListener('inline-activate', () => {
      TableTools.createView(section, {...projected, jobId: payload.jobId, original: source,
        onExpand: expanded => {if (parent !== window) parent.postMessage({type: 'docling-table-focus', expanded}, '*');}});
    }, {once: true}); observer.observe(section);
  }
})();
