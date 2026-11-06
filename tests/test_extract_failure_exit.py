"""AST extraction failure must exit nonzero and leave outputs untouched."""
from __future__ import annotations

import concurrent.futures
import concurrent.futures.process

import pytest

import graphify.__main__ as mainmod
import graphify.extract as extractmod


def test_ast_failure_exits_nonzero_and_keeps_prior_graph(monkeypatch, tmp_path, capsys):
    (tmp_path / "main.go").write_text("package main\nfunc main() {}\n")
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    graph = out_dir / "graph.json"
    graph.write_bytes(b'{"nodes": [{"id": "keep"}], "links": []}')
    before = graph.read_bytes()

    def _boom(paths, **kwargs):
        raise MemoryError()

    monkeypatch.setattr("graphify.extract.extract", _boom)
    monkeypatch.setattr(mainmod, "_check_skill_version", lambda _: None)
    monkeypatch.setattr(
        mainmod.sys, "argv", ["graphify", "extract", str(tmp_path), "--out", str(out_dir)]
    )

    with pytest.raises(SystemExit) as exc_info:
        mainmod.main()

    assert exc_info.value.code not in (0, None)
    assert graph.read_bytes() == before
    assert not (out_dir / "manifest.json").exists()
    err = capsys.readouterr().err
    assert "AST extraction failed" in err
    assert "MemoryError" in err


class _BrokenPool:
    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def submit(self, fn, item):
        fut = concurrent.futures.Future()
        fut.set_exception(concurrent.futures.process.BrokenProcessPool("pool died"))
        return fut


def test_broken_pool_falls_back_to_sequential_and_keeps_all_files(monkeypatch, tmp_path):
    count = extractmod._PARALLEL_THRESHOLD + 5
    paths = []
    for i in range(count):
        p = tmp_path / f"m{i}.py"
        p.write_text(f"def f{i}():\n    return {i}\n")
        paths.append(p)

    monkeypatch.setattr(concurrent.futures, "ProcessPoolExecutor", _BrokenPool)
    result = extractmod.extract(paths, cache_root=tmp_path, source_root=tmp_path, parallel=True)

    names = {n.get("label", "") for n in result["nodes"]}
    for i in range(count):
        assert any(f"f{i}" in name for name in names), f"f{i} dropped"
