import json
from pathlib import Path

import pytest
from docling_core.types.doc import DocItemLabel, DoclingDocument, Size
from lxml import html

from docling_desk.documents.pagination import export_paginated_html


@pytest.mark.parametrize(
    "filename,first_text,second_text,unit",
    [
        ("synthetic-report.pdf", "月別の件数", "前ページの表1", "ページ"),
        ("synthetic-slides.pptx", "表と図の比較", "日本語と出典の確認", "スライド"),
        ("synthetic-sheet.xlsx", "区分ごとの件数", "実資料の品質検証", "シート"),
    ],
)
def test_source_boundaries_keep_content_and_images(
    tmp_path, filename, first_text, second_text, unit
):
    root = Path(__file__).resolve().parent / "fixtures/documents"
    candidates = [(p.parent, json.loads(p.read_text())) for p in root.glob("*/job.json")]
    folder, _ = max(
        (
            pair
            for pair in candidates
            if pair[1]["filename"] == filename and pair[1]["state"] == "success"
        ),
        key=lambda pair: pair[1]["created"],
    )
    doc = DoclingDocument.load_from_json(folder / "document.json")
    target = tmp_path / "extracted.html"
    export_paginated_html(doc, filename, target)
    rendered = html.fromstring(target.read_text())
    pages = rendered.xpath("//section[@data-page]")
    assert [page.get("data-page") for page in pages] == ["1", "2"]
    assert first_text in pages[0].text_content() and first_text not in pages[1].text_content()
    assert second_text in pages[1].text_content() and second_text not in pages[0].text_content()
    assert unit + " 1" in pages[0].xpath("./header")[0].text_content()
    assert unit + " 2" in pages[1].xpath("./header")[0].text_content()
    assert len(pages[0].xpath('.//img[starts-with(@src,"data:image/")]')) == 1
    assert not pages[1].xpath(".//img")
    assert len(rendered.xpath("//nav/a")) == 2
    if filename.endswith(".xlsx"):
        assert "集計" in pages[0].xpath("./header")[0].text_content()
        assert "注記" in pages[1].xpath("./header")[0].text_content()
        assert "印刷ページ境界は取得していません" in rendered.text_content()


def test_empty_and_unlocated_pages_are_not_invented_or_dropped(tmp_path):
    doc = DoclingDocument(name="unlocated-word")
    doc.add_text(label=DocItemLabel.TEXT, text="ページ位置を持たないWord本文")
    target = tmp_path / "unknown.html"
    export_paginated_html(doc, "example.docx", target)
    root = html.fromstring(target.read_text())
    assert root.xpath('//section[@data-page="unknown"]')
    assert "ページ境界未取得" in root.text_content()
    assert "ページ位置を持たないWord本文" in root.text_content()
    assert not root.xpath('//section[@data-page="1"]')
    doc.add_page(page_no=1, size=Size(width=595, height=842))
    doc.add_page(page_no=2, size=Size(width=595, height=842))
    export_paginated_html(doc, "example.pdf", target)
    root = html.fromstring(target.read_text())
    assert [p.get("data-page") for p in root.xpath("//section")] == ["1", "2", "unknown"]
    assert (
        "ページ位置を持たないWord本文"
        in root.xpath('//section[@data-page="unknown"]')[0].text_content()
    )
    assert len(root.xpath('//p[@class="empty-page"]')) == 2
