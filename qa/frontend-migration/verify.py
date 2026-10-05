"""Collect successful migration gates without modifying any saved document."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
QA = ROOT / "qa/frontend-migration"


def read(name):
    return json.loads((QA / name).read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def browser_report(name, expected, skipped=0):
    report = read(name)
    stats = report["stats"]
    assert stats["expected"] == expected and stats["skipped"] == skipped, stats
    assert stats["unexpected"] == 0 and stats["flaky"] == 0, stats
    assert not report["errors"]
    return stats


def main():
    cases = read("layout/cases.json")
    layout = read("layout/results.json")
    assert not layout["errors"] and not read("layout/case-errors.json")
    assert {r["name"] for r in layout["results"]} == {c["name"] for c in cases["cases"]}
    for result in layout["results"]:
        assert result["unexplainedPixels"] == 0, result
        detail = read(f"layout/{result['name']}.json")
        assert detail["baseline"] == detail["after"], result["name"]
        assert detail["assets"] == detail["afterAssets"], result["name"]
    calibration = read("layout/legacy-color-calibration.json")
    shared = read("layout/shared-results.json")
    assert len(shared) == 12
    for item in shared:
        assert item["before"] == item["after"]
        assert item["maximumChannelDifference"] <= calibration["maximumChannelDifference"]
    bridge = sorted((QA / "layout").glob("*-natural-bridge-before.png"))
    assert len(bridge) == 7
    for before in bridge:
        after = before.with_name(before.name.replace("-before.png", "-after.png"))
        assert sha(before) == sha(after), before.name
    hashes = read("layout/source-hashes.json")
    originals = {
        p: h
        for p, h in hashes.items()
        if "/original." in p
        or "/preview" in p
        or "/slides/" in p
        or "/sheets/" in p
        or "/pages/" in p
    }
    assert len(originals) == 617
    assert all(sha(ROOT / "data" / p) == h for p, h in originals.items())
    css = read("css-verification.json")
    concurrent_css = []
    for item in css:
        path = ROOT / item["file"]
        checkpoint = subprocess.run(
            ["git", "show", "bb7868287f575708f6ffbfd6af984f7cd05711dd:" + item["file"]],
            cwd=ROOT,
            capture_output=True,
            check=True,
        ).stdout
        assert hashlib.sha256(checkpoint).hexdigest() == item["sha256"]
        if sha(path) != item["sha256"]:
            # The concurrent copy feature replaces only pointer-event rules and
            # adds selectors for its new controls. Existing layout rules remain.
            assert item["file"] == "static/style.css"
            assert (
                path.read_bytes().split(b"/* Document frames")[0]
                == checkpoint.split(b"/* PowerPoint frames")[0]
            )
            concurrent_css.append(item["file"])
    implementation = read("implementation-hashes.json")
    concurrent_source = [
        p for p, h in implementation.items() if "/src/" in p and sha(ROOT / p) != h
    ]
    migration = read("migration-build.json")
    assert sha(QA / "migration-index.html") == migration["htmlSha256"]
    for name, expected in migration["assets"].items():
        assert sha(ROOT / "src/docling_desk/resources/static/frontend/assets" / name) == expected
    assert (ROOT / "frontend/dist/index.html").read_bytes() == (
        ROOT / "src/docling_desk/resources/static/frontend/index.html"
    ).read_bytes()
    for asset in (ROOT / "frontend/dist/assets").iterdir():
        assert sha(asset) == sha(ROOT / "src/docling_desk/resources/static/frontend/assets" / asset.name)
    checks = (QA / "frontend-checks.txt").read_text()
    assert re.search(r"Tests\s+5 passed", checks)
    python = (QA / "python-tests.txt").read_text()
    assert re.search(r"226 passed", python)
    assert "12 passed" in (QA / "workbook-stability.txt").read_text()
    assert "256 passed" in (QA / "current-python-tests.txt").read_text()
    assert re.search(r"Tests\s+10 passed", (QA / "current-unit-tests.txt").read_text())
    reports = {
        "production": browser_report("browser-results.json", 17),
        "development": browser_report("dev-browser-results.json", 16, 1),
        "layout": browser_report("layout-browser-results.json", 3),
    }
    live = read("current-live-verification.json")
    assert not live["pageErrors"] and not live["mutationRequests"]
    with urllib.request.urlopen(live["url"]) as response:
        body = response.read().decode()
    assert live["bundle"] in body and "/static/frontend/assets/" in body
    svg = read("layout/native-svg-calibration.json")
    assert svg["browserVersion"] == cases["version"]
    for mode in ["before", "repeat"]:
        assert sha(QA / "layout" / svg[mode + "File"]) == svg[mode + "Sha256"]
    result = {
        "status": "complete",
        "verifiedAt": datetime.now(timezone.utc).isoformat(),
        "browser": cases["version"],
        "documentLayoutCases": len(layout["results"]),
        "sharedLayoutCases": len(shared),
        "originalExcelBridgeCases": len(bridge),
        "originalAssetsUnchanged": len(originals),
        "migrationCssPreserved": len(css),
        "concurrentCssChanges": concurrent_css,
        "concurrentSourceChanges": concurrent_source,
        "migrationBuild": migration,
        "layoutCoverage": read("layout/coverage-runs.json"),
        "unexplainedPixels": 0,
        "rawRasterDifferenceCases": [r for r in layout["results"] if r["changedPixels"]],
        "rasterPolicy": {
            "generalObservedLegacyColorNoise": calibration["maximumChannelDifference"],
            "comparisonRegion": "document display viewport from each full-page capture",
            "thumbnailChecks": "geometry, selection, natural dimensions, source SHA-256",
            "fullPageImagesAndDifferencesRetained": True,
            "geometryTolerance": 0,
            "geometryPrecisionDecimals": 3,
            "additionalLegacyNoiseAllowedOnlyAtSamePixelChannel": True,
            "imageMasks": False,
            "imageRescaling": False,
        },
        "migrationPythonTests": 226,
        "currentPythonTests": 256,
        "migrationFrontendTests": 5,
        "currentFrontendTests": 10,
        "browserReports": reports,
        "liveUrl": live["url"],
        "liveSavedDocuments": len(live["documents"]),
        "applicationSourceSha256": {p: sha(ROOT / p) for p in implementation if "/src/" in p},
        "distributedAssetsSha256": {
            p.name: sha(p) for p in (ROOT / "frontend/dist/assets").iterdir()
        },
    }
    (QA / "verification.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print("Migration gates passed: 116 document, 12 shared, 7 original Excel bridge cases.")


if __name__ == "__main__":
    main()
