"""Run the documented source setup as one reproducible command; --plan makes no changes."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def commands(python: str, venv: Path, *, models: bool = True) -> list[tuple[Path, list[str]]]:
    interpreter = str(venv / "bin/python")
    frontend = ROOT / "frontend"
    manager = json.loads((frontend / "package.json").read_text())["packageManager"]
    name, version = manager.split("@")
    if shutil.which("corepack"):
        pnpm = ["corepack", manager]
    else:
        pnpm = [name]
        if shutil.which(name):
            installed = subprocess.check_output([name, "--version"], text=True).strip()
            if installed != version:
                raise ValueError(f"{manager} が必要です（現在 {installed}）。")
        else:
            raise ValueError(f"corepack または {manager} を導入してください。")
    steps = []
    if not (venv / "bin/python").is_file():
        steps.append((ROOT, [python, "-m", "venv", str(venv)]))
    steps += [
        (ROOT, ["sh", "scripts/bootstrap-python.sh", interpreter]),
        (
            ROOT,
            [
                interpreter,
                "-m",
                "pip",
                "install",
                "--index-url",
                "https://download.pytorch.org/whl/cpu",
                "torch==2.14.1",
                "torchvision==0.29.1",
            ],
        ),
        (ROOT, [interpreter, "-m", "pip", "install", "-r", "requirements-lock.txt"]),
        (
            ROOT,
            [
                interpreter,
                "-m",
                "pip",
                "install",
                "--no-deps",
                "./docling",
                "./packages/docling-azure-ocr",
            ],
        ),
        (
            ROOT,
            [interpreter, "-m", "pip", "install", "--no-deps", "--no-build-isolation", "-e", "."],
        ),
        (frontend, [*pnpm, "install", "--frozen-lockfile"]),
        (frontend, [*pnpm, "build"]),
        (frontend, [*pnpm, "export:static"]),
        (
            ROOT,
            [
                interpreter,
                "-c",
                "from docling_desk.config import DATA; DATA.mkdir(parents=True, exist_ok=True)",
            ],
        ),
    ]
    if models:
        steps.append((ROOT, [interpreter, "-m", "docling_desk.operations.download_models"]))
        steps.append((ROOT, [interpreter, "-m", "docling_desk.operations.models"]))
    steps.append((ROOT, [interpreter, "-m", "docling_desk", "--diagnose"]))
    return steps


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Docling Desk source setup")
    parser.add_argument("--python", default="python3.12")
    parser.add_argument("--venv", type=Path, default=ROOT / ".venv")
    parser.add_argument("--plan", action="store_true", help="準備手順を表示するだけで実行しない")
    parser.add_argument(
        "--skip-models",
        action="store_true",
        help="モデル取得と整合検査を省略する（準備完了とは扱わない）",
    )
    args = parser.parse_args(argv)
    try:
        steps = commands(args.python, args.venv.expanduser().resolve(), models=not args.skip_models)
        if not args.plan:
            required = (ROOT / ".node-version").read_text().strip()
            actual = (
                subprocess.check_output(["node", "--version"], text=True).strip().removeprefix("v")
            )
            if actual != required:
                raise ValueError(f"Node.js {required} が必要です（現在 {actual}）。")
            if not (ROOT / "docling/pyproject.toml").is_file():
                raise ValueError(
                    "先に git submodule update --init --recursive を実行してください。"
                )
        for index, (cwd, command) in enumerate(steps, 1):
            print(
                json.dumps(
                    {"step": index, "cwd": str(cwd), "command": command, "plan_only": args.plan}
                ),
                flush=True,
            )
            if not args.plan:
                env = dict(os.environ, DOCLING_ENV_FILE=os.devnull)
                subprocess.run(command, cwd=cwd, env=env, check=True)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"準備を完了できませんでした: {error}", file=sys.stderr)
        return 1
    if not args.plan:
        print(
            f"準備を確認しました。{args.venv.expanduser().resolve() / 'bin/python'} -m docling_desk で起動できます。"
            if not args.skip_models
            else "モデル取得は未実施です。起動前に準備を確認してください。"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
