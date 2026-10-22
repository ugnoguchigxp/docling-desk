"""Per-feature diagnostics built from local state only.

Nothing here calls an external service, so a diagnosis never costs money or depends
on a provider being reachable. It reports whether each feature has what it needs
(configuration, installed tools, local models) and what to do when it does not.
Responses carry no credentials, paths, endpoints, or file names. They are served
below ``/api/``, so the login check applies; ``/health/*`` stays the public probe.
"""

from __future__ import annotations

import importlib.util
import logging
import os
import shutil
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from docling_desk import config
from docling_desk.config import MIN_FREE
from docling_desk.operations.models import MANIFEST_HINT
from docling_desk.operations.models import diagnose as model_diagnosis

LOG = logging.getLogger(__name__)
VERSION = 1
OK, NOT_CONFIGURED, TEMPORARY, DISABLED = "ok", "not_configured", "temporary_failure", "disabled"
AVAILABLE = "available"
QUICKLOOK = Path("/usr/bin/qlmanage")
SANDBOX_EXEC = Path("/usr/bin/sandbox-exec")

Check = dict[str, Any]


def _platform() -> str:
    return "darwin" if sys.platform == "darwin" else "linux" if sys.platform == "linux" else "other"


def _which(name: str) -> str | None:
    return shutil.which(name)


def _powerpoint() -> bool:
    from docling_desk.preview.powerpoint_export import POWERPOINT

    return POWERPOINT.is_dir()


def _check(
    ident: str,
    label: str,
    status: str = OK,
    *,
    required: bool = True,
    code: str | None = None,
    recovery: str | None = None,
) -> Check:
    return {
        "id": ident,
        "label": label,
        "required": required,
        "status": status,
        "code": code if status != OK else None,
        "recovery": recovery if status != OK else None,
    }


def _tool(
    ident: str, label: str, found: bool, code: str, recovery: str, *, required: bool = True
) -> Check:
    return _check(
        ident,
        label,
        OK if found else NOT_CONFIGURED,
        required=required,
        code=code,
        recovery=recovery,
    )


def _disk() -> Check:
    try:
        enough = shutil.disk_usage(config.DATA).free >= MIN_FREE
        code = "disk_space"
    except OSError:
        enough, code = False, "disk_unreadable"
    return _check(
        "disk_space",
        "保存先の空き容量",
        OK if enough else TEMPORARY,
        code=code,
        recovery="保存先の空き容量を2 GiB以上にしてください。不要な資料の削除やバックアップ済みデータの退避で戻ります。",
    )


def _models() -> Check:
    try:
        problems = model_diagnosis(config.MODELS, config.MODEL_MANIFEST)
    except OSError:
        return _check(
            "models",
            "抽出モデル",
            TEMPORARY,
            code="models_unreadable",
            recovery="モデルの保存先を読み取れません。DOCLING_MODELS_DIRの権限とマウントを確認してください。",
        )
    except ValueError:  # an unreadable manifest has its own recovery, not a generic error
        return _check(
            "models",
            "抽出モデル",
            NOT_CONFIGURED,
            code="models_manifest_invalid",
            recovery=MANIFEST_HINT,
        )
    if not problems:
        return _check("models", "抽出モデル")
    # An interrupted swap is recovered first; otherwise report the first problem.
    chosen = next((item for item in problems if item["state"] == TEMPORARY), problems[0])
    return _check(
        "models", "抽出モデル", chosen["state"], code=chosen["code"], recovery=chosen["recovery"]
    )


def _libreoffice(ident: str) -> Check:
    return _tool(
        ident,
        "LibreOffice",
        bool(_which("libreoffice") or _which("soffice")),
        "libreoffice_missing",
        "LibreOffice（Impress・Writer・Calc）を導入してください。Dockerイメージには同梱されています。",
    )


def local_viewing() -> list[Check]:
    frontend = (config.RESOURCES / "static/frontend/index.html").is_file()
    data = config.DATA.is_dir() and os.access(config.DATA, os.W_OK)
    restoring = (config.DATA / "runtime" / "blob-restore.incomplete").exists()
    return [
        _tool(
            "frontend",
            "画面",
            frontend,
            "frontend_missing",
            "画面を構築してください。ソース導入は corepack pnpm --dir frontend build と "
            "corepack pnpm --dir frontend export:static、Dockerはイメージを再構築します。",
        ),
        _check(
            "data_dir",
            "保存先",
            OK if data else TEMPORARY,
            code="data_unwritable",
            recovery="保存先（DOCLING_DATA_DIR）が存在し、書き込めることを確認してください。"
            "Dockerはボリュームの権限（UID/GID 10001）を確認します。",
        ),
        _disk(),
        _check(
            "blob_restore",
            "Blobからの復元",
            TEMPORARY if restoring else OK,
            code="blob_restore_incomplete",
            recovery="Blobからの復元が完了していません。再試行は自動で続きます。"
            "解消しない場合は docs/content-storage.md のBlobミラーを確認してください。",
        ),
    ]


def conversion() -> list[Check]:
    checks = [
        _models(),
        _disk(),
        _tool(
            "docling",
            "Docling本体",
            importlib.util.find_spec("docling") is not None,
            "docling_missing",
            "ソース導入手順の pip install --no-deps ./docling ./packages/docling-azure-ocr を実行してください。",
        ),
    ]
    if _platform() == "darwin":
        checks.append(_check("local_ocr", "ローカルOCR（Apple）"))
    else:
        checks.append(
            _tool(
                "local_ocr",
                "ローカルOCR（Tesseract）",
                bool(_which("tesseract")),
                "tesseract_missing",
                "Tesseractと日本語・英語の言語データ（tesseract-ocr-jpn、tesseract-ocr-eng）を導入してください。"
                "Dockerイメージには同梱されています。",
            )
        )
    checks.append(_azure_ocr())
    return checks


def _azure_ocr() -> Check:
    """Optional. Same local credential check as ``/api/ocr``; it never calls Azure."""
    from docling_azure_ocr.config import OcrError, api_key, load_profile

    try:
        load_profile("azure_read").check_credentials(api_key())
    except (ValueError, RuntimeError, OcrError):
        return _check(
            "azure_ocr",
            "Azure OCR（任意）",
            NOT_CONFIGURED,
            required=False,
            code="azure_ocr_not_configured",
            recovery="Azure OCRを使う場合だけ、DOCLING_AZURE_OCR_ENDPOINTとAPIキー（またはManaged Identity）を設定してください。"
            "未設定でもローカルOCRで変換できます。",
        )
    return _check("azure_ocr", "Azure OCR（任意）", required=False)


def office_presentation() -> list[Check]:
    if _platform() == "darwin":
        return [
            _tool(
                "powerpoint",
                "Microsoft PowerPoint",
                _powerpoint(),
                "powerpoint_missing",
                "PPTXの高再現プレビューにはこのMacのMicrosoft PowerPointが必要です。"
                "原本のダウンロードと抽出本文の閲覧は使えます。",
            )
        ]
    if _platform() == "linux":
        return [_libreoffice("libreoffice")]
    return [_unsupported_platform()]


def office_documents() -> list[Check]:
    if _platform() == "darwin":
        return [
            _tool(
                "quicklook",
                "Quick Look",
                QUICKLOOK.is_file(),
                "quicklook_missing",
                "macOSのQuick Look（/usr/bin/qlmanage）を利用できません。OSの状態を確認してください。",
            )
        ]
    if _platform() == "linux":
        return [_libreoffice("libreoffice")]
    return [_unsupported_platform()]


def _unsupported_platform() -> Check:
    return _check(
        "platform",
        "対応OS",
        NOT_CONFIGURED,
        code="platform_unsupported",
        recovery="macOSまたはLinux（Docker推奨）で実行してください。",
    )


def tex_preview() -> list[Check]:
    checks = [
        _tool(
            "tectonic",
            "Tectonic",
            bool(_which("tectonic")),
            "tectonic_missing",
            "組版にはTectonicが必要です。macOSは brew install tectonic、Linuxはtectonicとbubblewrapを導入してください。"
            "同梱のDockerイメージには含まれません。TeXソースは導入なしで閲覧できます。",
        )
    ]
    if _platform() == "darwin":
        checks.append(
            _tool(
                "sandbox",
                "組版の隔離（sandbox-exec）",
                SANDBOX_EXEC.is_file(),
                "sandbox_missing",
                "macOSのsandbox-execを利用できません。OSの状態を確認してください。",
            )
        )
    else:
        checks.append(
            _tool(
                "sandbox",
                "組版の隔離（bubblewrap）",
                _platform() == "linux" and bool(_which("bwrap")),
                "sandbox_missing",
                "安全な組版にはbubblewrap（bwrap）が必要です。導入してから再取り込みしてください。",
            )
        )
    return checks


def semantic_search() -> list[Check]:
    from docling_desk.knowledge.embedding_azure import AzureEmbedding, EmbeddingError

    recovery = (
        "意味検索を使う場合だけ、DOCLING_AZURE_EMBEDDING_ENDPOINT・DOCLING_AZURE_EMBEDDING_KEY・"
        "DOCLING_AZURE_EMBEDDING_DEPLOYMENT・DOCLING_AZURE_EMBEDDING_MODEL_VERSIONをすべて設定してください。"
        "未設定でもWikiと全文検索は使えます。"
    )
    try:
        configured = AzureEmbedding.from_env() is not None
        code = "embedding_not_configured"
    except EmbeddingError:
        configured, code = False, "embedding_invalid"
    return [
        _check(
            "embedding",
            "Azure embedding",
            OK if configured else NOT_CONFIGURED,
            code=code,
            recovery=recovery,
        )
    ]


def _provider_check(
    ident: str,
    label: str,
    prefix: str,
    errors: type[Exception],
    build: Callable[[], Any],
    hints: dict[str, str],
) -> Check:
    """Run a provider's own local preflight and map its error code to a recovery."""
    try:
        build().preflight()
    except errors as error:
        code = str(getattr(error, "code", "configuration"))
        return _check(
            ident,
            label,
            NOT_CONFIGURED,
            code=f"{prefix}_{code}",
            recovery=hints.get(code, hints["configuration"]),
        )
    return _check(ident, label)


CODEX_HINTS = {
    "not_configured": "専用環境にCodex SDK（openai_codex）をインストールしてください。",
    "authentication": "Codexにログインするか、OPENAI_API_KEYを設定してください。",
}


def translation() -> list[Check]:
    from docling_desk.translation.provider import (
        TranslationError,
        configured_profile,
        provider_for,
    )

    hints = {
        **CODEX_HINTS,
        "configuration": "翻訳の設定値（DOCLING_TRANSLATION_*）を確認してください。",
    }
    if os.environ.get("DOCLING_TRANSLATION_PROVIDER") == "azure_openai":
        hints = {
            "authentication": "AZURE_OPENAI_API_KEY（またはDOCLING_AZURE_API_KEY）を設定してください。",
            "configuration": "DOCLING_TRANSLATION_PROVIDER=azure_openai には、"
            "HTTPSのDOCLING_AZURE_ENDPOINTとDOCLING_AZURE_DEPLOYMENTが必要です。",
        }
    return [
        _provider_check(
            "translation_provider",
            "翻訳の接続先",
            "translation",
            TranslationError,
            lambda: provider_for(configured_profile()),
            hints,
        )
    ]


def explanation() -> list[Check]:
    from docling_desk.explanation.provider import (
        ExplanationError,
        configured_profile,
        provider_for,
    )

    hints = {
        **CODEX_HINTS,
        "configuration": "解説の設定値（DOCLING_EXPLANATION_*）を確認してください。",
    }
    checks = [
        _provider_check(
            "explanation_provider",
            "解説の接続先（Codex）",
            "explanation",
            ExplanationError,
            lambda: provider_for(configured_profile()),
            hints,
        )
    ]
    try:
        web = configured_profile().web_provider
    except ExplanationError:
        return checks
    if web == "disabled":
        checks.append(_check("web_search", "Web検索（任意）", DISABLED, required=False))
    elif web == "brave":
        checks.append(
            _tool(
                "web_search",
                "Web検索（Brave・任意）",
                bool(os.environ.get("BRAVE_SEARCH_API_KEY")),
                "brave_key_missing",
                "Brave Searchを使う場合は BRAVE_SEARCH_API_KEY を設定してください。"
                "解説の本体はWeb検索の設定なしでも利用できます。",
                required=False,
            )
        )
    else:
        checks.append(_check("web_search", "Web検索（DuckDuckGo・任意）", required=False))
    return checks


def blob_storage(mirror: object | None, *, runtime: bool) -> list[Check]:
    if config.STORAGE_BACKEND != "azure-blob":
        return [
            _check(
                "storage_backend",
                "Blobミラー",
                NOT_CONFIGURED,
                code="blob_not_configured",
                recovery="Blobへ複製する場合だけ DOCLING_STORAGE=azure-blob とDOCLING_BLOB_*を設定します。"
                "ローカル保存のままでも閲覧・検索は使えます。docs/content-storage.mdを参照してください。",
            )
        ]
    checks = [_check("storage_backend", "Blobミラー")]
    if not runtime:
        return checks
    if mirror is None:
        checks.append(
            _check(
                "mirror",
                "Blob同期",
                TEMPORARY,
                code="blob_mirror_inactive",
                recovery="Blob同期が起動していません。起動ログを確認し、DOCLING_BLOB_*を修正して再起動してください。",
            )
        )
        return checks
    try:
        status = getattr(mirror, "status", None)
        if not callable(status):
            raise ValueError("Blob mirror has no status reader")
        state = status()
    except Exception:  # noqa: BLE001 - a broken state file must not fail the report
        LOG.warning("Blob mirror status unreadable", exc_info=True)
        checks.append(
            _check(
                "mirror",
                "Blob同期",
                TEMPORARY,
                code="blob_status_unreadable",
                recovery="Blob同期の状態を読み取れません。ログを確認してください。",
            )
        )
        return checks
    code = (
        "blob_restore_incomplete"
        if state.get("restore_incomplete")
        else "blob_conflicts"
        if state.get("conflicts")
        else "blob_sync_errors"
        if state.get("errors")
        else None
    )
    checks.append(
        _check(
            "mirror",
            "Blob同期",
            TEMPORARY if code else OK,
            code=code,
            recovery="Blobとの同期に未解決の復元・競合・エラーがあります。"
            "GET /api/storage の件数を確認し、docs/content-storage.mdのBlobミラーに従って対処してください。",
        )
    )
    return checks


def _feature(
    ident: str,
    label: str,
    description: str,
    collect: Callable[[], list[Check]],
    *,
    optional: bool = False,
    depends_on: tuple[str, ...] = (),
) -> dict[str, Any]:
    try:
        checks = collect()
    except Exception:  # noqa: BLE001 - one broken probe must not hide the others
        LOG.exception("Diagnostic for %s failed", ident)
        checks = [
            _check(
                "diagnostic",
                "診断",
                TEMPORARY,
                code="diagnostic_error",
                recovery="診断中にエラーが発生しました。サーバーのログを確認してください。",
            )
        ]
    failing = [c for c in checks if c["required"] and c["status"] in {NOT_CONFIGURED, TEMPORARY}]
    if any(c["status"] == NOT_CONFIGURED for c in failing):
        status = NOT_CONFIGURED
    elif failing:
        status = TEMPORARY
    else:
        status = AVAILABLE
    return {
        "id": ident,
        "label": label,
        "description": description,
        "optional": optional,
        "depends_on": list(depends_on),
        "status": status,
        "reasons": [c["code"] for c in failing],
        "recovery": list(dict.fromkeys(c["recovery"] for c in failing if c["recovery"])),
        "notes": [
            c["code"]
            for c in checks
            if not c["required"] and c["status"] in {NOT_CONFIGURED, TEMPORARY}
        ],
        "prerequisites": checks,
    }


def build_report(blob_mirror: object | None = None, *, runtime: bool = True) -> dict[str, Any]:
    """The report served at ``GET /api/diagnostics/features``.

    ``runtime=False`` skips checks that need the running server (Blob sync state),
    for use from the command line.
    """
    return {
        "version": VERSION,
        "platform": _platform(),
        "features": [
            _feature(
                "local_viewing",
                "ローカル閲覧・全文検索",
                "取り込み済みの資料・Wikiの閲覧と全文検索。モデルや外部サービスは不要です。",
                local_viewing,
            ),
            _feature(
                "conversion",
                "資料の変換",
                "PDF・Office・Markdownの抽出。テキスト・画像・TeXソース・図の閲覧は変換モデルなしでも使えます。",
                conversion,
            ),
            _feature(
                "office_presentation",
                "PowerPointの原本プレビュー",
                "PPTXの原本表示。未設定でも抽出本文と原本ダウンロードは使えます。",
                office_presentation,
                optional=True,
                depends_on=("conversion",),
            ),
            _feature(
                "office_documents",
                "Excel・Wordの原本プレビュー",
                "XLSX・DOCXの原本表示。未設定でも抽出本文と原本ダウンロードは使えます。",
                office_documents,
                optional=True,
                depends_on=("conversion",),
            ),
            _feature(
                "tex_preview",
                "TeXの組版表示",
                "TeXを組版して表示します。ソース表示には不要です。",
                tex_preview,
                optional=True,
            ),
            _feature(
                "semantic_search",
                "意味検索",
                "embeddingによる意味検索。全文検索には不要です。",
                semantic_search,
                optional=True,
            ),
            _feature(
                "translation",
                "翻訳",
                "資料の翻訳。他の機能には不要です。",
                translation,
                optional=True,
            ),
            _feature(
                "explanation",
                "解説",
                "資料の解説。Web検索は任意です。",
                explanation,
                optional=True,
            ),
            _feature(
                "blob_storage",
                "Azure Blobミラー",
                "原本とWikiのBlobへの複製。ローカル保存が正本です。",
                lambda: blob_storage(blob_mirror, runtime=runtime),
                optional=True,
            ),
        ],
    }


def format_text(report: dict[str, Any]) -> str:
    names = {AVAILABLE: "利用可能", NOT_CONFIGURED: "未設定", TEMPORARY: "一時的な問題"}
    lines = []
    for feature in report["features"]:
        lines.append(f"[{names[feature['status']]}] {feature['label']}")
        for hint in feature["recovery"]:
            lines.append(f"    {hint}")
        for check in feature["prerequisites"]:
            if not check["required"] and check["status"] in {NOT_CONFIGURED, TEMPORARY}:
                lines.append(f"    (任意) {check['recovery']}")
    return "\n".join(lines)
