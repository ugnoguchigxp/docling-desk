'use strict';
(() => {
  const el = id => document.getElementById(id);
  const status = el('diagramStatus'), canvas = el('diagramCanvas');
  const notices = new Set();
  let selectLinkedPage = () => false;
  function notice(text) {
    notices.add(text); status.textContent = [...notices].join('\n'); status.hidden = false;
  }
  document.addEventListener('securitypolicyviolation', event => {
    if (['img-src','font-src','connect-src','script-src-elem'].includes(event.effectiveDirective)) {
      notice('外部参照の画像・フォント・追加資源は読み込みません。図に埋め込まれた画像は表示できます。');
    }
  });
  document.addEventListener('error', event => {
    if (event.target instanceof HTMLImageElement) notice('一部の画像を表示できませんでした。');
    if (event.target instanceof HTMLScriptElement) notice('描画に必要な追加資源を読み込めませんでした。');
  }, true);
  document.addEventListener('click', event => {
    const link = event.target.closest?.('a');
    if (link) {
      event.preventDefault();
      if(selectLinkedPage(link.getAttribute('href'))) event.stopPropagation();
      else notice('このプレビューでは外部リンクを開きません。');
    }
  }, true);
  try {
    const data = JSON.parse(el('diagramData').textContent);
    const viewer = new GraphViewer(canvas, mxUtils.parseXml(data.xml).documentElement, {
      page: data.page, nav:true, resize:false, lightbox:false,
      'auto-fit':true, center:true, responsive:false, 'responsive-auto-fit':false, border:16
    });
    const graph = viewer.graph;
    graph.setPanning(true);
    selectLinkedPage = url => {
      if(typeof url !== 'string' || !url.startsWith('data:page/id,')) return false;
      try { return viewer.selectPageById(decodeURIComponent(url.slice('data:page/id,'.length))); } catch { return false; }
    };
    graph.openLink = url => { if(!selectLinkedPage(url)) notice('このプレビューでは外部リンクを開きません。'); };
    graph.panningHandler.useLeftButtonForPanning = true;
    graph.panningHandler.ignoreCell = true;
    // There is no editor UI and graph edits are disabled by GraphViewer.
    const minimum = 0.05, maximum = 8;
    function fit() {
      graph.fit(24);
      if (graph.view.scale > 1) graph.zoomTo(1);
      graph.center();
      controls();
    }
    function zoom(value) { graph.zoomTo(Math.max(minimum, Math.min(maximum, value))); controls(); }
    const pages = el('diagramPage');
    data.names.forEach((name, index) => pages.add(new Option(`${index+1} · ${name}`, String(index))));
    function inspectShapes() {
      for (const cell of Object.values(graph.model.cells)) {
        const state = graph.view.getState(cell);
        if (!state?.shape) continue;
        const shape = state.style.shape;
        if (shape && !mxCellRenderer.defaultShapes[shape] && !state.shape.stencil && !shape.startsWith('stencil(')) {
          notice('一部の独自図形は標準の形で表示しています。原本の図形定義を確認してください。');
        }
      }
    }
    function controls() {
      pages.value = String(viewer.currentPage);
      el('diagramPrevious').disabled = viewer.currentPage <= 0;
      el('diagramNext').disabled = viewer.currentPage >= data.names.length-1;
      el('diagramZoom').textContent = `${Math.round(graph.view.scale*100)}%`;
      el('diagramZoomOut').disabled = graph.view.scale <= minimum;
      el('diagramZoomIn').disabled = graph.view.scale >= maximum;
      parent.postMessage({type:'docling-unit-current',jobId:document.body.dataset.job,number:viewer.currentPage+1}, '*');
    }
    function select(index) {
      if (index<0 || index>=data.names.length) return;
      viewer.selectPage(index); fit(); layers(); inspectShapes();
    }
    el('diagramPrevious').addEventListener('click',()=>select(viewer.currentPage-1));
    el('diagramNext').addEventListener('click',()=>select(viewer.currentPage+1));
    pages.addEventListener('change',()=>select(Number(pages.value)));
    el('diagramZoomOut').addEventListener('click',()=>zoom(graph.view.scale/1.25));
    el('diagramZoomIn').addEventListener('click',()=>zoom(graph.view.scale*1.25));
    el('diagramActual').addEventListener('click',()=>zoom(1));
    el('diagramFit').addEventListener('click',fit);
    canvas.addEventListener('wheel',event=>{
      if (!event.ctrlKey && !event.metaKey) return;
      event.preventDefault(); zoom(graph.view.scale*(event.deltaY<0?1.1:1/1.1));
    },{passive:false});
    canvas.addEventListener('keydown',event=>{
      if(event.key==='+'||event.key==='='){event.preventDefault();zoom(graph.view.scale*1.25);}
      if(event.key==='-'){event.preventDefault();zoom(graph.view.scale/1.25);}
      if(event.key==='0'){event.preventDefault();fit();}
      if(event.key==='PageDown'){event.preventDefault();select(viewer.currentPage+1);}
      if(event.key==='PageUp'){event.preventDefault();select(viewer.currentPage-1);}
    });
    const list=el('diagramLayerList');
    function layers() {
      list.querySelectorAll('label').forEach(node=>node.remove());
      const root=graph.model.root;
      const count=graph.model.getChildCount(root);
      el('diagramLayers').hidden=count<2;
      for(let i=0;i<count;i++){
        const layer=graph.model.getChildAt(root,i), label=document.createElement('label'), input=document.createElement('input');
        input.type='checkbox';input.checked=graph.model.isVisible(layer);
        const name=graph.convertValueToString(layer)||`レイヤー ${i+1}`;
        input.setAttribute('aria-label',name);
        input.addEventListener('change',()=>{graph.model.setVisible(layer,input.checked);fit();});
        label.append(input,document.createTextNode(name));list.append(label);
      }
    }
    el('diagramLayers').addEventListener('click',()=>{
      list.hidden=!list.hidden;el('diagramLayers').setAttribute('aria-expanded',String(!list.hidden));
    });
    window.addEventListener('message',event=>{
      const d=event.data;
      if(event.source===parent && d?.jobId===document.body.dataset.job && d.type==='docling-diagram-select' && Number.isInteger(d.number))select(d.number-1);
    });
    viewer.addListener('graphChanged',()=>{controls();layers();inspectShapes();});
    graph.view.addListener(mxEvent.SCALE,controls);
    status.hidden=notices.size===0;
    fit();layers();inspectShapes();
    let previousWidth=canvas.clientWidth,previousHeight=canvas.clientHeight;
    new ResizeObserver(()=>{
      if(canvas.clientWidth!==previousWidth || canvas.clientHeight!==previousHeight){previousWidth=canvas.clientWidth;previousHeight=canvas.clientHeight;fit();}
    }).observe(canvas);
  } catch(error) {
    notice('図を表示できませんでした。原本を保存して内容を確認してください。');
    console.error('Diagram preview failed',error);
  }
})();
