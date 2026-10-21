"""Regenerate supply/clearance.json inventory records from local metadata.

R20 (inventory phase). It reads only local files: .venv dist-info, frontend/node_modules,
knowledge-api/node_modules and the repository. It never contacts the network.
A record written here has status "inventoried": it never clears anything.
Only a human review may add status "confirmed" (see operations/supply.py `_confirmed`).

Run from the repository root:
    DOCLING_ENV_FILE=/dev/null .venv/bin/python qa/refactoring/5d0d310/R20/build_ledger.py
Records with any other status (for example "confirmed") already in the file are kept.
"""

from __future__ import annotations

import json
import re
import sys
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

from docling_desk.operations import supply  # noqa: E402

REVIEWER = "Claude (自動棚卸し, 人による確認未了)"
DATE = "2026-10-21"
HUMAN = "人によるライセンス条項と配布条件の確認が未了"

FAMILY_CONDITIONS = [
    ("agpl", "AGPL-3.0: 配布・ネットワーク提供の双方で対応するソースの提供と全文の同梱が必要"),
    ("lgpl", "LGPL: 差し替え可能な形での提供とソース入手手段の告知が必要"),
    ("gpl", "GPL系: 結合の態様に応じたソース提供義務。例外条項の有無の確認が必要"),
    ("mpl", "MPL-2.0: 改変したファイルのソース提供と通知の保持が必要"),
    ("apache", "Apache-2.0: ライセンス全文とNOTICE(あれば)の保持、改変の明示が必要"),
    ("bsd", "BSD系: 著作権表示・条件・免責の保持が必要(再配布時は告知に含める)"),
    ("mit", "MIT系: 著作権表示と許諾文の保持が必要"),
    ("isc", "ISC: 著作権表示と許諾文の保持が必要"),
    ("0bsd", "0BSD: 条件なし(告知は任意)"),
    ("psf", "PSF系: 著作権表示とライセンス文の保持が必要"),
    ("python", "PSF系: 著作権表示とライセンス文の保持が必要"),
    ("zlib", "zlib: 由来の明示と改変の表示が必要"),
    ("unlicense", "Unlicense: 条件なし"),
    ("cc0", "CC0: 条件なし"),
    ("blueoak", "BlueOak-1.0.0: ライセンス文の保持が必要"),
    ("ofl", "OFL: ライセンス文の同梱と予約フォント名の扱いに従うこと"),
    ("cc-by", "CC-BY: 帰属表示が必要"),
    ("epl", "EPL-2.0: 改変ファイルのソース提供とライセンス全文の保持が必要(弱いコピーレフト)"),
    ("afl", "AFL-2.1: 著作権表示とライセンス文の保持が必要"),
    ("hpnd", "HPND系: 著作権表示の保持が必要"),
    ("mit-cmu", "MIT-CMU: 著作権表示と許諾文の保持が必要"),
]

# Facts that differ from the generic family. Each is from local metadata/files, or marked
# as an upstream-known matter that still needs checking against the shipped wheel/image.
GENERIC = {"dual license", "license", "other", "unknown"}

PY_SPECIAL = {
    "pymupdf": {
        "conditions": [
            "AGPL-3.0 または Artifex 商用ライセンスの二重ライセンス。本件はAGPL条件で使う想定(NOTICE.md)",
            "配布(wheelを要求するだけの配布は除く)・Dockerイメージ・ネットワーク提供のいずれでも、対応するソースの入手手段と全文の提示が必要",
            "同梱の MuPDF(libmupdf)もAGPL",
        ],
        "remaining": [
            "dist-info の COPYING は『Dual Licensed - GNU AFFERO GPL 3.0 or Artifex Commercial License』の1行だけで、AGPL全文は同梱されない",
            "本体(MIT)との結合物としてAGPLが及ぶ範囲の法的判断は未了(人と法務の確認が必要)",
            "AGPLの対応ソース(pymupdf 1.28.2 と MuPDF)の入手先を配布物に明記する必要",
        ],
    },
    "certifi": {"remaining": ["MPL-2.0。改変なしで再配布する前提を確認"]},
    "tqdm": {"remaining": ["MPL-2.0 AND MIT の二重表記。MPL部分の対象ファイルを未確認"]},
    "torch": {
        "remaining": [
            "wheel内に多数の第三者ライブラリ(License-File 91件)を含む。告知は各ファイルを保持して配布する必要",
            "Dockerfile は https://download.pytorch.org/whl/cpu のwheelを使う。lock記載のPyPI版と実体が一致するか未照合",
        ]
    },
    "torchvision": {
        "remaining": ["Dockerfile は https://download.pytorch.org/whl/cpu のwheelを使う。実体未照合"]
    },
    "numpy": {"remaining": ["wheel同梱ライブラリのライセンス17件を含む。告知の保持を確認"]},
    "scipy": {
        "remaining": [
            "wheelがOpenBLAS/libgfortran等を同梱しうる(上流の既知事項)。dist-infoにはLicense-Fileがなく、実体を未確認"
        ]
    },
    "pandas": {
        "remaining": [
            "wheel同梱の第三者部品のライセンス文がdist-infoに見当たらない。実体を未確認"
        ]
    },
    "pypdfium2": {
        "conditions": ["BSD-3-Clause / Apache-2.0 と、同梱PDFiumおよびその依存のライセンス(19件)の保持が必要"],
        "remaining": ["同梱PDFium依存のライセンス文(License-File 19件)をDockerイメージ内で確認"],
    },
    "opencv-python-headless": {
        "remaining": [
            "wheelが FFmpeg などを同梱しうる(上流の既知事項。LGPL等)。dist-info には2件のLicense-Fileだけで、実体を未確認",
            "pyproject.tomlのextrasが要求しない(TableFormerが使う)。宣言と配布のずれを解消する必要",
        ]
    },
    "marko": {"remaining": ["pyproject.tomlのextrasが要求しない(MD入力の取込に必要)。宣言と配布のずれを解消する必要"]},
    "openai-codex-cli-bin": {
        "conditions": ["Apache-2.0。同梱のrg(ripgrep)とzshの条件は別"],
        "remaining": [
            "Codex CLI本体、rg、zshの実行ファイルを同梱するが、dist-infoにライセンスファイルがない",
            "Rust製バイナリが静的に含む第三者crateの告知を確認できない",
            ".venv はarm64 macOS用。Dockerのlinux用ビルドは別の実体で未確認",
        ],
    },
    "openai-codex": {"remaining": ["dist-info にライセンスファイルなし(Apache-2.0の表記のみ)"]},
    "cryptography": {"remaining": ["wheelがOpenSSLを同梱する。OpenSSLのライセンス文を未確認"]},
    "lxml": {"remaining": ["wheelがlibxml2/libxslt等を静的に含む。それらのライセンス文を未確認"]},
    "hf-xet": {"remaining": ["Rust製。静的に含む第三者crateの告知を未確認"]},
    "pydantic-core": {"remaining": ["Rust製。静的に含む第三者crateの告知を未確認"]},
    "tokenizers": {"remaining": ["dist-infoにライセンスファイルなし。Rust製で第三者crateの告知を未確認"]},
    "safetensors": {"remaining": ["Rust製。静的に含む第三者crateの告知を未確認"]},
    "rpds-py": {"remaining": ["Rust製。静的に含む第三者crateの告知を未確認"]},
    "ruff": {"remaining": ["開発用。Dockerイメージに入るが実行されない(classification.json)"]},
    "ty": {"remaining": ["開発用。Dockerイメージに入るが実行されない(classification.json)"]},
    "pytest": {"remaining": ["開発用。Dockerイメージに入るが実行されない(classification.json)"]},
    "iniconfig": {"remaining": ["開発用。Dockerイメージに入るが実行されない(classification.json)"]},
    "reportlab": {"remaining": ["開発用。Dockerイメージに入るが実行されない(classification.json)"]},
}


def conditions_for(expression: str | None) -> list[str]:
    if not expression:
        return ["ライセンス表記が取得できない"]
    low = expression.lower()
    found = []
    for key, text in FAMILY_CONDITIONS:
        if key in low and text not in found:
            if key == "gpl" and ("lgpl" in low or "agpl" in low) and "gpl " not in low.replace(
                "lgpl", ""
            ).replace("agpl", ""):
                continue
            found.append(text)
    return found or ["表記を既知の区分に分類できない。条項の確認が必要"]


def python_license(name: str) -> tuple[str | None, int]:
    try:
        dist = metadata.distribution(name)
    except metadata.PackageNotFoundError:
        return None, 0
    meta = dist.metadata
    expression = meta.get("License-Expression")
    if not expression:
        text = meta.get("License")
        if text and len(text) <= 80 and "\n" not in text and text.lower() not in GENERIC:
            expression = text
    if not expression:
        classifiers = [
            c.split("::")[-1].strip() for c in meta.get_all("Classifier") or [] if c.startswith("License ::")
        ]
        expression = " / ".join(classifiers) or None
    return expression, len(meta.get_all("License-File") or [])


def python_records(package: dict) -> dict:
    name, version = package["name"], package["version"]
    expression, files = python_license(name)
    special = PY_SPECIAL.get(name.lower(), {})
    remaining = [HUMAN]
    if expression is None:
        remaining.append(".venv に実体がなくメタデータを確認できない")
    elif files == 0 and name.lower() not in PY_SPECIAL:
        remaining.append("dist-info にライセンスファイルがない(表記のみ)")
    remaining += special.get("remaining", [])
    if package.get("platform") == "darwin":
        remaining.append("macOS専用。Dockerイメージには入らない")
    if package["note"] and package["scope"] in {"runtime", "optional"} and "extras" in package["note"]:
        remaining.append(package["note"])
    return {
        "name": name,
        "version": version,
        "ecosystem": "pypi",
        "status": "inventoried",
        "reviewer": REVIEWER,
        "reviewed_on": DATE,
        "source": package["source"],
        "scope": package["scope"],
        "direct": package["direct"],
        "distribution": package["distribution"],
        "observed_license": expression,
        "license_evidence": f".venv dist-info METADATA (License-File {files}件)",
        "conditions": special.get("conditions") or conditions_for(expression),
        "remaining": remaining,
    }


def notice_names() -> set[str]:
    """Package names that static/frontend/licenses.txt actually carries a notice for."""
    path = ROOT / "src/docling_desk/resources/static/frontend/licenses.txt"
    if not path.is_file():
        return set()
    text = path.read_text(encoding="utf-8")
    names = {m.group(1) for m in re.finditer(r"^(@?[^\s]+) \d[^\s]* \(", text, re.M)}
    return names | {"react", "react-dom", "scheduler", "query-core", "@tanstack/react-query"} | {
        "ag-grid-community",
        "ag-grid-react",
    }


NOTICE_NAMES = notice_names()


def npm_dir(name: str, version: str) -> Path | None:
    store = ROOT / "frontend/node_modules/.pnpm"
    prefix = name.replace("/", "+") + "@" + version
    for entry in sorted(store.glob(prefix + "*")):
        if entry.name == prefix or entry.name.startswith(prefix + "_") or entry.name.startswith(prefix + "("):
            candidate = entry / "node_modules" / name
            if (candidate / "package.json").is_file():
                return candidate
    return None


def license_of(manifest: dict) -> str | None:
    value = manifest.get("license")
    if isinstance(value, dict):
        value = value.get("type")
    if not value and isinstance(manifest.get("licenses"), list):
        value = " OR ".join(str(item.get("type")) for item in manifest["licenses"] if isinstance(item, dict))
    return value if isinstance(value, str) and value else None


def node_record(package: dict, folder: Path | None, ecosystem: str) -> dict:
    expression = None
    has_text = None
    if folder is not None:
        manifest = json.loads((folder / "package.json").read_text(encoding="utf-8"))
        expression = license_of(manifest)
        has_text = any(
            item.name.lower().startswith(("license", "licence", "copying")) for item in folder.iterdir()
        )
    remaining = [HUMAN]
    if folder is None:
        remaining.append("node_modules に実体がなくメタデータを確認できない")
    if expression is None and folder is not None:
        remaining.append("package.json にlicense表記がない")
        for item in sorted(folder.iterdir()):
            if item.name.lower().startswith(("license", "licence", "copying")) and item.is_file():
                head = item.read_text(encoding="utf-8", errors="replace")[:300]
                if "MIT License" in head or "Permission is hereby granted, free of charge" in head:
                    expression = "MIT (LICENSEファイルの本文から推定。package.jsonに表記なし)"
                break
        else:
            remaining.append("package内にライセンス文ファイルもない。配布物の告知を作れない")
    if has_text is False:
        remaining.append("package内にライセンス文ファイルがない")
    if expression and "GPL" in expression and " OR " in expression:
        remaining.append("OR の選択肢からMITなど許容的な側を選ぶ前提。選択の記録が必要")
    if expression and "MPL" in expression and package["scope"] == "runtime":
        remaining.append("MPLの選択肢/ファイルを含む。改変なしで配布する前提を確認")
    if ecosystem == "npm" and package["scope"] == "runtime" and NOTICE_NAMES:
        if package["name"] not in NOTICE_NAMES:
            remaining.append(
                "static/frontend/licenses.txt に告知がない(export-static.mjs が dependencies の再帰から外れる)"
            )
    if package["scope"] == "build":
        remaining.append("dependencies にあるがビルド専用。生成物にだけ影響する(classification.json)")
    return {
        "name": package["name"],
        "version": package["version"],
        "ecosystem": ecosystem,
        "status": "inventoried",
        "reviewer": REVIEWER,
        "reviewed_on": DATE,
        "source": package["source"],
        "scope": package["scope"],
        "direct": package["direct"],
        "distribution": package["distribution"],
        "observed_license": expression,
        "license_evidence": "node_modules の package.json"
        + ("" if has_text is None else f" / ライセンス文ファイル{'あり' if has_text else 'なし'}"),
        "conditions": conditions_for(expression) if package["scope"] != "build" else ["生成物にpackageを含まない"],
        "remaining": remaining,
    }


def bun_folder(name: str) -> Path | None:
    candidate = ROOT / "knowledge-api/node_modules" / name
    return candidate if (candidate / "package.json").is_file() else None


def manual_records(report: dict) -> list[dict]:
    def record(name, version, kind, license_, scope, conditions, remaining, source, evidence):
        return {
            "name": name,
            "version": version,
            "kind": kind,
            "status": "inventoried",
            "reviewer": REVIEWER,
            "reviewed_on": DATE,
            "source": source,
            "distribution": scope,
            "observed_license": license_,
            "license_evidence": evidence,
            "conditions": conditions,
            "remaining": [HUMAN, *remaining],
        }

    out = []
    for model in json.loads((ROOT / "src/docling_desk/resources/models/manifest.json").read_text()):
        out.append(
            record(
                model["repo"],
                model["revision"],
                "model",
                model.get("declared_license"),
                ["docker"],
                [
                    "モデルカードの宣言のみ。重みの利用許諾は別に確認が必要",
                    "取得時に上流の告知(README等)を保持する(NOTICE.md)",
                ],
                [
                    "重みファイルそのものの条件と、学習データ由来の条件を未確認",
                    "wheel/sdistにはモデルを含まない(manifest.json のみ)。Docker のmodelsステージが実行時にHugging Faceから取得する",
                    "patterns が null のrepoは全ファイルを取得する。実際の取得ファイル一覧を未照合"
                    if model["patterns"] is None
                    else "patterns で絞った取得物だけが対象。実取得ファイルを未照合",
                ],
                model["license_source"],
                "モデルカード(固定revision)の宣言。ネットワーク確認はしていない",
            )
        )
    debian = "Debian bookworm パッケージ。イメージ内 /usr/share/doc/<package>/copyright で確認する"
    for name in [a["name"] for a in report["assets"] if a["kind"] == "os-package"]:
        extra = []
        if name.startswith("libreoffice"):
            extra = ["推移依存を含む大量のDebianパッケージが入る。完全な一覧はビルド済みイメージの dpkg -l が必要"]
        if name.startswith("fonts-"):
            extra = ["フォントの条件(OFL等)は版により異なる。イメージ内のcopyrightで確認する"]
        out.append(
            record(
                name,
                None,
                "os-package",
                None,
                ["docker"],
                ["Debianパッケージのcopyrightとソース入手手段(GPL/LGPL等を含む場合)を配布条件として確認する"],
                [debian, "Dockerを起動・ビルドしていないため版と推移依存を未取得", *extra],
                "Dockerfile",
                "Dockerfile の apt-get install 行",
            )
        )
    images: dict[str, dict] = {}
    for image in report["assets"]:
        if image["kind"] not in {"base-image", "build-image"}:
            continue
        entry = images.setdefault(
            image["name"], {"kind": "build-image", "sources": [], "distribution": set()}
        )
        if image["kind"] == "base-image":
            entry["kind"] = "base-image"
        entry["sources"].append(f"{image['source']}({image['kind']})")
        entry["distribution"] |= set(image["distribution"])
    for name, entry in images.items():
        shipped = entry["kind"] == "base-image"
        out.append(
            record(
                name,
                None,
                entry["kind"],
                None,
                sorted(entry["distribution"]),
                ["最終イメージの場合、OS層に含まれるGPL/LGPL等のパッケージの告知とソース入手手段が必要"]
                if shipped
                else ["最終イメージには入らない。成果物のライセンスは各項目で扱う"],
                [
                    "タグ固定のみでdigestを固定していない(版が動く)",
                    "イメージ内の実体を未取得",
                    *(
                        ["Bunランタイムは静的にJavaScriptCore(LGPL)等を含む(上流の既知事項)。実体を未確認"]
                        if name.startswith("oven/bun")
                        else []
                    ),
                ],
                ", ".join(entry["sources"]),
                "Dockerfile の FROM 行",
            )
        )
    assets = json.loads((ROOT / "supply/assets.json").read_text(encoding="utf-8"))["assets"]
    details = {
        "docling-slim": (
            "MIT",
            ["MIT: 著作権表示と許諾文の保持が必要。docling/LICENSE を配布物へ残す"],
            ["wheel/sdistには含まれず、Dockerイメージだけがソースを同梱", "依存する推移packageはロックで別途扱う"],
            "docling/LICENSE と docling/pyproject.toml",
        ),
        "docling-azure-ocr": (
            "MIT",
            ["MIT: 著作権表示と許諾文の保持が必要"],
            ["wheel が要求する docling-azure-ocr==0.1.0 の入手先(index)が確認できない"],
            "packages/docling-azure-ocr/LICENSE",
        ),
        "ag-grid-community": (
            "MIT",
            ["MIT: ag-grid-LICENSE.txt を配布物に残す(現状同梱)"],
            ["manifest.json の sha256 と実ファイルの一致は tests/test_supply_inventory.py で確認する"],
            "vendor/manifest.json と ag-grid-LICENSE.txt",
        ),
        "tabulens (port)": (
            "MIT",
            ["MIT: tabulens-LICENSE.txt を残す(現状同梱)"],
            ["移植元(../tabulens)がこのリポジトリ外にあり、移植元の版を再現できない"],
            "vendor/tabulens-LICENSE.txt(著作権者 y.noguchi)",
        ),
        "draw.io viewer": (
            "Apache-2.0 + 素材の追加条件",
            [
                "Apache-2.0: LICENSE(全文)を同梱し改変の明示。上流にNOTICEがあれば保持する",
                "アイコン・図形素材: Atlassian製品/マーケットプレイスの資産としての利用と、素材を参照・訓練入力とする派生を明示許可なく禁止(利用者の図の出力は対象外)",
                "MathJax(drawio-mathjax.min.js)は Apache-2.0(mathjax-LICENSE)",
            ],
            [
                "上流のNOTICEファイルが同梱されていない。必要性を未確認",
                "追加条件が本製品の配布・利用方法(Atlassian製品へ組込まない)に抵触しないことの確認が人の判断として未了",
            ],
            "vendor/drawio/{LICENSE,mathjax-LICENSE,img-LICENSE,shapes-LICENSE,stencils-LICENSE} と manifest.json",
        ),
        "Plate editor-kit registry": (
            "MIT",
            ["MIT: frontend/src/plate/LICENSE の著作権表示を、コンパイル後の配布物でも保持する"],
            [
                "コンパイル後の配布物(static/frontend)にこのLICENSE文が入るか未照合(licenses.txt は package の LICENSE を集める)",
                "上流の版は取得日(2026-10-16)だけで固定される",
            ],
            "frontend/src/plate/LICENSE",
        ),
        "shadcn ui primitives": (
            "MIT",
            ["MIT: 著作権表示(shadcn)の保持が必要"],
            ["コンパイル後の配布物にこのLICENSE文が入るか未照合"],
            "frontend/src/plate/ui-primitives/LICENSE",
        ),
        "excalidraw fonts": (
            None,
            ["フォントごとのライセンス(上流の宣言ではOFL等が中心)に従う。ライセンス文の同梱が求められる場合がある"],
            [
                "フォントファイルはライセンス文なしで複製されている(@excalidraw/excalidraw のnpm packageに個別の文がない)",
                "9ファミリー(Assistant, Cascadia, ComicShanns, Excalifont, Liberation, Lilita, Nunito, Virgil, Xiaolai)の個別条件を未確認",
                "NOTICE.md にフォントの記載がない",
            ],
            "src/docling_desk/resources/static/frontend/excalidraw/fonts と @excalidraw/excalidraw 0.18.1 のpackage",
        ),
        "KaTeX fonts": (
            None,
            ["katex本体はMIT(licenses.txt に入る)。フォント個別の条件は別"],
            ["KaTeX_*.ttf/woff/woff2 の個別条件を未確認(ttfとwoffは出力に含まれる)"],
            "static/frontend/assets/KaTeX_*",
        ),
        "tiktoken encodings (cl100k_base, o200k_base)": (
            None,
            ["語彙ファイルの利用条件を確認する必要がある"],
            [
                "初回利用時にネットワーク取得される未固定の資産で、モデル台帳に載っていない",
                "オフライン起動(HF_HUB_OFFLINE=1 の Docker)では初回の呼び出しが失敗しうる。TIKTOKEN_CACHE_DIR で固定する方針が未決",
            ],
            "src/docling_desk/knowledge/store.py ほか、.venv の tiktoken_ext/openai_public.py の取得先URL",
        ),
        "openai-codex-cli-bin bundled binaries": (
            "Apache-2.0 (codex)",
            ["codex本体はApache-2.0。rg(ripgrep)とzshはそれぞれの条件に従う"],
            [
                "同梱の rg と zsh のライセンス文が見当たらない",
                "Rust製バイナリが静的に含む第三者crateの告知を確認できない",
            ],
            ".venv/lib/python3.12/site-packages/openai_codex_cli_bin-0.160.0.dist-info/RECORD",
        ),
    }
    for asset in assets:
        license_, conditions, remaining, evidence = details[asset["name"]]
        out.append(
            record(
                asset["name"],
                asset.get("version"),
                asset["kind"],
                license_,
                asset["distribution"],
                conditions,
                remaining,
                asset["source"],
                evidence,
            )
        )
    return out


def main() -> None:
    report = supply.inventory(ROOT)
    records = []
    for package in report["packages"]:
        if package["ecosystem"] == "pypi":
            records.append(python_records(package))
        elif package["ecosystem"] == "npm" and package["scope"] in {"runtime", "build"}:
            records.append(node_record(package, npm_dir(package["name"], package["version"]), "npm"))
        elif package["ecosystem"] == "bun" and package["scope"] == "runtime":
            records.append(node_record(package, bun_folder(package["name"]), "bun"))
    records += manual_records(report)
    path = ROOT / "supply/clearance.json"
    existing = json.loads(path.read_text(encoding="utf-8"))
    kept = [r for r in existing.get("records", []) if r.get("status") != "inventoried"]
    existing["records"] = kept + records
    existing["note"] = (
        "確認した項目だけ status を confirmed にし、reviewer と reviewed_on と version を残します。"
        "不明な項目をまとめて確認済みにしません。"
        "status が inventoried の記録は自動棚卸しの観測値で、人の確認ではなく、利用権の確認済みとして扱いません。"
    )
    lines = [json.dumps(r, ensure_ascii=False) for r in existing["records"]]
    head = json.dumps({k: v for k, v in existing.items() if k != "records"}, ensure_ascii=False)
    body = ",\n".join("  " + line for line in lines)
    path.write_text(head[:-1] + ',\n "records": [\n' + body + "\n ]\n}\n", encoding="utf-8")
    print(len(kept), "kept,", len(records), "inventoried")


if __name__ == "__main__":
    main()
