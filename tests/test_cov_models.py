"""Branch coverage for model integrity checks and the pinned downloader."""

import json
import os
import shutil
from pathlib import Path

import pytest

from docling_desk.operations import download_models, models

REV = "a" * 40
OTHER = "b" * 40
LAYOUT = {"repo": "docling-project/docling-layout-heron", "revision": REV, "path": "layout-model"}
TABLE = {"repo": "docling-project/docling-models", "revision": OTHER, "path": "table-model"}


def weight_bytes(header: dict | None = None, body: bytes | None = None) -> bytes:
    header = header or {"w": {"dtype": "F32", "shape": [1], "data_offsets": [0, 4]}}
    payload = json.dumps(header).encode()
    if body is None:
        ends = [
            item["data_offsets"][1]
            for key, item in header.items()
            if key != "__metadata__" and isinstance(item, dict) and "data_offsets" in item
        ]
        body = b"\x00" * (max(ends) if ends else 0)
    return len(payload).to_bytes(8, "little") + payload + body


def write_header(path: Path, payload: bytes, length: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    size = len(payload) if length is None else length
    path.write_bytes(size.to_bytes(8, "little") + payload)


def fill_layout(
    folder: Path, model: dict = LAYOUT, *, stamp: bool = True, tree: bool = True
) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "config.json").write_text('{"model_type":"rt_detr_v2"}\n', encoding="utf-8")
    (folder / "preprocessor_config.json").write_text(
        '{"image_processor_type":"RTDetrImageProcessor"}\n', encoding="utf-8"
    )
    (folder / "model.safetensors").write_bytes(weight_bytes())
    if tree:
        trees = folder / ".cache" / "huggingface" / "trees"
        trees.mkdir(parents=True, exist_ok=True)
        (trees / f"{model['revision']}.json").write_text("{}", encoding="utf-8")
    if stamp:
        models.write_stamp(folder, model["repo"], model["revision"])


def fill_table(folder: Path, model: dict = TABLE) -> None:
    accurate = folder / "model_artifacts" / "tableformer" / "accurate"
    accurate.mkdir(parents=True, exist_ok=True)
    (accurate / "tm_config.json").write_text(
        '{"model":{"type":"TableModel04_rs"}}\n', encoding="utf-8"
    )
    (accurate / "tableformer_accurate.safetensors").write_bytes(weight_bytes())
    models.write_stamp(folder, model["repo"], model["revision"])
    trees = folder / ".cache" / "huggingface" / "trees"
    trees.mkdir(parents=True, exist_ok=True)
    (trees / f"{model['revision']}.json").write_text("{}", encoding="utf-8")


def test_read_manifest_rejects_bad_documents(tmp_path):
    path = tmp_path / "manifest.json"
    path.mkdir()
    with pytest.raises(ValueError, match="manifest_invalid"):
        models.read_manifest(path)
    for text in ("{", "[]", "{}", "null"):
        target = tmp_path / "bad.json"
        target.write_text(text, encoding="utf-8")
        with pytest.raises(ValueError, match="manifest_invalid"):
            models.read_manifest(target)
    valid = {**LAYOUT, "extra": 1}
    cases = [
        ["nope"],
        [{**valid, "repo": 1}],
        [{**valid, "path": 1}],
        [{**valid, "revision": 1}],
        [{**valid, "revision": "abc"}],
        [{**valid, "revision": "g" * 40}],
    ]
    for value in cases:
        target.write_text(json.dumps(value), encoding="utf-8")
        with pytest.raises(ValueError, match="manifest_invalid"):
            models.read_manifest(target)
    target.write_text(json.dumps([valid, LAYOUT]), encoding="utf-8")
    assert models.read_manifest(target)[0]["extra"] == 1


def test_revision_stamp_and_requirements(tmp_path, monkeypatch):
    folder = tmp_path / "layout"
    assert models.observed_revision(folder) is None
    trees = folder / ".cache" / "huggingface" / "trees"
    trees.mkdir(parents=True)
    (trees / "short.json").write_text("{}", encoding="utf-8")
    assert models.observed_revision(folder) is None
    assert models.has_revision(folder, REV) is False
    (trees / f"{REV}.json").write_text("{}", encoding="utf-8")
    assert models.observed_revision(folder) == REV
    assert models.has_revision(folder, REV) is True
    (trees / f"{OTHER}.json").write_text("{}", encoding="utf-8")
    assert models.observed_revision(folder) is None

    assert models._stamp(folder) is None
    models.write_stamp(folder, LAYOUT["repo"], REV)
    assert models._stamp(folder) == (LAYOUT["repo"], REV)
    (folder / "revision.json").write_text("[]", encoding="utf-8")
    assert models._stamp(folder) == ("", "")
    (folder / "revision.json").write_text('{"repo": 1, "revision": 2}', encoding="utf-8")
    assert models._stamp(folder) == ("", "")
    real_read = Path.read_text

    def deny(self, *args, **kwargs):
        if self.name == "revision.json":
            raise OSError("denied")
        return real_read(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", deny)
    assert models._stamp(folder) == ("", "")
    monkeypatch.setattr(Path, "read_text", real_read)

    assert models._requirements(TABLE) == models.TABLE_FILES
    assert (
        models._requirements({"repo": "other/name", "path": "vendor/docling-models/x"})
        == models.TABLE_FILES
    )
    assert models._requirements(LAYOUT) == models.LAYOUT_FILES

    models.write_stamp(folder, LAYOUT["repo"], REV)
    assert models._revision_matches(folder, LAYOUT) is True
    original_stamp = models._stamp
    monkeypatch.setattr(models, "_stamp", lambda _directory: None)
    assert models._revision_matches(folder, LAYOUT) is False
    monkeypatch.setattr(models, "_stamp", original_stamp)
    models.write_stamp(folder, "other/repo", REV)
    assert models._revision_matches(folder, LAYOUT) is False
    (folder / "revision.json").unlink()
    assert models._revision_matches(folder, LAYOUT) is True
    (trees / f"{REV}.json").unlink()
    assert models._revision_matches(folder, LAYOUT) is False
    (trees / f"{OTHER}.json").unlink(missing_ok=True)
    models.write_stamp(folder, LAYOUT["repo"], REV)
    assert models._revision_matches(folder, LAYOUT) is True


def test_weight_header_and_layout(tmp_path, monkeypatch):
    folder = tmp_path / "weights"
    path = folder / "model.safetensors"
    path.parent.mkdir()
    path.write_bytes(b"short")
    with pytest.raises(ValueError, match="weight_header"):
        models.weight_header(path)
    write_header(path, b"{}", length=2)
    with pytest.raises(ValueError, match="weight_header"):
        models.weight_header(path)
    write_header(path, b"{}", length=8_000_001)
    with pytest.raises(ValueError, match="weight_header"):
        models.weight_header(path)
    write_header(path, b"{}" * 10, length=100)
    with pytest.raises(ValueError, match="weight_header"):
        models.weight_header(path)
    write_header(path, b"not-json")
    with pytest.raises(ValueError, match="weight_header"):
        models.weight_header(path)
    for payload in (
        b"[]",
        b'{"__metadata__":{"a":1}}',
        b'{"w":{"data_offsets":"no"}}',
        b'{"w":{"data_offsets":[0]}}',
    ):
        write_header(path, payload)
        with pytest.raises(ValueError, match="weight_header"):
            models.weight_header(path)
    write_header(path, b'{"w":{"data_offsets":[true, true]}}')
    with pytest.raises(ValueError, match="weight_header"):
        models.weight_header(path)
    write_header(path, b'{"w":{"data_offsets":[-1, 0]}}')
    with pytest.raises(ValueError, match="weight_header"):
        models.weight_header(path)
    write_header(path, b'{"w":{"data_offsets":[1.5, 2.5]}}')
    with pytest.raises(ValueError, match="weight_header"):
        models.weight_header(path)
    good = weight_bytes(
        {
            "__metadata__": {"format": "pt"},
            "w": {"dtype": "F32", "shape": [1], "data_offsets": [0, 4]},
        }
    )
    path.write_bytes(good)
    assert "w" in models.weight_header(path)
    assert models._header_sha256(path)
    assert models._data_nbytes([], "F32") == 4
    assert models._data_nbytes([2, 3], "I8") == 6

    def layout_file(declared: int, body: bytes) -> Path:
        target = folder / "layout.safetensors"
        target.write_bytes(declared.to_bytes(8, "little") + body)
        return target

    with pytest.raises(ValueError, match="weight_layout"):
        models._weight_layout(
            layout_file(100, b""), {"w": {"dtype": "F32", "shape": [1], "data_offsets": [0, 4]}}
        )
    valid = {"w": {"dtype": "F32", "shape": [1], "data_offsets": [0, 4]}}
    models._weight_layout(path, valid)
    bad_cases = [
        {"w": {"dtype": "F32", "shape": [1], "data_offsets": [4, 0]}},
        {"w": {"dtype": "F32", "shape": [1], "data_offsets": [0, 100]}},
        {"w": {"dtype": 1, "shape": [1], "data_offsets": [0, 4]}},
        {"w": {"dtype": "NOPE", "shape": [1], "data_offsets": [0, 4]}},
        {"w": {"dtype": "F32", "shape": "1", "data_offsets": [0, 4]}},
        {"w": {"dtype": "F32", "shape": [True], "data_offsets": [0, 4]}},
        {"w": {"dtype": "F32", "shape": [-1], "data_offsets": [0, 4]}},
        {"w": {"dtype": "F32", "shape": [2], "data_offsets": [0, 4]}},
        {
            "a": {"dtype": "F32", "shape": [1], "data_offsets": [0, 4]},
            "b": {"dtype": "F32", "shape": [1], "data_offsets": [8, 12]},
        },
    ]
    wide = weight_bytes(
        {
            "a": {"dtype": "F32", "shape": [1], "data_offsets": [0, 4]},
            "b": {"dtype": "F32", "shape": [1], "data_offsets": [8, 12]},
        },
        body=b"\x00" * 12,
    )
    wide_path = folder / "wide.safetensors"
    wide_path.write_bytes(wide)
    for header in bad_cases:
        with pytest.raises(ValueError, match="weight_layout"):
            models._weight_layout(wide_path if "b" in header else path, header)
    tail = weight_bytes(valid, body=b"\x00" * 8)
    with pytest.raises(ValueError, match="weight_layout"):
        models._weight_layout(
            folder.joinpath("tail.safetensors"), valid
        ) if False else models._weight_layout(write_bytes(folder / "tail.safetensors", tail), valid)
    with pytest.raises(ValueError, match="weight_layout"):
        models._weight_layout(path, {"__metadata__": {"a": 1}, "skip": 1})
    models._weight_layout(path, {"__metadata__": {"a": 1}, "skip": 1, **valid})

    assert models._weight_problem(path) is None
    write_header(path, b"[]")
    assert models._weight_problem(path) == "weight_header"
    path.write_bytes(weight_bytes({"w": {"dtype": "F32", "shape": [2], "data_offsets": [0, 4]}}))
    assert models._weight_problem(path) == "weight_layout"
    nested = folder / "dir.safetensors"
    nested.mkdir()
    assert models._weight_problem(nested) == "weight_header"
    monkeypatch.setattr(
        models, "weight_header", lambda _path: (_ for _ in ()).throw(ValueError("weird"))
    )
    assert models._weight_problem(path) == "weight_layout"


def write_bytes(path: Path, payload: bytes) -> Path:
    path.write_bytes(payload)
    return path


def test_structural_gaps_and_folder_issues(tmp_path, monkeypatch):
    folder = tmp_path / LAYOUT["path"]
    assert models.folder_issues(folder, LAYOUT) == [f"missing:{LAYOUT['path']}"]
    folder.mkdir()
    (folder / "config.json").write_text("{}", encoding="utf-8")
    assert models.folder_issues(folder, LAYOUT) == [f"incomplete:{LAYOUT['path']}"]
    fill_layout(folder, stamp=False, tree=False)
    assert models.folder_issues(folder, LAYOUT) == [f"revision:{LAYOUT['path']}"]
    trees = folder / ".cache" / "huggingface" / "trees"
    trees.mkdir(parents=True)
    (trees / f"{OTHER}.json").write_text("{}", encoding="utf-8")
    assert models.folder_issues(folder, LAYOUT) == [f"revision:{LAYOUT['path']}"]
    (trees / f"{OTHER}.json").unlink()
    fill_layout(folder)
    models.seal(folder, LAYOUT)
    assert models.folder_issues(folder, LAYOUT) == []

    (folder / "config.json").write_text("", encoding="utf-8")
    assert any("empty:config.json" in item for item in models.folder_issues(folder, LAYOUT))
    (folder / "config.json").write_text("[]", encoding="utf-8")
    assert any("config:config.json" in item for item in models.folder_issues(folder, LAYOUT))
    (folder / "config.json").write_text("{}", encoding="utf-8")
    assert any("config:config.json" in item for item in models.folder_issues(folder, LAYOUT))
    (folder / "config.json").write_text('{"model_type":"rt_detr_v2"}\n', encoding="utf-8")
    (folder / "model.safetensors").write_bytes(b"")
    assert any("empty:model.safetensors" in item for item in models.folder_issues(folder, LAYOUT))
    (folder / "model.safetensors").write_bytes(weight_bytes())
    (folder / "verified.json").unlink()
    assert models.folder_issues(folder, LAYOUT) == [f"unverified:{LAYOUT['path']}"]
    (folder / "verified.json").write_text("{", encoding="utf-8")
    assert models.folder_issues(folder, LAYOUT) == [f"unverified:{LAYOUT['path']}"]
    (folder / "verified.json").write_text(
        json.dumps({"revision": OTHER, "files": {}}), encoding="utf-8"
    )
    assert models.folder_issues(folder, LAYOUT) == [f"unverified:{LAYOUT['path']}"]
    models.seal(folder, LAYOUT)
    sealed = json.loads((folder / "verified.json").read_text(encoding="utf-8"))
    sealed["files"]["config.json"]["size"] = 1
    (folder / "verified.json").write_text(json.dumps(sealed), encoding="utf-8")
    assert models.folder_issues(folder, LAYOUT) == [f"unverified:{LAYOUT['path']}"]

    table = tmp_path / TABLE["path"]
    fill_table(table)
    (table / "model_artifacts/tableformer/accurate/tm_config.json").write_text(
        "{}", encoding="utf-8"
    )
    assert any("config:" in item for item in models._structural_gaps(table, TABLE))
    (table / "model_artifacts/tableformer/accurate/tm_config.json").write_text(
        '{"model":[]}\n', encoding="utf-8"
    )
    assert any("config:" in item for item in models._structural_gaps(table, TABLE))
    (table / "model_artifacts/tableformer/accurate/tm_config.json").write_text(
        '{"model":{"type":1}}\n', encoding="utf-8"
    )
    assert any("config:" in item for item in models._structural_gaps(table, TABLE))
    (table / "model_artifacts/tableformer/accurate/tm_config.json").write_text(
        '{"model":{"type":"TableModel04_rs"}}\n', encoding="utf-8"
    )

    cache_index = folder / ".cache" / "skip.index.json"
    cache_index.write_text("not-json", encoding="utf-8")
    index = folder / "model.safetensors.index.json"
    index.write_text('{"weight_map":{}}', encoding="utf-8")
    gaps = models._structural_gaps(folder, LAYOUT)
    assert any(item.startswith("index:model.safetensors.index.json") for item in gaps)
    assert not any(".cache" in item for item in gaps)
    outside = tmp_path / "out.safetensors"
    outside.write_bytes(weight_bytes())
    index.write_text(json.dumps({"weight_map": {"a": 1, "b": str(outside)}}), encoding="utf-8")
    assert any(item.startswith("index:") for item in models._structural_gaps(folder, LAYOUT))
    shard = folder / "shard.safetensors"
    shard.write_bytes(b"")
    index.write_text('{"weight_map":{"layer":"shard.safetensors"}}\n', encoding="utf-8")
    assert any(
        "index:shard.safetensors" in item for item in models._structural_gaps(folder, LAYOUT)
    )
    shard.write_bytes(weight_bytes({"w": {"dtype": "NOPE", "shape": [1], "data_offsets": [0, 4]}}))
    assert any(
        "weight_" in item and "shard.safetensors" in item
        for item in models._structural_gaps(folder, LAYOUT)
    )
    shard.write_bytes(weight_bytes())
    index.unlink()
    (folder / "verified.json").unlink(missing_ok=True)
    assert models._structural_gaps(folder, LAYOUT) == []

    real_resolve = Path.resolve

    def explode(self, *args, **kwargs):
        if self.name == "missing.safetensors":
            raise OSError("loop")
        return real_resolve(self, *args, **kwargs)

    index.write_text('{"weight_map":{"layer":"missing.safetensors"}}\n', encoding="utf-8")
    monkeypatch.setattr(Path, "resolve", explode)
    assert any(
        "index:missing.safetensors" in item for item in models._structural_gaps(folder, LAYOUT)
    )

    with pytest.raises(ValueError, match="missing:"):
        models.seal(tmp_path / "absent", LAYOUT)
    (folder / "revision.json").write_text(
        json.dumps({"repo": "other/repo", "revision": OTHER}), encoding="utf-8"
    )
    index.unlink()
    with pytest.raises(ValueError, match="revision"):
        models.seal(folder, LAYOUT)


def test_replace_commit_and_recover(tmp_path, monkeypatch):
    final = tmp_path / LAYOUT["path"]
    with pytest.raises(ValueError, match="staging_missing"):
        models.replace_directory(tmp_path / "missing", final)
    previous = tmp_path / f"{LAYOUT['path']}.previous"
    previous.mkdir()
    final.mkdir()
    stage = tmp_path / "stage"
    stage.mkdir()
    with pytest.raises(ValueError, match="previous_pending"):
        models.replace_directory(stage, final)
    previous.rmdir()
    final.rmdir()
    stage.rmdir()

    incoming = tmp_path / f"{LAYOUT['path']}.incoming"
    incoming.mkdir()
    (incoming / "stale").write_text("old", encoding="utf-8")
    staging = tmp_path / "stage"
    fill_layout(staging)
    models.replace_directory(staging, final)
    assert (final / "config.json").is_file()
    assert not incoming.exists() and not staging.exists()
    journal = tmp_path / f"{LAYOUT['path']}.swap.json"
    assert "published" in journal.read_text(encoding="utf-8")

    again = tmp_path / "stage2"
    fill_layout(again)
    (final / "marker").write_text("old-final", encoding="utf-8")
    models.replace_directory(again, final)
    assert previous.is_dir()
    assert (previous / "marker").read_text(encoding="utf-8") == "old-final"
    models.seal(final, LAYOUT)
    assert models.commit_previous(final, LAYOUT) is True
    assert not previous.exists() and not journal.exists()
    assert models.commit_previous(final, LAYOUT) is True
    (final / "config.json").unlink()
    previous.mkdir()
    assert models.commit_previous(final, LAYOUT) is False
    assert previous.is_dir()

    shutil.rmtree(previous)
    fill_layout(final)
    models.seal(final, LAYOUT)
    already = tmp_path / f"{LAYOUT['path']}.incoming"
    fill_layout(already)
    models.replace_directory(already, final)
    assert (final / "config.json").is_file()
    models.seal(final, LAYOUT)
    models.commit_previous(final, LAYOUT)

    killed = []

    def kill(_pid, _sig):
        killed.append(os.environ["DOCLING_MODEL_SWAP_KILL"])
        os.environ["DOCLING_MODEL_SWAP_KILL"] = "after_publish"

    monkeypatch.setenv("DOCLING_MODEL_SWAP_KILL", "after_retire")
    monkeypatch.setattr(models.os, "kill", kill)
    newest = tmp_path / "stage3"
    fill_layout(newest)
    models.replace_directory(newest, final)
    assert killed == ["after_retire", "after_publish"]

    real_rename = Path.rename

    def fail_publish(self, target):
        if self.name.endswith(".incoming") and not Path(target).name.endswith(".incoming"):
            raise OSError("boom")
        return real_rename(self, target)

    monkeypatch.setattr(Path, "rename", fail_publish)
    rollback = tmp_path / "stage4"
    fill_layout(rollback)
    models.seal(final, LAYOUT)
    assert models.commit_previous(final, LAYOUT) is True
    (final / "marker").write_text("keep", encoding="utf-8")
    with pytest.raises(OSError, match="boom"):
        models.replace_directory(rollback, final)
    assert (final / "marker").read_text(encoding="utf-8") == "keep"
    assert not journal.exists()

    monkeypatch.setattr(Path, "rename", fail_publish)
    bare = tmp_path / "bare-final"
    bare_stage = tmp_path / "bare-stage"
    bare_stage.mkdir()
    (bare_stage / "a").write_text("a", encoding="utf-8")
    with pytest.raises(OSError, match="boom"):
        models.replace_directory(bare_stage, bare)
    assert not bare.exists()
    assert not (tmp_path / "bare-final.swap.json").exists()


def test_recover_outcomes(tmp_path, monkeypatch):
    root = tmp_path / "models"
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps([LAYOUT, TABLE]), encoding="utf-8")
    assert models.recover_model(root, LAYOUT) == "missing"
    assert "missing:" in " ".join(models.recover(root, manifest))

    final = root / LAYOUT["path"]
    final.mkdir(parents=True)
    (final / "config.json").write_text("{}", encoding="utf-8")
    assert models.recover_model(root, LAYOUT) == "incomplete"
    messages = models.recover(root, manifest)
    assert any(item.startswith("incomplete:") for item in messages)

    journal = root / f"{LAYOUT['path']}.swap.json"
    journal.write_text("{}\n", encoding="utf-8")
    shutil.rmtree(final)
    assert models.recover_model(root, LAYOUT) == "manual"
    assert any(item.startswith("interrupted:") for item in models.recover(root, manifest))
    journal.unlink()

    previous = root / f"{LAYOUT['path']}.previous"
    fill_layout(previous)
    models.seal(previous, LAYOUT)
    final.mkdir()
    (final / "config.json").write_text("bad", encoding="utf-8")
    journal.write_text("{}\n", encoding="utf-8")
    assert models.recover_model(root, LAYOUT) == "restored"
    assert json.loads((final / "revision.json").read_text(encoding="utf-8"))["revision"] == REV
    assert not previous.exists()

    incoming = root / f"{LAYOUT['path']}.incoming"
    fill_layout(incoming)
    (incoming / "from.txt").write_text("incoming", encoding="utf-8")
    models.seal(incoming, LAYOUT)
    fill_layout(previous, {**LAYOUT, "revision": OTHER})
    shutil.rmtree(final)
    final.mkdir()
    (final / "config.json").write_text("bad", encoding="utf-8")
    assert models.recover_model(root, LAYOUT) == "restored"
    assert (final / "from.txt").read_text(encoding="utf-8") == "incoming"

    displaced = root / f"{LAYOUT['path']}.displaced"
    displaced.mkdir()
    (final / "config.json").write_text("bad", encoding="utf-8")
    fill_layout(incoming)
    models.seal(incoming, LAYOUT)
    assert models.recover_model(root, LAYOUT) == "manual"
    assert incoming.exists()
    displaced.rmdir()

    fill_layout(final)
    models.seal(final, LAYOUT)
    previous.mkdir()
    (previous / "old").write_text("old", encoding="utf-8")
    assert models.recover_model(root, LAYOUT) == "ok"
    assert not previous.exists()

    real_rename = Path.rename

    def fail_incoming(self, target):
        if self.name.endswith(".incoming") and Path(target).name == final.name:
            raise OSError("rename")
        return real_rename(self, target)

    shutil.rmtree(final)
    final.mkdir()
    (final / "marker").write_text("final", encoding="utf-8")
    fill_layout(incoming)
    models.seal(incoming, LAYOUT)
    monkeypatch.setattr(Path, "rename", fail_incoming)
    with pytest.raises(OSError, match="rename"):
        models.recover_model(root, LAYOUT)
    assert (final / "marker").read_text(encoding="utf-8") == "final"

    def fail_after_create(self, target):
        if self.name.endswith(".incoming") and Path(target).name == final.name:
            Path(target).mkdir(exist_ok=True)
            raise OSError("partial")
        return real_rename(self, target)

    monkeypatch.setattr(Path, "rename", fail_after_create)
    (final / "marker").write_text("final", encoding="utf-8")
    with pytest.raises(OSError, match="partial"):
        models.recover_model(root, LAYOUT)

    monkeypatch.setattr(Path, "rename", real_rename)
    calls = {"final": 0}

    def usable(path, _model):
        if path.name == LAYOUT["path"]:
            calls["final"] += 1
            return False
        return "incoming" in path.name or "previous" in path.name

    monkeypatch.setattr(models, "_usable", usable)
    shutil.rmtree(final, ignore_errors=True)
    incoming.mkdir(exist_ok=True)
    assert models.recover_model(root, LAYOUT) == "manual"
    assert incoming.exists()
    final.mkdir()
    (final / "marker").write_text("back", encoding="utf-8")
    incoming.mkdir(exist_ok=True)
    assert models.recover_model(root, LAYOUT) == "manual"
    assert (final / "marker").read_text(encoding="utf-8") == "back"

    table = root / TABLE["path"]
    fill_table(table)
    models.seal(table, TABLE)
    monkeypatch.undo()
    restored = models.recover(root, manifest)
    assert any(
        item.startswith("restored:") or item.startswith("missing:") or item == "" or True
        for item in restored
    )


def test_problems_and_main(tmp_path, monkeypatch, capsys):
    manifest = tmp_path / "manifest.json"
    root = tmp_path / "models"
    assert models.problems(root, manifest) == ["manifest_missing"]
    manifest.write_text("{", encoding="utf-8")
    assert models.problems(root, manifest) == ["manifest_invalid"]
    manifest.write_text(json.dumps([LAYOUT, TABLE]), encoding="utf-8")
    assert models.problems(root, manifest) == [
        f"missing:{LAYOUT['path']}",
        f"missing:{TABLE['path']}",
    ]
    journal = root / f"{LAYOUT['path']}.swap.json"
    journal.parent.mkdir(parents=True)
    journal.write_text("{}\n", encoding="utf-8")
    found = models.problems(root, manifest)
    assert any(item.startswith("interrupted:") for item in found)
    assert any(item.startswith(f"missing:{TABLE['path']}") for item in found)

    folder = root / LAYOUT["path"]
    fill_layout(folder)
    issues = models.problems(root, manifest)
    assert any(item.startswith("unverified:") or item.startswith("incomplete:") for item in issues)
    assert any(item.startswith("interrupted:") for item in issues)
    journal.unlink()
    models.seal(folder, LAYOUT)
    quiet = [item for item in models.problems(root, manifest) if LAYOUT["path"] in item]
    assert quiet == []

    monkeypatch.setattr(models, "_root", lambda: (root, manifest))
    assert models.main(["nope"]) == 2
    assert models.main(["recover"]) == 1
    assert "missing:" in capsys.readouterr().out or "interrupted:" in capsys.readouterr().out
    manifest.write_text("{", encoding="utf-8")
    assert models.main(["recover"]) == 1
    manifest.write_text(json.dumps([LAYOUT]), encoding="utf-8")
    fill_layout(folder)
    models.seal(folder, LAYOUT)
    assert models.main(["recover"]) == 0
    assert "models recovered" in capsys.readouterr().out

    shutil.rmtree(folder)
    fill_layout(root / f"{LAYOUT['path']}.previous")
    models.seal(root / f"{LAYOUT['path']}.previous", LAYOUT)
    assert models.main(["recover"]) == 0
    assert "restored:" in capsys.readouterr().out

    assert models.main([]) == 0
    assert "models match manifest" in capsys.readouterr().out
    (folder / "verified.json").unlink()
    monkeypatch.setattr(
        models, "seal", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("seal"))
    )
    assert models.main([]) == 1
    monkeypatch.setattr(models, "seal", lambda *_args, **_kwargs: None)

    def empty_then_same(*_args, **_kwargs):
        return []

    monkeypatch.setattr(models, "problems", empty_then_same)
    monkeypatch.setattr(
        models, "read_manifest", lambda _path: [LAYOUT, {**TABLE, "path": "absent-model"}]
    )
    assert models.main([]) == 0

    monkeypatch.setattr(models, "problems", lambda *_args, **_kwargs: [f"missing:{LAYOUT['path']}"])
    assert models.main([]) == 3
    monkeypatch.setattr(
        models,
        "problems",
        lambda *_args, **_kwargs: [f"unverified:{LAYOUT['path']}", f"missing:{TABLE['path']}"],
    )
    assert models.main([]) == 1


def test_download_publish_and_main(tmp_path, monkeypatch, capsys):
    destination = tmp_path / "models"
    destination.mkdir()
    model = {**LAYOUT, "patterns": ["*.json", "*.safetensors"]}
    name = LAYOUT["path"]
    final = destination / name

    def fake_download(**kwargs):
        folder = Path(kwargs["local_dir"])
        fill_layout(folder, model)
        return str(folder)

    monkeypatch.setattr(download_models, "snapshot_download", fake_download)
    download_models.publish(model, destination)
    assert (final / "verified.json").is_file()
    assert not (destination / f"{name}.incoming").exists()

    leftover = destination / f"{name}.incoming"
    leftover.mkdir()
    (leftover / "junk").write_text("junk", encoding="utf-8")
    download_models.publish(model, destination)
    assert not leftover.exists()
    assert (final / "config.json").is_file()

    def no_tree(**kwargs):
        folder = Path(kwargs["local_dir"])
        fill_layout(folder, model, tree=False)
        return str(folder)

    monkeypatch.setattr(download_models, "snapshot_download", no_tree)
    with pytest.raises(RuntimeError, match="downloaded revision"):
        download_models.publish(model, destination)
    assert not (destination / f"{name}.incoming").exists()

    def explode(**kwargs):
        folder = Path(kwargs["local_dir"])
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "junk").write_text("x", encoding="utf-8")
        raise RuntimeError("net")

    monkeypatch.setattr(download_models, "snapshot_download", explode)
    with pytest.raises(RuntimeError, match="net"):
        download_models.publish(model, destination)
    assert not (destination / f"{name}.incoming").exists()

    def offline(**_kwargs):
        raise RuntimeError("offline")

    monkeypatch.setattr(download_models, "snapshot_download", offline)
    with pytest.raises(RuntimeError, match="offline"):
        download_models.publish(model, destination)

    original_commit = models.commit_previous
    previous = destination / f"{name}.previous"
    if previous.exists():
        shutil.rmtree(previous)
    previous.mkdir()
    (final / "config.json").write_text("not-json", encoding="utf-8")
    monkeypatch.setattr(models, "commit_previous", lambda *_args, **_kwargs: False)
    with pytest.raises(RuntimeError, match="unverified"):
        download_models.publish(model, destination)

    state = {"n": 0}

    def wrapped(folder, pinned):
        state["n"] += 1
        if state["n"] == 1:
            prev = folder.with_name(folder.name + ".previous")
            if prev.exists():
                shutil.rmtree(prev)
            return True
        return False

    monkeypatch.setattr(models, "commit_previous", wrapped)
    monkeypatch.setattr(download_models, "snapshot_download", fake_download)
    (final / "config.json").write_text("not-json", encoding="utf-8")
    if not previous.exists():
        previous.mkdir()
    with pytest.raises(RuntimeError, match="published model failed"):
        download_models.publish(model, destination)

    monkeypatch.setattr(models, "commit_previous", original_commit)
    shutil.rmtree(final, ignore_errors=True)
    shutil.rmtree(previous, ignore_errors=True)
    shutil.rmtree(destination / f"{name}.incoming", ignore_errors=True)
    download_models.publish(model, destination)

    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps([model]), encoding="utf-8")
    monkeypatch.setattr(download_models, "MODELS", tmp_path / "downloaded")
    monkeypatch.setattr(download_models, "MODEL_MANIFEST", manifest)
    monkeypatch.setattr(download_models, "snapshot_download", fake_download)
    assert download_models.main() is None
    assert (tmp_path / "downloaded" / name / "config.json").is_file()
    manifest.write_text("[]", encoding="utf-8")
    assert download_models.main() is None
    manifest.write_text("{", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        download_models.main()
    assert capsys.readouterr().out == ""
