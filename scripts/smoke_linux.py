"""Exercise a running container using synthetic documents only."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.request import Request, urlopen
from uuid import uuid4

from PIL import Image, ImageDraw, ImageFont


def check(base: str, samples: Path) -> list[dict]:
    def get(path):
        with urlopen(base + path, timeout=30) as response:
            return response.read()

    assert json.loads(get("/health/ready"))["status"] == "ready"
    index = get("/").decode()
    for asset in re.findall(r'(?:src|href)="(/static/frontend/[^"?#]+)"', index):
        assert get(asset)
    results = []
    with TemporaryDirectory(prefix="linux-smoke-") as work:
        scratch = Path(work)
        (scratch / "synthetic.md").write_text("# Linux Markdown\n\n合成資料の本文です。\n")
        (scratch / "synthetic.txt").write_text("Linux text document\n合成資料の本文です。\n")
        font_path = subprocess.check_output(
            ["fc-match", "-f", "%{file}", "Noto Sans CJK JP"], text=True
        )
        font = ImageFont.truetype(font_path, 56)
        image = Image.new("RGB", (1600, 500), "white")
        draw = ImageDraw.Draw(image)
        draw.text((70, 80), "Linux OCR validation", font=font, fill="black")
        draw.text((70, 220), "日本語の合成資料", font=font, fill="black")
        image.save(scratch / "synthetic-scan.pdf", "PDF", resolution=150)
        documents = [*sorted(samples.glob("synthetic-*")), *sorted(scratch.iterdir())]
        for source in documents:
            boundary = uuid4().hex
            body = (
                (
                    f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{source.name}"\r\n'
                    "Content-Type: application/octet-stream\r\n\r\n"
                ).encode()
                + source.read_bytes()
                + f"\r\n--{boundary}--\r\n".encode()
            )
            request = Request(
                base + "/api/upload",
                data=body,
                headers={
                    "Content-Type": f"multipart/form-data; boundary={boundary}",
                },
            )
            with urlopen(request, timeout=30) as response:
                job = json.load(response)
            deadline = time.monotonic() + 360
            while job["state"] in {"queued", "running"} and time.monotonic() < deadline:
                time.sleep(1)
                job = json.loads(get(f"/api/jobs/{job['id']}"))
            assert job["state"] == "success", job
            ident = job["id"]
            suffix = source.suffix
            view = {
                ".pdf": "pdf",
                ".pptx": "slides/1",
                ".xlsx": "workbook",
                ".docx": "word",
                ".md": "document",
                ".txt": "document",
            }[suffix]
            assert get(f"/view/{ident}/{view}")
            text = get(f"/files/{ident}/text.txt").decode()
            assert text.strip()
            for line in get(f"/files/{ident}/rag.jsonl").splitlines():
                json.loads(line)
            json.loads(get(f"/api/jobs/{ident}/tables"))
            if suffix == ".pptx":
                for number in (1, 2):
                    assert get(f"/view/{ident}/slides/{number}")
                    thumbnail = get(f"/api/jobs/{ident}/slides/{number}/thumbnail")
                    assert thumbnail.startswith(b"RIFF") and len(thumbnail) <= 2000
            if suffix == ".xlsx":
                for number in (1, 2):
                    sheet = get(f"/view/{ident}/sheets/{number}").decode()
                    assert "worksheet" in sheet and "sheet-row-axis" in sheet
                assert json.loads(get(f"/api/jobs/{ident}/translations"))["units"]
            if suffix == ".pdf":
                assert get(f"/view/{ident}/pages/1")
                assert get(f"/api/jobs/{ident}/pdf/pages/1/thumbnail").startswith(b"RIFF")
            if source.name == "synthetic-scan.pdf":
                assert "Linux OCR validation" in text and "日本語" in re.sub(r"\s+", "", text), text
            results.append(
                {
                    "filename": source.name,
                    "id": ident,
                    "state": job["state"],
                    "duration": job["duration"],
                    "preview": job["preview"],
                    "tables": job["tables"],
                    "chunks": job["chunks"],
                }
            )
            print(json.dumps(results[-1], ensure_ascii=False), flush=True)
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    parser.add_argument("--samples", type=Path, required=True)
    arguments = parser.parse_args()
    check(arguments.base_url.rstrip("/"), arguments.samples)
