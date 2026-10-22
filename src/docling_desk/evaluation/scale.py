"""Measure validated canonical Wiki search through real loopback HTTP; no cloud calls."""

from __future__ import annotations

import argparse
import json
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from docling_desk.evaluation.monitor import RssSampler
from docling_desk.evaluation.recording import machine_info, percentile, source_fingerprint
from docling_desk.evaluation.search_bench import populate
from docling_desk.evaluation.session import IsolatedApp


def run(
    data: Path,
    documents: int = 100,
    chunks_each: int = 100,
    repeats: int = 30,
    concurrency: int = 8,
) -> dict:
    if min(documents, chunks_each, repeats, concurrency) < 1 or concurrency > 8:
        raise ValueError("件数・試行数は1以上、同時要求は1〜8にしてください。")
    if data.exists() and any(data.iterdir()):
        raise ValueError("測定には空の保存先が必要です。")
    populate(data, documents, chunks_each, 1024)
    with IsolatedApp(data) as app:
        assert app.client and app.process
        client = app.client
        status = client.get("/api/knowledge/status").raise_for_status().json()
        if status["chunks"] != documents * chunks_each:
            raise ValueError("HTTP起動後の実チャンク数が測定規模と一致しません。")
        monitor = RssSampler(app.process.pid)
        monitor.start()
        monitor.mark("measure")

        def sample(barrier=None):
            if barrier:
                barrier.wait(timeout=30)
            started = time.perf_counter()
            response = client.post(
                "/api/knowledge/search",
                json={
                    "query": "alpha",
                    "mode": "text",
                    "kind": "wiki",
                    "namespace": "bench",
                    "limit": 20,
                },
            )
            response.raise_for_status()
            result = response.json()
            search_seconds = time.perf_counter() - started
            hits = result["output"]["results"]
            if len(hits) != min(20, documents * chunks_each) or any(
                not hit["source_id"].startswith("wiki-bench-") or "alpha" not in hit["text"]
                for hit in hits
            ):
                raise ValueError("検索結果の件数・出典・本文が不正です。")
            context_started = time.perf_counter()
            context = client.post(
                "/api/knowledge/context",
                json={
                    "retrieval_id": result["id"],
                    "chunk_ids": [hit["chunk_id"] for hit in hits],
                    "budget": 4000,
                },
            )
            context.raise_for_status()
            if "alpha" not in context.text:
                raise ValueError("取得した親文脈に根拠がありません。")
            return {
                "search_seconds": search_seconds,
                "context_seconds": time.perf_counter() - context_started,
                "total_seconds": time.perf_counter() - started,
            }

        try:
            sample()  # warm up before collecting measured samples
            sequential = [sample() for _ in range(repeats)]
            parallel = []
            with ThreadPoolExecutor(max_workers=concurrency) as executor:
                for _ in range(repeats):
                    barrier = threading.Barrier(concurrency)
                    futures = [executor.submit(sample, barrier) for _ in range(concurrency)]
                    parallel.extend(future.result(timeout=60) for future in futures)
            # A normal request must still work after the concurrent phase.
            recovery = sample()
            provider = client.get("/api/evaluation/provider").raise_for_status().json()
        finally:
            rss = monitor.stop()

    def summary(samples, field):
        values = [s[field] for s in samples]
        return {
            "samples": len(values),
            "p50": percentile(values, 0.5),
            "p95": percentile(values, 0.95),
            "max": max(values),
        }

    search = summary(sequential, "search_seconds")
    context = summary(sequential, "context_seconds")
    concurrent = summary(parallel, "search_seconds")
    return {
        "role": "measured_local_wiki_http_not_full_product_certification",
        "machine": machine_info(),
        "source": source_fingerprint(),
        "documents": documents,
        "chunks": status["chunks"],
        "dimensions": 1024,
        "concurrency": concurrency,
        "search": search,
        "context": context,
        "concurrent_search": concurrent,
        "post_load_recovery": recovery,
        "rss": rss,
        "provider": provider,
        "budget_comparisons": {
            "text_search_300ms": search["p95"] <= 0.3,
            "context_500ms": context["p95"] <= 0.5,
            "concurrent_search_1s": concurrent["p95"] <= 1,
            "search_server_rss_8gib": rss["measure_max_rss_bytes"] <= 8 * 1024**3,
        },
        "production_pass": False,
        "not_run": [
            "document_conversion",
            "conversion_child_rss",
            "print_preparation",
            "browser_paint",
            "declared_linux_hardware",
            "real_embedding_quality",
            "saturation_9th_request",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--documents", type=int, default=100)
    parser.add_argument("--chunks-each", type=int, default=100)
    parser.add_argument("--repeats", type=int, default=30)
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    with tempfile.TemporaryDirectory(prefix="docling-scale-") as temporary:
        result = run(
            Path(temporary), args.documents, args.chunks_each, args.repeats, args.concurrency
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps({"chunks": result["chunks"], "budget_comparisons": result["budget_comparisons"]})
    )
    return 0 if all(result["budget_comparisons"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
