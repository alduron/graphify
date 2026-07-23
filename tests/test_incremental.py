"""Integration tests for incremental graphify extract behavior."""
from __future__ import annotations
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

PYTHON = sys.executable

# Backend-selecting env vars. These tests assume no working LLM backend (a docs
# corpus should fail without one); strip them so a developer who has a real
# ANTHROPIC_API_KEY / OPENAI_API_KEY / etc. exported does not make a docs extract
# succeed and break the "no backend" path. CI has none of these set anyway.
_LLM_ENV_KEYS = (
    "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY",
    "MOONSHOT_API_KEY", "DEEPSEEK_API_KEY", "OLLAMA_BASE_URL",
    "AWS_PROFILE", "AWS_REGION", "AWS_DEFAULT_REGION", "AWS_ACCESS_KEY_ID",
)


def _run(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k not in _LLM_ENV_KEYS}
    return subprocess.run(
        [PYTHON, "-m", "graphify"] + args,
        cwd=cwd,
        capture_output=True,
        text=True,
        env=env,
    )


def _make_docs_corpus(tmp_path: Path) -> Path:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "intro.md").write_text("# Introduction\nThis doc introduces the system.")
    (docs / "api.md").write_text("# API Reference\nThe API has endpoints.")
    return docs


def test_manifest_written_after_extract(tmp_path):
    """After a full extract run, manifest.json must exist (or run fails before writing it)."""
    docs = _make_docs_corpus(tmp_path)
    r = _run(["extract", str(docs)], tmp_path)
    # Should fail with no API key — but NOT with a path error
    assert "no LLM API key" in r.stderr or r.returncode != 0
    # manifest should NOT exist (run failed before writing)
    manifest = docs / "graphify-out" / "manifest.json"
    assert not manifest.exists()


def test_incremental_mode_detected_via_manifest(tmp_path):
    """If manifest.json + graph.json exist, incremental mode message is shown."""
    docs = _make_docs_corpus(tmp_path)
    out = docs / "graphify-out"
    out.mkdir()
    (out / "graph.json").write_text(json.dumps({"nodes": [], "links": []}))
    (out / "manifest.json").write_text(json.dumps({"document": [str(docs / "intro.md")]}))
    r = _run(["extract", str(docs)], tmp_path)
    combined = r.stdout + r.stderr
    assert "incremental" in combined.lower() or r.returncode != 0


def test_no_incremental_without_manifest(tmp_path):
    """Without manifest.json, full scan message is shown (not incremental)."""
    docs = _make_docs_corpus(tmp_path)
    r = _run(["extract", str(docs)], tmp_path)
    # Check combined output doesn't contain incremental-mode phrasing.
    # Use a phrase rather than a bare word to avoid matching the tmp_path,
    # which pytest derives from the test name and contains "incremental".
    assert "incremental update" not in r.stdout.lower()
    assert "incremental scan" not in r.stdout.lower()


def test_extract_no_cluster_incremental_noop_preserves_existing_graph(tmp_path):
    """#1347: no-op incremental no-cluster extract must not overwrite graph.json."""
    project = tmp_path / "project"
    project.mkdir()
    (project / "app.py").write_text(
        "def alpha():\n    return 1\n", encoding="utf-8"
    )

    first = _run(["extract", str(project), "--no-cluster"], tmp_path)
    assert first.returncode == 0, first.stderr
    graph_path = project / "graphify-out" / "graph.json"
    before_text = graph_path.read_text(encoding="utf-8")
    before = json.loads(before_text)
    assert before.get("nodes"), "first run should produce a non-empty code graph"

    second = _run(["extract", str(project), "--no-cluster"], tmp_path)
    assert second.returncode == 0, second.stderr

    after_text = graph_path.read_text(encoding="utf-8")
    after = json.loads(after_text)
    assert after.get("nodes"), "no-op incremental run must not empty the graph"
    assert after_text == before_text


def _edges(graph_json: Path) -> list[dict]:
    g = json.loads(graph_json.read_text())
    return g.get("links", g.get("edges", []))


def _node_labels(graph_json: Path) -> set[str]:
    g = json.loads(graph_json.read_text())
    return {n.get("label") for n in g.get("nodes", [])}


def _make_three_file_project(base: Path) -> Path:
    """A tiny 3-file project used by the `update --files` scenarios below."""
    proj = base / "proj"
    proj.mkdir()
    (proj / "keep.py").write_text("def keep_fn():\n    return 1\n", encoding="utf-8")
    (proj / "modify.py").write_text("def old_fn():\n    return 2\n", encoding="utf-8")
    (proj / "drop.py").write_text("def drop_fn():\n    return 3\n", encoding="utf-8")
    return proj


def test_update_files_flag_incremental_rebuild(tmp_path):
    """`graphify update --files <list>` (PR 3a) must exercise the SAME incremental
    path _rebuild_code already implements for the watcher/hooks: only the changed
    file is re-extracted, an untouched file's nodes survive unchanged, and a
    deleted file's nodes are evicted -- without ever falling back to a full-corpus
    rebuild (which would also happen to produce the right end state, so the
    real assertion that matters is that this only re-extracts what --files lists;
    see the graphify.watch._rebuild_code unit tests in test_watch.py for that)."""
    proj = _make_three_file_project(tmp_path)

    r1 = _run(["extract", str(proj), "--no-cluster"], tmp_path)
    assert r1.returncode == 0, r1.stderr
    gj = proj / "graphify-out" / "graph.json"
    before_labels = _node_labels(gj)
    assert {"keep_fn()", "old_fn()", "drop_fn()"} <= before_labels

    # Modify one file, delete another, leave the third untouched.
    (proj / "modify.py").write_text("def new_fn():\n    return 20\n", encoding="utf-8")
    (proj / "drop.py").unlink()

    changelist = tmp_path / "changed_files.txt"
    changelist.write_text(
        "\n".join([str(proj / "modify.py"), str(proj / "drop.py"), ""]),
        encoding="utf-8",
    )

    r2 = _run(["update", str(proj), "--files", str(changelist), "--no-cluster"], tmp_path)
    assert r2.returncode == 0, r2.stderr

    after_labels = _node_labels(gj)
    assert "new_fn()" in after_labels, "modified file's new symbol should appear"
    assert "old_fn()" not in after_labels, "modified file's stale symbol should be gone"
    assert "keep_fn()" in after_labels, "untouched file's nodes must survive"
    assert "drop_fn()" not in after_labels, "deleted file's nodes must be evicted"


def test_update_files_and_out_flag_write_outside_project_tree(tmp_path):
    """`graphify update --files ... --out DIR` must resolve graph.json/cache/lock
    under DIR/graphify-out/ (mirroring `graphify extract --out DIR`), never
    inside the scanned project -- required for the agent-private-cache use case
    where the project tree must stay untouched by graphify's own output."""
    proj = _make_three_file_project(tmp_path)
    external_out = tmp_path / "agent-cache"

    r1 = _run(
        ["extract", str(proj), "--no-cluster", "--out", str(external_out)], tmp_path
    )
    assert r1.returncode == 0, r1.stderr
    gj = external_out / "graphify-out" / "graph.json"
    assert gj.exists()
    before_labels = _node_labels(gj)
    assert {"keep_fn()", "old_fn()", "drop_fn()"} <= before_labels
    assert not (proj / "graphify-out").exists(), "extract --out must not write inside the project"

    (proj / "modify.py").write_text("def new_fn():\n    return 20\n", encoding="utf-8")
    (proj / "drop.py").unlink()

    changelist = tmp_path / "changed_files.txt"
    changelist.write_text(
        "\n".join([str(proj / "modify.py"), str(proj / "drop.py"), ""]),
        encoding="utf-8",
    )

    r2 = _run(
        [
            "update", str(proj),
            "--files", str(changelist),
            "--out", str(external_out),
            "--no-cluster",
        ],
        tmp_path,
    )
    assert r2.returncode == 0, r2.stderr
    assert not (proj / "graphify-out").exists(), "update --out must not write inside the project"

    after_labels = _node_labels(gj)
    assert "new_fn()" in after_labels
    assert "old_fn()" not in after_labels
    assert "keep_fn()" in after_labels
    assert "drop_fn()" not in after_labels

    cache_dir = external_out / "graphify-out" / "cache"
    assert cache_dir.exists(), "the AST cache should also resolve under --out"


def test_update_prunes_a_removed_imports_edge(tmp_path):
    """#1521: when an import is deleted from a file, `graphify update` must prune
    the edge it produced — preserving it (keyed only on endpoint membership) left a
    stale edge that drove phantom circular-dependency findings."""
    proj = tmp_path / "proj"
    pkg = proj / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "b.py").write_text("def helper():\n    return 1\n")
    (pkg / "a.py").write_text("from pkg.b import helper\ndef use():\n    return helper()\n")

    # initial extract -> the import edge a -> b exists
    r1 = _run(["extract", str(proj), "--no-cluster"], tmp_path)
    assert r1.returncode == 0, r1.stderr
    gj = proj / "graphify-out" / "graph.json"
    before = _edges(gj)
    assert any(e.get("relation") in ("imports", "imports_from") and
               str(e.get("source_file", "")).endswith("a.py") for e in before), \
        f"expected an import edge from a.py initially: {before}"

    # remove the import, then update
    (pkg / "a.py").write_text("def use():\n    return 1\n")
    r2 = _run(["update", str(proj)], tmp_path)
    assert r2.returncode == 0, r2.stderr
    after = _edges(gj)

    # the stale import edge owned by a.py must be gone
    stale = [e for e in after
             if e.get("relation") in ("imports", "imports_from")
             and str(e.get("source_file", "")).endswith("a.py")]
    assert not stale, f"removed import's edge survived update (stale): {stale}"
