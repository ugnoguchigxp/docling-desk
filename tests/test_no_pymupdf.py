"""PyMuPDF (AGPL) must not be needed by any Docling Desk module."""

import re
import subprocess
import sys
from pathlib import Path

import docling_desk

PACKAGE = Path(docling_desk.__file__).parent
# Executing this module writes sample files; it has no import-time dependencies of its own.
SCRIPTS = {"docling_desk.development.samples"}
IMPORT = re.compile(r"^\s*(?:import|from)\s+(?:pymupdf|fitz)\b", re.M)
LOADER = """
import importlib, pkgutil, sys
sys.modules["pymupdf"] = None
sys.modules["fitz"] = None
import docling_desk
failed = []
count = 0
for module in pkgutil.walk_packages(docling_desk.__path__, "docling_desk."):
    if module.name.endswith(".__main__") or module.name in {scripts}:
        continue
    try:
        importlib.import_module(module.name)
        count += 1
    except Exception as error:
        failed.append(f"{{module.name}}: {{error!r}}")
print(count)
sys.exit("\\n".join(failed) if failed else 0)
"""


def test_every_module_imports_with_pymupdf_and_fitz_unavailable(tmp_path):
    result = subprocess.run(
        [sys.executable, "-I", "-c", LOADER.format(scripts=sorted(SCRIPTS))],
        cwd=tmp_path,
        env={"DOCLING_ENV_FILE": "/dev/null", "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    # A deliberately broken import (None in sys.modules) would fail above.
    assert int(result.stdout.strip().splitlines()[-1]) > 100


def test_no_source_file_imports_pymupdf_or_fitz():
    offenders = [
        str(path.relative_to(PACKAGE))
        for path in PACKAGE.rglob("*.py")
        if IMPORT.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []


def test_no_test_imports_pymupdf_or_fitz():
    offenders = [
        path.name
        for path in Path(__file__).parent.glob("*.py")
        if IMPORT.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []
