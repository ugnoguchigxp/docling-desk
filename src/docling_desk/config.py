"""Separate installed resources, writable state and optional checkout tooling."""

import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

PACKAGE_ROOT = Path(__file__).resolve().parent
RESOURCES = PACKAGE_ROOT / "resources"
# Only an editable/source checkout uses its existing data and .env by default.
_candidate = PACKAGE_ROOT.parents[1]
PROJECT_ROOT = (
    _candidate
    if (_candidate / "pyproject.toml").is_file()
    and (_candidate / "src/docling_desk").resolve() == PACKAGE_ROOT
    else None
)
STATE_ROOT = (
    Path(
        os.environ.get(
            "DOCLING_STATE_DIR", str(PROJECT_ROOT or Path.home() / ".local/share/docling-desk")
        )
    )
    .expanduser()
    .resolve()
)
ENV_FILE = Path(os.environ.get("DOCLING_ENV_FILE", str(STATE_ROOT / ".env"))).expanduser()
load_dotenv(ENV_FILE, override=False)
DATA = Path(os.environ.get("DOCLING_DATA_DIR", str(STATE_ROOT / "data"))).expanduser().resolve()
MODELS = (
    Path(os.environ.get("DOCLING_MODELS_DIR", str(STATE_ROOT / "models"))).expanduser().resolve()
)
CACHE = Path(os.environ.get("DOCLING_CACHE_DIR", str(STATE_ROOT / ".cache"))).expanduser().resolve()
MODEL_MANIFEST = RESOURCES / "models/manifest.json"
ALLOWED_HOSTS = [
    value.strip()
    for value in os.environ.get("DOCLING_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
    if value.strip()
]
ALLOWED_ORIGINS = {
    value.strip().rstrip("/")
    for value in os.environ.get("DOCLING_ALLOWED_ORIGINS", "").split(",")
    if value.strip()
}


def _root_path() -> str:
    """Reverse-proxy prefix such as /assessment; the proxy strips it before forwarding."""
    value = os.environ.get("DOCLING_ROOT_PATH", "").strip().rstrip("/")
    if value and not re.fullmatch(r"/[A-Za-z0-9._~-]+(?:/[A-Za-z0-9._~-]+)*", value):
        raise RuntimeError("DOCLING_ROOT_PATHは /assessment のような絶対パスで指定してください。")
    return value


ROOT_PATH = _root_path()


def url(path: str) -> str:
    """Public URL of an app path when served below a reverse-proxy prefix."""
    return ROOT_PATH + path if path.startswith("/") else path


AUTH_MODE = os.environ.get("DOCLING_AUTH_MODE", "none").strip().lower()
AUTH_COOKIE = os.environ.get("DOCLING_AUTH_COOKIE", "mplm_access_token").strip()
# This app's own copy of the verified token; empty disables it. See web/auth.py.
AUTH_SESSION_COOKIE = os.environ.get("DOCLING_AUTH_SESSION_COOKIE", "docling_session").strip()
AUTH_TOKEN_TYPE = os.environ.get("DOCLING_AUTH_TOKEN_TYPE", "access").strip()
AUTH_ISSUER = os.environ.get("DOCLING_AUTH_ISSUER", "").strip()
AUTH_AUDIENCE = os.environ.get("DOCLING_AUTH_AUDIENCE", "").strip()
AUTH_USER_CLAIM = os.environ.get("DOCLING_AUTH_USER_CLAIM", "userId").strip()
AUTH_LOGIN_URL = os.environ.get("DOCLING_AUTH_LOGIN_URL", "").strip()
# How the login page is told where to return: the parameter name, and whether the
# value is the absolute URL ("url") or the site-internal path with query ("path").
AUTH_RETURN_PARAM = os.environ.get("DOCLING_AUTH_RETURN_PARAM", "next").strip()
AUTH_RETURN_FORMAT = os.environ.get("DOCLING_AUTH_RETURN_FORMAT", "url").strip().lower()
if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*", AUTH_RETURN_PARAM):
    raise RuntimeError("DOCLING_AUTH_RETURN_PARAMの形式が不正です。")
if AUTH_RETURN_FORMAT not in {"url", "path"}:
    raise RuntimeError("DOCLING_AUTH_RETURN_FORMATは url か path を指定してください。")
AUTH_LEEWAY = int(os.environ.get("DOCLING_AUTH_LEEWAY_SECONDS", "30"))


def _auth_secret() -> bytes:
    secret_file = os.environ.get("DOCLING_AUTH_JWT_SECRET_FILE", "").strip()
    if secret_file:
        return Path(secret_file).expanduser().read_text().strip().encode()
    return os.environ.get("DOCLING_AUTH_JWT_SECRET", "").strip().encode()


AUTH_JWT_SECRET = _auth_secret()
if AUTH_MODE not in {"none", "jwt"}:
    raise RuntimeError("DOCLING_AUTH_MODEは none か jwt を指定してください。")
if AUTH_MODE == "jwt" and len(AUTH_JWT_SECRET) < 32:
    raise RuntimeError(
        "DOCLING_AUTH_MODE=jwt には32バイト以上の DOCLING_AUTH_JWT_SECRET"
        "（または DOCLING_AUTH_JWT_SECRET_FILE）が必要です。"
    )
# Where originals and the Wiki are kept. The local filesystem stays the source of
# truth; ``azure-blob`` additionally mirrors ``content/`` to a Blob container.
STORAGE_BACKEND = os.environ.get("DOCLING_STORAGE", "local").strip().lower()
if STORAGE_BACKEND not in {"local", "azure-blob"}:
    raise RuntimeError("DOCLING_STORAGEは local か azure-blob を指定してください。")
BLOB_CONNECTION_STRING = os.environ.get("DOCLING_BLOB_CONNECTION_STRING", "").strip()
BLOB_ACCOUNT_URL = os.environ.get("DOCLING_BLOB_ACCOUNT_URL", "").strip().rstrip("/")
BLOB_CONTAINER = os.environ.get("DOCLING_BLOB_CONTAINER", "").strip()
BLOB_PREFIX = os.environ.get("DOCLING_BLOB_PREFIX", "").strip().strip("/")
BLOB_CLIENT_ID = os.environ.get("DOCLING_BLOB_CLIENT_ID", "").strip()
BLOB_SYNC_DERIVED = os.environ.get("DOCLING_BLOB_SYNC_DERIVED", "1").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
BLOB_INTERVAL = max(5, int(os.environ.get("DOCLING_BLOB_INTERVAL_SECONDS", "30")))
if STORAGE_BACKEND == "azure-blob":
    if not BLOB_CONTAINER or not (BLOB_CONNECTION_STRING or BLOB_ACCOUNT_URL):
        raise RuntimeError(
            "DOCLING_STORAGE=azure-blob には DOCLING_BLOB_CONTAINER と、"
            "DOCLING_BLOB_ACCOUNT_URL（Managed Identity）または "
            "DOCLING_BLOB_CONNECTION_STRING が必要です。"
        )
MAX_BYTES = 50 * 1024 * 1024
MIN_FREE = 2 * 1024**3
MAX_PAGES = int(os.environ.get("DOCLING_MAX_PAGES", str(sys.maxsize)))
if MAX_PAGES < 1:
    raise ValueError("DOCLING_MAX_PAGES は1以上にしてください。")
for name, value in {
    "HF_HUB_OFFLINE": "1",
    "TRANSFORMERS_OFFLINE": "1",
    "HF_HOME": os.environ.get("HF_HOME", str(STATE_ROOT / ".hf")),
    "HF_HUB_DISABLE_TELEMETRY": "1",
}.items():
    os.environ[name] = value


def checkout_root() -> Path:
    """Development checks need a source checkout, never writable site-packages."""
    if PROJECT_ROOT is None:
        raise RuntimeError("この操作はソースのチェックアウトから実行してください。")
    return PROJECT_ROOT
