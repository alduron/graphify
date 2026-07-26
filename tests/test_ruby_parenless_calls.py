"""Ruby's paren-less self-send (`helper`, no parens) must resolve - it is the dominant call style.

It never reached walk_calls: the grammar emits a bare `identifier`, not a `call` node, so these
calls were entirely absent from Ruby graphs.

Disambiguating is NOT a guess - it is Ruby's own rule: a bare name is a local variable read if a
local of that name is bound in scope, and a method call otherwise. The tests below pin both
directions, because getting the binding side wrong produces WRONG edges, which is worse than the
missing ones this fixes.
"""

from __future__ import annotations

from pathlib import Path

from graphify.extract import extract


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _calls(result: dict) -> set[tuple[str, str]]:
    by_id = {n["id"]: n for n in result["nodes"]}
    return {
        (str(by_id[e["source"]]["label"]), str(by_id[e["target"]]["label"]))
        for e in result["edges"]
        if e["relation"] == "calls"
    }


def test_parenless_self_send_resolves(tmp_path: Path):
    src = _write(tmp_path / "a.rb", "def helper\n  1\nend\n\ndef go\n  helper\nend\n")

    assert ("go()", "helper()") in _calls(extract([src], cache_root=tmp_path / "c"))


def test_a_local_variable_read_is_not_a_call(tmp_path: Path):
    """`helper = 5` binds a local, so the later bare `helper` is a READ, not a call."""
    src = _write(
        tmp_path / "a.rb", "def helper\n  1\nend\n\ndef go\n  helper = 5\n  helper\nend\n"
    )

    assert ("go()", "helper()") not in _calls(extract([src], cache_root=tmp_path / "c"))


def test_a_parameter_shadowing_a_method_is_not_a_call(tmp_path: Path):
    """Parameters live OUTSIDE the body node, so a collector that only walks the body misses them
    and emits a wrong edge here. This is the case that caught exactly that bug."""
    src = _write(tmp_path / "a.rb", "def helper\n  1\nend\n\ndef go(helper)\n  helper\nend\n")

    assert ("go()", "helper()") not in _calls(extract([src], cache_root=tmp_path / "c"))


def test_an_unknown_bare_name_produces_no_edge(tmp_path: Path):
    """Self-limiting: these go through the same name-resolution path as every other call, so an
    unresolvable name simply yields nothing rather than a speculative edge."""
    src = _write(tmp_path / "a.rb", "def go\n  mystery\nend\n")

    assert _calls(extract([src], cache_root=tmp_path / "c")) == set()


def test_explicit_paren_and_self_forms_still_work(tmp_path: Path):
    """Regression guard for the two forms that already resolved."""
    paren = _write(tmp_path / "p.rb", "def helper\n  1\nend\n\ndef go\n  helper()\nend\n")
    assert ("go()", "helper()") in _calls(extract([paren], cache_root=tmp_path / "c1"))

    selfy = _write(
        tmp_path / "s.rb",
        "class A\n  def helper\n    1\n  end\n  def go\n    self.helper\n  end\nend\n",
    )
    assert (".go()", ".helper()") in _calls(extract([selfy], cache_root=tmp_path / "c2"))
