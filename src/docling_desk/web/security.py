from __future__ import annotations

from collections.abc import Awaitable, Callable
from urllib.parse import urlparse

from fastapi import Request
from fastapi.responses import JSONResponse, Response
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.types import Receive, Scope, Send

from docling_desk import config

# The app's own pages may be framed only by the app itself (clickjacking).
SAME_ORIGIN_FRAMES = "frame-ancestors 'self'"


class HostCheck(TrustedHostMiddleware):
    """TrustedHostMiddleware, except for a caller that proved the LARM credential.

    The Host allow-list stops DNS rebinding, which works only through a browser;
    a browser cannot attach the Bearer, so a gateway may forward any Host it uses.
    """

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("state", {}).get("service_bearer"):
            await self.app(scope, receive, send)
            return
        await super().__call__(scope, receive, send)


async def local_only(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    # A Bearer is attached by the caller, never by a browser on its own: no CSRF.
    bearer = getattr(request.state, "service_bearer", False)
    if request.method in {"POST", "PATCH", "PUT", "DELETE"} and not bearer:
        origin = request.headers.get("origin")
        if origin:
            parsed = urlparse(origin)
            accepted = (
                origin in config.ALLOWED_ORIGINS
                if config.ALLOWED_ORIGINS
                else parsed.scheme == "http"
            )
            if not accepted or parsed.netloc != request.headers.get("host"):
                return JSONResponse(
                    {"detail": "他サイトからのアップロードは受け付けません。"}, status_code=403
                )
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    if request.url.path.startswith("/view/"):
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src data:; font-src 'self'; object-src 'none'; base-uri 'none'; "
            "form-action 'none'; sandbox allow-scripts allow-downloads"
        )
        if request.url.path.endswith("/workbook"):
            response.headers["Content-Security-Policy"] = (
                "default-src 'none'; script-src 'self'; style-src 'self'; frame-src 'self'; "
                "object-src 'none'; base-uri 'none'; sandbox allow-scripts"
            )
        if "/sheets/" in request.url.path:
            response.headers["Content-Security-Policy"] = (
                "default-src 'none'; script-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
                "font-src 'self' data:; object-src 'none'; base-uri 'none'; sandbox allow-scripts"
            )
        if "/slides/" in request.url.path:
            response.headers["Content-Security-Policy"] = (
                "default-src 'none'; script-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
                "font-src 'self' data:; object-src 'none'; base-uri 'none'; sandbox allow-scripts"
            )
        if "/pages/" in request.url.path:
            response.headers["Content-Security-Policy"] = (
                "default-src 'none'; script-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
                "font-src 'self' data:; object-src 'none'; base-uri 'none'; sandbox allow-scripts"
            )
        if request.url.path.endswith(("/word", "/document", "/source")):
            response.headers["Content-Security-Policy"] = (
                "default-src 'none'; script-src 'self'; img-src 'self' data:; "
                "style-src 'self' 'unsafe-inline'; font-src 'self' data:; "
                "object-src 'none'; base-uri 'none'; sandbox allow-scripts"
            )
        if request.url.path.endswith("/pdf"):
            response.headers["Content-Security-Policy"] = (
                "default-src 'none'; script-src 'self'; img-src 'self'; frame-src 'self'; "
                "style-src 'self' 'unsafe-inline'; object-src 'none'; base-uri 'none'; "
                "form-action 'none'; sandbox allow-scripts"
            )
        if request.url.path.endswith("/diagram"):
            response.headers["Content-Security-Policy"] = (
                "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                "img-src data:; font-src data:; object-src 'none'; base-uri 'none'; "
                "form-action 'none'; sandbox allow-scripts"
            )
    elif (
        request.url.path.startswith("/api/wiki/sources/") and request.url.path.endswith("/original")
    ) or request.url.path.startswith("/api/wiki/media/"):
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; script-src 'none'; style-src 'unsafe-inline'; "
            "object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
        )
    elif request.url.path == "/static/frontend/print.html":
        # A passive same-origin shell populated by the parent UI. Original
        # document viewers retain their opaque origin and existing sandbox.
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; script-src 'none'; img-src 'self' data:; "
            "style-src 'self' 'unsafe-inline'; font-src 'self' data:; "
            "object-src 'none'; base-uri 'none'; form-action 'none'; "
            "sandbox allow-same-origin allow-modals; " + SAME_ORIGIN_FRAMES
        )
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
    elif request.url.path.startswith("/files/"):
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; frame-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; font-src 'self' data:; sandbox"
        )
    elif request.url.path in {"/", "/static/frontend/index.html"}:
        # The Plate authoring UI accepts user-inserted media URLs. Imported Wiki
        # HTML and document viewers still use their existing inert renderers.
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data: blob: https:; media-src 'self' blob: https:; "
            "font-src 'self' data:; style-src 'self'; script-src 'self'; "
            "frame-src 'self' https:; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; "
            + SAME_ORIGIN_FRAMES
        )
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
    else:
        # Document viewers below /view/ and /files/ keep their sandbox instead: their
        # nested frames have an opaque-origin parent that 'self' would never match.
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; frame-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; "
            + SAME_ORIGIN_FRAMES
        )
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
    return response
