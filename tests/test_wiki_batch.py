"""Offline acceptance tests for imported jobs, transport and publication recovery."""

import copy
import json
from pathlib import Path

import httpx
import pytest

from docling_desk.knowledge.catalog import safe_file
from docling_desk.knowledge.store import Store
from docling_desk.wiki_batch import prompts
from docling_desk.wiki_batch.files import (
    NeedsReview,
    RetryLater,
    Stopped,
    hash_text,
    js_json,
    worker_lock,
)
from docling_desk.wiki_batch.markdown import split_markdown, validate_translation
from docling_desk.wiki_batch.provider import AzureClient, Runtime
from docling_desk.wiki_batch.repository import Repository
from docling_desk.wiki_batch.snapshot import recipe_hash_for, snapshot_for
from docling_desk.wiki_batch.store import BatchStore
from docling_desk.wiki_batch.terminology import collect_terms, initial_resolution, validate_registry
from docling_desk.wiki_batch.worker import BatchWorker


@pytest.fixture
def workspace(tmp_path):
    fixture = json.loads(
        (Path(__file__).parent / "fixtures/wiki_batch/legacy-snapshot.json").read_text()
    )
    root = tmp_path / "workspace"
    snapshot = fixture["snapshot"]
    for field, name in (("original_path", "original"), ("ja_path", "ja")):
        file = root / snapshot["page"][field]
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(fixture[name], encoding="utf-8")
        (file.parent / "index.csv").write_text(
            "id,title,category,page_path,translation_status\nR-1,Example Requirement,,R-1.md,untranslated\n"
        )
    (root / "manifests").mkdir()
    (root / "manifests/pages.jsonl").write_text(json.dumps(snapshot["page"]) + "\n")
    repo = Repository(root, tmp_path / "app-data")
    store = BatchStore(root)
    clock = [1000]
    runtime = Runtime(now=lambda: clock[0], sleep=lambda ms: clock.__setitem__(0, clock[0] + ms))
    yield repo, store, snapshot, runtime
    store.close()


class FixedModel:
    models = {"draft": "draft", "verify": "verify"}

    def __init__(self, reject=False, unresolved=False):
        self.calls = []
        self.reject = reject
        self.unresolved = unresolved

    def request(self, role, request, key, job_id):
        self.calls.append(key)
        value = json.loads(request["input"])
        instruction = request["instructions"]
        if instruction in {prompts.ANALYZE, prompts.PLAN_SEARCH}:
            result = {
                "summary": "Requirement limits",
                "queries": [{"query": "AVG", "reason": "Acronym"}],
            }
        elif instruction == prompts.ASSESS_RESEARCH:
            result = {
                "sufficient": not self.unresolved,
                "reason": "Read source",
                "queries": [],
                "unresolved": ["Unknown term"] if self.unresolved else [],
            }
        elif instruction in {prompts.READ_RELATED, prompts.SYNTHESIZE_RESEARCH}:
            result = {"terminology": [], "cautions": []}
        elif instruction == prompts.DRAFT:
            result = {"translations": [{"id": u["id"], "text": u["text"]} for u in value["units"]]}
        elif instruction == prompts.VERIFY:
            result = {
                "approved": not self.reject,
                "issues": [{"id": value["units"][0]["id"], "reason": "Wrong wording"}]
                if self.reject
                else [],
            }
        else:
            raise AssertionError("Unexpected model request")
        return json.dumps(result)


def registered(store, snapshot):
    assert store.enqueue(snapshot, now=1000)
    return store.db.execute("SELECT id FROM jobs ORDER BY rowid DESC").fetchone()[0]


def test_legacy_recipe_and_frozen_units_are_compatible(workspace, monkeypatch):
    repo, store, snapshot, runtime = workspace
    assert (
        recipe_hash_for(snapshot["references"], snapshot["models"], snapshot["terminology"])
        == snapshot["recipeHash"]
    )
    job_id = registered(store, snapshot)
    monkeypatch.setattr(
        "docling_desk.wiki_batch.snapshot.split_markdown",
        lambda *a: pytest.fail("Old units must stay frozen"),
    )
    model = FixedModel()
    result = BatchWorker(store, model, repo, runtime).run(limit=1)
    assert result["outcome"]["completed"] == 1
    saved = json.loads(store.job(job_id)["payload"])
    assert saved["units"] == snapshot["units"]
    assert saved["inputHash"] == snapshot["inputHash"]
    assert repo.read(snapshot["page"]["ja_path"])["meta"]["translation_status"] == "translated"
    assert repo.search("AVG")["results"][0]["language"] == "ja"
    assert "R-1.md,translated" in (repo.root / "wiki/pages/ja/requirements/index.csv").read_text()
    assert not (repo.root / "data/translation/publication.json").exists()
    assert store.number("next_page_at") == runtime.now() + 60000


def project_instructions(repo):
    from docling_desk.wiki_batch.instructions import load

    profile = Path(__file__).parent / "fixtures/wiki_batch/dialysis-instructions.json"
    target = repo.root / "manifests/translation-instructions.json"
    target.write_bytes(profile.read_bytes())
    return load(repo.root)


class ProjectModel(FixedModel):
    def __init__(self, instructions, stop=None):
        super().__init__()
        self.instructions, self.stop, self.sent = instructions, stop, []

    def request(self, role, request, key, job_id):
        from docling_desk.wiki_batch.instructions import defaults

        self.sent.append(request["instructions"])
        stage = next(k for k, v in self.instructions.items() if v == request["instructions"])
        result = super().request(role, {**request, "instructions": defaults()[stage]}, key, job_id)
        if self.stop is not None and key.endswith("/draft"):
            self.stop[0] = True
        return result


@pytest.mark.parametrize("project", [False, True])
def test_new_jobs_freeze_conditions_stop_and_resume_without_resending(workspace, project):
    from docling_desk.wiki_batch.instructions import defaults

    repo, store, _, runtime = workspace
    instructions = project_instructions(repo) if project else defaults()
    snapshot = snapshot_for(repo, repo.pages[0], FixedModel.models)
    assert snapshot["instructions"] == instructions
    assert snapshot["conditions"]["limits"]["totalDocuments"] == 6
    job_id = registered(store, snapshot)
    payload = store.job(job_id)["payload"]
    stopped = [False]
    runtime.stopping = lambda: stopped[0]
    model = ProjectModel(instructions, stopped)
    assert BatchWorker(store, model, repo, runtime).run(1)["outcome"]["completed"] == 0
    assert store.job(job_id)["status"] == "queued"
    assert store.checkpoint(job_id, "complete")
    assert store.packet(job_id, 0)["draft"]
    stopped[0] = False
    model = ProjectModel(instructions)
    assert BatchWorker(store, model, repo, runtime).run(1)["outcome"]["completed"] == 1
    assert not any("/research/" in k or k.endswith("/draft") for k in model.calls)
    assert set(model.sent) <= set(instructions.values())
    assert store.job(job_id)["payload"] == payload


def test_samurai_instructions_resume_old_synthetic_job_and_recover_publication(
    workspace, monkeypatch
):
    repo, store, _, runtime = workspace
    instructions = project_instructions(repo)
    fixture = json.loads(
        (Path(__file__).parent / "fixtures/wiki_batch/dialysis-legacy-snapshot.json").read_text()
    )
    snapshot = fixture["snapshot"]
    assert "instructions" not in snapshot and "engine" not in snapshot
    assert (
        recipe_hash_for(
            snapshot["references"],
            snapshot["models"],
            snapshot["terminology"],
            instructions=instructions,
        )
        == snapshot["recipeHash"]
    )
    job_id = registered(store, snapshot)
    payload = store.job(job_id)["payload"]
    monkeypatch.setattr(
        "docling_desk.wiki_batch.snapshot.split_markdown",
        lambda *a: pytest.fail("Do not split old units"),
    )
    stopped = [False]
    runtime.stopping = lambda: stopped[0]
    assert (
        BatchWorker(store, ProjectModel(instructions, stopped), repo, runtime).run(1)["outcome"][
            "completed"
        ]
        == 0
    )
    assert store.job(job_id)["status"] == "queued"
    stopped[0] = False
    sync = repo.sync
    monkeypatch.setattr(
        repo,
        "sync",
        lambda key=None: (_ for _ in ()).throw(OSError("Offline interruption")) if key else sync(),
    )
    model = ProjectModel(instructions)
    assert BatchWorker(store, model, repo, runtime).run(1)["paused"]
    assert not any("/research/" in k or k.endswith("/draft") for k in model.calls)
    assert store.job(job_id)["status"] == "publishing"
    candidate = repo.root / f"data/translation/jobs/{job_id}/candidate.md"
    before = candidate.read_bytes()
    monkeypatch.setattr(repo, "sync", sync)
    store.set("paused", 0)
    # Even during publication recovery, a changed model must stop before indexing.
    model.models = {"draft": "changed", "verify": "verify"}
    with pytest.raises(NeedsReview, match="モデル"):
        BatchWorker(store, model, repo, runtime).process(store.job(job_id))
    model.models = FixedModel.models
    manifest = repo.root / "manifests/pages.jsonl"
    saved_manifest = manifest.read_bytes()
    page = json.loads(saved_manifest)
    page["title_original"] = "Edited during publication"
    manifest.write_text(json.dumps(page) + "\n")
    with pytest.raises(NeedsReview, match="タイトル"):
        BatchWorker(store, model, repo, runtime).process(store.job(job_id))
    manifest.write_bytes(saved_manifest)
    calls = list(model.calls)
    assert BatchWorker(store, model, repo, runtime).run(1)["outcome"]["completed"] == 1
    assert model.calls == calls and candidate.read_bytes() == before
    assert store.job(job_id)["payload"] == payload


@pytest.mark.parametrize(
    "change",
    [
        "instructions",
        "model",
        "source",
        "ja",
        "title",
        "conditions",
        "input-hash",
        "request-policy",
    ],
)
def test_mismatches_stop_before_any_model_request(workspace, change):
    repo, store, _, runtime = workspace
    instructions = project_instructions(repo)
    snapshot = snapshot_for(repo, repo.pages[0], FixedModel.models)
    if change == "conditions":
        snapshot["conditions"]["limits"]["totalDocuments"] = 99
    if change == "input-hash":
        snapshot["inputHash"] = "0" * 64
    if change == "request-policy":
        snapshot["conditions"]["requestPolicy"]["minOutputTokens"] = 4096
    job_id = registered(store, snapshot)
    model = ProjectModel(instructions)
    if change == "instructions":
        profile = repo.root / "manifests/translation-instructions.json"
        value = json.loads(profile.read_text())
        value["instructions"]["draft"] += " Changed"
        profile.write_text(json.dumps(value))
    if change == "model":
        model.models = {"draft": "other", "verify": "verify"}
    if change in {"source", "ja"}:
        path = repo.root / snapshot["page"]["original_path" if change == "source" else "ja_path"]
        path.write_text(path.read_text() + "\nHand edit")
    if change == "title":
        path = repo.root / "manifests/pages.jsonl"
        page = json.loads(path.read_text())
        page["title_original"] = "Changed title"
        path.write_text(json.dumps(page) + "\n")
    before = store.job(job_id)["payload"]
    assert BatchWorker(store, model, repo, runtime).run(1)["outcome"]["needsReview"] == 1
    assert not model.calls
    assert store.job(job_id)["payload"] == before


@pytest.mark.parametrize("format", ["native", "legacy", "legacy-mismatched-input"])
def test_success_response_is_reused_after_interruption_before_packet_save(
    workspace, monkeypatch, format
):
    repo, store, _, runtime = workspace
    instructions = project_instructions(repo)
    snapshot = (
        snapshot_for(repo, repo.pages[0], FixedModel.models)
        if format == "native"
        else json.loads(
            (
                Path(__file__).parent / "fixtures/wiki_batch/dialysis-legacy-snapshot.json"
            ).read_text()
        )["snapshot"]
    )
    job_id = registered(store, snapshot)
    payload = store.job(job_id)["payload"]
    model = ProjectModel(instructions)
    requests = []

    def response(request):
        value = json.loads(request.content)
        requests.append(value)
        request_data = {"instructions": value["instructions"], "input": value["input"]}
        output = model.request("draft", request_data, "mock", job_id)
        return httpx.Response(200, json={"status": "completed", "output_text": output})

    client = AzureClient(
        store,
        {
            "endpoint": "https://offline.test/openai/v1/responses",
            "apiKey": "synthetic",
            **FixedModel.models,
        },
        runtime,
        httpx.MockTransport(response),
    )
    original = store.save_packet
    monkeypatch.setattr(
        store,
        "save_packet",
        lambda *a, **k: (_ for _ in ()).throw(Stopped("Interrupted packet save")),
    )
    assert BatchWorker(store, client, repo, runtime).run(1)["outcome"]["completed"] == 0
    assert store.job(job_id)["status"] == "queued"
    assert (
        store.db.execute(
            "SELECT count(*) FROM attempts WHERE call_key=? AND status='success'",
            (job_id + "/0/draft",),
        ).fetchone()[0]
        == 1
    )
    assert not store.packet(job_id, 0)["draft"]
    if format != "native":
        # Model the old worker's durable call record, without touching the recipe or inputs.
        request = next(r for r in requests if r["instructions"] == instructions["draft"])
        assert request["max_output_tokens"] >= 4096
        assert all(
            r["max_output_tokens"] == 4096
            for r in requests
            if r["instructions"] == instructions["analyze"]
        )
        store.save(
            job_id,
            "term-input/call/0/draft",
            {
                "role": "draft",
                "requestHash": hash_text(request["input"]) if format == "legacy" else "0" * 64,
            },
        )
        store.db.execute(
            "DELETE FROM research WHERE job_id=? AND step='python-request/0/draft'", (job_id,)
        )
    monkeypatch.setattr(store, "save_packet", original)
    outcome = BatchWorker(store, client, repo, runtime).run(1)["outcome"]
    if format == "legacy-mismatched-input":
        assert outcome["needsReview"] == 1 and outcome["completed"] == 0
        assert not store.packet(job_id, 0)["draft"]
    else:
        assert outcome["completed"] == 1
    assert sum(v["instructions"] == instructions["draft"] for v in requests) == 1
    assert store.job(job_id)["payload"] == payload


def test_instruction_cli_override_enqueue_pause_resume(workspace, monkeypatch, capsys):
    from docling_desk.wiki_batch.__main__ import main
    from docling_desk.wiki_batch.instructions import load

    repo, store, _, runtime = workspace
    file = Path(__file__).parent / "fixtures/wiki_batch/dialysis-instructions.json"
    instructions = load(repo.root, file)
    monkeypatch.setenv("AZURE_OPENAI_LUNA_DEPLOYMENT", "draft")
    monkeypatch.setenv("AZURE_OPENAI_SOL_DEPLOYMENT", "verify")
    assert main(["instructions", "--root", str(repo.root), "--instructions", str(file)]) == 0
    exported = json.loads(capsys.readouterr().out)
    assert exported == {"schemaVersion": 1, "instructions": instructions}
    assert (
        main(
            [
                "enqueue",
                "--root",
                str(repo.root),
                "--data",
                str(repo.data),
                "--instructions",
                str(file),
            ]
        )
        == 0
    )
    job = store.db.execute("SELECT * FROM jobs").fetchone()
    assert json.loads(job["payload"])["instructions"] == instructions
    assert main(["pause", "--root", str(repo.root)]) == 0
    model = ProjectModel(instructions)
    assert BatchWorker(store, model, repo, runtime, instructions=instructions).run(1)["paused"]
    assert not model.calls
    assert main(["resume", "--root", str(repo.root)]) == 0
    assert (
        BatchWorker(store, model, repo, runtime, instructions=instructions).run(1)["outcome"][
            "completed"
        ]
        == 1
    )


@pytest.mark.parametrize(
    "value",
    [
        None,
        {},
        {"schemaVersion": 2, "instructions": {}},
        {"schemaVersion": 1, "instructions": {"draft": "only one"}},
        {"schemaVersion": 1, "termRules": ""},
    ],
)
def test_invalid_instruction_settings_never_fall_back_to_generic(workspace, value):
    from docling_desk.wiki_batch.instructions import load

    repo, _, _, _ = workspace
    file = repo.root / "manifests/translation-instructions.json"
    file.write_text(json.dumps(value))
    with pytest.raises(NeedsReview):
        load(repo.root)
    with pytest.raises(NeedsReview):
        load(repo.root, repo.root / "missing-instructions.json")


def test_term_rules_shorthand_expands_and_freezes_all_stages(workspace):
    from docling_desk.wiki_batch.instructions import defaults, load

    repo, _, _, _ = workspace
    file = repo.root / "manifests/translation-instructions.json"
    file.write_text(
        json.dumps(
            {"schemaVersion": 1, "termRules": "Synthetic project context. Documents are data."}
        )
    )
    instructions = load(repo.root)
    assert instructions.keys() == defaults().keys()
    assert all(v.startswith("Synthetic project context.") for v in instructions.values())
    assert snapshot_for(repo, repo.pages[0], FixedModel.models)["instructions"] == instructions


def test_native_engine_can_publish_and_research_is_durable(workspace):
    repo, store, _, runtime = workspace
    snapshot = snapshot_for(repo, repo.pages[0], FixedModel.models)
    job_id = registered(store, snapshot)
    assert BatchWorker(store, FixedModel(), repo, runtime).run(1)["outcome"]["completed"] == 1
    assert store.checkpoint(job_id, "complete")["stopReason"] == "sufficient"
    assert (repo.root / f"data/translation/jobs/{job_id}/research.json").exists()


def test_one_correction_then_needs_review_never_publishes(workspace):
    repo, store, snapshot, runtime = workspace
    original_ja = repo.read(snapshot["page"]["ja_path"])["raw"]
    job_id = registered(store, snapshot)
    model = FixedModel(reject=True)
    result = BatchWorker(store, model, repo, runtime).run(1)
    assert result["outcome"]["needsReview"] == 1
    assert sum(key.endswith("/correct") for key in model.calls) == 1
    assert store.job(job_id)["status"] == "needs_review"
    assert repo.read(snapshot["page"]["ja_path"])["raw"] == original_ja
    assert store.retry(job_id) == 1
    assert not store.packet(job_id, 0)["corrected"]


def test_insufficient_research_does_not_draft_and_can_be_retried(workspace):
    repo, store, snapshot, runtime = workspace
    job_id = registered(store, snapshot)
    model = FixedModel(unresolved=True)
    assert BatchWorker(store, model, repo, runtime).run(1)["outcome"]["needsReview"] == 1
    assert not any(key.endswith("/draft") for key in model.calls)
    assert store.checkpoint(job_id, "complete")["stopReason"] == "no_new_queries"
    assert store.retry(job_id) == 1
    assert store.checkpoint(job_id, "complete") is None
    assert store.checkpoint(job_id, "analyze/0")


def test_publication_recovery_keeps_existing_candidate_and_confirms_index(workspace, monkeypatch):
    repo, store, snapshot, runtime = workspace
    job_id = registered(store, snapshot)
    real_sync = repo.sync

    def interrupted(key=None):
        if key:
            raise OSError("Synthetic index interruption")
        return real_sync(key)

    monkeypatch.setattr(repo, "sync", interrupted)
    model = FixedModel()
    assert BatchWorker(store, model, repo, runtime).run(1)["paused"]
    assert store.job(job_id)["status"] == "publishing"
    file = repo.root / f"data/translation/jobs/{job_id}/candidate.md"
    before = file.read_bytes()
    calls = len(model.calls)
    monkeypatch.setattr(repo, "sync", real_sync)
    monkeypatch.setattr(
        "docling_desk.wiki_batch.worker.candidate",
        lambda *a: pytest.fail("Pending candidate must be reused"),
    )
    store.set("paused", 0)
    assert BatchWorker(store, model, repo, runtime).run(1)["outcome"]["completed"] == 1
    assert file.read_bytes() == before
    assert len(model.calls) == calls


@pytest.mark.parametrize("field", ["original_path", "ja_path"])
def test_hand_edits_are_preserved(workspace, field):
    repo, store, snapshot, runtime = workspace
    job_id = registered(store, snapshot)
    file = repo.root / snapshot["page"][field]
    file.write_text(file.read_text() + "\nManual edit\n")
    model = FixedModel()
    assert BatchWorker(store, model, repo, runtime).run(1)["outcome"]["needsReview"] == 1
    assert model.calls == []
    assert file.read_text().endswith("Manual edit\n")
    assert store.job(job_id)["status"] == "needs_review"


def test_transport_cache_retry_headers_unknown_recovery_and_secret_scrub(workspace):
    repo, store, snapshot, runtime = workspace
    job_id = registered(store, snapshot)
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        if len(calls) == 1:
            return httpx.Response(429, headers={"retry-after": "12"}, content=b"rate limited")
        return httpx.Response(200, json={"status": "completed", "output_text": '{"ok":true}'})

    config = {
        "endpoint": "https://test.example/openai/v1/responses",
        "apiKey": "secret-test-key",
        "draft": "draft",
        "verify": "verify",
    }
    client = AzureClient(store, config, runtime, httpx.MockTransport(handler))
    request = {"instructions": "Test", "input": "{}", "maxOutputTokens": 2048}
    with pytest.raises(RetryLater) as exc:
        client.request("draft", request, job_id + "/test", job_id)
    assert exc.value.deadline == 13000
    assert client.request("draft", request, job_id + "/test", job_id) == '{"ok":true}'
    assert runtime.now() == 13000
    assert client.request("draft", request, job_id + "/test", job_id) == '{"ok":true}'
    assert len(calls) == 2
    assert calls[1]["store"] is False and calls[1]["model"] == "draft"
    store.db.execute(
        "INSERT INTO attempts(id,job_id,call_key,role,started_at,status,reserved) VALUES('pending',?,'unknown','draft',?,'pending',2048)",
        (job_id, runtime.now()),
    )
    store.recover(runtime.now())
    assert store.number("blocked_until") == runtime.now() + 210000
    assert (
        store.db.execute("SELECT status FROM attempts WHERE id='pending'").fetchone()[0]
        == "unknown"
    )
    assert "secret-test-key" not in client.scrub("secret-test-key https://test.example")


def test_trial_selection_and_single_worker_survive_reopen(workspace):
    repo, store, snapshot, runtime = workspace
    for i in range(5):
        next_snapshot = copy.deepcopy(snapshot)
        next_snapshot["page"]["key"] = f"requirements/R-{i}"
        next_snapshot["inputHash"] = hash_text(str(i))
        registered(store, next_snapshot)
    first = store.trial(runtime.now())
    assert len(first["jobIds"]) == 3
    with worker_lock(repo.root):
        with pytest.raises(ValueError, match="既に実行中"):
            with worker_lock(repo.root):
                pytest.fail("Duplicate worker")
    reopened = BatchStore(repo.root)
    try:
        assert reopened.trial(runtime.now())["jobIds"] == first["jobIds"]
    finally:
        reopened.close()


def test_json_utf16_case_sensitive_term_occurrences_and_markdown_guards():
    assert (
        js_json({"10": "ten", "2": "two", "a": "日本😀", "n": -0.0})
        == '{"2":"two","10":"ten","a":"日本😀","n":0}'
    )
    entry = {
        "id": "avg",
        "senseId": "one",
        "source": {"text": "AVG", "caseSensitive": True},
        "aliases": [],
        "definition": "Test meaning",
        "preferredJa": None,
        "acceptedJa": [],
        "disallowedJa": [],
        "domain": "test",
        "appliesWhen": "context",
        "conditional": True,
        "status": "draft",
        "sources": [],
    }
    registry = validate_registry({"schemaVersion": 1, "entries": [entry]})
    units = split_markdown(
        "# Title\n\n😀 AVG avg `AVG` [link](https://test/AVG)\n", "Title", "", False
    )
    terms = collect_terms(registry, units)
    assert len(terms["occurrences"]) == 1
    assert terms["occurrences"][0]["start"] == 3
    assert initial_resolution(terms)["decisions"][0]["entryKey"] is None
    heading = {"id": "heading", "kind": "heading", "text": "## Keep", "keep": {}}
    with pytest.raises(NeedsReview, match="階層"):
        validate_translation(
            {"translations": [{"id": "heading", "text": "Missing heading"}]}, {"units": [heading]}
        )
    number = {
        "id": "number",
        "kind": "paragraph",
        "text": "Keep __KEEP_0__",
        "keep": {"__KEEP_0__": "60"},
    }
    with pytest.raises(NeedsReview):
        validate_translation(
            {"translations": [{"id": "number", "text": "Keep 61"}]}, {"units": [number]}
        )


def test_workspace_rejects_symlink_and_path_traversal(workspace):
    repo, _, _, _ = workspace
    with pytest.raises(ValueError):
        safe_file(repo.root, "../other.md")
    (repo.root / "link.md").symlink_to(repo.root / repo.pages[0]["original_path"])
    with pytest.raises(ValueError):
        safe_file(repo.root, "link.md")
    with pytest.raises(ValueError):
        safe_file(repo.root, "wiki/pages/original/requirements/r-1.md")
    repo.sync()
    assert not any(s.get("language") == "ja" for s in Store(repo.data).chunks())


def test_import_workspace_copies_frozen_history_and_refuses_active_source(workspace, tmp_path):
    from docling_desk.wiki_batch.migration import import_workspace

    repo, store, snapshot, _ = workspace
    job_id = registered(store, snapshot)
    target = tmp_path / "copied"
    target.mkdir()
    with worker_lock(repo.root):
        with pytest.raises(NeedsReview, match="実行中"):
            import_workspace(repo.root, target, tmp_path / "copied-index")
    assert not list(target.iterdir())
    result = import_workspace(repo.root, target, tmp_path / "copied-index")
    assert result["translation_history"] and result["paused"]
    migrated = BatchStore(target)
    try:
        assert migrated.job(job_id)["payload"] == store.job(job_id)["payload"]
        assert migrated.setting("paused") == "1"
    finally:
        migrated.close()
    assert (target / snapshot["page"]["original_path"]).read_bytes() == (
        repo.root / snapshot["page"]["original_path"]
    ).read_bytes()
    assert not (target / ".env").exists()
    with pytest.raises(ValueError, match="空"):
        import_workspace(repo.root, target, tmp_path / "copied-index")


def test_prepare_build_preserves_export_and_fixed_ids(tmp_path):
    from docling_desk.wiki_batch import preparation

    root = tmp_path / "new"
    source = root / "requirements"
    source.mkdir(parents=True)
    raw = "# Requirement title\n\nID: R-1\n\nRequirement body\n"
    (source / "requirement.md").write_text(raw)
    preparation.configure(root)
    preparation.migrate()
    preparation.configure(root)
    result = preparation.build()
    assert result["pages_per_language"] == 1
    assert (root / "sources/notion/requirements/requirement.md").read_text() == raw
    assert preparation.check()["source_bytes_preserved"]
    repo = Repository(root, tmp_path / "prepared-index")
    assert repo.sync()["pages"] == 1
    articles = Store(repo.data).sources("wiki")
    assert any(s["path"] == "wiki/pages/index.md" for s in articles)
    assert any(s["path"].startswith("wiki/pages/folders/") for s in articles)


def test_stop_after_draft_resumes_saved_packet_without_redrafting(workspace):
    repo, store, snapshot, runtime = workspace
    job_id = registered(store, snapshot)
    stopped = [False]
    runtime.stopping = lambda: stopped[0]

    class StopModel(FixedModel):
        def request(self, role, request, key, job_id):
            result = super().request(role, request, key, job_id)
            if key.endswith("/draft"):
                stopped[0] = True
            return result

    model = StopModel()
    BatchWorker(store, model, repo, runtime).run(1)
    assert store.job(job_id)["status"] == "queued"
    assert store.packet(job_id, 0)["draft"]
    assert not store.packet(job_id, 0)["verification"]
    stopped[0] = False
    model = FixedModel()
    assert BatchWorker(store, model, repo, runtime).run(1)["outcome"]["completed"] == 1
    assert not any(key.endswith("/draft") or "/research/" in key for key in model.calls)


def test_publication_cannot_complete_when_index_declines_confirmation(workspace, monkeypatch):
    repo, store, snapshot, runtime = workspace
    job_id = registered(store, snapshot)
    real_sync = repo.sync

    def unconfirmed(key=None):
        result = real_sync(key)
        if key:
            result["indexed"] = False
        return result

    monkeypatch.setattr(repo, "sync", unconfirmed)
    assert BatchWorker(store, FixedModel(), repo, runtime).run(1)["paused"]
    assert store.job(job_id)["status"] == "publishing"
    assert (repo.root / "data/translation/publication.json").exists()


def test_bounded_term_decisions_require_external_source_and_approved_wording():
    from docling_desk.wiki_batch.terminology import (
        parse_decisions,
        term_issues,
        validate_resolution,
    )

    entry = {
        "id": "avg",
        "senseId": "clinical",
        "source": {"text": "AVG", "caseSensitive": True},
        "aliases": [],
        "definition": "Access graft",
        "preferredJa": "人工血管",
        "acceptedJa": [],
        "disallowedJa": ["平均値"],
        "domain": "clinical",
        "appliesWhen": "Access",
        "conditional": True,
        "status": "verified",
        "sources": [{"url": "https://example.com/reference", "locator": "definition"}],
    }
    registry = validate_registry({"schemaVersion": 1, "entries": [entry]})
    units = split_markdown("# Title\n\nAVG access is inspected.\n", "Title", "", False)
    terms = collect_terms(registry, units)
    occurrence = terms["occurrences"][0]
    assert not initial_resolution(terms)["decisions"]
    value = {
        "unresolved": [],
        "decisions": [
            {
                "occurrenceId": occurrence["id"],
                "entryKey": "avg/clinical",
                "reason": "Source access context",
                "citations": ["term:avg/clinical"],
            }
        ],
    }
    with pytest.raises(NeedsReview, match="出典"):
        parse_decisions(value, terms, [occurrence], {"term:avg/clinical", "source"})
    value["decisions"][0]["citations"].append("source")
    resolution = {
        "registryHash": terms["registryHash"],
        "decisions": parse_decisions(value, terms, [occurrence], {"term:avg/clinical", "source"}),
    }
    validate_resolution(terms, resolution)
    unit = next(u for u in units if u["id"] in occurrence["unitIds"])
    assert term_issues(terms, resolution, units, [{"id": unit["id"], "text": "平均値を確認する。"}])
    assert not term_issues(
        terms, resolution, units, [{"id": unit["id"], "text": "人工血管を確認する。"}]
    )


def test_cli_registration_and_status_are_offline_and_reject_ignored_options(
    workspace, monkeypatch, capsys
):
    from docling_desk.wiki_batch import __main__ as cli

    repo, _, _, _ = workspace
    monkeypatch.setattr(cli.signal, "signal", lambda *a: None)
    monkeypatch.setenv("AZURE_OPENAI_LUNA_DEPLOYMENT", "draft")
    monkeypatch.setenv("AZURE_OPENAI_SOL_DEPLOYMENT", "verify")
    monkeypatch.setattr(
        cli, "AzureClient", lambda *a: pytest.fail("Offline command must not create a client")
    )
    common = ["--root", str(repo.root), "--data", str(repo.data)]
    assert cli.main(["enqueue", *common, "--limit", "1"]) == 0
    assert json.loads(capsys.readouterr().out)["added"] == 1
    assert cli.main(["status", *common]) == 0
    assert json.loads(capsys.readouterr().out)["counts"]["queued"] == 1
    with pytest.raises(SystemExit):
        cli.main(["run", *common, "--key", "requirements/R-1"])


def test_provider_six_attempt_limit_and_authentication_errors(workspace):
    from docling_desk.wiki_batch.files import FatalProviderError

    _, store, snapshot, runtime = workspace
    job_id = registered(store, snapshot)
    config = {
        "endpoint": "https://test.example/openai/v1/responses",
        "apiKey": "secret",
        "draft": "draft",
        "verify": "verify",
    }
    calls = []

    def unauthorized(request):
        calls.append(request)
        return httpx.Response(401, text="secret")

    client = AzureClient(store, config, runtime, httpx.MockTransport(unauthorized))
    request = {"instructions": "Test", "input": "{}", "maxOutputTokens": 2048}
    with pytest.raises(FatalProviderError, match="401"):
        client.request("draft", request, job_id + "/auth", job_id)
    for number in range(6):
        store.db.execute(
            "INSERT INTO attempts(id,job_id,call_key,role,started_at,status,reserved) VALUES(?,?,?,'draft',?,'error',2048)",
            (str(number), job_id, job_id + "/limited", runtime.now()),
        )
    with pytest.raises(NeedsReview, match="上限"):
        client.request("draft", request, job_id + "/limited", job_id)
    assert len(calls) == 1


def test_raw_line_endings_are_preserved_and_manual_change_is_detected(workspace):
    from docling_desk.knowledge.catalog import evidence_body

    repo, store, snapshot, runtime = workspace
    job_id = registered(store, snapshot)
    target = repo.root / snapshot["page"]["ja_path"]
    raw = target.read_bytes().decode("utf-8").replace("\n", "\r\n")
    target.write_bytes(raw.encode("utf-8"))
    assert repo.read(snapshot["page"]["ja_path"])["raw"] == raw
    model = FixedModel()
    assert BatchWorker(store, model, repo, runtime).run(1)["outcome"]["needsReview"] == 1
    assert not model.calls and store.job(job_id)["status"] == "needs_review"
    assert (
        evidence_body("Body mentions ## 書き出し原本 inline.\n\n## 書き出し原本\nArchive")
        == "Body mentions ## 書き出し原本 inline."
    )
