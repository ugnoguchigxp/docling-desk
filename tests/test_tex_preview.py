"""Standalone TeX ingestion, failure fallback and the shared PDF viewer."""

import shutil
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pymupdf
import pytest
from fastapi.testclient import TestClient
from lxml import html

from docling_desk import config
from docling_desk.app import create_app
from docling_desk.preview import tex
from docling_desk.storage import document_folder, original_file

SOURCE = r"""\documentclass{article}
\begin{document}
Hello TeX. $E=mc^2$
\newpage Second page.
\end{document}
"""


def fake_compile(source, folder):
    assert source.read_text() == SOURCE
    with pymupdf.open() as doc:
        for text in ("Hello TeX", "Second page"):
            page = doc.new_page()
            page.insert_text((72, 72), text)
        doc.save(folder / "tex-preview.pdf")
    return "tex-preview.pdf"


@pytest.mark.parametrize("suffix", [".TeX", ".LaTeX"])
def test_upload_source_and_compiled_view(suffix, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA", tmp_path)
    monkeypatch.setattr(tex, "compile_tex", fake_compile)
    with TestClient(create_app()) as client:
        client.app.state.worker = SimpleNamespace(submit=lambda fn, *args: fn(*args))
        folder_id = client.post("/api/folders", json={"name": "TeX"}).json()["id"]
        result = client.post(
            "/api/upload",
            files={"file": ("paper" + suffix, SOURCE.encode())},
            data={"folder_id": folder_id, "ocr_provider": "azure_read"},
        )
        assert result.status_code == 202, result.text
        job = result.json()
        assert job["state"] == "success" and job["folder_id"] == folder_id
        assert job["tex_preview"] == "tex-preview.pdf" and job["tex_error"] is None
        base = f"/view/{job['id']}"
        source = client.get(base + "/source")
        assert source.status_code == 200
        assert html.fromstring(source.text).xpath("//pre/text()") == [SOURCE]
        assert "source-viewer.js" in source.text
        assert "sandbox allow-scripts" in source.headers["content-security-policy"]
        pdf = client.get(base + "/pdf")
        assert pdf.status_code == 200 and pdf.text.count('class="pdf-page"') == 2
        page = client.get(base + "/pages/2")
        assert page.status_code == 200 and "Second" in page.text
        assert client.get(base + "/pages/3").status_code == 404
        image = client.get(f"/api/jobs/{job['id']}/pdf/pages/2")
        assert image.status_code == 200 and image.headers["content-type"] == "image/png"
        assert client.get(f"/api/jobs/{job['id']}/pdf/pages/2/thumbnail").status_code == 200
        download = client.get(f"/files/{job['id']}/original{suffix.lower()}?download=true")
        assert download.content == SOURCE.encode()
        # The source remains the source of truth for extraction/search.
        folder = document_folder(tmp_path, job["id"])
        assert (folder / "text.txt").read_text() == SOURCE
        assert original_file(folder, suffix.lower()).read_text() == SOURCE
        assert client.get(f"/api/jobs/{job['id']}/translations").status_code == 200


@pytest.mark.parametrize(
    "message", ["Tectonicが必要です", "Missing package", "組版が120秒以内に完了しませんでした"]
)
def test_compilation_failure_preserves_source(message, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA", tmp_path)
    monkeypatch.setattr(tex, "compile_tex", Mock(side_effect=RuntimeError(message)))
    with TestClient(create_app()) as client:
        client.app.state.worker = SimpleNamespace(submit=lambda fn, *args: fn(*args))
        result = client.post("/api/upload", files={"file": ("paper.tex", SOURCE.encode())})
        assert result.status_code == 202
        job = result.json()
        assert job["state"] == "partial" and job["tex_error"] == message
        assert job["preview"] and job["tex_preview"] is None
        assert client.get(f"/view/{job['id']}/source").status_code == 200
        assert client.get(f"/view/{job['id']}/pdf").status_code == 404


def test_tex_source_is_literal_and_binary_upload_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA", tmp_path)
    monkeypatch.setattr(tex, "compile_tex", Mock(side_effect=RuntimeError("unavailable")))
    with TestClient(create_app()) as client:
        client.app.state.worker = SimpleNamespace(submit=lambda fn, *args: fn(*args))
        source = SOURCE + "<script>alert(1)</script>\n"
        job = client.post("/api/upload", files={"file": ("paper.tex", source.encode())}).json()
        response = client.get(f"/view/{job['id']}/source")
        tree = html.fromstring(response.text)
        assert tree.xpath("//pre/text()") == [source]
        assert not tree.xpath("//script[not(@src)]")
        assert (
            client.post("/api/upload", files={"file": ("bad.tex", b"abc\x00")}).status_code == 422
        )


def test_missing_engine_never_invokes_a_shell(tmp_path, monkeypatch):
    monkeypatch.setattr(tex.shutil, "which", lambda name: None)
    (tmp_path / "paper.tex").write_text(SOURCE)
    with pytest.raises(RuntimeError, match="Tectonic"):
        tex.compile_tex(tmp_path / "paper.tex", tmp_path)


def test_unavailable_os_sandbox_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(tex.sys, "platform", "unsupported")
    with pytest.raises(RuntimeError, match="安全な組版環境"):
        tex.isolated_command("/usr/bin/tectonic", tmp_path, tmp_path)


def test_timeout_terminates_compiler_group(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CACHE", tmp_path / "cache")
    monkeypatch.setattr(tex.shutil, "which", lambda name: "/usr/bin/tectonic")
    monkeypatch.setattr(tex, "isolated_command", lambda *args: ["tectonic"])
    process = Mock(pid=123)
    process.wait.side_effect = [subprocess.TimeoutExpired("tectonic", 120), 0]
    monkeypatch.setattr(tex.subprocess, "Popen", Mock(return_value=process))
    kill = Mock()
    monkeypatch.setattr(tex.os, "killpg", kill)
    (tmp_path / "paper.tex").write_text(SOURCE)
    with pytest.raises(RuntimeError, match="120秒"):
        tex.compile_tex(tmp_path / "paper.tex", tmp_path)
    kill.assert_called_once_with(123, tex.signal.SIGKILL)
    assert not (tmp_path / "tex-preview.pdf").exists()


@pytest.mark.skipif(not shutil.which("tectonic"), reason="optional Tectonic engine")
def test_real_engine_typesets_and_cannot_read_private_files(tmp_path):
    # Uses the warmed engine cache, with no models or LLM providers.
    private = tmp_path / "private.tex"
    private.write_text("PRIVATE-CONTENT-DO-NOT-INCLUDE")
    output = tmp_path / "result"
    output.mkdir()
    source = output / "paper.tex"
    source.write_text(SOURCE)
    result = tex.compile_tex(source, output)
    with pymupdf.open(output / result) as pdf:
        assert len(pdf) == 2 and "Hello TeX" in pdf[0].get_text()
    source.write_text(
        r"\documentclass{article}\begin{document}\input{" + str(private) + r"}\end{document}"
    )
    with pytest.raises(RuntimeError):
        tex.compile_tex(source, output)
    source.write_text(
        r"\documentclass{article}\begin{document}\input{"
        + "../" * 32
        + str(private).lstrip("/")
        + r"}\end{document}"
    )
    with pytest.raises(RuntimeError):
        tex.compile_tex(source, output)
