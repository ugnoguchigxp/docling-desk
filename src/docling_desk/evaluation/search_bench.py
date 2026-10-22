"""Measure local full-text search on a synthetic Wiki index. No network and no user documents."""

from __future__ import annotations

import argparse
import json
import resource
import statistics
import sys
import tempfile
import time
from pathlib import Path

from docling_desk.evaluation.provider import SyntheticEmbedding
from docling_desk.knowledge.service import Knowledge
from docling_desk.knowledge.store import Store


def populate(data: Path, documents: int, chunks_each: int, dimensions: int) -> None:
    store = Store(data)
    articles = [
        {
            "id": f"wiki-bench-{index:05d}",
            "path": f"article-{index}.md",
            "title": f"記事{index}",
            "body": "\n\n".join(
                f"## 断片{part}\n記事{index} 断片{part} 検索語alpha" for part in range(chunks_each)
            ),
        }
        for index in range(documents)
    ]
    # Use the same canonical files/manifest path as imports. Direct SQLite
    # inserts are deliberately discarded by Store's next canonical-file sync.
    store.import_many("bench", articles)
    profile = SyntheticEmbedding(dimensions).profile
    with store.connection() as db:
        if dimensions:
            for part, chunk in enumerate(store.chunks()):
                vector = [0.0] * dimensions
                vector[part % dimensions] = 1.0
                db.execute(
                    "INSERT OR REPLACE INTO vectors(profile,hash,vector) VALUES(?,?,?)",
                    (profile, chunk["text_hash"], json.dumps(vector)),
                )


def _percentile(samples: list[float], fraction: float) -> float:
    ordered = sorted(samples)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * fraction)))
    return ordered[index]


def measure(data: Path, documents: int, chunks_each: int, dimensions: int, repeats: int) -> dict:
    if documents < 1 or chunks_each < 1 or repeats < 1 or not 0 <= dimensions <= 8192:
        raise ValueError("文書数・断片数・試行数は1以上、次元数は0〜8192にしてください。")
    if data.exists() and any(data.iterdir()):
        raise ValueError("測定先には空のディレクトリを指定してください。")
    populate(data, documents, chunks_each, dimensions)
    knowledge = Knowledge(data, SyntheticEmbedding(dimensions) if dimensions else None)
    query = {"query": "alpha", "mode": "text", "kind": "wiki", "namespace": "bench", "limit": 20}
    vector = [1.0] + [0.0] * (dimensions - 1) if dimensions else None
    try:
        knowledge.refresh()
        chunks = knowledge.store.chunks()
        sources = {chunk["source_id"] for chunk in chunks}
        expected = {chunk["id"]: chunk for chunk in chunks}
        if len(chunks) != documents * chunks_each or len(sources) != documents:
            raise ValueError("測定前の実文書数・断片数が要求した規模と一致しません。")
        vectors = knowledge.vectors()
        vector_count = len(vectors)
        if dimensions and (
            vector_count != len(chunks) or any(len(v) != dimensions for v in vectors.values())
        ):
            raise ValueError("測定前のベクトル数が断片数と一致しません。")
        del vectors

        def checked_search(value, query_vector=None):
            started = time.perf_counter()
            result = knowledge.search(value, query_vector)
            elapsed = time.perf_counter() - started
            hits = result["results"]
            if (
                len(hits) != min(20, len(chunks))
                or len({h["chunk_id"] for h in hits}) != len(hits)
                or any(
                    hit["chunk_id"] not in expected
                    or hit["source_id"] != expected[hit["chunk_id"]]["source_id"]
                    or hit["text"] != expected[hit["chunk_id"]]["text"]
                    or "alpha" not in hit["text"]
                    for hit in hits
                )
            ):
                raise ValueError("測定中の検索結果・本文・出典が正解データと一致しません。")
            return elapsed

        checked_search(query)
        text_samples = []
        for _ in range(repeats):
            text_samples.append(checked_search(query))
        hybrid_samples = []
        if vector:
            hybrid = {**query, "mode": "hybrid"}
            checked_search(hybrid, vector)
            for _ in range(repeats):
                hybrid_samples.append(checked_search(hybrid, vector))
    finally:
        knowledge.close()
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if sys.platform != "darwin":
        rss *= 1024
    return {
        "documents": len(sources),
        "chunks": len(chunks),
        "vectors": vector_count,
        "evidence_checked": True,
        "dimensions": dimensions,
        "repeats": repeats,
        "text_seconds": _summary(text_samples),
        "hybrid_seconds": _summary(hybrid_samples) if hybrid_samples else None,
        "max_rss_bytes": rss,
        "external_requests": 0,
        "input": "canonical synthetic Wiki imports; synthetic vectors, not semantic quality",
    }


def _summary(samples: list[float]) -> dict:
    return {
        "p50": statistics.median(samples),
        "p95": _percentile(samples, 0.95),
        "max": max(samples),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Measure synthetic local search")
    parser.add_argument("--documents", type=int, default=100)
    parser.add_argument("--chunks-each", type=int, default=100)
    parser.add_argument("--dimensions", type=int, default=1024)
    parser.add_argument("--repeats", type=int, default=30)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    with tempfile.TemporaryDirectory() as directory:
        result = measure(
            Path(directory), args.documents, args.chunks_each, args.dimensions, args.repeats
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
