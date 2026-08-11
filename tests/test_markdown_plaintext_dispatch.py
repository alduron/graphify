"""Plain-text document formats are walked, not merely linkable (Plan 62 Phase 2).

extract_markdown has emitted ``file --references--> linked document`` edges since #1376, and
_MD_LINKABLE_EXTS has claimed ``.markdown``, ``.rst`` and ``.txt`` as valid link targets the whole
time. But collect_files enumerates ONLY _DISPATCH suffixes, and those three were absent from it. So
a link to a sibling ``notes.txt`` resolved to a node id that the walk never created: the edge pointed
at nothing, and a fileless target is dropped downstream rather than kept
(Caveat_ANewEdgeKindNeedsItsTargetKeptOrTheEdgeDies).

The invariant these tests hold is the one that failed silently: an extension the link resolver will
resolve TO must be an extension the walker collects.
"""
from pathlib import Path

from graphify.extract import _DISPATCH, _MD_LINKABLE_EXTS, collect_files, extract_markdown


def _write(tmp_path: Path, name: str, body: str) -> Path:
    p = tmp_path / name
    p.write_text(body, encoding="utf-8")
    return p


def test_markdown_linkable_exts_are_all_walkable():
    """The load-bearing assertion. Either set may grow; they may not disagree."""
    missing = _MD_LINKABLE_EXTS - set(_DISPATCH)
    assert missing == set(), (
        f"{sorted(missing)} resolve as link targets but are never enumerated by collect_files, "
        "so every edge pointing at one dies"
    )


def test_plaintext_document_suffixes_dispatch_to_the_markdown_extractor():
    for ext in (".markdown", ".rst", ".txt"):
        assert _DISPATCH[ext] is extract_markdown


def test_a_txt_document_is_collected_by_the_walk(tmp_path):
    _write(tmp_path, "notes.txt", "some notes\n")
    _write(tmp_path, "readme.md", "# Readme\n")
    collected = {p.name for p in collect_files(tmp_path)}
    assert collected == {"notes.txt", "readme.md"}


def test_a_link_to_a_txt_sibling_reaches_that_siblings_own_node(tmp_path):
    """The end-to-end shape of the defect: the edge target id must equal the id the sibling file
    produces for itself, or the two never merge into one node."""
    hub = _write(tmp_path, "index.md", "See [the notes](./notes.txt) for detail.\n")
    sibling = _write(tmp_path, "notes.txt", "raw notes\n")

    edges = extract_markdown(hub)["edges"]
    refs = [e for e in edges if e["relation"] == "references"]
    assert len(refs) == 1

    sibling_file_node = extract_markdown(sibling)["nodes"][0]
    assert refs[0]["target"] == sibling_file_node["id"]


def test_an_rst_document_yields_its_file_node_and_links(tmp_path):
    """reStructuredText underline headings are not ATX, so there are no section nodes. The file node
    plus resolvable links is the intended floor and is strictly more than the previous nothing."""
    doc = _write(
        tmp_path,
        "guide.rst",
        "Guide\n=====\n\nSee `the readme <./readme.md>`_ and [also](./other.rst).\n",
    )
    result = extract_markdown(doc)
    assert result["nodes"][0]["label"] == "guide.rst"
    targets = {e["target"] for e in result["edges"] if e["relation"] == "references"}
    assert any("other" in t for t in targets)


def test_an_oversized_document_is_skipped_rather_than_read_whole(tmp_path):
    """.txt is an open grab bag (logs, licences, data dumps), so the walk needs the ceiling
    extract_json and extract_yaml already carry."""
    from graphify.extract import _MD_MAX_BYTES

    big = _write(tmp_path, "huge.txt", "x" * (_MD_MAX_BYTES + 10))
    result = extract_markdown(big)
    assert result["nodes"] == []
    assert "too large" in result["error"]
