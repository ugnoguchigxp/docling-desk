"""Launch the installed local application from any working directory."""

import argparse
import json
import os
import sys

DEFAULT_PORT = 28765
LOOPBACK_BINDS = {"127.0.0.1", "localhost", "::1"}


def exposed_without_login(host: str) -> bool:
    """True when an unauthenticated server would listen beyond loopback.

    The container image listens on all interfaces by design: Compose publishes the
    port on 127.0.0.1 only, so DOCLING_CONTAINER marks that case.
    """
    from docling_desk import config

    return (
        config.AUTH_MODE == "none"
        and host not in LOOPBACK_BINDS
        and not config.AUTH_ALLOW_INSECURE
        and os.environ.get("DOCLING_CONTAINER") != "1"
    )


def announce_auth(host: str, port: int) -> None:
    """Say which login applies; on a terminal, print a one-time URL for LARM mode."""
    from docling_desk import config

    if config.AUTH_MODE == "larm":
        print("認証: LARM_API_TOKEN（Bearer）", file=sys.stderr)
        if sys.stdout.isatty():
            from docling_desk.web import auth

            shown = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
            ticket = auth.issue_ticket(auth.STARTUP_TICKET_SECONDS)
            print(
                "ブラウザーで開く（10分以内・1回限り）: "
                f"http://{shown}:{port}{auth.exchange_url(ticket)}"
            )
    elif config.LARM_TOKEN and config.AUTH_MODE_EXPLICIT:
        print(
            f"警告: LARM_API_TOKENがありますが DOCLING_AUTH_MODE={config.AUTH_MODE} のため使用しません。",
            file=sys.stderr,
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Docling Desk local document workspace")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--diagnose",
        action="store_true",
        help="サーバーを起動せず、機能ごとの利用可否と復旧方法を表示する",
    )
    parser.add_argument("--json", action="store_true", help="--diagnose をJSONで表示する")
    args = parser.parse_args(argv)
    if args.diagnose:
        from docling_desk.api.diagnostics import build_report, format_text

        report = build_report(runtime=False)
        print(
            json.dumps(report, ensure_ascii=False, indent=2) if args.json else format_text(report)
        )
        # Optional features may be unset; only a missing core feature fails the command.
        core = [f for f in report["features"] if not f["optional"]]
        return 0 if all(f["status"] == "available" for f in core) else 1
    import uvicorn

    if exposed_without_login(args.host):
        print(
            f"認証なしでは --host {args.host} で待ち受けできません。LARM_API_TOKEN等で認証を設定するか、"
            "DOCLING_AUTH_ALLOW_INSECURE=1 で明示的に許可してください。",
            file=sys.stderr,
        )
        return 2
    announce_auth(args.host, args.port)
    uvicorn.run("docling_desk.app:app", host=args.host, port=args.port, workers=1, access_log=False)
    return 0
