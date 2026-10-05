import json
import os
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

import docling_desk.app as web
from docling_desk import config as desk_config
from docling_desk.operations.models import (
    commit_previous,
    problems,
    recover,
    replace_directory,
    seal,
    write_stamp,
)

LAYOUT = {
    "repo": "docling-project/docling-layout-heron",
    "revision": "a" * 40,
    "path": "docling-project--docling-layout-heron",
}
TABLE = {
    "repo": "docling-project/docling-models",
    "revision": "b" * 40,
    "path": "docling-project--docling-models",
}


def weight_bytes() -> bytes:
    header = b'{"w":{"dtype":"F32","shape":[1],"data_offsets":[0,4]}}'
    return len(header).to_bytes(8, "little") + header + b"\x00\x00\x00\x00"


def fill_layout(folder, model=LAYOUT):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "config.json").write_text('{"model_type":"rt_detr_v2"}\n', encoding="utf-8")
    (folder / "preprocessor_config.json").write_text(
        '{"image_processor_type":"RTDetrImageProcessor"}\n', encoding="utf-8"
    )
    (folder / "model.safetensors").write_bytes(weight_bytes())
    write_stamp(folder, model["repo"], model["revision"])
    seal(folder, model)


def fill_table(folder, model=TABLE):
    accurate = folder / "model_artifacts/tableformer/accurate"
    accurate.mkdir(parents=True, exist_ok=True)
    (accurate / "tm_config.json").write_text(
        '{"model":{"type":"TableModel04_rs"}}\n', encoding="utf-8"
    )
    (accurate / "tableformer_accurate.safetensors").write_bytes(weight_bytes())
    write_stamp(folder, model["repo"], model["revision"])
    seal(folder, model)


def test_replace_directory_keeps_previous_until_verification(tmp_path):
    final = tmp_path / LAYOUT["path"]
    final.mkdir()
    (final / "config.json").write_text("old", encoding="utf-8")
    write_stamp(final, LAYOUT["repo"], LAYOUT["revision"])
    staging = tmp_path / "incoming"
    fill_layout(staging)
    replace_directory(staging, final)
    previous = tmp_path / f"{LAYOUT['path']}.previous"
    assert (final / "config.json").read_text(encoding="utf-8").startswith("{")
    assert previous.is_dir()
    assert (previous / "config.json").read_text(encoding="utf-8") == "old"
    assert not staging.exists()
    assert commit_previous(final, LAYOUT)
    assert not previous.exists()


def test_revision_conflict_and_missing_model_are_distinct(tmp_path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            [
                {
                    "repo": "docling-project/docling-layout-heron",
                    "revision": "a" * 40,
                    "path": "docling-project--docling-layout-heron",
                }
            ]
        ),
        encoding="utf-8",
    )
    models = tmp_path / "models"
    assert problems(models, manifest) == ["missing:docling-project--docling-layout-heron"]
    folder = models / "docling-project--docling-layout-heron"
    folder.mkdir(parents=True)
    (folder / "config.json").write_text("{}", encoding="utf-8")
    trees = folder / ".cache/huggingface/trees"
    trees.mkdir(parents=True)
    (trees / f"{'b' * 40}.json").write_text("{}", encoding="utf-8")
    write_stamp(folder, "docling-project/docling-layout-heron", "a" * 40)
    assert problems(models, manifest) == ["revision:docling-project--docling-layout-heron"]
    (trees / f"{'a' * 40}.json").write_text("{}", encoding="utf-8")
    assert any(item.startswith("incomplete:") for item in problems(models, manifest))
    fill_layout(folder)
    assert problems(models, manifest) == []
    (folder / "config.json").unlink()
    assert problems(models, manifest) == [
        "incomplete:docling-project--docling-layout-heron:missing:config.json"
    ]
    (folder / "revision.json").write_text("{", encoding="utf-8")
    assert problems(models, manifest) == ["revision:docling-project--docling-layout-heron"]
    (trees / f"{'b' * 40}.json").unlink()
    (folder / "revision.json").unlink()
    assert any(item.startswith("incomplete:") for item in problems(models, manifest))


def test_health_ready_names_a_missing_model_without_calling_it_ready(tmp_path, monkeypatch):
    monkeypatch.setattr(desk_config, "DATA", tmp_path)
    monkeypatch.setattr(desk_config, "MODELS", tmp_path / "models")
    client = TestClient(web.app)
    response = client.get("/health/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "unavailable"
    assert any(item.startswith("missing:") for item in body["reasons"])
    client.close()


def test_stamp_without_tree_cache_is_complete_only_with_weights(tmp_path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            [
                {
                    "repo": "docling-project/docling-layout-heron",
                    "revision": "a" * 40,
                    "path": "docling-project--docling-layout-heron",
                }
            ]
        ),
        encoding="utf-8",
    )
    folder = tmp_path / "models" / "docling-project--docling-layout-heron"
    folder.mkdir(parents=True)
    write_stamp(folder, "docling-project/docling-layout-heron", "a" * 40)
    assert any(item.startswith("incomplete:") for item in problems(tmp_path / "models", manifest))
    (folder / "config.json").write_text("{}", encoding="utf-8")
    assert any(item.startswith("incomplete:") for item in problems(tmp_path / "models", manifest))
    (folder / "config.json").write_text("not-json", encoding="utf-8")
    assert any("config:config.json" in item for item in problems(tmp_path / "models", manifest))
    fill_layout(folder)
    (folder / "model.safetensors").write_bytes(b"")
    assert any(
        "empty:model.safetensors" in item for item in problems(tmp_path / "models", manifest)
    )
    bad = b'{"w":1}'
    (folder / "model.safetensors").write_bytes(len(bad).to_bytes(8, "little") + bad)
    assert any("weight_header:" in item for item in problems(tmp_path / "models", manifest))
    (folder / "model.safetensors").write_bytes(weight_bytes())
    (folder / "model.safetensors.index.json").write_text(
        '{"weight_map":{"layer":"missing.safetensors"}}\n', encoding="utf-8"
    )
    assert any(
        "index:missing.safetensors" in item for item in problems(tmp_path / "models", manifest)
    )
    assert problems(tmp_path / "models", tmp_path / "absent.json") == ["manifest_missing"]


def test_readme_only_tableformer_and_killed_swap_are_not_ready(tmp_path):
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps([LAYOUT, TABLE]), encoding="utf-8")
    models = tmp_path / "models"
    table = models / TABLE["path"]
    accurate = table / "model_artifacts/tableformer/accurate"
    accurate.mkdir(parents=True)
    (accurate / "README.md").write_text("readme", encoding="utf-8")
    write_stamp(table, TABLE["repo"], TABLE["revision"])
    assert any("missing:" in item for item in problems(models, manifest_path))
    old = {**LAYOUT, "revision": "c" * 40}
    final = models / LAYOUT["path"]
    fill_layout(final, old)
    incoming = tmp_path / "staged-new"
    fill_layout(incoming, LAYOUT)
    root = os.path.dirname(os.path.dirname(__file__))
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import os,sys; from pathlib import Path; sys.path.insert(0, sys.argv[1]);"
            "os.environ['DOCLING_MODEL_SWAP_KILL']='after_retire';"
            "from docling_desk.operations.models import replace_directory;"
            "replace_directory(Path(sys.argv[2]), Path(sys.argv[3]))",
            root,
            str(incoming),
            str(final),
        ],
        check=False,
    )
    assert completed.returncode < 0
    assert not final.exists()
    assert (models / f"{LAYOUT['path']}.previous").is_dir()
    assert (models / f"{LAYOUT['path']}.incoming").is_dir()
    assert any(item.startswith("interrupted:") for item in problems(models, manifest_path))
    restored = recover(models, manifest_path)
    assert any(item.startswith("restored:") for item in restored)
    assert (final / "revision.json").is_file()
    assert json.loads((final / "revision.json").read_text())["revision"] == "a" * 40
    assert not (models / f"{LAYOUT['path']}.previous").exists()
    again = recover(models, manifest_path)
    assert not any(item.startswith(("interrupted:", "restored:")) for item in again)
    assert json.loads((final / "revision.json").read_text())["revision"] == "a" * 40
    assert any(item.startswith("incomplete:") for item in problems(models, manifest_path))
    assert not any(item.startswith("interrupted:") for item in problems(models, manifest_path))


def test_health_ready_rejects_missing_weights_with_a_reason(tmp_path, monkeypatch):
    from docling_desk.config import RESOURCES as ROOT

    manifest = json.loads((ROOT / "models/manifest.json").read_text(encoding="utf-8"))
    models = tmp_path / "models"
    for model in manifest:
        folder = models / model["path"]
        folder.mkdir(parents=True)
        write_stamp(folder, model["repo"], model["revision"])
        if model["repo"].endswith("docling-models"):
            accurate = folder / "model_artifacts/tableformer/accurate"
            accurate.mkdir(parents=True)
            (accurate / "README.md").write_text("readme", encoding="utf-8")
        else:
            (folder / "config.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(desk_config, "MODELS", models)
    monkeypatch.setattr(desk_config, "DATA", tmp_path)
    client = TestClient(web.app)
    response = client.get("/health/ready")
    assert response.status_code == 503
    text = " ".join(response.json()["reasons"])
    assert "incomplete:" in text
    assert "README" not in text
    client.close()


def test_recover_uses_only_a_directory_matching_the_manifest(tmp_path):
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps([LAYOUT]), encoding="utf-8")
    models = tmp_path / "models"
    final = models / LAYOUT["path"]
    previous = models / f"{LAYOUT['path']}.previous"
    fill_layout(previous, LAYOUT)
    final.mkdir(parents=True)
    (final / "config.json").write_text("{}", encoding="utf-8")
    write_stamp(final, LAYOUT["repo"], "c" * 40)
    (models / f"{LAYOUT['path']}.swap.json").write_text('{"phase":"published"}\n', encoding="utf-8")
    assert any(item.startswith("interrupted:") for item in problems(models, manifest_path))
    restored = recover(models, manifest_path)
    assert any(item.startswith("restored:") for item in restored)
    assert json.loads((final / "revision.json").read_text(encoding="utf-8"))["revision"] == "a" * 40
    assert not previous.exists()
    recover(models, manifest_path)
    assert json.loads((final / "revision.json").read_text(encoding="utf-8"))["revision"] == "a" * 40

    mismatched = models / f"{LAYOUT['path']}.previous"
    final.rename(mismatched)
    kept = {**LAYOUT, "revision": "c" * 40}
    fill_layout(mismatched, kept)
    final.mkdir()
    write_stamp(final, LAYOUT["repo"], "d" * 40)
    (final / "config.json").write_text("{}", encoding="utf-8")
    (models / f"{LAYOUT['path']}.swap.json").write_text('{"phase":"published"}\n', encoding="utf-8")
    refused = recover(models, manifest_path)
    assert any(item.startswith("interrupted:") for item in refused)
    assert mismatched.is_dir()
    assert (
        json.loads((mismatched / "revision.json").read_text(encoding="utf-8"))["revision"]
        == "c" * 40
    )


def test_manifest_must_be_a_nonempty_pin_list(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="manifest_invalid"):
        from docling_desk.operations.models import read_manifest

        read_manifest(path)


def _header_file(header: bytes, body: bytes = b"") -> bytes:
    return len(header).to_bytes(8, "little") + header + body


def test_unreadable_weights_cannot_be_sealed_or_treated_as_ready(tmp_path):
    folder = tmp_path / "models" / LAYOUT["path"]
    folder.mkdir(parents=True)
    (folder / "config.json").write_text('{"model_type":"rt_detr_v2"}\n', encoding="utf-8")
    (folder / "preprocessor_config.json").write_text(
        '{"image_processor_type":"RTDetrImageProcessor"}\n', encoding="utf-8"
    )
    write_stamp(folder, LAYOUT["repo"], LAYOUT["revision"])
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps([LAYOUT]), encoding="utf-8")
    broken = [
        _header_file(b'{"w":{"dtype":"F32","shape":[100],"data_offsets":[0,400]}}'),
        _header_file(b'{"w":{"dtype":"F32","shape":[1],"data_offsets":[4,0]}}', b"\x00" * 4),
        _header_file(b'{"w":{"dtype":"F32","shape":[1],"data_offsets":[0,4]}}', b"\x00"),
        _header_file(b'{"w":{"dtype":"F32","shape":[100],"data_offsets":[0,4]}}', b"\x00" * 4),
    ]
    for payload in broken:
        (folder / "model.safetensors").write_bytes(payload)
        with pytest.raises(ValueError):
            seal(folder, LAYOUT)
        assert not (folder / "verified.json").exists()
        assert any("weight_layout:" in item for item in problems(tmp_path / "models", manifest))
    (folder / "model.safetensors").write_bytes(weight_bytes())
    seal(folder, LAYOUT)
    assert problems(tmp_path / "models", manifest) == []
    (folder / "verified.json").unlink()
    (folder / "model.safetensors.index.json").write_text(
        '{"weight_map":{"layer":"shard.safetensors"}}\n', encoding="utf-8"
    )
    (folder / "shard.safetensors").write_bytes(
        _header_file(b'{"w":{"dtype":"F32","shape":[100],"data_offsets":[0,400]}}')
    )
    with pytest.raises(ValueError):
        seal(folder, LAYOUT)
    assert any(
        "weight_layout:shard.safetensors" in item
        for item in problems(tmp_path / "models", manifest)
    )
