import json
from pathlib import Path

import pytest
from docling_core.types.doc import (
    BoundingBox,
    CoordOrigin,
    DocItemLabel,
    DoclingDocument,
    ProvenanceItem,
    Size,
    TableCell,
    TableData,
)

from docling_desk.documents.rag import build_context_chunks, export_rag

ROOT = Path(__file__).resolve().parents[1]


def sample(filename):
    jobs = [
        (p.parent, json.loads(p.read_text()))
        for p in (ROOT / "tests/fixtures/documents").glob("*/job.json")
    ]
    return max(
        (j for j in jobs if j[1]["filename"] == filename and j[1]["state"] == "success"),
        key=lambda j: j[1]["created"],
    )


def test_slide_context_keeps_title_and_table_without_picture_data():
    folder, job = sample("synthetic-slides.pptx")
    doc = DoclingDocument.load_from_json(folder / "document.json")
    before = doc.model_dump_json()
    parents, children, native, policy = build_context_chunks(
        doc, job["filename"], "a" * 64, job["id"]
    )
    assert len(parents) == 2 and len(children) == 2
    first, second = parents
    assert first.pages == [1] and second.pages == [2]
    assert "#/tables/0" in first.refs and "#/pictures/0" not in first.refs
    assert "表と図の比較" in first.text and all(
        v in first.text for v in ["120", "180", "150", "450", "BはAより60件多い"]
    )
    assert "日本語と出典の確認" not in first.text
    assert all(t in second.text for t in ["これは合成テスト資料", "表1と図1の対応", "翻訳と実資料"])
    assert [c.parent_id for c in children] == [c.id for c in parents]
    assert policy.strategy == "1 slide per context"
    assert doc.model_dump_json() == before


def test_sheet_context_keeps_notes_tables_and_sheet_identity():
    folder, job = sample("synthetic-sheet.xlsx")
    doc = DoclingDocument.load_from_json(folder / "document.json")
    parents, children, _, _ = build_context_chunks(doc, job["filename"], "a" * 64, job["id"])
    assert len(parents) == 2 and len(children) == 2
    first, second = parents
    assert first.headings == ["集計"] and second.headings == ["注記"]
    assert {"#/tables/0", "#/tables/1"} <= set(first.refs)
    assert "#/pictures/0" not in first.refs
    assert "架空データ" in first.text and "件数" in first.text and "180" in first.text
    assert "実資料の品質検証は未実施" in second.text
    assert first.pages == [1] and second.pages == [2]


def table_doc(values):
    doc = DoclingDocument(name="long-sheet")
    doc.add_page(page_no=1, size=Size(width=2, height=len(values) + 1))
    prov = ProvenanceItem(
        page_no=1,
        charspan=(0, 0),
        bbox=BoundingBox(l=0, t=0, r=2, b=len(values) + 1, coord_origin=CoordOrigin.TOPLEFT),
    )
    doc.add_text(label=DocItemLabel.TEXT, text="売上集計・単位は円", prov=prov)
    cells = []
    rows = [["製品", "金額"], *[[f"製品-{i}", v] for i, v in enumerate(values, 1)]]
    for r, row in enumerate(rows):
        for c, value in enumerate(row):
            cells.append(
                TableCell(
                    text=value,
                    column_header=r == 0,
                    start_row_offset_idx=r,
                    end_row_offset_idx=r + 1,
                    start_col_offset_idx=c,
                    end_col_offset_idx=c + 1,
                )
            )
    table = doc.add_table(
        data=TableData(num_rows=len(rows), num_cols=2, table_cells=cells), prov=prov
    )
    return doc, table


def test_large_sheet_splits_only_between_rows_and_repeats_headers_and_context():
    doc, table = table_doc([str(i) + "円の説明文" * 8 for i in range(1, 81)])
    parents, children, _, policy = build_context_chunks(
        doc, "sales.xlsx", "a" * 64, "job", target=600, tolerance=0
    )
    assert len(parents) == 1 and parents[0].oversize and policy.oversized_search_chunks == 0
    parts = [c for c in children if c.kind == "table_rows"]
    assert len(parts) > 1
    covered = []
    for c in parts:
        assert c.parent_id == parents[0].id and c.pages == [1] and len(c.text) <= 600
        assert "製品\t金額" in c.text and "単位は円" in c.text
        assert c.refs == [table.self_ref]
        covered.extend(range(c.row_range[0], c.row_range[1] + 1))
    assert covered == list(range(2, 82))
    assert "製品-80" in parents[0].text


def test_atomic_oversized_row_is_flagged_without_truncation():
    doc, _ = table_doc(["長" * 700])
    parents, children, _, policy = build_context_chunks(
        doc, "sales.xlsx", "a" * 64, "job", target=300, tolerance=0
    )
    row = next(c for c in children if c.kind == "table_rows")
    assert row.oversize and row.row_range == [2, 2]
    assert "長" * 700 in row.text and "長" * 700 in parents[0].text
    assert policy.oversized_search_chunks == 1


def test_unlocated_content_is_not_assigned_to_an_invented_slide():
    doc = DoclingDocument(name="no-provenance")
    doc.add_page(page_no=1, size=Size(width=100, height=100))
    text = doc.add_text(label=DocItemLabel.TEXT, text="ページを持たないメモ")
    parents, _, _, _ = build_context_chunks(doc, "slides.pptx", "a" * 64, "job")
    unlocated = next(c for c in parents if c.kind == "unlocated")
    assert unlocated.pages == [] and unlocated.refs == [text.self_ref]
    assert "メモ" in unlocated.text and len(parents) == 1


def test_reexport_replaces_image_data_in_comparison_and_keeps_pdf_text_boundaries(tmp_path):
    folder, job = sample("synthetic-report.pdf")
    doc = DoclingDocument.load_from_json(folder / "document.json")
    before = doc.model_dump_json()
    _, _, expected, _ = build_context_chunks(doc, job["filename"], "a" * 64, job["id"])
    # Old comparison files must not keep serving picture data after a reexport.
    (tmp_path / "rag-docling.jsonl").write_text('{"text":"画像の意味説明は未生成"}\n')
    policy = export_rag(doc, job["filename"], job["id"], folder / "original.pdf", tmp_path)
    comparison = (tmp_path / "rag-docling.jsonl").read_bytes()
    native = [json.loads(line) for line in comparison.splitlines()]
    parents = [json.loads(line) for line in (tmp_path / "rag.jsonl").read_text().splitlines()]
    assert [c["text"] for c in native] == [c.text for c in expected]
    assert [c["text"] for c in parents] == [c["text"] for c in native]
    assert policy.context_chunks == len(native)
    export_rag(doc, job["filename"], job["id"], folder / "original.pdf", tmp_path)
    assert (tmp_path / "rag-docling.jsonl").read_bytes() == comparison
    assert all(len(c["source_sha256"]) == 64 for c in parents)
    assert doc.model_dump_json() == before


def prose_doc(paragraphs):
    doc = DoclingDocument(name="prose-sheet")
    doc.add_page(page_no=1, size=Size(width=100, height=100))
    prov = ProvenanceItem(
        page_no=1,
        charspan=(0, 0),
        bbox=BoundingBox(l=0, t=0, r=100, b=100, coord_origin=CoordOrigin.TOPLEFT),
    )
    for text in paragraphs:
        doc.add_text(label=DocItemLabel.TEXT, text=text, prov=prov)
    return doc


@pytest.mark.parametrize("size", [2000, 2001, 2499, 2500, 2501])
def test_sheet_tolerance_boundary_counts_unit_and_keeps_parent(size):
    # The full sheet text includes its unit and two paragraph separators.
    overhead = len("シート 1\n\n\n\n")
    doc = prose_doc(["甲" * 900, "乙" * (size - overhead - 900)])
    parents, children, _, policy = build_context_chunks(doc, "sales.xlsx", "a" * 64, "job")
    assert len(parents) == 1 and len(parents[0].text) == size
    assert policy.target_chars == 2000 and policy.tolerance_chars == 500
    assert policy.split_threshold_chars == 2500
    assert len(children) == (1 if size <= 2500 else 2)
    assert policy.oversized_search_chunks == 0
    if size <= 2500:
        assert children[0].text == parents[0].text and not children[0].oversize
    assert all(c.parent_id == parents[0].id for c in children)


def test_small_table_tail_merges_with_headers_counted_and_no_rows_lost():
    doc, table = table_doc(["値" * n for n in [900, 900, 900, 900, 250]])
    parents, children, _, policy = build_context_chunks(doc, "sales.xlsx", "a" * 64, "job")
    parts = [c for c in children if c.kind == "table_rows"]
    assert len(parts) == 2 and parts[-1].text_chars > 2000
    assert all(c.text_chars == len(c.text) <= 2500 and not c.oversize for c in parts)
    assert [r for c in parts for r in range(c.row_range[0], c.row_range[1] + 1)] == list(
        range(2, 7)
    )
    assert all("製品\t金額" in c.text and table.self_ref in c.refs for c in parts)
    assert policy.oversized_search_chunks == 0 and "製品-5" in parents[0].text
    _, strict, _, _ = build_context_chunks(doc, "sales.xlsx", "a" * 64, "job", tolerance=0)
    assert len([c for c in strict if c.kind == "table_rows"]) == 3


def test_long_text_item_splits_at_paragraphs_and_merges_small_tail():
    text = "甲" * 1200 + "\n\n" + "乙" * 1200 + "\n\n" + "丙" * 300
    doc = prose_doc([text])
    parents, children, _, policy = build_context_chunks(doc, "sales.xlsx", "a" * 64, "job")
    assert len(children) == 2 and policy.oversized_search_chunks == 0
    assert "\n\n".join(c.text[len(c.unit + "\n\n") :] for c in children) == text
    assert all(c.refs == ["#/texts/0"] and c.text_chars <= 2500 for c in children)
    assert text in parents[0].text


def test_prose_tail_merge_keeps_each_source_ref():
    doc = prose_doc(["甲" * 1200, "乙" * 1200, "丙" * 300])
    _, children, _, _ = build_context_chunks(doc, "sales.xlsx", "a" * 64, "job")
    assert len(children) == 2
    assert children[-1].refs == ["#/texts/1", "#/texts/2"]
    assert "乙" * 1200 in children[-1].text and "丙" * 300 in children[-1].text


@pytest.mark.parametrize("kind", ["row", "paragraph"])
def test_default_policy_preserves_atomic_content_above_threshold(kind):
    text = "長" * 2600
    doc = table_doc([text])[0] if kind == "row" else prose_doc([text])
    parents, children, _, policy = build_context_chunks(doc, "sales.xlsx", "a" * 64, "job")
    oversized = [c for c in children if c.oversize]
    assert len(oversized) == policy.oversized_search_chunks == 1
    assert text in oversized[0].text and text in parents[0].text


def test_wide_table_headers_count_toward_threshold():
    doc, table = table_doc(["値" * 300 for _ in range(4)])
    table.data.table_cells[0].text = "製品" * 900
    _, children, _, policy = build_context_chunks(doc, "sales.xlsx", "a" * 64, "job")
    parts = [c for c in children if c.kind == "table_rows"]
    assert len(parts) == 3 and policy.oversized_search_chunks == 0
    assert all("製品" * 900 in c.text and c.text_chars <= 2500 for c in parts)
    assert [c.row_range for c in parts] == [[2, 2], [3, 3], [4, 5]]


def test_slide_budget_unchanged_and_negative_tolerance_rejected():
    doc = prose_doc(["甲" * 900, "乙" * 2000])
    _, children, _, policy = build_context_chunks(doc, "slides.pptx", "a" * 64, "job")
    assert len(children) == 1 and policy.target_chars == 4000 and policy.tolerance_chars == 0
    with pytest.raises(ValueError, match="tolerance"):
        build_context_chunks(doc, "sales.xlsx", "a" * 64, "job", tolerance=-1)


@pytest.mark.parametrize("extension", ["pdf", "pptx", "xlsx"])
def test_pictures_captions_footnotes_and_nested_text_are_excluded_from_all_rag(extension):
    doc, table = table_doc(["本文の表データ"])
    picture = doc.add_picture(prov=table.prov[0])
    caption = doc.add_text(
        label=DocItemLabel.CAPTION, text="図の専用キャプション", prov=table.prov[0]
    )
    footnote = doc.add_text(label=DocItemLabel.FOOTNOTE, text="図の専用脚注", prov=table.prov[0])
    nested = doc.add_text(
        label=DocItemLabel.TEXT, text="画像に含まれる文字", parent=picture, prov=table.prov[0]
    )
    picture.captions.append(caption.get_ref())
    picture.footnotes.append(footnote.get_ref())
    before = doc.model_dump_json()
    parents, children, native, policy = build_context_chunks(
        doc, f"sample.{extension}", "a" * 64, "job"
    )
    excluded_refs = {i.self_ref for i in [picture, caption, footnote, nested]}
    assert all(chunks for chunks in [parents, children, native])
    for chunks in [parents, children, native]:
        assert any("本文の表データ" in c.text for c in chunks)
        for c in chunks:
            assert not excluded_refs.intersection([*c.refs, *c.context_refs])
            assert all(r.kind == "table" for r in c.relations)
            assert not any(
                marker in c.text for marker in ["未生成", "[図", "図の専用", "画像に含まれる文字"]
            )
    assert policy.image_content == "excluded" and doc.model_dump_json() == before


@pytest.mark.parametrize("extension", ["pdf", "pptx", "xlsx"])
def test_picture_only_document_has_no_rag_chunks_or_placeholder(extension, tmp_path):
    doc = prose_doc([])
    picture = doc.add_picture()
    caption = doc.add_text(label=DocItemLabel.CAPTION, text="図だけの資料", parent=picture)
    picture.captions.append(caption.get_ref())
    source = tmp_path / f"original.{extension}"
    source.write_bytes(b"original file stays unchanged")
    before = doc.model_dump_json()
    policy = export_rag(doc, f"sample.{extension}", "job", source, tmp_path)
    assert policy.context_chunks == policy.search_chunks == policy.docling_chunks == 0
    for name in ["rag.jsonl", "rag-index.jsonl", "rag-docling.jsonl"]:
        assert (tmp_path / name).read_bytes() == b""
    assert doc.model_dump_json() == before
    assert source.read_bytes() == b"original file stays unchanged"


@pytest.mark.parametrize("extension", ["pptx", "xlsx"])
def test_table_text_contains_only_caption_columns_and_values_with_source_metadata(extension):
    doc, table = table_doc(["101", "202"])
    caption = doc.add_text(label=DocItemLabel.CAPTION, text="年度別売上", prov=table.prov[0])
    table.captions.append(caption.get_ref())
    before = doc.model_dump_json()
    parents, children, native, policy = build_context_chunks(
        doc, f"sales.{extension}", "a" * 64, "job"
    )
    expected = "年度別売上\n製品\t金額\n製品-1\t101\n製品-2\t202"
    for c in [*parents, *children]:
        assert expected in c.text
        assert table.self_ref not in c.text
        assert "元行" not in c.text and "見出し 1 行" not in c.text and "[表" not in c.text
        relation = next(r for r in c.relations if r.ref == table.self_ref)
        assert relation.header_rows == 1 and relation.row_range == [2, 3]
        assert relation.captions == [caption.self_ref]
    assert all(
        r.header_rows == 1 and r.row_range == [2, 3]
        for c in native
        for r in c.relations
        if r.ref == table.self_ref
    )
    assert policy.embedding_text_field == "text" and doc.model_dump_json() == before


def test_split_table_ranges_survive_without_row_numbers_in_text():
    values = ["内容" * 100 for _ in range(20)]
    doc, table = table_doc(values)
    parents, children, _, _ = build_context_chunks(doc, "sales.xlsx", "a" * 64, "job")
    parts = [c for c in children if c.kind == "table_rows"]
    assert len(parts) > 1
    recovered = []
    for c in parts:
        relation = next(r for r in c.relations if r.ref == table.self_ref)
        assert relation.row_range == c.row_range and relation.header_rows == 1
        data_lines = c.text.split("製品\t金額\n", 1)[1].splitlines()
        expected = [
            f"製品-{row_no - 1}\t{values[row_no - 2]}"
            for row_no in range(c.row_range[0], c.row_range[1] + 1)
        ]
        assert data_lines == expected
        assert table.self_ref not in c.text and "元行" not in c.text
        recovered.extend(range(c.row_range[0], c.row_range[1] + 1))
    assert recovered == list(range(2, 22))
    assert parents[0].relations[0].row_range == [2, 21]


def test_empty_column_names_do_not_get_generated_labels_and_real_numbers_are_kept():
    doc, table = table_doc(["10", "20"])
    table.data.table_cells[1].text = ""
    table.data.table_cells[2].text = "123"
    parents, _, _, _ = build_context_chunks(doc, "sales.xlsx", "a" * 64, "job")
    assert "製品\t\n123\t10\n製品-2\t20" in parents[0].text
    assert "列 2" not in parents[0].text and "元行" not in parents[0].text


def test_source_values_resembling_management_tags_are_not_removed():
    literal = "[表 #/tables/99 · 見出し 1 行 · 元行番号付き]"
    doc, _ = table_doc([literal])
    parents, children, _, _ = build_context_chunks(doc, "sales.xlsx", "a" * 64, "job")
    assert literal in parents[0].text and literal in children[0].text
