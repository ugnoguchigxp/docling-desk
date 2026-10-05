"""Python search/read tools inspired by llm-fetch, with a deterministic contingent search plan."""

from __future__ import annotations

import html as html_module
import http.client
import ipaddress
import json
import os
import re
import socket
import ssl
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, urlunsplit

import certifi
from lxml import html
from lxml.etree import LxmlError  # ty: ignore[unresolved-import]  # Installed binary extension.

from docling_desk.explanation.provider import Budget, ExplanationError

DNS_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="explanation-dns")
CHALLENGE = re.compile(r"anomaly-modal|challenge-form|bots use DuckDuckGo|captcha", re.I)
DIRECTIVE = re.compile(
    r"ignore (?:all |the )?(?:previous|prior) instructions|system prompt|"
    r"send (?:your|the) (?:api key|credentials)|以前の指示を無視|システムプロンプトを",
    re.I,
)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def public_url(raw: str) -> str:
    try:
        if len(raw) > 2048 or any(ord(c) < 32 for c in raw):
            raise ValueError()
        p = urlsplit(raw)
        if p.scheme not in {"http", "https"} or not p.hostname or p.username or p.password:
            raise ValueError()
        host = p.hostname.lower().rstrip(".")
        if (
            host == "localhost"
            or host.endswith((".localhost", ".local", ".internal", ".home.arpa"))
            or host == "home.arpa"
        ):
            raise ValueError()
        if p.port not in {None, 443 if p.scheme == "https" else 80}:
            raise ValueError()
        try:
            address = ipaddress.ip_address(host)
            if not public_ip(str(address)):
                raise ValueError()
        except ValueError:
            # A numeric-looking host must not bypass IP validation.
            if re.fullmatch(r"[\d.:]+", host) or ":" in host:
                raise
        return urlunsplit((p.scheme, p.netloc, p.path or "/", p.query, ""))
    except (ValueError, TypeError) as exc:
        raise ExplanationError(
            "unsafe_url", "公開HTTP/HTTPSの標準ポートだけを取得できます。"
        ) from exc


def public_ip(raw: str) -> bool:
    try:
        ip = ipaddress.ip_address(raw)
        if not ip.is_global or ip.is_multicast or ip.is_unspecified:
            return False
        if isinstance(ip, ipaddress.IPv6Address):
            return ip in ipaddress.ip_network("2000::/3") and not any(
                ip in ipaddress.ip_network(n)
                for n in (
                    "2001::/32",
                    "2001:2::/48",
                    "2001:10::/28",
                    "2001:20::/28",
                    "2001:db8::/32",
                    "2002::/16",
                    "3fff::/20",
                )
            )
        return True
    except ValueError:
        return False


def generic_query(raw: str) -> str:
    query = raw.strip()
    if (
        not query
        or len(query) > 150
        or re.search(r"https?://|\S+@\S+|\d{4,}|sk-[a-zA-Z0-9]+|[\r\n]", query)
    ):
        raise ExplanationError("sensitive_query", "一般的な用語だけで検索してください。")
    return query


class SafeHTTP:
    def __init__(self):
        self.cancelled = threading.Event()
        self.connection: http.client.HTTPConnection | None = None

    def cancel(self) -> None:
        self.cancelled.set()
        if self.connection:
            self.connection.close()

    def get(
        self, raw: str, timeout: float = 20, headers: dict | None = None
    ) -> tuple[str, bytes, str]:
        deadline = time.monotonic() + timeout
        url = public_url(raw)
        for _ in range(5):
            if self.cancelled.is_set():
                raise ExplanationError("interrupted", "Web取得を中断しました。")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ExplanationError("web_timeout", "Web取得がタイムアウトしました。")
            p = urlsplit(url)
            host = p.hostname or ""
            port = 443 if p.scheme == "https" else 80
            future = DNS_POOL.submit(socket.getaddrinfo, host, port, 0, socket.SOCK_STREAM)
            try:
                addresses = future.result(timeout=remaining)
            except (TimeoutError, OSError) as exc:
                future.cancel()
                raise ExplanationError(
                    "web_network", "Webの接続先を確認できませんでした。"
                ) from exc
            if (
                not addresses
                or len(addresses) > 64
                or any(not public_ip(str(a[4][0])) for a in addresses)
            ):
                raise ExplanationError("unsafe_url", "内部ネットワークの取得を拒否しました。")
            conn = http.client.HTTPConnection(
                host, port, timeout=max(0.1, deadline - time.monotonic())
            )
            self.connection = conn
            try:
                # Connect to the validated address, not a second DNS resolution.
                sock = socket.create_connection(
                    (str(addresses[0][4][0]), int(addresses[0][4][1])),
                    timeout=max(0.1, deadline - time.monotonic()),
                )
                conn.sock = sock
                if p.scheme == "https":
                    sock = ssl.create_default_context(cafile=certifi.where()).wrap_socket(
                        sock, server_hostname=host
                    )
                conn.sock = sock
                conn.request(
                    "GET",
                    urlunsplit(("", "", p.path or "/", p.query, "")),
                    headers={
                        "User-Agent": "Mozilla/5.0 (compatible; DoclingExplanation/1.0)",
                        "Accept": "text/html,text/plain,application/json,application/javascript",
                        "Accept-Encoding": "identity",
                        **(headers or {}),
                    },
                )
                response = conn.getresponse()
                if response.status in {301, 302, 303, 307, 308}:
                    location = response.getheader("Location")
                    if not location:
                        raise ExplanationError("web_response", "転送先がありません。")
                    next_url = public_url(urljoin(url, location))
                    if (urlsplit(next_url).scheme, urlsplit(next_url).netloc) != (
                        p.scheme,
                        p.netloc,
                    ):
                        headers = None  # Provider credentials never follow a cross-origin redirect.
                    url = next_url
                    continue
                if response.status != 200:
                    raise ExplanationError(
                        "web_rate" if response.status in {403, 429} else "web_response",
                        f"Web取得に失敗しました（HTTP {response.status}）。",
                    )
                if response.getheader("Content-Encoding", "identity") not in {"identity", ""}:
                    raise ExplanationError(
                        "web_encoding", "圧縮されたWeb本文を採用できませんでした。"
                    )
                chunks, size = [], 0
                while True:
                    if self.cancelled.is_set() or time.monotonic() >= deadline:
                        raise ExplanationError("web_timeout", "Web取得がタイムアウトしました。")
                    sock.settimeout(max(0.1, deadline - time.monotonic()))
                    chunk = response.read(16384)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > 1024**2:
                        raise ExplanationError("web_size", "Web本文が取得上限を超えています。")
                    chunks.append(chunk)
                    if response.isclosed():
                        break
                return url, b"".join(chunks), response.getheader("Content-Type", "")
            except (OSError, http.client.HTTPException) as exc:
                raise ExplanationError("web_network", "Webページとの通信に失敗しました。") from exc
            finally:
                conn.close()
                self.connection = None
        raise ExplanationError("web_redirect", "Webページの転送が多すぎます。")


def text_only(markup: str) -> str:
    return " ".join(html.fromstring("<div>" + markup + "</div>").text_content().split())


def normalize_hit_url(value: str) -> str:
    url = urljoin("https://duckduckgo.com", value)
    p = urlsplit(url)
    if p.hostname and p.hostname.endswith("duckduckgo.com"):
        values = parse_qs(p.query)
        if values.get("uddg"):
            url = values["uddg"][0]
    return public_url(url)


def parse_search(body: str) -> list[dict]:
    if CHALLENGE.search(body):
        raise ExplanationError("web_challenge", "検索サービスがアクセス確認を要求しています。")
    tree = html.fromstring(body)
    hits = []
    for link in tree.xpath('//a[contains(@class,"result__a") or contains(@class,"result-link")]')[
        :100
    ]:
        try:
            url = normalize_hit_url(link.get("href", ""))
        except ExplanationError:
            continue
        if any(h["url"] == url for h in hits):
            continue
        parent = link.getparent()
        container = parent.getparent() if parent is not None else None
        snippet = (
            text_only(html.tostring(container, encoding="unicode")) if container is not None else ""
        )
        hits.append(
            {
                "url": url,
                "title": link.text_content().strip()[:200],
                "snippet": snippet[:500],
                "trust": "untrusted",
                "rank": len(hits) + 1,
            }
        )
        if len(hits) == 5:
            break
    if not hits and not re.search(r"no results|検索結果がありません", tree.text_content(), re.I):
        raise ExplanationError("web_parse", "検索結果の形式を確認できませんでした。")
    return hits


def parse_preload(body: str) -> list[dict]:
    if CHALLENGE.search(body):
        raise ExplanationError("web_challenge", "検索サービスがアクセス確認を要求しています。")
    match = re.search(r'DDG\.pageLayout\.load\(\s*["\']d["\']\s*,\s*(\[)', body)
    if not match:
        raise ExplanationError("web_parse", "検索結果の形式を確認できませんでした。")
    try:
        rows, _ = json.JSONDecoder().raw_decode(body[match.start(1) :])
    except ValueError as exc:
        raise ExplanationError("web_parse", "検索結果を読み込めませんでした。") from exc
    if not isinstance(rows, list) or len(rows) > 1000:
        raise ExplanationError("web_parse", "検索結果の形式が不正です。")
    hits = []
    for row in rows:
        if not isinstance(row, dict) or "n" in row:
            continue
        if not all(isinstance(row.get(k), str) for k in ("u", "t", "a")):
            raise ExplanationError("web_parse", "検索結果の形式が変更されています。")
        try:
            url = public_url(row["u"])
        except ExplanationError:
            continue
        if any(h["url"] == url for h in hits):
            continue
        hits.append(
            {
                "url": url,
                "title": text_only(row["t"])[:200],
                "snippet": text_only(row["a"])[:500],
                "trust": "untrusted",
                "rank": len(hits) + 1,
            }
        )
        if len(hits) == 5:
            break
    return hits


def extract_page(body: bytes, content_type: str) -> dict:
    if "text/plain" in content_type:
        text, title = body.decode("utf-8", errors="replace"), ""
    elif "html" in content_type:
        charset = re.search(r"charset\s*=\s*[\"\']?([^;\s\"\'>]+)", content_type, re.I)
        if charset is None:
            charset = re.search(
                r"charset\s*=\s*[\"\']?([^;\s\"\'>]+)",
                body[:4096].decode("ascii", errors="ignore"),
                re.I,
            )
        encoding = charset.group(1) if charset else "utf-8"
        tree = html.fromstring(body, parser=html.HTMLParser(encoding=encoding))
        if len(list(tree.iter())) > 50000:
            raise ExplanationError("web_size", "Web本文の構造が取得上限を超えています。")
        title = " ".join(tree.xpath("//title/text()"))[:200]
        for node in tree.xpath(
            "//script|//style|//noscript|//iframe|//object|//svg|//nav|//footer|//form|//*[@hidden]|//*[@aria-hidden='true']"
        ):
            if node.getparent() is not None:
                node.drop_tree()
        for node in list(tree.iter()):
            if (
                re.search(
                    r"display\s*:\s*none|visibility\s*:\s*hidden", (node.get("style") or ""), re.I
                )
                and node.getparent() is not None
            ):
                node.drop_tree()
        scopes = tree.xpath("//article|//main|//*[@role='main']")
        scope = max(scopes, key=lambda n: len(n.text_content())) if scopes else tree
        text = "\n".join(line.strip() for line in scope.text_content().splitlines() if line.strip())
    else:
        raise ExplanationError("web_type", "本文を確認できるHTML・テキストだけを採用します。")
    if CHALLENGE.search(text[:2000]) or len(text.strip()) < 150:
        raise ExplanationError("web_quality", "参照先の十分な本文を確認できませんでした。")
    if DIRECTIVE.search(text):
        raise ExplanationError(
            "web_directive", "モデルへの操作指示を含むページを根拠から除外しました。"
        )
    return {"title": title, "text": text[:6000], "truncated": len(text) > 6000}


class WebSearchProvider:
    def __init__(self, provider: str = "duckduckgo", http: SafeHTTP | None = None):
        self.provider = provider
        self.http = http or SafeHTTP()
        self.cooldown_until = 0.0

    def preflight(self) -> None:
        if self.provider == "brave" and not os.environ.get("BRAVE_SEARCH_API_KEY"):
            raise ExplanationError("not_configured", "Brave Searchの認証が未設定です。")

    def cancel(self) -> None:
        self.http.cancel()

    def search(self, query: str, timeout: float) -> list[dict]:
        query = generic_query(query)
        if time.monotonic() < self.cooldown_until:
            raise ExplanationError("web_challenge", "検索サービスのアクセス制限中です。")
        if self.provider == "brave":
            self.preflight()
            _, body, _ = self.http.get(
                "https://api.search.brave.com/res/v1/web/search?"
                + urlencode({"q": query, "count": 5}),
                timeout,
                {"X-Subscription-Token": os.environ["BRAVE_SEARCH_API_KEY"]},
            )
            rows = json.loads(body).get("web", {}).get("results", [])
            return [
                {
                    "url": public_url(r["url"]),
                    "title": r.get("title", "")[:200],
                    "snippet": html_module.unescape(r.get("description", ""))[:500],
                    "trust": "untrusted",
                    "rank": i + 1,
                }
                for i, r in enumerate(rows[:5])
            ]
        deadline = time.monotonic() + timeout
        last = None
        # Same-provider routes from llm-fetch; never execute returned JavaScript.
        for route in ("web", "html", "lite"):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                endpoint = {
                    "web": "https://duckduckgo.com/",
                    "html": "https://html.duckduckgo.com/html/",
                    "lite": "https://lite.duckduckgo.com/lite/",
                }[route]
                _, raw, _ = self.http.get(endpoint + "?" + urlencode({"q": query}), remaining)
                body = raw.decode("utf-8", errors="replace")
                if route != "web":
                    return parse_search(body)
                if CHALLENGE.search(body):
                    raise ExplanationError(
                        "web_challenge", "検索サービスがアクセス確認を要求しています。"
                    )
                token = re.search(r'\bvqd\s*=\s*["\'](\d+-\d+(?:-\d+)?)["\']', body)
                scripts = html.fromstring(body).xpath("//script/@src")
                preload = next((urljoin(endpoint, s) for s in scripts if "/d.js" in s), "")
                p = urlsplit(preload)
                qs = parse_qs(p.query)
                if (
                    not token
                    or p.scheme != "https"
                    or p.hostname != "links.duckduckgo.com"
                    or p.path != "/d.js"
                    or qs.get("q") != [query]
                    or qs.get("vqd") != [token.group(1)]
                    or p.username
                    or p.password
                    or p.port
                ):
                    raise ExplanationError("web_parse", "検索のpreload参照を確認できませんでした。")
                _, raw, _ = self.http.get(preload, max(0.1, deadline - time.monotonic()))
                return parse_preload(raw.decode("utf-8", errors="replace"))
            except ExplanationError as exc:
                last = exc
                if exc.code in {"web_challenge", "web_rate"}:
                    self.cooldown_until = time.monotonic() + 60
                    raise
        raise last or ExplanationError("web_timeout", "Web検索の時間上限に達しました。")

    def read(self, url: str, timeout: float) -> dict:
        final, body, content_type = self.http.get(url, timeout)
        return {
            "url": final,
            **extract_page(body, content_type),
            "fetched_at": now(),
            "kind": "web",
            "trust": "untrusted",
        }


def research(
    plans: list[dict],
    budget: Budget,
    web: WebSearchProvider,
    *,
    initial=None,
    progress=None,
    reserve_seconds=0,
) -> dict:
    """Execute the first turn's contingent plan without further model decisions."""
    plans = plans[:4]
    state = {
        "status": "researching",
        "topics": [p["topic"] for p in plans],
        "queries": [],
        "evidence": [],
        "observations": [],
        "hits": {},
        "attempted": [],
        "covered_topics": [],
        "skipped": 0,
        "search_attempts": [],
        "completed_queries": [],
        "pending_read": None,
        "research_seconds": 0,
        **(initial or {}),
    }
    # Older checkpoints recorded requests but not their completion. Retry only
    # unconfirmed HTTP work, within the same total request/time allowances.
    if initial and "search_attempts" not in initial:
        state["search_attempts"] = list(state["queries"])
        state["completed_queries"] = list(
            {h.get("query") for h in state["hits"].values() if h.get("query")}
            | {o["query"] for o in state["observations"] if o.get("query")}
        )
    if web.provider == "disabled":
        return {**state, "status": "disabled_by_policy"}
    if not plans:
        return {**state, "status": "not_needed"}
    started = time.monotonic()
    spent = state["research_seconds"]
    deadline = started + min(max(0, 120 - spent), max(0, budget.remaining() - reserve_seconds))

    def report(stage):
        state["research_seconds"] = spent + time.monotonic() - started
        if progress:
            progress(state, stage)

    def remaining():
        # Exhausting web time must not discard the faithful source explanation.
        budget.remaining()
        return min(20, deadline - time.monotonic())

    def relevant(plan, value):
        terms = [t.casefold().strip() for t in plan["required_terms"] if t.strip()]
        text = (value.get("title", "") + "\n" + value.get("text", "")).casefold()
        return bool(terms) and all(t in text for t in terms)

    def rank(plan, hit):
        host = urlsplit(hit["url"]).hostname or ""
        preferred = any(host == d or host.endswith("." + d) for d in plan["preferred_domains"])
        text = (hit.get("title", "") + " " + hit.get("snippet", "")).casefold()
        matches = sum(t.casefold() in text for t in plan["required_terms"] if t)
        return (not preferred, -matches, hit["url"])

    # Breadth first: give each topic one search/read opportunity before fallbacks.
    for query_key in ("query", "alternate_query"):
        for plan in plans:
            # A confirmed page can answer several topics without another fetch.
            for item in state["evidence"]:
                if relevant(plan, item):
                    topics = item.setdefault("topics", [item.get("topic")])
                    if plan["topic"] not in topics:
                        topics.append(plan["topic"])
                    if plan["topic"] not in state["covered_topics"]:
                        state["covered_topics"].append(plan["topic"])
                        report("確認済みの本文を別の調査項目にも使用")
            if plan["topic"] in state["covered_topics"] or remaining() <= 0:
                continue
            raw = plan[query_key]
            try:
                query = generic_query(raw)
            except ExplanationError:
                state["skipped"] += 1
                state["observations"].append(
                    {"error_code": "sensitive_query", "topic": plan["topic"]}
                )
                report("送信できない検索語を省略")
                continue
            if query not in state["completed_queries"] and len(state["search_attempts"]) < 8:
                if query not in state["queries"]:
                    state["queries"].append(query)
                state["search_attempts"].append(query)
                report(f"補足情報を検索 {len(state['search_attempts'])}/8")
                try:
                    hits = web.search(query, max(0.1, remaining()))
                except (OSError, ValueError, LxmlError, ExplanationError) as exc:
                    if isinstance(exc, ExplanationError) and exc.code == "interrupted":
                        report("未完了の検索と経過時間を保存")
                        raise
                    if isinstance(exc, ExplanationError) and not (
                        exc.code.startswith("web_") or exc.code == "unsafe_url"
                    ):
                        raise
                    state["observations"].append(
                        {"query": query, "error_code": getattr(exc, "code", "web_response")}
                    )
                    hits = []
                state["completed_queries"].append(query)
                for hit in hits[:5]:
                    try:
                        url = public_url(hit["url"])
                    except ExplanationError:
                        continue
                    previous = state["hits"].get(url, {})
                    queries = list(previous.get("queries", [previous.get("query")]))
                    if query not in queries:
                        queries.append(query)
                    state["hits"][url] = {**hit, "url": url, "query": query, "queries": queries}
                report("検索結果を保存")
            hits = sorted(
                [h for h in state["hits"].values() if query in h.get("queries", [h.get("query")])],
                key=lambda h: rank(plan, h),
            )
            hit_urls = {h["url"] for h in hits}
            read_count = sum(url in hit_urls for url in state["attempted"])
            for hit in hits:
                url = hit["url"]
                if (
                    (url in state["attempted"] and url != state["pending_read"])
                    or len(state["attempted"]) >= 12
                    or read_count >= 2
                ):
                    continue
                if remaining() <= 0:
                    break
                state["attempted"].append(url)
                state["pending_read"] = url
                read_count += 1
                report(f"補足の本文を確認 {len(state['attempted'])}/12")
                try:
                    page = web.read(url, max(0.1, remaining()))
                except (OSError, ValueError, LxmlError, ExplanationError) as exc:
                    if isinstance(exc, ExplanationError) and exc.code == "interrupted":
                        report("未完了の本文取得と経過時間を保存")
                        raise
                    if isinstance(exc, ExplanationError) and not (
                        exc.code.startswith("web_") or exc.code == "unsafe_url"
                    ):
                        raise
                    state["observations"].append(
                        {"url": url, "error_code": getattr(exc, "code", "web_response")}
                    )
                    page = None
                state["pending_read"] = None
                if page is not None:
                    # Save readable text even when it answers a different planned
                    # topic; only relevant pages are passed to the final model.
                    matching = [p["topic"] for p in plans if relevant(p, page)]
                    if matching:
                        existing = next(
                            (e for e in state["evidence"] if e["text"] == page["text"]), None
                        )
                        if existing is None:
                            page.update(
                                id=f"web-{len(state['evidence']) + 1}",
                                topic=matching[0],
                                topics=matching,
                            )
                            state["evidence"].append(page)
                        else:
                            existing["topics"] = list(
                                dict.fromkeys(
                                    existing.get("topics", [existing.get("topic")]) + matching
                                )
                            )
                        state["covered_topics"] = list(
                            dict.fromkeys(state["covered_topics"] + matching)
                        )
                    else:
                        state["observations"].append(
                            {"url": url, "topic": plan["topic"], "error_code": "irrelevant"}
                        )
                report("確認した本文と未解決の項目を保存")
                if plan["topic"] in state["covered_topics"]:
                    break
    evidence = state["evidence"]
    if evidence:
        status = "success" if set(state["covered_topics"]) == set(state["topics"]) else "partial"
    elif not state["queries"] and state["skipped"]:
        status = "skipped_sensitive"
    elif any(o.get("error_code") for o in state["observations"]) or remaining() <= 0:
        status = "failed"
    else:
        status = "no_usable_evidence" if state["hits"] else "no_results"
    state["research_seconds"] = spent + time.monotonic() - started
    return {**state, "status": status}
