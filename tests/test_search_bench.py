import pytest

from docling_desk.evaluation import search_bench
from docling_desk.knowledge.service import Knowledge
from docling_desk.knowledge.store import Store


def test_canonical_benchmark_survives_reopen_and_refresh(tmp_path):
    search_bench.populate(tmp_path, 2, 3, 4)
    store = Store(tmp_path)
    assert len(store.sources("wiki")) == 2
    assert len(store.chunks()) == 6
    result = search_bench.measure(tmp_path / "measurement", 2, 3, 4, 2)
    assert (result["documents"], result["chunks"], result["vectors"]) == (2, 6, 6)
    assert result["evidence_checked"] is True
    assert result["hybrid_seconds"]["p95"] > 0
    assert result["external_requests"] == 0


@pytest.mark.parametrize("violation", ["empty", "wrong_source", "wrong_text"])
def test_benchmark_refuses_invalid_search_instead_of_timing_success(
    tmp_path, monkeypatch, violation
):
    search = Knowledge.search

    def invalid(self, *args):
        result = search(self, *args)
        if violation == "empty":
            result["results"] = []
        else:
            result["results"][0]["source_id" if violation == "wrong_source" else "text"] = "wrong"
        return result

    monkeypatch.setattr(Knowledge, "search", invalid)
    with pytest.raises(ValueError, match="出典"):
        search_bench.measure(tmp_path, 1, 2, 0, 1)


def test_benchmark_refuses_disappeared_corpus_and_nonempty_destination(tmp_path, monkeypatch):
    populate = search_bench.populate

    def broken(data, *args):
        populate(data, *args)
        store = Store(data)
        store.delete_wiki(store.sources("wiki")[0]["id"])

    monkeypatch.setattr(search_bench, "populate", broken)
    with pytest.raises(ValueError, match="規模"):
        search_bench.measure(tmp_path, 1, 2, 0, 1)
    with pytest.raises(ValueError, match="空"):
        search_bench.measure(tmp_path, 1, 2, 0, 1)
