"""Registered senses, source occurrences, decisions and page-wide wording checks."""

from __future__ import annotations

import json
import re

import regex

from docling_desk.knowledge.catalog import evidence_body, safe_file, split_frontmatter

from .files import NeedsReview, hash_text, js_json, utf16_length, utf16_offset
from .markdown import restore

VERSION = "registered-terms-v2"
INPUT_CHARS = 32000
CONTEXT_CHARS = 12000
DECISION_CHARS = 1500


def term_key(entry):
    return entry["id"] + "/" + entry["senseId"]


def validate_registry(value, root=None):
    if (
        not isinstance(value, dict)
        or value.get("schemaVersion") != 1
        or not isinstance(value.get("entries"), list)
    ):
        raise NeedsReview("対訳集の形式が不正です。")
    keys, aliases = set(), {}
    for entry in value["entries"]:
        if not isinstance(entry, dict):
            raise NeedsReview("対訳集の項目が不正です。")
        for field in ("id", "senseId", "definition", "domain", "appliesWhen"):
            if (
                not isinstance(entry.get(field), str)
                or not entry[field].strip()
                or utf16_length(entry[field]) > 1000
            ):
                raise NeedsReview("対訳集の定義が不正です。")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", entry["id"]) or not re.fullmatch(
            r"[A-Za-z0-9_.-]+", entry["senseId"]
        ):
            raise NeedsReview("対訳集のIDが不正です。")
        if (
            entry.get("status") not in {"verified", "draft", "deprecated"}
            or type(entry.get("conditional")) is not bool
        ):
            raise NeedsReview("対訳集の状態・条件が不正です。")
        for field in ("acceptedJa", "disallowedJa", "aliases", "sources"):
            if not isinstance(entry.get(field), list):
                raise NeedsReview("対訳集の配列が不正です。")
        if entry.get("preferredJa") is not None and (
            not isinstance(entry["preferredJa"], str) or not entry["preferredJa"].strip()
        ):
            raise NeedsReview("推奨訳が不正です。")
        if any(
            not isinstance(v, str) or not v.strip()
            for field in ("acceptedJa", "disallowedJa")
            for v in entry[field]
        ):
            raise NeedsReview("許容訳・禁止訳が不正です。")
        if entry["status"] == "verified" and (not entry.get("preferredJa") or not entry["sources"]):
            raise NeedsReview("確認済み用語には訳語と出典が必要です。")
        if entry.get("preferredJa") in entry["disallowedJa"] or set(entry["acceptedJa"]) & set(
            entry["disallowedJa"]
        ):
            raise NeedsReview("推奨訳・許容訳・禁止訳が矛盾しています。")
        key = term_key(entry)
        if key in keys:
            raise NeedsReview("対訳集のIDが重複しています。")
        keys.add(key)
        seen = set()
        for alias in [entry.get("source"), *entry["aliases"]]:
            if (
                not isinstance(alias, dict)
                or not isinstance(alias.get("text"), str)
                or not alias["text"].strip()
                or utf16_length(alias["text"]) > 200
                or type(alias.get("caseSensitive")) is not bool
            ):
                raise NeedsReview("対訳集の別名が不正です。")
            identity = (
                alias["caseSensitive"],
                alias["text"] if alias["caseSensitive"] else alias["text"].lower(),
            )
            if identity in seen:
                raise NeedsReview("対訳集の別名が重複しています。")
            seen.add(identity)
            aliases.setdefault(identity, []).append(entry)
        for source in entry["sources"]:
            if (
                not isinstance(source, dict)
                or bool(source.get("url")) == bool(source.get("path"))
                or not isinstance(source.get("locator"), str)
                or not source["locator"].strip()
            ):
                raise NeedsReview("対訳集の出典が不正です。")
            if source.get("url"):
                from urllib.parse import urlsplit

                url = urlsplit(source["url"])
                if url.scheme != "https" or not url.hostname or url.username or url.password:
                    raise NeedsReview("出典URLが不正です。")
            if source.get("path"):
                if not re.fullmatch(r"[a-f0-9]{64}", str(source.get("hash", ""))):
                    raise NeedsReview("出典ハッシュが不正です。")
                if root is not None:
                    raw = safe_file(root, source["path"]).read_bytes().decode("utf-8")
                    body = (
                        evidence_body(split_frontmatter(raw)[0])
                        if source["path"].endswith(".md")
                        else raw
                    )
                    if hash_text(body) != source["hash"]:
                        raise NeedsReview("対訳集の出典が変更されています。")
    for entries in aliases.values():
        if len(entries) > 1 and any(
            e["status"] == "verified" and not e["conditional"] for e in entries
        ):
            raise NeedsReview("多義語には適用条件と意味判断が必要です。")
    return value


def load_registry(root):
    file = root / "manifests/translation-terminology.json"
    return (
        validate_registry(json.loads(file.read_text(encoding="utf-8")), root)
        if file.exists()
        else {"schemaVersion": 1, "entries": []}
    )


def find(entry, text):
    body = re.sub(
        r"```[\s\S]*?```|~~~[\s\S]*?~~~|`[^\n]*?`|https?://[^\s<>]+|(?<=\]\()[^)]*(?=\))",
        lambda m: " " * len(m[0]),
        text,
    )
    found = {}
    for alias in [entry["source"], *entry["aliases"]]:
        pattern = regex.compile(
            r"(?<![\p{L}\p{N}_])" + regex.escape(alias["text"]) + r"(?![\p{L}\p{N}_])",
            flags=0 if alias["caseSensitive"] else regex.I,
        )
        for match in pattern.finditer(body):
            start, end = utf16_offset(text, match.start()), utf16_offset(text, match.end())
            found[(start, end)] = {
                "start": start,
                "end": end,
                "text": text[match.start() : match.end()],
            }
    matches = sorted(found.values(), key=lambda m: (m["start"], -m["end"]))
    return [
        m
        for m in matches
        if not any(
            b != m
            and b["start"] <= m["start"]
            and b["end"] >= m["end"]
            and b["end"] - b["start"] > m["end"] - m["start"]
            for b in matches
        )
    ]


def compact_entry(entry):
    return {
        "key": term_key(entry),
        "source": entry["source"]["text"],
        **{
            k: entry[k]
            for k in (
                "definition",
                "preferredJa",
                "acceptedJa",
                "disallowedJa",
                "domain",
                "appliesWhen",
                "conditional",
                "status",
                "sources",
            )
        },
    }


def candidates_for_text(snapshot, text):
    return [compact_entry(e) for e in snapshot["entries"] if find(e, text)]


def collect_terms(registry, units):
    groups = {}
    for i, unit in enumerate(units):
        if unit["kind"] in {"code", "fence", "code_block", "html", "def", "space", "hr", "literal"}:
            continue
        groups.setdefault(unit.get("group", i), []).append(unit)
    occurrences, matched = [], set()
    for group, parts in groups.items():
        text, spans = "", []
        for unit in parts:
            start = utf16_length(text)
            text += restore(unit, unit["text"])
            spans.append((unit["id"], start, utf16_length(text)))
        hits = {}
        for entry in registry["entries"]:
            if entry["status"] == "deprecated" or (
                entry["domain"] == "metadata" and parts[0]["kind"] != "cell"
            ):
                continue
            for occurrence in find(entry, text):
                key = (occurrence["start"], occurrence["end"])
                hit = hits.setdefault(
                    key,
                    {
                        "id": "",
                        **occurrence,
                        "group": group,
                        "unitIds": [
                            i
                            for i, start, end in spans
                            if start < occurrence["end"] and end > occurrence["start"]
                        ],
                        "entryKeys": [],
                    },
                )
                if term_key(entry) not in hit["entryKeys"]:
                    hit["entryKeys"].append(term_key(entry))
                matched.add(term_key(entry))
        for hit in hits.values():
            hit["entryKeys"] = list(
                dict.fromkeys(
                    hit["entryKeys"]
                    + [
                        key
                        for longer in hits.values()
                        if longer["start"] <= hit["start"] and longer["end"] >= hit["end"]
                        for key in longer["entryKeys"]
                    ]
                )
            )
        for hit in sorted(hits.values(), key=lambda h: (h["start"], -h["end"])):
            hit["id"] = f"term-{len(occurrences)}"
            occurrences.append(hit)
    return {
        "version": VERSION,
        "registryHash": hash_text(js_json(registry)),
        "entries": [e for e in registry["entries"] if term_key(e) in matched],
        "occurrences": occurrences,
    }


def initial_resolution(snapshot):
    decisions = []
    for occurrence in snapshot["occurrences"]:
        entries = [
            e
            for e in snapshot["entries"]
            if term_key(e) in occurrence["entryKeys"] and e["status"] == "verified"
        ]
        if len(entries) == 1 and not entries[0]["conditional"]:
            key = term_key(entries[0])
            decisions.append(
                {
                    "occurrenceId": occurrence["id"],
                    "entryKey": key,
                    "reason": "確認済みの単一の意味",
                    "citations": ["term:" + key],
                }
            )
        elif not entries:
            decisions.append(
                {
                    "occurrenceId": occurrence["id"],
                    "entryKey": None,
                    "reason": "未確認の定義は調査の参考とし、訳語を強制しない",
                    "citations": [],
                }
            )
    return {"registryHash": snapshot["registryHash"], "decisions": decisions}


def term_context(snapshot, resolution, unit_ids):
    occurrences = [o for o in snapshot["occurrences"] if set(o["unitIds"]) & set(unit_ids)]
    keys = {k for o in occurrences for k in o["entryKeys"]}
    context = {
        "version": snapshot["version"],
        "registryHash": snapshot["registryHash"],
        "entries": [compact_entry(e) for e in snapshot["entries"] if term_key(e) in keys],
        "occurrences": occurrences,
        "decisions": [
            d
            for d in (resolution or {}).get("decisions", [])
            if any(o["id"] == d["occurrenceId"] for o in occurrences)
        ],
    }
    return {**context, "inputHash": hash_text(js_json(context))}


def source_context(units, occurrences):
    wanted = {i for o in occurrences for i in o["unitIds"]}
    groups = {o["group"] for o in occurrences}
    selected = set(wanted)
    for group in groups:
        parts = [u for i, u in enumerate(units) if u.get("group", i) == group]
        for i, unit in enumerate(parts):
            if unit["id"] in wanted:
                selected.update(u["id"] for u in parts[max(0, i - 1) : i + 2])
    offsets, result = {}, []
    for i, unit in enumerate(units):
        group = unit.get("group", i)
        text = restore(unit, unit["text"])
        start = offsets.get(group, 0)
        offsets[group] = start + utf16_length(text)
        if unit["id"] in selected:
            result.append(
                {
                    "id": unit["id"],
                    "group": group,
                    "text": text,
                    "start": start,
                    "end": offsets[group],
                }
            )
    return result


def parse_decisions(value, snapshot, occurrences, citation_ids):
    if (
        not isinstance(value, dict)
        or value.get("unresolved") != []
        or not isinstance(value.get("decisions"), list)
        or len(value["decisions"]) != len(occurrences)
    ):
        raise NeedsReview("用語の意味を確定できません。")
    expected = {o["id"]: o for o in occurrences}
    decisions = []
    for decision in value["decisions"]:
        if (
            not isinstance(decision, dict)
            or decision.get("occurrenceId") not in expected
            or not isinstance(decision.get("reason"), str)
            or not decision["reason"].strip()
            or utf16_length(decision["reason"]) > 1000
        ):
            raise NeedsReview("用語の判断・固定IDが不正です。")
        occurrence = expected.pop(decision["occurrenceId"])
        if decision.get("entryKey") not in occurrence["entryKeys"] or not any(
            term_key(e) == decision["entryKey"] and e["status"] == "verified"
            for e in snapshot["entries"]
        ):
            raise NeedsReview("登録されていない用語の意味が選ばれました。")
        citations = decision.get("citations")
        if (
            not isinstance(citations, list)
            or not citations
            or any(not isinstance(c, str) or c not in citation_ids for c in citations)
            or not any(not c.startswith("term:") for c in citations)
        ):
            raise NeedsReview("用語の適用判断には原文または関連本文の出典が必要です。")
        if utf16_length(js_json(decision)) > DECISION_CHARS:
            raise NeedsReview("用語の判断が入力予算を超えています。")
        decisions.append(
            {
                "occurrenceId": decision["occurrenceId"],
                "entryKey": decision["entryKey"],
                "reason": decision["reason"],
                "citations": ["source:" + c if re.fullmatch(r"u\d+", c) else c for c in citations],
            }
        )
    return decisions


def validate_resolution(snapshot, resolution):
    if (
        resolution["registryHash"] != snapshot["registryHash"]
        or len(resolution["decisions"]) != len(snapshot["occurrences"])
        or len({d["occurrenceId"] for d in resolution["decisions"]}) != len(snapshot["occurrences"])
    ):
        raise NeedsReview("登録用語の意味判断が欠落・重複しています。")
    by_id = {o["id"]: o for o in snapshot["occurrences"]}
    for decision in resolution["decisions"]:
        occurrence = by_id.get(decision["occurrenceId"])
        if occurrence is None:
            raise NeedsReview("登録用語の固定IDが不正です。")
        verified = [
            term_key(e)
            for e in snapshot["entries"]
            if term_key(e) in occurrence["entryKeys"] and e["status"] == "verified"
        ]
        if (decision["entryKey"] is None and verified) or (
            decision["entryKey"] is not None and decision["entryKey"] not in verified
        ):
            raise NeedsReview("登録用語の採用IDが不正です。")
        for other in resolution["decisions"]:
            b = by_id[other["occurrenceId"]]
            if (
                decision["entryKey"]
                and other["entryKey"]
                and decision["entryKey"] != other["entryKey"]
                and occurrence["group"] == b["group"]
                and occurrence["start"] < b["end"]
                and occurrence["end"] > b["start"]
                and set(occurrence["entryKeys"]) & set(b["entryKeys"])
            ):
                raise NeedsReview("重なる用語の意味判断が矛盾しています。")


def term_packets(packets, snapshot):
    result = []

    def size(units):
        context = term_context(snapshot, None, [u["id"] for u in units])
        return utf16_length(js_json(context)) + len(context["occurrences"]) * (DECISION_CHARS + 1)

    for packet in packets:
        pending = []
        for unit in packet["units"]:
            if pending and size(pending + [unit]) > CONTEXT_CHARS:
                result.append({"position": len(result), "units": pending})
                pending = []
            pending.append(unit)
            if size(pending) > CONTEXT_CHARS:
                raise NeedsReview("単一の翻訳単位の用語情報が入力予算を超えています。")
        if pending:
            result.append({"position": len(result), "units": pending})
    return result


def term_issues(snapshot, resolution, units, translations):
    entries = {term_key(e): e for e in snapshot["entries"]}
    occurrences = {o["id"]: o for o in snapshot["occurrences"]}
    texts = {t["id"]: t["text"] for t in translations}
    by_id = {u["id"]: u for u in units}
    issues = []
    for decision in resolution["decisions"]:
        entry = entries.get(decision["entryKey"])
        if not entry:
            continue
        occurrence = occurrences[decision["occurrenceId"]]
        for unit_id in occurrence["unitIds"]:
            if unit_id not in texts or unit_id not in by_id:
                continue
            mixed = any(
                d["entryKey"]
                and d["entryKey"] != decision["entryKey"]
                and unit_id in occurrences[d["occurrenceId"]]["unitIds"]
                and decision["entryKey"] in occurrences[d["occurrenceId"]]["entryKeys"]
                for d in resolution["decisions"]
            )
            if any(word in texts[unit_id] for word in entry["disallowedJa"]):
                issues.append(
                    {
                        "id": unit_id,
                        "reason": f"{occurrence['text']}の意味{term_key(entry)}では禁止訳を使いません。適用条件：{entry['appliesWhen']}",
                        "semanticOnly": mixed,
                    }
                )
            if (
                entry["domain"] == "metadata"
                and by_id[unit_id]["text"].strip() == occurrence["text"]
                and texts[unit_id].strip() not in [entry.get("preferredJa"), *entry["acceptedJa"]]
            ):
                issues.append(
                    {
                        "id": unit_id,
                        "reason": f"項目の表記を「{entry['preferredJa']}」に揃えてください",
                        "semanticOnly": False,
                    }
                )
    return issues


def consistency_checks(snapshot, resolution, units, translations):
    selected = [
        u
        for u in units
        if not u["literal"] and any(u["id"] in o["unitIds"] for o in snapshot["occurrences"])
    ]
    examples = {}
    entries = {term_key(e): e for e in snapshot["entries"]}
    texts = {t["id"]: t["text"] for t in translations}
    for decision in resolution["decisions"]:
        entry = entries.get(decision["entryKey"])
        if not entry:
            continue
        occurrence = next(o for o in snapshot["occurrences"] if o["id"] == decision["occurrenceId"])
        for unit in selected:
            if unit["id"] not in occurrence["unitIds"] or unit["id"] not in texts:
                continue
            text = restore(unit, texts[unit["id"]])
            for wording in sorted(
                set([entry.get("preferredJa"), *entry["acceptedJa"]]) - {None, ""},
                key=len,
                reverse=True,
            ):
                at = text.find(wording)
                if at >= 0:
                    examples.setdefault(
                        (decision["entryKey"], wording),
                        {
                            "entryKey": decision["entryKey"],
                            "id": unit["id"],
                            "wording": wording,
                            "excerpt": text[max(0, at - 40) : at + len(wording) + 40],
                        },
                    )

    def packet(parts):
        ids = {u["id"] for u in parts}
        context = term_context(snapshot, resolution, ids)
        keys = {d["entryKey"] for d in context["decisions"]}
        return {
            "units": [
                {
                    "id": u["id"],
                    "text": u["text"],
                    "kind": u["kind"],
                    "protectedValues": {k: v for k, v in u["keep"].items() if k in u["text"]},
                }
                for u in parts
            ],
            "translation": {"translations": [t for t in translations if t["id"] in ids]},
            "registeredTerminology": context,
            "consistencyExamples": [e for e in examples.values() if e["entryKey"] in keys],
        }

    result, parts = [], []
    for unit in selected:
        if parts and utf16_length(js_json(packet(parts + [unit]))) > INPUT_CHARS:
            result.append(packet(parts))
            parts = []
        parts.append(unit)
        if utf16_length(js_json(packet(parts))) > INPUT_CHARS:
            raise NeedsReview("単一の用語表記検証が入力予算を超えています。")
    if parts:
        result.append(packet(parts))
    return result
