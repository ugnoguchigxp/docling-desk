"""Durable, bounded explanation jobs independent of translation."""

from __future__ import annotations

import logging
import os
import shutil
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

from docling_desk.config import MIN_FREE
from docling_desk.documents.library import LOCK as LIBRARY_LOCK
from docling_desk.explanation.context import (
    document_context,
    numeric_issues,
    outline_evidence,
    pack,
    seed_terms,
    target_blocks,
)
from docling_desk.explanation.provider import (
    Budget,
    ExplanationError,
    configured_profile,
    provider_for,
    validate_explanation,
)
from docling_desk.explanation.search import retrieve
from docling_desk.explanation.source import current_hash, snapshot
from docling_desk.explanation.store import (
    ACTIVE,
    LOCK,
    latest,
    now,
    publish,
    read_checkpoint,
    read_state,
    saved_units,
    write_checkpoint,
    write_state,
)
from docling_desk.explanation.web import WebSearchProvider, research
from docling_desk.storage import document_folders, job_file


def enabled() -> bool:
    return os.environ.get("DOCLING_EXPLANATION_ENABLED", "1") != "0"


def result(folder: Path, unit_id: str, source_hash: str | None = None) -> dict:
    state = read_state(folder, unit_id)
    value, warning = latest(folder, state)
    checksum = source_hash
    if checksum is None:
        try:
            source = snapshot(folder)
            checksum = source["source_hash"]
            if state["updated_at"] is None and not any(u["id"] == unit_id for u in source["units"]):
                raise ExplanationError("not_found", "解説対象が見つかりません。")
        except (OSError, ValueError, KeyError, TypeError):
            if state["updated_at"] is None:
                raise ExplanationError("storage", "原文データを確認できません。")
    saved_hash = value["source_hash"] if value is not None else state.get("source_hash")
    return {
        "state": state,
        "available": value is not None,
        "result": value,
        "warning": warning,
        "source_match": None if checksum is None or saved_hash is None else saved_hash == checksum,
        "stale": saved_hash is not None and checksum is not None and saved_hash != checksum,
    }


def overview(folder: Path) -> dict:
    try:
        source = snapshot(folder)
        units = source["units"]
        checksum = source["source_hash"]
        source_error = None
    except (OSError, ValueError, KeyError, TypeError):
        units, checksum = [], None
        source_error = "原文を照合できません。保存済み解説は閲覧できます。"
    known = {u["id"] for u in units}
    units += [
        {"id": uid, "kind": uid.split("-")[0], "number": int(uid.split("-")[1]), "name": ""}
        for uid in saved_units(folder)
        if uid not in known
    ]
    rows = []
    for unit in units:
        try:
            record = result(folder, unit["id"], checksum)
            rows.append(
                {
                    **{k: unit.get(k) for k in ("id", "kind", "number", "name")},
                    **{
                        k: record[k]
                        for k in ("state", "available", "warning", "source_match", "stale")
                    },
                }
            )
        except ExplanationError as exc:
            rows.append(
                {
                    "id": unit["id"],
                    "kind": unit["kind"],
                    "number": unit["number"],
                    "name": unit.get("name", ""),
                    "available": False,
                    "state": {"state": "failed", "error": str(exc)},
                    "storage_error": str(exc),
                }
            )
    try:
        profile = configured_profile()
        metadata, configuration_error = profile.metadata(), None
    except ExplanationError as exc:
        metadata, configuration_error = {}, str(exc)
    return {
        "units": rows,
        "source_error": source_error,
        "profile": metadata,
        "enabled": enabled(),
        "configuration_error": configuration_error,
    }


class ExplanationManager:
    def __init__(self):
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="explanation")
        self.slots = threading.BoundedSemaphore(3)
        self.stop = threading.Event()
        self.current = None
        self.web = None
        self.guard = threading.Lock()
        self.resumer = None

    def resume_pending(self, data: Path):
        """Continue accepted requests after restart, including a backlog larger than the queue."""

        def resume():
            for folder in sorted(document_folders(data)):
                if self.stop.is_set():
                    return
                if not folder.is_dir() or not (job_file(folder)).is_file():
                    continue
                try:
                    units = saved_units(folder)
                except ExplanationError:
                    continue
                for uid in units:
                    while not self.stop.is_set():
                        try:
                            state = read_state(folder, uid)
                            if state["state"] != "interrupted" or state.get("recovery_warning"):
                                break
                            if not (
                                state.get("resume_requested")
                                or state.get("error_code") == "interrupted"
                                or state.get("error") == "サーバー停止で解説を中断しました。"
                            ):
                                break
                            self.submit(folder, uid, resume=True)
                            break
                        except ExplanationError as exc:
                            if self.stop.is_set():
                                return
                            if exc.code == "queue_full":
                                self.stop.wait(0.5)
                                continue
                            self._resume_failed(folder, uid, exc)
                            break
                        except (OSError, ValueError, KeyError, TypeError):
                            self._resume_failed(
                                folder,
                                uid,
                                ExplanationError("storage", "再開する原文を確認できません。"),
                            )
                            break

        self.resumer = threading.Thread(target=resume, name="explanation-resume", daemon=True)
        self.resumer.start()

    def _resume_failed(self, folder, uid, exc):
        with LOCK:
            try:
                state = read_state(folder, uid)
                if state["state"] == "interrupted":
                    state.update(
                        state="failed",
                        stage="再開できませんでした",
                        error=str(exc),
                        error_code=exc.code,
                        resume_requested=False,
                        updated_at=now(),
                    )
                    write_state(folder, state)
            except (OSError, ValueError, ExplanationError):
                pass

    def close(self):
        self.stop.set()
        with self.guard:
            if self.current:
                self.current.cancel()
            if self.web:
                self.web.cancel()
        # Queued jobs run briefly to persist interrupted status and release slots.
        self.executor.shutdown(wait=False)

    def submit(
        self, folder: Path, unit_id: str, force: bool = False, *, resume: bool = False
    ) -> dict:
        with LIBRARY_LOCK, LOCK:
            state = read_state(folder, unit_id)
            value, warning = latest(folder, state)
            if state["state"] in ACTIVE:
                if current_hash(folder) != state.get(
                    "source_hash"
                ) or configured_profile().metadata()["config_hash"] != state.get("profile", {}).get(
                    "config_hash"
                ):
                    raise ExplanationError(
                        "conflict", "別の原文・設定で解説を処理中です。完了後に再試行してください。"
                    )
                return {"reused": True, "state": state, "warning": warning}
            if value is not None and not force and not resume:
                return {"reused": True, "state": state, "warning": warning}
            if not enabled() or self.stop.is_set():
                raise ExplanationError(
                    "disabled", "新しい解説の生成は停止中です。保存版は閲覧できます。"
                )
            source = snapshot(folder)
            if resume and source["source_hash"] != state.get("source_hash"):
                raise ExplanationError("source_changed", "原文が更新されたため自動再開できません。")
            if source["extraction_state"] not in {"success", "partial"}:
                raise ExplanationError("conflict", "抽出完了後に解説できます。")
            unit = next((u for u in source["units"] if u["id"] == unit_id), None)
            if unit is None:
                raise ExplanationError("not_found", "解説対象が見つかりません。")
            if not unit["blocks"]:
                state.update(
                    state="not_applicable",
                    stage="抽出済み本文・表には解説対象がありません。",
                    source_hash=source["source_hash"],
                    error=None,
                    updated_at=now(),
                )
                write_state(folder, state)
                return {"reused": False, "state": state}
            profile = configured_profile()
            continuing = (resume or state["state"] == "interrupted") and not force
            if continuing and state.get("profile", {}).get("config_hash") not in {
                None,
                profile.metadata()["config_hash"],
            }:
                raise ExplanationError(
                    "configuration_changed",
                    "解説設定が変わったため自動再開できません。再生成してください。",
                )
            groups = [target_blocks(unit)]
            provider = provider_for(profile)
            web = WebSearchProvider(profile.web_provider)
            provider.preflight()
            web.preflight()
            if shutil.disk_usage(folder).free < MIN_FREE:
                raise ExplanationError("storage_space", "保存に必要な空き容量がありません。")
            if not self.slots.acquire(blocking=False):
                raise ExplanationError(
                    "queue_full", "解説の処理キューが満杯です。完了後に再試行してください。"
                )
            attempt = uuid4().hex
            checkpoint = (
                read_checkpoint(
                    folder, unit_id, source["source_hash"], profile.metadata()["config_hash"]
                )
                if continuing
                else {}
            )
            try:
                if not checkpoint:
                    write_checkpoint(
                        folder,
                        unit_id,
                        {
                            "source_hash": source["source_hash"],
                            "config_hash": profile.metadata()["config_hash"],
                        },
                    )
                state.update(
                    state="queued",
                    stage="続きから再開します" if checkpoint else "開始待ち（順番に処理します）",
                    attempt_id=attempt,
                    error=None,
                    error_code=None,
                    review_issues=[],
                    resume_requested=True,
                    started_at=now(),
                    source_hash=source["source_hash"],
                    profile=profile.metadata(),
                    llm_calls=max(checkpoint.get("calls", 0), state.get("llm_calls", 0))
                    if checkpoint
                    else state.get("llm_calls", 0)
                    if continuing
                    else 0,
                    updated_at=now(),
                )
                write_state(folder, state)
                self.executor.submit(
                    self._run, folder, unit, source, groups, provider, web, attempt, checkpoint
                )
            except Exception:
                self.slots.release()
                state.update(
                    state="failed",
                    stage="受付失敗",
                    error="解説を開始できませんでした。",
                    updated_at=now(),
                )
                write_state(folder, state)
                raise
            return {"reused": False, "state": state}

    def _stage(self, folder, unit_id, attempt, stage, **values):
        if self.stop.is_set():
            raise ExplanationError("interrupted", "サーバー停止で解説を中断しました。")
        with LOCK:
            state = read_state(folder, unit_id)
            if state.get("attempt_id") != attempt or state["state"] not in ACTIVE:
                raise ExplanationError("conflict", "解説の実行状態が変わりました。")
            state.update(state="running", stage=stage, updated_at=now(), **values)
            write_state(folder, state)

    def _run(self, folder, unit, source, groups, provider, web, attempt, checkpoint):
        try:
            with self.guard:
                self.current, self.web = provider, web
            budget = Budget(provider)
            budget.calls = max(
                checkpoint.get("calls", 0), read_state(folder, unit["id"]).get("llm_calls", 0)
            )
            budget.input_counts = checkpoint.get("input_counts", [])
            budget.usage_counts = checkpoint.get("usage_counts", [])
            spent = checkpoint.get("spent_seconds", 0)
            budget.deadline -= spent
            started = time.monotonic()
            checkpoint.update(
                source_hash=source["source_hash"],
                config_hash=provider.profile.metadata()["config_hash"],
            )

            def save(*, confirmed=False):
                if not confirmed:
                    budget.remaining()
                    if self.stop.is_set():
                        raise ExplanationError("interrupted", "サーバー停止で解説を中断しました。")
                checkpoint.update(
                    calls=budget.calls,
                    input_counts=budget.input_counts,
                    usage_counts=budget.usage_counts,
                    spent_seconds=min(3600, spent + time.monotonic() - started),
                )
                with LOCK:
                    state = read_state(folder, unit["id"])
                    if state.get("attempt_id") != attempt or state["state"] not in ACTIVE:
                        raise ExplanationError("conflict", "解説の実行状態が変わりました。")
                    state.update(llm_calls=budget.calls, updated_at=now())
                    write_state(folder, state)
                    write_checkpoint(folder, unit["id"], checkpoint)

            budget.on_call = save

            def save_response(task, response):
                checkpoint["draft" if task == "draft" else "final"] = response
                save(confirmed=True)

            budget.on_response = save_response
            budget.check_usage()
            blocks = [b for group in groups for b in group]
            outline, surrounding = document_context(source, unit)
            base = {
                "target_unit": {k: unit[k] for k in ("id", "kind", "number", "name")},
                "document_outline": outline,
                "blocks": blocks,
                "allowed_source_ids": [b["part_id"] for b in blocks],
                "extraction_state": source["extraction_state"],
            }
            draft = checkpoint.get("draft")
            final = checkpoint.get("final")
            if not draft and budget.calls:
                raise ExplanationError(
                    "unknown_outcome",
                    "前回のAI応答を確認できません。2回の上限を守るため自動で呼び直しません。保存版は閲覧できます。",
                )
            if draft and final is None and budget.calls >= 2:
                raise ExplanationError(
                    "unknown_outcome",
                    "最終のAI応答を確認できません。2回の上限に達したため自動で呼び直しません。",
                )
            if draft is None:
                self._stage(folder, unit["id"], attempt, "資料全体と前後の関係を確認")
                seed = retrieve(folder, source, seed_terms(blocks), exclude_pages=[unit["number"]])
                for item in seed["evidence"]:
                    item["id"] = item["id"].replace("local-", "seed-")
                payload = pack(
                    "draft",
                    base,
                    surrounding[:2] + seed["evidence"] + surrounding[2:],
                    provider.profile,
                )
                checkpoint["first_context"] = payload["evidence"]
                self._stage(
                    folder, unit["id"], attempt, "原文・資料構成から下書きと調査方針を作成（1/2）"
                )
                draft = budget.complete("draft", payload)
                checkpoint["draft"] = draft
                save()
            draft_issues = []
            try:
                validate_explanation(
                    draft["explanation"],
                    blocks,
                    checkpoint.get("first_context", []) + outline_evidence(outline),
                )
            except ExplanationError as exc:
                draft_issues.append(str(exc))
            draft_issues.extend(numeric_issues(draft["explanation"], blocks))
            if {f["source_id"] for f in draft["facts"]} != {b["part_id"] for b in blocks}:
                draft_issues.append("原文の全part_idを事実台帳と完成版に含めてください。")
            self._stage(folder, unit["id"], attempt, "不足する資料内の関連箇所を検索")
            local = checkpoint.get("local")
            if local is None:
                local = retrieve(
                    folder,
                    source,
                    [term for p in draft["search_plan"] for term in p["terms"]],
                    exclude_pages=[unit["number"]],
                )
                checkpoint["local"] = local
                save()

            def research_progress(value, stage):
                checkpoint["external"] = value
                if self.stop.is_set():
                    save(confirmed=True)
                    raise ExplanationError("interrupted", "サーバー停止で解説を中断しました。")
                self._stage(folder, unit["id"], attempt, stage)
                save()

            external = checkpoint.get("external", {})
            if not final and (not external or external.get("status") == "researching"):
                self._stage(folder, unit["id"], attempt, "調査方針に沿って補足情報を収集")
                external = research(
                    draft["search_plan"],
                    budget,
                    web,
                    initial=external,
                    progress=research_progress,
                    reserve_seconds=min(provider.profile.timeout, budget.remaining() / 2),
                )
                checkpoint["external"] = external
                save()
            if final is None:
                # Never reissue a final call with an unknown outcome after restart.
                if budget.calls >= 2:
                    raise ExplanationError(
                        "unknown_outcome",
                        "最終のAI応答を確認できません。2回の上限に達したため自動で呼び直しません。",
                    )
                referenced = {
                    ref
                    for section in draft["explanation"]["sections"]
                    for ref in section["evidence_ids"]
                }
                adjacent = [e for e in surrounding if abs(e["distance"]) == 1]
                retained = [e for e in checkpoint.get("first_context", []) if e["id"] in referenced]
                # Alternate local context and external evidence so that neither monopolizes space.
                preferred = []
                for index in range(max(len(adjacent), len(external["evidence"]))):
                    if index < len(adjacent):
                        preferred.append(adjacent[index])
                    if index < len(external["evidence"]):
                        preferred.append(external["evidence"][index])
                payload = pack(
                    "finalize",
                    {
                        **base,
                        "draft": draft["explanation"],
                        "fact_ledger": draft["facts"],
                        "gaps": draft["gaps"],
                        "draft_issues": draft_issues,
                        "web_status": external["status"],
                        "research_topics": external.get("topics", []),
                        "unresolved_topics": [
                            t
                            for t in external.get("topics", [])
                            if t not in external.get("covered_topics", [])
                        ],
                    },
                    retained + preferred + local["evidence"] + surrounding,
                    provider.profile,
                )
                checkpoint["final_context"] = payload["evidence"]
                self._stage(
                    folder, unit["id"], attempt, "調査結果と原文を照合し、解説を完成（2/2）"
                )
                final = budget.complete("finalize", payload)
                checkpoint["final"] = final
                save()
            evidence = checkpoint.get("final_context", []) + outline_evidence(outline)
            merged = validate_explanation(final["explanation"], blocks, evidence)
            missing_numbers = numeric_issues(merged, blocks)
            if missing_numbers:
                self._stage(
                    folder, unit["id"], attempt, "数値の保持を確認", review_issues=missing_numbers
                )
                raise ExplanationError(
                    "quality",
                    "原文の数値が完成版から抜けているため公開しません。途中結果は保存済みです。",
                )
            if not final["audit"]["approved"] or final["audit"]["issues"]:
                self._stage(
                    folder,
                    unit["id"],
                    attempt,
                    "完成版の照合で問題を検出",
                    review_issues=final["audit"]["issues"],
                )
                raise ExplanationError(
                    "quality",
                    "原文との照合で問題が残りました。途中結果を保存し、既存の完成版を保持します。",
                )
            # Keep full retrieved evidence privately; presentation exposes only useful supplements.
            local["evidence"] = list(
                {
                    e["id"]: e
                    for e in checkpoint.get("first_context", []) + evidence + local["evidence"]
                    if e["kind"] == "document"
                }.values()
            )
            value = {
                "source": unit,
                "source_parts": blocks,
                "document_outline": outline,
                "fact_ledger": draft["facts"],
                "output_language": "ja",
                "reader_background": "13歳程度",
                "source_hash": source["source_hash"],
                "hash_version": source["hash_version"],
                "source_name": source["source_name"],
                "extraction_state": source["extraction_state"],
                "unlocated_count": source["unlocated_count"],
                "profile": provider.profile.metadata(),
                "local_search": local,
                "web_search": external,
                "explanation": merged,
                "verification": {
                    "parts": 1,
                    "boundaries": 0,
                    "calls": budget.calls,
                    "input_tokens": budget.input_counts,
                    "provider_usage": budget.usage_counts,
                    "first_context_ids": [e["id"] for e in checkpoint.get("first_context", [])],
                    "final_context_ids": [e["id"] for e in evidence],
                    "audit": final["audit"],
                },
            }
            self._stage(folder, unit["id"], attempt, "完成版を保存")
            with LIBRARY_LOCK, LOCK:
                budget.remaining()
                if self.stop.is_set():
                    raise ExplanationError("interrupted", "サーバー停止で解説を中断しました。")
                if current_hash(folder) != source["source_hash"]:
                    raise ExplanationError(
                        "source_changed", "原文が更新されたため解説を保存しませんでした。"
                    )
                if shutil.disk_usage(folder).free < MIN_FREE:
                    raise ExplanationError(
                        "storage_space", "解説保存に必要な空き容量がありません。"
                    )
                if not publish(folder, unit["id"], attempt, value):
                    raise ExplanationError("conflict", "解説の保存状態が変わりました。")
        except Exception as exc:
            logging.getLogger(__name__).error(
                "Explanation %s/%s failed (%s)\n%s",
                folder.name,
                unit["id"],
                type(exc).__name__,
                "".join(traceback.format_tb(exc.__traceback__)),
            )
            message = (
                str(exc)
                if isinstance(exc, ExplanationError)
                else "原文・検索データまたは解説の処理に失敗しました。"
            )
            code = exc.code if isinstance(exc, ExplanationError) else "processing"
            with LOCK:
                try:
                    state = read_state(folder, unit["id"])
                    if state.get("attempt_id") == attempt and state["state"] in ACTIVE:
                        state.update(
                            state="interrupted" if self.stop.is_set() else "failed",
                            stage="中断" if self.stop.is_set() else "失敗",
                            error=message,
                            error_code=code,
                            resume_requested=self.stop.is_set(),
                            updated_at=now(),
                        )
                        write_state(folder, state)
                except (OSError, ValueError, ExplanationError):
                    pass  # A disk failure must leave the last published version untouched.
        finally:
            with self.guard:
                self.current, self.web = None, None
            self.slots.release()
