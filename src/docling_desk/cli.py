"""Launch the installed local application from any working directory."""

import argparse
import json


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Docling Desk local document workspace")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
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

    uvicorn.run("docling_desk.app:app", host=args.host, port=args.port, workers=1, access_log=False)
    return 0
