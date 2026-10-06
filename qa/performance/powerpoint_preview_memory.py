"""Measure preview RSS in a fresh process, independently of Docling/model imports.

Use --implementation /path/to/previous/editable_preview.py to compare an older
renderer. This loads only that module; it still uses current common configuration,
so the earlier 100-page guard does not prevent the 530-page memory comparison.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import resource
import shutil
import sys
import time
from pathlib import Path

import psutil

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from docling_desk.preview.editable_preview import build_editable_preview


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--implementation", type=Path)
    args = parser.parse_args()
    folder = args.output.resolve()
    folder.mkdir(parents=True, exist_ok=False)
    source, pdf = folder / "original.pptx", folder / "powerpoint-rendered.pdf"
    shutil.copyfile(args.source, source)
    shutil.copyfile(args.pdf, pdf)
    build = build_editable_preview
    if args.implementation:
        spec = importlib.util.spec_from_file_location("previous_renderer", args.implementation)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        build = module.build_editable_preview
    baseline = psutil.Process().memory_info().rss / 1024**2
    started = time.perf_counter()
    build(source, pdf, folder)
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if sys.platform != "darwin":
        peak *= 1024
    report = {
        "implementation": str(args.implementation or "current"),
        "seconds": round(time.perf_counter() - started, 3),
        "baseline_rss_mib": round(baseline, 1),
        "peak_rss_mib": round(peak / 1024**2, 1),
    }
    (folder / "report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report))


if __name__ == "__main__":
    main()
