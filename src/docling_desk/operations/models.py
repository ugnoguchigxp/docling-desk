"""Pin local model directories to models/manifest.json without downloading them."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import sys
from pathlib import Path

LAYOUT_FILES = (
    ("config.json", "json", ("model_type", "architectures")),
    ("preprocessor_config.json", "json", ("image_processor_type",)),
    ("model.safetensors", "weight", ()),
)
TABLE_FILES = (
    ("model_artifacts/tableformer/accurate/tm_config.json", "json", ("model",)),
    ("model_artifacts/tableformer/accurate/tableformer_accurate.safetensors", "weight", ()),
)
RECOVER_HINT = (
    "python -m docling_desk.operations.models recover を実行してください。"
    "manifestのrevisionと一致する完了済みディレクトリだけを戻します。"
    "一致する候補が無いときは、その世代のソースとmanifestへ戻し、"
    ".previous は確認が終わるまで残します。再取得は必須ではありません。"
)


def read_manifest(path: Path) -> list[dict]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("manifest_invalid") from exc
    if not isinstance(value, list) or not value:
        raise ValueError("manifest_invalid")
    for model in value:
        revision = model.get("revision") if isinstance(model, dict) else None
        if (
            not isinstance(model, dict)
            or not isinstance(model.get("repo"), str)
            or not isinstance(model.get("path"), str)
            or not isinstance(revision, str)
            or len(revision) != 40
            or any(character not in "0123456789abcdef" for character in revision)
            or not {"repo", "revision", "path"} <= set(model)
        ):
            raise ValueError("manifest_invalid")
    return value


def _tree_commits(directory: Path) -> list[str]:
    trees = directory / ".cache" / "huggingface" / "trees"
    if not trees.is_dir():
        return []
    return [path.stem for path in trees.glob("*.json") if len(path.stem) == 40]


def observed_revision(directory: Path) -> str | None:
    commits = _tree_commits(directory)
    if len(commits) != 1:
        return None
    return commits[0]


def has_revision(directory: Path, revision: str) -> bool:
    """True when the pinned commit is cached, even if older tree files remain."""
    return (directory / ".cache" / "huggingface" / "trees" / f"{revision}.json").is_file()


def write_stamp(directory: Path, repo: str, revision: str) -> None:
    path = directory / "revision.json"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps({"repo": repo, "revision": revision}, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _revision_matches(directory: Path, model: dict) -> bool:
    stamp_path = directory / "revision.json"
    stamp = _stamp(directory)
    if stamp_path.is_file() and stamp is None:
        return False
    pinned = (model["repo"], model["revision"])
    if stamp is not None and stamp != pinned:
        return False
    commits = _tree_commits(directory)
    pinned_present = has_revision(directory, model["revision"])
    if commits and not pinned_present:
        return False
    return pinned_present or stamp == pinned


def _stamp(directory: Path) -> tuple[str, str] | None:
    path = directory / "revision.json"
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        repo, revision = value.get("repo"), value.get("revision")
    except (OSError, json.JSONDecodeError, AttributeError):
        # A present but unreadable stamp is not the same as a missing one.
        return ("", "")
    if not isinstance(repo, str) or not isinstance(revision, str):
        return ("", "")
    return repo, revision


def _requirements(model: dict) -> tuple[tuple[str, str, tuple[str, ...]], ...]:
    if model["repo"].endswith("/docling-models") or "docling-models" in model["path"]:
        return TABLE_FILES
    return LAYOUT_FILES


def _json_object(path: Path) -> dict | None:
    if not path.is_file() or path.stat().st_size <= 0:
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return None
    return value if isinstance(value, dict) and value else None


def weight_header(path: Path) -> dict:
    """Read the safetensors header only. The tensor body stays on disk."""
    with path.open("rb") as handle:
        raw = handle.read(8)
        if len(raw) < 8:
            raise ValueError("weight_header")
        length = int.from_bytes(raw, "little")
        if length <= 2 or length > 8_000_000:
            raise ValueError("weight_header")
        header = handle.read(length)
    if len(header) != length:
        raise ValueError("weight_header")
    try:
        value = json.loads(header)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("weight_header") from exc
    if not isinstance(value, dict):
        raise ValueError("weight_header")
    tensors = {key: item for key, item in value.items() if key != "__metadata__"}
    if not tensors:
        raise ValueError("weight_header")
    for item in tensors.values():
        offsets = item.get("data_offsets") if isinstance(item, dict) else None
        if (
            not isinstance(offsets, list)
            or len(offsets) != 2
            or not all(
                isinstance(number, int) and not isinstance(number, bool) and number >= 0
                for number in offsets
            )
        ):
            raise ValueError("weight_header")
    return value


DTYPE_BYTES = {
    "BOOL": 1,
    "U8": 1,
    "I8": 1,
    "F8_E5M2": 1,
    "F8_E4M3": 1,
    "I16": 2,
    "U16": 2,
    "F16": 2,
    "BF16": 2,
    "I32": 4,
    "U32": 4,
    "F32": 4,
    "F64": 8,
    "I64": 8,
    "U64": 8,
}


def _data_nbytes(shape: list, dtype: str) -> int:
    count = 1
    for number in shape:
        count *= number
    return count * DTYPE_BYTES[dtype]


def _weight_layout(path: Path, header: dict) -> None:
    """Reject a header whose offsets, dtype, and shape do not cover the file."""
    with path.open("rb") as handle:
        raw = handle.read(8)
    header_length = int.from_bytes(raw, "little")
    data_size = path.stat().st_size - 8 - header_length
    if data_size < 0:
        raise ValueError("weight_layout")
    spans = []
    for key, item in header.items():
        if key == "__metadata__" or not isinstance(item, dict):
            continue
        start, end = item["data_offsets"]
        dtype = item.get("dtype")
        shape = item.get("shape")
        if (
            start > end
            or end > data_size
            or not isinstance(dtype, str)
            or dtype not in DTYPE_BYTES
            or not isinstance(shape, list)
            or not all(
                isinstance(number, int) and not isinstance(number, bool) and number >= 0
                for number in shape
            )
            or end - start != _data_nbytes(shape, dtype)
        ):
            raise ValueError("weight_layout")
        spans.append((start, end))
    if not spans:
        raise ValueError("weight_layout")
    cursor = 0
    for start, end in sorted(spans):
        if start != cursor:
            raise ValueError("weight_layout")
        cursor = end
    if cursor != data_size:
        raise ValueError("weight_layout")


def _weight_problem(path: Path) -> str | None:
    try:
        header = weight_header(path)
        _weight_layout(path, header)
    except ValueError as exc:
        reason = str(exc)
        return reason if reason in {"weight_header", "weight_layout"} else "weight_layout"
    except OSError:
        return "weight_header"
    return None


def _header_sha256(path: Path) -> str:
    header = weight_header(path)
    encoded = json.dumps(header, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _index_targets(directory: Path) -> list[tuple[str, str]]:
    found = []
    for path in directory.rglob("*.index.json"):
        if ".cache" in path.parts:
            continue
        document = _json_object(path)
        weight_map = document.get("weight_map") if document else None
        if not isinstance(weight_map, dict) or not weight_map:
            relative = path.relative_to(directory).as_posix()
            found.append((relative, ""))
            continue
        for target in weight_map.values():
            if isinstance(target, str):
                found.append((path.relative_to(directory).as_posix(), target))
    return found


def folder_issues(directory: Path, model: dict) -> list[str]:
    """Light check: required files, config syntax, index targets, and the sealed header."""
    name = Path(model["path"]).name
    if not directory.is_dir():
        return [f"missing:{name}"]
    if not _revision_matches(directory, model):
        if (
            not (directory / "revision.json").is_file()
            and not _tree_commits(directory)
            and _structural_gaps(directory, model)
        ):
            return [f"incomplete:{name}"]
        return [f"revision:{name}"]
    gaps = _structural_gaps(directory, model)
    if gaps:
        return [f"incomplete:{name}:{gap}" for gap in gaps]
    verified = directory / "verified.json"
    try:
        seal = json.loads(verified.read_text(encoding="utf-8")) if verified.is_file() else None
    except (OSError, json.JSONDecodeError):
        seal = None
    expected = _seal_body(directory, model)
    if not isinstance(seal, dict) or seal.get("revision") != model["revision"]:
        return [f"unverified:{name}"]
    if seal.get("files") != expected["files"]:
        return [f"unverified:{name}"]
    return []


def _structural_gaps(directory: Path, model: dict) -> list[str]:
    gaps = []
    for relative, kind, keys in _requirements(model):
        path = directory / relative
        if not path.is_file():
            gaps.append(f"missing:{relative}")
            continue
        if path.stat().st_size <= 0:
            gaps.append(f"empty:{relative}")
            continue
        if kind == "json":
            document = _json_object(path)
            if document is None:
                gaps.append(f"config:{relative}")
                continue
            if relative.endswith("tm_config.json"):
                model_section = document.get("model")
                if not isinstance(model_section, dict) or not isinstance(
                    model_section.get("type"), str
                ):
                    gaps.append(f"config:{relative}")
            elif not any(key in document for key in keys):
                gaps.append(f"config:{relative}")
        elif kind == "weight":
            problem = _weight_problem(path)
            if problem:
                gaps.append(f"{problem}:{relative}")
    for index, target in _index_targets(directory):
        if not target:
            gaps.append(f"index:{index}")
            continue
        weight = (directory / index).parent / target
        try:
            inside = weight.resolve().is_relative_to(directory.resolve())
        except OSError:
            inside = False
        if not inside or not weight.is_file() or weight.stat().st_size <= 0:
            gaps.append(f"index:{target}")
            continue
        problem = _weight_problem(weight)
        if problem:
            gaps.append(f"{problem}:{target}")
    return gaps


def _seal_body(directory: Path, model: dict) -> dict:
    files = {}
    for relative, kind, _keys in _requirements(model):
        path = directory / relative
        entry = {"size": path.stat().st_size}
        if kind == "weight":
            entry["header_sha256"] = _header_sha256(path)
        files[relative] = entry
    return {"revision": model["revision"], "files": files}


def seal(directory: Path, model: dict) -> None:
    """Record a verified generation after reading weight headers, not tensor bodies."""
    issues = _structural_gaps(directory, model)
    if issues or not _revision_matches(directory, model):
        raise ValueError(issues[0] if issues else "revision")
    path = directory / "verified.json"
    temporary = directory / "verified.json.tmp"
    temporary.write_text(
        json.dumps(_seal_body(directory, model), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _siblings(final: Path) -> tuple[Path, Path, Path]:
    return (
        final.with_name(final.name + ".previous"),
        final.with_name(final.name + ".incoming"),
        final.with_name(final.name + ".swap.json"),
    )


def _journal(path: Path, phase: str) -> None:
    path.write_text(json.dumps({"phase": phase}) + "\n", encoding="utf-8")


def _kill_for_test(phase: str) -> None:
    if os.environ.get("DOCLING_MODEL_SWAP_KILL") == phase:
        os.kill(os.getpid(), signal.SIGKILL)


def replace_directory(staging: Path, final: Path) -> None:
    if not staging.is_dir():
        raise ValueError("staging_missing")
    previous, incoming, journal = _siblings(final)
    if previous.exists() and final.exists():
        raise ValueError("previous_pending")
    if staging.resolve() != incoming.resolve():
        if incoming.exists():
            shutil.rmtree(incoming)
        staging.rename(incoming)
    _journal(journal, "retiring")
    if final.exists():
        final.rename(previous)
    _journal(journal, "retired")
    _kill_for_test("after_retire")
    try:
        incoming.rename(final)
    except Exception:
        if previous.exists() and not final.exists():
            previous.rename(final)
        journal.unlink(missing_ok=True)
        raise
    _journal(journal, "published")
    _kill_for_test("after_publish")


def commit_previous(final: Path, model: dict) -> bool:
    """Remove the saved previous copy only after the published directory verifies."""
    if folder_issues(final, model):
        return False
    previous, _incoming, journal = _siblings(final)
    if previous.exists():
        shutil.rmtree(previous)
    journal.unlink(missing_ok=True)
    return True


def _usable(path: Path, model: dict) -> bool:
    return path.is_dir() and not folder_issues(path, model)


def recover_model(models_dir: Path, model: dict) -> str:
    """Restore one model from a swap interrupted by process death. Does not download."""
    name = Path(model["path"]).name
    final = models_dir / name
    previous, incoming, journal = _siblings(final)
    if _usable(final, model):
        commit_previous(final, model)
        return "ok"
    candidate = None
    if _usable(incoming, model):
        candidate = incoming
    elif _usable(previous, model):
        candidate = previous
    interrupted = previous.exists() or incoming.exists() or journal.exists()
    if candidate is None:
        if final.exists():
            return "manual" if interrupted else "incomplete"
        return "manual" if interrupted else "missing"
    displaced = None
    if final.exists():
        displaced = final.with_name(final.name + ".displaced")
        if displaced.exists():
            return "manual"
        final.rename(displaced)
    try:
        candidate.rename(final)
    except OSError:
        if displaced is not None and displaced.exists() and not final.exists():
            displaced.rename(final)
        raise
    if not _usable(final, model):
        if final.exists() and not candidate.exists():
            final.rename(candidate)
        if displaced is not None and displaced.exists() and not final.exists():
            displaced.rename(final)
        return "manual"
    if displaced is not None and displaced.exists():
        shutil.rmtree(displaced)
    commit_previous(final, model)
    return "restored"


def recover(models_dir: Path, manifest_path: Path) -> list[str]:
    messages = []
    for model in read_manifest(manifest_path):
        name = Path(model["path"]).name
        outcome = recover_model(models_dir, model)
        if outcome == "manual":
            messages.append(f"interrupted:{name}")
            messages.append(f"recover:{name}:{RECOVER_HINT}")
        elif outcome == "missing":
            messages.append(f"missing:{name}")
        elif outcome == "incomplete":
            messages.extend(folder_issues(models_dir / name, model))
        elif outcome == "restored":
            messages.append(f"restored:{name}")
    return messages


def problems(models_dir: Path, manifest_path: Path) -> list[str]:
    if not manifest_path.is_file():
        return ["manifest_missing"]
    try:
        manifest = read_manifest(manifest_path)
    except ValueError:
        return ["manifest_invalid"]
    found = []
    for model in manifest:
        name = Path(model["path"]).name
        folder = models_dir / name
        previous, incoming, journal = _siblings(folder)
        if not folder.is_dir():
            if previous.exists() or incoming.exists() or journal.exists():
                found.append(f"interrupted:{name}")
                found.append(f"recover:{name}:{RECOVER_HINT}")
            else:
                found.append(f"missing:{name}")
            continue
        issues = folder_issues(folder, model)
        found.extend(issues)
        if issues and (previous.exists() or incoming.exists() or journal.exists()):
            found.append(f"interrupted:{name}")
            found.append(f"recover:{name}:{RECOVER_HINT}")
    return found


def _root() -> tuple[Path, Path]:
    from docling_desk.config import MODEL_MANIFEST, MODELS

    return MODELS, MODEL_MANIFEST


def main(argv: list[str] | None = None) -> int:
    models, manifest_path = _root()
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ["recover"]:
        try:
            messages = recover(models, manifest_path)
        except ValueError as exc:
            print(exc)
            return 1
        if any(item.startswith(("interrupted:", "missing:", "recover:")) for item in messages):
            print("\n".join(messages) or "recover_failed")
            return 1
        print("\n".join(messages) if messages else "models recovered")
        return 0
    if args:
        print("usage: python -m docling_desk.operations.models [recover]")
        return 2
    found = problems(models, manifest_path)
    structural = [item for item in found if not item.startswith("unverified:")]
    if not structural:
        try:
            for model in read_manifest(manifest_path):
                folder = models / Path(model["path"]).name
                if folder.is_dir():
                    seal(folder, model)
        except (OSError, ValueError) as exc:
            print(exc)
            return 1
        found = problems(models, manifest_path)
    if not found:
        print("models match manifest")
        return 0
    print("\n".join(found))
    return 3 if all(item.startswith("missing:") for item in found) else 1


if __name__ == "__main__":
    sys.exit(main())
