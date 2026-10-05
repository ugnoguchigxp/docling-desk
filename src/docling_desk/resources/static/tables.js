'use strict';
window.TableBrowser = (() => {
  const el = id => document.getElementById(id);
  let job = null, tables = null, controller = null, visible = false;
  const views = new Map();
  function setJob(next) {
    controller?.abort(); controller = null;
    for (const view of views.values()) view.destroy(); views.clear();
    job = next; tables = null; el('tablePicker').replaceChildren(); el('tableGrid').replaceChildren();
    el('tableStatus').textContent = '';
  }
  function hide() {visible = false; for (const view of views.values()) view.close();}
  function select() {
    const index = Number(el('tablePicker').value), table = tables?.[index]; if (!table) return;
    for (const child of el('tableGrid').children) child.hidden = Number(child.dataset.index) !== index;
    if (!views.has(index)) {
      const host = document.createElement('div'); host.dataset.index = index; el('tableGrid').append(host);
      views.set(index, TableTools.createView(host, {table, index, jobId: job.id,
        onSource: () => {if (table.pages.length) document.dispatchEvent(new CustomEvent('table-source', {detail: {jobId: job.id, page: table.pages[0]}}));}}));
    } else views.get(index).redraw();
  }
  async function show() {
    visible = true;
    if (tables) {select(); return;}
    if (controller || !job || !['success', 'partial'].includes(job.state)) return;
    const current = job.id, request = new AbortController(); controller = request; el('tableStatus').textContent = '抽出済みの表を読み込んでいます…';
    try {
      const response = await fetch(`/api/jobs/${current}/tables`, {signal: request.signal});
      if (!response.ok) throw new Error('表を読み込めませんでした。');
      const data = await response.json(); if (job.id !== current || controller !== request) return; tables = data;
      for (let i = 0; i < tables.length; i++) {
        const table = tables[i], option = document.createElement('option'); option.value = i;
        option.textContent = `${table.source_label} / ${table.label}（${table.rows.length}行 × ${table.columns}列）`; el('tablePicker').append(option);
      }
      el('tableStatus').textContent = tables.length ? '' : 'この文書から抽出された表はありません。';
      if (tables.length) {el('tablePicker').value = tables.reduce((best, table, i) => table.rows.length * table.columns > tables[best].rows.length * tables[best].columns ? i : best, 0); if (visible) select();}
    } catch (error) {if (error.name !== 'AbortError' && job.id === current) el('tableStatus').textContent = error.message;}
    finally {if (controller === request) controller = null;}
  }
  el('tablePicker').addEventListener('change', select);
  return {setJob, show, hide};
})();
