import os
import time

import pytest

from docling_desk.operations.retention import apply, candidates, load_policy


def test_retention_removes_expired_cache_only(tmp_path):
    policy = load_policy()
    original = tmp_path / "job" / "original.pdf"
    original.parent.mkdir()
    original.write_bytes(b"%PDF")
    database = tmp_path / "knowledge" / "local.sqlite"
    database.parent.mkdir()
    database.write_bytes(b"sqlite")
    thumb = tmp_path / "job" / "thumbnails" / "page.webp"
    thumb.parent.mkdir()
    thumb.write_bytes(b"image")
    cache = tmp_path / ".cache" / "old.bin"
    cache.parent.mkdir()
    cache.write_bytes(b"cache")
    pin = tmp_path / "models" / "layout" / ".cache" / "huggingface" / "trees" / ("a" * 40 + ".json")
    pin.parent.mkdir(parents=True)
    pin.write_text("{}", encoding="utf-8")
    stamp = tmp_path / "models" / "layout" / "revision.json"
    stamp.write_text("{}", encoding="utf-8")
    kept_cache = tmp_path / "knowledge" / ".cache" / "vectors.bin"
    kept_cache.parent.mkdir()
    kept_cache.write_bytes(b"vectors")
    old = time.time() - (policy["thumbnail_max_age_days"] + 1) * 86400
    os.utime(thumb, (old, old))
    os.utime(cache, (old, old))
    os.utime(pin, (old, old))
    os.utime(stamp, (old, old))
    os.utime(kept_cache, (old, old))
    expired = candidates(tmp_path, policy)
    assert thumb in expired and cache in expired
    assert pin not in expired and stamp not in expired
    apply(expired)
    assert original.read_bytes() == b"%PDF"
    assert database.read_bytes() == b"sqlite"
    assert not thumb.exists() and not cache.exists()
    assert kept_cache.read_bytes() == b"vectors"
    assert "rag-docling.jsonl" in policy["protected"]
    with pytest.raises(ValueError, match="protected"):
        apply([original])
