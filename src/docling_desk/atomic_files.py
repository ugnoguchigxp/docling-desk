"""Byte-level atomic file publication shared by the local stores.

Callers keep everything that differs between stores: JSON encoding, size limits,
publication order, parent/symlink checks and the error types they raise. This
module only makes one file appear as either the old complete version or the new
complete version.

Steps: unique temporary in the destination directory (same filesystem), exclusive
create (never follows a pre-planted link), write, flush, fsync, replace, fsync of
the parent directory, and removal of the temporary on any failure.

Platform notes: POSIX only (``os.fchmod`` and directory descriptors). A directory
fsync that the platform or filesystem cannot perform (the directory cannot be
opened, or fsync reports EINVAL/ENOTSUP) is skipped. A real I/O error of that
directory fsync happens after the replace, so the new file is already published:
it is logged as a warning and not raised, because a caller would otherwise treat a
published write as failed. Every failure before the replace raises and removes the
temporary.

Temporary names end with ``.tmp`` so existing cleanup and exclusion rules
(blob mirror, diagram repair, asset refusal) keep matching them.
"""

from __future__ import annotations

import errno
import logging
import os
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

LOGGER = logging.getLogger(__name__)
_UNSUPPORTED = {errno.EINVAL, errno.ENOTSUP, errno.EOPNOTSUPP, errno.ENOSYS}


def sync_directory(directory: Path) -> None:
    """Flush a directory entry change; silently skip where it is unsupported."""
    try:
        descriptor = os.open(directory, os.O_RDONLY)
    except OSError:
        return  # e.g. directories cannot be opened on this platform
    try:
        os.fsync(descriptor)
    except OSError as error:
        if error.errno not in _UNSUPPORTED:
            raise
    finally:
        os.close(descriptor)


def atomic_write_bytes(
    path: Path,
    data: bytes,
    *,
    mode: int | None = None,
    before_replace: Callable[[], None] | None = None,
    create_parent: bool = True,
) -> None:
    """Publish ``data`` at ``path`` atomically and durably.

    ``mode`` of None keeps the umask-governed default of a plain file; an integer
    is applied exactly (``0o600`` for private files). ``before_replace`` runs after
    the data is on disk and before the replace, for fault injection or a last
    check; if it raises, nothing is published and the temporary is removed.
    The parent directory is created if missing unless ``create_parent`` is False,
    in which case a missing parent raises FileNotFoundError (a late write into a
    deleted document folder must not bring the folder back). Any existing
    destination, symlink included, is replaced by the new regular file, never
    written through.
    """
    if create_parent:
        path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    descriptor = os.open(  # a failure here created nothing, so nothing is removed
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o666 if mode is None else mode,
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            if mode is not None:
                os.fchmod(stream.fileno(), mode)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if before_replace is not None:
            before_replace()
        os.replace(temporary, path)
        try:
            sync_directory(path.parent)
        except OSError:
            # Published already: report, but do not make the caller undo a durable write.
            LOGGER.warning("directory sync after publishing %s failed", path, exc_info=True)
    finally:
        temporary.unlink(missing_ok=True)
