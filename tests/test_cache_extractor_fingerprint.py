"""The per-file cache must invalidate when the EXTRACTOR changes, not just the file.

Keyed on file content alone, an unchanged file replays a result produced by the OLD extractor, so
every extraction improvement is silently inert for every existing install until someone deletes the
cache by hand. Measured on aethergraph: three consecutive `sync --full` runs returned byte-identical
counts after an extractor fix shipped; deleting the cache recovered +990 edges immediately
(Caveat_GraphifyPerFileCacheMakesExtractorFixesInert).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from graphify import cache


@pytest.fixture(autouse=True)
def _reset_cache_module():
    """The stat index and the fingerprint are process-global; isolate every test."""
    cache.extractor_fingerprint.cache_clear()
    cache._stat_index = {}
    cache._stat_index_root = None
    cache._stat_index_dirty = False
    yield
    cache.extractor_fingerprint.cache_clear()
    cache._stat_index = {}
    cache._stat_index_root = None
    cache._stat_index_dirty = False


def test_fingerprint_is_stable_and_non_empty() -> None:
    first = cache.extractor_fingerprint()
    assert first
    assert first == cache.extractor_fingerprint(), "must be stable within a process"


def test_file_hash_changes_when_the_extractor_changes(tmp_path: Path, monkeypatch) -> None:
    """Same bytes on disk, different extractor -> different cache key. This is the whole fix."""
    src = tmp_path / "a.py"
    src.write_text("def f():\n    return 1\n", encoding="utf-8")

    monkeypatch.setattr(cache, "extractor_fingerprint", lambda: "extractor-AAAA")
    before = cache.file_hash(src, tmp_path)

    cache._stat_index = {}
    cache._stat_index_root = None
    monkeypatch.setattr(cache, "extractor_fingerprint", lambda: "extractor-BBBB")
    after = cache.file_hash(src, tmp_path)

    assert before != after, "an extractor change must invalidate the cached entry"


def test_file_hash_is_unchanged_for_the_same_extractor(tmp_path: Path, monkeypatch) -> None:
    """The fix must not defeat caching in the normal case - identical extractor, identical key."""
    src = tmp_path / "a.py"
    src.write_text("def f():\n    return 1\n", encoding="utf-8")

    monkeypatch.setattr(cache, "extractor_fingerprint", lambda: "extractor-AAAA")
    first = cache.file_hash(src, tmp_path)
    cache._stat_index = {}
    cache._stat_index_root = None
    second = cache.file_hash(src, tmp_path)

    assert first == second


def test_stat_index_is_dropped_when_the_fingerprint_changes(tmp_path: Path, monkeypatch) -> None:
    """The stat index maps (size, mtime) -> a PREVIOUSLY COMPUTED hash, so it would hand back
    pre-fingerprint keys and defeat the invalidation entirely if it were not also dropped."""
    index_file = cache._stat_index_file(tmp_path)
    index_file.parent.mkdir(parents=True, exist_ok=True)
    index_file.write_text(
        json.dumps({"_fingerprint": "OLD-EXTRACTOR", "C:/x/a.py": {"size": 1, "mtime_ns": 2, "hash": "stale"}}),
        encoding="utf-8",
    )

    monkeypatch.setattr(cache, "extractor_fingerprint", lambda: "NEW-EXTRACTOR")
    cache._ensure_stat_index(tmp_path)

    assert "C:/x/a.py" not in cache._stat_index, "stale hashes must not survive an extractor change"
    assert cache._stat_index.get("_fingerprint") == "NEW-EXTRACTOR"


def test_stat_index_is_kept_when_the_fingerprint_matches(tmp_path: Path, monkeypatch) -> None:
    """Matching extractor keeps the fast path - otherwise every run pays a full re-hash."""
    index_file = cache._stat_index_file(tmp_path)
    index_file.parent.mkdir(parents=True, exist_ok=True)
    index_file.write_text(
        json.dumps({"_fingerprint": "SAME", "C:/x/a.py": {"size": 1, "mtime_ns": 2, "hash": "keep"}}),
        encoding="utf-8",
    )

    monkeypatch.setattr(cache, "extractor_fingerprint", lambda: "SAME")
    cache._ensure_stat_index(tmp_path)

    assert cache._stat_index.get("C:/x/a.py", {}).get("hash") == "keep"
