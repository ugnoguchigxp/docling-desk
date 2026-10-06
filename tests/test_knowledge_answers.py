"""RAG relevance selection, parent evidence, citations and task lifecycle."""

import threading
import time

import pytest
import test_local_knowledge as local_tests
from test_local_knowledge import article, document

import docling_desk.knowledge.service as service
from docling_desk import config
from docling_desk.knowledge.answer import AnswerProvider

client = local_tests.client


class Fixed:
    def __init__(self):
        self.candidates = []
        self.evidence = []
        self.selected = None
        self.cancelled = False

    def select(self, question, candidates):
        self.candidates = candidates
        return self.selected if self.selected is not None else [candidates[-1]["chunk_id"]]

    def answer(self, question, evidence):
        self.evidence = evidence
        return {
            "answer": "キーワードの意味と役割です。[S1]",
            "citation_ids": ["S1"],
            "unknowns": [],
        }

    def cancel(self):
        self.cancelled = True


def wait(client, task_id):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        response = client.get(f"/api/knowledge/retrievals/{task_id}")
        assert response.status_code == 200, response.text
        value = response.json()
        if value["state"] not in {"queued", "running"}:
            return value
        time.sleep(0.01)
    pytest.fail("RAG task did not finish")


def start(client, **values):
    response = client.post("/api/knowledge/answers", json={"query": "キーワード", **values})
    assert response.status_code == 202, response.text
    return response.json()["id"]


def test_rag_selects_from_top_five_and_hides_rejected_results(client, monkeypatch):
    for i in range(8):
        article(client, f"term{i}.md", f"# 用語{i}\n\nキーワードについて{i}の説明。")
    fixed = Fixed()
    monkeypatch.setattr(service, "AnswerProvider", lambda: fixed)
    result = wait(client, start(client, limit=50))
    assert result["state"] == "completed", result
    assert len(fixed.candidates) == 5
    assert len(fixed.evidence) == len(result["output"]["results"]) == 1
    selected = fixed.candidates[-1]
    assert result["output"]["results"][0]["chunk_id"] == selected["chunk_id"]
    assert selected["text"] in fixed.evidence[0]["text"]
    assert result["output"]["answer"].endswith("[S1]")
    assert result["output"]["citations"][0]["label"] == "S1"


def test_rag_uses_full_parent_text_and_revalidates_deleted_source(client, monkeypatch):
    job = document(config.DATA, text="キーワードの検索断片")
    fixed = Fixed()
    monkeypatch.setattr(service, "AnswerProvider", lambda: fixed)
    task_id = start(client)
    result = wait(client, task_id)
    assert result["state"] == "completed", result
    assert "関連する根拠本文" in fixed.evidence[0]["text"]
    assert result["output"]["citations"][0]["unit"] == 2
    assert result["output"]["citations"][0]["job_id"] == job
    # Wiki deletion exercises the same revision/scope revalidation.
    source = article(client, "term.md", "# 用語\n\nキーワードの意味")
    second = start(client, source_id=source)
    assert wait(client, second)["output"]["answer"]
    assert client.delete("/api/wiki/sources/" + source).status_code == 200
    stale = client.get("/api/knowledge/retrievals/" + second).json()["output"]
    assert stale["stale"]
    assert "answer" not in stale and "citations" not in stale


def test_rag_no_matches_or_all_rejected_never_generates(client, monkeypatch):
    fixed = Fixed()
    fixed.selected = []
    monkeypatch.setattr(service, "AnswerProvider", lambda: fixed)
    assert wait(client, start(client))["output"]["results"] == []
    assert fixed.candidates == []
    article(client, body="# 用語\n\nキーワードの意味")
    result = wait(client, start(client))
    assert fixed.candidates
    assert not fixed.evidence
    assert result["output"]["results"] == []
    assert result["output"]["citations"] == []


def test_rag_drops_answer_when_source_changes_during_generation(client, monkeypatch):
    source = article(client, body="# 用語\n\nキーワードの意味")
    fixed = Fixed()

    def answer(question, evidence):
        assert client.delete("/api/wiki/sources/" + source).status_code == 200
        return {"answer": "古い回答[S1]", "citation_ids": ["S1"], "unknowns": []}

    fixed.answer = answer
    monkeypatch.setattr(service, "AnswerProvider", lambda: fixed)
    result = wait(client, start(client))
    assert result["state"] == "failed"
    assert "出典が更新" in result["error"]
    assert "answer" not in result["output"]


@pytest.mark.parametrize(
    "stage,response",
    [
        ("select", {"selected_ids": ["invented"]}),
        ("select", {"selected_ids": ["c1", "c1"]}),
        ("answer", {"answer": "誤った参照[S9]", "citation_ids": ["S9"], "unknowns": []}),
        ("answer", {"answer": "参照がない回答", "citation_ids": ["S1"], "unknowns": []}),
    ],
)
def test_rag_provider_rejects_invalid_selection_and_citations(monkeypatch, stage, response):
    provider = AnswerProvider()
    monkeypatch.setattr(provider, "complete_structured", lambda *args: response)
    with pytest.raises(ValueError):
        if stage == "select":
            provider.select("用語", [{"chunk_id": "c1", "text": "用語本文"}])
        else:
            provider.answer("用語", [{"label": "S1", "text": "用語本文"}])


def test_rag_cancellation_stops_selection_without_generating(client, monkeypatch):
    article(client, body="# 用語\n\nキーワードの意味")
    entered, released = threading.Event(), threading.Event()
    fixed = Fixed()

    def select(question, candidates):
        entered.set()
        assert released.wait(5)
        return [candidates[0]["chunk_id"]]

    def cancel():
        fixed.cancelled = True
        released.set()

    fixed.select, fixed.cancel = select, cancel
    monkeypatch.setattr(service, "AnswerProvider", lambda: fixed)
    task_id = start(client)
    assert entered.wait(5)
    response = client.post(f"/api/knowledge/tasks/{task_id}/cancel", json={})
    assert response.json()["state"] == "cancelled"
    assert fixed.cancelled and fixed.evidence == []
    assert wait(client, task_id)["state"] == "cancelled"


def test_rag_request_id_does_not_duplicate_llm_work(client, monkeypatch):
    article(client, body="# 用語\n\nキーワードの意味")
    fixed = Fixed()
    monkeypatch.setattr(service, "AnswerProvider", lambda: fixed)
    task_id = start(client, client_request_id="same-request")
    assert wait(client, task_id)["state"] == "completed"
    assert start(client, client_request_id="same-request") == task_id
    assert (
        client.post(
            "/api/knowledge/search",
            json={"query": "キーワード", "client_request_id": "same-request", "limit": 5},
        ).status_code
        == 422
    )


def test_rag_selection_keeps_priority_order_and_uses_no_rejected_parent(client, monkeypatch):
    for i in range(5):
        article(client, f"term{i}.md", f"# 用語{i}\n\nキーワードの説明{i}。")
    fixed = Fixed()

    def select(question, candidates):
        fixed.candidates = candidates
        return [candidates[4]["chunk_id"], candidates[1]["chunk_id"]]

    fixed.select = select
    monkeypatch.setattr(service, "AnswerProvider", lambda: fixed)
    output = wait(client, start(client))["output"]
    assert [h["chunk_id"] for h in output["results"]] == [
        fixed.candidates[i]["chunk_id"] for i in (4, 1)
    ]
    assert [e["title"] for e in fixed.evidence] == [h["title"] for h in output["results"]]
    assert len(fixed.evidence) == 2
