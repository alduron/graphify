"""Per-symbol spans and body hashes (AetherGraph Plan 44 section 2a).

The whole point of the body hash is to separate "this symbol MOVED" from "this symbol CHANGED". A
consumer that cannot tell those apart calls every refactor a change and flags every node anchored to
the file, so the move-invariance test below is the one that matters most.

The span derivation is an approximation of the AST end (see spans.py), and these pin which way it is
allowed to be wrong: containers cover their members, file nodes do NOT cover their whole file, and
anything unlocatable is left alone rather than guessed at.
"""

from __future__ import annotations

from pathlib import Path

from graphify.spans import annotate_symbol_spans

SOURCE = """import os


def alpha():
    return 1


class Widget:
    def render(self):
        return "a"

    def hide(self):
        return "b"
"""


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "sample.py"
    path.write_text(text, encoding="utf-8")
    return path


def _result(path: Path) -> dict:
    """The shape one file's extraction produces: a file node, a function, a class, two methods."""
    return {
        "nodes": [
            {"id": "sample", "label": "sample.py", "source_file": str(path), "source_location": "L1"},
            {"id": "alpha", "label": "alpha", "source_file": str(path), "source_location": "L4"},
            {"id": "widget", "label": "Widget", "source_file": str(path), "source_location": "L8"},
            {"id": "render", "label": "render", "source_file": str(path), "source_location": "L9"},
            {"id": "hide", "label": "hide", "source_file": str(path), "source_location": "L12"},
        ],
        "edges": [
            {"source": "sample", "target": "alpha", "relation": "contains"},
            {"source": "sample", "target": "widget", "relation": "contains"},
            {"source": "widget", "target": "render", "relation": "method"},
            {"source": "widget", "target": "hide", "relation": "method"},
        ],
    }


def _by_id(result: dict) -> dict[str, dict]:
    return {node["id"]: node for node in result["nodes"]}


def test_a_leaf_symbol_spans_to_the_next_declaration(tmp_path: Path) -> None:
    path = _write(tmp_path, SOURCE)
    nodes = _by_id(annotate_symbol_spans(_result(path), path))
    assert nodes["alpha"]["end_line"] == 7
    assert nodes["render"]["end_line"] == 11


def test_a_container_span_covers_its_members(tmp_path: Path) -> None:
    """Without this a class's span is just its header, so a method-body change would never move the
    class's hash - and a class is what knowledge is usually anchored to."""
    path = _write(tmp_path, SOURCE)
    nodes = _by_id(annotate_symbol_spans(_result(path), path))
    assert nodes["widget"]["end_line"] == nodes["hide"]["end_line"]


def test_a_file_node_does_NOT_span_its_whole_file(tmp_path: Path) -> None:
    """Deliberate. 'contains' does not extend, because file anchors are common and extending them
    would flag every file-anchored node on any change anywhere in the file."""
    path = _write(tmp_path, SOURCE)
    nodes = _by_id(annotate_symbol_spans(_result(path), path))
    assert nodes["sample"]["end_line"] == 3


def test_moving_a_symbol_without_changing_it_keeps_its_hash(tmp_path: Path) -> None:
    """THE test. A refactor that shifts a file down moves every symbol and changes none of them; a
    detector that cannot see the difference calls the whole file stale."""
    path = _write(tmp_path, SOURCE)
    before = _by_id(annotate_symbol_spans(_result(path), path))["alpha"]["body_sha"]

    shifted = _write(tmp_path, "# a new header comment\n# and another\n" + SOURCE)
    moved = _result(shifted)
    for node in moved["nodes"]:
        node["source_location"] = f"L{int(node['source_location'][1:]) + 2}"
    after = _by_id(annotate_symbol_spans(moved, shifted))["alpha"]["body_sha"]

    assert before == after


def test_changing_a_body_changes_that_symbols_hash_and_no_others(tmp_path: Path) -> None:
    path = _write(tmp_path, SOURCE)
    before = _by_id(annotate_symbol_spans(_result(path), path))

    edited = _write(tmp_path, SOURCE.replace('return "a"', 'return "CHANGED"'))
    after = _by_id(annotate_symbol_spans(_result(edited), edited))

    assert after["render"]["body_sha"] != before["render"]["body_sha"]
    # The container sees it too, since its span covers its members.
    assert after["widget"]["body_sha"] != before["widget"]["body_sha"]
    assert after["alpha"]["body_sha"] == before["alpha"]["body_sha"]
    assert after["hide"]["body_sha"] == before["hide"]["body_sha"]


def test_whitespace_only_edits_do_not_move_a_hash(tmp_path: Path) -> None:
    """Nothing true of a symbol's prose stops being true because its body gained trailing spaces."""
    path = _write(tmp_path, SOURCE)
    before = _by_id(annotate_symbol_spans(_result(path), path))["alpha"]["body_sha"]

    padded = _write(tmp_path, SOURCE.replace("    return 1", "    return 1   "))
    after = _by_id(annotate_symbol_spans(_result(padded), padded))["alpha"]["body_sha"]
    assert before == after


def test_a_sourceless_stub_is_left_alone(tmp_path: Path) -> None:
    """A cross-file reference placeholder has no body here; giving it the file's first span would
    make an unrelated edit look like a change to it."""
    path = _write(tmp_path, SOURCE)
    result = _result(path)
    result["nodes"].append(
        {"id": "elsewhere", "label": "Elsewhere", "source_file": "", "source_location": ""}
    )
    nodes = _by_id(annotate_symbol_spans(result, path))
    assert "end_line" not in nodes["elsewhere"]
    assert "body_sha" not in nodes["elsewhere"]


def test_an_unreadable_file_is_not_an_extraction_failure(tmp_path: Path) -> None:
    """A derived field must never take the extraction down with it."""
    missing = tmp_path / "gone.py"
    result = annotate_symbol_spans(_result(missing), missing)
    assert all("body_sha" not in node for node in result["nodes"])


def test_nodes_sharing_a_start_line_share_a_span(tmp_path: Path) -> None:
    """A decorator and its function, or a class and its constructor. Nothing in the text tells them
    apart, so claiming a different span for each would be an invention."""
    path = _write(tmp_path, SOURCE)
    result = _result(path)
    result["nodes"].append(
        {"id": "alpha_alias", "label": "alpha", "source_file": str(path), "source_location": "L4"}
    )
    nodes = _by_id(annotate_symbol_spans(result, path))
    assert nodes["alpha_alias"]["body_sha"] == nodes["alpha"]["body_sha"]


def test_a_containment_cycle_terminates(tmp_path: Path) -> None:
    path = _write(tmp_path, SOURCE)
    result = _result(path)
    result["edges"].append({"source": "render", "target": "widget", "relation": "method"})
    nodes = _by_id(annotate_symbol_spans(result, path))
    assert nodes["widget"]["end_line"] >= nodes["hide"]["end_line"]
