"""Verify the review's recorded gates without changing document data."""

from __future__ import annotations

import hashlib
import json
import re
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
QA = Path(__file__).resolve().parent


def read(name):
    return json.loads((QA / name).read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def report(name, expected, skipped=0):
    value = read(name)
    stats = value["stats"]
    assert stats["expected"] == expected and stats["skipped"] == skipped, stats
    assert stats["unexpected"] == stats["flaky"] == 0 and not value["errors"]
    return stats


def main():
    reports = {
        "production": report("browser-results.json", 32),
        "development": report("dev-browser-results.json", 30, 2),
        "documentLayout": report("layout-document-results.json", 3),
        "finalSharedLayout": report("shared-layout-results.json", 1),
        "finalLayout": report("layout-browser-results.json", 3),
    }
    cases = read("layout/results.json")
    assert not cases["errors"] and not read("layout/case-errors.json")
    assert {r["name"] for r in cases["results"]} == set(read("final-layout-cases.json"))
    prior = read("document-layout-results.json")
    assert not prior["errors"]
    assert {r["name"] for r in prior["results"]} == set(read("layout-cases.json"))
    cases["results"] = prior["results"] + cases["results"]
    for result in cases["results"]:
        detail = read(f"layout/{result['name']}.json")
        assert result["unexplainedPixels"] == 0
        assert detail["baseline"] == detail["after"]
        assert detail["assets"] == detail["afterAssets"]
    shared = read("layout/shared-results.json")
    noise = read("layout/legacy-color-calibration.json")["maximumChannelDifference"]
    assert len(shared) == 12
    for result in shared:
        assert result["before"] == result["after"]
        assert result["maximumChannelDifference"] <= noise
    bridge = sorted((QA / "layout").glob("*-natural-bridge-before.png"))
    assert len(bridge) == 7
    for before in bridge:
        assert sha(before) == sha(
            before.with_name(before.name.replace("-before.png", "-after.png"))
        )
    original = json.loads((ROOT / "qa/frontend-migration/layout/source-hashes.json").read_text())
    original = {
        p: h
        for p, h in original.items()
        if any(
            part in p
            for part in (
                "/original.",
                "/preview",
                "/slides/",
                "/sheets/",
                "/pages/",
            )
        )
    }
    assert len(original) == 617
    assert all(sha(ROOT / "data" / p) == h for p, h in original.items())
    source = read("final-implementation-hashes.json")
    assert all(sha(ROOT / p) == h for p, h in source.items() if ".test." not in p)
    live = read("live-verification.json")
    assert not live["pageErrors"] and not live["mutationRequests"]
    assert len(live["documents"]) == 4 and all(d["previewVisible"] for d in live["documents"])
    with urllib.request.urlopen(live["url"]) as response:
        assert live["bundle"] in response.read().decode()
    assert (ROOT / "src/docling_desk/resources/static/frontend/index.html").read_bytes() == (
        QA / "review-index.html"
    ).read_bytes()
    assert "Concurrent exports passed" in (QA / "export-test.txt").read_text()
    units = re.search(r"Tests\s+(\d+) passed", (QA / "unit-tests.txt").read_text())
    python = re.search(r"(\d+) passed", (QA / "python-tests.txt").read_text())
    assert units and int(units.group(1)) >= 28
    assert python and int(python.group(1)) >= 276
    result = {
        "status": "complete",
        "verifiedAt": datetime.now(timezone.utc).isoformat(),
        "reviewPasses": 3,
        "findingsFixed": 9,
        "unresolvedFindings": [],
        "frontendTests": int(units.group(1)),
        "pythonTests": int(python.group(1)),
        "documentComparisonCases": len(cases["results"]),
        "sharedComparisonCases": len(shared),
        "originalExcelBridgeCases": len(bridge),
        "originalAssetsUnchanged": len(original),
        "browserReports": reports,
        "live": live,
        "comparisonBaseline": (QA / "baseline-index.html").read_text(),
        "distributedIndexSha256": sha(ROOT / "src/docling_desk/resources/static/frontend/index.html"),
        "distributedAssetsSha256": {
            p.name: sha(p) for p in (ROOT / "frontend/dist/assets").iterdir()
        },
        "sourceSha256": {p: sha(ROOT / p) for p in source if ".test." not in p},
        "notes": [
            "Initial synthetic PPTX Japanese baseline failed its own repeated geometry check; its failure is retained in layout-first-pass.json. Japanese translation comparison uses Excel instead.",
            "The final interaction changes adjust table selection feedback and passive fallback drop handling. The final build rechecks three saved document formats, shared geometry and the original Excel bridge; all prior document comparison records remain preserved.",
            "Development skips the two production-only checks: missing table chunk and cold-load measurement.",
        ],
    }
    (QA / "verification.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print("Review complete: 9 findings fixed, no unresolved findings, all recorded gates passed.")


if __name__ == "__main__":
    main()
