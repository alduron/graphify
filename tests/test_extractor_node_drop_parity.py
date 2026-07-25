"""EVERY extractor must disambiguate colliding ids, not silently drop the second symbol.

ids.make_id strips underscore padding, so `_helper` and `helper` normalize onto one id. Each
extractor's `add_node` guards on `nid not in <seen set>`, so without _claim_nid the second symbol is
never appended - it is absent from the graph and calls to it misattribute to its sibling.

The generic tree-sitter extractor was fixed first (it also threads the effective id through its
callers, so its EDGES are correct too). These cover the other twenty extractors, which share the
same guard and therefore shared the same drop.
"""

from __future__ import annotations

from pathlib import Path

from graphify.extract import extract


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _labels(result: dict) -> set[str]:
    return {str(n.get("label") or "") for n in result["nodes"]}


def test_bash_underscore_sibling_functions_both_survive(tmp_path: Path):
    """`_deploy` / `deploy` is an extremely common shell idiom."""
    src = _write(
        tmp_path / "run.sh",
        "#!/usr/bin/env bash\n"
        "_deploy() {\n  echo private\n}\n\n"
        "deploy() {\n  _deploy\n}\n",
    )

    result = extract([src], cache_root=tmp_path / "cache")

    labels = _labels(result)
    assert any("_deploy" in lbl for lbl in labels)
    assert any(lbl.lstrip(".").rstrip("()") == "deploy" for lbl in labels), (
        f"the public sibling was dropped onto the private one: {sorted(labels)}"
    )


def test_go_underscore_sibling_functions_both_survive(tmp_path: Path):
    src = _write(
        tmp_path / "main.go",
        "package main\n\n"
        "func _helper() int { return 1 }\n\n"
        "func helper() int { return _helper() }\n",
    )

    result = extract([src], cache_root=tmp_path / "cache")

    labels = {lbl.lstrip(".").rstrip("()") for lbl in _labels(result)}
    assert "_helper" in labels
    assert "helper" in labels, f"the public sibling was dropped: {sorted(labels)}"


def test_rust_underscore_sibling_functions_both_survive(tmp_path: Path):
    src = _write(
        tmp_path / "lib.rs",
        "fn _helper() -> i32 { 1 }\n\n"
        "fn helper() -> i32 { _helper() }\n",
    )

    result = extract([src], cache_root=tmp_path / "cache")

    labels = {lbl.lstrip(".").rstrip("()") for lbl in _labels(result)}
    assert "_helper" in labels
    assert "helper" in labels, f"the public sibling was dropped: {sorted(labels)}"


def test_a_non_colliding_underscore_name_keeps_its_plain_id(tmp_path: Path):
    """The fix must not churn ids in the overwhelmingly common non-colliding case, or every
    stored anchor pointing at an underscore-named symbol would detach on re-index."""
    src = _write(tmp_path / "solo.sh", "#!/usr/bin/env bash\n_only() {\n  echo hi\n}\n")

    result = extract([src], cache_root=tmp_path / "cache")

    ids = [str(n.get("id")) for n in result["nodes"] if "_only" in str(n.get("label") or "")]
    assert ids, "the function node should exist"
    assert not any(i.endswith("_n") or "_u1_" in i for i in ids), f"id churned: {ids}"
