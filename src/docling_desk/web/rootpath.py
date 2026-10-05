"""Serve the app below a reverse-proxy prefix (``DOCLING_ROOT_PATH``).

The proxy strips the prefix, so routes stay unchanged. Documents, viewer pages
and scripts name app paths such as ``/static/...``; they are prefixed while
being sent, which leaves the stored files and their hashes untouched.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable

from fastapi import Request
from fastapi.responses import Response

from docling_desk import config

ROOTS = "api|files|view|static"
# HTML is rewritten only where a URL is expected, never in text a document contains.
HTML_URL = re.compile(
    rf"""(?P<lead>\b(?:src|href|action|poster|data-url)=["']|url\(["']?)/(?P<root>{ROOTS})/"""
)
# Scripts name the same roots in string literals; stylesheets in url().
SCRIPT_URL = re.compile(rf"""(?P<lead>["'`(])/(?P<root>{ROOTS})/""")
# The built React bundle is only checked for its own base, never rewritten wholesale.
BUNDLE_URL = re.compile(r"""(?P<lead>["'`(])/static/frontend/""")
BUNDLE_ASSETS = "/static/frontend/assets/"
SCRIPT_TYPES = {"text/javascript", "application/javascript"}
TEXT_TYPES = {"text/html", "text/css"} | SCRIPT_TYPES
DROPPED = {b"content-length", b"etag", b"last-modified"}


def prefix_text(text: str, root: str, *, kind: str = "text/html", bundle: bool = False) -> str:
    if bundle:
        return BUNDLE_URL.sub(lambda m: f"{m['lead']}{root}/static/frontend/", text)
    pattern = HTML_URL if kind == "text/html" else SCRIPT_URL
    return pattern.sub(lambda m: f"{m['lead']}{root}/{m['root']}/", text)


def base_meta(root: str) -> str:
    return f'<meta name="docling-base" content="{root}">'


async def prefix_urls(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    response = await call_next(request)
    root = config.ROOT_PATH
    kind = response.headers.get("content-type", "").split(";")[0].strip().lower()
    if (
        not root
        or kind not in TEXT_TYPES
        # Partial, cached or encoded bodies must reach the client byte for byte.
        or response.status_code != 200
        or "content-encoding" in response.headers
        or "/vendor/" in request.url.path
    ):
        return response
    body = b"".join([chunk async for chunk in response.body_iterator])  # type: ignore[attr-defined]
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        return Response(body, status_code=200, headers=dict(response.headers))
    bundle = request.url.path.startswith(BUNDLE_ASSETS)
    body = prefix_text(text, root, kind=kind, bundle=bundle).encode("utf-8")
    rewritten = Response(body, status_code=response.status_code)
    # Keep every header (including repeated ones such as Set-Cookie) except those
    # that described the original bytes.
    rewritten.raw_headers = [
        (name, value) for name, value in response.raw_headers if name.lower() not in DROPPED
    ] + [(b"content-length", str(len(body)).encode())]
    return rewritten
