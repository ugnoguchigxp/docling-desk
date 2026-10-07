"""Serve copied recovery assets with the real app, JWT and HTTPS in isolation.

The output must be new. No source document or running application is changed.
Use recovery_preview_browser.mjs against this server to inspect the final build.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path

from lxml import html

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "packages/docling-azure-ocr/src"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--saved-preview", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8898)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    identifier = "d" * 32
    folder = output / "data/derived/documents" / identifier
    preview = folder / "progressive-preview"
    shutil.copytree(args.saved_preview, preview)
    slides = []
    for page in preview.glob("page-*.html"):
        number = int(page.stem.removeprefix("page-"))
        svg = html.fromstring(page.read_text()).xpath("//svg")[0]
        layout = json.loads(svg.get("data-text-layout"))
        slides.append(
            {
                "number": number,
                "width": layout["width"],
                "height": layout["height"],
                "preview": f"progressive-preview/{page.name}",
            }
        )
    slides.sort(key=lambda slide: slide["number"])
    (folder / "slides.json").write_text(json.dumps({"slides": slides}))
    os.environ.update(
        DOCLING_ENV_FILE="/dev/null",
        DOCLING_DATA_DIR=str(output / "data"),
        DOCLING_STORAGE="local",
        DOCLING_AUTH_MODE="jwt",
        DOCLING_AUTH_JWT_SECRET=secrets.token_hex(32),
        DOCLING_AUTH_LOGIN_URL="/login",
        DOCLING_ALLOWED_ORIGINS=f"https://127.0.0.1:{args.port}",
    )
    from docling_desk.documents.conversion import Job, save_job
    from docling_desk.storage import original_file, record_original

    job = Job(
        id=identifier,
        filename="saved-recovery-530.pptx",
        state="success",
        pages=max(s["number"] for s in slides),
        preview="progressive-preview/revision-530.html",
        slide_layout=False,
    )
    original = original_file(folder, ".pptx")
    original.parent.mkdir(parents=True, exist_ok=True)
    original.write_bytes(b"QA placeholder: viewing must never decompose a PPTX")
    record_original(folder, job.filename, job.created)
    save_job(folder, job)
    translations = folder / "translations/en"
    translations.mkdir(parents=True)
    (translations / "slide-1.json").write_text(
        '{"state":"completed","result":{"text":"saved translation"}}'
    )
    protected = {}
    for path in (output / "data").rglob("*"):
        if path.is_file():
            with path.open("rb") as stream:
                protected[str(path.relative_to(output))] = hashlib.file_digest(
                    stream, "sha256"
                ).hexdigest()
    (output / "before.json").write_text(json.dumps(protected, indent=2))
    certificate, key = output / "certificate.pem", output / "private-key.pem"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-subj",
            "/CN=localhost",
            "-keyout",
            str(key),
            "-out",
            str(certificate),
        ],
        check=True,
        capture_output=True,
    )
    key.chmod(0o600)

    def encode(value):
        return base64.urlsafe_b64encode(value).rstrip(b"=").decode()

    head = encode(b'{"alg":"HS256","typ":"JWT"}')
    payload = encode(
        json.dumps({"type": "access", "userId": "local-qa", "exp": time.time() + 3600}).encode()
    )
    signature = hmac.new(
        os.environ["DOCLING_AUTH_JWT_SECRET"].encode(),
        f"{head}.{payload}".encode(),
        hashlib.sha256,
    ).digest()
    credentials = output / "credentials.json"
    credentials.write_text(json.dumps({"token": f"{head}.{payload}.{encode(signature)}"}))
    credentials.chmod(0o600)
    import uvicorn

    print(
        f"Isolated saved-page viewer: https://127.0.0.1:{args.port}/?job={identifier}", flush=True
    )
    uvicorn.run(
        "docling_desk.app:app",
        host="127.0.0.1",
        port=args.port,
        ssl_keyfile=str(key),
        ssl_certfile=str(certificate),
        access_log=False,
    )


if __name__ == "__main__":
    main()
