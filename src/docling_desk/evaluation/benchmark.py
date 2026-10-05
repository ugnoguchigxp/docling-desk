"""User-path HTTP benchmark. search_bench.py stays as the in-process search-core measurement."""

from __future__ import annotations

import json
from pathlib import Path

import httpx

from docling_desk.documents.conversion import Job, save_job
from docling_desk.evaluation.corpus import NAMESPACE, SCENARIOS, input_fingerprint, load_articles
from docling_desk.evaluation.monitor import RssSampler
from docling_desk.evaluation.path import execute_user_path
from docling_desk.evaluation.recording import (
    machine_info,
    production_allowed,
    rollup,
    source_fingerprint,
    summarize_samples,
)
from docling_desk.evaluation.session import IsolatedApp

DOCUMENT_JOB = "e1" * 16
DOCUMENT_TEXT = "青葉台保管票の照合番号は441です。"


def load_scenarios() -> dict:
    return json.loads(SCENARIOS.read_text(encoding="utf-8"))


def import_corpus(client: httpx.Client, namespace: str = NAMESPACE) -> dict[str, dict]:
    articles = load_articles()
    response = client.post(
        "/api/wiki/import",
        data={"namespace": namespace},
        files=[
            ("files", (article["path"], article["raw"].encode(), "text/markdown"))
            for article in articles
        ],
    )
    if response.status_code != 201:
        raise RuntimeError(response.text)
    catalog = client.get("/api/wiki/catalog")
    catalog.raise_for_status()
    by_key = {}
    for article in catalog.json()["articles"]:
        key = article.get("external_key")
        if key:
            by_key[key] = article
    missing = [article["source_key"] for article in articles if article["source_key"] not in by_key]
    if missing:
        raise RuntimeError(f"登録後に source_key が見つかりません: {missing}")
    return by_key


def write_search_artifact(data: Path) -> str:
    folder = data / DOCUMENT_JOB
    folder.mkdir()
    save_job(
        folder,
        Job(
            id=DOCUMENT_JOB,
            filename="青葉台保管票.pdf",
            state="success",
            pages=1,
            synthetic=True,
        ),
    )
    (folder / "original.pdf").write_bytes(b"%PDF-1.4 synthetic evaluation artifact")
    parent = {
        "id": f"{DOCUMENT_JOB}:pdf:1",
        "text": DOCUMENT_TEXT,
        "pages": [1],
        "refs": ["#/texts/1"],
        "headings": ["保管"],
        "unit": "ページ 1",
    }
    child = {
        **parent,
        "id": parent["id"] + ":search:0",
        "parent_id": parent["id"],
        "text": DOCUMENT_TEXT,
    }
    (folder / "rag.jsonl").write_text(json.dumps(parent, ensure_ascii=False), encoding="utf-8")
    (folder / "rag-index.jsonl").write_text(json.dumps(child, ensure_ascii=False), encoding="utf-8")
    return "doc-" + DOCUMENT_JOB


def _run_scenario(
    client: httpx.Client, scenario: dict, sources: dict[str, dict], repeats: int | None
) -> dict:
    measured = repeats if repeats is not None else scenario["repeats"]
    warmup = scenario.get("warmup", 0)
    if scenario["expected_source_key"] == "synthetic-document-artifact":
        expected_id = "doc-" + DOCUMENT_JOB
    elif scenario.get("expected_source_key"):
        expected_id = sources[scenario["expected_source_key"]]["id"]
    else:
        expected_id = None
    samples = []
    for index in range(warmup + measured):
        sample = execute_user_path(
            client,
            query=scenario["query"],
            mode=scenario["mode"],
            kind=scenario["kind"],
            namespace=scenario.get("namespace"),
            limit=scenario["limit"],
            budget=scenario["context_budget"],
            expect=scenario["expect"],
            expected_source_id=expected_id,
            expected_passage=scenario.get("expected_passage"),
            source_endpoint=scenario["source_endpoint"],
        )
        sample["role"] = "warmup" if index < warmup else "measured"
        sample["index"] = index
        samples.append(sample)
    measured_samples = [sample for sample in samples if sample["role"] == "measured"]
    summary = summarize_samples(measured_samples)
    summary["production_pass"] = production_allowed(scenario.get("scale_role", ""), "proposed")
    summary["scale_role"] = scenario.get("scale_role")
    return {
        "id": scenario["id"],
        "status": "executed",
        "scale_role": scenario.get("scale_role"),
        "condition": scenario.get("condition"),
        "production_pass": False,
        "mode": scenario["mode"],
        "kind": scenario["kind"],
        "expect": scenario["expect"],
        "includes_document_sync": scenario.get("includes_document_sync", False),
        "conversion": False,
        "proves": "loopback HTTP path and aggregation"
        if scenario["id"] != "small_mixed_artifact"
        else "document sync plus search on a prebuilt artifact",
        "does_not_prove": [
            "formal hardware performance",
            "browser wait",
            "conversion performance",
            "semantic relevance",
        ],
        "warmup_samples": [sample for sample in samples if sample["role"] == "warmup"],
        "samples": measured_samples,
        "summary": summary,
    }


def run_benchmark(
    data: Path,
    *,
    repeats: int | None = None,
    scenario_ids: set[str] | None = None,
) -> dict:
    if repeats is not None and repeats < 1:
        raise ValueError("測定回数は1以上です。")
    document = load_scenarios()
    fingerprint = source_fingerprint()
    inputs = input_fingerprint()
    machine = machine_info()
    results = []
    not_run = []
    with IsolatedApp(data) as app:
        assert app.client and app.process
        sampler = RssSampler(app.process.pid)
        sampler.mark("prepare")
        sampler.start()
        sources = import_corpus(app.client)
        prepare_status = app.client.get("/api/knowledge/status")
        prepare_status.raise_for_status()
        chunks_after_wiki = prepare_status.json()["chunks"]
        sampler.mark("measure")
        artifact_written = False
        executed_ordinal = 0
        for scenario in document["scenarios"]:
            if not scenario.get("stage1_execute"):
                not_run.append(
                    {"id": scenario["id"], "status": "not_run", "reason": scenario["reason"]}
                )
                continue
            if scenario_ids and scenario["id"] not in scenario_ids:
                results.append(
                    {
                        "id": scenario["id"],
                        "status": "not_selected",
                        "reason": "この実行では選んでいません。",
                    }
                )
                continue
            if scenario["id"] == "small_mixed_artifact" and not artifact_written:
                write_search_artifact(data)
                artifact_written = True
                app.client.get("/api/knowledge/status").raise_for_status()
            executed_ordinal += 1
            scenario_result = _run_scenario(app.client, scenario, sources, repeats)
            scenario_result["ordinal"] = executed_ordinal
            scenario_result["same_process_as_previous"] = executed_ordinal > 1
            if scenario_result["warmup_samples"]:
                scenario_result["warmup_samples"][0]["condition"] = (
                    "post_start" if executed_ordinal == 1 else "warmup_in_shared_process"
                )
            results.append(scenario_result)
        after = app.client.get("/api/knowledge/status")
        after.raise_for_status()
        provider = app.client.get("/api/evaluation/provider")
        provider.raise_for_status()
        rss = sampler.stop()
        provider_body = provider.json()
    items = [*results, *not_run]
    return {
        "report_version": 1,
        "role": "mechanism_check",
        "transport": "loopback_http",
        "testclient_used_for_timings": False,
        "production_pass": False,
        "budget_status": "proposed",
        "provider": provider_body,
        "external_requests": provider_body["external_requests"],
        "semantic_quality_claimed": False,
        "chunk_count_after_wiki_import": chunks_after_wiki,
        "chunk_count_after_run": after.json()["chunks"],
        "chunk_count_is_measured": True,
        "dimensions": provider_body["dimensions"],
        "dimension_series": "synthetic provider is present; text search does not claim this dimension's semantic quality",
        **fingerprint,
        **inputs,
        "machine": machine,
        "rss": rss,
        "scenarios": results,
        "not_run": not_run,
        "rollup": rollup(items),
        "limits": [
            "小規模CI検査の成功は本番性能の合格ではない。",
            "既存の性能予算は提案値のまま。",
            "HTTPの時間に画面描画は含まれない。",
            "検索成果物の投入は文書変換の性能でも精度でもない。",
            "疑似providerのvectorは意味検索の精度ではない。",
            "プロセス起動後の暖機を、OSキャッシュまで制御した冷状態とは呼ばない。",
            "同じプロセスで続けたシナリオは、最初の1件以外すでに暖まっている。",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    import argparse
    import tempfile

    parser = argparse.ArgumentParser(description="Measure the loopback user search path")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int)
    parser.add_argument("--scenario", action="append", dest="scenarios")
    args = parser.parse_args(argv)
    with tempfile.TemporaryDirectory(prefix="docling-user-path-") as directory:
        report = run_benchmark(
            Path(directory),
            repeats=args.repeats,
            scenario_ids=set(args.scenarios) if args.scenarios else None,
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "output": str(args.output),
                "production_pass": report["production_pass"],
                "chunk_count_after_wiki_import": report["chunk_count_after_wiki_import"],
                "external_requests": report["external_requests"],
                "rollup": report["rollup"],
            },
            ensure_ascii=False,
        )
    )
    return 0
