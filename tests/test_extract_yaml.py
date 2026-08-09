"""YAML structural extraction (#1544).

A config/manifest file must yield its SECTION tree; a data-shaped file must yield
almost nothing. The bounds are the whole point -- an unbounded key walk over YAML
reproduces the orphan-key explosion that made extract_json manifest-only (#1224).
"""
from pathlib import Path

from graphify.extract import _DISPATCH, extract_yaml


def _labels(result: dict) -> list[str]:
    return [n["label"] for n in result["nodes"]]


def _write(tmp_path: Path, name: str, body: str) -> Path:
    p = tmp_path / name
    p.write_text(body, encoding="utf-8")
    return p


def test_yaml_is_dispatched_by_suffix():
    # Registering the suffix is also what puts it in collect_files' walk set, so a
    # missing entry here means yaml is never even enumerated, not merely unparsed.
    assert _DISPATCH[".yaml"] is extract_yaml
    assert _DISPATCH[".yml"] is extract_yaml


def test_nested_keys_are_labelled_by_dotted_path(tmp_path):
    p = _write(tmp_path, "config.yaml", """
candidate:
  name: "Robert"
  attestations:
    criminal_conviction: false
search:
  max_age_hours: 24
""".lstrip())
    labels = _labels(extract_yaml(p))
    assert labels[0] == "config.yaml"
    # The dotted path, not the bare key: it is unique within the file, so an anchor
    # reads `config.yaml::candidate.attestations`.
    assert "candidate" in labels
    assert "candidate.name" in labels
    assert "candidate.attestations" in labels
    assert "candidate.attestations.criminal_conviction" in labels
    assert "search.max_age_hours" in labels
    assert "name" not in labels


def test_file_contains_top_level_and_parents_contain_children(tmp_path):
    p = _write(tmp_path, "c.yaml", "top:\n  child: 1\n")
    result = extract_yaml(p)
    by_label = {n["label"]: n["id"] for n in result["nodes"]}
    pairs = {(e["source"], e["target"]) for e in result["edges"]}
    assert all(e["relation"] == "contains" for e in result["edges"])
    assert (by_label["c.yaml"], by_label["top"]) in pairs
    assert (by_label["top"], by_label["top.child"]) in pairs


def test_sequence_item_fields_are_skipped_as_data_rows(tmp_path):
    # pre-commit shape: the item's sibling keys sit on their own undashed lines. They
    # are data rows, and treating them as sections invents `repos.hooks.name`.
    p = _write(tmp_path, ".pre-commit-config.yaml", """
repos:
  - repo: local
    hooks:
      - id: style
        name: style gate
  - repo: other
    hooks:
      - id: x
""".lstrip())
    labels = _labels(extract_yaml(p))
    assert labels == [".pre-commit-config.yaml", "repos"]


def test_scalar_sequence_keeps_its_key(tmp_path):
    p = _write(tmp_path, "c.yaml", 'search:\n  departments:\n    - "Engineering"\n    - "IT"\n')
    labels = _labels(extract_yaml(p))
    assert "search.departments" in labels


def test_dedent_out_of_a_sequence_resumes_mapping_keys(tmp_path):
    p = _write(tmp_path, "c.yaml", """
repos:
  - repo: local
    hooks: []
after:
  key: 1
""".lstrip())
    labels = _labels(extract_yaml(p))
    assert "after" in labels
    assert "after.key" in labels
    assert "repos.hooks" not in labels


def test_block_scalar_body_is_not_parsed_as_keys(tmp_path):
    p = _write(tmp_path, "c.yaml", """
job:
  run: |
    export A=1
    echo not_a_key: value
    fake: mapping
  shell: bash
""".lstrip())
    labels = _labels(extract_yaml(p))
    assert "job.run" in labels
    assert "job.shell" in labels
    assert not any("not_a_key" in lbl or "fake" in lbl for lbl in labels)


def test_comments_and_document_markers_do_not_emit(tmp_path):
    p = _write(tmp_path, "c.yaml", """
# leading: comment
---
real: 1
""".lstrip())
    labels = _labels(extract_yaml(p))
    assert labels == ["c.yaml", "real"]


def test_depth_cap_drops_the_deep_subtree_without_orphaning(tmp_path):
    p = _write(tmp_path, "c.yaml", "a:\n  b:\n    c:\n      d: 1\n")
    result = extract_yaml(p)
    labels = _labels(result)
    assert "a.b.c" in labels        # depth 3 kept
    assert "a.b.c.d" not in labels  # depth 4 dropped
    # Nothing survives without a contains edge into it.
    targets = {e["target"] for e in result["edges"]}
    node_ids = {n["id"] for n in result["nodes"]}
    file_id = result["nodes"][0]["id"]
    assert node_ids - targets == {file_id}


def test_fanout_cap_stops_a_data_map(tmp_path):
    body = "lookup:\n" + "".join(f"  key_{i}: {i}\n" for i in range(120))
    p = _write(tmp_path, "c.yaml", body)
    labels = _labels(extract_yaml(p))
    assert "lookup" in labels
    # Capped well below 120, and the surviving children are still real children.
    assert 1 < len(labels) <= 45


def test_oversized_file_is_skipped_not_parsed(tmp_path):
    p = _write(tmp_path, "big.yaml", "k: " + "x" * (1_048_576 + 10) + "\n")
    result = extract_yaml(p)
    assert result["nodes"] == []
    assert "too large" in result["error"]


def test_unreadable_file_returns_an_error_not_a_raise(tmp_path):
    result = extract_yaml(tmp_path / "missing.yaml")
    assert result["nodes"] == []
    assert result["error"]


def test_tab_indented_line_is_skipped_rather_than_mislevelled(tmp_path):
    # YAML forbids tab indentation; guessing a level for one would corrupt the stack.
    p = _write(tmp_path, "c.yaml", "a:\n\tb: 1\nc: 2\n")
    labels = _labels(extract_yaml(p))
    assert "c" in labels
    assert not any(lbl.endswith(".b") for lbl in labels)


def test_quoted_keys_are_unquoted(tmp_path):
    p = _write(tmp_path, "c.yaml", '"quoted": 1\n\'single\': 2\n')
    labels = _labels(extract_yaml(p))
    assert "quoted" in labels
    assert "single" in labels


def test_every_node_carries_the_config_file_type(tmp_path):
    p = _write(tmp_path, "c.yaml", "a:\n  b: 1\n")
    result = extract_yaml(p)
    assert {n["file_type"] for n in result["nodes"]} == {"config"}
    assert all(n["source_file"] == str(p) for n in result["nodes"])
