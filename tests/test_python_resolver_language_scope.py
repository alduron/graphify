"""A Python resolver must not resolve against symbols defined in another language.

run_language_resolvers gates only ACTIVATION - a resolver runs when the corpus contains any file
with its suffix - and then hands it the ENTIRE node/edge set, every language included. So any
NAME-KEYED index a language resolver builds has to be restricted to that language's own files, or
a Python `Foo.method()` can silently bind to a TypeScript class named Foo.

Mirrors Caveat_GraphifyReceiverCaptureIsSharedGateItPerLang for the resolution half.
"""

from __future__ import annotations

from pathlib import Path

from graphify.extract import extract


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _calls(result: dict) -> list[tuple[str, str]]:
    by_id = {n["id"]: n for n in result["nodes"]}
    return [
        (by_id.get(e["source"], {}).get("label", "?"), by_id.get(e["target"], {}).get("label", "?"))
        for e in result["edges"]
        if e["relation"] == "calls"
    ]


def test_python_qualified_call_does_not_bind_to_a_same_named_ts_class(tmp_path: Path):
    """`Widget` exists in BOTH languages; the Python call must reach the Python one only."""
    py_def = _write(
        tmp_path / "widget.py",
        "class Widget:\n    def render(self):\n        return 'py'\n",
    )
    ts_def = _write(
        tmp_path / "widget.ts",
        "export class Widget {\n  render() { return 'ts'; }\n}\n",
    )
    py_caller = _write(
        tmp_path / "app.py",
        "from widget import Widget\n\ndef go():\n    return Widget.render()\n",
    )

    result = extract([py_def, ts_def, py_caller], cache_root=tmp_path / "cache")

    by_id = {n["id"]: n for n in result["nodes"]}
    for edge in result["edges"]:
        if edge["relation"] != "calls":
            continue
        src = by_id.get(edge["source"], {})
        tgt = by_id.get(edge["target"], {})
        if "go" in str(src.get("label", "")):
            assert str(tgt.get("source_file", "")).endswith(".py"), (
                f"python call resolved into another language: {tgt}"
            )


def test_a_js_call_does_not_bind_to_a_same_named_python_class(tmp_path: Path):
    """The mirror case. Scoping only the INDEX is not enough - every resolver also iterates the
    WHOLE corpus's raw calls, so the JS pass would otherwise process a Python call site (and vice
    versa). Both halves are needed: the calls a pass reads and the definitions it resolves against."""
    py_def = _write(
        tmp_path / "widget.py",
        "class Widget:\n    def render(self):\n        return 'py'\n",
    )
    ts_def = _write(
        tmp_path / "widget.ts",
        "export class Widget {\n  render() { return 'ts'; }\n}\n",
    )
    ts_caller = _write(
        tmp_path / "app.ts",
        "import { Widget } from './widget';\nexport function go() { return Widget.render(); }\n",
    )

    result = extract([py_def, ts_def, ts_caller], cache_root=tmp_path / "cache")

    by_id = {n["id"]: n for n in result["nodes"]}
    for edge in result["edges"]:
        if edge["relation"] != "calls":
            continue
        src = by_id.get(edge["source"], {})
        tgt = by_id.get(edge["target"], {})
        if "go" in str(src.get("label", "")) and str(src.get("source_file", "")).endswith(".ts"):
            assert not str(tgt.get("source_file", "")).endswith(".py"), (
                f"js call resolved into python: {tgt}"
            )


def test_a_python_only_class_name_still_resolves(tmp_path: Path):
    """Scoping must not break the normal case it is protecting."""
    py_def = _write(
        tmp_path / "svc.py",
        "class Service:\n    def run(self):\n        return 1\n",
    )
    py_caller = _write(
        tmp_path / "app.py",
        "from svc import Service\n\ndef go():\n    return Service.run()\n",
    )

    result = extract([py_def, py_caller], cache_root=tmp_path / "cache")

    assert any(
        "go" in src and "run" in tgt for src, tgt in _calls(result)
    ), "the in-language qualified call must still resolve"
