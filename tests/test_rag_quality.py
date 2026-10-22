from docling_desk.evaluation.corpus import load_cases
from docling_desk.evaluation.rag_quality import evaluate


class Answer:
    def select(self, question, candidates):
        return [candidates[0]["chunk_id"]]

    def answer(self, question, evidence):
        return {"answer": "誤った上限は999箱です。[S1]", "citation_ids": ["S1"], "unknowns": []}

    def cancel(self):
        pass


def test_actual_rag_path_and_flagged_model_judgment_do_not_become_quality_pass(tmp_path):
    case = load_cases()[0]
    seen = []

    def judge(payload):
        seen.append(payload)
        return {
            "grounded": False,
            "addresses_question": False,
            "citations_support_claims": False,
            "answerability_handled_correctly": False,
            "issues": ["数値と引用が根拠に一致しない"],
        }

    report = evaluate(
        tmp_path, [case], answer_factory=Answer, judge=judge, query_mode="authored", timeout=5
    )
    assert "999箱" in seen[0]["answer"]
    assert seen[0]["evidence"] and "120箱" in seen[0]["evidence"][0]["text"]
    assert report["cases"][0]["status"] == "model_flagged"
    assert report["quality_passed"] is False
    assert report["human_review"] == "not_run"
