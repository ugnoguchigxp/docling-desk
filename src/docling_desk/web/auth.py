"""Optional shared-login check for deployments behind a reverse proxy.

The default (``DOCLING_AUTH_MODE=none``) keeps the loopback-only behaviour.
``jwt`` accepts an HS256 token issued by the host application, read from a
cookie or an ``Authorization: Bearer`` header, so users signed in there can
open this app without a second login.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import time
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import quote

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

from docling_desk import config

PUBLIC_PREFIXES = ("/health/", "/static/")
MIN_SECRET_BYTES = 32


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
    except (UnicodeDecodeError, ValueError) as error:
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
    if not isinstance(expires, (int, float)) or isinstance(expires, bool):
        raise TokenError("no expiry")
    if moment > expires + leeway:
        raise TokenError("expired")
    not_before = claims.get("nbf")
    if isinstance(not_before, (int, float)) and moment < not_before - leeway:
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


def _denied(request: Request) -> Response:
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


async def authenticate(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    if config.AUTH_MODE != "jwt" or request.url.path.startswith(PUBLIC_PREFIXES):
        return await call_next(request)
    token = request.cookies.get(config.AUTH_COOKIE) or _bearer(request)
    if not token:
        return _denied(request)
    try:
        claims = verify_token(
            token,
            config.AUTH_JWT_SECRET,
            leeway=config.AUTH_LEEWAY,
            token_type=config.AUTH_TOKEN_TYPE,
            issuer=config.AUTH_ISSUER,
            audience=config.AUTH_AUDIENCE,
        )
    except TokenError:
        return _denied(request)
    request.state.user = str(claims.get(config.AUTH_USER_CLAIM) or claims.get("sub") or "")
    return await call_next(request)
