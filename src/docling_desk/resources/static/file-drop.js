'use strict';
// Forward file drops from the app's sandboxed PDF and Excel viewers.
// Only known child windows may relay messages; source-document scripts remain disabled.
(() => {
  if (window === parent) return;
  const send = payload => parent.postMessage({type:'docling-file-drop', time:Date.now(), ...payload}, '*');
  const isFileDrag = event => [...(event.dataTransfer?.types || [])].includes('Files');
  for (const type of ['dragenter', 'dragover']) window.addEventListener(type, event => {
    if (!isFileDrag(event)) return;
    event.preventDefault(); event.dataTransfer.dropEffect = 'copy'; send({action:'enter'});
  }, true);
  window.addEventListener('dragleave', event => { if (isFileDrag(event)) send({action:'leave'}); }, true);
  window.addEventListener('drop', event => {
    if (!isFileDrag(event)) return;
    event.preventDefault(); event.stopImmediatePropagation();
    const files = [], rejected = [], items = [...(event.dataTransfer.items || [])].filter(item => item.kind === 'file');
    if (items.length) {
      for (const item of items) {
        const entry = item.webkitGetAsEntry?.();
        if (entry?.isDirectory) { rejected.push(`${entry.name}：フォルダーではなく、ファイルをドロップしてください。`); continue; }
        const file = item.getAsFile(); if (file) files.push(file);
      }
    } else files.push(...event.dataTransfer.files);
    send({action:'drop', files, rejected});
  }, true);
  window.addEventListener('message', event => {
    if (event.data?.type !== 'docling-file-drop' || !['enter','leave','drop'].includes(event.data.action)) return;
    if (![...document.querySelectorAll('iframe')].some(frame => frame.contentWindow === event.source)) return;
    if (event.data.action === 'drop' && (!Array.isArray(event.data.files) || !event.data.files.every(file => file instanceof File) || !Array.isArray(event.data.rejected) || !event.data.rejected.every(reason => typeof reason === 'string'))) return;
    send({action:event.data.action, time:event.data.time, files:event.data.files, rejected:event.data.rejected});
  });
})();
