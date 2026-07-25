"""The remaining EXACT-resolvable Python call shapes: super() and return-type propagation.

Both consume a DECLARATION rather than making an inference, so they stay inside the no-guessing rule
that Decision_PythonUntypedReceiverEmitsNoEdge sets:

  * `super().method()` - the target is the caller's own base classes, named in source.
  * `x = build()` then `x.method()` - x's type is `build`'s DECLARED `-> T` annotation.

With these, the exact set is exhausted: typed params, annotated locals, constructor calls,
self.<field>, self/cls (incl. inheritance), super(), ClassName.method(), module.func(), and declared
return types. Everything still unresolved needs a genuine guess.
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
        (
            str(by_id.get(e["source"], {}).get("label", "")),
            str(by_id.get(e["target"], {}).get("label", "")),
        )
        for e in result["edges"]
        if e["relation"] == "calls"
    }


# ---- super() ------------------------------------------------------------------------------------
def test_super_call_resolves_to_the_base_method_across_files(tmp_path: Path):
    base = _write(tmp_path / "base.py", "class Base:\n    def run(self):\n        return 1\n")
    child = _write(
        tmp_path / "child.py",
        "from base import Base\n\n"
        "class Child(Base):\n"
        "    def run(self):\n"
        "        return super().run()\n",
    )

    result = extract([base, child], cache_root=tmp_path / "cache")

    by_id = {n["id"]: n for n in result["nodes"]}
    hit = [
        e for e in result["edges"]
        if e["relation"] == "calls"
        and "run" in str(by_id.get(e["source"], {}).get("label", ""))
        and str(by_id.get(e["target"], {}).get("source_file", "")).endswith("base.py")
    ]
    assert hit, "super().run() must resolve to the BASE class's run"


def test_super_call_does_not_resolve_to_the_overriding_method_itself(tmp_path: Path):
    """super() means 'skip my own class'. Resolving to the caller's class would find the very
    method making the call - a self-loop that inverts what super() means."""
    src = _write(
        tmp_path / "solo.py",
        "class Base:\n    def run(self):\n        return 1\n\n"
        "class Child(Base):\n    def run(self):\n        return super().run()\n",
    )

    result = extract([src], cache_root=tmp_path / "cache")

    for edge in result["edges"]:
        if edge["relation"] == "calls":
            assert edge["source"] != edge["target"], "super() resolved to the caller itself"


# ---- return-type propagation --------------------------------------------------------------------
def test_declared_return_type_types_the_local_across_files(tmp_path: Path):
    _write(tmp_path / "models.py", "class Repo:\n    def save(self):\n        return 1\n")
    factory = _write(
        tmp_path / "factory.py",
        "from models import Repo\n\ndef build_repo() -> Repo:\n    return Repo()\n",
    )
    caller = _write(
        tmp_path / "app.py",
        "from factory import build_repo\n\n"
        "def go():\n"
        "    repo = build_repo()\n"
        "    return repo.save()\n",
    )

    result = extract([tmp_path / "models.py", factory, caller], cache_root=tmp_path / "cache")

    assert ("go()", ".save()") in _calls(result)


def test_an_unannotated_producer_types_nothing(tmp_path: Path):
    """No declaration means no resolution - the rule this must not break."""
    _write(tmp_path / "models.py", "class Repo:\n    def save(self):\n        return 1\n")
    factory = _write(
        tmp_path / "factory.py",
        "from models import Repo\n\ndef build_repo():\n    return Repo()\n",
    )
    caller = _write(
        tmp_path / "app.py",
        "from factory import build_repo\n\n"
        "def go():\n"
        "    repo = build_repo()\n"
        "    return repo.save()\n",
    )

    result = extract([tmp_path / "models.py", factory, caller], cache_root=tmp_path / "cache")

    assert ("go()", ".save()") not in _calls(result)


def test_a_conflicting_return_annotation_across_the_corpus_is_refused(tmp_path: Path):
    """Two `build()` declarations returning DIFFERENT types cannot both be right - refuse."""
    _write(tmp_path / "a_model.py", "class Alpha:\n    def save(self):\n        return 1\n")
    _write(tmp_path / "b_model.py", "class Beta:\n    def save(self):\n        return 2\n")
    fa = _write(tmp_path / "fa.py", "from a_model import Alpha\n\ndef build() -> Alpha:\n    return Alpha()\n")
    fb = _write(tmp_path / "fb.py", "from b_model import Beta\n\ndef build() -> Beta:\n    return Beta()\n")
    caller = _write(
        tmp_path / "app.py",
        "from fa import build\n\ndef go():\n    x = build()\n    return x.save()\n",
    )

    result = extract(
        [tmp_path / "a_model.py", tmp_path / "b_model.py", fa, fb, caller],
        cache_root=tmp_path / "cache",
    )

    assert ("go()", ".save()") not in _calls(result)


def test_an_annotated_local_still_wins_over_the_producer(tmp_path: Path):
    """An explicit annotation on the variable is more direct than its producer's return type;
    the existing path must keep priority."""
    _write(tmp_path / "models.py", "class Repo:\n    def save(self):\n        return 1\n")
    caller = _write(
        tmp_path / "app.py",
        "from models import Repo\n\n"
        "def go():\n"
        "    repo: Repo = make()\n"
        "    return repo.save()\n",
    )

    result = extract([tmp_path / "models.py", caller], cache_root=tmp_path / "cache")

    assert ("go()", ".save()") in _calls(result)
