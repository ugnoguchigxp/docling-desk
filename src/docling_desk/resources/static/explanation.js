'use strict';
window.ExplanationBrowser = (() => {
  const el = id => document.getElementById(id), names = {page:'ページ',slide:'スライド',sheet:'シート'};
  const activeStates = ['queued','running'];
  const webNames = {success:'本文を確認済み',partial:'一部を確認済み',failed:'調査に失敗',no_results:'該当情報なし',no_usable_evidence:'本文を確認できませんでした',skipped_sensitive:'検索語を送信せず省略',disabled_by_policy:'設定により無効',not_needed:'追加調査なし'};
  let job = null, current = null, data = null, opened = false, busy = false, timer = null, epoch = 0, rendered = null, refreshing = null, refreshAgain = false, lastError = '', refreshError = '';
  async function request(url, options) {
    const controller = new AbortController(), timeout = setTimeout(() => controller.abort(), 30000);
    try {
      const response = await fetch(url, {...options, cache:'no-store', signal:controller.signal}), value = await response.json();
      if (!response.ok) throw new Error(value.detail || `HTTP ${response.status}`); return value;
    } catch(error) { if (error.name === 'AbortError') throw new Error('応答を確認できませんでした。接続が戻れば状態の確認を続けます。'); throw error; }
    finally { clearTimeout(timeout); }
  }
  function unit() { return data?.units.find(u => u.number === current); }
  function visible(show) {
    if (show && !opened) rendered = null;
    opened = show; el('explanationPanel').hidden = !show; el('preview').classList.toggle('explanation-open', show);
    el('explainOpen').setAttribute('aria-expanded', String(show)); window.dispatchEvent(new Event('resize'));
  }
  function text(tag, value, parent) { const node = document.createElement(tag); node.textContent = value; parent.append(node); return node; }
  function refs(sourceIds, evidenceIds, value, parent) {
    const row = document.createElement('div'); row.className = 'explanation-refs';
    for (const id of new Set(evidenceIds)) {
      const item = value.web_search.evidence.find(e => e.id === id && e.kind === 'web');
      if (!item) continue;
      try {
        const url = new URL(item.url); if (!['http:','https:'].includes(url.protocol)) continue;
        const link = text('a', item.title || '補足を詳しく読む', row);
        link.href = url.href; link.target = '_blank'; link.rel = 'noopener noreferrer';
      } catch { /* Ignore invalid saved URLs. */ }
    }
    if (row.childElementCount) parent.append(row);
  }
  function renderValue(value, canJump) {
    const root = el('explanationText'); root.replaceChildren();
    text('p', `作成: ${new Date(value.created_at).toLocaleString('ja-JP')} · Web: ${webNames[value.web_search.status] || value.web_search.status}`, root);
    for (const section of value.explanation.sections) { text('h3', section.title || '詳しい解説', root); text('p', section.text, root); }
    if (value.explanation.glossary.length) { text('h3','用語の説明',root); for (const term of value.explanation.glossary) { text('h4',term.term,root); text('p',term.definition,root); } }
    if (value.explanation.supplements.length) { text('h3','Webからの補足',root); for (const supplement of value.explanation.supplements) { text('h4',supplement.title,root); text('p',supplement.text,root); refs([],supplement.evidence_ids,value,root); } }
    for (const table of value.source.tables) {
      const detail = document.createElement('details'); root.append(detail); text('summary', `${table.label} · 元の表を確認`, detail);
      const box = document.createElement('div'); box.className = 'explanation-table-scroll'; detail.append(box);
      const grid = document.createElement('table'); grid.setAttribute('aria-label',table.label); box.append(grid);
      for (const [index,cells] of table.rows.entries()) { const row = document.createElement('tr'); grid.append(row); for (const cell of cells) text(index < table.header_rows ? 'th' : 'td',cell,row); }
      if (table.merged) text('p','結合セルの範囲は原本と保存JSONで確認できます。',detail);
    }
    if (!canJump) { const saved = document.createElement('details'); saved.id = 'explanationSavedSource'; root.append(saved); text('summary','保存時の原文を確認',saved); for (const block of value.source.blocks) text('p',block.text,saved); }
    text('h3','対象と制約',root);
    for (const limitation of value.explanation.limitations) text('p',limitation,root);
    text('p', `図 ${value.source.excluded_pictures}件は対象外です。読む位置を特定できなかった本文・表: ${value.unlocated_count}件。${value.extraction_state === 'partial' ? '原文の抽出は部分成功です。' : ''}`,root);
  }
  function downloads(entry) {
    document.querySelectorAll('.explanation-download').forEach(n => n.remove());
    if (!entry?.available) return;
    for (const [format,label] of [['markdown','解説を保存（Markdown）'],['json','解説と根拠を保存（JSON）']]) {
      const link = document.createElement('a'); link.className = 'explanation-download'; link.textContent = label;
      link.href = `/api/jobs/${job.id}/explanations/${entry.id}?download=${format}`; el('downloads').append(link);
    }
  }
  async function render() {
    const entry = unit(); downloads(entry);
    el('explainOpen').disabled = !entry || busy || (!data?.enabled && !entry.available);
    el('explainOpen').title = entry ? `現在の${names[entry.kind]} ${entry.number}の解説` : '現在のページ・スライド・シートを確認しています。';
    if (!opened) return;
    el('explanationTitle').textContent = entry ? `${names[entry.kind]} ${entry.number}${entry.name ? ` · ${entry.name}` : ''} の解説` : '現在の範囲を確認しています';
    el('explainRetry').hidden = !entry || entry.available || activeStates.includes(entry.state.state) || (entry.state.state === 'not_applicable' && !entry.stale) || !data?.enabled || !!entry.storage_error;
    el('explainRegenerate').hidden = !entry?.available || activeStates.includes(entry.state.state) || !data?.enabled || !!entry.storage_error;
    el('explainRetry').disabled = el('explainRegenerate').disabled = busy || !!data?.source_error || !!data?.configuration_error;
    const issues = entry?.state.state === 'failed' ? (entry.state.review_issues || []).slice(0,5).join('\n') : '';
    el('explanationStatus').textContent = [entry?.state.stage, entry?.state.error, issues, entry?.warning, lastError, refreshError,
      entry?.stale ? (entry.available ? '原文が更新されています。保存時の解説を表示します。' : '原文が更新されています。再試行できます。') : '', data?.source_error, data?.configuration_error].filter(Boolean).join('\n');
    const key = entry ? `${job.id}:${entry.id}:${entry.state.latest_version_id}` : '';
    if (key === rendered) return; rendered = key; el('explanationText').replaceChildren();
    if (!entry?.available) return;
    const generation = epoch, id = job.id;
    try { const value = await request(`/api/jobs/${id}/explanations/${entry.id}`); if (generation === epoch && key === rendered && opened && value.result) renderValue(value.result, value.source_match === true); }
    catch(error) { if (generation === epoch && key === rendered) { rendered = null; el('explanationStatus').textContent = error.message; } }
  }
  function refresh() {
    if (!job) return Promise.resolve();
    refreshAgain = true;
    if (refreshing) return refreshing;
    clearTimeout(timer); timer = null;
    refreshing = (async () => {
      do {
        refreshAgain = false;
        const generation = epoch, id = job.id;
        try {
          const next = await request(`/api/jobs/${id}/explanations`);
          if (generation !== epoch) { refreshAgain = !!job; continue; }
          data = next; refreshError = ''; const service = next.profile.web_provider || '未設定';
          el('explanationDisclosure').textContent = `未作成の解説は本文・表をOpenAIへ送信します。Web補足: ${service === 'disabled' ? '無効' : service + 'で一般的な用語を検索'}。保存版は再生成せず開きます。`;
          await render();
        } catch(error) {
          if (generation === epoch) { refreshError = `状態の確認を再試行しています。${error.message}`; await render(); }
          else refreshAgain = !!job;
        }
      } while (refreshAgain && job);
    })().finally(() => {
      refreshing = null;
      if (job && (opened || refreshError || data?.units.some(u => activeStates.includes(u.state.state)))) timer = setTimeout(refresh, 1500);
    });
    return refreshing;
  }
  function setJob(next) {
    if (job?.id === next?.id && job?.state === next?.state) { job = next; return; }
    epoch++; clearTimeout(timer); timer = null; job = next; lastError = ''; refreshError = ''; current = null; data = null; rendered = null; visible(false);
    el('explainOpen').disabled = true; el('explanationDisclosure').hidden = !next || !['success','partial'].includes(next.state);
    document.querySelectorAll('.explanation-download').forEach(n => n.remove());
    if (next && ['success','partial'].includes(next.state)) {
      const position = window.SlideBrowser?.current(); current = position?.jobId === next.id ? position.number : null; refresh();
      el('original').contentWindow?.postMessage({type:'docling-unit-request',jobId:next.id},'*');
    }
  }
  function position(number) { if (!Number.isInteger(number) || number < 1 || number === current) return; current = number; lastError = ''; rendered = null; render(); }
  async function generate(force = false) {
    const entry = unit(); if (!entry || busy || !data?.enabled || data.source_error || data.configuration_error) return;
    const generation = epoch, id = job.id; busy = true; lastError = ''; await render();
    try { const accepted = await request(`/api/jobs/${id}/explanations`, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({unit_id:entry.id,force})}); if (generation === epoch) { entry.state = accepted.state; rendered = null; await render(); await refresh(); } }
    catch(error) { if (generation === epoch) { lastError = error.message; el('explanationStatus').textContent = error.message; } }
    finally { busy = false; render(); }
  }
  el('explainOpen').addEventListener('click',async () => { visible(true); el('explanationPanel').focus(); await refresh(); const entry = unit(); if (entry && !entry.available && entry.state.state === 'uncreated') await generate(); });
  el('explainClose').addEventListener('click',() => { visible(false); el('explainOpen').focus(); });
  el('explainRetry').addEventListener('click',() => generate()); el('explainRegenerate').addEventListener('click',() => generate(true));
  el('explanationPanel').addEventListener('keydown',event => { if (event.key === 'Escape') { visible(false); el('explainOpen').focus(); } });
  document.addEventListener('slide-selected',event => { if (job?.id === event.detail.jobId) position(event.detail.number); });
  window.addEventListener('message',event => { if (event.source === el('original').contentWindow && event.data?.type === 'docling-unit-current' && event.data.jobId === job?.id) position(event.data.number); });
  el('original').addEventListener('load',() => { if (job) el('original').contentWindow?.postMessage({type:'docling-unit-request',jobId:job.id},'*'); });
  window.addEventListener('online', () => { if (job) refresh(); });
  if (typeof currentJobs !== 'undefined' && typeof selected !== 'undefined') setJob(currentJobs.find(j => j.id === selected) || null);
  return {setJob};
})();
