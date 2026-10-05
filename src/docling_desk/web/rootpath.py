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

TEXT_TYPES = {"text/html", "text/css", "text/javascript", "application/javascript"}
# Quoted or url() references to this app's own roots.
APP_PATH = re.compile(r"""(?P<lead>["'`(])/(?P<root>api|files|view|static)/""")
# The built React bundle is only checked for its own base, never rewritten wholesale.
BUNDLE_PATH = re.compile(r"""(?P<lead>["'`(])/static/frontend/""")
BUNDLE_ASSETS = "/static/frontend/assets/"


def prefix_text(text: str, root: str, *, bundle: bool = False) -> str:
    if bundle:
        return BUNDLE_PATH.sub(lambda m: f"{m['lead']}{root}/static/frontend/", text)
    return APP_PATH.sub(lambda m: f"{m['lead']}{root}/{m['root']}/", text)


def base_meta(root: str) -> str:
    return f'<meta name="docling-base" content="{root}">'


async def prefix_urls(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    response = await call_next(request)
    root = config.ROOT_PATH
    kind = response.headers.get("content-type", "").split(";")[0].strip().lower()
    if not root or kind not in TEXT_TYPES or "/vendor/" in request.url.path:
        return response
    body = b"".join([chunk async for chunk in response.body_iterator])  # type: ignore[attr-defined]
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        text = None
    headers = {k: v for k, v in response.headers.items() if k.lower() != "content-length"}
    if text is not None:
        text = prefix_text(text, root, bundle=request.url.path.startswith(BUNDLE_ASSETS))
        body = text.encode("utf-8")
    return Response(body, status_code=response.status_code, headers=headers)
