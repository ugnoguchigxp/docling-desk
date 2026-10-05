"""Launch the installed local application from any working directory."""

import argparse


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Docling Desk local document workspace")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    import uvicorn

    uvicorn.run("docling_desk.app:app", host=args.host, port=args.port, workers=1, access_log=False)
    return 0
