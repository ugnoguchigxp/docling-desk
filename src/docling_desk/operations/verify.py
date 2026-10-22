"""Verification entry for this demo. It does not call Azure or copy user documents.

The default run is the lightweight offline check. Broader scopes are chosen
explicitly with ``--scope`` and never widen on their own:

``default``       ruff, pytest, tool guides, model manifest (missing models tolerated).
``browser``       default + JavaScript suites + synthetic browser e2e. The e2e run
                  starts its own QA server and needs an already built frontend bundle
                  (or ``--build-frontend``).
``distribution``  browser + frontend build/export + installed-package check + HTTP
                  acceptance. The pinned models are required, so this is a
                  distribution-candidate check, not a development check.

``--with-js`` keeps its meaning (default + JavaScript suites). ``--require-models``
turns the model check strict in any scope. Docker, Azure and saved-document lanes are
never run here and are reported as not_run.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from docling_desk.config import checkout_root
from docling_desk.operations import distribution_evidence

ROOT = checkout_root()
SCOPES = ("default", "browser", "distribution")
MODELS_COMMAND = ["-m", "docling_desk.operations.models"]
ALL_LANES = (
    "lint",
    "types",
    "python",
    "tool_guides",
    "models",
    "javascript",
    "frontend_build",
    "browser",
    "package",
    "http",
    "clearance",
    "docker",
    "azure",
    "saved_documents",
)


@dataclass(frozen=True)
class Check:
    command: list[str]
    cwd: Path
    lane: str = ""
    env: dict[str, str] = field(default_factory=dict)


def command_result(
    args: list[str], cwd: Path, extra_env: dict[str, str] | None = None, lane: str = ""
) -> dict:
    started = time.time()
    env = os.environ.copy()
    env["DOCLING_ENV_FILE"] = os.devnull
    extra = f"{ROOT / 'src'}{os.pathsep}{ROOT / 'packages/docling-azure-ocr/src'}"
    env["PYTHONPATH"] = extra + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env.update(extra_env or {})
    completed = subprocess.run(args, cwd=cwd, env=env, check=False)
    return {
        "command": args,
        "cwd": str(cwd),
        "lane": lane,
        "exit_code": completed.returncode,
        "seconds": round(time.time() - started, 3),
    }


def is_models_check(result: dict) -> bool:
    return result["command"][-2:] == MODELS_COMMAND


def accepted(result: dict, require_models: bool = False) -> bool:
    if result.get("skipped"):
        return False
    # Exit 3 means the pinned models are absent. A mismatch or invalid manifest is 1.
    # A development check tolerates the absence; a distribution candidate does not.
    if is_models_check(result):
        return result["exit_code"] in ({0} if require_models else {0, 3})
    return result["exit_code"] == 0


def checks(
    include_js: bool,
    scope: str = "default",
    build_frontend: bool = False,
    report_directory: Path | None = None,
) -> list[Check]:
    if scope not in SCOPES:
        raise ValueError(f"unknown scope: {scope}")
    python = sys.executable
    items = [
        Check(
            [python, "-m", "ruff", "check", ".", "--exclude", "docling", "--exclude", "qa"],
            ROOT,
            "lint",
        ),
        Check([python, "-m", "pytest", "tests", "-q"], ROOT, "python"),
        Check(
            [
                python,
                "-m",
                "ty",
                "check",
                "src",
                "packages/docling-azure-ocr/src",
                "--error-on-warning",
            ],
            ROOT,
            "types",
        ),
        Check([python, "-m", "docling_desk.operations.tool_guides"], ROOT, "tool_guides"),
        Check([python, *MODELS_COMMAND], ROOT, "models"),
    ]
    if include_js or scope != "default":
        items.extend(
            [
                Check(["bun", "test"], ROOT / "knowledge-api", "javascript"),
                Check(["bun", "run", "typecheck"], ROOT / "knowledge-api", "javascript"),
                Check(["bun", "run", "lint"], ROOT / "knowledge-api", "javascript"),
                Check(["bun", "run", "contract:check"], ROOT / "knowledge-api", "javascript"),
                Check(["pnpm", "test:run"], ROOT / "frontend", "javascript"),
                Check(["pnpm", "typecheck"], ROOT / "frontend", "javascript"),
                Check(["pnpm", "lint"], ROOT / "frontend", "javascript"),
            ]
        )
    if build_frontend or scope == "distribution":
        items.extend(
            [
                Check(["pnpm", "build"], ROOT / "frontend", "frontend_build"),
                Check(["pnpm", "export:static"], ROOT / "frontend", "frontend_build"),
            ]
        )
    if scope in {"browser", "distribution"}:
        env = {"UI_SYNTHETIC": "1", "DOCLING_PYTHON": python}
        if report_directory is not None:
            env["UI_REPORT_DIRECTORY"] = str(report_directory)
        items.append(Check(["pnpm", "test:e2e:synthetic"], ROOT / "frontend", "browser", env))
    if scope == "distribution":
        items.extend(
            [
                Check(
                    [python, "-m", "docling_desk.operations.supply", "--check-release"],
                    ROOT,
                    "clearance",
                ),
                Check([python, "scripts/verify_package.py"], ROOT, "package"),
                Check(
                    [python, "-m", "pytest", "tests/test_distribution_http.py", "-q"], ROOT, "http"
                ),
            ]
        )
    return items


def not_run_lanes(planned: list[Check]) -> list[str]:
    """Lanes this run does not execute. not_run is never a pass."""
    ran = {check.lane for check in planned}
    return [lane for lane in ALL_LANES if lane not in ran]


def lane_outcomes(results: list[dict], require_models: bool) -> dict[str, str]:
    """Map executed checks to the lane names of distribution_evidence.

    A lane fails if any of its checks failed, is not_run if any was skipped (or the
    models were tolerated as absent), and passes only when every check passed.
    """
    lanes: dict[str, str] = {}
    for item in results:
        lane = item.get("lane")
        if not lane:
            continue
        if item.get("skipped"):
            status = "not_run"
        elif is_models_check(item) and item["exit_code"] == 3 and not require_models:
            status = "not_run"
        else:
            status = "passed" if accepted(item, require_models) else "failed"
        previous = lanes.get(lane)
        rank = {"failed": 2, "not_run": 1, "passed": 0}
        if previous is None or rank[status] > rank[previous]:
            lanes[lane] = status
    return lanes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the demo's verification entry")
    parser.add_argument("--with-js", action="store_true")
    parser.add_argument(
        "--scope",
        choices=SCOPES,
        default="default",
        help="default is the lightweight offline run; browser and distribution widen it",
    )
    parser.add_argument(
        "--build-frontend",
        action="store_true",
        help="build and export the frontend before the browser run (distribution always does)",
    )
    parser.add_argument(
        "--require-models",
        action="store_true",
        help="fail when the pinned models are absent (implied by --scope distribution)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "qa/technical-quality/latest/verify.json",
    )
    args = parser.parse_args(argv)
    require_models = args.require_models or args.scope == "distribution"
    profile = "distribution_candidate" if require_models else "dev_check"
    planned = checks(
        args.with_js,
        args.scope,
        args.build_frontend,
        args.output.resolve().parent / "browser",
    )
    bundle = Path(distribution_evidence.ROOT / "src/docling_desk/resources/static/frontend")
    results: list[dict] = []
    for check in planned:
        broken = [r for r in results if r["lane"] == "frontend_build" and not accepted(r)]
        if check.lane == "browser" and broken:
            results.append(
                {
                    "command": check.command,
                    "cwd": str(check.cwd),
                    "lane": check.lane,
                    "exit_code": None,
                    "skipped": "frontend_build_failed",
                    "seconds": 0,
                }
            )
            continue
        if check.lane == "browser" and not distribution_evidence.bundle_record(bundle)["ok"]:
            results.append(
                {
                    "command": check.command,
                    "cwd": str(check.cwd),
                    "lane": check.lane,
                    "exit_code": None,
                    "skipped": "frontend_bundle_missing_or_broken; build it or use --build-frontend",
                    "seconds": 0,
                }
            )
            continue
        results.append(command_result(check.command, check.cwd, check.env, check.lane))
    report = {
        "scope": args.scope,
        "profile": profile,
        "results": results,
        "not_run": not_run_lanes(planned),
        "source": distribution_evidence.source_identity(ROOT),
        "ok": all(accepted(item, require_models) for item in results),
    }
    if args.scope == "distribution":
        lanes = lane_outcomes(results, require_models)
        record = distribution_evidence.evidence(
            report["source"]["commit"],
            distribution_evidence.bundle_record(bundle),
            lanes,
            source=report["source"],
            candidate=True,
        )
        evidence_path = args.output.with_name("distribution-evidence.json")
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        evidence_path.write_text(
            json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        report["distribution_evidence"] = str(evidence_path)
        report["ok"] = report["ok"] and record["ok"]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
