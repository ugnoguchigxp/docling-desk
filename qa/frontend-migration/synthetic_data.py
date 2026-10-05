"""Build a browser fixture directory without reading the user's data folder."""

from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = {
    "7" * 32: "slide",
    "8" * 32: "sheet",
    "9" * 32: "page",
    "a" * 32: "glyph",
    "b" * 32: "sheet",
}
SAVED_DOCUMENT_IDS = {
    "cca5aede53b04a63b4f165b1c72794b2",
    "1357fa0e4b694bc7b06095c269cef984",
}


def materialize(destination: Path, *, synthetic: bool, source: Path | None = None) -> None:
    if destination.exists():
        shutil.rmtree(destination)
    if synthetic:
        destination.mkdir(parents=True)
        for job_id, kind in FIXTURES.items():
            shutil.copytree(ROOT / "tests/fixtures/documents" / kind, destination / job_id)
        office_spec = importlib.util.spec_from_file_location(
            "synthetic_office", Path(__file__).with_name("synthetic_office.py")
        )
        assert office_spec and office_spec.loader
        office = importlib.util.module_from_spec(office_spec)
        office_spec.loader.exec_module(office)
        glyph = destination / ("a" * 32)
        office.write_deck(glyph / "original.pptx")
        workbook = destination / ("b" * 32)
        job = json.loads((workbook / "job.json").read_text(encoding="utf-8"))
        job["id"] = "b" * 32
        job["filename"] = "synthetic-workbook.xlsx"
        (workbook / "job.json").write_text(json.dumps(job), encoding="utf-8")
        office.write_workbook(workbook / "original.xlsx")
        leaked = SAVED_DOCUMENT_IDS.intersection(path.name for path in destination.iterdir())
        if leaked:
            raise RuntimeError("合成fixtureに保存済み実資料のIDが含まれています。")
        return
    if source is None or not source.is_dir():
        raise FileNotFoundError("画面fixtureのコピー元がありません。")
    shutil.copytree(source, destination)
