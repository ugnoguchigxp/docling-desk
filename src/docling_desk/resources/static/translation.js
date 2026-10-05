'use strict';
window.TranslationBrowser = (() => {
  const el = id => document.getElementById(id), names = {slide:'スライド', sheet:'シート', page:'ページ'};
  const states = {queued:'順番待ち', waiting:'間隔を空けて待機中', running:'翻訳中', completed:'翻訳済み', failed:'失敗', interrupted:'中断', untranslated:'未翻訳'};
  const activeStates = ['queued','waiting','running'];
  let job = null, data = null, current = 1, language = 'original', timer = null, controller = null, generation = 0, rendered = '', panelKey = '', busy = false;
  const savedLanguages = new Map();
  async function fetchJSON(url, options = {}) {
    const response = await fetch(url, options), value = await response.json();
    if (!response.ok) throw new Error(value.detail || '翻訳データを取得できません。');
    return value;
  }
  function activeUnit() { return data?.units.find(unit => unit.number === current) || data?.units[0]; }
  function status(text) { el('translationStatus').textContent = text; el('translationStatus').hidden = !text; }
  function notifyFrame() {
    if (!job || job.filename.toLowerCase().endsWith('.pptx')) return;
    el('original').contentWindow?.postMessage({type:'docling-translation-language', jobId:job.id, language, revision:activeUnit()?.languages[language]?.result_created_at || ''}, '*');
  }
  function updateDownloads() {
    document.querySelectorAll('.translation-download').forEach(node => node.remove());
    if (!job || !data) return;
    for (const lang of ['en','ja']) for (const unit of data.units) if (unit.languages[lang].available) {
      const link = document.createElement('a'); link.className = 'translation-download';
      link.href = `/api/jobs/${job.id}/translations/${lang}/${unit.id}?download=true`;
      link.textContent = `${lang === 'en' ? '英訳' : '日本語訳'} ${names[unit.kind]} ${unit.number} JSON`; el('downloads').append(link);
    }
  }
  async function render() {
    const unit = activeUnit(); if (!job || !unit) return;
    const record = language === 'original' ? null : unit.languages[language];
    const warning = [];
    if (record) {
      if (record.stale) warning.push('原文が更新されています。再翻訳してください。');
      else if (!record.available) warning.push(`${names[unit.kind]} ${unit.number} は${states[record.state]}です。原文を表示しています。`);
      if (record.error) warning.push(record.error);
      if (unit.kind === 'slide') warning.push('サムネイルは原文です。');
      if (unit.excluded_count) warning.push(`対応を確定できない文字セル ${unit.excluded_count}件は原文のままです。`);
      if (record.available && !unit.segments_count) warning.push('翻訳対象の文字がありません。');
    }
    const active = data.units.filter(u => Object.values(u.languages).some(r => activeStates.includes(r.state))).length;
    if (active) warning.unshift(`翻訳処理中 · 残り ${active}単位`);
    const waiting = data.units.flatMap(u => Object.values(u.languages)).find(r => r.state === 'waiting');
    if (waiting) {
      const seconds = Math.max(0, Math.ceil((Date.parse(waiting.next_attempt_at) - Date.now()) / 1000));
      const reason = waiting.wait_reason === 'rate_limit' ? 'レート制限のため' : waiting.wait_reason === 'retry' ? '再試行まで' : '次の翻訳まで';
      warning.push(`${reason}約${seconds}秒待機します。`);
    }
    if (data.unlocated_count) warning.push(`ページ位置のない要素 ${data.unlocated_count}件は翻訳対象外です。`);
    status(warning.join(' '));
    el('translationPanel').hidden = true;
    const key = `${job.id}:${unit.id}:${language}:${record?.result_created_at || ''}:${record?.available}`;
    if (key === rendered) { if (record?.available && unit.mode === 'panel' && panelKey === key) el('translationPanel').hidden = false; return; }
    rendered = key;
    if (unit.kind === 'slide') SlideBrowser.setLanguage(language, true);
    else if (unit.kind === 'sheet' || unit.kind === 'page') notifyFrame();
    if (record?.available && unit.mode === 'panel') {
      const id = job.id, wanted = key;
      try {
        const value = await fetchJSON(`/api/jobs/${id}/translations/${language}/${unit.id}`);
        if (job?.id !== id || rendered !== wanted || !value.available) return;
        el('translationPanelTitle').textContent = `${names[unit.kind]} ${unit.number} · ${language === 'en' ? '英訳' : '日本語訳'}`;
        el('translationPanelText').replaceChildren();
        for (const text of value.texts || []) { const p = document.createElement('p'); p.textContent = text; el('translationPanelText').append(p); }
        panelKey = key;
        el('translationPanel').hidden = false;
      } catch (error) { if (job?.id === id && rendered === wanted) { rendered = ''; status(error.message); } }
    }
  }
  async function refresh() {
    if (!job || !['success','partial'].includes(job.state)) return;
    clearTimeout(timer); controller?.abort(); controller = new AbortController();
    const id = job.id, request = controller, epoch = generation;
    try {
      const value = await fetchJSON(`/api/jobs/${id}/translations`, {signal:request.signal});
      if (generation !== epoch || job?.id !== id) return;
      data = value; updateDownloads(); await render();
      if (value.units.some(u => Object.values(u.languages).some(r => activeStates.includes(r.state)))) timer = setTimeout(refresh, 1500);
    } catch (error) { if (error.name !== 'AbortError' && generation === epoch) status(error.message); }
  }
  function setJob(next) {
    if (job?.id === next?.id && job?.state === next?.state) return;
    controller?.abort(); clearTimeout(timer); generation++; rendered = ''; panelKey = ''; data = null; current = 1; job = next;
    language = savedLanguages.get(next?.id) || 'original'; el('translationLanguage').value = language;
    el('translationPanel').hidden = true; status('');
    el('translateOpen').disabled = !next || !['success','partial'].includes(next.state);
    if (next) refresh();
  }
  function selectLanguage(next) {
    if (!['original','en','ja'].includes(next)) return;
    language = next; if (job) savedLanguages.set(job.id, next);
    el('translationLanguage').value = next; rendered = ''; render(); refresh();
  }
  async function openDialog() {
    await refresh(); if (!data || !job) return;
    const unit = activeUnit(), name = names[unit.kind];
    el('translationScope').options[0].textContent = `現在の${name} ${current}`;
    el('translationUnits').querySelector('legend').textContent = `翻訳する${name}`;
    el('translationUnitList').replaceChildren();
    for (const entry of data.units) {
      const label = document.createElement('label'), checkbox = document.createElement('input'); checkbox.type = 'checkbox'; checkbox.value = entry.id; checkbox.checked = entry.number === current;
      label.append(checkbox, document.createTextNode(`${name} ${entry.number}`)); el('translationUnitList').append(label);
    }
    el('translationDisclosure').textContent = `対象の本文・表を${data.profile.provider === 'azure_openai' ? 'Azure OpenAI' : 'OpenAI'}へ送信します。`;
    el('translationTarget').value = language === 'ja' ? 'ja' : 'en'; el('translationForce').checked = false;
    el('translationInterval').value = data.scheduling?.interval_seconds ?? 60;
    el('translationQueueNote').textContent = `1${name}ずつ順番に翻訳します。長い${name}を分割する場合も、この間隔を空けます。`;
    el('translationDialogMessage').textContent = data.configuration_error || ''; el('translationDialogMessage').hidden = !data.configuration_error;
    el('translationSubmit').disabled = !!data.configuration_error;
    el('translationDialog').showModal();
  }
  el('translateOpen').addEventListener('click', openDialog);
  el('translationDialogClose').addEventListener('click', () => el('translationDialog').close());
  el('translationPanelClose').addEventListener('click', () => selectLanguage('original'));
  el('translationLanguage').addEventListener('change', event => selectLanguage(event.target.value));
  el('translationScope').addEventListener('change', event => { el('translationUnits').hidden = event.target.value !== 'selected'; });
  el('translationForm').addEventListener('submit', async event => {
    event.preventDefault(); if (!job || !data || busy) return;
    const scope = el('translationScope').value, target = el('translationTarget').value;
    const ids = scope === 'all' ? null : scope === 'current' ? [activeUnit().id] : [...el('translationUnitList').querySelectorAll('input:checked')].map(input => input.value);
    if (ids && !ids.length) { el('translationDialogMessage').textContent = '翻訳する範囲を選択してください。'; el('translationDialogMessage').hidden = false; return; }
    busy = true; el('translationSubmit').disabled = true; const id = job.id;
    try {
      await fetchJSON(`/api/jobs/${id}/translations`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({target_language:target, unit_ids:ids, force:el('translationForce').checked, interval_seconds:Number(el('translationInterval').value)})});
      el('translationDialog').close(); if (job?.id === id) { selectLanguage(target); await refresh(); }
    } catch (error) { el('translationDialogMessage').textContent = error.message; el('translationDialogMessage').hidden = false; }
    finally { busy = false; el('translationSubmit').disabled = false; }
  });
  document.addEventListener('slide-selected', event => { if (job?.id === event.detail.jobId) { current = event.detail.number; render(); } });
  window.addEventListener('message', event => {
    if (event.source !== el('original').contentWindow || event.data?.jobId !== job?.id) return;
    if (event.data.type === 'docling-unit-current' && Number.isInteger(event.data.number)) { current = event.data.number; render(); }
  });
  el('original').addEventListener('load', notifyFrame);
  // app.js loads before this module; adopt a document that was opened by a direct URL.
  if (typeof currentJobs !== 'undefined' && typeof selected !== 'undefined') setJob(currentJobs.find(item => item.id === selected) || null);
  return {setJob, refresh};
})();
