"""Loopback user-path benchmark. Does not grade production performance."""

from docling_desk.evaluation.benchmark import run_benchmark


def test_isolated_http_path_records_samples_without_external_calls(tmp_path):
    report = run_benchmark(
        tmp_path,
        repeats=1,
        scenario_ids={"small_text", "small_text_absent", "small_mixed_artifact"},
    )
    assert report["transport"] == "loopback_http"
    assert report["testclient_used_for_timings"] is False
    assert report["production_pass"] is False
    assert report["budget_status"] == "proposed"
    assert report["external_requests"] == 0
    assert report["provider"]["outbound_connect_attempts"] == 0
    assert report["provider"]["provider_kind"] == "synthetic"
    assert report["provider"]["production_env_flag"] is None
    assert report["semantic_quality_claimed"] is False
    assert report["chunk_count_is_measured"] is True
    assert report["chunk_count_after_wiki_import"] >= 10
    assert report["source_tree_sha256"]
    assert report["git_commit"]
    assert report["input_sha256"]
    assert report["machine"]["formal_measurement"] is False
    small = next(item for item in report["scenarios"] if item["id"] == "small_text")
    assert small["status"] == "executed"
    assert small["summary"]["success_count"] == 1
    assert small["summary"]["production_pass"] is False
    assert small["summary"]["scale_role"] == "ci_smoke"
    assert small["samples"][0]["evidence_ok"] is True
    assert small["ordinal"] == 1
    assert small["same_process_as_previous"] is False
    assert small["samples"][0]["source_observed_via"] == "wiki"
    absent = next(item for item in report["scenarios"] if item["id"] == "small_text_absent")
    assert absent["summary"]["empty_count"] == 1
    assert absent["summary"]["success_count"] == 0
    assert absent["summary"]["search_seconds"]["p95"] is None
    assert absent["same_process_as_previous"] is True
    mixed = next(item for item in report["scenarios"] if item["id"] == "small_mixed_artifact")
    assert mixed["conversion"] is False
    assert mixed["summary"]["success_count"] == 1
    assert report["chunk_count_after_run"] > report["chunk_count_after_wiki_import"]
    assert report["rss"]["client_process_included"] is False
    assert report["rss"]["sample_count"] >= 1
    assert report["rollup"]["passed"] == 0
    assert report["rollup"]["not_run_counted_as_passed"] is False
    assert any(item["id"] == "browser_paint" for item in report["not_run"])
    assert any(item["id"] == "document_conversion" for item in report["not_run"])
    assert any(item["id"] == "formal_hardware" for item in report["not_run"])
    assert any(item["status"] == "not_selected" for item in report["scenarios"])
