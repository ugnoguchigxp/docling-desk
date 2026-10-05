"""Backup and restore the local Wiki database and document folders."""

import sys

from docling_desk.operations.backup import main as backup_main  # noqa: E402


def _argv(argv: list[str]) -> list[str]:
    if len(argv) == 3 and argv[0] in {"backup", "restore"} and not argv[1].startswith("-"):
        action, source, destination = argv
        if action == "backup":
            return ["backup", "--data", source, "--output", destination]
        return ["restore", "--archive", source, "--destination", destination]
    return argv


if __name__ == "__main__":
    sys.exit(backup_main(_argv(sys.argv[1:])))
