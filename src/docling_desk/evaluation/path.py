"""One user-path measurement over loopback HTTP."""

from __future__ import annotations

import time

import httpx


def _failure(outcome: str, started: float, **values) -> dict:
    record = {
        "outcome": outcome,
        "search_seconds": None,
        "retrieval_seconds": None,
        "context_seconds": None,
        "source_seconds": None,
        "total_seconds": time.perf_counter() - started,
        "result_count": 0,
        "evidence_ok": False,
        "search_http_status": None,
        "retrieval_http_status": None,
        "context_http_status": None,
        "source_http_status": None,
    }
    record.update(values)
    return record


def confirm_retrieval(client: httpx.Client, retrieval_id: str, timeout: float = 5) -> dict:
    """Poll until the retrieval leaves the queue. A transport or HTTP error is not a ready result."""
    started = time.perf_counter()
    request_seconds = 0.0
    status = None
    while True:
        try:
            request_started = time.perf_counter()
            response = client.get(f"/api/knowledge/retrievals/{retrieval_id}")
            request_seconds = time.perf_counter() - request_started
        except httpx.HTTPError as exc:
            return {
                "ready": False,
                "outcome": "failure",
                "error": str(exc),
                "status_code": status,
                "elapsed": time.perf_counter() - started,
                "request_seconds": request_seconds,
            }
        status = response.status_code
        elapsed = time.perf_counter() - started
        if status >= 400:
            return {
                "ready": False,
                "outcome": "failure",
                "error": response.text[:500],
                "status_code": status,
                "elapsed": elapsed,
                "request_seconds": request_seconds,
            }
        try:
            body = response.json()
        except ValueError as exc:
            return {
                "ready": False,
                "outcome": "failure",
                "error": str(exc),
                "status_code": status,
                "elapsed": elapsed,
                "request_seconds": request_seconds,
            }
        if body.get("state") not in {"queued", "running"}:
            return {
                "ready": True,
                "outcome": "ready",
                "body": body,
                "status_code": status,
                "elapsed": elapsed,
                "request_seconds": request_seconds,
            }
        if elapsed > timeout:
            return {
                "ready": False,
                "outcome": "timeout",
                "body": body,
                "status_code": status,
                "elapsed": elapsed,
                "request_seconds": request_seconds,
            }
        time.sleep(0.02)


def execute_user_path(
    client: httpx.Client,
    *,
    query: str,
    mode: str,
    kind: str,
    namespace: str | None,
    limit: int,
    budget: int,
    expect: str,
    expected_source_id: str | None,
    expected_passage: str | None,
    source_endpoint: str,
    timeout: float = 5,
) -> dict:
    started = time.perf_counter()
    payload = {"query": query, "mode": mode, "kind": kind, "limit": limit}
    if namespace:
        payload["namespace"] = namespace
    search_started = time.perf_counter()
    try:
        response = client.post("/api/knowledge/search", json=payload)
    except httpx.HTTPError as exc:
        return _failure("failure", started, error=str(exc))
    search_seconds = time.perf_counter() - search_started
    search_status = response.status_code
    if search_status >= 400:
        return _failure(
            "failure",
            started,
            search_seconds=search_seconds,
            search_http_status=search_status,
        )
    try:
        posted = response.json()
    except ValueError as exc:
        return _failure(
            "failure",
            started,
            search_seconds=search_seconds,
            search_http_status=search_status,
            error=str(exc),
        )
    retrieval_id = posted.get("id")
    if not retrieval_id:
        return _failure(
            "failure",
            started,
            search_seconds=search_seconds,
            search_http_status=search_status,
            error="検索応答に id がありません。",
        )
    confirmed = confirm_retrieval(client, retrieval_id, timeout)
    common = {
        "search_seconds": search_seconds,
        "retrieval_seconds": confirmed["elapsed"],
        "retrieval_request_seconds": confirmed["request_seconds"],
        "search_http_status": search_status,
        "retrieval_http_status": confirmed.get("status_code"),
        "retrieval_id": retrieval_id,
    }
    if not confirmed["ready"]:
        outcome = "timeout" if confirmed["outcome"] == "timeout" else "failure"
        return _failure(outcome, started, error=confirmed.get("error"), **common)
    body = confirmed["body"]
    if body.get("state") == "cancelled":
        return _failure("cancelled", started, **common)
    if body.get("state") != "completed":
        return _failure("failure", started, **common, error=body.get("error"))
    results = (body.get("output") or {}).get("results") or []
    if not isinstance(results, list):
        return _failure("failure", started, error="検索結果の形式が不正です。", **common)
    if expect == "empty":
        outcome = "empty" if not results else "wrong_evidence"
        return _failure(outcome, started, result_count=len(results), **common)
    if not results:
        return _failure("empty", started, **common)
    top = results[:limit]
    chunk_ids = [item.get("chunk_id") for item in top]
    if any(not chunk_id for chunk_id in chunk_ids):
        return _failure(
            "failure",
            started,
            result_count=len(results),
            error="検索結果に chunk_id がありません。",
            **common,
        )
    evidence_hit = bool(expected_passage and expected_source_id) and any(
        item.get("source_id") == expected_source_id and expected_passage in item.get("text", "")
        for item in top
    )
    context_started = time.perf_counter()
    try:
        context_response = client.post(
            "/api/knowledge/context",
            json={
                "retrieval_id": retrieval_id,
                "chunk_ids": chunk_ids,
                "budget": budget,
            },
        )
    except httpx.HTTPError as exc:
        return _failure("failure", started, result_count=len(results), error=str(exc), **common)
    context_seconds = time.perf_counter() - context_started
    if context_response.status_code >= 400:
        return _failure(
            "failure",
            started,
            result_count=len(results),
            context_seconds=context_seconds,
            context_http_status=context_response.status_code,
            **common,
        )
    try:
        context_body = context_response.json()
    except ValueError as exc:
        return _failure(
            "failure",
            started,
            result_count=len(results),
            context_seconds=context_seconds,
            context_http_status=context_response.status_code,
            error=str(exc),
            **common,
        )
    passage_in_context = bool(expected_passage and expected_passage in context_body.get("text", ""))
    source_seconds = None
    source_status = None
    passage_in_source = False
    if source_endpoint == "wiki":
        target = expected_source_id if evidence_hit else top[0].get("source_id")
        if not target:
            return _failure(
                "failure",
                started,
                result_count=len(results),
                context_seconds=context_seconds,
                error="出典 id がありません。",
                **common,
            )
        source_started = time.perf_counter()
        try:
            source_response = client.get(f"/api/wiki/sources/{target}")
        except httpx.HTTPError as exc:
            return _failure(
                "failure",
                started,
                result_count=len(results),
                context_seconds=context_seconds,
                error=str(exc),
                **common,
            )
        source_seconds = time.perf_counter() - source_started
        source_status = source_response.status_code
        if source_response.status_code >= 400:
            return _failure(
                "failure",
                started,
                result_count=len(results),
                context_seconds=context_seconds,
                source_seconds=source_seconds,
                source_http_status=source_status,
                **common,
            )
        try:
            source_body = source_response.json().get("body", "")
        except ValueError as exc:
            return _failure(
                "failure",
                started,
                result_count=len(results),
                context_seconds=context_seconds,
                source_seconds=source_seconds,
                source_http_status=source_status,
                error=str(exc),
                **common,
            )
        passage_in_source = bool(expected_passage and expected_passage in source_body)
    elif source_endpoint == "context":
        passage_in_source = passage_in_context
    else:
        raise ValueError(source_endpoint)
    evidence_ok = evidence_hit and passage_in_context and passage_in_source
    return {
        "outcome": "success" if evidence_ok else "wrong_evidence",
        "search_seconds": search_seconds,
        "retrieval_seconds": confirmed["elapsed"],
        "context_seconds": context_seconds,
        "source_seconds": source_seconds,
        "total_seconds": time.perf_counter() - started,
        "result_count": len(results),
        "evidence_ok": evidence_ok,
        "search_http_status": search_status,
        "retrieval_http_status": confirmed.get("status_code"),
        "context_http_status": context_response.status_code,
        "source_http_status": source_status,
        "retrieval_id": retrieval_id,
        "source_observed_via": source_endpoint,
    }
