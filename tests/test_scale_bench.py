from docling_desk.evaluation.scale import run


def test_scale_http_measures_real_chunks_and_post_load_recovery(tmp_path):
    result = run(tmp_path, documents=2, chunks_each=3, repeats=1, concurrency=2)
    assert result["chunks"] == 6
    assert result["search"]["samples"] == 1
    assert result["concurrent_search"]["samples"] == 2
    assert result["provider"]["outbound_connect_attempts"] == 0
    assert result["post_load_recovery"]["search_seconds"] > 0
    assert result["production_pass"] is False
    assert "conversion_child_rss" in result["not_run"]
