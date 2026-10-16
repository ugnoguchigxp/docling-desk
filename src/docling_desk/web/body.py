"""Bound request streams before JSON decoding or multipart spooling."""

from fastapi import HTTPException
from fastapi.responses import JSONResponse
from starlette.datastructures import Headers
from starlette.formparsers import MultiPartException


class BodyLimit:
    def __init__(self, app, limit: int, detail: str = "request_too_large"):
        self.app, self.limit, self.detail = app, limit, detail

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in {"POST", "PUT", "PATCH", "DELETE"}:
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        declared = headers.get("content-length", "")
        if declared.isdigit() and int(declared) > self.limit:
            await JSONResponse({"detail": self.detail}, status_code=413)(scope, receive, send)
            return
        size, exceeded = 0, False

        async def bounded_receive():
            nonlocal size, exceeded
            message = await receive()
            size += len(message.get("body", b""))
            if size > self.limit:
                exceeded = True
                if headers.get("content-type", "").startswith("multipart/form-data"):
                    # Starlette closes every partially spooled file for this error.
                    raise MultiPartException(self.detail)
                raise HTTPException(413, self.detail)
            return message

        async def bounded_send(message):
            if exceeded and message["type"] == "http.response.start":
                # Starlette maps multipart errors to 400 after cleaning up files.
                message = {**message, "status": 413}
            await send(message)

        await self.app(scope, bounded_receive, bounded_send)
