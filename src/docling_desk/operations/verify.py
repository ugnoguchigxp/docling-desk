"""One offline verification entry for this demo. It does not call Azure or copy user documents."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from docling_desk.config import checkout_root

ROOT = checkout_root()


def command_result(args: list[str], cwd: Path) -> dict:
    started = time.time()
    env = os.environ.copy()
    env["DOCLING_ENV_FILE"] = os.devnull
    extra = f"{ROOT / 'src'}{os.pathsep}{ROOT / 'packages/docling-azure-ocr/src'}"
    env["PYTHONPATH"] = extra + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    completed = subprocess.run(args, cwd=cwd, env=env, check=False)
    return {
        "command": args,
        "cwd": str(cwd),
        "exit_code": completed.returncode,
        "seconds": round(time.time() - started, 3),
    }


def accepted(result: dict) -> bool:
    command = result["command"]
    # Exit 3 means the pinned models are absent. A mismatch or invalid manifest is 1.
    if command[-2:] == ["-m", "docling_desk.operations.models"]:
        return result["exit_code"] in {0, 3}
    return result["exit_code"] == 0


def checks(include_js: bool) -> list[tuple[list[str], Path]]:
    python = sys.executable
    items = [
        (
            [python, "-m", "ruff", "check", ".", "--exclude", "docling", "--exclude", "qa"],
            ROOT,
        ),
        ([python, "-m", "pytest", "tests", "-q"], ROOT),
        ([python, "-m", "docling_desk.operations.models"], ROOT),
    ]
    if include_js:
        items.extend(
            [
                (["bun", "test"], ROOT / "knowledge-api"),
                (["bun", "run", "typecheck"], ROOT / "knowledge-api"),
                (["bun", "run", "lint"], ROOT / "knowledge-api"),
                (["bun", "run", "contract:check"], ROOT / "knowledge-api"),
                (["pnpm", "test:run"], ROOT / "frontend"),
                (["pnpm", "typecheck"], ROOT / "frontend"),
                (["pnpm", "lint"], ROOT / "frontend"),
            ]
        )
    return items


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the demo's offline verification entry")
    parser.add_argument("--with-js", action="store_true")
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "qa/technical-quality/latest/verify.json",
    )
    args = parser.parse_args(argv)
    results = [command_result(cmd, cwd) for cmd, cwd in checks(args.with_js)]
    report = {"results": results, "ok": all(accepted(item) for item in results)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
