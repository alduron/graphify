"""Registered-name literal index (Plan 08 Phase 5, the extraction horizon).

A per-file index of REGISTERED NAME LITERALS: strings that act as cross-boundary
join points between code and artifacts graphify cannot open (Blueprints, data
tables, ini config). A gameplay tag fired in C++ and consumed by two
Blueprint-only assets couples through the *string* "Ability.Burn", not through a
symbol edge. Indexing the literal lets a touch of any file that names it trigger
just-in-time injection of knowledge anchored to that tag.

Output is NAMES ONLY. No source content leaves in the payload - each record is
{file, name, kind, line} where kind is one of:

    gameplay_tag  Unreal gameplay tag (tag macros, RequestGameplayTag, ini)
    event         delegate / event name (dynamic delegate decl, literal binding)
    config_key    dotted tag-like literal seen in MORE THAN ONE file (join point)
    name          dotted tag-like literal seen in exactly one file (weak signal)

Precision over recall: a literal is only indexed when it either sits in a
recognised registration context (a tag macro, a delegate declaration, a binding
call) or matches the strict dotted tag-shape pattern. Arbitrary strings, file
paths, version numbers and comment prose are never indexed.

Public API
----------
extract_file_literals(path, text=None) -> list[dict]
    Per-file literals with contextual kinds already assigned (gameplay_tag /
    event) plus raw dotted candidates tagged "name". No cross-file promotion.

build_literal_index(files, root=None) -> list[dict]
    Aggregate across files: stamp the repo-relative ``file``, promote dotted
    "name" candidates seen in >1 file to "config_key", drop within-file
    duplicates (most specific kind wins), and return a deterministically
    ordered list.
"""
from __future__ import annotations

import re
from pathlib import Path

# ---------------------------------------------------------------------------
# Extensions we treat as C++ (Unreal gameplay code) for the context-anchored
# tag / event detectors. Everything else gets the generic dotted-literal scan.
# ---------------------------------------------------------------------------
_CPP_SUFFIXES: frozenset[str] = frozenset(
    {".cpp", ".cc", ".cxx", ".c", ".h", ".hpp", ".hh", ".hxx", ".inl", ".ipp"}
)
_INI_SUFFIXES: frozenset[str] = frozenset({".ini"})

# A gameplay-tag STRING is dotted segments System.Aspect.Specific, depth 1..6.
# A segment is PascalCase (Weapon, FireMode) OR pure-numeric (01, 02) - both
# occur in real Unreal tag registries (mirrors the aetherpulse oracle rule).
_TAG_SEGMENT = re.compile(r"^([A-Z][A-Za-z0-9]*|[0-9]+)$", re.ASCII)
_MAX_TAG_DEPTH = 6

# A generic dotted tag-like literal (the config_key / name candidate). Stricter
# than a tag context allows: the FIRST segment must be PascalCase (starts with a
# capital) so "1.2.3" version strings, "a.b" lowercase paths and "foo.txt" file
# names are rejected. At least two segments (one dot), at most six.
_DOTTED_LITERAL = re.compile(
    r"^[A-Z][A-Za-z0-9]*(?:\.(?:[A-Z][A-Za-z0-9]*|[0-9]+)){1,5}$", re.ASCII
)


def _is_tag_shaped(s: str) -> bool:
    """True when ``s`` looks like a gameplay-tag string (dotted PascalCase /
    numeric segments, depth 1..6). Used to gate context-anchored captures so an
    obviously-wrong RequestGameplayTag argument is dropped."""
    if not s or len(s) > 200:
        return False
    segs = s.split(".")
    if not (1 <= len(segs) <= _MAX_TAG_DEPTH):
        return False
    return all(_TAG_SEGMENT.match(seg) for seg in segs)


# ---------------------------------------------------------------------------
# C++ gameplay-tag detectors (context-anchored, high precision).
# ---------------------------------------------------------------------------

# UE_DEFINE_GAMEPLAY_TAG(TAG_State_Actor_Corroded, "State.Actor.Corroded")
# plus the _STATIC and _COMMENT variants. Capture the dotted STRING (the
# cross-boundary join point), not the TAG_ C++ constant.
_RE_TAG_DEFINE = re.compile(
    r'\bUE_DEFINE_GAMEPLAY_TAG(?:_STATIC|_COMMENT)?\s*\(\s*'
    r'TAG_[A-Za-z0-9_]+\s*,\s*"(?P<tag>[^"]+)"',
    re.ASCII,
)

# Raw FNativeGameplayTag TAG_Foo(UE_PLUGIN_NAME, UE_MODULE_NAME, "Foo.Bar", ...)
# constructor form (used when the macro cannot carry the FOO_API export macro).
_RE_TAG_FNATIVE = re.compile(
    r'\bFNativeGameplayTag\s+TAG_[A-Za-z0-9_]+\s*\([^";]*?"(?P<tag>[^"]+)"',
    re.ASCII,
)

# FGameplayTag::RequestGameplayTag(FName("X.Y")) and the bare / TEXT() forms.
_RE_TAG_REQUEST = re.compile(
    r'\bRequestGameplayTag\s*\(\s*'
    r'(?:FName\s*\(\s*)?(?:TEXT\s*\(\s*)?'
    r'"(?P<tag>[^"]+)"',
    re.ASCII,
)

# ---------------------------------------------------------------------------
# C++ event / delegate detectors.
# ---------------------------------------------------------------------------

# DYNAMIC delegate declarations are the Blueprint-exposed (cross-boundary) ones.
# Variant with no _RetVal: the delegate name is the FIRST identifier.
#   DECLARE_DYNAMIC_MULTICAST_DELEGATE(FOnFoo)
#   DECLARE_DYNAMIC_MULTICAST_DELEGATE_OneParam(FOnFoo, int32, Value)
_RE_DYN_DELEGATE = re.compile(
    r'\bDECLARE_DYNAMIC(?:_MULTICAST)?_DELEGATE'
    r'(?:_(?:One|Two|Three|Four|Five|Six|Seven|Eight|Nine)Params?)?'
    r'\s*\(\s*(?P<name>F[A-Z][A-Za-z0-9_]*)\s*[,)]',
    re.ASCII,
)
# Variant with _RetVal: return type is first, delegate name is SECOND.
#   DECLARE_DYNAMIC_DELEGATE_RetVal_OneParam(bool, FOnBar, int32, Value)
_RE_DYN_DELEGATE_RETVAL = re.compile(
    r'\bDECLARE_DYNAMIC(?:_MULTICAST)?_DELEGATE_RetVal'
    r'(?:_(?:One|Two|Three|Four|Five|Six|Seven|Eight|Nine)Params?)?'
    r'\s*\(\s*[^,()]+,\s*(?P<name>F[A-Z][A-Za-z0-9_]*)\s*[,)]',
    re.ASCII,
)

# Literal-named bindings: the bound handler / action is a string literal that a
# reflection lookup resolves at runtime - a genuine cross-boundary join point.
#   BindUFunction(this, FName("OnMontageEnded"))
#   AddUFunction(obj, FName(TEXT("HandleHit")))
_RE_BIND_UFUNCTION = re.compile(
    r'\b(?:Add|Bind)UFunction\s*\([^;)]*?'
    r'FName\s*\(\s*(?:TEXT\s*\(\s*)?"(?P<name>[A-Za-z_][A-Za-z0-9_]*)"',
    re.ASCII,
)
#   InputComponent->BindAction("Jump", IE_Pressed, ...)  /  BindAxis("MoveForward", ...)
_RE_BIND_INPUT = re.compile(
    r'\bBind(?:Action|Axis)\s*\(\s*(?:TEXT\s*\(\s*)?'
    r'"(?P<name>[A-Za-z_][A-Za-z0-9_]*)"',
    re.ASCII,
)

# ---------------------------------------------------------------------------
# INI gameplay-tag detector.
# ---------------------------------------------------------------------------

# DefaultGameplayTags.ini (and any *.ini) list tags under the GameplayTags
# settings sections:
#   +GameplayTagList=(Tag="Ability.Burn",DevComment="...")
#   +GameplayTagRedirects=(OldTagName="A.B",NewTagName="A.C")
_RE_INI_TAG_LIST = re.compile(r'\bTag\s*=\s*"(?P<tag>[^"]+)"', re.ASCII)
_RE_INI_TAG_REDIRECT = re.compile(
    r'\b(?:Old|New)TagName\s*=\s*"(?P<tag>[^"]+)"', re.ASCII
)
# Section headers that hold gameplay tags. Matched case-insensitively; a plain
# [GameplayTags] section is accepted too.
_RE_INI_SECTION = re.compile(r'^\s*\[(?P<name>[^\]]+)\]\s*$')
_INI_TAG_SECTION_HINTS = ("gameplaytag",)

# Generic quoted string literal (single- or double-quoted) for the dotted scan.
_RE_QUOTED = re.compile(r'"([^"\r\n]{1,200})"' r"|'([^'\r\n]{1,200})'", re.ASCII)


def _line_of(text: str, pos: int) -> int:
    """1-based line number for a byte/char offset into ``text``."""
    return text.count("\n", 0, pos) + 1


def _add(records: list[dict], seen: set, name: str, kind: str, line: int) -> None:
    """Append a (name, kind, line) record, deduped on (name, kind) so the same
    literal declared once is not emitted per regex overlap. First occurrence
    wins the line number (definition order is deterministic)."""
    key = (name, kind)
    if key in seen:
        return
    seen.add(key)
    records.append({"name": name, "kind": kind, "line": line})


def _scan_cpp(text: str, records: list[dict], seen: set) -> None:
    for m in _RE_TAG_DEFINE.finditer(text):
        tag = m.group("tag")
        if _is_tag_shaped(tag):
            _add(records, seen, tag, "gameplay_tag", _line_of(text, m.start()))
    for m in _RE_TAG_FNATIVE.finditer(text):
        tag = m.group("tag")
        if _is_tag_shaped(tag):
            _add(records, seen, tag, "gameplay_tag", _line_of(text, m.start()))
    for m in _RE_TAG_REQUEST.finditer(text):
        tag = m.group("tag")
        if _is_tag_shaped(tag):
            _add(records, seen, tag, "gameplay_tag", _line_of(text, m.start()))
    # _RetVal variants first so they consume the leading return-type token
    # before the no-RetVal pattern could mistake it for the delegate name.
    for m in _RE_DYN_DELEGATE_RETVAL.finditer(text):
        _add(records, seen, m.group("name"), "event", _line_of(text, m.start()))
    for m in _RE_DYN_DELEGATE.finditer(text):
        _add(records, seen, m.group("name"), "event", _line_of(text, m.start()))
    for m in _RE_BIND_UFUNCTION.finditer(text):
        _add(records, seen, m.group("name"), "event", _line_of(text, m.start()))
    for m in _RE_BIND_INPUT.finditer(text):
        _add(records, seen, m.group("name"), "event", _line_of(text, m.start()))


def _scan_ini(text: str, records: list[dict], seen: set) -> None:
    in_tag_section = False
    offset = 0
    for line in text.splitlines(keepends=True):
        sec = _RE_INI_SECTION.match(line)
        if sec is not None:
            sect = sec.group("name").lower()
            in_tag_section = any(h in sect for h in _INI_TAG_SECTION_HINTS)
        elif in_tag_section:
            for rx in (_RE_INI_TAG_LIST, _RE_INI_TAG_REDIRECT):
                for m in rx.finditer(line):
                    tag = m.group("tag")
                    if _is_tag_shaped(tag):
                        _add(records, seen, tag, "gameplay_tag", _line_of(text, offset))
        offset += len(line)


def _scan_dotted(text: str, records: list[dict], seen: set) -> None:
    """Generic dotted tag-like string-literal scan (all languages). Emits kind
    'name'; build_literal_index promotes the cross-file ones to 'config_key'.
    Skips any literal already captured with a more specific kind in this file."""
    already = {name for (name, _kind) in seen}
    for m in _RE_QUOTED.finditer(text):
        val = m.group(1) if m.group(1) is not None else m.group(2)
        if not val or val in already:
            continue
        if _DOTTED_LITERAL.match(val):
            _add(records, seen, val, "name", _line_of(text, m.start()))


def extract_file_literals(path: Path, text: str | None = None) -> list[dict]:
    """Return the registered-name literals in one file as
    [{name, kind, line}, ...] (no ``file`` field yet; build_literal_index
    stamps it). ``text`` may be supplied to avoid a re-read. Never raises -
    an unreadable file yields an empty list."""
    if text is None:
        try:
            text = Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return []
    suffix = Path(path).suffix.lower()
    records: list[dict] = []
    seen: set = set()
    if suffix in _CPP_SUFFIXES:
        _scan_cpp(text, records, seen)
    elif suffix in _INI_SUFFIXES:
        _scan_ini(text, records, seen)
    # Generic dotted scan runs for every file type (a dotted tag string can
    # appear in a data table export, a .json config, a .cs binding, etc.).
    _scan_dotted(text, records, seen)
    return records


def build_literal_index(
    files: list[Path], root: Path | None = None
) -> list[dict]:
    """Build the cross-file literal index: [{file, name, kind, line}, ...].

    ``files`` is the set of source/config files to scan. ``root``, when given,
    relativises the ``file`` field for portable, machine-independent output.

    Cross-file promotion: a dotted literal emitted as 'name' that occurs in more
    than one distinct file is promoted to 'config_key' (a shared join point);
    single-file dotted literals stay 'name'. gameplay_tag / event kinds are
    never reclassified. Output is sorted by (file, kind, name, line) for
    deterministic snapshots."""
    # Pass 1: per-file extraction.
    per_file: list[tuple[str, list[dict]]] = []
    name_files: dict[str, set[str]] = {}
    for path in files:
        p = Path(path)
        recs = extract_file_literals(p)
        if not recs:
            continue
        if root is not None:
            try:
                rel = p.resolve().relative_to(Path(root).resolve()).as_posix()
            except (ValueError, OSError):
                rel = p.as_posix()
        else:
            rel = p.as_posix()
        per_file.append((rel, recs))
        for r in recs:
            if r["kind"] == "name":
                name_files.setdefault(r["name"], set()).add(rel)

    # Pass 2: promote cross-file 'name' -> 'config_key' and flatten.
    out: list[dict] = []
    for rel, recs in per_file:
        for r in recs:
            kind = r["kind"]
            if kind == "name" and len(name_files.get(r["name"], ())) > 1:
                kind = "config_key"
            out.append(
                {"file": rel, "name": r["name"], "kind": kind, "line": r["line"]}
            )

    out.sort(key=lambda r: (r["file"], r["kind"], r["name"], r["line"]))
    return out
