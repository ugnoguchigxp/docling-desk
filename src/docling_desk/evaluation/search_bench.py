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

from docling_desk.knowledge.service import Knowledge
from docling_desk.knowledge.store import Store, digest, normalized


class _Profile:
    def __init__(self, name: str):
        self.profile = name


def populate(data: Path, documents: int, chunks_each: int, dimensions: int) -> None:
    store = Store(data)
    profile = "bench"
    with store.connection() as db:
        for index in range(documents):
            source_id = f"wiki-{index:05d}"
            revision = f"rev-{index:05d}"
            db.execute(
                """INSERT INTO sources(id,kind,namespace,path,title,body,revision,metadata,updated)
                VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    source_id,
                    "wiki",
                    "bench",
                    f"article-{index}.md",
                    f"記事{index}",
                    "本文",
                    revision,
                    "{}",
                    0,
                ),
            )
            for part in range(chunks_each):
                text = f"記事{index} 断片{part} 検索語alpha"
                chunk_id = f"{source_id}:{part}"
                text_hash = digest(text)
                db.execute(
                    """INSERT INTO chunks(id,source_id,revision,text,text_hash,context,locator,context_id)
                    VALUES(?,?,?,?,?,?,?,?)""",
                    (
                        chunk_id,
                        source_id,
                        revision,
                        text,
                        text_hash,
                        "",
                        "{}",
                        digest(source_id + revision + digest(text)),
                    ),
                )
                db.execute(
                    "INSERT INTO chunk_fts VALUES(?,?,?)",
                    (chunk_id, normalized(f"記事{index}"), normalized(text)),
                )
                if dimensions:
                    vector = [1.0 if i == part % dimensions else 0.0 for i in range(dimensions)]
                    db.execute(
                        "INSERT INTO vectors(profile,hash,vector) VALUES(?,?,?)",
                        (profile, text_hash, json.dumps(vector)),
                    )


def _percentile(samples: list[float], fraction: float) -> float:
    ordered = sorted(samples)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * fraction)))
    return ordered[index]


def measure(data: Path, documents: int, chunks_each: int, dimensions: int, repeats: int) -> dict:
    populate(data, documents, chunks_each, dimensions)
    knowledge = Knowledge(data, _Profile("bench"))
    query = {"query": "alpha", "mode": "text", "kind": "wiki", "namespace": "bench", "limit": 20}
    vector = [1.0] + [0.0] * (dimensions - 1) if dimensions else None
    try:
        knowledge.search(query)
        text_samples = []
        for _ in range(repeats):
            started = time.perf_counter()
            knowledge.search(query)
            text_samples.append(time.perf_counter() - started)
        hybrid_samples = []
        if vector:
            hybrid = {**query, "mode": "hybrid"}
            knowledge.search(hybrid, vector)
            for _ in range(repeats):
                started = time.perf_counter()
                knowledge.search(hybrid, vector)
                hybrid_samples.append(time.perf_counter() - started)
    finally:
        knowledge.close()
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if sys.platform != "darwin":
        rss *= 1024
    return {
        "documents": documents,
        "chunks": documents * chunks_each,
        "dimensions": dimensions,
        "repeats": repeats,
        "text_seconds": _summary(text_samples),
        "hybrid_seconds": _summary(hybrid_samples) if hybrid_samples else None,
        "max_rss_bytes": rss,
        "external_requests": 0,
        "input": "synthetic wiki chunks in the local search schema",
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
