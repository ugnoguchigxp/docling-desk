"""Storage-level behaviour of standalone Excalidraw diagrams."""

import json
import os
import time
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import HTTPException

from docling_desk.documents import diagram_store as store
from docling_desk.documents.conversion import Job, save_job
from docling_desk.documents.library import ItemRef, Operation, load, operate, snapshot
from docling_desk.storage import (
    document_folder,
    document_folders,
    job_file,
    original_file,
    remove_document,
)

FIXTURE = Path(__file__).parent / "fixtures" / "diagrams" / "basic.excalidraw"


def scene(label="a") -> bytes:
    element = {"id": label, "type": "rectangle", "x": 0, "y": 0, "width": 1, "height": 1}
    value = {"type": "excalidraw", "version": 2, "elements": [element], "appState": {}}
    return json.dumps(value).encode()


def new_id() -> str:
    return uuid4().hex


def key() -> str:
    return uuid4().hex


def create(tmp_path, raw=None, name="図", folder=None, identifier=None, idem=None):
    identifier = identifier or new_id()
    status, info = store.create(tmp_path, identifier, name, folder, raw or scene(), idem or key())
    assert status == 201
    return identifier, info


def manifest(tmp_path, identifier):
    return json.loads((store.content_dir(tmp_path, identifier) / "manifest.json").read_text())


def error(call, status):
    with pytest.raises(HTTPException) as caught:
        call()
    assert caught.value.status_code == status, caught.value.detail
    return caught.value


def test_create_preserves_bytes_and_publishes_every_record(tmp_path):
    raw = FIXTURE.read_bytes()
    identifier, info = create(tmp_path, raw, name="設計図")
    assert info["name"] == "設計図.excalidraw"
    assert info["revision_number"] == 1 and info["draft"] is None
    assert info["etag"] == f'"r-{info["revision_id"]}"'
    assert info["sha256"] == store.sha256(raw) and info["size"] == len(raw)
    value = manifest(tmp_path, identifier)
    assert value["version"] == 2 and value["kind"] == "excalidraw"
    assert (
        value["key"]
        == f"documents/{identifier}/revisions/{info['revision_id']}/original.excalidraw"
    )
    assert (tmp_path / "content" / value["key"]).read_bytes() == raw
    folder = document_folder(tmp_path, identifier)
    assert folder.is_dir() and folder in document_folders(tmp_path)
    job = Job.model_validate_json(job_file(folder).read_text())
    assert job.excalidraw and job.state == "success" and job.preview == "excalidraw"
    assert original_file(folder, ".excalidraw").read_bytes() == raw
    assert load(tmp_path).files[identifier].name == "設計図.excalidraw"
    assert any(j["id"] == identifier for j in snapshot(tmp_path)["jobs"])
    revision = json.loads(
        (
            store.content_dir(tmp_path, identifier)
            / "revisions"
            / info["revision_id"]
            / "revision.json"
        ).read_text()
    )
    assert revision["number"] == 1 and revision["parent"] is None
    assert revision["restored_from"] is None


def test_name_keeps_extension_and_placement_in_folder(tmp_path):
    from docling_desk.documents.library import FolderCreate, create_folder

    folder = create_folder(tmp_path, FolderCreate(name="図面")).id
    identifier, info = create(tmp_path, name="a.excalidraw", folder=folder)
    assert info["name"] == "a.excalidraw" and info["folder_id"] == folder
    _, second = create(tmp_path, name="a.excalidraw", folder=folder)
    assert second["name"] == "a (2).excalidraw"
    assert load(tmp_path).files[identifier].parent_id == folder
    error(lambda: store.create(tmp_path, new_id(), "x", "missing", scene(), key()), 404)
    assert len(document_folders(tmp_path)) == 2


def test_create_idempotent_replay_conflict_and_existing_id(tmp_path):
    identifier, first = create(tmp_path, idem="k1")
    status, replay = store.create(tmp_path, identifier, "図", None, scene(), "k1")
    assert status == 200 and replay == first
    error(lambda: store.create(tmp_path, identifier, "図", None, scene("b"), "k1"), 409)
    error(lambda: store.create(tmp_path, identifier, "図", None, scene(), "k2"), 412)
    assert len(document_folders(tmp_path)) == 1
    assert manifest(tmp_path, identifier)["revision_count"] == 1


def test_create_over_other_documents_is_precondition_failed(tmp_path):
    identifier = new_id()
    folder = document_folder(tmp_path, identifier)
    folder.mkdir(parents=True)
    save_job(folder, Job(id=identifier, filename="a.pdf", state="success"))
    error(lambda: store.create(tmp_path, identifier, "図", None, scene(), key()), 412)
    legacy = new_id()
    (tmp_path / legacy).mkdir()
    error(lambda: store.create(tmp_path, legacy, "図", None, scene(), key()), 412)


def test_failed_create_leaves_nothing(tmp_path, monkeypatch):
    import docling_desk.documents.diagram_store as module

    def broken(*args):
        raise OSError("disk")

    monkeypatch.setattr(module, "_place", broken)
    identifier = new_id()
    with pytest.raises(OSError):
        store.create(tmp_path, identifier, "図", None, scene(), key())
    assert not store.content_dir(tmp_path, identifier).exists()
    assert document_folders(tmp_path) == []


def test_invalid_scene_creates_nothing(tmp_path):
    error(lambda: store.create(tmp_path, new_id(), "図", None, b"{}", key()), 422)
    error(lambda: store.create(tmp_path, new_id(), "a/b", None, scene(), key()), 422)
    assert document_folders(tmp_path) == []


def test_save_requires_current_etag_and_replays_after_success(tmp_path):
    identifier, first = create(tmp_path)
    status, second = store.save(tmp_path, identifier, scene("b"), first["etag"], "s1")
    assert status == 201 and second["revision_number"] == 2
    assert second["operation_id"] and second["etag"] != first["etag"]
    assert original_file(
        document_folder(tmp_path, identifier), ".excalidraw"
    ).read_bytes() == scene("b")
    # A retry carries the old etag; the stored result wins over the ETag check.
    assert store.save(tmp_path, identifier, scene("b"), first["etag"], "s1") == (201, second)
    error(lambda: store.save(tmp_path, identifier, scene("c"), first["etag"], "s1"), 409)
    error(lambda: store.save(tmp_path, identifier, scene("c"), first["etag"], "s2"), 412)
    assert store.info(tmp_path, identifier).revision_number == 2
    # The first revision stays readable and unchanged.
    raw, _, _ = store.scene_content(tmp_path, identifier, first["revision_id"])
    assert raw == scene()


def test_unknown_or_foreign_ids_are_not_found(tmp_path):
    foreign = new_id()
    folder = document_folder(tmp_path, foreign)
    folder.mkdir(parents=True)
    save_job(folder, Job(id=foreign, filename="a.pdf", state="success"))
    for target in (foreign, new_id(), "x", "../x"):
        error(lambda target=target: store.info(tmp_path, target), 404)
        error(lambda target=target: store.save(tmp_path, target, scene(), '"r-x"', "k"), 404)
        error(lambda target=target: store.revisions(tmp_path, target, None, 10), 404)


def test_revision_list_pages_newest_first(tmp_path):
    identifier, info = create(tmp_path)
    for index in range(4):
        _, info = store.save(tmp_path, identifier, scene(str(index)), info["etag"], key())
    items, following = store.revisions(tmp_path, identifier, None, 2)
    assert [i.number for i in items] == [5, 4] and following == 4
    assert items[0].current and not items[1].current
    items, following = store.revisions(tmp_path, identifier, following, 2)
    assert [i.number for i in items] == [3, 2] and following == 2
    items, following = store.revisions(tmp_path, identifier, following, 2)
    assert [i.number for i in items] == [1] and following is None
    assert items[0].parent is None
    everything, following = store.revisions(tmp_path, identifier, None, 100)
    assert [i.number for i in everything] == [5, 4, 3, 2, 1] and following is None
    assert everything[0].parent == everything[1].id


def test_restore_creates_a_new_revision_and_keeps_history(tmp_path):
    identifier, first = create(tmp_path, scene("one"))
    _, second = store.save(tmp_path, identifier, scene("two"), first["etag"], key())
    status, restored = store.restore(
        tmp_path, identifier, first["revision_id"], second["etag"], "r1"
    )
    assert status == 201 and restored["revision_number"] == 3
    assert restored["sha256"] == first["sha256"]
    items, _ = store.revisions(tmp_path, identifier, None, 10)
    assert items[0].restored_from == first["revision_id"] and items[0].current
    assert [i.restored_from for i in items[1:]] == [None, None]
    assert store.restore(tmp_path, identifier, first["revision_id"], second["etag"], "r1") == (
        201,
        restored,
    )
    error(
        lambda: store.restore(tmp_path, identifier, first["revision_id"], second["etag"], "r2"), 412
    )
    error(lambda: store.restore(tmp_path, identifier, new_id(), restored["etag"], "r3"), 404)
    error(lambda: store.restore(tmp_path, identifier, "../x", restored["etag"], "r4"), 404)
    raw, _, _ = store.scene_content(tmp_path, identifier, None)
    assert raw == scene("one")


def test_draft_lifecycle_prunes_versions_and_commits(tmp_path):
    identifier, info = create(tmp_path, scene("base"))
    error(lambda: store.draft_create(tmp_path, identifier, None, '"r-x"'), 412)
    draft = store.draft_create(tmp_path, identifier, None, info["etag"])
    assert draft.version == 1 and draft.base_revision == info["revision_id"]
    assert draft.etag == f'"d-{draft.id}-1"'
    raw, current = store.draft_content(tmp_path, identifier, draft.id)
    assert raw == scene("base") and current == draft
    conflict = error(lambda: store.draft_create(tmp_path, identifier, None, info["etag"]), 409)
    assert draft.id in conflict.detail
    assert store.info(tmp_path, identifier).draft == draft
    for number in range(2, 7):
        draft = store.draft_update(tmp_path, identifier, draft.id, scene(f"v{number}"), draft.etag)
    assert draft.version == 6
    versions = store.content_dir(tmp_path, identifier) / "drafts" / draft.id / "versions"
    assert sorted(int(p.name) for p in versions.iterdir()) == [4, 5, 6]
    assert manifest(tmp_path, identifier)["draft"]["versions"] == [4, 5, 6]
    error(lambda: store.draft_update(tmp_path, identifier, draft.id, scene(), '"d-x-1"'), 412)
    error(lambda: store.draft_update(tmp_path, identifier, new_id(), scene(), draft.etag), 404)
    stale = f'"d-{draft.id}-5"'
    error(
        lambda: store.draft_commit(tmp_path, identifier, draft.id, info["etag"], stale, "c0"), 412
    )
    status, committed = store.draft_commit(
        tmp_path, identifier, draft.id, info["etag"], draft.etag, "c1"
    )
    assert status == 201 and committed["revision_number"] == 2 and committed["draft"] is None
    raw, _, _ = store.scene_content(tmp_path, identifier, None)
    assert raw == scene("v6")
    assert not (store.content_dir(tmp_path, identifier) / "drafts" / draft.id).exists()
    assert store.draft_commit(tmp_path, identifier, draft.id, info["etag"], draft.etag, "c1") == (
        201,
        committed,
    )
    error(lambda: store.draft_info(tmp_path, identifier, draft.id), 404)


def test_commit_with_stale_base_is_rejected_and_keeps_the_draft(tmp_path):
    identifier, info = create(tmp_path)
    draft = store.draft_create(tmp_path, identifier, scene("draft"), info["etag"])
    _, newer = store.save(tmp_path, identifier, scene("newer"), info["etag"], key())
    error(
        lambda: store.draft_commit(
            tmp_path, identifier, draft.id, newer["etag"], draft.etag, key()
        ),
        412,
    )
    error(
        lambda: store.draft_commit(tmp_path, identifier, draft.id, info["etag"], draft.etag, key()),
        412,
    )
    assert store.draft_info(tmp_path, identifier, draft.id) == draft
    assert store.info(tmp_path, identifier).revision_number == 2
    assert store.info(tmp_path, identifier).draft == draft


def test_discard_removes_draft_and_allows_a_new_one(tmp_path):
    identifier, info = create(tmp_path)
    draft = store.draft_create(tmp_path, identifier, None, info["etag"])
    error(lambda: store.draft_discard(tmp_path, identifier, draft.id, '"d-x-9"'), 412)
    store.draft_discard(tmp_path, identifier, draft.id, draft.etag)
    assert not (store.content_dir(tmp_path, identifier) / "drafts" / draft.id).exists()
    assert store.info(tmp_path, identifier).draft is None
    error(lambda: store.draft_discard(tmp_path, identifier, draft.id, draft.etag), 404)
    assert store.draft_create(tmp_path, identifier, None, info["etag"]).id != draft.id


def test_operation_lookup_and_seven_day_expiry(tmp_path):
    identifier, info = create(tmp_path, idem="op")
    assert store.operation_result(tmp_path, identifier, info["operation_id"]) == info
    error(lambda: store.operation_result(tmp_path, identifier, new_id()), 404)
    content = store.content_dir(tmp_path, identifier)
    value = manifest(tmp_path, identifier)
    old = time.time() - 8 * 24 * 3600
    for entry in value["operations"].values():
        entry["created"] = old
    (content / "manifest.json").write_text(json.dumps(value))
    error(lambda: store.operation_result(tmp_path, identifier, info["operation_id"]), 404)
    # An expired key is no longer a replay: the request runs as a new operation.
    status, saved = store.save(tmp_path, identifier, scene("b"), info["etag"], "op")
    assert status == 201 and saved["revision_number"] == 2
    assert not (content / "operations" / f"{info['operation_id']}.json").exists()
    assert (
        list(manifest(tmp_path, identifier)["operations"].values())[0]["id"]
        == saved["operation_id"]
    )


def test_operations_are_capped_and_old_files_deleted(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "OPERATION_LIMIT", 3)
    identifier, info = create(tmp_path)
    ids = [info["operation_id"]]
    for _ in range(5):
        _, info = store.save(tmp_path, identifier, scene(key()), info["etag"], key())
        ids.append(info["operation_id"])
    value = manifest(tmp_path, identifier)
    assert sorted(e["id"] for e in value["operations"].values()) == sorted(ids[-3:])
    files = {
        p.stem for p in (store.content_dir(tmp_path, identifier) / "operations").glob("*.json")
    }
    assert files == set(ids[-3:])
    error(lambda: store.operation_result(tmp_path, identifier, ids[0]), 404)


def test_delete_through_library_removes_every_directory(tmp_path):
    identifier, info = create(tmp_path)
    store.draft_create(tmp_path, identifier, None, info["etag"])
    (tmp_path / "cache" / "documents" / identifier / "thumbnails").mkdir(parents=True)
    result = operate(
        tmp_path, Operation(action="delete", items=[ItemRef(kind="file", id=identifier)])
    )
    assert result["items"] == [{"kind": "file", "id": identifier}]
    for root in ("content", "derived", "runtime", "cache"):
        assert not (tmp_path / root / "documents" / identifier).exists()
    assert identifier not in load(tmp_path).files
    assert document_folders(tmp_path) == []


def test_copy_is_refused_directly_and_under_a_folder(tmp_path):
    from docling_desk.documents.library import FolderCreate, create_folder

    parent = create_folder(tmp_path, FolderCreate(name="親")).id
    target = create_folder(tmp_path, FolderCreate(name="先")).id
    identifier, _ = create(tmp_path, folder=parent)
    for item in (ItemRef(kind="file", id=identifier), ItemRef(kind="folder", id=parent)):
        error(
            lambda item=item: operate(
                tmp_path, Operation(action="copy", items=[item], destination=target)
            ),
            409,
        )
    caught = error(
        lambda: operate(
            tmp_path,
            Operation(action="copy", items=[ItemRef(kind="file", id=identifier)]),
        ),
        409,
    )
    assert caught.detail == "図表の複製には対応していません。"
    assert len(document_folders(tmp_path)) == 1
    moved = operate(
        tmp_path,
        Operation(action="move", items=[ItemRef(kind="file", id=identifier)], destination=target),
    )
    assert moved["items"]
    assert store.info(tmp_path, identifier).folder_id == target


def test_rename_in_library_is_reflected_without_touching_revisions(tmp_path):
    identifier, info = create(tmp_path)
    operate(
        tmp_path,
        Operation(
            action="rename", items=[ItemRef(kind="file", id=identifier)], name="新名称.excalidraw"
        ),
    )
    renamed = store.info(tmp_path, identifier)
    assert renamed.name == "新名称.excalidraw" and renamed.etag == info["etag"]


def test_repair_rebuilds_missing_job_and_derived_folder(tmp_path):
    identifier, info = create(tmp_path, name="復旧")
    folder = document_folder(tmp_path, identifier)
    remove_document_parts(tmp_path, identifier)
    assert document_folders(tmp_path) == []
    result = store.repair_diagrams(tmp_path)
    assert result["repaired"] == [identifier]
    assert folder.is_dir() and job_file(folder).is_file()
    assert folder in document_folders(tmp_path)
    job = Job.model_validate_json(job_file(folder).read_text())
    assert job.excalidraw and job.state == "success"
    assert store.info(tmp_path, identifier).revision_id == info["revision_id"]
    assert load(tmp_path).files[identifier].name == "復旧.excalidraw"
    assert store.repair_diagrams(tmp_path)["repaired"] == []


def remove_document_parts(data, identifier):
    import shutil

    shutil.rmtree(data / "derived" / "documents" / identifier)
    shutil.rmtree(data / "runtime" / "documents" / identifier)
    path = data / "content" / "manifests" / "library.json"
    library = json.loads(path.read_text())
    library["files"].pop(identifier)
    path.write_text(json.dumps(library))


def test_repair_rebuilds_only_the_missing_derived_folder(tmp_path):
    import shutil

    identifier, _ = create(tmp_path)
    shutil.rmtree(tmp_path / "derived" / "documents" / identifier)
    assert document_folders(tmp_path) == []
    assert store.repair_diagrams(tmp_path)["repaired"] == [identifier]
    assert len(document_folders(tmp_path)) == 1


def age(path, seconds=2 * 3600):
    old = time.time() - seconds
    os.utime(path, (old, old))


def test_repair_deletes_only_old_unreferenced_files(tmp_path):
    identifier, info = create(tmp_path)
    _, second = store.save(tmp_path, identifier, scene("b"), info["etag"], key())
    draft = store.draft_create(tmp_path, identifier, None, second["etag"])
    draft = store.draft_update(tmp_path, identifier, draft.id, scene("c"), draft.etag)
    content = store.content_dir(tmp_path, identifier)
    stale = [
        content / "revisions" / new_id(),
        content / "drafts" / new_id(),
        content / "drafts" / draft.id / "versions" / "9",
        content / "operations" / f"{new_id()}.json",
        content / "revisions" / new_id() / "original.excalidraw.tmp",
    ]
    fresh = [content / "revisions" / new_id(), content / "operations" / f"{new_id()}.json"]
    for path in stale + fresh:
        if path.name.endswith(".json"):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{}")
        elif path.suffix == ".tmp":
            path.parent.mkdir(parents=True)
            path.write_text("x")
        else:
            path.mkdir(parents=True)
    for path in stale:
        age(path)
    age(content / "revisions" / stale[4].parent.name)
    result = store.repair_diagrams(tmp_path)
    assert result["removed"] >= 5
    for path in stale:
        assert not path.exists(), path
    for path in fresh:
        assert path.exists(), path
    # Everything the manifest references survives.
    assert store.info(tmp_path, identifier).revision_number == 2
    assert store.draft_content(tmp_path, identifier, draft.id)[0] == scene("c")
    for revision in (info, second):
        assert store.scene_content(tmp_path, identifier, revision["revision_id"])[0]
    assert store.operation_result(tmp_path, identifier, second["operation_id"]) == second


def test_repair_removes_abandoned_creation_but_not_unlisted_originals(tmp_path):
    orphan = tmp_path / "content" / "documents" / new_id()
    (orphan / "revisions" / new_id()).mkdir(parents=True)
    age(orphan)
    other = new_id()
    legacy = tmp_path / "content" / "documents" / other
    legacy.mkdir(parents=True)
    (legacy / "original.pdf").write_bytes(b"%PDF-1.4")
    age(legacy)
    store.repair_diagrams(tmp_path)
    assert not orphan.exists()
    assert (legacy / "original.pdf").exists()


def test_other_documents_keep_their_original_resolution(tmp_path):
    identifier = new_id()
    folder = document_folder(tmp_path, identifier)
    folder.mkdir(parents=True)
    save_job(folder, Job(id=identifier, filename="a.pdf", state="success"))
    assert (
        original_file(folder, ".pdf")
        == tmp_path / "content" / "documents" / identifier / "original.pdf"
    )
    # Even an .excalidraw suffix falls back to the fixed name without a v2 manifest.
    assert original_file(folder, ".excalidraw").name == "original.excalidraw"
    create_id, _ = create(tmp_path)
    diagram = document_folder(tmp_path, create_id)
    assert original_file(diagram, ".pdf").name == "original.pdf"
    assert "revisions" in original_file(diagram, ".excalidraw").parts


def test_manifest_key_outside_the_document_is_ignored(tmp_path):
    identifier, _ = create(tmp_path)
    path = store.content_dir(tmp_path, identifier) / "manifest.json"
    value = json.loads(path.read_text())
    value["key"] = "documents/other/revisions/x/original.excalidraw"
    path.write_text(json.dumps(value))
    folder = document_folder(tmp_path, identifier)
    assert original_file(folder, ".excalidraw").name == "original.excalidraw"
    assert "revisions" not in original_file(folder, ".excalidraw").parts


def test_remove_document_helper_still_handles_diagrams(tmp_path):
    identifier, _ = create(tmp_path)
    remove_document(document_folder(tmp_path, identifier))
    assert not store.content_dir(tmp_path, identifier).exists()


def test_files_are_fsynced_before_publish(tmp_path, monkeypatch):
    synced = []
    real = os.fsync
    monkeypatch.setattr(os, "fsync", lambda fd: (synced.append(fd), real(fd))[1])
    create(tmp_path)
    # original, revision.json, operation and manifest: one file sync and one directory sync each
    assert len(synced) >= 8


def test_directory_fsync_failure_is_tolerated(tmp_path, monkeypatch):
    real = os.open

    def refuse_directories(path, flags, *args, **kwargs):
        if Path(path).is_dir():
            raise OSError("no directory handles")
        return real(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", refuse_directories)
    identifier, _ = create(tmp_path)
    assert store.info(tmp_path, identifier).revision_number == 1


def test_repair_skips_corrupt_manifests_and_continues(tmp_path, caplog):
    broken, _ = create(tmp_path)
    healthy, _ = create(tmp_path)
    shutil_rmtree = __import__("shutil").rmtree
    for identifier in (broken, healthy):
        shutil_rmtree(tmp_path / "derived" / "documents" / identifier)
    path = store.content_dir(tmp_path, broken) / "manifest.json"
    value = json.loads(path.read_text())
    del value["created"]
    value["operations"] = {"x": 1}
    path.write_text(json.dumps(value))
    other = store.content_dir(tmp_path, new_id())
    other.mkdir(parents=True)
    (other / "manifest.json").write_text("{not json")
    with caplog.at_level("WARNING"):
        result = store.repair_diagrams(tmp_path)
    assert healthy in result["repaired"]
    assert broken in caplog.text


def test_repair_restores_the_folder_when_it_still_exists(tmp_path):
    from docling_desk.documents.library import FolderCreate, create_folder

    folder = create_folder(tmp_path, FolderCreate(name="図面")).id
    identifier, _ = create(tmp_path, folder=folder)
    assert manifest(tmp_path, identifier)["folder_id"] == folder
    remove_document_parts(tmp_path, identifier)
    store.repair_diagrams(tmp_path)
    assert store.info(tmp_path, identifier).folder_id == folder
    # A folder deleted in the meantime falls back to the root.
    remove_document_parts(tmp_path, identifier)
    path = tmp_path / "content" / "manifests" / "library.json"
    library = json.loads(path.read_text())
    library["folders"] = {}
    path.write_text(json.dumps(library))
    store.repair_diagrams(tmp_path)
    assert store.info(tmp_path, identifier).folder_id is None


def test_revision_page_reads_only_what_it_returns(tmp_path, monkeypatch):
    identifier, info = create(tmp_path)
    for index in range(9):
        _, info = store.save(tmp_path, identifier, scene(str(index)), info["etag"], key())
    reads = []
    real = store._revision
    monkeypatch.setattr(store, "_revision", lambda *args: (reads.append(args[1]), real(*args))[1])
    items, following = store.revisions(tmp_path, identifier, None, 2)
    assert [i.number for i in items] == [10, 9] and following == 9
    assert len(reads) == 3
    reads.clear()
    items, following = store.revisions(tmp_path, identifier, 3, 5)
    assert [i.number for i in items] == [2, 1] and following is None


def test_repair_replaces_a_lost_placement_when_job_json_exists(tmp_path):
    from docling_desk.documents.library import FolderCreate, create_folder

    folder = create_folder(tmp_path, FolderCreate(name="図面")).id
    identifier, _ = create(tmp_path, folder=folder)
    path = tmp_path / "content" / "manifests" / "library.json"
    library = json.loads(path.read_text())
    library["files"].pop(identifier)
    path.write_text(json.dumps(library))
    assert job_file(document_folder(tmp_path, identifier)).is_file()
    assert store.repair_diagrams(tmp_path)["repaired"] == [identifier]
    assert store.info(tmp_path, identifier).folder_id == folder
    assert store.repair_diagrams(tmp_path)["repaired"] == []


def test_cyclic_revision_chain_does_not_hang_repair_or_listing(tmp_path, caplog):
    identifier, info = create(tmp_path)
    _, second = store.save(tmp_path, identifier, scene("b"), info["etag"], key())
    content = store.content_dir(tmp_path, identifier)
    path = content / "revisions" / info["revision_id"] / "revision.json"
    value = json.loads(path.read_text())
    value["parent"] = second["revision_id"]
    path.write_text(json.dumps(value))
    orphan = content / "revisions" / new_id()
    orphan.mkdir()
    age(orphan)
    with caplog.at_level("WARNING"):
        store.repair_diagrams(tmp_path)
    assert "循環" in caplog.text
    assert orphan.exists()  # nothing is deleted when the chain cannot be trusted
    items, following = store.revisions(tmp_path, identifier, None, 10)
    assert [i.number for i in items] == [2, 1] and following is None
