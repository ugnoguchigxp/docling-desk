"""Delete only regenerable cache files. Originals, the Wiki database, and active extracts stay."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from docling_desk.config import RESOURCES

POLICY_PATH = RESOURCES / "ops/retention-policy.json"
PROTECTED_NAMES = {
    "library.json",
    "local.sqlite",
    "document.json",
    "rag.jsonl",
    "rag-index.jsonl",
    "rag-docling.jsonl",
    "job.json",
}


def load_policy(path: Path = POLICY_PATH) -> dict:
    policy = json.loads(path.read_text(encoding="utf-8"))
    if policy.get("cache_max_age_days", 0) < 1 or policy.get("thumbnail_max_age_days", 0) < 1:
        raise ValueError("retention ages must be at least one day")
    return policy


def protected(path: Path) -> bool:
    if path.name in PROTECTED_NAMES or path.name.startswith("original."):
        return True
    return bool({"knowledge", "content", "runtime"}.intersection(path.parts))


def candidates(root: Path, policy: dict, now: float | None = None) -> list[Path]:
    moment = time.time() if now is None else now
    found = []
    limits = {
        "thumbnails": policy["thumbnail_max_age_days"],
        ".cache": policy["cache_max_age_days"],
        "cache": policy["cache_max_age_days"],
    }
    for path in root.rglob("*"):
        if not path.is_file() or path.is_symlink() or protected(path):
            continue
        if "huggingface" in path.parts or path.name == "revision.json":
            continue
        area = next((name for name in limits if name in path.parts), None)
        if area is None:
            continue
        age = moment - path.stat().st_mtime
        if age >= limits[area] * 86400:
            found.append(path)
    return sorted(found)


def apply(paths: list[Path]) -> list[str]:
    blocked = [path for path in paths if protected(path)]
    if blocked:
        raise ValueError(f"refusing to delete protected file: {blocked[0].name}")
    removed = []
    for path in paths:
        path.unlink()
        removed.append(str(path))
    return removed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Remove expired regenerable caches")
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    paths = candidates(args.root, load_policy())
    if args.apply:
        removed = apply(paths)
    else:
        removed = [str(path) for path in paths]
    print(json.dumps({"removed": removed, "applied": args.apply}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
