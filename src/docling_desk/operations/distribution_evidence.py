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
OPTIONAL = ("docker", "azure", "saved_documents")


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


def lane_status(value: str) -> str:
    if value in {"passed", "success"}:
        return "passed"
    if value in {"failed", "failure"}:
        return "failed"
    if value in {"not_run", "skipped", "cancelled"}:
        return "not_run"
    return "failed"


def evidence(commit: str, bundle: dict, lanes: dict[str, str]) -> dict:
    normalized = {}
    for name in (*REQUIRED, *OPTIONAL):
        status = lane_status(lanes.get(name, "not_run"))
        if name == "frontend_build" and status == "passed" and not bundle.get("ok"):
            status = "failed"
        normalized[name] = {"status": status}
    report = {
        "commit": commit,
        "bundle_sha256": bundle.get("sha256"),
        "bundle_problems": bundle.get("problems", []),
        "lanes": normalized,
        "note": "not_run は成功ではありません。docker、azure、保存済み実資料は別レーンです。",
    }
    required_ok = all(normalized[name]["status"] == "passed" for name in REQUIRED)
    report["ok"] = bool(bundle.get("ok")) and required_ok and bool(commit) and commit != "unknown"
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
    parser.add_argument("--docker", default="not_run")
    parser.add_argument("--azure", default="not_run")
    parser.add_argument("--saved-documents", default="not_run")
    args = parser.parse_args(argv)
    record = bundle_record(args.bundle)
    lanes = {
        "frontend_build": args.frontend_build,
        "browser": args.browser,
        "http": args.http,
        "docker": args.docker,
        "azure": args.azure,
        "saved_documents": args.saved_documents,
    }
    report = evidence(args.commit or git_commit(ROOT), record, lanes)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"ok": report["ok"], "commit": report["commit"]}, ensure_ascii=False))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
