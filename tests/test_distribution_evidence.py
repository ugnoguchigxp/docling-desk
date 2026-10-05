import json

from docling_desk.operations.distribution_evidence import bundle_record, evidence


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
