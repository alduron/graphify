"""`extract --no-semantic`: an AST-only corpus that still indexes parseable documents.

`classify_file` routes .md/.yaml to DOCUMENT, which lands them in `semantic_files`, which sets
`needs_llm`, which HARD-EXITS 1 without an API key. Any real repo trips that on its first README,
so an AST-only caller had no choice but to exclude every doc extension up front - and then markdown
headings and yaml keys, both of which parse deterministically, were never indexed at all.

`--no-semantic` splits the corpus by the only question that matters: does a deterministic extractor
exist for this file. If yes it goes to the AST path; if no it is dropped, with the count reported.
Mirrors what `watch._rebuild_code` already does for the incremental path.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Every backend key graphify's detect_backend() consults. Blanked so the test proves the
# no-API-key path rather than quietly passing on the developer's own credentials.
_KEY_VARS = (
    "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY",
    "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY",
)


def _corpus(tmp_path: Path) -> Path:
    (tmp_path / "docs").mkdir(parents=True)
    (tmp_path / "docs" / "guide.md").write_text("# Guide\n\n## Install\n\n## Usage\n", encoding="utf-8")
    (tmp_path / "config.yaml").write_text("search:\n  limit: 5\n", encoding="utf-8")
    (tmp_path / "mod.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    # .txt gained the markdown extractor (Plan 62 Phase 2), so it now ROUTES rather than drops.
    (tmp_path / "notes.txt").write_text("loose prose\n", encoding="utf-8")
    # .html is classified DOCUMENT and has no deterministic extractor, so it is now the one
    # that must be dropped rather than routed.
    (tmp_path / "page.html").write_text("<p>loose markup</p>\n", encoding="utf-8")
    return tmp_path


def _run(target: Path, out: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in __import__("os").environ.items() if k not in _KEY_VARS}
    env["PYTHONPATH"] = str(REPO_ROOT)
    return subprocess.run(
        [sys.executable, "-m", "graphify", "extract", str(target),
         "--out", str(out), "--no-cluster", *extra],
        capture_output=True, text=True, env=env, cwd=str(REPO_ROOT), timeout=600,
    )


def _nodes(out: Path) -> list[dict]:
    data = json.loads((out / "graphify-out" / "graph.json").read_text(encoding="utf-8"))
    return list(data.get("nodes") or [])


def test_no_semantic_succeeds_without_any_api_key(tmp_path):
    proc = _run(_corpus(tmp_path / "repo"), tmp_path / "out", "--no-semantic")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "no LLM API key found" not in (proc.stdout + proc.stderr)


def test_without_the_flag_a_doc_corpus_still_hard_exits(tmp_path):
    # The behaviour the flag exists to opt out of, pinned so the flag cannot quietly become a no-op.
    proc = _run(_corpus(tmp_path / "repo"), tmp_path / "out")
    assert proc.returncode == 1
    assert "no LLM API key found" in (proc.stdout + proc.stderr)


def test_parseable_documents_reach_the_ast_path(tmp_path):
    out = tmp_path / "out"
    _run(_corpus(tmp_path / "repo"), out, "--no-semantic")
    by_type: dict[str, set[str]] = {}
    for n in _nodes(out):
        by_type.setdefault(str(n.get("file_type") or ""), set()).add(str(n.get("label")))
    assert {"Guide", "Install", "Usage"} <= by_type.get("document", set())
    assert {"search", "search.limit"} <= by_type.get("config", set())
    assert "run()" in by_type.get("code", set())


def test_a_document_with_no_extractor_is_dropped_and_counted(tmp_path):
    out = tmp_path / "out"
    proc = _run(_corpus(tmp_path / "repo"), out, "--no-semantic")
    # Reported, never silent: a caller must be able to see what the corpus lost.
    assert "--no-semantic:" in proc.stdout
    assert "1 skipped" in proc.stdout
    assert not any(str(n.get("label")) == "page.html" for n in _nodes(out))
    # The counterpart: a document type that DOES have an extractor is routed, not dropped.
    assert any(str(n.get("label")) == "notes.txt" for n in _nodes(out))


def test_a_code_only_corpus_is_unaffected_by_the_flag(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "mod.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    plain = _run(repo, tmp_path / "a")
    flagged = _run(repo, tmp_path / "b", "--no-semantic")
    assert plain.returncode == 0 and flagged.returncode == 0
    assert {n["label"] for n in _nodes(tmp_path / "a")} == {n["label"] for n in _nodes(tmp_path / "b")}
    # Nothing to route, so the flag says nothing at all.
    assert "--no-semantic:" not in flagged.stdout
