import json
from pathlib import Path

import pytest

from docling_desk.operations import verify

LIGHT = ["lint", "python", "tool_guides", "models"]


def lanes(items):
    return [item.lane for item in items]


def test_default_scope_stays_the_lightweight_offline_run():
    items = verify.checks(False)
    assert lanes(items) == LIGHT
    assert lanes(verify.checks(False, "default")) == LIGHT
    assert not any(item.env for item in items)
    with_js = verify.checks(True)
    assert lanes(with_js)[:4] == LIGHT
    assert set(lanes(with_js)[4:]) == {"javascript"}


def test_wider_scopes_are_explicit_and_additive():
    browser = verify.checks(False, "browser")
    assert lanes(browser)[:4] == LIGHT
    assert "javascript" in lanes(browser) and "browser" in lanes(browser)
    assert "frontend_build" not in lanes(browser)
    assert "package" not in lanes(browser)
    e2e = [item for item in browser if item.lane == "browser"][0]
    assert e2e.command == ["pnpm", "test:e2e:synthetic"]
    assert e2e.env["UI_SYNTHETIC"] == "1"

    built = verify.checks(False, "browser", build_frontend=True)
    assert lanes(built).index("frontend_build") < lanes(built).index("browser")

    distribution = verify.checks(False, "distribution")
    assert {"frontend_build", "browser", "package", "http"} <= set(lanes(distribution))
    assert lanes(distribution).index("frontend_build") < lanes(distribution).index("browser")
    with pytest.raises(ValueError):
        verify.checks(False, "everything")


def test_missing_models_are_tolerated_only_by_a_development_check():
    absent = {"command": ["py", *verify.MODELS_COMMAND], "exit_code": 3}
    mismatch = {"command": ["py", *verify.MODELS_COMMAND], "exit_code": 1}
    assert verify.accepted(absent) is True
    assert verify.accepted(absent, require_models=True) is False
    assert verify.accepted(mismatch) is False
    assert verify.accepted({"command": ["pytest"], "exit_code": 3}) is False
    assert verify.accepted({"command": ["pytest"], "exit_code": None, "skipped": "x"}) is False


def fake_run(models_exit=0, failing=()):
    def run(args, cwd, extra_env=None, lane=""):
        code = models_exit if lane == "models" else (1 if lane in failing else 0)
        return {"command": args, "cwd": str(cwd), "lane": lane, "exit_code": code, "seconds": 0}

    return run


@pytest.fixture
def stubbed(monkeypatch, tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    monkeypatch.setattr(
        verify.distribution_evidence,
        "source_identity",
        lambda root=None: {"commit": "abc123", "dirty": True, "digest": {"sha256": "d" * 64}},
    )
    monkeypatch.setattr(
        verify.distribution_evidence,
        "bundle_record",
        lambda path: {"ok": True, "problems": [], "sha256": "b" * 64, "assets": []},
    )
    return tmp_path


def set_dirty(monkeypatch, dirty):
    monkeypatch.setattr(
        verify.distribution_evidence,
        "source_identity",
        lambda root=None: {"commit": "abc123", "dirty": dirty, "digest": {"sha256": "d" * 64}},
    )


def run_main(tmp_path, *argv):
    output = tmp_path / "out" / "verify.json"
    code = verify.main([*argv, "--output", str(output)])
    return code, json.loads(output.read_text(encoding="utf-8")), output


def test_default_run_reports_scope_profile_source_and_what_did_not_run(monkeypatch, stubbed):
    monkeypatch.setattr(verify, "command_result", fake_run(models_exit=3))
    code, report, _ = run_main(stubbed)
    assert code == 0 and report["ok"] is True
    assert report["scope"] == "default" and report["profile"] == "dev_check"
    assert report["source"]["commit"] == "abc123"
    assert {"javascript", "browser", "package", "http", "docker"} <= set(report["not_run"])
    assert "python" not in report["not_run"]


def test_distribution_scope_requires_models_and_records_evidence(monkeypatch, stubbed):
    monkeypatch.setattr(verify, "command_result", fake_run(models_exit=3))
    code, report, output = run_main(stubbed, "--scope", "distribution")
    assert code == 1 and report["ok"] is False
    assert report["profile"] == "distribution_candidate"
    evidence = json.loads(Path(report["distribution_evidence"]).read_text(encoding="utf-8"))
    assert evidence["profile"] == "distribution_candidate"
    # The check ran and found the models absent: a candidate records that as failed.
    assert evidence["lanes"]["models"]["status"] == "failed"
    assert evidence["lanes"]["docker"]["status"] == "not_run"
    assert evidence["ok"] is False

    monkeypatch.setattr(verify, "command_result", fake_run(models_exit=0))
    set_dirty(monkeypatch, False)
    code, report, _ = run_main(stubbed, "--scope", "distribution")
    evidence = json.loads(Path(report["distribution_evidence"]).read_text(encoding="utf-8"))
    assert code == 0 and report["ok"] is True
    assert evidence["lanes"]["models"]["status"] == "passed"
    assert "docker" in report["not_run"]


def test_distribution_scope_fails_on_a_dirty_tree_even_when_every_lane_passed(monkeypatch, stubbed):
    monkeypatch.setattr(verify, "command_result", fake_run(models_exit=0))
    set_dirty(monkeypatch, True)  # the stub default, spelled out
    code, report, _ = run_main(stubbed, "--scope", "distribution")
    evidence = json.loads(Path(report["distribution_evidence"]).read_text(encoding="utf-8"))
    assert evidence["lanes"]["models"]["status"] == "passed"
    assert evidence["source_problems"] == ["source_dirty"]
    assert evidence["ok"] is False and report["ok"] is False and code == 1


def test_require_models_makes_any_scope_a_candidate_check(monkeypatch, stubbed):
    monkeypatch.setattr(verify, "command_result", fake_run(models_exit=3))
    code, report, _ = run_main(stubbed, "--require-models")
    assert code == 1 and report["profile"] == "distribution_candidate"


def test_browser_run_is_skipped_not_passed_when_the_build_failed(monkeypatch, stubbed):
    monkeypatch.setattr(verify, "command_result", fake_run(failing={"frontend_build"}))
    code, report, _ = run_main(stubbed, "--scope", "browser", "--build-frontend")
    skipped = [item for item in report["results"] if item["lane"] == "browser"]
    assert skipped[0]["skipped"] == "frontend_build_failed" and skipped[0]["exit_code"] is None
    assert code == 1 and report["ok"] is False


def test_browser_run_is_skipped_when_no_bundle_exists(monkeypatch, stubbed):
    monkeypatch.setattr(verify, "command_result", fake_run())
    monkeypatch.setattr(
        verify.distribution_evidence,
        "bundle_record",
        lambda path: {"ok": False, "problems": ["index_missing"]},
    )
    code, report, _ = run_main(stubbed, "--scope", "browser")
    skipped = [item for item in report["results"] if item["lane"] == "browser"]
    assert "frontend_bundle_missing" in skipped[0]["skipped"]
    assert code == 1
