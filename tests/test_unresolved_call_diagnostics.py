"""The unresolved-call diagnostic: make a dark call shape VISIBLE.

Call resolution is a chain of per-shape resolvers, and a shape no resolver claims
produces silence - no edge, no error, no warning. Two real defects lived in that
silence (Python module-qualified calls; underscore-padded sibling symbols), and both
were found by a human noticing a wrong answer rather than by any signal. These tests
pin the counter that turns such a shape into a number you can regression-gate.
"""

from __future__ import annotations

from pathlib import Path

from graphify.extract import extract


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_a_resolvable_call_is_not_counted_unresolved(tmp_path: Path):
    _write(tmp_path / "pkg/__init__.py", "")
    callee = _write(tmp_path / "pkg/mod.py", "def target():\n    return 1\n")
    caller = _write(
        tmp_path / "pkg/app.py",
        "from pkg import mod\n\ndef go():\n    return mod.target()\n",
    )

    result = extract([callee, caller], cache_root=tmp_path / "cache")

    py = result["unresolved_calls"][".py"]
    assert py["total"] >= 1
    assert py["unresolved"] == 0


def test_an_unresolvable_call_is_counted(tmp_path: Path):
    """A call to something outside the corpus stays unresolved, and says so."""
    caller = _write(
        tmp_path / "app.py",
        "import requests\n\ndef go():\n    return requests.get('x')\n",
    )

    result = extract([caller], cache_root=tmp_path / "cache")

    py = result["unresolved_calls"][".py"]
    assert py["unresolved"] == 1
    assert py["member"] == 1
    assert py["unresolved_ratio"] > 0


def test_counter_is_per_callsite_not_per_caller(tmp_path: Path):
    """One function with a resolvable AND an unresolvable call must count exactly one
    of each. A caller-level check would score the whole function either way and drown
    a real regression in noise."""
    _write(tmp_path / "pkg/__init__.py", "")
    callee = _write(tmp_path / "pkg/mod.py", "def target():\n    return 1\n")
    caller = _write(
        tmp_path / "pkg/app.py",
        "import requests\n"
        "from pkg import mod\n\n"
        "def go():\n"
        "    mod.target()\n"
        "    return requests.get('x')\n",
    )

    result = extract([callee, caller], cache_root=tmp_path / "cache")

    py = result["unresolved_calls"][".py"]
    assert py["unresolved"] == 1, "only the requests call is unresolvable"


def test_diagnostic_is_bucketed_by_extension(tmp_path: Path):
    """Per-language buckets: a shape going dark in one language must not be masked by
    healthy resolution in another."""
    py = _write(tmp_path / "app.py", "import requests\n\ndef go():\n    return requests.get('x')\n")
    js = _write(tmp_path / "app.js", "function go() { return missingThing.call(); }\n")

    result = extract([py, js], cache_root=tmp_path / "cache")

    buckets = result["unresolved_calls"]
    assert ".py" in buckets
    assert ".js" in buckets
    assert buckets[".py"]["unresolved"] == 1


def test_first_party_separates_a_missing_link_from_an_external_call(tmp_path: Path):
    """The raw unresolved ratio is uninterpretable on its own - most unresolved calls are always
    external. `first_party` counts only calls whose target this corpus DEFINES, so every one is a
    link the graph should have and does not. Its target is zero, so it can be regression-gated."""
    _write(tmp_path / "pkg/__init__.py", "")
    _write(tmp_path / "pkg/mod.py", "def homegrown():\n    return 1\n")
    caller = _write(
        tmp_path / "pkg/app.py",
        "import requests\n\n"
        "def go(thing):\n"
        "    thing.homegrown()\n"          # target IS defined here -> a missing link
        "    return requests.get('x')\n",  # external -> legitimately unresolved
    )
    callee = tmp_path / "pkg/mod.py"

    result = extract([callee, caller], cache_root=tmp_path / "cache")

    py = result["unresolved_calls"][".py"]
    assert py["unresolved"] == 2
    assert py["first_party"] == 1, "only the call whose target exists here counts"
    assert py["first_party_ratio"] > 0


def test_first_party_is_zero_when_everything_resolves(tmp_path: Path):
    _write(tmp_path / "pkg/__init__.py", "")
    callee = _write(tmp_path / "pkg/mod.py", "def target():\n    return 1\n")
    caller = _write(
        tmp_path / "pkg/app.py",
        "from pkg import mod\n\ndef go():\n    return mod.target()\n",
    )

    result = extract([callee, caller], cache_root=tmp_path / "cache")

    assert result["unresolved_calls"][".py"]["first_party"] == 0
