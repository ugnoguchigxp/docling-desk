"""Bounded research/draft/verify/correct/publish state machine."""

from __future__ import annotations

import json
import math

from .files import (
    FatalProviderError,
    NeedsReview,
    RetryLater,
    Stopped,
    hash_text,
    js_json,
    utf16_length,
)
from .instructions import load, validate
from .markdown import parse_json, restore, validate_translation, verify_result
from .provider import Runtime, wait_until
from .publish import assert_inputs, candidate, publish
from .research import research_for, translation_context
from .snapshot import LIMITS, REQUEST_POLICY, RESEARCH_VERSION, recipe_hash_for, request_policy
from .terminology import VERSION, consistency_checks, term_context, term_issues


class BatchWorker:
    def __init__(
        self, store, client, repository, runtime=None, publisher=publish, instructions=None
    ):
        self.store, self.client, self.repository = store, client, repository
        self.instructions = (
            load(repository.root) if instructions is None else validate(instructions)
        )
        self.runtime, self.publisher = runtime or Runtime(), publisher
        self.request_policy = REQUEST_POLICY

    def check(self):
        if self.runtime.stopping() or self.store.setting("paused") == "1":
            raise Stopped("翻訳バッチを停止しました。")

    def send(self, role, instructions, value, key, job_id):
        self.check()
        text = js_json(value)
        if utf16_length(text) > self.request_policy["inputChars"]:
            raise NeedsReview("翻訳・検証の入力予算を超えています。")
        request = {
            "instructions": instructions,
            "input": text,
            "maxOutputTokens": min(
                self.request_policy["maxOutputTokens"],
                max(
                    self.request_policy["minOutputTokens"],
                    math.ceil(
                        sum(utf16_length(u["text"]) for u in value.get("units", [])) * 1.5 + 512
                    ),
                ),
            ),
        }
        step = "python-request/" + key[len(job_id) + 1 :]
        saved = self.store.checkpoint(job_id, step)
        if saved and saved["hash"] != hash_text(text):
            raise NeedsReview("保存した要求と再開後の入力が一致しません。")
        legacy = self.store.checkpoint(job_id, "term-input/call/" + key[len(job_id) + 1 :])
        if legacy is not None and (
            legacy.get("requestHash") != hash_text(text) or legacy.get("role") != role
        ):
            raise NeedsReview("保存した旧形式の要求と再開後の入力が一致しません。")
        self.store.save(job_id, step, {"hash": hash_text(text)})
        return parse_json(self.client.request(role, request, key, job_id))

    def process(self, job):
        snapshot = json.loads(job["payload"])
        if snapshot.get("preparationError"):
            raise NeedsReview(snapshot["preparationError"])
        instructions = validate(snapshot.get("instructions", self.instructions))
        if instructions != self.instructions:
            raise NeedsReview("登録時から翻訳指示が変わりました。")
        if "conditions" in snapshot and snapshot["conditions"] != {
            "limits": LIMITS,
            "termVersion": VERSION,
            "researchVersion": RESEARCH_VERSION,
            "requestPolicy": REQUEST_POLICY,
        }:
            raise NeedsReview("登録時の処理条件をこのワーカーでは実行できません。")
        if snapshot.get("engine") not in {None, "python-wiki-batch-v1"}:
            raise NeedsReview("未対応の翻訳エンジンです。")
        self.request_policy = request_policy(snapshot)
        if (
            job["input_hash"] != snapshot["inputHash"]
            or job["recipe_hash"] != snapshot["recipeHash"]
        ):
            raise NeedsReview("保存したジョブと処理条件が一致しません。")
        if snapshot.get("models") != self.client.models:
            raise NeedsReview("登録時からモデルのデプロイ名が変わりました。")
        if snapshot["recipeHash"] != recipe_hash_for(
            snapshot["references"],
            snapshot.get("models"),
            snapshot.get("terminology"),
            snapshot.get("engine"),
            instructions,
        ):
            raise NeedsReview("登録時から翻訳の処理条件が変わりました。")
        if job["status"] != "publishing":
            assert_inputs(self.repository, snapshot, self.client.models)
            research = research_for(
                self.store, self.client, self.runtime, self.repository, job, snapshot, instructions
            )
            terms, resolution = snapshot["terminology"], research["registeredTerminology"]
            for packet in snapshot["packets"]:
                self.check()
                saved = self.store.packet(job["id"], packet["position"])
                if saved["verification"] and json.loads(saved["verification"])["approved"]:
                    validate_translation(json.loads(saved["draft"]), packet)
                    continue
                value = {
                    "units": [
                        {
                            "id": u["id"],
                            "text": u["text"],
                            "kind": u["kind"],
                            "protectedValues": {
                                k: v for k, v in u["keep"].items() if k in u["text"]
                            },
                        }
                        for u in packet["units"]
                    ],
                    **translation_context(
                        research, "\n".join(restore(u, u["text"]) for u in packet["units"])
                    ),
                    "registeredTerminology": term_context(
                        terms, resolution, [u["id"] for u in packet["units"]]
                    ),
                }
                prefix = f"{job['id']}/{packet['position']}"
                if saved["draft"]:
                    draft = validate_translation(json.loads(saved["draft"]), packet)
                else:
                    self.store.set("worker_stage", "draft")
                    self.runtime.log(
                        f"{job['page_key']}：下訳 {packet['position'] + 1}/{len(snapshot['packets'])}"
                    )
                    draft = validate_translation(
                        self.send(
                            "draft", instructions["draft"], value, prefix + "/draft", job["id"]
                        ),
                        packet,
                    )
                    self.store.save_packet(job["id"], packet["position"], draft)

                def verify(draft, suffix):
                    issues = term_issues(terms, resolution, packet["units"], draft["translations"])
                    result = verify_result(
                        self.send(
                            "verify",
                            instructions["verify"],
                            {**value, "translation": draft, "terminologyIssues": issues},
                            prefix + suffix,
                            job["id"],
                        ),
                        packet,
                    )
                    by_id = {i["id"]: i for i in result["issues"]}
                    by_id.update(
                        {
                            i["id"]: {"id": i["id"], "reason": i["reason"]}
                            for i in issues
                            if not i.get("semanticOnly")
                        }
                    )
                    return {
                        "approved": result["approved"] and not by_id,
                        "issues": list(by_id.values()),
                    }

                self.store.set("worker_stage", "verify")
                self.runtime.log(f"{job['page_key']}：対訳を検証")
                result = verify(draft, "/verify-corrected" if saved["corrected"] else "/verify")
                self.store.save_packet(
                    job["id"], packet["position"], draft, result, bool(saved["corrected"])
                )
                if not result["approved"] and not saved["corrected"]:
                    self.runtime.log(f"{job['page_key']}：指摘を修正")
                    draft = validate_translation(
                        self.send(
                            "draft",
                            instructions["draft"],
                            {
                                **value,
                                "previousTranslation": draft,
                                "corrections": result["issues"],
                            },
                            prefix + "/correct",
                            job["id"],
                        ),
                        packet,
                    )
                    self.store.save_packet(job["id"], packet["position"], draft, corrected=True)
                    result = verify(draft, "/verify-corrected")
                    self.store.save_packet(job["id"], packet["position"], draft, result, True)
                if not result["approved"]:
                    raise NeedsReview("; ".join(i["reason"] for i in result["issues"]))
            translations = [
                t
                for packet in snapshot["packets"]
                for t in json.loads(self.store.packet(job["id"], packet["position"])["draft"])[
                    "translations"
                ]
            ]
            for position, value in enumerate(
                consistency_checks(terms, resolution, snapshot["units"], translations)
            ):
                step = f"term-consistency/{position}/{hash_text(js_json(value))}"
                result = self.store.checkpoint(job["id"], step)
                if result is None:
                    result = verify_result(
                        self.send(
                            "verify",
                            instructions["verify"],
                            value,
                            job["id"] + "/" + step,
                            job["id"],
                        ),
                        {"units": value["units"]},
                    )
                    self.store.save(job["id"], step, result)
                if not result["approved"]:
                    self.store.save(job["id"], "term-consistency", result)
                    raise NeedsReview("ページ内の用語表記が一致しません。")
            self.check()
            assert_inputs(self.repository, snapshot, self.client.models)
            self.store.update(job["id"], "publishing", now=self.runtime.now())
        marker = self.repository.root / "data/translation/publication.json"
        if marker.exists():
            from docling_desk.knowledge.catalog import safe_file

            saved = json.loads(marker.read_text(encoding="utf-8"))
            if saved.get("id") != job["id"]:
                raise NeedsReview("別の記事の公開復旧が必要です。")
            file = safe_file(self.repository.root, saved["candidate_path"])
        else:
            file = candidate(self.store, job, snapshot, True)
        result = self.publisher(self.repository, self.store.job(job["id"]), snapshot, file)
        if (
            not isinstance(result, dict)
            or result.get("published") is not True
            or result.get("indexed") is not True
        ):
            raise NeedsReview("本文・目次・検索の更新を確認できません。")
        self.store.db.execute("BEGIN IMMEDIATE")
        try:
            self.store.set("next_page_at", self.runtime.now() + 60000)
            self.store.update(job["id"], "completed", now=self.runtime.now())
            self.store.db.execute("COMMIT")
            self.runtime.log(f"{job['page_key']}：公開・検索登録が完了")
        except BaseException:
            self.store.db.execute("ROLLBACK")
            raise

    def run(self, limit=None, trial=False):
        # The CLI holds worker_lock before entering; only then may recovery occur.
        self.store.recover(self.runtime.now())
        selected = self.store.trial(self.runtime.now())["jobIds"] if trial else None
        if selected is not None and any(
            r[0] not in selected
            for r in self.store.db.execute("SELECT id FROM jobs WHERE status='publishing'")
        ):
            raise NeedsReview("Trial対象外に公開の復旧が必要です。")
        outcome = {"completed": 0, "needsReview": 0, "failed": 0}
        handled = 0
        self.store.set("worker_pid", __import__("os").getpid())
        try:
            while limit is None or handled < limit:
                self.check()
                wait_until(self.store, self.runtime, lambda: self.store.number("next_page_at"))
                job = self.store.next(self.runtime.now(), selected)
                if job is None:
                    rows = self.store.db.execute(
                        "SELECT id,retry_at FROM jobs WHERE status IN ('queued','retry_wait','publishing')"
                    ).fetchall()
                    rows = [r for r in rows if selected is None or r[0] in selected]
                    if not rows:
                        break
                    wait_until(self.store, self.runtime, lambda: min(r[1] for r in rows))
                    continue
                try:
                    self.process(job)
                    outcome["completed"] += 1
                    handled += 1
                except RetryLater as exc:
                    self.store.update(
                        job["id"], "retry_wait", str(exc), exc.deadline, self.runtime.now()
                    )
                except Stopped:
                    self.store.update(
                        job["id"],
                        "queued"
                        if self.store.job(job["id"])["status"] != "publishing"
                        else "publishing",
                        now=self.runtime.now(),
                    )
                    raise
                except Exception as exc:
                    current = self.store.job(job["id"])
                    if current["status"] == "publishing":
                        self.store.update(
                            job["id"],
                            "publishing",
                            "公開を復旧する必要があります。",
                            now=self.runtime.now(),
                        )
                        self.store.set("paused", "1")
                        break
                    state = "needs_review" if isinstance(exc, NeedsReview) else "failed"
                    # Candidate and checkpoints remain available for inspection/retry.
                    try:
                        candidate(self.store, job, json.loads(job["payload"]), False)
                    except (ValueError, OSError, KeyError):
                        pass
                    self.store.update(
                        job["id"],
                        state,
                        str(exc) if isinstance(exc, NeedsReview) else "翻訳処理に失敗しました。",
                        now=self.runtime.now(),
                    )
                    outcome["needsReview" if state == "needs_review" else "failed"] += 1
                    handled += 1
                    self.store.set("next_page_at", self.runtime.now() + 60000)
                    if isinstance(exc, FatalProviderError):
                        self.store.set("paused", "1")
                        break
        except Stopped:
            pass
        finally:
            self.store.set("worker_pid", 0)
            self.store.set("worker_stage", "")
        return {**self.store.status(), "outcome": outcome}
