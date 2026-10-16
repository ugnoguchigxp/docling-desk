import io
import shutil
from uuid import uuid4

import pytest
from PIL import Image
from test_local_knowledge import article, search
from test_local_knowledge import client as client

from docling_desk import config as desk_config
from docling_desk.knowledge.store import Store


def save(client, payload=None, source_id=None):
    source_id = source_id or "wiki-" + uuid4().hex
    return client.put(
        "/api/wiki/sources/" + source_id,
        json=payload
        or {
            "namespace": "Wiki",
            "path": "writing/new.md",
            "title": "新しい記事",
            "body": "## 本文\n\n検索可能な新規記事です。😀\n",
            "article_info": {
                "subtitle": "副題",
                "keywords": ["Markdown", "AI"],
                "original_url": "https://example.com/original",
                "image": {
                    "url": "https://example.com/cover.png",
                    "alt": "図の説明",
                    "caption": "補足",
                    "credit": "出典",
                },
            },
        },
    )


def test_save_metadata_search_and_rebuild_from_files(client, tmp_path, monkeypatch):
    response = save(client)
    assert response.status_code == 200, response.text
    saved = response.json()
    sid = saved["id"]
    loaded = client.get(f"/api/wiki/sources/{sid}/edit").json()
    assert loaded == saved
    assert client.get(f"/api/wiki/sources/{sid}").json()["article_info"] == saved["article_info"]
    assert client.get(f"/api/wiki/sources/{sid}/markdown").text == saved["body"]
    assert sid in {hit["source_id"] for hit in search(client, "検索可能")["output"]["results"]}
    record = Store(tmp_path).files.load()[sid]
    assert record["metadata"]["article_info"] == saved["article_info"]
    # The search DB is disposable; article information must belong to the content store.
    client.app.state.knowledge.close()
    client.app.state.knowledge.thread.join(timeout=5)
    assert not client.app.state.knowledge.thread.is_alive()
    del client.app.state.knowledge
    # Restore only portable content into a clean data root. No old search DB or
    # still-closing Writer file handle can participate in the reconstruction.
    restored = tmp_path / "restored"
    shutil.copytree(tmp_path / "content", restored / "content")
    assert not (restored / "runtime").exists()
    monkeypatch.setattr(desk_config, "DATA", restored)
    assert client.get(f"/api/wiki/sources/{sid}/edit").json() == saved
    assert client.get(f"/api/wiki/sources/{sid}").json()["article_info"] == saved["article_info"]


def test_collision_identical_retry_and_metadata_conflict(client):
    first = save(client).json()
    assert save(client).status_code == 409
    payload = {k: v for k, v in first.items() if k != "id"}
    payload["etag"] = None  # response lost on initial creation
    assert save(client, payload, first["id"]).json() == first
    payload["etag"] = first["etag"]
    payload["article_info"]["subtitle"] = "他のタブで更新"
    changed = save(client, payload, first["id"])
    assert changed.status_code == 200
    assert changed.json()["etag"] != first["etag"]
    payload["body"] += "競合する本文"
    assert save(client, payload, first["id"]).status_code == 412
    assert client.get(f"/api/wiki/sources/{first['id']}/edit").json()["body"] == first["body"]
    assert len(client.get("/api/wiki/catalog").json()["articles"]) == 1


def test_edit_imported_markdown_retains_unknown_frontmatter_and_translation_metadata(client):
    raw = '---\r\ncustom: "untouched"\r\ntranslation_group: guide\r\n---\r\n# Original\r\n\r\n```mermaid\r\ngraph LR; A-->B\r\n```\r\n'
    sid = article(client, "imported.md", raw)
    existing = client.get(f"/api/wiki/sources/{sid}/edit").json()
    assert existing["body"] == raw
    existing["title"] = "管理タイトル"
    existing["article_info"]["subtitle"] = "副題のみ編集"
    assert save(client, {k: v for k, v in existing.items() if k != "id"}, sid).status_code == 200
    assert client.get(f"/api/wiki/sources/{sid}/edit").json()["body"] == raw
    assert client.get(f"/api/wiki/sources/{sid}").json()["translation_group"] == "guide"


@pytest.mark.parametrize(
    "changes",
    [
        {"path": "../escape.md"},
        {"path": "bad.csv"},
        {"namespace": "a/b"},
        {"title": "   "},
        {"body": "  \n"},
        {"body": "a\x00b"},
        {"article_info": {"original_url": "javascript:alert(1)"}},
        {"article_info": {"image": {"url": "data:image/svg+xml,<svg/>"}}},
        {"article_info": {"keywords": ["x"] * 21}},
        {"article_info": {"keywords": ["x\ninvalid"]}},
    ],
)
def test_reject_invalid_articles_without_publishing(client, changes):
    value = {"namespace": "Wiki", "path": "a.md", "title": "Title", "body": "Body", **changes}
    assert save(client, value).status_code == 422
    assert client.get("/api/wiki/catalog").json()["articles"] == []


def test_uploaded_cover_keeps_bytes_and_information(client, tmp_path):
    stream = io.BytesIO()
    Image.new("RGB", (4, 3), "teal").save(stream, format="PNG")
    raw = stream.getvalue()
    uploaded = client.post("/api/wiki/images", files={"file": ("cover.png", raw, "image/png")})
    assert uploaded.status_code == 201
    url = uploaded.json()["url"]
    assert client.get(url).content == raw
    assert client.get(url).headers["content-type"] == "image/png"
    saved = save(
        client,
        {
            "namespace": "Wiki",
            "path": "cover.md",
            "title": "Cover",
            "body": f'![代替]({url} "説明")',
            "article_info": {
                "image": {"url": url, "alt": "代替", "caption": "説明", "credit": "作者"}
            },
        },
    ).json()
    loaded = client.get(f"/api/wiki/sources/{saved['id']}/edit").json()
    assert loaded["article_info"]["image"] == saved["article_info"]["image"]
    assert Store(tmp_path).files.target("wiki/images/" + url.rsplit("/", 1)[1]).read_bytes() == raw
    assert (
        client.post(
            "/api/wiki/images", files={"file": ("fake.png", b"<svg onload=alert(1)>", "image/png")}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/wiki/images",
            files={"file": ("big.png", b"x" * (3 * 1024 * 1024 + 1), "image/png")},
        ).status_code
        == 413
    )


def test_missing_image_deleted_article_and_csv_cannot_be_saved(client):
    missing = {
        "namespace": "Wiki",
        "path": "missing.md",
        "title": "Title",
        "body": "![missing](/api/wiki/images/" + "a" * 64 + ".png)",
        "article_info": {"image": {"url": "/api/wiki/images/" + "a" * 64 + ".png"}},
    }
    assert save(client, missing).status_code == 422
    saved = save(client).json()
    client.delete(f"/api/wiki/sources/{saved['id']}")
    assert (
        save(client, {k: v for k, v in saved.items() if k != "id"}, saved["id"]).status_code == 409
    )
    sid = article(client, "index.csv", "title,path\nTitle,a.md\n")
    assert client.get(f"/api/wiki/sources/{sid}/edit").status_code == 404


def test_first_body_image_follows_markdown_order_and_resets_stale_metadata(client):
    payload = {
        "namespace": "Wiki",
        "path": "first.md",
        "title": "First image",
        "body": '---\ncover: "![fake](https://example.com/front.png)"\n---\n\n```md\n![fake](https://example.com/code.png)\n```\n\n> ![先頭][cover]\n\n![次](https://example.com/second.png)\n\n[cover]: https://example.com/first.png "説明"',
        "article_info": {
            "image": {"url": "https://example.com/unrelated.png", "credit": "別画像の出典"}
        },
    }
    response = save(client, payload)
    assert response.status_code == 200, response.text
    first = response.json()
    assert first["article_info"]["image"] == {
        "url": "https://example.com/first.png",
        "alt": "先頭",
        "caption": "説明",
        "credit": "",
    }
    update = {k: v for k, v in first.items() if k != "id"}
    update["article_info"]["image"]["credit"] = "先頭の出典"
    first = save(client, update, first["id"]).json()
    assert first["article_info"]["image"]["credit"] == "先頭の出典"
    update = {k: v for k, v in first.items() if k != "id"}
    update["body"] = (
        "![次](https://example.com/second.png)\n\n![先頭](https://example.com/first.png)"
    )
    second = save(client, update, first["id"]).json()
    assert second["article_info"]["image"]["url"].endswith("second.png")
    assert second["article_info"]["image"]["credit"] == ""
    update = {k: v for k, v in second.items() if k != "id"}
    update["body"] = "No image remains."
    cleared = save(client, update, first["id"]).json()
    assert not any(cleared["article_info"]["image"].values())


def test_native_editor_state_keeps_formatting_comments_and_nested_image_order(
    client, tmp_path, monkeypatch
):
    document = {
        "version": 1,
        "value": [
            {
                "type": "p",
                "align": "center",
                "children": [
                    {"text": "Rich text", "color": "#176779", "fontSize": "24px", "underline": True}
                ],
            },
            {
                "type": "column_group",
                "children": [
                    {
                        "type": "column",
                        "children": [
                            {
                                "type": "img",
                                "url": "https://example.com/one.png",
                                "alt": "一枚目",
                                "caption": [{"text": "補足"}],
                                "children": [{"text": ""}],
                            }
                        ],
                    }
                ],
            },
        ],
        "discussions": [
            {
                "id": "d1",
                "comments": [
                    {
                        "id": "c1",
                        "contentRich": [{"type": "p", "children": [{"text": "Comment"}]}],
                        "createdAt": "2026-10-16T00:00:00Z",
                    }
                ],
            }
        ],
    }
    response = save(
        client,
        {
            "namespace": "Wiki",
            "path": "rich.md",
            "title": "Rich",
            "body": "Rich text",
            "editor_document": document,
        },
    )
    assert response.status_code == 200, response.text
    saved = response.json()
    assert saved["editor_document"] == document
    assert saved["article_info"]["image"]["url"].endswith("one.png")
    assert client.get(f"/api/wiki/sources/{saved['id']}").json()["editor_document"] == document
    assert Store(tmp_path).files.load()[saved["id"]]["metadata"]["editor_document"] == document
    client.app.state.knowledge.close()
    client.app.state.knowledge.thread.join(timeout=5)
    assert not client.app.state.knowledge.thread.is_alive()
    del client.app.state.knowledge
    # Restore only portable content into a clean data root. No old search DB or
    # still-closing Writer file handle can participate in the reconstruction.
    restored = tmp_path / "restored"
    shutil.copytree(tmp_path / "content", restored / "content")
    assert not (restored / "runtime").exists()
    monkeypatch.setattr(desk_config, "DATA", restored)
    assert client.get(f"/api/wiki/sources/{saved['id']}/edit").json()["editor_document"] == document
    invalid = {k: v for k, v in saved.items() if k != "id"}
    invalid["editor_document"] = {"version": 1, "value": [{"type": "p", "children": []}]}
    assert save(client, invalid, saved["id"]).status_code == 422


def test_generic_media_is_immutable_and_never_served_as_active_html(client):
    raw = b"<html><script>alert(1)</script></html>"
    response = client.post("/api/wiki/media", files={"file": ("sample.html", raw, "text/html")})
    assert response.status_code == 201
    loaded = client.get(response.json()["url"])
    assert loaded.content == raw
    assert loaded.headers["content-type"] == "application/octet-stream"
    assert loaded.headers["content-disposition"] == "attachment"
    assert "default-src 'none'" in loaded.headers["content-security-policy"]


def test_markdown_reimport_clears_stale_rich_document_and_reselects_body_image(client):
    initial = save(
        client,
        {
            "namespace": "Wiki",
            "path": "reimport.md",
            "title": "Rich",
            "body": "Old content",
            "editor_document": {
                "version": 1,
                "value": [{"type": "p", "children": [{"text": "Old content", "underline": True}]}],
            },
        },
    ).json()
    same = client.post(
        "/api/wiki/import",
        data={"namespace": "Wiki"},
        files=[("files", ("reimport.md", b"Old content", "text/markdown"))],
    )
    assert same.status_code == 201
    assert (
        client.get(f"/api/wiki/sources/{initial['id']}").json()["editor_document"]
        == initial["editor_document"]
    )
    raw = 'New content\n\n![new](https://example.com/new.png "New caption")'
    changed = client.post(
        "/api/wiki/import",
        data={"namespace": "Wiki"},
        files=[("files", ("reimport.md", raw.encode(), "text/markdown"))],
    )
    assert changed.status_code == 201
    loaded = client.get(f"/api/wiki/sources/{initial['id']}").json()
    assert loaded.get("editor_document") is None
    assert loaded["body"] == raw
    assert loaded["article_info"]["image"]["url"] == "https://example.com/new.png"


def test_candidate_uses_current_native_caption_and_preserves_explicit_empty_alt(client):
    response = save(
        client,
        {
            "namespace": "Wiki",
            "path": "caption.md",
            "title": "Caption",
            "body": '![old](https://example.com/photo.png "Old caption")',
            "editor_document": {
                "version": 1,
                "value": [
                    {
                        "type": "img",
                        "url": "https://example.com/photo.png",
                        "alt": "",
                        "title": "Old caption",
                        "caption": [{"text": "New caption"}],
                        "children": [{"text": ""}],
                    }
                ],
            },
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["article_info"]["image"] == {
        "url": "https://example.com/photo.png",
        "alt": "",
        "caption": "New caption",
        "credit": "",
    }
