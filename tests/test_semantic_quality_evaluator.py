"""Semantic evaluator checks. Live HTTP uses the isolated server and fictional wiki only."""

import copy

import pytest

from docling_desk.evaluation.corpus import load_articles, load_cases, validate_cases
from docling_desk.evaluation.recording import ROOT
from docling_desk.evaluation.semantic import (
    evaluate,
    gold_observation,
    judge_answer,
    judge_context,
    mutation_probes,
)


def test_gold_passages_come_from_authored_sources_and_stay_draft():
    cases = load_cases()
    articles = {article["source_key"]: article["raw"] for article in load_articles()}
    assert len(articles) == 10
    assert len(cases) == 20
    assert {case["category"] for case in cases} >= {
        "number_unit",
        "negation",
        "exception",
        "table",
        "near_evidence",
        "figure",
        "unanswerable",
        "revision",
        "deletion",
    }
    for case in cases:
        assert case["review_status"] == "draft"
        assert case["dataset_version"] == "draft-1"
        assert "chunk_id" not in case
        if case["query_policy"] == "question_as_written":
            assert case["search_query"] == case["question"]
        for passage in case["gold_passages"]:
            assert passage["text"] in articles[passage["source_key"]]
    current = (ROOT / "qa/semantic-quality/corpus/seasonal-quota.md").read_text(encoding="utf-8")
    previous = (ROOT / "qa/semantic-quality/corpus/previous/seasonal-quota-v1.md").read_text(
        encoding="utf-8"
    )
    assert "季節枠の搬出上限は100箱です。" in current
    assert "季節枠の搬出上限は120箱です。" not in current
    assert "季節枠の搬出上限は120箱です。" in previous
    assert "積雪" not in "\n".join(articles.values())
    probes = mutation_probes(cases)
    assert probes["detected"] == 5
    assert probes["detection_complete"] is True
    assert probes["paraphrase"]["review_required"] is True
    assert probes["paraphrase"]["auto_passed"] is False
    assert all(item["model_audit"] == "approved" for item in probes["results"])


def test_gold_must_be_in_the_authored_body_and_negation_survives_paraphrase():
    cases = load_cases()
    broken = copy.deepcopy(cases)
    broken[0]["gold_passages"][0]["text"] = "抽出器が返した架空の文"
    with pytest.raises(ValueError, match="原資料の本文にありません"):
        validate_cases(broken)
    case = next(item for item in cases if item["case_id"] == "carry-lexical-001")
    text = "搬出上限は120箱です。雨天時は搬出禁止です。"
    unknown = judge_context(case, text, [{"source_key": None}])
    assert unknown["status"] == "fail"
    assert unknown["reason"] == "unknown_source"
    observation = gold_observation(case)
    observation["answer"]["claims"][0]["text"] = "雨の日は出せません。雨天時は搬出可能です。"
    observation["answer"]["claims"][0]["paraphrase"] = True
    observation["answer"]["model_audit"] = "approved"
    judgment = judge_answer(case, observation["answer"], [text])
    assert judgment["status"] == "fail"
    assert "negation_reversed" in judgment["reasons"]
    assert judgment["model_audit_ignored"] is True
    exact = gold_observation(case)
    assert judge_answer(case, exact["answer"], [exact["context_text"]])["status"] == "pass"
    appended = gold_observation(case)
    appended["answer"]["claims"][0]["text"] += "搬出上限は102箱です。"
    appended_judgment = judge_answer(case, appended["answer"], [appended["context_text"]])
    assert appended_judgment["status"] == "fail"
    assert "numeric_mismatch" in appended_judgment["reasons"]
    extra = gold_observation(case)
    extra["answer"]["claims"].append(
        {
            "text": "資料は永久に保存されます。",
            "citation_source_keys": ["fictional-carry-rule-v1"],
            "cited_text": extra["context_text"],
            "paraphrase": False,
        }
    )
    extra_judgment = judge_answer(case, extra["answer"], [extra["context_text"]])
    assert extra_judgment["status"] == "review_required"
    assert "unsupported_addition" in extra_judgment["reasons"]
    other_subject = gold_observation(case)
    other_subject["answer"]["claims"][0]["text"] += "西倉庫の搬出上限は50箱です。"
    other_judgment = judge_answer(case, other_subject["answer"], [other_subject["context_text"]])
    assert other_judgment["status"] == "review_required"
    assert "numeric_mismatch" not in (other_judgment.get("reasons") or [])


def test_live_text_search_context_revision_and_deletion(tmp_path):
    report = evaluate(tmp_path)
    by_id = {item["case_id"]: item for item in report["cases"]}
    carry = by_id["carry-lexical-001"]
    assert carry["retrieval"]["status"] == "pass"
    assert carry["context"]["status"] == "pass"
    assert carry["answer"]["status"] == "not_run"
    natural = by_id["carry-natural-002"]
    assert natural["retrieval"]["status"] == "fail"
    assert natural["query_rewritten"] is False
    assert natural["search_query"] == "雨天時に搬出できますか。通常時の上限も教えてください。"
    assert natural["context"]["status"] == "not_run"
    assert by_id["rain-lexical-004"]["context"]["status"] == "review_required"
    evidence = report["metrics"]["evidence_retrieval"]
    context = report["metrics"]["context_sufficiency"]
    assert evidence["numerator"] == 11
    assert evidence["denominator"] == 16
    assert context["numerator"] == 10
    assert context["denominator"] == 11
    assert context["not_fetched"] == 5
    snow = by_id["snow-unanswerable-015"]
    assert snow["retrieval"]["status"] == "not_applicable"
    assert snow["answer"]["status"] == "not_run"
    assert by_id["seasonal-revision-019"]["retrieval"]["status"] == "pass"
    assert by_id["temporary-deletion-020"]["retrieval"]["status"] == "pass"
    assert report["quality_passed"] is False
    assert report["approved_case_count"] == 0
    assert report["external_requests"] == 0
    assert report["provider"]["outbound_connect_attempts"] == 0
    assert report["provider"]["provider_kind"] == "synthetic"
    assert report["metrics"]["evidence_retrieval"]["full_dataset_pass_rate"] is None
    assert report["metrics"]["evidence_retrieval"]["denominator"] > 0
    assert report["metrics"]["live_answer"]["status"] == "not_run"
    assert report["metrics"]["live_answer"]["rate"] is None
    assert report["replay"][0]["execution_mode"] == "replay"
    assert report["replay"][0]["captured_from_live_model"] is False
    assert report["replay"][0]["merged_into_live_answer_rate"] is False
    assert report["rollup"]["passed"] == 0
    assert report["rollup"]["not_run_counted_as_passed"] is False
    assert report["chunk_count"] > 0
    assert any(item["id"] == "live_ocr" for item in report["not_run"])
