import json
import subprocess

from docling_desk.operations.distribution_evidence import (
    bundle_record,
    evidence,
    source_digest,
    source_identity,
)


def test_missing_bundle_asset_is_not_a_successful_distribution(tmp_path):
    bundle = tmp_path / "frontend"
    assets = bundle / "assets"
    assets.mkdir(parents=True)
    (assets / "app.js").write_text("console.log(1)\n", encoding="utf-8")
    (bundle / "index.html").write_text(
        '<script type="module" src="/static/frontend/assets/app.js"></script>\n',
        encoding="utf-8",
    )
    (bundle / "print.html").write_text("<p>print</p>\n", encoding="utf-8")
    record = bundle_record(bundle)
    assert record["ok"] is True
    (assets / "app.js").unlink()
    broken = bundle_record(bundle)
    assert broken["ok"] is False
    assert any(item.startswith("asset_missing:") for item in broken["problems"])
    report = evidence(
        "abc123",
        broken,
        {
            "frontend_build": "success",
            "browser": "skipped",
            "http": "success",
            "docker": "not_run",
            "azure": "not_run",
            "saved_documents": "not_run",
        },
    )
    assert report["ok"] is False
    assert report["lanes"]["frontend_build"]["status"] == "failed"
    assert report["lanes"]["browser"]["status"] == "not_run"
    assert report["lanes"]["docker"]["status"] == "not_run"
    passed = evidence(
        "abc123",
        record,
        {
            "frontend_build": "success",
            "browser": "success",
            "http": "success",
            "docker": "not_run",
            "azure": "not_run",
            "saved_documents": "not_run",
        },
    )
    assert passed["ok"] is True
    assert json.dumps(passed)


def _git(root, *args):
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def test_source_digest_identifies_uncommitted_content_and_leaves_out_evidence(tmp_path):
    _git(tmp_path, "init", "-q")
    (tmp_path / "a.txt").write_text("one\n", encoding="utf-8")
    (tmp_path / ".env").write_text("SECRET=1\n", encoding="utf-8")
    (tmp_path / "link").symlink_to("a.txt")
    _git(tmp_path, "add", "a.txt", "link")
    clean = source_digest(tmp_path)
    assert clean["file_count"] == 1 and len(clean["sha256"]) == 64
    (tmp_path / "docs" / "refactoring").mkdir(parents=True)
    (tmp_path / "docs" / "refactoring" / "ledger.md").write_text("x\n", encoding="utf-8")
    (tmp_path / "qa" / "refactoring").mkdir(parents=True)
    (tmp_path / "qa" / "refactoring" / "run.json").write_text("{}\n", encoding="utf-8")
    assert source_digest(tmp_path) == clean
    (tmp_path / "a.txt").write_text("two\n", encoding="utf-8")
    assert source_digest(tmp_path)["sha256"] != clean["sha256"]
    (tmp_path / "new.txt").write_text("untracked\n", encoding="utf-8")
    assert source_digest(tmp_path)["file_count"] == 2


def test_source_identity_records_revisions_and_hashes_not_environment():
    identity = source_identity()
    assert identity["commit"] and len(identity["digest"]["sha256"]) == 64
    assert identity["model_manifest_sha256"] and set(identity["lockfiles"]) >= {
        "requirements-lock.txt"
    }
    assert "env" not in json.dumps(identity).lower().replace("environment", "")


def test_a_distribution_candidate_needs_the_models_a_dev_check_may_lack():
    bundle = {"ok": True, "sha256": "x", "problems": []}
    lanes = {"frontend_build": "success", "browser": "success", "http": "success"}
    dev = evidence("abc123", bundle, lanes)
    assert dev["ok"] is True and dev["profile"] == "dev_check"
    assert dev["lanes"]["models"]["status"] == "not_run"
    candidate = evidence("abc123", bundle, lanes, candidate=True)
    assert candidate["ok"] is False and candidate["profile"] == "distribution_candidate"
    ready = evidence(
        "abc123", bundle, {**lanes, "models": "passed", "clearance": "passed"}, candidate=True
    )
    assert ready["ok"] is True
    uncleared = evidence("abc123", bundle, {**lanes, "models": "passed"}, candidate=True)
    assert uncleared["ok"] is False
    assert uncleared["lanes"]["clearance"]["status"] == "not_run"


def test_a_candidate_from_a_dirty_or_unknown_tree_is_not_ok():
    bundle = {"ok": True, "sha256": "x", "problems": []}
    lanes = {"frontend_build": "success", "browser": "success", "http": "success"}
    passed = {**lanes, "models": "passed", "clearance": "passed"}
    clean = evidence("abc123", bundle, passed, source={"dirty": False}, candidate=True)
    assert clean["ok"] is True and clean["source_problems"] == []
    dirty = evidence("abc123", bundle, passed, source={"dirty": True}, candidate=True)
    assert dirty["ok"] is False and dirty["source_problems"] == ["source_dirty"]
    unknown = evidence("abc123", bundle, passed, source={"dirty": None}, candidate=True)
    assert unknown["ok"] is False and unknown["source_problems"] == ["source_state_unknown"]
    # A development check records the state but is not refused for it.
    dev = evidence("abc123", bundle, lanes, source={"dirty": True})
    assert dev["ok"] is True and dev["source_problems"] == []
