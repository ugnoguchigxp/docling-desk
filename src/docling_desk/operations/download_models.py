"""Fetch pinned inference artifacts into a complete directory, then publish that directory."""

import json
import shutil
import sys
from pathlib import Path

from huggingface_hub import snapshot_download

from docling_desk.config import MODEL_MANIFEST, MODELS


def _tools():
    from docling_desk.operations.models import (
        commit_previous,
        has_revision,
        recover_model,
        replace_directory,
        seal,
        write_stamp,
    )

    return has_revision, replace_directory, write_stamp, seal, commit_previous, recover_model


def publish(model: dict, destination: Path) -> None:
    has_revision, replace_directory, write_stamp, seal, commit_previous, recover_model = _tools()
    name = Path(model["path"]).name
    final = destination / name
    staging = destination / f"{name}.incoming"
    recover_model(destination, model)
    if (final.with_name(final.name + ".previous")).exists() and final.exists():
        if not commit_previous(final, model):
            raise RuntimeError(f"previous model is still unverified for {model['repo']}")
    if staging.exists():
        shutil.rmtree(staging)
    try:
        snapshot_download(
            repo_id=model["repo"],
            revision=model["revision"],
            allow_patterns=model["patterns"],
            local_dir=staging,
        )
        if not has_revision(staging, model["revision"]):
            raise RuntimeError(f"downloaded revision does not match manifest for {model['repo']}")
        write_stamp(staging, model["repo"], model["revision"])
        seal(staging, model)
        replace_directory(staging, final)
        if not commit_previous(final, model):
            raise RuntimeError(f"published model failed verification for {model['repo']}")
    except Exception:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        raise


def main() -> None:
    destination = MODELS
    destination.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((MODEL_MANIFEST).read_text(encoding="utf-8"))
    for model in manifest:
        publish(model, destination)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"model download failed: {exc}", file=sys.stderr)
        sys.exit(1)
