"""Symbols differing only by underscore padding must stay DISTINCT nodes.

ids.make_id strips leading/trailing underscores, so `_render` and `render` normalize
onto one id. The second one used to hit `nid in seen_ids` and never be appended at
all: the symbol was absent from the graph and calls to it misattributed to its
sibling. Verified live on aethergraph (BlockRuleset.evaluate/_evaluate - the
block-enforcement path - WorkingSetPredictor.predict/_predict, Cli.__init__/init).

The first claimant keeps the unsuffixed id so no stored anchor/symbol_key is
rewritten; only the symbol that previously did not exist gets a new one.
"""

from __future__ import annotations

from pathlib import Path

from graphify.extract import extract


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _labels(result: dict) -> set[str]:
    return {n.get("label") for n in result["nodes"] if n.get("file_type") == "code"}


def _edge(result: dict, src_label: str, tgt_label: str) -> bool:
    by_id = {n["id"]: n for n in result["nodes"]}
    return any(
        e["relation"] == "calls"
        and by_id.get(e["source"], {}).get("label") == src_label
        and by_id.get(e["target"], {}).get("label") == tgt_label
        for e in result["edges"]
    )


def test_private_and_public_sibling_methods_are_both_nodes(tmp_path: Path):
    src = _write(
        tmp_path / "svc.py",
        "class Renderer:\n"
        "    def _render(self, x):\n"
        "        return x\n\n"
        "    def render(self, x):\n"
        "        return self._render(x)\n\n"
        "    def go(self, x):\n"
        "        return self.render(x)\n",
    )

    result = extract([src], cache_root=tmp_path / "cache")

    labels = _labels(result)
    assert "._render()" in labels
    assert ".render()" in labels, "the public sibling was dropped onto the private one"
    # And the calls land on the right one of the pair.
    assert _edge(result, ".render()", "._render()")
    assert _edge(result, ".go()", ".render()")


def test_public_first_then_private_also_stays_distinct(tmp_path: Path):
    """Order must not decide whether a symbol exists - only which id it gets."""
    src = _write(
        tmp_path / "svc.py",
        "class Renderer:\n"
        "    def render(self, x):\n"
        "        return x\n\n"
        "    def _render(self, x):\n"
        "        return x\n",
    )

    result = extract([src], cache_root=tmp_path / "cache")

    labels = _labels(result)
    assert ".render()" in labels
    assert "._render()" in labels


def test_dunder_and_bare_sibling_stay_distinct(tmp_path: Path):
    """`__init__` and `init` both normalize to `init` (repeated `_` collapse + strip)."""
    src = _write(
        tmp_path / "cli.py",
        "class Cli:\n"
        "    def __init__(self):\n"
        "        self.x = 1\n\n"
        "    def init(self):\n"
        "        return 2\n",
    )

    result = extract([src], cache_root=tmp_path / "cache")

    labels = _labels(result)
    assert ".__init__()" in labels
    assert ".init()" in labels


def test_module_level_underscore_siblings_stay_distinct(tmp_path: Path):
    """Not just methods: two module-level functions collide the same way."""
    src = _write(
        tmp_path / "mod.py",
        "def _helper(x):\n    return x\n\n"
        "def helper(x):\n    return _helper(x)\n",
    )

    result = extract([src], cache_root=tmp_path / "cache")

    labels = _labels(result)
    assert "_helper()" in labels
    assert "helper()" in labels


def test_first_claimant_keeps_the_unsuffixed_id(tmp_path: Path):
    """The pre-existing id must be preserved, or every stored anchor/symbol_key
    pointing at it would silently detach on re-index."""
    src = _write(
        tmp_path / "svc.py",
        "class Renderer:\n"
        "    def _render(self):\n        return 1\n\n"
        "    def render(self):\n        return 2\n",
    )

    result = extract([src], cache_root=tmp_path / "cache")

    ids = {n["label"]: n["id"] for n in result["nodes"] if n.get("file_type") == "code"}
    assert not ids["._render()"].endswith("_n"), "first claimant's id must not move"
    assert ids[".render()"] != ids["._render()"]


def test_no_suffix_when_there_is_no_collision(tmp_path: Path):
    """An underscore-named symbol with no sibling keeps its plain id - the fix must
    not churn ids for the (vast majority) non-colliding case."""
    src = _write(tmp_path / "mod.py", "def _solo(x):\n    return x\n")

    result = extract([src], cache_root=tmp_path / "cache")

    ids = {n["label"]: n["id"] for n in result["nodes"] if n.get("file_type") == "code"}
    assert ids["_solo()"].endswith("_solo")
