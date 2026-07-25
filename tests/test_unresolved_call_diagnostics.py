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
