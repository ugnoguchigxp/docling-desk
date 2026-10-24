"""Isolated headless LibreOffice exports for Linux document previews.

Untrusted Office files are opened only inside bubblewrap: no network, read-only
system directories, a private copy of the file and an empty environment. The
private profile disables macro execution and links to untrusted locations, and
uploads with macro projects are refused before conversion.
"""

from __future__ import annotations

import html
import logging
import os
import shutil
import signal
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from lxml import html as markup
from openpyxl import load_workbook

from docling_desk.preview.sheets import workbook_sheets


def normalize_merged_cells(table, worksheet, sheet: dict) -> None:
    """Calc keeps source spans even when its HTML omits hidden rows/columns."""
    columns = [
        number
        for number in range(1, worksheet.max_column + 1)
        if number not in sheet["hidden_columns"]
    ]
    merged = {(area.min_row, area.min_col): area for area in worksheet.merged_cells.ranges}
    occupied = set()
    row_number = 0
    for row in table.xpath("./tr|./tbody/tr"):
        row_number += 1
        while row_number in sheet["hidden_rows"]:
            row_number += 1
        column_index = 0
        for cell in row.xpath("./td"):
            while (row_number, column_index) in occupied:
                column_index += 1
            area = (
                merged.get((row_number, columns[column_index]))
                if column_index < len(columns)
                else None
            )
            if area is not None:
                span = sum(area.min_col <= number <= area.max_col for number in columns)
                rows = [
                    number
                    for number in range(area.min_row, area.max_row + 1)
                    if number not in sheet["hidden_rows"]
                ]
                cell.set("colspan", str(span))
                cell.set("rowspan", str(len(rows)))
                for number in rows:
                    for index in range(column_index, column_index + span):
                        occupied.add((number, index))
            column_index += int(cell.get("colspan", "1"))


def _kill_group(process: subprocess.Popen) -> None:
    """SIGKILL the whole process group; the leader's pid is the group id."""
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


LOGGER = logging.getLogger(__name__)
# Directories LibreOffice reads; everything else in the host stays invisible.
READ_ONLY = (
    "/usr",
    "/bin",
    "/lib",
    "/lib64",
    "/etc/fonts",
    "/etc/libreoffice",
    "/etc/alternatives",
    "/etc/ld.so.cache",
    "/etc/passwd",
)
# No macro ever runs, whatever the document asks; nor links to untrusted places.
PROFILE = """<?xml version="1.0" encoding="UTF-8"?>
<oor:items xmlns:oor="http://openoffice.org/2001/registry" \
xmlns:xs="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
<item oor:path="/org.openoffice.Office.Common/Security/Scripting">\
<prop oor:name="DisableMacrosExecution" oor:op="fuse"><value>true</value></prop></item>
<item oor:path="/org.openoffice.Office.Common/Security/Scripting">\
<prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop></item>
<item oor:path="/org.openoffice.Office.Common/Security/Scripting">\
<prop oor:name="BlockUntrustedRefererLinks" oor:op="fuse"><value>true</value></prop></item>
</oor:items>
"""


def run_in_group(
    command: list[str], timeout: float, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess:
    """Like ``subprocess.run(timeout=...)``, but the child leads its own process group.

    ``soffice`` is a launcher that starts ``soffice.bin``. ``subprocess.run`` kills
    only the launcher on timeout and leaves the real office process running, so the
    whole group is killed instead (also when interrupted).
    """
    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
        env=env,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except BaseException:
        _kill_group(process)
        try:
            process.communicate(timeout=10)  # reap the leader and drain the pipes
        except subprocess.TimeoutExpired:
            pass  # something left the group and still holds the pipes; do not hang
        raise
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def sandbox(command: list[str], work: Path) -> list[str]:
    """Wrap LibreOffice in bubblewrap, or refuse outside a container without it.

    The container image cannot create namespaces (all capabilities are dropped);
    there the container is the boundary and its network policy applies.
    """
    bubblewrap = shutil.which("bwrap")
    if bubblewrap is None:
        if os.environ.get("DOCLING_CONTAINER") == "1":
            LOGGER.warning("LibreOffice runs without bubblewrap inside the container")
            return command
        raise RuntimeError(
            "LibreOfficeを隔離して実行するにはbubblewrap（bwrap）が必要です。"
            "導入してから再取り込みしてください。抽出結果は閲覧できます。"
        )
    prefix = [bubblewrap, "--die-with-parent", "--new-session", "--unshare-all"]
    for name in READ_ONLY:
        if Path(name).exists():
            prefix += ["--ro-bind", name, name]
    prefix += ["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp"]
    prefix += ["--bind", str(work), str(work), "--chdir", str(work)]
    return [*prefix, *command]


def convert_office(source: Path, destination: Path, output_format: str) -> Path:
    binary = shutil.which("libreoffice") or shutil.which("soffice")
    if not binary:
        raise RuntimeError("LibreOfficeがありません。Linux用Dockerイメージを使用してください。")
    destination.mkdir(parents=True, exist_ok=True)
    # Never reuse a user profile or attach to another running Office instance.
    with TemporaryDirectory(prefix="docling-office-") as temporary:
        work = Path(temporary).resolve()
        # The sandbox sees only this directory: a private copy in, the export out.
        private = work / f"input{source.suffix.lower()}"
        shutil.copyfile(source, private)
        output_dir = work / "out"
        output_dir.mkdir()
        profile = work / "profile"
        (profile / "user").mkdir(parents=True)
        (profile / "user" / "registrymodifications.xcu").write_text(PROFILE, encoding="utf-8")
        home = work / "home"
        home.mkdir()
        # Nothing of the server's environment (credentials included) reaches the office.
        env = {
            "PATH": "/usr/bin:/bin",
            "HOME": str(home),
            "TMPDIR": str(work),
            "XDG_CACHE_HOME": str(home / ".cache"),
            "XDG_CONFIG_HOME": str(home / ".config"),
            "LANG": "C.UTF-8",
            "SAL_USE_VCLPLUGIN": "svp",
        }
        command = [
            binary,
            f"-env:UserInstallation={profile.as_uri()}",
            "--headless",
            "--nologo",
            "--nodefault",
            "--nofirststartwizard",
            "--norestore",
            "--convert-to",
            output_format,
            "--outdir",
            str(output_dir),
            str(private),
        ]
        try:
            result = run_in_group(sandbox(command, work), timeout=150, env=env)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("LibreOfficeの変換が時間内に完了しませんでした。") from exc
        produced = output_dir / f"{private.stem}.{output_format.split(':', 1)[0]}"
        if result.returncode != 0 or not produced.is_file() or not produced.stat().st_size:
            raise RuntimeError("LibreOfficeの変換結果を確認できませんでした。")
        output = destination / f"{source.stem}.{output_format.split(':', 1)[0]}"
        shutil.move(produced, output)
        # HTML exports write their images next to the page.
        for extra in output_dir.iterdir():
            shutil.move(extra, destination / extra.name)
    return output


def office_html(source: Path, folder: Path) -> str:
    directory = folder / "libreoffice"
    # Export into a fresh directory: a failed conversion cannot reuse stale HTML.
    with TemporaryDirectory(prefix="office-html-", dir=folder) as work:
        raw = convert_office(source, Path(work), "html")
        if source.suffix.lower() == ".xlsx":
            tree = markup.document_fromstring(raw.read_bytes())
            tables = tree.xpath("//body/table")
            visible = [sheet for sheet in workbook_sheets(source) if sheet["visible"]]
            workbook = load_workbook(source)
            if len(tables) != len(visible):
                raise ValueError("LibreOfficeのシート数が原本と一致しません。")
            sections = []
            for sheet, table in zip(visible, tables, strict=True):
                table.set("class", "worksheet")
                normalize_merged_cells(table, workbook.worksheets[sheet["number"] - 1], sheet)
                target = Path(work) / f"sheet-{sheet['number']}.html"
                head = tree.find("head")
                target.write_text(
                    "<!doctype html><html>"
                    + (
                        markup.tostring(head, encoding="unicode")
                        if head is not None
                        else "<head></head>"
                    )
                    + "<body>"
                    + markup.tostring(table, encoding="unicode")
                    + "</body></html>",
                    encoding="utf-8",
                )
                sections.append(
                    f"<section><h2>{html.escape(sheet['name'])}</h2>"
                    f'<iframe src="{target.name}" sandbox></iframe></section>'
                )
            raw.write_text(
                '<!doctype html><html><head><meta charset="utf-8"></head><body>'
                + "".join(sections)
                + "</body></html>",
                encoding="utf-8",
            )
        if directory.exists():
            shutil.rmtree(directory)
        shutil.copytree(work, directory)
    return (directory / raw.name).relative_to(folder).as_posix()
