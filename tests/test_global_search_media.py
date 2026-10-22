"""Saved diagram/image content uses the same retrieval and citation contracts as documents."""

import json
from io import BytesIO
from uuid import UUID, uuid4
from xml.etree import ElementTree as ET

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from test_drawio import compressed, exported_html, png, svg
from test_local_knowledge import article, document, search

from docling_desk import config
from docling_desk.app import create_app
from docling_desk.knowledge.diagram_text import drawio_sections, excalidraw_sections
from docling_desk.storage import document_folder, original_file


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA", tmp_path.resolve())
    for key in ("ENDPOINT", "KEY", "DEPLOYMENT", "MODEL_VERSION", "DIMENSIONS"):
        monkeypatch.delenv("DOCLING_AZURE_EMBEDDING_" + key, raising=False)
    with TestClient(create_app()) as client:
        yield client


def scene(text, **extra):
    return {
        "type": "excalidraw",
        "version": 2,
        "elements": [
            {
                "id": "t",
                "type": "text",
                "x": 0,
                "y": 0,
                "width": 100,
                "height": 20,
                "text": text,
                **extra,
            },
            {
                "id": "deleted",
                "type": "text",
                "x": 0,
                "y": 30,
                "width": 100,
                "height": 20,
                "text": "DeletedObjectUnique",
                "isDeleted": True,
            },
            {
                "id": "shape",
                "type": "rectangle",
                "x": 0,
                "y": 50,
                "width": 100,
                "height": 20,
                "customData": {"text": "ShapeMetadataUnique"},
            },
        ],
        "appState": {"name": "AppStateUnique"},
    }


def test_excalidraw_extracts_only_live_displayed_text_with_object_refs():
    value = scene("最初の文字", originalText="UndisplayedOriginalUnique")
    value["elements"].append(
        {"id": "bound", "type": "text", "text": "結び付いた文字", "containerId": "shape"}
    )
    sections = excalidraw_sections(json.dumps(value))
    assert sections == [{"text": "最初の文字\n結び付いた文字", "refs": ["t", "bound"]}]


@pytest.mark.parametrize("raw", ["[]", "{}", '{"elements":[1]}', "broken"])
def test_malformed_excalidraw_is_rejected_before_indexing(raw):
    with pytest.raises(ValueError):
        excalidraw_sections(raw)


def test_drawio_preserves_plain_labels_and_extracts_html_text_without_urls(tmp_path):
    root = ET.fromstring(drawio(False))
    cells = root.find("diagram/mxGraphModel/root")
    ET.SubElement(cells, "mxCell", id="plain", value="<文字をそのまま>", style="html=0;")
    ET.SubElement(
        cells,
        "mxCell",
        id="html",
        style="html=1;",
        value=(
            '<img src="https://external.invalid/private-image">画像の説明'
            "<script>ScriptBodyUnique</script><style>StyleBodyUnique</style>"
        ),
    )
    source = tmp_path / "labels.drawio"
    source.write_bytes(ET.tostring(root))
    _, sections = drawio_sections(source)
    assert "<文字をそのまま>" in sections[0]["text"]
    assert "画像の説明" in sections[0]["text"]
    assert "工程の説明 1" in sections[0]["text"]
    for absent in ("external.invalid", "ScriptBodyUnique", "StyleBodyUnique", "<b>"):
        assert absent not in sections[0]["text"]


def create_diagram(client, value=None, name="設計図"):
    identifier = uuid4().hex
    response = client.put(
        f"/api/diagrams/{identifier}",
        json={"name": name, "folder_id": None, "scene": value or scene("図中文字の検索")},
        headers={"If-None-Match": "*", "Idempotency-Key": uuid4().hex},
    )
    assert response.status_code == 201, response.text
    return identifier, response.json()


def context(client, retrieval):
    return client.post(
        "/api/knowledge/context",
        json={
            "retrieval_id": retrieval["id"],
            "chunk_ids": [h["chunk_id"] for h in retrieval["output"]["results"]],
        },
    )


def test_excalidraw_committed_text_drafts_restore_and_stale_citations(client):
    identifier, info = create_diagram(client)
    sid = "doc-" + identifier
    found = search(client, "図中文字", source_id=sid)
    hit = found["output"]["results"][0]
    assert hit["job_id"] == identifier and hit["unit"] == 1
    assert hit["locator"]["refs"] == ["t"]
    assert "図中文字の検索" in context(client, found).json()["text"]
    for term in ("DeletedObjectUnique", "ShapeMetadataUnique", "AppStateUnique"):
        assert not search(client, term)["output"]["results"]
    draft = client.post(
        f"/api/diagrams/{identifier}/drafts",
        content=json.dumps(scene("未確定テキスト")),
        headers={"If-Match": info["etag"], "Content-Type": "application/json"},
    )
    assert draft.status_code == 201, draft.text
    assert not search(client, "未確定テキスト")["output"]["results"]
    assert search(client, "図中文字")["output"]["results"]
    commit = client.post(
        f"/api/diagrams/{identifier}/drafts/{draft.json()['id']}/commit",
        json={"source_etag": info["etag"]},
        headers={"If-Match": draft.json()["etag"], "Idempotency-Key": uuid4().hex},
    )
    assert commit.status_code == 201, commit.text
    assert search(client, "未確定テキスト")["output"]["results"][0]["source_id"] == sid
    assert not search(client, "図中文字")["output"]["results"]
    assert context(client, found).status_code == 409
    assert client.get("/api/knowledge/retrievals/" + found["id"]).json()["output"]["stale"]
    restored = client.post(
        f"/api/diagrams/{identifier}/restore",
        json={"revision_id": info["revision_id"]},
        headers={"If-Match": commit.json()["etag"], "Idempotency-Key": uuid4().hex},
    )
    assert restored.status_code == 201, restored.text
    assert search(client, "図中文字")["output"]["results"]
    assert not search(client, "未確定テキスト")["output"]["results"]


def drawio(packed):
    root = ET.Element("mxfile")
    for number in (1, 2):
        page = ET.SubElement(root, "diagram", id=f"p{number}", name=f"設計ページ {number}")
        model = ET.Element("mxGraphModel")
        cells = ET.SubElement(model, "root")
        ET.SubElement(cells, "mxCell", id="0")
        ET.SubElement(cells, "mxCell", id="1", parent="0")
        ET.SubElement(
            cells,
            "mxCell",
            id="text",
            parent="1",
            vertex="1",
            value=f"<b>工程の説明 {number}</b><br>次の行 &amp; 数式 $$x^n$$",
            style="html=1;",
        )
        obj = ET.SubElement(cells, "object", id="wrapped", label=f"業務ノード {number}")
        ET.SubElement(obj, "mxCell", parent="1", vertex="1", style="html=1;")
        value = ET.tostring(model, encoding="unicode")
        if packed:
            page.text = compressed(value)
        else:
            page.append(model)
    return ET.tostring(root, encoding="utf-8")


@pytest.mark.parametrize(
    "name,wrap,packed",
    [
        ("設計.drawio", bytes, False),
        ("設計.DRAWIO", bytes, True),
        ("設計.dio", bytes, True),
        ("設計.xml", bytes, False),
        ("設計.svg", svg, True),
        ("設計.png", png, True),
        ("設計.html", exported_html, True),
        ("設計.htm", exported_html, False),
    ],
)
def test_drawio_labels_containers_and_page_citations(client, name, wrap, packed):
    response = client.post("/api/upload", files={"file": (name, wrap(drawio(packed)))})
    assert response.status_code == 202, response.text
    identifier = response.json()["id"]
    found = search(client, "工程の説明 2")
    hit = found["output"]["results"][0]
    assert hit["job_id"] == identifier and hit["unit"] == 2
    assert hit["locator"]["heading"] == "設計ページ 2"
    assert hit["locator"]["refs"] == ["text", "wrapped"]
    assert "業務ノード 2" in hit["text"] and "<b>" not in hit["text"]
    assert "$$x^n$$" in context(client, found).json()["text"]
    assert search(client, "業務ノード 2")["output"]["results"][0]["unit"] == 2
    assert client.get(f"/view/{identifier}/diagram?unit=2").status_code == 200
    assert not (document_folder(config.DATA, identifier) / "rag.jsonl").exists()


def test_image_metadata_update_scope_move_delete_and_shared_wiki_search(client):
    raw = BytesIO()
    Image.new("RGB", (32, 24), "blue").save(raw, format="PNG")
    response = client.post("/api/upload", files={"file": ("業務構成.png", raw.getvalue())})
    assert response.status_code == 202, response.text
    identifier = response.json()["id"]
    endpoint = "/api/v1/media/" + str(UUID(identifier))
    info = client.get(endpoint).json()
    updated = client.patch(
        endpoint,
        json={
            "display_name": "業務構成.png",
            "description": "横断テスト用の構成画像",
            "tags": ["設計タグ"],
            "default_alt": "代替テキスト",
            "default_credit": "画像の著作者",
            "source_url": "https://example.com/image-source",
        },
        headers={"If-Match": info["metadata_etag"]},
    )
    assert updated.status_code == 200, updated.text
    wiki = article(client, body="# Wiki\n\n横断テスト用の説明")
    found = search(client, "横断テスト")
    assert {h["source_id"] for h in found["output"]["results"]} == {wiki, "doc-" + identifier}
    for query in ("業務構成", "設計タグ", "代替テキスト", "画像の著作者", "image-source"):
        assert search(client, query)["output"]["results"][0]["job_id"] == identifier
    folder = client.post("/api/folders", json={"name": "設計"}).json()["id"]
    assert not search(client, "横断テスト", folder_id=folder)["output"]["results"]

    def operation(action, **values):
        return client.post(
            "/api/library/operations",
            json={
                "action": action,
                "items": [{"kind": "file", "id": identifier}],
                **values,
            },
        )

    assert operation("move", destination=folder).status_code == 200
    assert len(search(client, "横断テスト", folder_id=folder)["output"]["results"]) == 1
    assert operation("rename", name="更新後の名前.png").status_code == 200
    assert search(client, "更新後の名前")["output"]["results"][0]["job_id"] == identifier
    info = client.get(endpoint).json()
    assert (
        client.patch(
            endpoint,
            json={"display_name": info["display_name"], "description": "更新した説明"},
            headers={"If-Match": info["metadata_etag"]},
        ).status_code
        == 200
    )
    assert not search(client, "設計タグ")["output"]["results"]
    assert context(client, found).status_code == 409
    assert operation("delete").status_code == 200
    assert not search(client, "更新した説明")["output"]["results"]


def test_empty_or_broken_content_keeps_name_search_and_removes_old_diagram_text(client):
    identifier, _ = create_diagram(client, scene(""), name="文字なし図表")
    assert search(client, "文字なし図表")["output"]["results"][0]["job_id"] == identifier
    folder = document_folder(config.DATA, identifier)
    source = original_file(folder, ".excalidraw")
    source.write_text(json.dumps(scene("以前の本文")))
    found = search(client, "以前の本文")
    assert found["output"]["results"]
    source.write_text('{"elements": [1]}')
    assert not search(client, "以前の本文")["output"]["results"]
    assert search(client, "文字なし図表")["output"]["results"]
    assert context(client, found).status_code == 409
    job = document(config.DATA, filename="空の抽出.pdf")
    (config.DATA / job / "rag.jsonl").write_text("")
    (config.DATA / job / "rag-index.jsonl").write_text("")
    assert search(client, "空の抽出")["output"]["results"][0]["job_id"] == job


def test_symlink_diagram_original_is_never_read(client, tmp_path):
    identifier, _ = create_diagram(client)
    source = original_file(document_folder(config.DATA, identifier), ".excalidraw")
    source.unlink()
    outside = tmp_path.parent / (uuid4().hex + ".excalidraw")
    try:
        outside.write_text(json.dumps(scene("OutsideTextUnique")))
        source.symlink_to(outside)
        assert not search(client, "OutsideTextUnique")["output"]["results"]
        assert search(client, "設計図")["output"]["results"]
    finally:
        outside.unlink(missing_ok=True)


def test_global_search_returns_documents_diagrams_images_and_published_wiki_together(client):
    term = "種類横断の検証"
    pdf = document(config.DATA, text=term)
    wiki = article(client, body="# 公開Wiki\n\n" + term)
    excalidraw, _ = create_diagram(client, scene(term))
    xml = ET.fromstring(drawio(False))
    xml.find("diagram/mxGraphModel/root/mxCell[@id='text']").set("value", term)
    response = client.post(
        "/api/upload",
        files={
            "file": ("工程.drawio", ET.tostring(xml)),
        },
    )
    assert response.status_code == 202, response.text
    drawio_id = response.json()["id"]
    raw = BytesIO()
    Image.new("RGB", (32, 24), "blue").save(raw, format="PNG")
    response = client.post("/api/upload", files={"file": (term + ".png", raw.getvalue())})
    assert response.status_code == 202, response.text
    image = response.json()["id"]
    expected = {"doc-" + id for id in (pdf, excalidraw, drawio_id, image)} | {wiki}
    hits = search(client, term)["output"]["results"]
    assert {hit["source_id"] for hit in hits} == expected
    assert {hit["source_id"] for hit in search(client, term, kind="wiki")["output"]["results"]} == {
        wiki
    }
    assert {
        hit["source_id"] for hit in search(client, term, kind="document")["output"]["results"]
    } == expected - {wiki}
    for hit in hits:
        if hit["kind"] == "wiki":
            assert client.get("/api/wiki/sources/" + hit["source_id"]).status_code == 200
        else:
            assert client.get("/api/jobs/" + hit["job_id"]).status_code == 200
