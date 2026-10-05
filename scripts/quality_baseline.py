"""Record the review scope without secrets or user documents."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
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
}
SKIP_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".pdf", ".sqlite", ".pyc"}


def git(*args: str) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=False
    )
    return completed.stdout.strip()


def listed_files() -> list[dict]:
    rows = []
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(ROOT)
        if set(relative.parts) & SKIP_PARTS or path.suffix.lower() in SKIP_SUFFIXES:
            continue
        if path.name in {".env", "credentials.json"} or "secret" in path.name.lower():
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        rows.append({"path": relative.as_posix(), "sha256": digest, "bytes": path.stat().st_size})
    return rows


def main() -> int:
    files = listed_files()
    identity = hashlib.sha256(
        "\n".join(f"{item['path']}:{item['sha256']}" for item in files).encode()
    ).hexdigest()
    payload = {
        "document_kind": "current_scope_not_a_score",
        "source_digest": identity,
        "commit": git("rev-parse", "HEAD"),
        "status_short": git("status", "--short"),
        "docling_commit": git("-C", "docling", "rev-parse", "HEAD"),
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "file_count": len(files),
        "files": files,
        "usage_conditions": "docs/technical-quality/usage-conditions.md",
        "commands": [
            "python -m pytest tests/test_quality_recovery.py tests/test_model_integrity.py tests/test_local_knowledge.py tests/test_azure_ocr.py -q",
            "python -m pytest tests -q",
            "bun test",
            "bun run typecheck",
            "bun run lint",
            "bun run contract:check",
            "pnpm test:run",
            "pnpm typecheck",
            "pnpm lint",
        ],
        "excluded": sorted(SKIP_PARTS),
    }
    destination = ROOT / "qa" / "technical-quality" / identity[:12]
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "baseline.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(destination / "baseline.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
