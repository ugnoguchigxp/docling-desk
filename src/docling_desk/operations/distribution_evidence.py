"""Record which distribution checks ran for one commit. A skipped lane is not a pass."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from docling_desk.config import checkout_root

ROOT = checkout_root()
ASSET = re.compile(r"""(?:src|href)=["'](/static/frontend/assets/[^"']+)["']""")
REQUIRED = ("frontend_build", "browser", "http")
OPTIONAL = ("models", "clearance", "docker", "azure", "saved_documents")
# A distribution candidate also needs the pinned models. A development check
# tolerates their absence and records the lane as not_run.
CANDIDATE_REQUIRED = (*REQUIRED, "models", "clearance")
# Evidence and plans are outputs of a run, not part of the source it describes.
DIGEST_EXCLUDED = ("docs/refactoring/", "qa/refactoring/")
LOCKFILES = (
    "requirements-lock.txt",
    "frontend/pnpm-lock.yaml",
    "knowledge-api/bun.lock",
)
MODEL_MANIFEST = "src/docling_desk/resources/models/manifest.json"


def bundle_record(bundle: Path) -> dict:
    index = bundle / "index.html"
    if not index.is_file():
        return {"ok": False, "problems": ["index_missing"], "sha256": None, "assets": []}
    text = index.read_text(encoding="utf-8")
    problems = []
    assets = []
    digest = hashlib.sha256()
    digest.update(index.read_bytes())
    for match in ASSET.finditer(text):
        relative = match.group(1).removeprefix("/static/frontend/")
        path = bundle / relative
        if not path.is_file():
            problems.append(f"asset_missing:{relative}")
            continue
        file_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        digest.update(file_hash.encode())
        assets.append({"path": relative, "sha256": file_hash})
    print_shell = bundle / "print.html"
    if not print_shell.is_file():
        problems.append("print_missing")
    elif "<script" in print_shell.read_text(encoding="utf-8"):
        problems.append("print_has_script")
    return {
        "ok": not problems,
        "problems": problems,
        "sha256": digest.hexdigest(),
        "assets": assets,
    }


def git_commit(root: Path) -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        return os.environ.get("GITHUB_SHA", "unknown")
    return completed.stdout.strip()


def _git(root: Path, *args: str) -> str | None:
    try:
        completed = subprocess.run(
            ["git", *args], cwd=root, check=False, capture_output=True, text=True
        )
    except OSError:
        return None
    return completed.stdout if completed.returncode == 0 else None


def _file_sha256(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def source_digest(root: Path, excluded: tuple[str, ...] = DIGEST_EXCLUDED) -> dict:
    """Identify the working tree, uncommitted edits included.

    Method (same as docs/refactoring/baseline.json): SHA256 of
    json.dumps(rows sorted by path, sort_keys=True) where a row is
    {path, sha256, bytes}. Files are `git ls-files --cached --others
    --exclude-standard`; symlinks, gitlinks (the docling submodule), missing
    paths and an actual .env are left out. `excluded` path prefixes are left
    out too so that writing evidence does not change the digest it names.
    """
    listing = _git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    if listing is None:
        return {"sha256": None, "file_count": 0, "method": "git_unavailable"}
    rows = []
    for relative in sorted(set(filter(None, listing.split("\0")))):
        path = root / relative
        if relative.startswith(excluded) or path.name == ".env":
            continue
        if path.is_symlink() or not path.is_file():
            continue
        data = path.read_bytes()
        rows.append(
            {"path": relative, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
        )
    rows.sort(key=lambda row: row["path"])
    return {
        "sha256": hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest(),
        "file_count": len(rows),
        "excluded_prefixes": list(excluded),
        "method": "sha256(json.dumps(sorted {path,sha256,bytes} rows, sort_keys=True))",
    }


def source_identity(root: Path = ROOT) -> dict:
    """Commit, uncommitted state, source digest, submodule and pinned inputs.

    Values are hashes and revisions only; no environment values or secrets.
    """
    commit = git_commit(root)
    status = _git(root, "status", "--short", "--untracked-files=all")
    dirty_paths = [
        line[3:] for line in (status or "").splitlines() if not line[3:].startswith(DIGEST_EXCLUDED)
    ]
    submodule = _git(root, "-C", "docling", "rev-parse", "HEAD")
    recorded = _git(root, "ls-tree", "HEAD", "docling")
    return {
        "commit": commit,
        "dirty": bool(dirty_paths) if status is not None else None,
        "dirty_file_count": len(dirty_paths) if status is not None else None,
        "digest": source_digest(root),
        "docling_commit": (submodule or "").strip() or None,
        "docling_recorded_commit": recorded.split()[2] if recorded else None,
        "model_manifest_sha256": _file_sha256(root / MODEL_MANIFEST),
        "lockfiles": {name: _file_sha256(root / name) for name in LOCKFILES},
    }


def lane_status(value: str) -> str:
    if value in {"passed", "success"}:
        return "passed"
    if value in {"failed", "failure"}:
        return "failed"
    if value in {"not_run", "skipped", "cancelled"}:
        return "not_run"
    return "failed"


def evidence(
    commit: str,
    bundle: dict,
    lanes: dict[str, str],
    *,
    source: dict | None = None,
    candidate: bool = False,
) -> dict:
    """`candidate` makes the pinned-model lane required (distribution candidate).

    A candidate must also come from a clean tree: when ``source`` says the tree is
    dirty (or cannot say), ``source_problems`` records it and ``ok`` is False.
    """
    normalized = {}
    for name in (*REQUIRED, *OPTIONAL):
        status = lane_status(lanes.get(name, "not_run"))
        if name == "frontend_build" and status == "passed" and not bundle.get("ok"):
            status = "failed"
        normalized[name] = {"status": status}
    required = CANDIDATE_REQUIRED if candidate else REQUIRED
    report = {
        "commit": commit,
        "profile": "distribution_candidate" if candidate else "dev_check",
        "required_lanes": list(required),
        "bundle_sha256": bundle.get("sha256"),
        "bundle_problems": bundle.get("problems", []),
        "lanes": normalized,
        "note": "not_run は成功ではありません。docker、azure、保存済み実資料は別レーンです。",
    }
    problems: list[str] = []
    if source is not None:
        report["source"] = source
        if candidate and source.get("dirty") is True:
            problems.append("source_dirty")
        elif candidate and source.get("dirty") is None:
            problems.append("source_state_unknown")
    report["source_problems"] = problems
    required_ok = all(normalized[name]["status"] == "passed" for name in required)
    report["ok"] = (
        bool(bundle.get("ok"))
        and required_ok
        and bool(commit)
        and commit != "unknown"
        and not problems
    )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write distribution check evidence")
    parser.add_argument(
        "--bundle", type=Path, default=ROOT / "src/docling_desk/resources/static/frontend"
    )
    parser.add_argument("--output", type=Path, default=ROOT / "qa/distribution/latest.json")
    parser.add_argument("--commit", default="")
    parser.add_argument("--frontend-build", default="not_run")
    parser.add_argument("--browser", default="not_run")
    parser.add_argument("--http", default="not_run")
    parser.add_argument("--models", default="not_run")
    parser.add_argument("--clearance", default="not_run")
    parser.add_argument(
        "--candidate",
        action="store_true",
        help="distribution candidate: the pinned models lane must have passed",
    )
    parser.add_argument("--docker", default="not_run")
    parser.add_argument("--azure", default="not_run")
    parser.add_argument("--saved-documents", default="not_run")
    args = parser.parse_args(argv)
    record = bundle_record(args.bundle)
    lanes = {
        "frontend_build": args.frontend_build,
        "browser": args.browser,
        "http": args.http,
        "models": args.models,
        "clearance": args.clearance,
        "docker": args.docker,
        "azure": args.azure,
        "saved_documents": args.saved_documents,
    }
    report = evidence(
        args.commit or git_commit(ROOT),
        record,
        lanes,
        source=source_identity(ROOT),
        candidate=args.candidate,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"ok": report["ok"], "commit": report["commit"]}, ensure_ascii=False))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
