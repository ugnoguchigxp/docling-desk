"""Compile a standalone TeX original in an OS-isolated temporary workspace."""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from docling_desk import config
from docling_desk.documents.text import read_text_input
from docling_desk.preview.pdf_view import pdf_pages


def isolated_command(engine: str, work: Path, cache: Path) -> list[str]:
    engine = str(Path(engine).resolve())
    command = [engine, "--untrusted", "--outdir", str(work), str(work / "original.tex")]
    if sys.platform == "darwin" and Path("/usr/bin/sandbox-exec").is_file():
        # Tectonic's untrusted mode disables shell escape, but is not a file
        # sandbox. The OS must also prevent input of private host files.
        readable = [
            "/System",
            "/usr",
            "/bin",
            "/dev",
            "/private/etc/fonts",
            "/private/etc/ssl",
            "/private/etc/resolv.conf",
            "/Library/Fonts",
            "/Library/Apple",
            "/opt/homebrew/Cellar",
            "/opt/homebrew/opt",
            "/opt/homebrew/lib",
            "/opt/homebrew/bin",
            "/opt/homebrew/etc/fonts",
            "/opt/homebrew/share",
            str(Path(engine).resolve()),
            str(work),
            str(cache),
        ]
        profile = (
            "(version 1)(allow default)(deny file-read*)(deny file-write*)"
            '(allow file-read-metadata)(allow file-read-data (literal "/"))(deny process-fork)'
            + "".join(f"(allow file-read* (subpath {json.dumps(p)}))" for p in readable)
            + "".join(
                f"(allow file-write* (subpath {json.dumps(str(p))}))"
                for p in (work, cache, Path("/dev"))
            )
        )
        return ["/usr/bin/sandbox-exec", "-p", profile, *command]
    if sys.platform == "linux" and (bubblewrap := shutil.which("bwrap")):
        prefix = [bubblewrap, "--die-with-parent", "--unshare-all", "--share-net"]
        for name in (
            "/usr",
            "/bin",
            "/lib",
            "/lib64",
            "/etc/fonts",
            "/etc/ssl",
            "/etc/resolv.conf",
            "/etc/ld.so.cache",
        ):
            if Path(name).exists():
                prefix += ["--ro-bind", name, name]
        if not Path(engine).is_relative_to("/usr"):
            prefix += ["--ro-bind", engine, engine]
        prefix += [
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--tmpfs",
            "/tmp",
            "--bind",
            str(work),
            str(work),
            "--bind",
            str(cache),
            str(cache),
            "--chdir",
            str(work),
        ]
        return [*prefix, *command]
    raise RuntimeError(
        "TeXの安全な組版環境がありません。macOSのsandbox-exec、Linuxのbubblewrapが必要です。"
    )


def compile_tex(source: Path, folder: Path) -> str:
    engine = shutil.which("tectonic")
    if not engine:
        raise RuntimeError(
            "TeXの組版にはTectonicが必要です。導入後に再取り込みしてください。ソースは閲覧できます。"
        )
    cache = (config.CACHE / "tectonic").resolve()
    cache.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="docling-tex-") as temporary:
        work = Path(temporary).resolve()
        (work / "original.tex").write_text(read_text_input(source), encoding="utf-8")
        # Do not forward application credentials or user TeX configuration.
        env = {
            "PATH": os.defpath,
            "HOME": str(work),
            "TMPDIR": str(work),
            "XDG_CACHE_HOME": str(cache),
            "XDG_CONFIG_HOME": str(work),
            "TECTONIC_CACHE_DIR": str(cache),
            "TECTONIC_UNTRUSTED_MODE": "1",
        }
        with (work / "compiler.log").open("wb") as output:
            process = subprocess.Popen(
                isolated_command(engine, work, cache),
                cwd=work,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            try:
                process.wait(timeout=120)
            except subprocess.TimeoutExpired as exc:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
                raise RuntimeError(
                    "TeXの組版が120秒以内に完了しませんでした。ソースは閲覧できます。"
                ) from exc
        pdf = work / "original.pdf"
        if process.returncode or not pdf.is_file():
            with (work / "compiler.log").open("rb") as log:
                log.seek(max(0, log.seek(0, 2) - 4000))
                detail = log.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"TeXを組版できませんでした（終了コード {process.returncode}）。ソースや必要なパッケージを確認してください。\n"
                + detail
            )
        if pdf.stat().st_size > config.MAX_BYTES or len(pdf_pages(pdf)) > config.MAX_PAGES:
            raise RuntimeError("TeXの組版結果が容量またはページ数の上限を超えています。")
        target = folder / "tex-preview.pdf"
        shutil.copyfile(pdf, target.with_suffix(".tmp"))
        target.with_suffix(".tmp").replace(target)
    return target.name
