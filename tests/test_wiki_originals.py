"""Synthetic workspace originals: rendering, exact downloads and path boundaries."""

import json
import os
from pathlib import Path
from urllib.parse import quote

import pytest
from bs4 import BeautifulSoup
from test_local_knowledge import client as client
from test_wiki_batch import workspace as workspace

from docling_desk import config
from docling_desk.knowledge.originals import Originals


@pytest.fixture
def originals(workspace, client, monkeypatch):
    repo, _, snapshot, _ = workspace
    monkeypatch.setattr(config, "DATA", repo.data)
    root = repo.root / "sources/notion/日本語 フォルダー"
    root.mkdir(parents=True)
    values = {
        "原本 メモ.md": "# 原本\r\n\r\n合成データ\r\n[CSV](<下位/目次 一覧.csv>)\r\n[添付](<下位/添付 ファイル.bin>)\r\n<script>unsafe</script>".encode(),
        "下位/目次 一覧.csv": "title,page_path\r\n原本,../原本 メモ.md\r\n".encode(),
        "下位/添付 ファイル.bin": b"\x00\xff\x01synthetic\r\n",
        "100%25.md": b"# Literal percent filename",
    }
    for name, raw in values.items():
        target = root / name
        target.parent.mkdir(exist_ok=True)
        target.write_bytes(raw)
    appendix = "\n\n## 書き出し原本\n"
    for i, name in enumerate(values):
        appendix += (
            f"[原本{i}](../../../../sources/notion/{quote('日本語 フォルダー/' + name)})\n\n"
        )
    appendix += "[欠落](../../../../sources/notion/missing.md)\n"
    appendix += "[外部領域](../../../../.env)\n"
    appendix += (
        "![添付画像](../../../../sources/notion/日本語%20フォルダー/下位/添付%20ファイル.bin)\n"
    )
    for field in ("original_path", "ja_path"):
        target = repo.root / snapshot["page"][field]
        target.write_bytes(target.read_bytes() + appendix.encode())
    repo.sync()
    catalog = client.get("/api/wiki/catalog").json()["articles"]
    sources = {s["path"]: s for s in catalog}
    original = sources[snapshot["page"]["original_path"]]
    return repo, client, root, values, sources, original


def test_wiki_opens_original_markdown_csv_and_attachments_without_registration(originals):
    repo, client, root, values, sources, original = originals
    before = {name: (root / name).read_bytes() for name in values}
    for path, source in sources.items():
        if not path.endswith("R-1.md"):
            continue
        page = client.get("/api/wiki/sources/" + source["id"]).json()
        links = BeautifulSoup(page["html"], "html.parser")
        for i, (name, raw) in enumerate(values.items()):
            link = links.find("a", string=f"原本{i}")
            assert link["target"] == "_blank" and "original?" in link["href"]
            view = client.get(link["href"])
            assert view.status_code == 200
            assert "読み取り専用" in view.text
            assert "<script>" not in view.text
            assert "default-src 'none'" in view.headers["content-security-policy"]
            download = BeautifulSoup(view.text, "html.parser").find("a", string="原本を取得")
            result = client.get(download["href"])
            assert result.content == raw
            assert (
                "filename*=UTF-8''" + quote(Path(name).name, safe="")
                == result.headers["content-disposition"].split("; ")[1]
            )
            assert result.headers["content-type"] == "application/octet-stream"
        assert links.find("a", string="欠落")["aria-disabled"] == "true"
        assert links.find("a", string="外部領域")["aria-disabled"] == "true"
        assert links.find("a", string="[画像: 添付画像]")["href"]
        assert not links.find("a", string="欠落").get("target")
    assert {name: (root / name).read_bytes() for name in values} == before
    assert not any(s["path"].startswith("sources/") for s in sources.values())
    # Raw Markdown's nested link opens CSV; raw CSV's title links back to the raw Markdown.
    endpoint = f"/api/wiki/sources/{original['id']}/original"
    view = client.get(endpoint, params={"path": "sources/notion/日本語 フォルダー/原本 メモ.md"})
    nested = BeautifulSoup(view.text, "html.parser").find("a", string="CSV")
    csv = client.get(nested["href"])
    assert "<table>" in csv.text
    back = BeautifulSoup(csv.text, "html.parser").find("a", string="原本")
    assert client.get(back["href"]).status_code == 200
    assert repo.data.joinpath("runtime/knowledge/wiki-workspaces.json").is_file()


@pytest.mark.parametrize(
    "path",
    [
        "sources/notion/missing.md",
        "../outside.md",
        "/etc/passwd",
        "sources/../.env",
        "sources/notion/../../.env",
        "sources\\notion\\secret",
        "sources//notion/a",
        "sources/notion/./a",
        "data/translation.sqlite",
        "sources/notion/%2e%2e/secret",
    ],
)
def test_missing_and_outside_files_have_visible_error(originals, path):
    _, client, _, _, _, original = originals
    for download in (False, True):
        result = client.get(
            f"/api/wiki/sources/{original['id']}/original",
            params={"path": path, "download": download},
        )
        assert result.status_code == 404
        assert "原本を参照できません" in result.text


@pytest.mark.parametrize("directory", [False, True])
def test_symlinks_are_rejected_even_when_the_target_is_inside(originals, directory):
    repo, client, root, _, _, original = originals
    link = root / ("alias" if directory else "alias.md")
    link.symlink_to(root / ("下位" if directory else "原本 メモ.md"), target_is_directory=directory)
    path = link.relative_to(repo.root).as_posix() + ("/目次 一覧.csv" if directory else "")
    result = client.get(f"/api/wiki/sources/{original['id']}/original", params={"path": path})
    assert result.status_code == 404
    assert not Originals(repo.data).link(original, path)


def test_deleted_original_and_replaced_source_root_fail_after_link_rendering(originals):
    repo, client, root, _, _, original = originals
    target = root / "原本 メモ.md"
    path = target.relative_to(repo.root).as_posix()
    url = Originals(repo.data).link(original, path)
    assert url
    target.unlink()
    assert client.get(url).status_code == 404
    sources = repo.root / "sources"
    saved = repo.root / "saved-sources"
    sources.rename(saved)
    sources.symlink_to(saved, target_is_directory=True)
    with pytest.raises(ValueError, match="シンボリック"):
        Originals(repo.data).register(repo.root)
    assert (
        client.get(
            f"/api/wiki/sources/{original['id']}/original",
            params={"path": "sources/notion/日本語 フォルダー/下位/目次 一覧.csv"},
        ).status_code
        == 404
    )


def test_file_swap_to_symlink_between_check_and_open_cannot_escape(originals, monkeypatch):
    repo, _, root, _, _, original = originals
    target = root / "race.md"
    target.write_text("Synthetic safe data")
    outside = repo.root / "outside.md"
    outside.write_text("Synthetic outside data")
    real_open = os.open

    def swapped(path, flags, *args, **kwargs):
        if path == "race.md":
            target.unlink()
            target.symlink_to(outside)
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", swapped)
    with pytest.raises(OSError):
        with Originals(repo.data).open(original, target.relative_to(repo.root).as_posix()):
            pytest.fail("Symlink swap must never yield a file")


def test_unregistered_workspace_and_unknown_article_cannot_read_files(originals):
    repo, client, _, _, _, original = originals
    registry = repo.data / "runtime/knowledge/wiki-workspaces.json"
    registry.write_text(json.dumps({"version": 1, "workspaces": {}}))
    endpoint = f"/api/wiki/sources/{original['id']}/original"
    assert (
        client.get(
            endpoint, params={"path": "sources/notion/日本語 フォルダー/原本 メモ.md"}
        ).status_code
        == 404
    )
    assert (
        client.get(
            "/api/wiki/sources/unknown/original", params={"path": "sources/notion/file.md"}
        ).status_code
        == 404
    )


def test_workspace_migration_preserves_original_bytes_and_registers_destination(
    originals, tmp_path
):
    from docling_desk.knowledge.store import Store
    from docling_desk.wiki_batch.files import hash_text
    from docling_desk.wiki_batch.migration import import_workspace

    repo, _, root, values, _, _ = originals
    target, data = tmp_path / "copied-workspace", tmp_path / "copied-app"
    target.mkdir()
    result = import_workspace(repo.root, target, data)
    assert result["paused"] and result["indexed"]
    registry = Originals(data).load()
    assert set(registry) == {hash_text(str(target.resolve()))}
    assert next(iter(registry.values()))["root"] == str(target.resolve())
    source = next(s for s in Store(data).sources("wiki") if s.get("language") == "original")
    for name, raw in values.items():
        relative = (root / name).relative_to(repo.root).as_posix()
        assert (repo.root / relative).read_bytes() == raw
        assert (target / relative).read_bytes() == raw
        with Originals(data).open(source, relative) as stream:
            assert stream.read() == raw
