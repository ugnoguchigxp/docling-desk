"""Add trusted selection/drop bridges to already sanitized document previews."""

from lxml import html


def interactive_html(raw: str, job_id: str, unit_id: str) -> str:
    # Imports here avoid coupling the text binding's source templates to this wrapper.
    from docling_desk.translation.source import serialize

    tree = html.fromstring(raw)
    head, body = tree.find("head"), tree.find("body")
    if head is None or body is None:
        raise ValueError("文書プレビューにhead/bodyがありません。")
    body.set("data-job", job_id)
    body.set("data-unit", unit_id)
    for src in ("/static/document-frame.js", "/static/file-drop.js"):
        if not head.xpath("script[@src=$src]", src=src):
            head.append(html.Element("script", src=src, defer="defer"))
    head.append(html.Element("link", rel="stylesheet", href="/static/document-frame.css"))
    return serialize(tree, raw)
