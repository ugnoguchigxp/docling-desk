"""R20 distribution inventory: classification evidence, ledger integrity and notices."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import tomllib
from importlib import metadata
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

from docling_desk.operations.supply import (
    _base_images,
    distributed,
    inventory,
    unreviewed_distribution,
)

ROOT = Path(__file__).resolve().parents[1]
REVIEWER = "Claude (自動棚卸し, 人による確認未了)"


@pytest.fixture(scope="module")
def report():
    return inventory(ROOT)


@pytest.fixture(scope="module")
def ledger():
    return json.loads((ROOT / "supply/clearance.json").read_text(encoding="utf-8"))["records"]


def _closure(requirements: list[str]) -> set[str]:
    installed = {canonicalize_name(d.metadata["Name"]): d for d in metadata.distributions()}
    seen: set[tuple[str, tuple[str, ...]]] = set()
    pending = [Requirement(item) for item in requirements]
    while pending:
        requirement = pending.pop()
        name = canonicalize_name(requirement.name)
        key = (name, tuple(sorted(requirement.extras)))
        if key in seen:
            continue
        seen.add(key)
        dist = installed.get(name)
        if dist is None:
            continue
        for text in dist.requires or []:
            child = Requirement(text)
            extras = list(requirement.extras) or [""]
            if child.marker is None or any(child.marker.evaluate({"extra": e}) for e in extras):
                pending.append(child)
    return {name for name, _ in seen}


def test_python_scopes_follow_pyproject_and_classification(report):
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    packages = {
        canonicalize_name(p["name"]): p for p in report["packages"] if p["ecosystem"] == "pypi"
    }
    for name in ("pytest", "iniconfig", "ruff", "ty", "reportlab"):
        assert packages[name]["scope"] == "development"
    assert packages["pypdfium2"]["scope"] == "runtime" and packages["pypdfium2"]["direct"] is True
    assert "pymupdf" not in packages and "fonttools" not in packages
    assert packages["fastapi"]["direct"] is True and packages["anyio"]["direct"] is False
    assert packages["azure-identity"]["scope"] == "optional"
    mcp = packages["mcp"]
    assert mcp["scope"] == "optional" and "docker" not in mcp["distribution"]
    assert project["optional-dependencies"]["mcp"] == ["mcp==1.26.0"]
    # darwin-only pins never reach the Linux image
    for name in ("ocrmac", "pyobjc-core", "pyobjc-framework-vision"):
        assert "docker" not in packages[name]["distribution"]
        assert packages[name]["platform"] == "darwin"
    assert "docker" in packages["torch"]["distribution"]


def test_development_names_are_not_runtime_requirements():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    if "pytest" not in {canonicalize_name(d.metadata["Name"]) for d in metadata.distributions()}:
        pytest.skip("development environment not installed")
    runtime = _closure(project["dependencies"])
    classes = json.loads((ROOT / "supply/classification.json").read_text(encoding="utf-8"))
    development = {canonicalize_name(n) for n in classes["python"]["development"]["names"]}
    assert not development & runtime, "a runtime requirement needs a development-classified name"
    declared_dev = {
        canonicalize_name(Requirement(item).name)
        for item in project["optional-dependencies"]["dev"]
    }
    assert declared_dev <= development


def test_undeclared_runtime_gap_is_still_real():
    """marko and opencv are used at runtime yet not required by the declared extras.

    If pyproject.toml starts requiring them this test fails: then move them out of
    classification.json python.undeclared_runtime."""
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    if "docling-slim" not in {
        canonicalize_name(d.metadata["Name"]) for d in metadata.distributions()
    }:
        pytest.skip("docling-slim not installed")
    runtime = _closure(project["dependencies"])
    classes = json.loads((ROOT / "supply/classification.json").read_text(encoding="utf-8"))
    for name in classes["python"]["undeclared_runtime"]["names"]:
        assert canonicalize_name(name) not in runtime
    assert "import marko" in (ROOT / "docling/docling/backend/md_backend.py").read_text(
        encoding="utf-8"
    )


def test_build_only_npm_packages_are_not_runtime(report):
    npm = {p["name"]: p for p in report["packages"] if p["ecosystem"] == "npm"}
    classes = json.loads((ROOT / "supply/classification.json").read_text(encoding="utf-8"))
    declared = json.loads((ROOT / "frontend/package.json").read_text(encoding="utf-8"))
    for name in classes["npm"]["build_only_roots"]["names"]:
        assert name in declared["dependencies"]
        assert npm[name]["scope"] == "build" and npm[name]["distribution"] == ["build"]
        assert not distributed(npm[name])
    assert npm["react"]["scope"] == "runtime" and "frontend" in npm["react"]["distribution"]
    # a package also needed by shipped code must stay shipped
    assert npm["tslib"]["scope"] in {"runtime", "development"}
    build = {p["name"] for p in npm.values() if p["scope"] == "build"}
    assert {"tailwindcss", "@tailwindcss/vite"} <= build


def test_bun_runtime_is_the_knowledge_api_image_only(report):
    bun = {p["name"]: p for p in report["packages"] if p["ecosystem"] == "bun"}
    assert bun["hono"]["distribution"] == ["knowledge-api"]
    assert bun["@biomejs/biome"]["scope"] == "development"
    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")
    assert "!knowledge-api" not in dockerignore
    manifest = (ROOT / "MANIFEST.in").read_text(encoding="utf-8")
    assert not re.search(r"^\S+\s+(?:\./)?knowledge-api/", manifest, re.M)
    dockerfile = (ROOT / "knowledge-api/Dockerfile").read_text(encoding="utf-8")
    assert "bun install --frozen-lockfile --production" in dockerfile


def test_unused_candidate_has_no_reference_in_frontend():
    classes = json.loads((ROOT / "supply/classification.json").read_text(encoding="utf-8"))
    for item in classes["npm"]["build_only_roots"]["unused_candidates"]:
        name = item["name"]
        hits = []
        for path in (ROOT / "frontend").rglob("*"):
            if (
                not path.is_file()
                or "node_modules" in path.parts
                or "dist" in path.parts
                or path.name in {"pnpm-lock.yaml", "package.json"}
                or path.suffix in {".png", ".jpg", ".woff2"}
            ):
                continue
            try:
                if name in path.read_text(encoding="utf-8"):
                    hits.append(path.relative_to(ROOT).as_posix())
            except UnicodeDecodeError:
                continue
        assert not hits, f"{name} is referenced: {hits}"


def test_base_images_cover_every_dockerfile():
    images = _base_images(ROOT)
    finals = {(i["source"], i["image"]) for i in images if i["final"]}
    assert ("Dockerfile", "python:3.12-slim-bookworm") in finals
    assert ("deploy/Dockerfile.gateway", "python:3.12-slim-bookworm") in finals
    assert ("knowledge-api/Dockerfile", "oven/bun:1.4.2-slim") in finals
    build = {i["image"] for i in images if not i["final"]}
    assert "node:24-bookworm-slim" in build
    pnpm = json.loads((ROOT / "frontend/package.json").read_text(encoding="utf-8"))[
        "packageManager"
    ]
    assert f"npm install --global {pnpm}" in (ROOT / "Dockerfile").read_text(encoding="utf-8")


def test_ledger_never_clears_on_inventory_alone(ledger, report):
    seen = set()
    for record in ledger:
        key = (record["name"], record.get("version"), record.get("ecosystem"))
        assert key not in seen, f"duplicate ledger record {key}"
        seen.add(key)
        assert record["status"] in {"inventoried", "confirmed"}
        if record["status"] == "inventoried":
            assert record["reviewer"] == REVIEWER
            assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", record["reviewed_on"])
            assert record["conditions"] and record["remaining"]
    inventoried = {r["name"] for r in ledger if r["status"] == "inventoried"}
    for item in report["packages"] + report["assets"]:
        if item["name"] in inventoried and not any(
            r["status"] == "confirmed" and r["name"] == item["name"] for r in ledger
        ):
            assert item["cleared"] is False


def test_every_distributed_item_has_a_ledger_record(report, ledger):
    recorded = {(r["name"], r.get("version")) for r in ledger} | {(r["name"], None) for r in ledger}
    missing = []
    for item in report["packages"] + [a for a in report["assets"] if a["kind"] != "fixture"]:
        if distributed(item) and (item["name"], item.get("version")) not in recorded:
            missing.append(item["name"])
    assert not missing, missing
    # development-only Python tools are in the lock and the image, so they are recorded too
    assert any(r["name"] == "pytest" for r in ledger)


def test_unreviewed_distribution_lists_what_blocks_release(report):
    rows = unreviewed_distribution(report)
    names = {row["name"] for row in rows}
    assert {"python:3.12-slim-bookworm", "excalidraw fonts"} <= names
    assert "pytest" not in names  # development scope is not a distribution blocker
    assert all(row["review_status"] in {"inventoried", "unreviewed"} for row in rows)
    assert not any("AGPL" in text for r in report["packages"] for text in r.get("conditions", []))


def test_asset_paths_exist_and_vendor_hashes_match():
    assets = json.loads((ROOT / "supply/assets.json").read_text(encoding="utf-8"))["assets"]
    for asset in assets:
        for relative in asset["paths"]:
            if relative.startswith("../"):
                continue
            assert (ROOT / relative).exists(), relative
    vendor = ROOT / "src/docling_desk/resources/static/vendor"
    grid = json.loads((vendor / "manifest.json").read_text(encoding="utf-8"))
    for name, expected in grid["files"].items():
        assert hashlib.sha256((vendor / name).read_bytes()).hexdigest() == expected["sha256"]
    drawio = json.loads((vendor / "drawio/manifest.json").read_text(encoding="utf-8"))
    for name, digest in drawio["files"].items():
        assert hashlib.sha256((vendor / "drawio" / name).read_bytes()).hexdigest() == digest


def test_docling_submodule_matches_the_recorded_revision(ledger):
    project = tomllib.loads((ROOT / "docling/pyproject.toml").read_text(encoding="utf-8"))
    assets = json.loads((ROOT / "supply/assets.json").read_text(encoding="utf-8"))["assets"]
    docling = next(a for a in assets if a["name"] == "docling-slim")
    assert project["project"]["version"] == docling["version"]
    root = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert any(
        item.startswith("docling-slim[") and f"=={docling['version']}" in item
        for item in root["dependencies"]
    )
    try:
        gitlink = subprocess.run(
            ["git", "ls-tree", "HEAD", "docling"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
            timeout=20,
        ).stdout.split()
    except (OSError, subprocess.SubprocessError):
        pytest.skip("git metadata unavailable")
    assert gitlink[1] == "commit" and gitlink[2] == docling["revision"]


def test_notice_covers_the_shipped_third_party_items():
    notice = (ROOT / "NOTICE.md").read_text(encoding="utf-8")
    for needle in (
        "pypdfium2",
        "Atlassian",
        "Plate",
        "Excalifont",
        "openai-codex-cli-bin",
        "oven/bun",
        "tiktoken",
        "inventoried",
    ):
        assert needle in notice, needle
    wiki = (ROOT / "src/docling_desk/wiki_batch/NOTICE.md").read_text(encoding="utf-8")
    assert "licenses/" in wiki and "knowledge-api" in wiki
    for name in ("contextStill.txt", "hono-standard.txt"):
        assert (ROOT / "src/docling_desk/wiki_batch/licenses" / name).is_file()


def test_decisions_do_not_claim_a_scan():
    decisions = json.loads((ROOT / "supply/decisions.json").read_text(encoding="utf-8"))
    assert decisions["advisories"] == []
    assert "実行していない" in decisions["inventory_run"]["result"]


@pytest.mark.parametrize(
    ("marker", "expected"),
    [
        ('sys_platform == "darwin"', True),
        ("sys_platform == 'darwin'", True),
        ('sys_platform != "darwin"', False),
        ('sys_platform == "darwin" or sys_platform == "linux"', False),
        ('platform_machine == "arm64" and sys_platform == "darwin"', True),
        ('python_version < "3.12" and sys_platform != "darwin"', False),
        ("", False),
        ("not a marker ((", False),
    ],
)
def test_darwin_only_markers_are_evaluated_not_substring_matched(marker, expected):
    from docling_desk.operations.supply import _darwin_only

    assert _darwin_only(marker) is expected


def test_a_non_darwin_marker_that_mentions_darwin_stays_in_the_image(tmp_path):
    from docling_desk.operations.supply import _python_packages

    lock = tmp_path / "requirements-lock.txt"
    lock.write_text(
        'only-mac==1.0; sys_platform == "darwin"\n'
        'not-mac==1.0; sys_platform != "darwin"\n'
        "plain==1.0\n",
        encoding="utf-8",
    )
    packages = {item["name"]: item for item in _python_packages(lock, tmp_path)}
    assert "docker" not in packages["only-mac"]["distribution"]
    assert packages["only-mac"]["platform"] == "darwin"
    assert "docker" in packages["not-mac"]["distribution"]
    assert packages["not-mac"]["platform"] is None
    assert "docker" in packages["plain"]["distribution"]
