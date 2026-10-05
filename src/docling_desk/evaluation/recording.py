"""Shared recording rules for performance and semantic reports."""

from __future__ import annotations

import hashlib
import os
import platform
import subprocess
from pathlib import Path

import psutil

from docling_desk.config import checkout_root

ROOT = checkout_root()
SKIP_PARTS = {
    ".git",
    ".venv",
    "node_modules",
    ".cache",
    "data",
    "models",
    ".hf",
    "__pycache__",
    ".knowledge-api",
    "docling",
    ".pytest_cache",
}
SKIP_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".pdf", ".sqlite", ".pyc"}
SKIP_NAMES = {"example-run.json", "example-report.json", ".env", "credentials.json"}

PERCENTILE_METHOD = (
    "nearest-rank: sort successful durations ascending; "
    "index = round((n-1)*fraction), clamped to [0, n-1]. "
    "Descriptive only. Small n is not a confidence bound or a guarantee."
)

PROPOSED_BUDGETS = {
    "status": "proposed",
    "text_search_p95_seconds": 0.3,
    "context_p95_seconds": 0.5,
    "cached_hybrid_p95_seconds": 2.0,
    "rss_sum_bytes": 8 * 1024**3,
    "total_wait_p95_seconds": None,
    "note": (
        "検証計画の提案値。検索、文脈、総待ちを同じ閾値にしない。"
        "採用前であり、範囲内でも本番合格にしない。"
    ),
}

LATENCY_EXCLUDED = ("failure", "empty", "timeout", "cancelled", "wrong_evidence")


def git(*args: str) -> str:
    completed = _git(ROOT, *args)
    if completed.returncode != 0:
        return ""
    return completed.stdout.decode().strip()


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, check=False)


def _unidentified() -> dict:
    return {
        "git_commit": None,
        "worktree_dirty": None,
        "source_tree_sha256": None,
        "hashed_files": 0,
        "hash_covers": None,
        "source_identity": "unavailable",
        "reason": "対象ソースを識別できない",
    }


def _excluded(relative: Path) -> bool:
    if set(relative.parts) & SKIP_PARTS or relative.suffix.lower() in SKIP_SUFFIXES:
        return True
    return relative.name in SKIP_NAMES or "secret" in relative.name.lower()


def source_fingerprint(root: Path | None = None) -> dict:
    """Hash the working tree, including untracked files. HEAD alone is not the hash."""
    root = root or ROOT
    try:
        listed = _git(root, "ls-files", "--cached", "--others", "--exclude-standard", "-z")
        head = _git(root, "rev-parse", "HEAD")
        status = _git(root, "status", "--porcelain", "-z")
    except OSError:
        return _unidentified()
    if listed.returncode != 0 or head.returncode != 0 or status.returncode != 0:
        return _unidentified()
    paths = [os.fsdecode(item) for item in listed.stdout.split(b"\0") if item]
    lines = []
    for rel in sorted(paths):
        relative = Path(rel)
        if _excluded(relative):
            continue
        file = root / relative
        if not file.is_file() or file.is_symlink():
            continue
        digest = hashlib.sha256(file.read_bytes()).hexdigest()
        lines.append(f"{relative.as_posix()}:{digest}")
    identity = hashlib.sha256("\n".join(lines).encode()).hexdigest()
    return {
        "git_commit": os.fsdecode(head.stdout).strip(),
        "worktree_dirty": bool(status.stdout),
        "source_tree_sha256": identity,
        "hashed_files": len(lines),
        "hash_covers": (
            "working tree bytes of tracked and untracked files, listed individually, "
            "excluding generated example reports"
        ),
        "source_identity": "working_tree",
    }


def hash_files(paths: list[Path]) -> str:
    lines = []
    for path in sorted(paths, key=lambda item: item.as_posix()):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        lines.append(f"{path.as_posix()}:{digest}")
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def machine_info() -> dict:
    return {
        "os": platform.platform(),
        "system": platform.system(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "cpu_count": os.cpu_count(),
        "memory_bytes": psutil.virtual_memory().total,
        "formal_measurement": False,
        "note": "実行した機械の記録であり、採用機器での正式測定ではない。",
    }


def percentile(samples: list[float], fraction: float) -> float | None:
    if not samples:
        return None
    ordered = sorted(samples)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * fraction)))
    return ordered[index]


def _timing(samples: list[dict], key: str) -> dict:
    values = [sample[key] for sample in samples if sample.get(key) is not None]
    return {
        "n": len(values),
        "p50": percentile(values, 0.50),
        "p95": percentile(values, 0.95),
        "max": max(values) if values else None,
        "method": PERCENTILE_METHOD,
        "guarantee": False,
    }


def _interval_result(value: float | None, limit: float | None) -> str:
    if limit is None or value is None:
        return "not_judged"
    if value > limit:
        return "above_proposed"
    return "within_proposed_not_a_pass"


def summarize_samples(samples: list[dict], budgets: dict | None = None) -> dict:
    """Latency uses functional successes only. Failures are never rewritten as fast successes."""
    budgets = budgets or PROPOSED_BUDGETS
    counts = {name: 0 for name in ("success", *LATENCY_EXCLUDED)}
    for sample in samples:
        outcome = sample["outcome"]
        if outcome not in counts:
            raise ValueError(f"unknown outcome: {outcome}")
        counts[outcome] += 1
    successes = [sample for sample in samples if sample["outcome"] == "success"]
    search_limit = budgets["text_search_p95_seconds"]
    context_limit = budgets["context_p95_seconds"]
    budget_exceeded = [
        sample
        for sample in successes
        if (sample.get("search_seconds") is not None and sample["search_seconds"] > search_limit)
        or (sample.get("context_seconds") is not None and sample["context_seconds"] > context_limit)
    ]
    total = len(samples)
    failure_numerator = counts["failure"] + counts["timeout"] + counts["cancelled"]
    search = _timing(successes, "search_seconds")
    context = _timing(successes, "context_seconds")
    source = _timing(successes, "source_seconds")
    overall = _timing(successes, "total_seconds")
    return {
        "sample_count": total,
        "success_count": counts["success"],
        "failure_count": counts["failure"],
        "empty_count": counts["empty"],
        "timeout_count": counts["timeout"],
        "cancelled_count": counts["cancelled"],
        "wrong_evidence_count": counts["wrong_evidence"],
        "budget_exceeded_count": len(budget_exceeded),
        "failure_rate": {
            "numerator": failure_numerator,
            "denominator": total,
            "rate": None if total == 0 else failure_numerator / total,
            "definition": "失敗、取消、期限超過。空結果と根拠不一致は別件数。",
        },
        "unsuccessful_rate": {
            "numerator": total - counts["success"],
            "denominator": total,
            "rate": None if total == 0 else (total - counts["success"]) / total,
            "definition": "成功以外。空結果と根拠不一致を含む。成功時間には入れない。",
        },
        "excluded_from_latency": list(LATENCY_EXCLUDED),
        "search_seconds": search,
        "retrieval_seconds": _timing(successes, "retrieval_seconds"),
        "context_seconds": context,
        "source_seconds": source,
        "total_seconds": overall,
        "total_definition": "検索要求の開始から出典取得完了までの壁時計。内訳の和ではない。",
        "budget_status": "proposed",
        "production_pass": False,
        "budget_comparison": [
            {
                "interval": "search",
                "p95": search["p95"],
                "proposed_seconds": search_limit,
                "result": _interval_result(search["p95"], search_limit),
            },
            {
                "interval": "context",
                "p95": context["p95"],
                "proposed_seconds": context_limit,
                "result": _interval_result(context["p95"], context_limit),
            },
            {
                "interval": "total",
                "p95": overall["p95"],
                "proposed_seconds": None,
                "result": "not_judged",
            },
        ],
    }


def production_allowed(scale_role: str, budget_status: str) -> bool:
    """Small runs and proposed budgets cannot become a production pass."""
    return scale_role == "formal" and budget_status == "adopted"


def rollup(items: list[dict]) -> dict:
    executed = [item for item in items if item.get("status") == "executed"]
    not_run = [item for item in items if item.get("status") == "not_run"]
    not_selected = [item for item in items if item.get("status") == "not_selected"]
    return {
        "executed": len(executed),
        "not_run": len(not_run),
        "not_selected": len(not_selected),
        "passed": 0,
        "not_run_counted_as_passed": False,
        "not_selected_counted_as_passed": False,
    }
