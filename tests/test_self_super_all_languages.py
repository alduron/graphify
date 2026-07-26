"""`this.m()` / `super.m()` must resolve in EVERY language, not just Python.

Both shapes are exact in any OO language - the receiver names the caller's own class, written in
source. Only the spelling differs (self/this/$this, super/base/parent), so this is a LanguageConfig
lookup and ONE shared resolver, not one implementation per language.

Before this, only Python resolved them. In Java, C#, Kotlin, Scala, PHP, Groovy, JS/TS, C++ and Lua
an inherited or cross-file `this.helper()` produced NO edge at all.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from graphify.extract import extract


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _has_call_into(result: dict, caller_frag: str, target_file_suffix: str) -> bool:
    by_id = {n["id"]: n for n in result["nodes"]}
    for e in result["edges"]:
        if e["relation"] != "calls":
            continue
        src = by_id.get(e["source"], {})
        tgt = by_id.get(e["target"], {})
        if caller_frag in str(src.get("label", "")) and str(
            tgt.get("source_file", "")
        ).replace("\\", "/").endswith(target_file_suffix):
            return True
    return False


# (label, base filename, base source, child filename, child source)
CASES = [
    (
        "java",
        "Base.java", "class Base {\n  int helper() { return 1; }\n}\n",
        "Child.java", "class Child extends Base {\n  int go() { return this.helper(); }\n}\n",
    ),
    (
        "csharp",
        "Base.cs", "class Base {\n  public int Helper() { return 1; }\n}\n",
        "Child.cs", "class Child : Base {\n  public int Go() { return this.Helper(); }\n}\n",
    ),
    (
        "kotlin",
        "Base.kt", "open class Base {\n    fun helper(): Int { return 1 }\n}\n",
        "Child.kt", "class Child : Base() {\n    fun go(): Int { return this.helper() }\n}\n",
    ),
    (
        "php",
        "Base.php", "<?php\nclass Base {\n  function helper() { return 1; }\n}\n",
        "Child.php", "<?php\nclass Child extends Base {\n  function go() { return $this->helper(); }\n}\n",
    ),
    (
        "typescript",
        "base.ts", "export class Base {\n  helper(): number { return 1; }\n}\n",
        "child.ts", "import { Base } from './base';\nexport class Child extends Base {\n  go(): number { return this.helper(); }\n}\n",
    ),
]


@pytest.mark.parametrize("lang,bf,bs,cf,cs", CASES, ids=[c[0] for c in CASES])
def test_this_call_reaches_an_inherited_method_in_another_file(
    tmp_path: Path, lang: str, bf: str, bs: str, cf: str, cs: str
):
    base = _write(tmp_path / bf, bs)
    child = _write(tmp_path / cf, cs)

    result = extract([base, child], cache_root=tmp_path / f"cache-{lang}")

    assert _has_call_into(result, "go", bf) or _has_call_into(result, "Go", bf), (
        f"{lang}: this.helper() did not reach the inherited method in {bf}"
    )


def test_super_call_skips_the_callers_own_class(tmp_path: Path):
    """`super` must start at the BASES - resolving to the own class finds the very method making
    the call, which inverts what super means and yields a self-edge."""
    src = _write(
        tmp_path / "Solo.java",
        "class Base {\n  int run() { return 1; }\n}\n"
        "class Child extends Base {\n  int run() { return super.run(); }\n}\n",
    )

    result = extract([src], cache_root=tmp_path / "cache")

    for edge in result["edges"]:
        if edge["relation"] == "calls":
            assert edge["source"] != edge["target"], "super resolved to the caller itself"


def test_python_still_works_after_generalization(tmp_path: Path):
    """The language-specific path this replaces must not regress."""
    base = _write(tmp_path / "base.py", "class Base:\n    def helper(self):\n        return 1\n")
    child = _write(
        tmp_path / "child.py",
        "from base import Base\n\nclass Child(Base):\n    def go(self):\n        return self.helper()\n",
    )

    result = extract([base, child], cache_root=tmp_path / "cache")

    assert _has_call_into(result, "go", "base.py")


def test_groovy_script_level_defs_produce_symbols_and_calls(tmp_path: Path):
    """A Groovy SCRIPT (top-level `def`, no class) used to extract ZERO code symbols - only the
    file node - so .gradle build logic was entirely invisible: nothing to anchor to, search or
    ranged-read. Worse than a missing edge."""
    src = _write(
        tmp_path / "build.groovy",
        "def helper() { return 1 }\ndef go() { return helper() }\n",
    )

    result = extract([src], cache_root=tmp_path / "cache")

    labels = {str(n.get("label")) for n in result["nodes"] if n.get("file_type") == "code"}
    assert "helper()" in labels, f"script-level def not extracted: {sorted(labels)}"
    assert "go()" in labels
    assert any(e["relation"] == "calls" for e in result["edges"]), "no call edge in a script"


def test_ruby_paren_call_and_self_call_resolve(tmp_path: Path):
    """Ruby resolves an explicit-paren call and a self-call. A PAREN-LESS call stays unresolved by
    design (the grammar cannot tell `helper` from a local variable read) -
    Caveat_RubyParenlessCallsAreNotExtracted."""
    src = _write(
        tmp_path / "a.rb",
        "class A\n  def helper\n    1\n  end\n  def go\n    self.helper\n  end\nend\n",
    )

    result = extract([src], cache_root=tmp_path / "cache")

    assert any(e["relation"] == "calls" for e in result["edges"])
