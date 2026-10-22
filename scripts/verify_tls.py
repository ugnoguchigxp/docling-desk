"""Probe public TLS endpoints without credentials, cookies, or application data.

Run with the interpreter being assessed. Network/DNS errors are failures, never
evidence that a compromised CA was rejected. The public incident hosts are
supplementary evidence, not a substitute for the vendor's patch information.
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import json
import socket
import ssl
import urllib.error
import urllib.request

import certifi
import httpx

if importlib.util.find_spec("requests"):
    import requests
else:
    requests = None

GOOD = ["pypi.org", "huggingface.co", "download.pytorch.org"]
BAD = [
    "dv-g3b-emudhra.we-have-a-problem.com",
    "dv-g3b-aaa-emudhra.we-have-a-problem.com",
]


def probe(client: str, host: str) -> None:
    url = f"https://{host}/"
    if client.startswith("ssl"):
        ctx = (
            ssl.create_default_context(cafile=certifi.where())
            if client == "ssl-certifi"
            else ssl.create_default_context()
        )
        with socket.create_connection((host, 443), timeout=10) as sock:
            with ctx.wrap_socket(sock, server_hostname=host):
                return
    elif client == "httpx":
        with httpx.Client(timeout=10, follow_redirects=False) as session:
            session.get(url)
    elif client == "requests":
        if requests is None:
            raise RuntimeError("requests is not installed")
        with requests.Session() as session:
            session.get(url, timeout=10, allow_redirects=False).close()
    elif client == "urllib":
        with urllib.request.urlopen(url, timeout=10):
            pass


def main() -> None:
    print(
        json.dumps(
            {
                "openssl": ssl.OPENSSL_VERSION,
                "packages": {
                    p: importlib.metadata.version(p)
                    for p in ("certifi", "httpx", "requests")
                    if importlib.util.find_spec(p)
                },
                "ssl_defaults": ssl.get_default_verify_paths()._asdict(),
                "certifi": certifi.where(),
            }
        )
    )
    failed = False
    for client in ("ssl", "ssl-certifi", "urllib", "httpx", "requests"):
        if client == "requests" and requests is None:
            print(json.dumps({"client": client, "outcome": "not_installed"}))
            continue
        for host in GOOD + BAD:
            try:
                probe(client, host)
                outcome = "accepted"
            except urllib.error.HTTPError:
                # An HTTP error is received only after the TLS handshake succeeded.
                outcome = "accepted"
            except Exception as exc:
                # These libraries wrap OpenSSL's verification error differently.
                outcome = (
                    "certificate_rejected"
                    if "CERTIFICATE_VERIFY_FAILED" in str(exc)
                    else "network_or_other_error"
                )
            ok = outcome == ("certificate_rejected" if host in BAD else "accepted")
            print(json.dumps({"client": client, "host": host, "outcome": outcome, "ok": ok}))
            failed |= not ok
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
