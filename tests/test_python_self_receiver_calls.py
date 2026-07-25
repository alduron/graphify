"""`self.method()` resolves EXACTLY - including inherited and cross-file methods.

The receiver's class is the caller's own enclosing class, known exactly from source. That is not
inference, so it is NOT covered by Decision_PythonUntypedReceiverEmitsNoEdge (which governs a
receiver whose type is genuinely unknowable, like an unannotated parameter).

Before this, only a same-file same-class method resolved - via bare-name lookup in the per-file
pass. An inherited method, or one defined in another file, produced NO edge at all.
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


def test_self_call_to_an_inherited_method_in_another_file(tmp_path: Path):
    """The whole point: the base class lives in a different file, so only an MRO walk finds it."""
    base = _write(
        tmp_path / "base.py",
        "class Base:\n    def helper(self):\n        return 1\n",
    )
    child = _write(
        tmp_path / "child.py",
        "from base import Base\n\n"
        "class Child(Base):\n"
        "    def go(self):\n"
        "        return self.helper()\n",
    )

    result = extract([base, child], cache_root=tmp_path / "cache")

    assert (".go()", ".helper()") in _calls(result)


def test_cls_receiver_resolves_the_same_way(tmp_path: Path):
    src = _write(
        tmp_path / "svc.py",
        "class Svc:\n"
        "    @classmethod\n"
        "    def build(cls):\n"
        "        return cls.make()\n\n"
        "    @classmethod\n"
        "    def make(cls):\n"
        "        return 1\n",
    )

    result = extract([src], cache_root=tmp_path / "cache")

    assert (".build()", ".make()") in _calls(result)


def test_self_call_does_not_reach_a_same_named_method_on_an_unrelated_class(tmp_path: Path):
    """Resolution is bounded to the caller's own hierarchy - no god-node fan-out (#543/#1219)."""
    other = _write(
        tmp_path / "other.py",
        "class Unrelated:\n    def helper(self):\n        return 'wrong'\n",
    )
    own = _write(
        tmp_path / "own.py",
        "class Owner:\n"
        "    def helper(self):\n        return 'right'\n\n"
        "    def go(self):\n        return self.helper()\n",
    )

    result = extract([other, own], cache_root=tmp_path / "cache")

    by_id = {n["id"]: n for n in result["nodes"]}
    for edge in result["edges"]:
        if edge["relation"] != "calls":
            continue
        src = by_id.get(edge["source"], {})
        tgt = by_id.get(edge["target"], {})
        if "go" in str(src.get("label", "")):
            assert str(tgt.get("source_file", "")).endswith("own.py"), (
                f"self-call escaped its own class hierarchy: {tgt}"
            )


def test_an_untyped_receiver_is_still_not_resolved(tmp_path: Path):
    """The policy this fix must NOT weaken: `obj.method()` on an unannotated parameter stays
    unresolved (Decision_PythonUntypedReceiverEmitsNoEdge). Only `self`/`cls` are exact."""
    svc = _write(tmp_path / "svc.py", "class Service:\n    def run(self):\n        return 1\n")
    worker = _write(
        tmp_path / "worker.py",
        "class Worker:\n    def go(self, obj):\n        return obj.run()\n",
    )

    result = extract([worker, svc], cache_root=tmp_path / "cache")

    assert (".go()", ".run()") not in _calls(result)


def test_self_call_to_a_same_file_method_still_resolves(tmp_path: Path):
    """Regression guard for the pre-existing in-file path."""
    src = _write(
        tmp_path / "svc.py",
        "class Svc:\n"
        "    def helper(self):\n        return 1\n\n"
        "    def go(self):\n        return self.helper()\n",
    )

    result = extract([src], cache_root=tmp_path / "cache")

    assert (".go()", ".helper()") in _calls(result)
