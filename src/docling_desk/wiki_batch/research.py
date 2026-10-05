"""Checkpoint every bounded reading/search/interpretation/term-decision step."""

from __future__ import annotations

from . import prompts
from .files import NeedsReview, Stopped, hash_text, js_json, utf16_length, write_json
from .markdown import parse_json
from .repository import chunks_for
from .snapshot import LIMITS, RESEARCH_VERSION
from .terminology import (
    INPUT_CHARS,
    VERSION,
    candidates_for_text,
    initial_resolution,
    parse_decisions,
    source_context,
    term_context,
    term_key,
    validate_resolution,
)


def bounded_text(value, maximum):
    if not isinstance(value, str) or not value.strip() or utf16_length(value) > maximum:
        raise NeedsReview("調査応答の本文が不正です。")
    return value.strip()


def analysis(value, maximum=4):
    if (
        not isinstance(value, dict)
        or not isinstance(value.get("queries"), list)
        or not 1 <= len(value["queries"]) <= maximum
    ):
        raise NeedsReview("調査キーワードの件数が不正です。")
    queries, seen = [], set()
    for query in value["queries"]:
        if not isinstance(query, dict):
            raise NeedsReview("調査キーワードの形式が不正です。")
        text = bounded_text(query.get("query"), 120)
        reason = bounded_text(query.get("reason"), 500)
        if text not in seen:
            seen.add(text)
            queries.append({"query": text, "reason": reason})
    return {"summary": bounded_text(value.get("summary"), 1500), "queries": queries}


def reading(value, documents):
    if (
        not isinstance(value, dict)
        or not isinstance(value.get("terminology"), list)
        or len(value["terminology"]) > 12
        or not isinstance(value.get("cautions"), list)
        or len(value["cautions"]) > 8
    ):
        raise NeedsReview("関連本文の読解形式が不正です。")
    ids, ambiguous = {}, set()
    for document in documents:
        for key in ("referenceId", "id", "key", "path"):
            identity = document.get(key)
            if identity is None:
                continue
            if identity in ids and ids[identity] != document["referenceId"]:
                ambiguous.add(identity)
            ids[identity] = document["referenceId"]
    for identity in ambiguous:
        ids.pop(identity, None)

    def citations(row):
        values = row.get("citations")
        if (
            not isinstance(values, list)
            or not values
            or any(not isinstance(v, str) or v not in ids for v in values)
        ):
            raise NeedsReview("読解結果の出典が参照資料と一致しません。")
        return list(dict.fromkeys(ids[v] for v in values))

    terms, cautions = [], []
    for row in value["terminology"]:
        if not isinstance(row, dict):
            raise NeedsReview("読解結果の用語が不正です。")
        terms.append(
            {
                "source": bounded_text(row.get("source"), 200),
                "target": bounded_text(row.get("target"), 300),
                "reason": bounded_text(row.get("reason"), 700),
                "citations": citations(row),
            }
        )
    for row in value["cautions"]:
        if not isinstance(row, dict):
            raise NeedsReview("読解結果の注意点が不正です。")
        cautions.append({"text": bounded_text(row.get("text"), 900), "citations": citations(row)})
    return {"terminology": terms, "cautions": cautions}


def assessment(value):
    if (
        not isinstance(value, dict)
        or type(value.get("sufficient")) is not bool
        or not isinstance(value.get("queries"), list)
        or len(value["queries"]) > 3
        or not isinstance(value.get("unresolved"), list)
        or len(value["unresolved"]) > 6
    ):
        raise NeedsReview("追加調査の判断形式が不正です。")
    queries = [
        {"query": bounded_text(q.get("query"), 120), "reason": bounded_text(q.get("reason"), 500)}
        for q in value["queries"]
        if isinstance(q, dict)
    ]
    if len(queries) != len(value["queries"]):
        raise NeedsReview("追加検索語が不正です。")
    unresolved = [bounded_text(v, 500) for v in value["unresolved"]]
    if (value["sufficient"] and (queries or unresolved)) or (
        not value["sufficient"] and not unresolved
    ):
        raise NeedsReview("調査の完了判断と未解消事項が矛盾しています。")
    return {
        "sufficient": value["sufficient"],
        "reason": bounded_text(value.get("reason"), 1000),
        "queries": queries,
        "unresolved": unresolved,
    }


def research_for(store, client, runtime, repository, job, snapshot):
    job_id, terms = job["id"], snapshot.get("terminology")
    if (
        snapshot.get("researchVersion") != RESEARCH_VERSION
        or not isinstance(snapshot.get("sourceText"), str)
        or hash_text(snapshot["sourceText"]) != snapshot["sourceBodyHash"]
        or not terms
        or terms.get("version") != VERSION
    ):
        raise NeedsReview("旧方式または不正な調査入力です。")
    file = store.root / "data/translation/jobs" / job_id / "research.json"

    def check():
        if runtime.stopping() or store.setting("paused") == "1":
            raise Stopped("調査を停止しました。")

    def checkpoint(step, operation):
        check()
        saved = store.checkpoint(job_id, step)
        if saved is not None:
            return saved
        store.set("worker_stage", step)
        stage = step.split("/")[0]
        label = {
            "analyze": "原文読解",
            "plan": "検索方針",
            "search": "Wiki検索",
            "documents": "関連本文を選択",
            "read": "関連本文を読解",
            "synthesis": "読解結果を統合",
            "assess": "追加調査を判断",
            "resolve": "用語の意味を判断",
            "round": "追加調査",
        }.get(stage, "調査")
        runtime.log(f"{job['page_key']}：{label}")
        result = operation()
        store.save(job_id, step, result)
        return result

    def ask(instructions, value, step):
        if "registeredTerminology" not in value:
            value = {**value, "registeredTerminology": candidates_for_text(terms, js_json(value))}
        text = js_json(value)
        if utf16_length(text) > INPUT_CHARS:
            raise NeedsReview("調査入力が予算を超えています。")
        previous = store.checkpoint(job_id, "input/" + step)
        digest = hash_text(text)
        if previous and previous["hash"] != digest:
            raise NeedsReview("保存した調査要求と移行後の入力が一致しません。")
        store.save(job_id, "input/" + step, {"hash": digest, "chars": utf16_length(text)})
        return parse_json(
            client.request(
                "draft",
                {
                    "instructions": instructions,
                    "input": text,
                    "maxOutputTokens": 4096 if instructions == prompts.RESOLVE_TERMS else 2048,
                },
                f"{job_id}/research/{step}",
                job_id,
            )
        )

    def finish(result):
        store.save(job_id, "complete", result)
        write_json(file, result)
        if result["stopReason"] != "sufficient":
            raise NeedsReview(
                "追加調査を終了しました（"
                + result["stopReason"]
                + "）："
                + "; ".join(result["unresolved"])
            )
        validate_resolution(terms, result["registeredTerminology"])
        return result

    cached = store.checkpoint(job_id, "complete")
    if cached is not None:
        if cached.get("sourceHash") != snapshot["sourceBodyHash"]:
            raise NeedsReview("保存済み調査の原文版が一致しません。")
        return finish(cached)
    resolution = initial_resolution(terms)
    pending = [
        o
        for o in terms["occurrences"]
        if not any(d["occurrenceId"] == o["id"] for d in resolution["decisions"])
    ]

    def save_terms():
        store.save(job_id, "registered-terms", resolution)
        write_json(
            file.with_name("terminology.json"),
            {
                **terms,
                "resolution": resolution,
                "unresolved": [
                    o
                    for o in terms["occurrences"]
                    if not any(d["occurrenceId"] == o["id"] for d in resolution["decisions"])
                ],
            },
        )

    # Offline sync must succeed before any model request.
    repository.sync()
    save_terms()
    chunks = []
    for section in chunks_for(snapshot["sourceText"], snapshot["page"]["title_original"], 4000):
        if chunks and utf16_length(chunks[-1]["content"] + "\n\n" + section["content"]) <= 4000:
            chunks[-1]["content"] += "\n\n" + section["content"]
        else:
            chunks.append(section)
    if not chunks:
        chunks = [{"content": snapshot["page"]["title_original"]}]
    analyses = []
    for i, chunk in enumerate(chunks):
        value = {"title": snapshot["page"]["title_original"]}
        if "category" in snapshot["page"]:
            value["category"] = snapshot["page"]["category"]
        value.update(
            {
                "section": i + 1,
                "total": len(chunks),
                "text": chunk["content"],
                "registeredTerminology": candidates_for_text(terms, chunk["content"]),
            }
        )
        analyses.append(
            checkpoint(
                f"analyze/{i}",
                lambda value=value, i=i: analysis(ask(prompts.ANALYZE, value, f"analyze/{i}")),
            )
        )
    plan = analyses[0]
    if len(analyses) > 1:
        for i in range(0, len(analyses), 3):
            value = {"title": snapshot["page"]["title_original"]}
            if i:
                value["previous"] = plan
            value["readings"] = analyses[i : i + 3]
            plan = checkpoint(
                f"plan/{i}",
                lambda value=value, i=i: analysis(ask(prompts.PLAN_SEARCH, value, f"plan/{i}"), 8),
            )
    mandatory = {}
    for occurrence in pending:
        mandatory.setdefault(
            occurrence["text"],
            {
                "query": occurrence["text"],
                "reason": "登録用語の適用条件を確認：" + ",".join(occurrence["entryKeys"]),
            },
        )
    mandatory = list(mandatory.values())[:3]
    plan = {
        **plan,
        "queries": (
            mandatory
            + [q for q in plan["queries"] if q["query"] not in {m["query"] for m in mandatory}]
        )[:8],
    }
    searches, documents, readings, rounds = [], [], [], []
    queries, interpretation = plan["queries"], {"terminology": [], "cautions": []}
    stop_reason, unresolved = "round_limit", []
    for round_number in range(LIMITS["additionalRounds"] + 1):
        prefix = f"round/{round_number}/" if round_number else ""
        round_searches = checkpoint(
            prefix + "search",
            lambda: [
                {
                    **q,
                    "expandedTerms": (
                        result := repository.search(q["query"], snapshot["page"]["key"])
                    )["expandedTerms"],
                    "indexedAt": result["indexedAt"],
                    "hits": result["results"],
                }
                for q in queries
            ],
        )
        searches.extend(round_searches)

        def choose():
            chosen, visited = [], set()
            candidates = round_searches + [s for s in searches if s not in round_searches]
            for position in range(4):
                for search in candidates:
                    if len(search["hits"]) <= position:
                        continue
                    hit = search["hits"][position]
                    if (
                        hit["key"] in visited
                        or len(chosen) >= 2
                        or len(documents) + len(chosen) >= 6
                    ):
                        continue
                    visited.add(hit["key"])
                    matching = [
                        s for s in candidates if any(h["key"] == hit["key"] for h in s["hits"])
                    ]
                    document = repository.related_document(
                        [h for s in matching for h in s["hits"] if h["key"] == hit["key"]],
                        [s["query"] for s in matching],
                        f"ref:{len(documents) + len(chosen) + 1}",
                        documents,
                    )
                    if document:
                        chosen.append(document)
            return chosen

        chosen = checkpoint(prefix + "documents", choose)
        record = {
            "round": round_number,
            "queries": queries,
            "newDocuments": [d["referenceId"] for d in chosen],
        }
        rounds.append(record)
        if round_number and not chosen:
            stop_reason = "no_new_evidence" if queries else "no_new_queries"
            break
        notes = []
        for document in chosen:
            i = len(documents)
            documents.append(document)
            note = checkpoint(
                f"read/{i}",
                lambda document=document, i=i: reading(
                    ask(
                        prompts.READ_RELATED,
                        {
                            "source": plan,
                            "document": document,
                            "registeredTerminology": candidates_for_text(
                                terms, document["content"]
                            ),
                        },
                        f"read/{i}",
                    ),
                    [document],
                ),
            )
            notes.append(note)
            readings.append(note)
        if notes:
            if not round_number and len(notes) == 1:
                interpretation = notes[0]
            else:
                step = f"synthesis/{round_number}" if round_number else "synthesis"
                value = {"source": plan}
                if round_number:
                    value["previous"] = interpretation
                value.update(
                    {
                        "readings": notes,
                        "documents": [
                            {
                                k: d[k]
                                for k in ("referenceId", "key", "title", "queries", "truncated")
                            }
                            for d in documents
                        ],
                    }
                )
                interpretation = checkpoint(
                    step,
                    lambda value=value, step=step: reading(
                        ask(prompts.SYNTHESIZE_RESEARCH, value, step), documents
                    ),
                )
        decision = checkpoint(
            f"assess/{round_number}",
            lambda: assessment(
                ask(
                    prompts.ASSESS_RESEARCH,
                    {
                        "source": plan,
                        "interpretation": interpretation,
                        "remainingRounds": 2 - round_number,
                        "remainingDocuments": 6 - len(documents),
                        "limits": LIMITS,
                        "documents": [
                            {k: d[k] for k in ("referenceId", "key", "title", "truncated")}
                            for d in documents
                        ],
                        "searched": [
                            {
                                "query": s["query"],
                                "reason": s["reason"],
                                "hits": [{"key": h["key"], "title": h["title"]} for h in s["hits"]],
                            }
                            for s in searches
                        ],
                    },
                    f"assess/{round_number}",
                )
            ),
        )
        record["assessment"] = decision
        unresolved = decision["unresolved"]
        if decision["sufficient"]:
            stop_reason = "sufficient"
            break
        if round_number == 2 or len(documents) >= 6:
            stop_reason = "round_limit" if round_number == 2 else "document_limit"
            break
        searched = {s["query"] for s in searches}
        queries = [q for q in decision["queries"] if q["query"] not in searched]
    if stop_reason == "sufficient":
        citations = {
            "source",
            *[d["referenceId"] for d in documents],
            *["term:" + term_key(e) for e in terms["entries"]],
        }
        offset = 0
        while offset < len(pending):
            count = min(8, len(pending) - offset)
            while count:
                occurrences = pending[offset : offset + count]
                original = source_context(snapshot["units"], occurrences)
                context = term_context(
                    terms, resolution, [i for o in occurrences for i in o["unitIds"]]
                )
                registered = {
                    "version": context["version"],
                    "registryHash": context["registryHash"],
                    "entries": context["entries"],
                    "occurrences": occurrences,
                    "resolvedOccurrences": [
                        o
                        for o in context["occurrences"]
                        if any(d["occurrenceId"] == o["id"] for d in context["decisions"])
                    ],
                    "decisions": context["decisions"],
                }
                registered["inputHash"] = hash_text(js_json(registered))
                value = {
                    "title": snapshot["page"]["title_original"],
                    "source": plan,
                    "original": original,
                    "allowedCitationIds": sorted(citations)
                    + ["source:" + u["id"] for u in original],
                    "registeredTerminology": registered,
                    "interpretation": interpretation,
                    "documents": [
                        {
                            "referenceId": d["referenceId"],
                            "title": d["title"],
                            "content": d["content"][:1200],
                            "truncated": d["truncated"] or len(d["content"]) > 1200,
                        }
                        for d in documents
                    ],
                }
                if utf16_length(js_json(value)) <= INPUT_CHARS:
                    break
                count -= 1
            if not count:
                raise NeedsReview("用語の意味判断が入力予算を超えています。")
            step = f"resolve/{offset}"
            ids = {u["id"] for u in original}
            decisions = checkpoint(
                step,
                lambda value=value, step=step, occurrences=occurrences, ids=ids: parse_decisions(
                    ask(prompts.RESOLVE_TERMS, value, step),
                    terms,
                    occurrences,
                    citations | ids | {"source:" + i for i in ids},
                ),
            )
            resolution["decisions"].extend(decisions)
            offset += count
            save_terms()
        try:
            validate_resolution(terms, resolution)
        except NeedsReview as exc:
            store.save(job_id, "term-resolution-error", {"reason": str(exc)})
            raise
    save_terms()
    return finish(
        {
            "version": RESEARCH_VERSION,
            "sourceHash": snapshot["sourceBodyHash"],
            "analyses": analyses,
            "plan": plan,
            "searches": searches,
            "documents": documents,
            "readings": readings,
            "interpretation": interpretation,
            "limits": LIMITS,
            "rounds": rounds,
            "stopReason": stop_reason,
            "unresolved": unresolved,
            "registeredTerminology": resolution,
        }
    )


def translation_context(research, source_text):
    terminology = [
        t for t in research["interpretation"]["terminology"] if t["source"] in source_text
    ]
    cautions = research["interpretation"]["cautions"]
    cited = {c for item in [*terminology, *cautions] for c in item["citations"]}
    references = [d for d in research["documents"] if d["referenceId"] in cited] or research[
        "documents"
    ][:2]
    return {
        "references": [
            {
                **d,
                "content": d["content"][:800],
                "truncated": d["truncated"] or len(d["content"]) > 800,
            }
            for d in references
        ],
        "research": {
            "sourceUnderstanding": research["plan"],
            "terminology": terminology,
            "cautions": cautions,
            "noResults": [s["query"] for s in research["searches"] if not s["hits"]],
            "stopReason": research["stopReason"],
        },
    }
