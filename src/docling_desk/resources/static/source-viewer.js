'use strict';
(() => {
  const pre = document.querySelector('pre.plain-text');
  if (!pre) return;
  const text = pre.textContent;
  document.body.classList.add('source-viewer');
  const toolbar = document.createElement('div');
  toolbar.className = 'source-toolbar';
  toolbar.setAttribute('role', 'toolbar');
  toolbar.setAttribute('aria-label', 'ソースの表示操作');
  const search = document.createElement('input');
  search.type = 'search';
  search.placeholder = '本文を検索';
  search.setAttribute('aria-label', '本文を検索');
  const status = document.createElement('span');
  status.setAttribute('role', 'status');
  const previous = button('前の一致', () => move(-1));
  const next = button('次の一致', () => move(1));
  let wrapped = true, numbered = true, marks = [], current = -1;
  const wrap = button('折り返し', () => {
    wrapped = !wrapped;
    wrap.setAttribute('aria-pressed', String(wrapped));
    pre.style.whiteSpace = wrapped ? 'pre-wrap' : 'pre';
  });
  wrap.setAttribute('aria-pressed', 'true');
  const numbers = button('行番号', () => {
    numbered = !numbered;
    numbers.setAttribute('aria-pressed', String(numbered));
    pre.classList.toggle('line-numbers', numbered);
  });
  numbers.setAttribute('aria-pressed', 'true');
  const size = document.createElement('select');
  size.setAttribute('aria-label', '文字サイズ');
  for (const value of [12, 14, 16, 20, 24]) {
    const option = new Option(`${value}px`, String(value), value === 14, value === 14);
    size.add(option);
  }
  size.addEventListener('change', () => { pre.style.fontSize = `${Number(size.value)}px`; });
  toolbar.append(search, previous, next, status, wrap, numbers, size);
  document.body.prepend(toolbar);
  pre.classList.add('line-numbers');
  let timer;
  search.addEventListener('input', () => {
    clearTimeout(timer);
    timer = setTimeout(render, 120);
  });
  search.addEventListener('keydown', event => {
    if (event.key === 'Enter') { event.preventDefault(); move(event.shiftKey ? -1 : 1); }
  });
  document.addEventListener('keydown', event => {
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'f') {
      event.preventDefault(); search.focus(); search.select();
    }
  });
  function button(label, action) {
    const node = document.createElement('button');
    node.type = 'button'; node.textContent = label;
    node.addEventListener('click', action); return node;
  }
  function move(delta) {
    if (!marks.length) return;
    marks[current]?.classList.remove('current');
    current = (current + delta + marks.length) % marks.length;
    marks[current].classList.add('current');
    marks[current].scrollIntoView({block:'center', inline:'nearest'});
    status.textContent = `${current + 1} / ${marks.length}${marks.length === 10000 ? '（上限）' : ''}件`;
  }
  function render() {
    const fragment = document.createDocumentFragment();
    const query = search.value;
    marks = []; current = -1;
    // Literal case-sensitive matching preserves source offsets for all Unicode.
    let offset = 0, number = 0;
    for (const line of text.split('\n')) {
      const span = document.createElement('span');
      span.className = 'source-line'; span.dataset.line = String(++number);
      let start = 0, at;
      while (query && marks.length < 10000 && (at = line.indexOf(query, start)) !== -1) {
        span.append(document.createTextNode(line.slice(start, at)));
        const mark = document.createElement('mark'); mark.textContent = line.slice(at, at + query.length);
        span.append(mark); marks.push(mark); start = at + query.length;
      }
      span.append(document.createTextNode(line.slice(start)));
      offset += line.length;
      if (offset < text.length) { span.append(document.createTextNode('\n')); offset++; }
      fragment.append(span);
    }
    pre.replaceChildren(fragment);
    previous.disabled = next.disabled = !marks.length;
    status.textContent = query ? `${marks.length}件` : `${number}行`;
    if (marks.length) move(1);
  }
  render();
})();
