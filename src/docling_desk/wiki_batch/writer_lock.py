"""One host-wide writer lock shared by imports and translation publication."""

import fcntl
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def wiki_lock(root: Path):
    target = root / "data/wiki-write.lock"
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_symlink():
        raise ValueError("Wikiのロック保存先が不正です。")
    with target.open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
