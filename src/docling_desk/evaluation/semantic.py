"""Offline semantic checks for full-text search and context. Live models are not called."""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path

import httpx

from docling_desk.evaluation.benchmark import import_corpus
from docling_desk.evaluation.corpus import (
    CORPUS,
    NAMESPACE,
    input_fingerprint,
    load_articles,
    load_cases,
    load_replay,
)
from docling_desk.evaluation.path import confirm_retrieval
from docling_desk.evaluation.recording import machine_info, rollup, source_fingerprint
from docling_desk.evaluation.session import IsolatedApp


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def judge_retrieval(case: dict, hits: list[dict]) -> dict:
    observed = [hit.get("source_key") for hit in hits[:5]]
    if not case["answerable"]:
        return {
            "status": "not_applicable",
            "reason": "答えがない質問には正解根拠の取得を要求しない",
            "hit_source_keys": observed,
            "mechanical": True,
        }
    passages = []
    for passage in case.get("gold_passages", []):
        passages.append(
            {
                "anchor": passage["anchor"],
                "source_key": passage["source_key"],
                "found": any(
                    passage["text"] in hit.get("text", "")
                    and hit.get("source_key") == passage["source_key"]
                    for hit in hits[:5]
                ),
            }
        )
    evidence_ok = all(key in observed for key in case["required_evidence"])
    passages_ok = bool(passages) and all(item["found"] for item in passages)
    return {
        "status": "pass" if evidence_ok and passages_ok else "fail",
        "reason": "required_evidence_in_top5"
        if evidence_ok and passages_ok
        else "evidence_missing",
        "hit_source_keys": observed,
        "passages": passages,
        "mechanical": True,
    }


def judge_context(case: dict, context_text: str | None, citations: list[dict]) -> dict:
    if not case["answerable"]:
        return {
            "status": "not_applicable",
            "reason": "答えがない質問の文脈充足は要求しない",
            "mechanical": True,
        }
    if context_text is None:
        return {
            "status": "not_run",
            "reason": "検索で根拠が取れず文脈を取得していない",
            "mechanical": True,
        }
    missing = []
    unresolved = False
    for fact in case.get("required_facts", []):
        surfaces = fact.get("surfaces") or []
        if not surfaces:
            unresolved = True
            continue
        if not all(surface in context_text for surface in surfaces):
            missing.append(fact["key"])
    conflicts = [
        surface for surface in case.get("conflicting_surfaces", []) if surface in context_text
    ]
    table_missing = [
        relation["signature"]
        for relation in case.get("table_relations", [])
        if relation["signature"] not in context_text
    ]
    extra = [
        citation.get("source_key")
        for citation in citations
        if citation.get("source_key") not in case["required_evidence"]
    ]
    if any(citation.get("source_key") is None for citation in citations):
        return {
            "status": "fail",
            "reason": "unknown_source",
            "missing_facts": missing,
            "mechanical": True,
        }
    reasons = []
    if missing:
        reasons.append("required_fact_missing")
    if table_missing:
        reasons.append("table_relation_mismatch")
    if conflicts:
        reasons.append("mixed_evidence")
    if reasons:
        return {
            "status": "fail",
            "reasons": reasons,
            "missing_facts": missing,
            "extra_source_keys": extra,
            "mechanical": True,
        }
    if extra:
        return {
            "status": "review_required",
            "reason": "要求出典以外が文脈に含まれる。矛盾する表面形はないが、余分な根拠を機械的には無害としない。",
            "extra_source_keys": extra,
            "passed": False,
        }
    if unresolved:
        return {
            "status": "review_required",
            "reason": "事実の表面形がなく、機械判定できない",
            "extra_source_keys": extra,
            "passed": False,
        }
    return {
        "status": "pass",
        "reason": "required_facts_present",
        "extra_source_keys": [],
        "mechanical": True,
    }


def judge_answer(case: dict, answer: dict | None, evidence_texts: list[str] | None = None) -> dict:
    if answer is None:
        return {
            "status": "not_run",
            "reason": "実回答モデルは未実施",
            "model_audit_ignored": True,
        }
    audit = answer.get("model_audit")
    base = {"model_audit": audit, "model_audit_ignored": True}
    claims = answer.get("claims") or []
    if answer.get("abstained"):
        if case["answerable"]:
            return {
                **base,
                "status": "fail",
                "reason": "unnecessary_abstention",
                "mechanical": True,
            }
        if claims:
            return {**base, "status": "fail", "reason": "unsupported_assertion", "mechanical": True}
        return {**base, "status": "pass", "reason": "abstained", "mechanical": True}
    if not case["answerable"]:
        return {**base, "status": "fail", "reason": "unsupported_assertion", "mechanical": True}
    if not claims:
        return {**base, "status": "fail", "reason": "missing_claim", "mechanical": True}
    combined = "\n".join(claim.get("text", "") for claim in claims)
    failures: list[str] = []
    reviews: list[str] = []
    paraphrased = any(claim.get("paraphrase") for claim in claims)
    for fact in case.get("required_facts", []):
        surfaces = fact.get("surfaces") or []
        present = bool(surfaces) and all(surface in combined for surface in surfaces)
        if _conflicting_quantity(case, fact, combined):
            failures.append("numeric_mismatch")
        flips = [surface for surface in fact.get("flip_surfaces", []) if surface in combined]
        if flips:
            failures.append("negation_reversed")
        if present:
            continue
        if paraphrased or not surfaces:
            reviews.append("claim_not_mechanically_checkable")
            continue
        if isinstance(fact.get("value"), (int, float)) and not isinstance(fact.get("value"), bool):
            failures.append("numeric_mismatch")
        else:
            failures.append("required_fact_missing")
    for forbidden in case.get("forbidden_claims", []):
        if forbidden in combined:
            failures.append("forbidden_claim")
    for relation in case.get("table_relations", []):
        if relation["signature"] not in combined:
            failures.append("table_relation_mismatch")
    for claim in claims:
        cited = claim.get("cited_text") or ""
        if evidence_texts is not None and (
            not cited or not any(cited in item for item in evidence_texts)
        ):
            failures.append("citation_not_in_evidence")
            continue
        if claim.get("paraphrase"):
            reviews.append("citation_support_not_mechanical")
            continue
        keys = claim.get("citation_source_keys") or []
        if not keys:
            failures.append("missing_citation")
            continue
        if any(key not in case["required_evidence"] for key in keys):
            failures.append("wrong_citation")
            continue
        if not cited:
            reviews.append("citation_support_not_mechanical")
        elif claim.get("text", "") not in cited:
            reviews.append("unsupported_addition")
    failures = _unique(failures)
    reviews = _unique(reviews)
    if failures:
        return {**base, "status": "fail", "reasons": failures, "mechanical": True}
    if reviews:
        return {**base, "status": "review_required", "reasons": reviews, "passed": False}
    return {**base, "status": "pass", "reason": "mechanical_claim_and_citation", "mechanical": True}


def _conflicting_quantity(case: dict, fact: dict, text: str) -> bool:
    value = fact.get("value")
    unit = fact.get("unit")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isinstance(unit, str):
        return False
    number = str(int(value)) if isinstance(value, float) and value.is_integer() else str(value)
    token = f"{number}{unit}"
    prefixes = []
    for passage in case.get("gold_passages", []):
        passage_text = passage.get("text") or ""
        start = 0
        while True:
            index = passage_text.find(token, start)
            if index < 0:
                break
            prefix = passage_text[:index]
            for separator in ("。", "\n"):
                prefix = prefix.split(separator)[-1]
            prefix = prefix.strip()
            if prefix and prefix not in prefixes:
                prefixes.append(prefix)
            start = index + len(token)
    for prefix in prefixes:
        pattern = rf"(?:^|(?<=[。\n])){re.escape(prefix)}(\d+(?:\.\d+)?){re.escape(unit)}"
        for match in re.finditer(pattern, text):
            if float(match.group(1)) != float(value):
                return True
    return False


def _replace(value, old: str, new: str):
    if isinstance(value, str):
        return value.replace(old, new)
    if isinstance(value, list):
        return [_replace(item, old, new) for item in value]
    if isinstance(value, dict):
        return {key: _replace(item, old, new) for key, item in value.items()}
    return value


def gold_observation(case: dict, context_text: str | None = None) -> dict:
    text = (
        context_text
        if context_text is not None
        else "\n".join(passage["text"] for passage in case.get("gold_passages", []))
    )
    keys = list(case.get("required_evidence", []))
    return {
        "origin": "authored_gold_passage_copy",
        "context_text": text,
        "citations": [{"source_key": key, "text": text} for key in keys],
        "hits": [
            {
                "source_key": key,
                "text": text,
                "chunk_id": "probe",
                "source_id": "probe",
                "revision": "probe",
            }
            for key in keys
        ],
        "answer": {
            "abstained": False,
            "model_audit": "approved",
            "claims": [
                {
                    "text": text,
                    "citation_source_keys": keys,
                    "cited_text": text,
                    "paraphrase": False,
                }
            ],
        },
    }


def _table_text() -> str:
    article = next(
        item for item in load_articles() if item["source_key"] == "fictional-inspection-table-v1"
    )
    return "\n".join(line for line in article["body"].splitlines() if line.startswith("|"))


def apply_mutation(name: str, case: dict, observation: dict) -> dict:
    mutated = copy.deepcopy(observation)
    if name == "numeric_102":
        return _replace(mutated, "120箱", "102箱")
    if name == "negation_flip":
        return _replace(mutated, "雨天時は搬出禁止", "雨天時は搬出可能")
    if name == "table_swap":
        swapped = (
            _table_text()
            .replace("| 試料採取 | 12 | 個 |", "| 試料採取 | 12 | 箱 |")
            .replace("| 通常搬出 | 120 | 箱 |", "| 通常搬出 | 120 | 個 |")
        )
        mutated["context_text"] = swapped
        mutated["answer"]["claims"][0]["text"] = swapped
        mutated["answer"]["claims"][0]["cited_text"] = swapped
        return mutated
    if name == "wrong_citation":
        claim = mutated["answer"]["claims"][0]
        claim["citation_source_keys"] = ["fictional-east-warehouse-v1"]
        claim["cited_text"] = "東倉庫の搬出上限は80個です。雨天時も搬出できます。"
        mutated["citations"] = [
            {"source_key": "fictional-east-warehouse-v1", "text": claim["cited_text"]}
        ]
        return mutated
    if name == "unsupported":
        mutated["answer"] = {
            "abstained": False,
            "model_audit": "approved",
            "claims": [
                {
                    "text": case["unsupported_example"],
                    "citation_source_keys": ["fictional-carry-rule-v1"],
                    "cited_text": "搬出上限は120箱です。雨天時は搬出禁止です。",
                    "paraphrase": False,
                }
            ],
        }
        return mutated
    raise KeyError(name)


def _stored_texts(observation: dict) -> list[str]:
    texts = []
    context = observation.get("context_text")
    if isinstance(context, str) and context:
        texts.append(context)
    for citation in observation.get("citations") or []:
        text = citation.get("text") if isinstance(citation, dict) else None
        if isinstance(text, str) and text and text not in texts:
            texts.append(text)
    return texts


def mutation_probes(cases: list[dict]) -> dict:
    by_id = {case["case_id"]: case for case in cases}
    planted = [
        ("numeric_102", "carry-lexical-001", "numeric_mismatch"),
        ("negation_flip", "carry-lexical-001", "negation_reversed"),
        ("table_swap", "table-sample-008", "table_relation_mismatch"),
        ("wrong_citation", "west-near-011", "wrong_citation"),
        ("unsupported", "snow-unanswerable-015", "unsupported_assertion"),
    ]
    results = []
    for name, case_id, expected in planted:
        case = by_id[case_id]
        base = gold_observation(case, _table_text() if name == "table_swap" else None)
        mutated = apply_mutation(name, case, base)
        evidence = _stored_texts(mutated)
        first = judge_answer(case, mutated["answer"], evidence)
        second = judge_answer(case, mutated["answer"], evidence)
        without_audit = copy.deepcopy(mutated["answer"])
        without_audit["model_audit"] = None
        ignored = judge_answer(case, without_audit, evidence)
        reasons = first.get("reasons") or ([first["reason"]] if first.get("reason") else [])
        results.append(
            {
                "id": name,
                "case_id": case_id,
                "expected": expected,
                "judgment": first,
                "detected": first["status"] == "fail" and expected in reasons,
                "model_audit": mutated["answer"].get("model_audit"),
                "model_audit_changed_outcome": first["status"] != ignored["status"],
                "repeat_match": first == second,
                "input": "authored copy with a planted error, separate from the corpus",
            }
        )
    paraphrase_case = by_id["carry-lexical-001"]
    paraphrase = gold_observation(paraphrase_case)
    paraphrase["answer"]["claims"][0]["text"] = "雨の日は出せません。"
    paraphrase["answer"]["claims"][0]["paraphrase"] = True
    paraphrase_judgment = judge_answer(
        paraphrase_case, paraphrase["answer"], _stored_texts(paraphrase)
    )
    detected = sum(item["detected"] for item in results)
    return {
        "execution_mode": "mutation_probe",
        "planted": len(results),
        "detected": detected,
        "detection_complete": (
            detected == len(results)
            and all(
                item["repeat_match"] and not item["model_audit_changed_outcome"] for item in results
            )
            and paraphrase_judgment["status"] == "review_required"
        ),
        "results": results,
        "paraphrase": {
            "judgment": paraphrase_judgment,
            "review_required": paraphrase_judgment["status"] == "review_required",
            "auto_passed": paraphrase_judgment["status"] == "pass",
            "model_audit_ignored": True,
        },
        "proves": "意図した誤りと、機械判定できない言い換えを区別できる",
        "does_not_prove": "実務資料や現行モデルの精度",
    }


def _search(client: httpx.Client, query: str) -> dict:
    response = client.post(
        "/api/knowledge/search",
        json={
            "query": query,
            "mode": "text",
            "kind": "wiki",
            "namespace": NAMESPACE,
            "limit": 5,
        },
    )
    if response.status_code != 202:
        return {"id": None, "state": "failed", "output": {}, "error": response.text[:500]}
    try:
        posted = response.json()
    except ValueError as exc:
        return {"id": None, "state": "failed", "output": {}, "error": str(exc)}
    retrieval_id = posted.get("id") if isinstance(posted, dict) else None
    if not retrieval_id:
        return {
            "id": None,
            "state": "failed",
            "output": {},
            "error": "検索応答に id がありません。",
        }
    confirmed = confirm_retrieval(client, retrieval_id)
    if not confirmed["ready"]:
        return {
            "id": posted["id"],
            "state": confirmed["outcome"],
            "output": {},
            "error": confirmed.get("error"),
        }
    return confirmed["body"]


def _hits(task: dict, id_to_key: dict[str, str]) -> list[dict] | None:
    output = task.get("output")
    results = output.get("results") if isinstance(output, dict) else []
    if not isinstance(results, list):
        return None
    hits = []
    for item in results:
        if not isinstance(item, dict) or any(
            key not in item for key in ("chunk_id", "source_id", "revision", "text")
        ):
            return None
        hits.append(
            {
                "chunk_id": item["chunk_id"],
                "source_id": item["source_id"],
                "source_key": id_to_key.get(item["source_id"]),
                "revision": item["revision"],
                "text": item["text"],
            }
        )
    return hits[:5]


def _parse_context(
    response: httpx.Response, id_to_key: dict[str, str]
) -> tuple[str | None, list[dict], dict | None]:
    if response.status_code != 200:
        return (
            None,
            [],
            {"status": "fail", "reason": f"http_{response.status_code}", "mechanical": True},
        )
    try:
        payload = response.json()
    except ValueError as exc:
        return (
            None,
            [],
            {
                "status": "fail",
                "reason": "invalid_context_json",
                "error": str(exc),
                "mechanical": True,
            },
        )
    if not isinstance(payload, dict) or not isinstance(payload.get("text"), str):
        return None, [], {"status": "fail", "reason": "invalid_context_shape", "mechanical": True}
    citations = []
    for item in payload.get("citations") or []:
        if not isinstance(item, dict) or "source_id" not in item:
            return (
                None,
                [],
                {"status": "fail", "reason": "invalid_context_shape", "mechanical": True},
            )
        citations.append(
            {
                "source_id": item["source_id"],
                "source_key": id_to_key.get(item["source_id"]),
                "revision": item.get("revision"),
            }
        )
    return payload["text"], citations, None


def _context(client: httpx.Client, task: dict, hits: list[dict]) -> httpx.Response:
    return client.post(
        "/api/knowledge/context",
        json={
            "retrieval_id": task["id"],
            "chunk_ids": [hit["chunk_id"] for hit in hits],
            "budget": 4000,
        },
    )


def _import_bytes(client: httpx.Client, filename: str, raw: bytes) -> None:
    response = client.post(
        "/api/wiki/import",
        data={"namespace": NAMESPACE},
        files=[("files", (filename, raw, "text/markdown"))],
    )
    if response.status_code != 201:
        raise RuntimeError(response.text)


def _incomplete(case: dict, task: dict) -> dict:
    return {
        "case_id": case["case_id"],
        "case_type": case["case_type"],
        "category": case["category"],
        "difficulty": case["difficulty"],
        "question_style": case["question_style"],
        "query_policy": case["query_policy"],
        "review_status": case["review_status"],
        "execution_mode": "live_text_search",
        "search_query": case["search_query"],
        "query_rewritten": False,
        "retrieval": {
            "status": "fail",
            "reason": "search_not_completed",
            "state": task.get("state"),
            "mechanical": True,
        },
        "context": {"status": "not_run", "reason": "検索が完了していない", "mechanical": True},
        "answer": judge_answer(case, None),
        "hits": [],
        "context_http_status": None,
        "citation_source_keys": [],
        "context_text": None,
    }


def _live_case(client: httpx.Client, case: dict, id_to_key: dict[str, str]) -> dict:
    if case["query_policy"] == "question_as_written" and case["search_query"] != case["question"]:
        raise ValueError(f"{case['case_id']} の自然な質問が検索語へ置き換えられています。")
    task = _search(client, case["search_query"])
    if task.get("state") != "completed":
        return _incomplete(case, task)
    hits = _hits(task, id_to_key)
    if hits is None:
        return _incomplete(case, {"state": "malformed"})
    retrieval = judge_retrieval(case, hits)
    context_text = None
    citations = []
    context_status = None
    if case["answerable"] and hits:
        response = _context(client, task, hits)
        context_status = response.status_code
        context_text, citations, error = _parse_context(response, id_to_key)
        context = error or judge_context(case, context_text, citations)
    elif not case["answerable"]:
        context = judge_context(case, None, [])
    else:
        context = judge_context(case, None, [])
    return {
        "case_id": case["case_id"],
        "case_type": case["case_type"],
        "category": case["category"],
        "difficulty": case["difficulty"],
        "question_style": case["question_style"],
        "query_policy": case["query_policy"],
        "review_status": case["review_status"],
        "execution_mode": "live_text_search",
        "search_query": case["search_query"],
        "query_rewritten": False,
        "retrieval": retrieval,
        "context": context,
        "answer": judge_answer(case, None),
        "hits": [
            {
                "source_key": hit["source_key"],
                "source_id": hit["source_id"],
                "revision": hit["revision"],
                "chunk_id": hit["chunk_id"],
                "passage_found": any(
                    passage["text"] in hit["text"] for passage in case.get("gold_passages", [])
                ),
            }
            for hit in hits
        ],
        "context_http_status": context_status,
        "citation_source_keys": [item["source_key"] for item in citations],
        "context_text": context_text,
    }


def _revision(client: httpx.Client, case: dict, id_to_key: dict[str, str]) -> dict:
    previous = (CORPUS / case["previous_path"]).read_bytes()
    current = (CORPUS / case["path"]).read_bytes()
    _import_bytes(client, case["path"], previous)
    old_task = _search(client, case["previous_query"])
    old_hits = _hits(old_task, id_to_key) or []
    _import_bytes(client, case["path"], current)
    old_output = {}
    rejected = None
    if old_task.get("state") == "completed" and old_task.get("id") and old_hits:
        recheck = client.get(f"/api/knowledge/retrievals/{old_task['id']}")
        if recheck.status_code == 200:
            try:
                old_output = recheck.json().get("output") or {}
            except ValueError:
                old_output = {}
        rejected = _context(client, old_task, old_hits).status_code
    new_task = _search(client, case["search_query"])
    new_hits = _hits(new_task, id_to_key) if new_task.get("state") == "completed" else []
    new_hits = new_hits or []
    new_text = None
    citations = []
    context_error = None
    if new_hits:
        response = _context(client, new_task, new_hits)
        new_text, citations, context_error = _parse_context(response, id_to_key)
    obsolete = case["obsolete_surfaces"][0]
    current_surface = case["gold_passages"][0]["text"]
    observed = new_text or ""
    ok = (
        old_task.get("state") == "completed"
        and new_task.get("state") == "completed"
        and bool(old_output.get("stale"))
        and rejected == 409
        and current_surface in observed
        and obsolete not in observed
    )
    return {
        "case_id": case["case_id"],
        "case_type": "revision",
        "category": case["category"],
        "difficulty": case["difficulty"],
        "review_status": case["review_status"],
        "execution_mode": "live_text_search",
        "retrieval": {
            "status": "pass" if ok else "fail",
            "reason": "old_retrieval_rejected_and_current_passage_present"
            if ok
            else "revision_mismatch",
            "stale": bool(old_output.get("stale")),
            "old_context_status": rejected,
            "current_passage_found": current_surface in observed,
            "obsolete_surface_absent": obsolete not in observed,
            "current_revision": new_hits[0]["revision"] if new_hits else None,
            "mechanical": True,
        },
        "context": context_error or judge_context(case, new_text, citations),
        "answer": judge_answer(case, None),
        "hits": [
            {
                "source_key": hit["source_key"],
                "revision": hit["revision"],
                "chunk_id": hit["chunk_id"],
            }
            for hit in new_hits
        ],
    }


def _deletion(client: httpx.Client, case: dict, id_to_key: dict[str, str]) -> dict:
    task = _search(client, case["search_query"])
    if task.get("state") != "completed":
        return _incomplete(case, task)
    hits = _hits(task, id_to_key)
    if hits is None:
        return _incomplete(case, {"state": "malformed"})
    source_id = next(
        (hit["source_id"] for hit in hits if hit["source_key"] == case["source_key"]), None
    )
    if not source_id:
        return {
            "case_id": case["case_id"],
            "case_type": "deletion",
            "category": case["category"],
            "difficulty": case["difficulty"],
            "review_status": case["review_status"],
            "execution_mode": "live_text_search",
            "retrieval": {
                "status": "fail",
                "reason": "before_delete_evidence_missing",
                "mechanical": True,
            },
            "context": {"status": "not_run", "reason": "削除前の根拠が取れなかった"},
            "answer": judge_answer(case, None),
        }
    deleted = client.delete(f"/api/wiki/sources/{source_id}")
    recheck = client.get(f"/api/knowledge/retrievals/{task['id']}")
    rejected = _context(client, task, hits)
    after = _search(client, case["search_query"])
    after_hits = _hits(after, id_to_key) if after.get("state") == "completed" else []
    if after_hits is None:
        gone = False
        after_hits = []
    else:
        gone = after.get("state") == "completed" and all(
            hit["source_id"] != source_id for hit in after_hits
        )
    stale = False
    if recheck.status_code == 200:
        try:
            stale = bool((recheck.json().get("output") or {}).get("stale"))
        except ValueError:
            stale = False
    ok = deleted.status_code == 200 and stale and rejected.status_code == 409 and gone
    return {
        "case_id": case["case_id"],
        "case_type": "deletion",
        "category": case["category"],
        "difficulty": case["difficulty"],
        "review_status": case["review_status"],
        "execution_mode": "live_text_search",
        "retrieval": {
            "status": "pass" if ok else "fail",
            "reason": "deleted_source_rejected" if ok else "deletion_mismatch",
            "stale": stale,
            "old_context_status": rejected.status_code,
            "source_absent_after_delete": gone,
            "mechanical": True,
        },
        "context": {
            "status": "not_applicable",
            "reason": "削除後は正解根拠を要求しない",
            "mechanical": True,
        },
        "answer": judge_answer(case, None),
        "hits": after_hits,
    }


def _rate(results: list[dict], predicate, label: str, scope: str) -> dict:
    denominator = [
        item for item in results if item["case_type"] == "search" and item.get("answerable_case")
    ]
    numerator = [item for item in denominator if predicate(item)]
    return {
        "label": label,
        "numerator": len(numerator),
        "denominator": len(denominator),
        "rate": None if not denominator else len(numerator) / len(denominator),
        "full_dataset_pass_rate": None,
        "scope": scope,
    }


def _context_rate(results: list[dict]) -> dict:
    answerable = [
        item for item in results if item["case_type"] == "search" and item.get("answerable_case")
    ]
    fetched = [
        item
        for item in answerable
        if item["context"].get("status") in {"pass", "fail", "review_required"}
    ]
    passed = [item for item in fetched if item["context"].get("status") == "pass"]
    return {
        "label": "文脈を取得できた回答可能なケースのうち、必要な事実がそろった件数",
        "numerator": len(passed),
        "denominator": len(fetched),
        "not_fetched": len(answerable) - len(fetched),
        "rate": None if not fetched else len(passed) / len(fetched),
        "full_dataset_pass_rate": None,
        "scope": "未取得の文脈は分母に入れず not_fetched に分ける。この率はデータセット全体の合格率ではない。",
    }


def _counts(cases: list[dict], field: str) -> dict:
    counts: dict[str, int] = {}
    for case in cases:
        counts[case[field]] = counts.get(case[field], 0) + 1
    return counts


def evaluate(data: Path) -> dict:
    cases = load_cases()
    by_id = {case["case_id"]: case for case in cases}
    fingerprint = source_fingerprint()
    inputs = input_fingerprint()
    live = []
    with IsolatedApp(data) as app:
        assert app.client
        sources = import_corpus(app.client)
        id_to_key = {article["id"]: key for key, article in sources.items()}
        status = app.client.get("/api/knowledge/status")
        status.raise_for_status()
        chunk_count = status.json()["chunks"]
        for case in cases:
            if case["case_type"] == "search":
                result = _live_case(app.client, case, id_to_key)
                result["answerable_case"] = case["answerable"]
                live.append(result)
            elif case["case_type"] == "revision":
                result = _revision(app.client, case, id_to_key)
                result["answerable_case"] = False
                live.append(result)
            elif case["case_type"] == "deletion":
                result = _deletion(app.client, case, id_to_key)
                result["answerable_case"] = False
                live.append(result)
            else:
                raise ValueError(case["case_type"])
        provider = app.client.get("/api/evaluation/provider")
        provider.raise_for_status()
        provider_body = provider.json()
    probes = mutation_probes(cases)
    replay = []
    for record in load_replay():
        evidence = [
            passage["text"]
            for passage in by_id[record["case_id"]].get("gold_passages", [])
            if isinstance(passage.get("text"), str)
        ]
        judgment = judge_answer(by_id[record["case_id"]], record["answer"], evidence)
        replay.append(
            {
                "case_id": record["case_id"],
                "execution_mode": "replay",
                "origin": record["origin"],
                "captured_from_live_model": record["captured_from_live_model"],
                "captured_from_live_ocr": record["captured_from_live_ocr"],
                "proves": record["proves"],
                "does_not_prove": record["does_not_prove"],
                "judgment": judgment,
                "merged_into_live_answer_rate": False,
            }
        )
    not_run = [
        {"id": "live_ocr", "status": "not_run", "reason": "Azure OCR には接続していない。"},
        {
            "id": "live_embedding",
            "status": "not_run",
            "reason": "実embeddingの関連度は未実施。疑似vectorは比較に使っていない。",
        },
        {
            "id": "live_answer_model",
            "status": "not_run",
            "reason": "回答モデルには接続していない。",
        },
        {
            "id": "human_approval",
            "status": "not_run",
            "reason": "正解は draft のまま。人の承認は未実施。",
        },
        {
            "id": "representative_corpus",
            "status": "not_run",
            "reason": "共有可能な代表資料での評価は未実施。",
        },
        {"id": "browser", "status": "not_run", "reason": "画面上の出典表示は未確認。"},
        {
            "id": "document_conversion",
            "status": "not_run",
            "reason": "Office と PDF の実変換は未実施。",
        },
    ]
    review_required = [
        item["case_id"]
        for item in live
        if item["retrieval"].get("status") == "review_required"
        or item["context"].get("status") == "review_required"
        or item["answer"].get("status") == "review_required"
    ]
    return {
        "report_version": 1,
        "role": "mechanism_check",
        "quality_passed": False,
        "review_status": "draft",
        "approved_case_count": sum(case["review_status"] == "approved" for case in cases),
        "case_count": len(cases),
        "counts": {
            "category": _counts(cases, "category"),
            "question_style": _counts(cases, "question_style"),
            "difficulty": _counts(cases, "difficulty"),
        },
        "chunk_count": chunk_count,
        "provider": provider_body,
        "external_requests": provider_body["external_requests"],
        **fingerprint,
        **inputs,
        "machine": machine_info(),
        "cases": live,
        "metrics": {
            "evidence_retrieval": _rate(
                live,
                lambda item: item["retrieval"].get("status") == "pass",
                "回答可能な検索ケースのうち、上位5件に正解根拠がそろった件数",
                "未取得も分母に残す。この率はデータセット全体の合格率ではない。",
            ),
            "context_sufficiency": _context_rate(live),
            "live_answer": {
                "label": "実回答",
                "numerator": 0,
                "denominator": 0,
                "rate": None,
                "status": "not_run",
                "full_dataset_pass_rate": None,
            },
            "appropriate_abstention": {
                "label": "適切な保留",
                "status": "not_run",
                "reason": "実回答がないため、保留の適否は未測定。検出試験だけを別に記録する。",
                "full_dataset_pass_rate": None,
            },
        },
        "mutation_probe": probes,
        "replay": replay,
        "not_run": not_run,
        "unevaluated": {
            "draft": len(cases),
            "review_required_case_ids": review_required,
            "not_run": not_run,
        },
        "rollup": rollup([{"status": "executed"}, *not_run]),
        "limits": [
            "正解は人が承認するまで draft。",
            "自然な質問の検索失敗は結果として残し、検索しやすい語へ置き換えない。",
            "保存応答の再生は、既知の検査へ渡ることを見るだけで、現行モデルの精度ではない。",
            "モデル自身の audit だけでは合格にしない。",
            "この成功率をデータセット全体の合格率と呼ばない。",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    import argparse
    import tempfile

    parser = argparse.ArgumentParser(description="Evaluate fictional wiki search and context")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    with tempfile.TemporaryDirectory(prefix="docling-semantic-") as directory:
        report = evaluate(Path(directory))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "output": str(args.output),
                "quality_passed": report["quality_passed"],
                "case_count": report["case_count"],
                "evidence_retrieval": report["metrics"]["evidence_retrieval"],
                "mutation_detection_complete": report["mutation_probe"]["detection_complete"],
                "external_requests": report["external_requests"],
                "rollup": report["rollup"],
            },
            ensure_ascii=False,
        )
    )
    return 0
