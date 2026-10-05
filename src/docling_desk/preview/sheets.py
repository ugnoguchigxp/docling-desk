"""Excel-style navigation around inert Quick Look worksheet previews."""

from __future__ import annotations

import html as escape_html
import json
import re
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree as etree
from zipfile import ZipFile

from lxml import html

from docling_desk.storage import original_file

MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def workbook_sheets(source: Path) -> list[dict]:
    with ZipFile(source) as archive:
        book = etree.fromstring(archive.read("xl/workbook.xml"))
        relations = etree.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        targets = {node.get("Id"): node.get("Target", "") for node in relations}
        sheets = []
        for number, node in enumerate(book.findall(f"{{{MAIN}}}sheets/{{{MAIN}}}sheet"), 1):
            target = targets.get(node.get(f"{{{REL}}}id"), "")
            path = (
                target.lstrip("/") if target.startswith("/") else str(PurePosixPath("xl") / target)
            )
            tree = etree.fromstring(archive.read(path))
            hidden = {
                int(row.get("r", "0"))
                for row in tree.findall(f"{{{MAIN}}}sheetData/{{{MAIN}}}row")
                if row.get("hidden") in {"1", "true"} and row.get("r", "").isdigit()
            }
            hidden_columns = {
                number
                for col in tree.findall(f"{{{MAIN}}}cols/{{{MAIN}}}col")
                if col.get("hidden") in {"1", "true"}
                for number in range(int(col.get("min", "1")), int(col.get("max", "1")) + 1)
            }
            sheets.append(
                {
                    "number": number,
                    "name": node.get("name", f"Sheet{number}"),
                    "hidden_rows": hidden,
                    "hidden_columns": hidden_columns,
                    "visible": node.get("state", "visible") == "visible",
                }
            )
        return sheets


def preview_sheets(folder: Path, preview: str) -> list[dict]:
    source = original_file(folder, ".xlsx")
    names = workbook_sheets(source)
    path = (folder / preview).resolve()
    if not path.is_relative_to(folder.resolve()):
        raise ValueError("シートのプレビューが見つかりません。")
    tree = html.fromstring(path.read_text(encoding="utf-8"))
    # Accept both untouched Quick Look tabs and the older static section wrapper.
    links = [
        (node.text_content(), node.getnext().get("href", ""))
        for node in tree.xpath('//div[@class="TabHeader"]')
        if node.getnext() is not None and node.getnext().tag == "a"
    ]
    if not links:
        links = [
            (node.xpath("string(h2)"), node.xpath("string(iframe/@src)"))
            for node in tree.xpath("//section[iframe]")
        ]
    if not links and tree.xpath('//table[contains(concat(" ", @class, " "), " worksheet ")]'):
        visible = [sheet for sheet in names if sheet["visible"]]
        links = [(visible[0]["name"], path.name)]
    result = []
    for label, href in links:
        sheet = next((sheet for sheet in names if sheet["name"] == label), None)
        target = (path.parent / href).resolve()
        if (
            sheet
            and Path(href).name == href
            and href.endswith(".html")
            and target.is_relative_to(folder.resolve())
            and target.is_file()
        ):
            result.append({**sheet, "path": target})
    if not result:
        raise ValueError("シートのプレビューが見つかりません。")
    return result


def column_name(number: int) -> str:
    name = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        name = chr(65 + remainder) + name
    return name


def sheet_html(folder: Path, job_id: str, sheet: dict) -> str:
    tree = html.fromstring(sheet["path"].read_text(encoding="utf-8"))
    # Remove document scripts. Only the app's size/zoom bridge can run below.
    for node in tree.xpath("//script|//iframe|//object|//embed|//base|//form|//meta[@http-equiv]"):
        node.drop_tree()
    for node in tree.iter():
        for attr in list(node.attrib):
            if attr.lower().startswith("on"):
                del node.attrib[attr]
        for attr in ("src", "href"):
            value = node.get(attr)
            if value is None:
                continue
            if attr == "src" and re.match(r"^data:image/(png|jpeg|gif|webp);base64,", value):
                continue
            target = (sheet["path"].parent / value).resolve()
            if (
                Path(value).name == value
                and target.is_relative_to(folder.resolve())
                and target.is_file()
            ):
                node.set(attr, f"/files/{job_id}/{target.relative_to(folder).as_posix()}")
            else:
                del node.attrib[attr]
    for table in tree.xpath('//table[contains(concat(" ", @class, " "), " worksheet ")]'):
        rows = table.xpath("./tr|./tbody/tr")
        cols = table.xpath("./col|./colgroup/col")
        count = sum(int(col.get("span", "1")) for col in cols)
        if not count:
            count = max(
                (sum(int(cell.get("colspan", "1")) for cell in row) for row in rows), default=1
            )
        axis_col = html.Element("col", style="width:44px")
        if cols:
            cols[0].addprevious(axis_col)
        else:
            table.insert(0, axis_col)
        style = table.get("style", "")
        table.set(
            "style",
            re.sub(
                r"width\s*:\s*([\d.]+)(?:px)?\s*;?", lambda m: f"width:{float(m[1]) + 44}px;", style
            ),
        )
        header = html.Element("tr", {"class": "sheet-column-axis"})
        header.append(html.Element("th", {"class": "sheet-corner", "aria-label": "行と列"}))
        number = 0
        for _ in range(count):
            number += 1
            while number in sheet.get("hidden_columns", set()):
                number += 1
            cell = html.Element("th", scope="col")
            cell.text = column_name(number)
            header.append(cell)
        if rows:
            rows[0].addprevious(header)
        else:
            table.append(header)
        number = 0
        for row in rows:
            number += 1
            while number in sheet["hidden_rows"]:
                number += 1
            cell = html.Element("th", {"class": "sheet-row-axis", "scope": "row"})
            cell.text = str(number)
            row.insert(0, cell)
    head = tree.find("head")
    if head is not None:
        for meta in head.xpath('meta[@name="viewport"]'):
            meta.set("content", "width=device-width,initial-scale=1")
        css = html.Element("link", rel="stylesheet", href="/static/sheets.css")
        head.append(css)
        script = html.Element("script", src="/static/sheet-frame.js", defer="defer")
        head.append(script)
        head.append(html.Element("script", src="/static/file-drop.js", defer="defer"))
    # Preserve quirks mode: Quick Look uses unitless sizes for the source cells.
    return html.tostring(tree, encoding="unicode")


def workbook_html(job_id: str, filename: str, sheets: list[dict]) -> str:
    payload = (
        json.dumps(
            {
                "jobId": job_id,
                "sheets": [{"number": s["number"], "name": s["name"]} for s in sheets],
            },
            ensure_ascii=False,
        )
        .replace("<", "\\u003c")
        .replace("&", "\\u0026")
    )
    return (
        '<!doctype html><html lang="ja"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{escape_html.escape(filename)}・シートプレビュー</title>"
        '<link rel="stylesheet" href="/static/sheets.css">'
        '<script src="/static/file-drop.js" defer></script>'
        '<script src="/static/document-frame.js" defer></script>'
        f'<script src="/static/sheets.js" defer></script></head><body class="workbook" data-job="{job_id}">'
        '<main id="sheetStage" aria-label="シート表示領域"></main>'
        '<div class="sheet-bottom-bar"><nav id="sheetTabs" role="tablist" aria-label="シート"></nav>'
        '<div class="sheet-zoom" role="group" aria-label="シートのズーム">'
        '<button id="sheetZoomOut" type="button" aria-label="縮小">−</button>'
        '<output id="sheetZoomLevel" aria-label="ズーム倍率" aria-live="polite"></output>'
        '<button id="sheetZoomIn" type="button" aria-label="拡大">＋</button>'
        '<button id="sheetActualSize" type="button">100%</button>'
        '<button id="sheetFit" type="button" aria-pressed="true">画面に合わせる</button></div></div>'
        f'<script id="sheetData" type="application/json">{payload}</script></body></html>'
    )
