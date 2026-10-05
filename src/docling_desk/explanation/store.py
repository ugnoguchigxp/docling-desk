"""Published versions survive retries, restart and library copying."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from docling_desk.explanation.provider import SCHEMAS, ExplanationError
from docling_desk.storage import document_folders, job_file

LOCK = threading.RLock()
ACTIVE = {"queued", "running"}
UNCERTAIN_COMMITS: set[tuple[str, str]] = set()


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def unit_path(folder: Path, unit_id: str) -> Path:
    if not re.fullmatch(r"(?:page|slide|sheet|document)-[1-9]\d*", unit_id):
        raise ExplanationError("input", "解説対象の単位が不正です。")
    return confined(folder, f"states/{unit_id}.json")


def confined(folder: Path, relative: str) -> Path:
    root = folder.resolve() / "explanations"
    path = root / relative
    if not path.resolve().is_relative_to(root) or root.is_symlink():
        raise ExplanationError("storage", "解説の保存先が不正です。")
    return path


def atomic_json(path: Path, value: dict) -> None:
    raw = json.dumps(value, ensure_ascii=False, indent=2).encode()
    if len(raw) > 20 * 1024**2:
        raise ExplanationError("storage_limit", "解説の保存容量上限を超えています。")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            os.chmod(temporary, 0o600)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
        fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        temporary.unlink(missing_ok=True)


def empty(unit_id: str) -> dict:
    return {
        "schema_version": 1,
        "unit_id": unit_id,
        "state": "uncreated",
        "stage": "",
        "attempt_id": None,
        "latest_version_id": None,
        "published_version_ids": [],
        "version_hashes": {},
        "error": None,
        "updated_at": None,
    }


def validate_state(value: dict, unit_id: str) -> dict:
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise ExplanationError("schema", "保存済み解説の形式に対応していません。")
    ids = value.get("published_version_ids", [])
    if (
        value.get("unit_id") != unit_id
        or value.get("state")
        not in ACTIVE | {"uncreated", "completed", "failed", "interrupted", "not_applicable"}
        or not isinstance(ids, list)
        or any(not isinstance(v, str) or not re.fullmatch(r"[a-f0-9]{32}", v) for v in ids)
        or len(ids) != len(set(ids))
        or (value.get("latest_version_id") and value["latest_version_id"] not in ids)
        or not isinstance(value.get("version_hashes"), dict)
        or type(value.get("llm_calls", 0)) is not int
        or not 0 <= value.get("llm_calls", 0) <= 2
    ):
        raise ExplanationError("storage", "保存済み解説の状態が不正です。")
    return value


def read_state(folder: Path, unit_id: str) -> dict:
    with LOCK:
        if (str(folder.resolve()), unit_id) in UNCERTAIN_COMMITS:
            raise ExplanationError(
                "storage",
                "解説の保存を確定できません。サーバー再起動後に保存状態を確認してください。",
            )
        path = unit_path(folder, unit_id)
        if not path.exists() and not path.with_suffix(".backup").exists():
            if list(confined(folder, f"versions/{unit_id}").glob("*.json")):
                raise ExplanationError("storage", "解説の状態ファイルがありません。")
            return empty(unit_id)
        try:
            value = validate_state(json.loads(path.read_text()), unit_id)
            if value.get("recovery_uncertain"):
                value["recovery_warning"] = (
                    "復旧時に公開状態を確認できない版がありました。確認済みの保存版を表示します。"
                )
            return value
        except ExplanationError as exc:
            if exc.code == "schema":
                raise
        except (OSError, ValueError, TypeError):
            pass
        try:
            value = validate_state(json.loads(path.with_suffix(".backup").read_text()), unit_id)
            return {**value, "recovery_warning": "状態を直前の保存から復旧表示しています。"}
        except (OSError, ValueError, TypeError, ExplanationError) as exc:
            raise ExplanationError("storage", "保存済み解説を読み込めません。") from exc


def write_state(folder: Path, value: dict) -> None:
    path = unit_path(folder, value["unit_id"])
    if path.exists():
        try:
            previous = validate_state(json.loads(path.read_text()), value["unit_id"])
            atomic_json(path.with_suffix(".backup"), previous)
        except (ValueError, ExplanationError):
            # A corrupt current state must not replace a known-good backup.
            pass
    atomic_json(path, {k: v for k, v in value.items() if k != "recovery_warning"})


def version_path(folder: Path, unit_id: str, version_id: str) -> Path:
    unit_path(folder, unit_id)
    if not re.fullmatch(r"[a-f0-9]{32}", version_id):
        raise ExplanationError("storage", "解説の版IDが不正です。")
    return confined(folder, f"versions/{unit_id}/{version_id}.json")


def read_checkpoint(folder: Path, unit_id: str, source_hash: str, config_hash: str) -> dict:
    unit_path(folder, unit_id)
    try:
        value = json.loads(confined(folder, f"checkpoints/{unit_id}.json").read_text())
        if (
            isinstance(value, dict)
            and value.get("schema_version") == 1
            and value.get("unit_id") == unit_id
            and value.get("source_hash") == source_hash
            and value.get("config_hash") == config_hash
            and isinstance(value.get("outlines", []), list)
            and len(value.get("outlines", [])) <= 8
            and all(isinstance(o, dict) for o in value.get("outlines", []))
            and isinstance(value.get("completed_parts", []), list)
            and len(value.get("completed_parts", [])) <= len(value.get("outlines", []))
            and all(isinstance(p, dict) for p in value.get("completed_parts", []))
            and all(
                isinstance(value.get(k, {}), dict) for k in ("local", "external", "draft", "final")
            )
            and type(value.get("calls", 0)) is int
            and 0 <= value.get("calls", 0) <= 2
            and isinstance(value.get("input_counts", []), list)
            and all(type(n) is int and 0 <= n <= 10000 for n in value.get("input_counts", []))
            and isinstance(value.get("usage_counts", []), list)
            and all(isinstance(n, dict) for n in value.get("usage_counts", []))
            and all(isinstance(value.get(k, []), list) for k in ("first_context", "final_context"))
            and isinstance(value.get("spent_seconds", 0), (int, float))
            and 0 <= value.get("spent_seconds", 0) <= 3600
        ):
            calls = value.get("calls", 0)
            if (
                len(value.get("input_counts", [])) > calls
                or len(value.get("usage_counts", [])) > calls
                or ("draft" in value and calls < 1)
                or ("final" in value and (calls != 2 or "draft" not in value))
            ):
                return {}
            for key, task in (("draft", "draft"), ("final", "finalize")):
                if key in value:
                    SCHEMAS[task].model_validate(value[key])
            return value
    except (OSError, ValueError):
        pass
    return {}


def write_checkpoint(folder: Path, unit_id: str, value: dict) -> None:
    unit_path(folder, unit_id)
    atomic_json(
        confined(folder, f"checkpoints/{unit_id}.json"),
        {
            **value,
            "schema_version": 1,
            "unit_id": unit_id,
        },
    )


def latest(folder: Path, state: dict) -> tuple[dict | None, str | None]:
    with LOCK:
        warning = state.get("recovery_warning")
        for version_id in reversed(state["published_version_ids"]):
            path = version_path(folder, state["unit_id"], version_id)
            try:
                raw = path.read_bytes()
                value = json.loads(raw)
                if not isinstance(value, dict):
                    raise ValueError()
                if (
                    hashlib.sha256(raw).hexdigest() != state["version_hashes"].get(version_id)
                    or value.get("unit_id") != state["unit_id"]
                ):
                    raise ValueError()
                if any(
                    value.get(k, 1) != 1
                    for k in (
                        "schema_version",
                        "source_format_version",
                        "copy_mapping_version",
                        "hash_version",
                    )
                ):
                    raise ExplanationError("schema", "保存済み解説の版に対応していません。")
                if version_id != state["latest_version_id"]:
                    warning = "最新の解説を読み込めないため、直前の完成版を表示しています。"
                return value, warning
            except (OSError, ValueError, TypeError):
                continue
        if state["published_version_ids"]:
            raise ExplanationError("storage", "保存済みの完成版を読み込めません。")
        return None, warning


def publish(folder: Path, unit_id: str, attempt: str, value: dict) -> bool:
    with LOCK:
        state = read_state(folder, unit_id)
        if state.get("attempt_id") != attempt or state["state"] != "running":
            return False
        version_id = uuid4().hex
        value.update(
            schema_version=1,
            source_format_version=1,
            copy_mapping_version=1,
            version_id=version_id,
            unit_id=unit_id,
            document_id=folder.name,
            created_at=now(),
        )
        path = version_path(folder, unit_id, version_id)
        atomic_json(path, value)
        updated = {
            **state,
            "state": "completed",
            "stage": "保存完了",
            "error": None,
            "error_code": None,
            "resume_requested": False,
            "latest_version_id": version_id,
            "published_version_ids": [*state["published_version_ids"], version_id],
            "version_hashes": {
                **state["version_hashes"],
                version_id: hashlib.sha256(path.read_bytes()).hexdigest(),
            },
            "updated_at": now(),
        }
        try:
            write_state(folder, updated)
        except Exception:
            # Never expose a commit whose durability could not be confirmed.
            try:
                atomic_json(unit_path(folder, unit_id), state)
            except Exception:
                UNCERTAIN_COMMITS.add((str(folder.resolve()), unit_id))
                raise
            raise
        return True


def saved_units(folder: Path) -> list[str]:
    states = confined(folder, "states")
    names = {p.stem for pattern in ("*.json", "*.backup") for p in states.glob(pattern)}
    names.update(p.name for p in confined(folder, "versions").glob("*") if p.is_dir())
    return sorted(n for n in names if re.fullmatch(r"(?:page|slide|sheet|document)-[1-9]\d*", n))


def active(folder: Path) -> bool:
    return any(pending(read_state(folder, unit)) for unit in saved_units(folder))


def pending(state: dict) -> bool:
    return state["state"] in ACTIVE or (
        state["state"] == "interrupted"
        and bool(state.get("resume_requested"))
        and not state.get("recovery_warning")
        and not state.get("recovery_uncertain")
    )


def recover(data: Path) -> None:
    with LOCK:
        for folder in document_folders(data):
            if not folder.is_dir() or not (job_file(folder)).is_file():
                continue
            try:
                units = saved_units(folder)
            except ExplanationError:
                continue
            for unit in units:
                UNCERTAIN_COMMITS.discard((str(folder.resolve()), unit))
                try:
                    state = read_state(folder, unit)
                    from_backup = bool(state.pop("recovery_warning", None))
                    if from_backup:
                        state["recovery_uncertain"] = True
                    if state["state"] in ACTIVE:
                        state.update(
                            state="interrupted",
                            stage="再起動後の再開待ち",
                            error=None,
                            error_code="interrupted",
                            resume_requested=not from_backup
                            and not state.get("recovery_uncertain"),
                            updated_at=now(),
                        )
                        write_state(folder, state)
                    elif from_backup:
                        write_state(folder, state)
                    if not from_backup and not state.get("recovery_uncertain"):
                        for path in confined(folder, f"versions/{unit}").glob("*.json"):
                            if path.stem not in state["published_version_ids"]:
                                path.unlink()
                except (OSError, ValueError, ExplanationError):
                    continue  # An unreadable unit cannot prevent the rest of the app starting.


def copy_saved(source: Path, destination: Path) -> None:
    """Called under library and explanation locks after ordinary source copying."""
    root = destination / "explanations"
    if root.exists():
        shutil.rmtree(root)
    for unit in saved_units(source):
        state = read_state(source, unit)
        if state.get("recovery_warning") or pending(state):
            raise ExplanationError("conflict", "解説の状態を確認してからコピーしてください。")
        copied = empty(unit)
        for version_id in state["published_version_ids"]:
            path = version_path(source, unit, version_id)
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != state["version_hashes"].get(version_id):
                raise ExplanationError("storage", "解説の完成版をコピーできません。")
            value = json.loads(raw)
            if not isinstance(value, dict) or value.get("schema_version") != 1:
                raise ExplanationError("schema", "保存済み解説の版に対応していません。")
            value["generated_document_id"] = value.get(
                "generated_document_id", value["document_id"]
            )
            value["document_id"] = destination.name
            for row in value.get("local_search", {}).get("evidence", []):
                if "chunk_id" in row:
                    row["chunk_id"] = row["chunk_id"].replace(
                        source.name + ":", destination.name + ":", 1
                    )
            new_path = version_path(destination, unit, version_id)
            atomic_json(new_path, value)
            copied["published_version_ids"].append(version_id)
            copied["version_hashes"][version_id] = hashlib.sha256(new_path.read_bytes()).hexdigest()
        copied.update(
            latest_version_id=state["latest_version_id"],
            state="completed"
            if state["latest_version_id"]
            else "not_applicable"
            if state["state"] == "not_applicable"
            else "uncreated",
            source_hash=state.get("source_hash"),
            updated_at=now(),
        )
        write_state(destination, copied)


def markdown(value: dict) -> str:
    from docling_desk.explanation.web import public_url

    body = [
        f"# わかりやすい解説 · {value['unit_id']}",
        f"作成日時: {value['created_at']}",
        f"Web確認: {value['web_search']['status']}",
    ]
    for section in value["explanation"]["sections"]:
        body += [
            "## " + section["title"],
            section["text"],
        ]
    body += ["## 用語の説明"]
    body += [f"- {t['term']}: {t['definition']}" for t in value["explanation"]["glossary"]]
    body += ["## Webからの補足"]
    for supplement in value["explanation"]["supplements"]:
        body += [
            "### " + supplement["title"],
            supplement["text"],
        ]
    used = {ref for s in value["explanation"]["supplements"] for ref in s["evidence_ids"]}
    links = []
    for evidence in value["web_search"]["evidence"]:
        if evidence["id"] not in used or evidence.get("kind") != "web":
            continue
        try:
            url = public_url(evidence["url"])
        except ExplanationError:
            continue
        links.append(url)
    if links:
        body += ["## 補足を詳しく読む"]
    for url in dict.fromkeys(links):
        body += [
            "- " + url,
        ]
    body += [
        "## 対象と制約",
        *value["explanation"]["limitations"],
        f"図は対象外: {value['source']['excluded_pictures']}件 · 位置未判定: {value['unlocated_count']}件",
    ]
    return "\n\n".join(body) + "\n"
