"""Type-aware JS/TS member-call resolution (structural mirror of the Python
member-call resolver in test_extract.py). Covers:

  * a local var typed by `new Thing()` (JS and TS)
  * `this.<field>` typed by a constructor `new`/param assignment
  * `this.<field>` typed by a TS field annotation
  * `this.<field>` typed by a TS constructor parameter property
  * a typed function parameter / typed local var annotation (TS)
  * a qualified static `Helper.method()` call (capitalized receiver)

Every resolved edge must be INFERRED (0.8) except the qualified-static path,
which is EXTRACTED (1.0, an explicit unambiguous class reference) -- same
contract as the Python resolver. Negative tests guard the same god-node
guards: an untyped receiver, an ambiguous (2-definition) class, and a
reassigned/ambiguous local var must never resolve.
"""

from pathlib import Path

from graphify.extract import extract


def test_js_local_var_member_call_resolves_inferred(tmp_path):
    """`const r = new Repo(); r.save();` within one function body resolves
    cross-file to Repo.save with an INFERRED `calls` edge."""
    repo_mod = tmp_path / "repo_mod.js"
    builder = tmp_path / "builder.js"
    repo_mod.write_text(
        "class Repo {\n"
        "  save() { return 1; }\n"
        "}\n"
        "module.exports = Repo;\n"
    )
    builder.write_text(
        "const Repo = require('./repo_mod');\n\n"
        "class Builder {\n"
        "  build() {\n"
        "    const repo = new Repo();\n"
        "    repo.save();\n"
        "  }\n"
        "}\n"
    )
    result = extract([builder, repo_mod], cache_root=tmp_path)
    nodes = {n["id"]: n for n in result["nodes"]}
    edges = [
        e for e in result["edges"]
        if e["relation"] == "calls"
        and "build" in nodes[e["source"]]["label"]
        and "save" in nodes[e["target"]]["label"]
        and "repo_mod.js" in (nodes[e["target"]].get("source_file") or "")
    ]
    assert len(edges) == 1, f"expected build->save edge, got {edges}"
    assert edges[0]["confidence"] == "INFERRED"


def test_ts_ctor_new_field_member_call_resolves_inferred(tmp_path):
    """`constructor(){ this.repo = new Repo(); }` then `this.repo.save()` in
    another method resolves cross-file to Repo.save (INFERRED)."""
    repo_mod = tmp_path / "repo_mod.ts"
    service = tmp_path / "service.ts"
    repo_mod.write_text(
        "export class Repo {\n"
        "  save() { return 1; }\n"
        "}\n"
    )
    service.write_text(
        "import { Repo } from './repo_mod';\n\n"
        "class Service {\n"
        "  constructor() {\n"
        "    this.repo = new Repo();\n"
        "  }\n"
        "  run() {\n"
        "    this.repo.save();\n"
        "  }\n"
        "}\n"
    )
    result = extract([service, repo_mod], cache_root=tmp_path)
    nodes = {n["id"]: n for n in result["nodes"]}
    edges = [
        e for e in result["edges"]
        if e["relation"] == "calls"
        and "run" in nodes[e["source"]]["label"]
        and "save" in nodes[e["target"]]["label"]
        and "repo_mod.ts" in (nodes[e["target"]].get("source_file") or "")
    ]
    assert len(edges) == 1, f"expected run->save edge, got {edges}"
    assert edges[0]["confidence"] == "INFERRED"


def test_ts_field_annotation_member_call_resolves_inferred(tmp_path):
    """A TS class field annotation (`private repo: Repo;`) types `this.repo`
    for a later `this.repo.save()` cross-file call (INFERRED)."""
    repo_mod = tmp_path / "repo_mod.ts"
    service = tmp_path / "service.ts"
    repo_mod.write_text(
        "export class Repo {\n"
        "  save() { return 1; }\n"
        "}\n"
    )
    service.write_text(
        "import { Repo } from './repo_mod';\n\n"
        "class Service {\n"
        "  private repo: Repo;\n"
        "  run() {\n"
        "    this.repo.save();\n"
        "  }\n"
        "}\n"
    )
    result = extract([service, repo_mod], cache_root=tmp_path)
    nodes = {n["id"]: n for n in result["nodes"]}
    edges = [
        e for e in result["edges"]
        if e["relation"] == "calls"
        and "run" in nodes[e["source"]]["label"]
        and "save" in nodes[e["target"]]["label"]
        and "repo_mod.ts" in (nodes[e["target"]].get("source_file") or "")
    ]
    assert len(edges) == 1, f"expected run->save edge, got {edges}"
    assert edges[0]["confidence"] == "INFERRED"


def test_ts_ctor_parameter_property_member_call_resolves_inferred(tmp_path):
    """A TS constructor parameter property (`constructor(private repo: Repo)`)
    both declares and assigns `this.repo`; a later `this.repo.save()` resolves
    cross-file (INFERRED)."""
    repo_mod = tmp_path / "repo_mod.ts"
    service = tmp_path / "service.ts"
    repo_mod.write_text(
        "export class Repo {\n"
        "  save() { return 1; }\n"
        "}\n"
    )
    service.write_text(
        "import { Repo } from './repo_mod';\n\n"
        "class Service {\n"
        "  constructor(private repo: Repo) {}\n"
        "  run() {\n"
        "    this.repo.save();\n"
        "  }\n"
        "}\n"
    )
    result = extract([service, repo_mod], cache_root=tmp_path)
    nodes = {n["id"]: n for n in result["nodes"]}
    edges = [
        e for e in result["edges"]
        if e["relation"] == "calls"
        and "run" in nodes[e["source"]]["label"]
        and "save" in nodes[e["target"]]["label"]
        and "repo_mod.ts" in (nodes[e["target"]].get("source_file") or "")
    ]
    assert len(edges) == 1, f"expected run->save edge, got {edges}"
    assert edges[0]["confidence"] == "INFERRED"


def test_ts_typed_param_member_call_resolves_inferred(tmp_path):
    """A typed parameter (`function f(repo: Repo)`) types `repo` for a
    `repo.save()` call in the same function body, cross-file (INFERRED)."""
    repo_mod = tmp_path / "repo_mod.ts"
    service = tmp_path / "service.ts"
    repo_mod.write_text(
        "export class Repo {\n"
        "  save() { return 1; }\n"
        "}\n"
    )
    service.write_text(
        "import { Repo } from './repo_mod';\n\n"
        "function run(repo: Repo) {\n"
        "  repo.save();\n"
        "}\n"
    )
    result = extract([service, repo_mod], cache_root=tmp_path)
    nodes = {n["id"]: n for n in result["nodes"]}
    edges = [
        e for e in result["edges"]
        if e["relation"] == "calls"
        and "run" in nodes[e["source"]]["label"]
        and "save" in nodes[e["target"]]["label"]
        and "repo_mod.ts" in (nodes[e["target"]].get("source_file") or "")
    ]
    assert len(edges) == 1, f"expected run->save edge, got {edges}"
    assert edges[0]["confidence"] == "INFERRED"


def test_ts_typed_local_var_annotation_member_call_resolves_inferred(tmp_path):
    """A bare TS local annotation (`let r: Repo;`) followed by an assignment
    types `r` for a later `r.save()` call, cross-file (INFERRED)."""
    repo_mod = tmp_path / "repo_mod.ts"
    service = tmp_path / "service.ts"
    repo_mod.write_text(
        "export class Repo {\n"
        "  save() { return 1; }\n"
        "}\n"
    )
    service.write_text(
        "import { Repo } from './repo_mod';\n\n"
        "function run() {\n"
        "  let r: Repo;\n"
        "  r = new Repo();\n"
        "  r.save();\n"
        "}\n"
    )
    result = extract([service, repo_mod], cache_root=tmp_path)
    nodes = {n["id"]: n for n in result["nodes"]}
    edges = [
        e for e in result["edges"]
        if e["relation"] == "calls"
        and "run" in nodes[e["source"]]["label"]
        and "save" in nodes[e["target"]]["label"]
        and "repo_mod.ts" in (nodes[e["target"]].get("source_file") or "")
    ]
    assert len(edges) == 1, f"expected run->save edge, got {edges}"
    assert edges[0]["confidence"] == "INFERRED"


def test_js_qualified_static_method_call_resolves_extracted(tmp_path):
    """`Helper.format()` across files resolves to the class-qualified method
    node with an EXTRACTED `calls` edge (cheap capitalized-receiver win)."""
    helper = tmp_path / "helper.js"
    caller = tmp_path / "caller.js"
    helper.write_text(
        "class Helper {\n"
        "  static format(x) { return x; }\n"
        "}\n"
        "module.exports = Helper;\n"
    )
    caller.write_text(
        "const Helper = require('./helper');\n\n"
        "function run() {\n"
        "  return Helper.format(1);\n"
        "}\n"
    )
    result = extract([caller, helper], cache_root=tmp_path)
    nodes = {n["id"]: n for n in result["nodes"]}
    edges = [
        e for e in result["edges"]
        if e["relation"] == "calls"
        and "run" in nodes[e["source"]]["label"]
        and "format" in nodes[e["target"]]["label"]
        and "helper.js" in (nodes[e["target"]].get("source_file") or "")
    ]
    assert len(edges) == 1, f"expected run->format edge, got {edges}"
    assert edges[0]["confidence"] == "EXTRACTED"


# ── negative / god-node guards ────────────────────────────────────────────────


def test_js_instance_member_call_not_overconnected(tmp_path):
    """A lowercase-receiver member call on an untyped param (`obj.run()`)
    must NOT be resolved cross-file -- the god-node guard stays intact."""
    svc = tmp_path / "svc.js"
    worker = tmp_path / "worker.js"
    svc.write_text(
        "class Service {\n"
        "  run() { return 1; }\n"
        "}\n"
    )
    worker.write_text(
        "class Worker {\n"
        "  go(obj) {\n"
        "    return obj.run();\n"
        "  }\n"
        "}\n"
    )
    result = extract([worker, svc], cache_root=tmp_path)
    nodes = {n["id"]: n for n in result["nodes"]}
    bad = [
        e for e in result["edges"]
        if e["relation"] == "calls"
        and "go" in nodes[e["source"]]["label"]
        and "run" in nodes[e["target"]]["label"]
    ]
    assert bad == [], f"instance member call must not connect cross-file: {bad}"


def test_js_local_var_ambiguous_class_bails(tmp_path):
    """`const repo = new Repo(); repo.save();` must not resolve when Repo is
    defined in 2+ files -- single-definition god-node guard."""
    a = tmp_path / "a.js"
    b = tmp_path / "b.js"
    builder = tmp_path / "builder.js"
    a.write_text("class Repo {\n  save() { return 1; }\n}\n")
    b.write_text("class Repo {\n  save() { return 2; }\n}\n")
    builder.write_text(
        "class Builder {\n"
        "  build() {\n"
        "    const repo = new Repo();\n"
        "    repo.save();\n"
        "  }\n"
        "}\n"
    )
    result = extract([builder, a, b], cache_root=tmp_path)
    nodes = {n["id"]: n for n in result["nodes"]}
    resolved = [
        e for e in result["edges"]
        if e["relation"] == "calls"
        and "build" in nodes[e["source"]]["label"]
        and "save" in nodes[e["target"]]["label"]
    ]
    assert resolved == [], f"ambiguous class name must not resolve: {resolved}"


def test_js_self_field_ambiguous_class_bails(tmp_path):
    """`this.repo.save()` must not resolve when the field's type name is
    defined in 2+ files -- the same single-definition god-node guard applies
    to this-field resolution."""
    a = tmp_path / "a.js"
    b = tmp_path / "b.js"
    service = tmp_path / "service.js"
    a.write_text("class Repo {\n  save() { return 1; }\n}\n")
    b.write_text("class Repo {\n  save() { return 2; }\n}\n")
    service.write_text(
        "class Service {\n"
        "  constructor() {\n"
        "    this.repo = new Repo();\n"
        "  }\n"
        "  run() {\n"
        "    this.repo.save();\n"
        "  }\n"
        "}\n"
    )
    result = extract([service, a, b], cache_root=tmp_path)
    nodes = {n["id"]: n for n in result["nodes"]}
    resolved = [
        e for e in result["edges"]
        if e["relation"] == "calls"
        and "run" in nodes[e["source"]]["label"]
        and "save" in nodes[e["target"]]["label"]
    ]
    assert resolved == [], f"ambiguous field type must not resolve: {resolved}"


def test_js_reassigned_local_var_member_call_bails(tmp_path):
    """A local var reassigned to a different constructed type must not
    resolve -- the 100%-confidence contract poisons an ambiguous binding to
    None."""
    repo_mod = tmp_path / "repo_mod.js"
    builder = tmp_path / "builder.js"
    repo_mod.write_text(
        "class Repo {\n"
        "  save() { return 1; }\n"
        "}\n"
        "class Other {\n"
        "  save() { return 2; }\n"
        "}\n"
        "module.exports = { Repo, Other };\n"
    )
    builder.write_text(
        "const { Repo, Other } = require('./repo_mod');\n\n"
        "class Builder {\n"
        "  build() {\n"
        "    let repo = new Repo();\n"
        "    repo = new Other();\n"
        "    repo.save();\n"
        "  }\n"
        "}\n"
    )
    result = extract([builder, repo_mod], cache_root=tmp_path)
    nodes = {n["id"]: n for n in result["nodes"]}
    resolved = [
        e for e in result["edges"]
        if e["relation"] == "calls"
        and "build" in nodes[e["source"]]["label"]
        and "save" in nodes[e["target"]]["label"]
    ]
    assert resolved == [], f"reassigned local var must not resolve: {resolved}"


# ── JS/TS base-class (extends) member-call resolution ────────────────────────

def test_ts_inherited_method_member_call_resolves_inferred(tmp_path):
    """`class Repo extends Base {}` inherits `save`; a local-var call
    `repo.save()` resolves cross-file to Base.save (INFERRED).

    Uses .ts (not .js): the plain JS grammar's `class_heritage` holds the
    `extends`/name pair directly, while TS wraps them in `extends_clause` --
    the extraction pass this resolver builds on only walks the TS shape
    (extract.py `_ts_walk_class_members`), so a bare `.js` `extends` is a
    separate, pre-existing gap outside this resolver's scope.
    """
    repo_mod = tmp_path / "repo_mod.ts"
    builder = tmp_path / "builder.ts"
    repo_mod.write_text(
        "export class Base {\n"
        "  save() { return 1; }\n"
        "}\n"
        "export class Repo extends Base {\n"
        "}\n"
    )
    builder.write_text(
        "import { Repo } from './repo_mod';\n\n"
        "class Builder {\n"
        "  build() {\n"
        "    const repo = new Repo();\n"
        "    repo.save();\n"
        "  }\n"
        "}\n"
    )
    result = extract([builder, repo_mod], cache_root=tmp_path)
    nodes = {n["id"]: n for n in result["nodes"]}
    edges = [
        e for e in result["edges"]
        if e["relation"] == "calls"
        and "build" in nodes[e["source"]]["label"]
        and "save" in nodes[e["target"]]["label"]
        and "repo_mod.ts" in (nodes[e["target"]].get("source_file") or "")
    ]
    assert len(edges) == 1, f"expected build->save edge (inherited method), got {edges}"
    assert edges[0]["confidence"] == "INFERRED"


def test_ts_inherited_field_member_call_resolves_inferred(tmp_path):
    """A field typed in a parent constructor (`this.svc = new Svc();` in
    Base) is inherited by `class C extends Base`; `this.svc.run()` in C
    resolves cross-file to Svc.run."""
    svc_mod = tmp_path / "svc_mod.ts"
    base_mod = tmp_path / "base_mod.ts"
    sub_mod = tmp_path / "sub_mod.ts"
    svc_mod.write_text(
        "export class Svc {\n"
        "  run() { return 1; }\n"
        "}\n"
    )
    base_mod.write_text(
        "import { Svc } from './svc_mod';\n\n"
        "export class Base {\n"
        "  constructor() {\n"
        "    this.svc = new Svc();\n"
        "  }\n"
        "}\n"
    )
    sub_mod.write_text(
        "import { Base } from './base_mod';\n\n"
        "class C extends Base {\n"
        "  go() {\n"
        "    this.svc.run();\n"
        "  }\n"
        "}\n"
    )
    result = extract([sub_mod, base_mod, svc_mod], cache_root=tmp_path)
    nodes = {n["id"]: n for n in result["nodes"]}
    edges = [
        e for e in result["edges"]
        if e["relation"] == "calls"
        and "go" in nodes[e["source"]]["label"]
        and "run" in nodes[e["target"]]["label"]
        and "svc_mod.ts" in (nodes[e["target"]].get("source_file") or "")
    ]
    assert len(edges) == 1, f"expected go->run edge (inherited field), got {edges}"
    assert edges[0]["confidence"] == "INFERRED"


def test_ts_cross_file_inherited_method_resolves_inferred(tmp_path):
    """Base lives in a different file than the subclass; the inherited
    method still resolves cross-file (INFERRED)."""
    base_mod = tmp_path / "base_mod.ts"
    repo_mod = tmp_path / "repo_mod.ts"
    builder = tmp_path / "builder.ts"
    base_mod.write_text(
        "export class Base {\n"
        "  save() { return 1; }\n"
        "}\n"
    )
    repo_mod.write_text(
        "import { Base } from './base_mod';\n\n"
        "export class Repo extends Base {\n"
        "}\n"
    )
    builder.write_text(
        "import { Repo } from './repo_mod';\n\n"
        "class Builder {\n"
        "  build() {\n"
        "    const repo = new Repo();\n"
        "    repo.save();\n"
        "  }\n"
        "}\n"
    )
    result = extract([builder, repo_mod, base_mod], cache_root=tmp_path)
    nodes = {n["id"]: n for n in result["nodes"]}
    edges = [
        e for e in result["edges"]
        if e["relation"] == "calls"
        and "build" in nodes[e["source"]]["label"]
        and "save" in nodes[e["target"]]["label"]
        and "base_mod.ts" in (nodes[e["target"]].get("source_file") or "")
    ]
    assert len(edges) == 1, f"expected build->save edge (cross-file base), got {edges}"
    assert edges[0]["confidence"] == "INFERRED"


def test_ts_inheritance_cycle_guard_no_hang(tmp_path):
    """Malformed cyclic inheritance (`A extends B` / `B extends A`) must not
    hang base-class resolution and must not emit a bogus edge for an
    undefined method."""
    mod = tmp_path / "cyclic.ts"
    mod.write_text(
        "class A extends B {\n"
        "  aMethod() {\n"
        "    const obj = new A();\n"
        "    obj.missing();\n"
        "  }\n"
        "}\n"
        "class B extends A {\n"
        "  bMethod() { return 1; }\n"
        "}\n"
    )
    result = extract([mod], cache_root=tmp_path)
    nodes = {n["id"]: n for n in result["nodes"]}
    bad = [
        e for e in result["edges"]
        if e["relation"] == "calls"
        and "aMethod" in nodes[e["source"]]["label"]
        and "missing" in nodes.get(e["target"], {}).get("label", "")
    ]
    assert bad == [], f"undefined method must not resolve even with a cycle: {bad}"


def test_js_plain_extends_emits_inherits_edge(tmp_path):
    """Plain `.js` `class Sub extends Base` emits an inherits edge (no TS
    _SymbolResolutionFacts pass runs for vanilla JS, so it is emitted in the
    generic class walk instead)."""
    mod = tmp_path / "m.js"
    mod.write_text(
        "class Base {\n"
        "  save() { return 1; }\n"
        "}\n"
        "class Sub extends Base {}\n"
    )
    result = extract([mod], cache_root=tmp_path)
    nodes = {n["id"]: n.get("label", "") for n in result["nodes"]}
    inh = [
        e for e in result["edges"]
        if e["relation"] == "inherits"
        and "Sub" in nodes.get(e["source"], "")
        and "Base" in nodes.get(e["target"], "")
    ]
    assert len(inh) == 1, f"expected one Sub->Base inherits edge, got {inh}"


def test_js_plain_inherited_method_member_call_resolves_inferred(tmp_path):
    """Plain `.js`: `this.repo.save()` where `repo` is a `Repo extends Base`
    and `save` lives on `Base` resolves to Base.save via base-class walking."""
    mod = tmp_path / "m.js"
    mod.write_text(
        "class Base {\n"
        "  save() { return 1; }\n"
        "}\n"
        "class Repo extends Base {}\n"
        "class Service {\n"
        "  constructor() { this.repo = new Repo(); }\n"
        "  go() { this.repo.save(); }\n"
        "}\n"
    )
    result = extract([mod], cache_root=tmp_path)
    nodes = {n["id"]: n.get("label", "") for n in result["nodes"]}
    edges = [
        e for e in result["edges"]
        if e["relation"] == "calls"
        and "go" in nodes.get(e["source"], "")
        and nodes.get(e["target"], "").strip("()").lstrip(".") == "save"
    ]
    assert len(edges) == 1, f"expected go->Base.save edge, got {edges}"
    assert edges[0]["confidence"] == "INFERRED"
