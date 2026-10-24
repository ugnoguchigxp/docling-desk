"""Optional shared-login check for deployments behind a reverse proxy.

The default (``DOCLING_AUTH_MODE=none``) keeps the loopback-only behaviour.
``jwt`` accepts an HS256 token issued by the host application, read from a
cookie or an ``Authorization: Bearer`` header, so users signed in there can
open this app without a second login.
``larm`` (chosen automatically when ``LARM_API_TOKEN`` is set) accepts the shared
LARM credential as a Bearer from backends such as Eumenes. Browsers never hold
it: they exchange a one-time ticket for this app's own session cookie.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import threading
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

from docling_desk import config

PUBLIC_PREFIXES = ("/health/", "/static/")
MIN_SECRET_BYTES = 32
SESSION_SECONDS = 15 * 60
EXCHANGE_PATH = "/auth/exchange"
LARM_COOKIE = "docling_larm_session"
LARM_SESSION_SECONDS = 14 * 24 * 60 * 60
# The sliding expiry is re-issued at most this often, not on every request.
LARM_RENEW_SECONDS = 60 * 60
TICKET_SECONDS = 60
STARTUP_TICKET_SECONDS = 10 * 60
MAX_TICKETS = 64
STATE_CHANGING = {"POST", "PATCH", "PUT", "DELETE"}
PREFIX = re.compile(r"/[A-Za-z0-9._~-]+(?:/[A-Za-z0-9._~-]+)*")

router = APIRouter()


class TokenError(Exception):
    pass


def _b64(part: str) -> bytes:
    try:
        return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))
    except (binascii.Error, ValueError) as error:
        raise TokenError("malformed") from error


def _object(part: str) -> dict[str, Any]:
    try:
        value = json.loads(_b64(part))
    except (UnicodeDecodeError, ValueError, RecursionError) as error:
        raise TokenError("malformed") from error
    if not isinstance(value, dict):
        raise TokenError("malformed")
    return value


def verify_token(
    token: str,
    secret: bytes,
    *,
    now: float | None = None,
    leeway: int = 30,
    token_type: str = "",
    issuer: str = "",
    audience: str = "",
) -> dict[str, Any]:
    """Return the claims of a valid HS256 token or raise TokenError."""
    parts = token.split(".")
    if len(parts) != 3:
        raise TokenError("malformed")
    header, claims = _object(parts[0]), _object(parts[1])
    # Only HS256 is accepted: never trust the header to pick a weaker algorithm.
    if header.get("alg") != "HS256":
        raise TokenError("algorithm")
    expected = hmac.new(secret, f"{parts[0]}.{parts[1]}".encode(), hashlib.sha256).digest()
    if not hmac.compare_digest(expected, _b64(parts[2])):
        raise TokenError("signature")
    moment = time.time() if now is None else now
    expires = claims.get("exp")
    if (
        not isinstance(expires, (int, float))
        or isinstance(expires, bool)
        or not math.isfinite(expires)
    ):
        raise TokenError("no expiry")
    if moment > expires + leeway:
        raise TokenError("expired")
    not_before = claims.get("nbf")
    if (
        isinstance(not_before, (int, float))
        and math.isfinite(not_before)
        and moment < not_before - leeway
    ):
        raise TokenError("not yet valid")
    if token_type and claims.get("type") != token_type:
        raise TokenError("token type")
    if issuer and claims.get("iss") != issuer:
        raise TokenError("issuer")
    if audience:
        claimed = claims.get("aud")
        if audience not in (claimed if isinstance(claimed, list) else [claimed]):
            raise TokenError("audience")
    return claims


def _bearer(request: Request) -> str:
    value = request.headers.get("authorization", "")
    scheme, _, token = value.partition(" ")
    return token.strip() if scheme.lower() == "bearer" else ""


def return_target(request: Request) -> str:
    """The address to come back to after login, in the configured format."""
    path = config.url(request.url.path)
    if config.AUTH_RETURN_FORMAT == "path":
        return path + (f"?{request.url.query}" if request.url.query else "")
    return str(request.url.replace(path=path))


def login_redirect(login: str, target: str) -> str:
    joiner = "&" if "?" in login else "?"
    return f"{login}{joiner}{config.AUTH_RETURN_PARAM}={quote(target, safe='')}"


def _login_required(request: Request) -> Response:
    login = config.AUTH_LOGIN_URL
    wants_page = request.method == "GET" and "text/html" in request.headers.get("accept", "")
    if login and wants_page:
        return RedirectResponse(login_redirect(login, return_target(request)), status_code=302)
    body: dict[str, str] = {"detail": "ログインが必要です。"}
    if login:
        # The browser knows which screen it is on, so it completes the redirect itself.
        body.update(
            login_url=login,
            return_param=config.AUTH_RETURN_PARAM,
            return_format=config.AUTH_RETURN_FORMAT,
        )
    return JSONResponse(body, status_code=401, headers={"Cache-Control": "no-store"})


def _rejected() -> Response:
    # A token that is present but not acceptable (wrong key, type, issuer...) will
    # not be fixed by logging in again; redirecting would only loop.
    return JSONResponse(
        {"detail": "ログイン情報を検証できません。管理者へ連絡してください。"},
        status_code=403,
        headers={"Cache-Control": "no-store"},
    )


def _session_cookie(response: Response, token: str, claims: dict[str, Any]) -> None:
    """Re-issue the verified token as this app's own cookie.

    Original-document viewers run in sandboxed iframes with an opaque origin, so
    browsers withhold SameSite=Lax cookies from their sub-requests (images, data).
    A SameSite=None cookie is sent; state-changing requests stay protected by the
    Origin check in local_only. It carries the same token and the same expiry.
    """
    name = config.AUTH_SESSION_COOKIE
    expires = claims.get("exp")
    if not name or not isinstance(expires, (int, float)):
        return
    response.set_cookie(
        name,
        token,
        # Short on purpose: it is re-issued while the shared login is valid, and must
        # not outlive a logout or revocation at the host application for long.
        max_age=max(0, min(int(expires - time.time()), SESSION_SECONDS)),
        path=config.root() or "/",
        secure=True,
        httponly=True,
        samesite="none",
    )


def _digest(value: str) -> bytes:
    return hashlib.sha256(value.encode()).digest()


def larm_bearer(request: Request) -> bool:
    """True when the request carries the shared LARM credential as its Bearer."""
    token = _bearer(request)
    # Comparing digests keeps the comparison constant-time whatever the lengths.
    return bool(token and config.LARM_TOKEN) and hmac.compare_digest(
        _digest(token), _digest(config.LARM_TOKEN)
    )


_keys: dict[Path, bytes] = {}
_keys_lock = threading.Lock()


def _create_secret(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".session.{secrets.token_hex(8)}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(secrets.token_bytes(32))
            stream.flush()
            os.fsync(stream.fileno())
        try:
            # link() never replaces: a key another process created first wins.
            os.link(temporary, path)
        except FileExistsError:
            pass
        except OSError:
            # Some volumes refuse hard links; rename still never exposes a partial key.
            if not path.exists():
                os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _local_secret() -> bytes:
    """A random key kept in runtime/keys/, created once without ever being replaced."""
    path = config.DATA / "runtime" / "keys" / "session.key"
    with _keys_lock:
        if path in _keys:
            return _keys[path]
        if not path.is_file():
            _create_secret(path)
        secret = path.read_bytes()
        if len(secret) < 32:
            raise RuntimeError(f"セッション鍵が不正です: {path}")
        _keys[path] = secret
        return secret


def _session_key() -> bytes:
    # Bound to the credential: rotating LARM_API_TOKEN ends every browser session.
    return hmac.new(
        _local_secret(), b"docling-larm-session\0" + _digest(config.LARM_TOKEN), hashlib.sha256
    ).digest()


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def _session_value(expires: int, prefix: str = "") -> str:
    """``v2.<expiry>.<prefix>.<mac>``: the prefix the browser reaches the app below."""
    payload = f"v2.{expires}.{_b64encode(prefix.encode())}"
    mac = hmac.new(_session_key(), payload.encode(), hashlib.sha256).digest()
    return f"{payload}.{_b64encode(mac)}"


def session_expiry(value: str, *, now: float | None = None) -> tuple[int, str] | None:
    """The expiry and prefix of a valid browser session cookie, or None."""
    parts = value.split(".")
    # Anything a client sends is checked as ASCII first: never raise before the login.
    if len(parts) != 4 or not value.isascii() or parts[0] != "v2":
        return None
    expires, encoded = parts[1], parts[2]
    if not (expires.isdecimal() and len(expires) <= 12):
        return None
    try:
        prefix = _b64(encoded).decode("ascii")
    except (TokenError, UnicodeDecodeError):
        return None
    if prefix and not valid_prefix(prefix):
        return None
    if not hmac.compare_digest(_session_value(int(expires), prefix).encode(), value.encode()):
        return None
    return (int(expires), prefix) if (time.time() if now is None else now) < int(expires) else None


def valid_prefix(prefix: str) -> bool:
    return bool(PREFIX.fullmatch(prefix)) and not {".", ".."} & set(prefix.split("/"))


def _set_larm_session(response: Response, prefix: str) -> None:
    expires = int(time.time()) + LARM_SESSION_SECONDS
    response.set_cookie(
        LARM_COOKIE,
        _session_value(expires, prefix),
        max_age=LARM_SESSION_SECONDS,
        # Below a gateway prefix the cookie stays with this app, not the whole host.
        path=prefix or "/",
        # SameSite=None reaches the sandboxed document viewers (opaque origin); see
        # _session_cookie. State-changing requests must still carry a same-site Origin.
        secure=True,
        httponly=True,
        samesite="none",
    )


_tickets: dict[bytes, tuple[float, str]] = {}
_tickets_lock = threading.Lock()


def issue_ticket(seconds: int = TICKET_SECONDS, prefix: str | None = None) -> str:
    """A one-time ticket a browser exchanges for a session at EXCHANGE_PATH.

    It remembers the prefix it was issued below: the browser's own requests carry
    no Bearer, so the forwarded prefix would otherwise be lost after the exchange.
    """
    ticket = secrets.token_urlsafe(32)
    now = time.monotonic()
    with _tickets_lock:
        for key in [key for key, (expires, _) in _tickets.items() if expires <= now]:
            del _tickets[key]
        if len(_tickets) >= MAX_TICKETS:
            del _tickets[min(_tickets, key=lambda key: _tickets[key][0])]
        _tickets[_digest(ticket)] = (now + seconds, config.root() if prefix is None else prefix)
    return ticket


def _consume_ticket(ticket: str) -> str | None:
    """The prefix of a valid unspent ticket, or None."""
    with _tickets_lock:
        entry = _tickets.pop(_digest(ticket), None)
    if entry is None or time.monotonic() >= entry[0]:
        return None
    return entry[1]


def exchange_url(ticket: str) -> str:
    return config.url(EXCHANGE_PATH) + "?" + "ticket=" + quote(ticket, safe="")


@router.post("/auth/ui-tickets")
def create_ui_ticket(request: Request) -> dict[str, object]:
    # Only a backend holding the credential may mint a browser login.
    if config.AUTH_MODE != "larm" or not getattr(request.state, "service_bearer", False):
        raise HTTPException(404)
    return {"url": exchange_url(issue_ticket()), "expires_in": TICKET_SECONDS}


@router.get(EXCHANGE_PATH)
def exchange_ticket(ticket: str = Query(default="", max_length=128), next: str = "/") -> Response:
    if config.AUTH_MODE != "larm":
        raise HTTPException(404)
    prefix = _consume_ticket(ticket)
    if prefix is None:
        return JSONResponse(
            {"detail": "ログイン用のURLが無効か期限切れです。もう一度開き直してください。"},
            status_code=401,
            headers={"Cache-Control": "no-store"},
        )
    # Only a path inside this app: never another host or a protocol-relative URL.
    target = next if next.startswith("/") and not next.startswith(("//", "/\\")) else "/"
    response = RedirectResponse(prefix + target, status_code=303)
    response.headers["Cache-Control"] = "no-store"
    _set_larm_session(response, prefix)
    return response


def _larm_required() -> Response:
    return JSONResponse(
        {"detail": "LARM_API_TOKENによる認証が必要です。"},
        status_code=401,
        headers={"Cache-Control": "no-store", "WWW-Authenticate": "Bearer"},
    )


async def _with_root(
    prefix: str, request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    reset = config.REQUEST_ROOT.set(prefix) if prefix else None
    try:
        return await call_next(request)
    finally:
        if reset is not None:
            config.REQUEST_ROOT.reset(reset)


async def _authenticate_larm(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    if larm_bearer(request):
        request.state.service_bearer = True
        prefix = request.headers.get("x-forwarded-prefix", "").strip().rstrip("/")
        if prefix and not valid_prefix(prefix):
            return JSONResponse({"detail": "X-Forwarded-Prefixの形式が不正です。"}, 400)
        return await _with_root(prefix, request, call_next)
    if request.url.path.startswith(PUBLIC_PREFIXES) or request.url.path == EXCHANGE_PATH:
        return await call_next(request)
    if _bearer(request):
        return _larm_required()
    session = session_expiry(request.cookies.get(LARM_COOKIE) or "")
    if session is None:
        return _larm_required()
    expires, prefix = session
    if request.method in STATE_CHANGING and not request.headers.get("origin"):
        # A cookie is sent by the browser on its own; a same-site Origin is the proof.
        return JSONResponse({"detail": "Originのない更新要求は受け付けません。"}, 403)
    response = await _with_root(prefix, request, call_next)
    if expires - time.time() < LARM_SESSION_SECONDS - LARM_RENEW_SECONDS:
        _set_larm_session(response, prefix)
    return response


async def authenticate(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    if config.AUTH_MODE == "larm":
        return await _authenticate_larm(request, call_next)
    if config.AUTH_MODE != "jwt" or request.url.path.startswith(PUBLIC_PREFIXES):
        return await call_next(request)
    own = request.cookies.get(config.AUTH_SESSION_COOKIE) if config.AUTH_SESSION_COOKIE else ""
    candidates = [
        (name, token)
        for name, token in (
            ("shared", request.cookies.get(config.AUTH_COOKIE) or ""),
            ("bearer", _bearer(request)),
            ("own", own or ""),
        )
        if token
    ]
    reasons: list[str] = []
    for source, token in candidates:
        try:
            claims = verify_token(
                token,
                config.AUTH_JWT_SECRET,
                leeway=config.AUTH_LEEWAY,
                token_type=config.AUTH_TOKEN_TYPE,
                issuer=config.AUTH_ISSUER,
                audience=config.AUTH_AUDIENCE,
            )
        except TokenError as error:
            # Only the shared token decides between "log in" and "rejected": a stale
            # copy of our own cookie must not keep a user away from the login page.
            if source != "own":
                reasons.append(str(error))
            continue
        request.state.user = str(claims.get(config.AUTH_USER_CLAIM) or claims.get("sub") or "")
        response = await call_next(request)
        if source != "own" or token != own:
            _session_cookie(response, token, claims)
        return response
    if reasons and any(reason != "expired" for reason in reasons):
        return _rejected()
    return _login_required(request)
