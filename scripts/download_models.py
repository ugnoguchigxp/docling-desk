"""Compatibility entry for the installed model downloader."""

from docling_desk.operations.download_models import main, publish

__all__ = ["publish"]

if __name__ == "__main__":
    main()
