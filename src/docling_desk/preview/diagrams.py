"""A small viewer shell; the diagram and HTML are never duplicated on disk."""

import html
import json

from docling_desk.documents.diagrams import Diagram


def diagram_html(job_id: str, filename: str, diagram: Diagram, unit: int) -> str:
    payload = (
        json.dumps(
            {"xml": diagram.xml, "names": diagram.names, "page": unit - 1}, ensure_ascii=False
        )
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )
    return f'''<!doctype html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(filename)} · 図のプレビュー</title>
<link rel="stylesheet" href="/static/diagram-viewer.css">
<script src="/static/diagram-config.js" defer></script>
<script src="/static/vendor/drawio/viewer-static.min.js" defer></script>
<script src="/static/vendor/drawio/stencils.min.js" defer></script>
<script src="/static/file-drop.js" defer></script>
<script src="/static/diagram-viewer.js" defer></script></head>
<body data-job="{job_id}">
<div class="diagram-toolbar" role="toolbar" aria-label="図の表示操作">
<button id="diagramPrevious" aria-label="前のページ">‹</button>
<select id="diagramPage" aria-label="図のページ"></select>
<button id="diagramNext" aria-label="次のページ">›</button>
<span class="diagram-divider"></span>
<button id="diagramZoomOut" aria-label="図を縮小">−</button>
<span id="diagramZoom" aria-live="polite"></span>
<button id="diagramZoomIn" aria-label="図を拡大">＋</button>
<button id="diagramActual">100%</button><button id="diagramFit">全体表示</button>
<button id="diagramLayers" aria-expanded="false" aria-controls="diagramLayerList">レイヤー</button>
</div>
<fieldset id="diagramLayerList" hidden><legend>表示するレイヤー</legend></fieldset>
<p id="diagramStatus" role="status">図を読み込んでいます…</p>
<div id="diagramCanvas" tabindex="0" role="region" aria-label="図の表示領域"></div>
<script type="application/json" id="diagramData">{payload}</script>
</body></html>'''
