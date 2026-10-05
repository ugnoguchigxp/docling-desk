"""
title: Docling Desk Document Viewer
author: docling-desk
version: 1.0.0
description: Authorized document search, evidence and sandboxed viewer connection.
requirements: httpx
"""

import html
import json
from urllib.parse import urlsplit

import httpx
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field


class ViewerServiceError(ValueError):
    def __init__(self, status: int, code: str):
        super().__init__("Docling Desk: " + code)
        self.status, self.code = status, code


class Tools:
    class Valves(BaseModel):
        GATEWAY_URL: str = Field(
            default="http://host.docker.internal:18768",
            description="Server-reachable viewer gateway; never a model argument",
        )
        CONNECTOR_TOKEN: str = Field(
            default="", description="Private gateway connector token (administrator only)"
        )

    def __init__(self):
        self.valves = self.Valves()

    async def _call(self, name, arguments, user):
        if not user or not user.get("id") or len(self.valves.CONNECTOR_TOKEN) < 32:
            raise ValueError("Docling Desk viewer user/configuration missing")
        try:
            async with httpx.AsyncClient(timeout=40, follow_redirects=False) as client:
                response = await client.post(
                    self.valves.GATEWAY_URL.rstrip("/") + "/host/tools/" + name,
                    json={"user_id": user["id"], "arguments": arguments},
                    headers={"authorization": "Bearer " + self.valves.CONNECTOR_TOKEN},
                )
        except httpx.HTTPError:
            raise ValueError("Docling Desk: viewer service unavailable") from None
        try:
            result = response.json()
        except ValueError:
            raise ValueError("Docling Desk: invalid viewer service response") from None
        if not isinstance(result, dict):
            raise ValueError("Docling Desk: invalid viewer service response")
        if response.status_code != 200:
            raise ViewerServiceError(response.status_code, str(result.get("detail", "unavailable")))
        return result

    @staticmethod
    def _reference(item):
        return {key: item[key] for key in ("source_id", "source_revision", "evidence_revision")}

    async def _sources(self, result, user, emitter, items_key):
        if emitter is None:
            return
        views = {}
        for item in result.get(items_key, []):
            ref = self._reference(item)
            # Resolve locations only when the API locator contains a real page.
            # Sheet/slide type is validated by the viewer manifest, not guessed.
            arguments = {**ref, "locator": item.get("locator", {})}
            key = json.dumps(arguments, sort_keys=True)
            if key not in views:
                try:
                    views[key] = await self._call("request_document_view", arguments, user)
                except ValueError as error:
                    if isinstance(error, ViewerServiceError) and (
                        error.status in {401, 403}
                        or error.code in {"source_not_found", "source_changed", "evidence_changed"}
                    ):
                        raise
                    views[key] = {}
            view = views[key]
            await emitter(
                {
                    "type": "source",
                    "data": {
                        "source": {
                            "name": item.get("title", "資料"),
                            "id": item["source_id"],
                            **({"embed_url": view["embed_url"]} if view else {}),
                        },
                        "document": [item.get("excerpt", item.get("text", ""))],
                        "metadata": [
                            {
                                **ref,
                                "context_id": item["context_id"],
                                "locator": item.get("locator", {}),
                            }
                        ],
                    },
                }
            )

    async def list_document_scopes(self, __user__=None) -> str:
        """List the current user's search scopes. Use before searching if scope is unknown."""
        return json.dumps(
            await self._call("list_document_scopes", {}, __user__), ensure_ascii=False
        )

    async def search_documents(
        self,
        query: str,
        scope: dict,
        mode: str = "text",
        limit: int = 10,
        __user__=None,
        __event_emitter__=None,
    ) -> str:
        """Search documents in the current user's granted scope. Preserve returned references."""
        result = await self._call(
            "search_documents",
            {"query": query, "scope": scope, "mode": mode, "limit": limit},
            __user__,
        )
        await self._sources(result, __user__, __event_emitter__, "results")
        return json.dumps(result, ensure_ascii=False)

    async def get_document_context(
        self,
        retrieval_id: str,
        references: list[dict],
        max_chars: int = 80000,
        __user__=None,
        __event_emitter__=None,
    ) -> str:
        """Retrieve search evidence and tables; retrieval_id belongs to the current user."""
        result = await self._call(
            "get_document_context",
            {"retrieval_id": retrieval_id, "references": references, "max_chars": max_chars},
            __user__,
        )
        await self._sources(result, __user__, __event_emitter__, "contexts")
        return json.dumps(result, ensure_ascii=False)

    async def request_document_view(
        self,
        source_id: str,
        source_revision: str,
        evidence_revision: str,
        location: dict = None,
        __user__=None,
    ) -> tuple:
        """Open a versioned document. location kind is page/slide/sheet/document, number starts at 1."""
        ref = {
            "source_id": source_id,
            "source_revision": source_revision,
            "evidence_revision": evidence_revision,
            "location": location,
        }
        result = await self._call("request_document_view", ref, __user__)
        url = result["embed_url"]
        if urlsplit(url).scheme not in {"https", "http"}:
            raise ValueError("Invalid viewer URL")
        # Stable URL only. Nested viewer messages are accepted only from the
        # one frame we created; document messages cannot inject host prompts.
        content = (
            '''<!doctype html><html><body style="margin:0"><iframe id="viewer" title="Docling Desk文書ビューアー" sandbox="allow-scripts allow-downloads" style="width:100%;height:760px;border:0" src="'''
            + html.escape(url, quote=True)
            + """"></iframe><script>
        const frame=document.getElementById('viewer');
        window.addEventListener('message',e=>{if(e.source!==frame.contentWindow)return;const d=e.data;
        if(d?.type==='iframe:height' && Number.isFinite(d.height)) parent.postMessage({type:'iframe:height',height:Math.min(1100,Math.max(600,d.height))},'*');
        if(d?.type==='input:prompt' && typeof d.text==='string' && d.text.length<=14000) parent.postMessage({type:'input:prompt',text:d.text},'*');});
        parent.postMessage({type:'iframe:height',height:760},'*');
        </script></body></html>"""
        )
        return HTMLResponse(content, headers={"Content-Disposition": "inline"}), result

    async def connect_document_view(self, code: str, __user__=None) -> str:
        """Approve only the 8-character connection code the user provided from their viewer."""
        return json.dumps(
            await self._call("connect_document_view", {"code": code}, __user__), ensure_ascii=False
        )
