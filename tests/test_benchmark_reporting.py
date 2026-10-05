"""Aggregation rules for the user-path benchmark. Timings here are fabricated."""

from pathlib import Path

import httpx
import pytest

from docling_desk.evaluation.benchmark import run_benchmark
from docling_desk.evaluation.path import execute_user_path
from docling_desk.evaluation.recording import (
    PERCENTILE_METHOD,
    production_allowed,
    rollup,
    source_fingerprint,
    summarize_samples,
)


def _sample(outcome, search, context=None, source=None, total=None):
    return {
        "outcome": outcome,
        "search_seconds": search,
        "retrieval_seconds": search,
        "context_seconds": context,
        "source_seconds": source,
        "total_seconds": total if total is not None else search,
    }


def test_slow_success_stays_visible_and_failures_are_excluded():
    samples = [
        _sample("success", 0.20, 0.10, 0.05, 0.40),
        _sample("success", 0.22, 0.11, 0.05, 0.42),
        _sample("success", 5.00, 0.20, 0.05, 5.30),
        _sample("failure", 0.01, total=0.01),
        _sample("empty", 0.002, total=0.002),
        _sample("timeout", 3.0, total=3.0),
        _sample("cancelled", 0.03, total=0.03),
        _sample("wrong_evidence", 0.04, 0.01, 0.01, 0.08),
    ]
    summary = summarize_samples(samples)
    assert summary["success_count"] == 3
    assert summary["failure_count"] == 1
    assert summary["empty_count"] == 1
    assert summary["timeout_count"] == 1
    assert summary["cancelled_count"] == 1
    assert summary["wrong_evidence_count"] == 1
    assert summary["search_seconds"]["max"] == 5.0
    assert summary["search_seconds"]["p50"] == 0.22
    assert summary["search_seconds"]["n"] == 3
    assert summary["total_seconds"]["max"] == 5.30
    assert summary["total_seconds"]["p50"] != 0.08
    assert 0.01 not in (summary["search_seconds"]["p50"], summary["search_seconds"]["max"])
    assert summary["budget_exceeded_count"] == 1
    assert summary["failure_rate"]["numerator"] == 3
    assert summary["failure_rate"]["denominator"] == 8
    assert summary["unsuccessful_rate"]["numerator"] == 5
    assert summary["search_seconds"]["method"] == PERCENTILE_METHOD
    assert summary["search_seconds"]["guarantee"] is False
    assert summary["production_pass"] is False
    total = next(item for item in summary["budget_comparison"] if item["interval"] == "total")
    assert total["result"] == "not_judged"
    assert total["proposed_seconds"] is None
    search = next(item for item in summary["budget_comparison"] if item["interval"] == "search")
    assert search["result"] == "above_proposed"


def test_no_successes_are_not_a_zero_percentile_or_a_pass():
    summary = summarize_samples([_sample("empty", 0.001, total=0.001)])
    assert summary["search_seconds"]["p95"] is None
    assert summary["search_seconds"]["p50"] is None
    assert summary["success_count"] == 0
    assert summary["production_pass"] is False
    search = next(item for item in summary["budget_comparison"] if item["interval"] == "search")
    assert search["result"] == "not_judged"


def test_within_proposed_budget_is_still_not_a_production_pass():
    summary = summarize_samples([_sample("success", 0.05, 0.05, 0.05, 0.20)])
    assert summary["budget_exceeded_count"] == 0
    search = next(item for item in summary["budget_comparison"] if item["interval"] == "search")
    assert search["result"] == "within_proposed_not_a_pass"
    assert summary["production_pass"] is False
    assert production_allowed("ci_smoke", "proposed") is False
    assert production_allowed("formal", "proposed") is False
    assert production_allowed("formal", "adopted") is True


def test_total_is_not_replaced_by_the_sum_of_parts():
    summary = summarize_samples([_sample("success", 0.2, 0.2, 0.2, 0.5)])
    assert summary["total_seconds"]["p50"] == 0.5
    assert summary["total_definition"]


def test_not_run_is_not_counted_as_passed():
    summary = rollup(
        [
            {"id": "small_text", "status": "executed"},
            {"id": "browser_paint", "status": "not_run"},
            {"id": "formal_hardware", "status": "not_run"},
            {"id": "scale", "status": "not_selected"},
        ]
    )
    assert summary["passed"] == 0
    assert summary["not_run"] == 2
    assert summary["not_run_counted_as_passed"] is False
    assert summary["executed"] == 1


def test_repeat_count_must_be_positive():
    with pytest.raises(ValueError, match="測定回数は1以上です。"):
        run_benchmark(Path("unused"), repeats=0)


def test_retrieval_error_is_separate_from_search_status():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/knowledge/search":
            return httpx.Response(202, json={"id": "abc", "state": "queued"})
        return httpx.Response(500, json={"detail": "boom"})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://evaluation.test")
    sample = execute_user_path(
        client,
        query="搬出",
        mode="text",
        kind="wiki",
        namespace="evaluation-small",
        limit=5,
        budget=4000,
        expect="passage",
        expected_source_id="wiki",
        expected_passage="搬出上限は120箱です。",
        source_endpoint="wiki",
    )
    assert sample["outcome"] == "failure"
    assert sample["search_http_status"] == 202
    assert sample["retrieval_http_status"] == 500
    assert sample["evidence_ok"] is False


def test_untracked_files_change_the_source_fingerprint(tmp_path):
    import subprocess

    (tmp_path / "tracked.py").write_text("print(1)\n", encoding="utf-8")
    subprocess.run(["git", "init", "-b", "main"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "add", "tracked.py"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=test@example.com",
            "-c",
            "user.name=test",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-m",
            "init",
        ],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    first = source_fingerprint(tmp_path)
    assert first["source_identity"] == "working_tree"
    assert first["hashed_files"] == 1
    folder = tmp_path / "evaluation"
    folder.mkdir()
    provider = folder / "provider.py"
    provider.write_text("VALUE = 1\n", encoding="utf-8")
    second = source_fingerprint(tmp_path)
    assert second["hashed_files"] == 2
    assert second["source_tree_sha256"] != first["source_tree_sha256"]
    provider.write_text("VALUE = 2\n", encoding="utf-8")
    third = source_fingerprint(tmp_path)
    assert third["source_tree_sha256"] != second["source_tree_sha256"]
    spaced = tmp_path / "名前 付き.py"
    spaced.write_text("x = 1\n", encoding="utf-8")
    fourth = source_fingerprint(tmp_path)
    assert fourth["hashed_files"] == 3
    assert fourth["source_tree_sha256"] != third["source_tree_sha256"]
    (tmp_path / "example-report.json").write_text("{}\n", encoding="utf-8")
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "local.sqlite").write_text("stored", encoding="utf-8")
    assert source_fingerprint(tmp_path)["source_tree_sha256"] == fourth["source_tree_sha256"]
    assert source_fingerprint(tmp_path / "not-a-repo")["source_identity"] == "unavailable"
    assert source_fingerprint(tmp_path / "not-a-repo")["source_tree_sha256"] is None
