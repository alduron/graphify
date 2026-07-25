"""Module-qualified Python call resolution (`mod.func()` after `from pkg import mod`).

This is the dominant Python call style, and before the python_module_calls resolver it
produced NO call edge at all: the in-file pass cannot see a callee defined in another
file, and the class-receiver resolver ignores a lowercase module receiver. Every case
here is modelled on a real call site that was missing from the graph.
"""

from __future__ import annotations

from pathlib import Path

from graphify.extract import extract


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _node_id(result: dict, label: str, source_file: str) -> str:
    """Labels are decorated: a function is `func()`, a method `.method()`, a class bare."""
    matches = [
        node["id"]
        for node in result["nodes"]
        if node.get("label") in (label, f"{label}()", f".{label}()")
        and node.get("source_file") == source_file
    ]
    assert len(matches) == 1, f"{label} in {source_file}: {matches}"
    return matches[0]


def _calls(result: dict, source: str, target: str) -> bool:
    return any(
        e["source"] == source and e["target"] == target and e["relation"] == "calls"
        for e in result["edges"]
    )


def test_from_package_import_module_then_qualified_call_resolves(tmp_path: Path):
    """`from pkg import mod` + `mod.func()` - the exact prompt.py -> pushv2.flag_nodes shape."""
    _write(tmp_path / "pkg/__init__.py", "")
    callee = _write(tmp_path / "pkg/pushv2.py", "def flag_nodes(flags, session_id):\n    return []\n")
    caller = _write(
        tmp_path / "pkg/prompt.py",
        "from pkg import pushv2\n\n"
        "def inject(flags, session_id):\n"
        "    return pushv2.flag_nodes(flags, session_id)\n",
    )

    result = extract([callee, caller], cache_root=tmp_path)

    assert _calls(
        result,
        _node_id(result, "inject", "pkg/prompt.py"),
        _node_id(result, "flag_nodes", "pkg/pushv2.py"),
    )


def test_qualified_call_does_not_bind_to_a_same_named_local_def(tmp_path: Path):
    """The receiver names a module, so the callee is that module's function - never the
    same-named local one. Binding locally was the silent false-positive risk."""
    _write(tmp_path / "pkg/__init__.py", "")
    real = _write(tmp_path / "pkg/helper.py", "def render(x):\n    return x\n")
    caller = _write(
        tmp_path / "pkg/app.py",
        "from pkg import helper\n\n"
        "def render(x):\n"
        "    return x\n\n"
        "def go(x):\n"
        "    return helper.render(x)\n",
    )

    result = extract([real, caller], cache_root=tmp_path)

    go = _node_id(result, "go", "pkg/app.py")
    assert _calls(result, go, _node_id(result, "render", "pkg/helper.py"))
    assert not _calls(result, go, _node_id(result, "render", "pkg/app.py"))


def test_aliased_module_import_resolves(tmp_path: Path):
    """`import pkg.mod as m` + `m.func()`."""
    _write(tmp_path / "pkg/__init__.py", "")
    callee = _write(tmp_path / "pkg/deep.py", "def compute():\n    return 2\n")
    caller = _write(
        tmp_path / "app.py",
        "import pkg.deep as d\n\ndef run():\n    return d.compute()\n",
    )

    result = extract([callee, caller], cache_root=tmp_path)

    assert _calls(
        result,
        _node_id(result, "run", "app.py"),
        _node_id(result, "compute", "pkg/deep.py"),
    )


def test_relative_module_import_resolves(tmp_path: Path):
    """`from . import mod` + `mod.func()` - relative imports resolve against the file's dir."""
    _write(tmp_path / "pkg/__init__.py", "")
    callee = _write(tmp_path / "pkg/util.py", "def span_label(a, b):\n    return f'{a}{b}'\n")
    caller = _write(
        tmp_path / "pkg/guard.py",
        "from . import util\n\ndef label(a, b):\n    return util.span_label(a, b)\n",
    )

    result = extract([callee, caller], cache_root=tmp_path)

    assert _calls(
        result,
        _node_id(result, "label", "pkg/guard.py"),
        _node_id(result, "span_label", "pkg/util.py"),
    )


def test_qualified_call_never_reaches_a_class_method(tmp_path: Path):
    """`mod.name()` addresses a module-level def; a class's same-named method is not a
    candidate, so an ambiguous name must not produce a bogus edge."""
    _write(tmp_path / "pkg/__init__.py", "")
    callee = _write(
        tmp_path / "pkg/svc.py",
        "class Worker:\n    def handle(self):\n        return 1\n",
    )
    caller = _write(
        tmp_path / "pkg/app.py",
        "from pkg import svc\n\ndef go():\n    return svc.handle()\n",
    )

    result = extract([callee, caller], cache_root=tmp_path)

    assert not _calls(
        result,
        _node_id(result, "go", "pkg/app.py"),
        _node_id(result, "handle", "pkg/svc.py"),
    )


def test_symbol_import_bare_call_still_resolves(tmp_path: Path):
    """The pre-existing bare-name path (`from pkg.mod import func` + `func()`) is untouched."""
    _write(tmp_path / "pkg/__init__.py", "")
    callee = _write(tmp_path / "pkg/mod.py", "def span_label(a):\n    return a\n")
    caller = _write(
        tmp_path / "app.py",
        "from pkg.mod import span_label\n\ndef run(a):\n    return span_label(a)\n",
    )

    result = extract([callee, caller], cache_root=tmp_path)

    assert _calls(
        result,
        _node_id(result, "run", "app.py"),
        _node_id(result, "span_label", "pkg/mod.py"),
    )
