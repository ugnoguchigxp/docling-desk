'use strict';
// Configure the official reader before it loads. No hosted viewer, editor,
// proxy, shape download or document URL is used.
(() => {
  const source = document.currentScript.src;
  const base = new URL('vendor/drawio/', source).href;
  for (const key of ['PROXY_URL','STYLE_PATH','SHAPES_PATH','STENCIL_PATH','DRAW_MATH_URL','GRAPH_IMAGE_PATH','mxImageBasePath','mxBasePath']) {
    window[key] = base + key;
  }
  window.DRAW_MATH_URL = base.replace(/\/$/, "");
  window.DRAWIO_BASE_URL = base;
  window.onDrawioViewerLoad = () => {};
})();
