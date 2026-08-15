"""Unreal opaque asset provider (Plan 08 Phase 5, the extraction horizon).

Assets that exist and participate in couplings but whose contents are beyond
extraction - Blueprints, data tables, materials - are compiled ``.uasset`` /
``.umap`` blobs. This provider mines them WITHOUT opening the editor to produce
opaque asset records:

    {path, asset_class, referenced_names}

``path`` is repo-relative. ``asset_class`` is a best-effort parent C++ class
(e.g. UGameplayAbility) recovered from the package import table. ``referenced_names``
are the registered-name literals (gameplay tags / event names) the asset points
at - the same join points feature 1 indexes on the code side. Emitting an opaque
node converts a silent absence ("found 3 consumers, concluded 3") into an
explicit "there are more consumers here you cannot read".

Implementation ladder (the plan's (a)+(b); (c) AssetRegistry.bin is out of scope):

(a) .uasset / .umap lightweight scan. The full PackageFileSummary layout drifts
    every engine release, so we do NOT rely on per-version field offsets. We scan
    the header region for length-prefixed FString records (the shape every FName
    in the name table uses) and classify them:
      - /Script/<Module>.<Class>  -> asset_class candidate (import table)
      - dotted tag-shaped strings  -> referenced gameplay tags
      - strings in the project's own known-literal set -> referenced names
    Filtering referenced names against the project literal index (feature 1)
    keeps precision high: an asset only "references" a name the code also knows.
    A best-effort structured name-table parse refines the string set when the
    header parses cleanly; it degrades to the raw scan otherwise.

(b) Config-driven references (DefaultGameplayTags.ini tag redirects) are indexed
    on the literal side (see literals.py), so they already flow into the
    known-literal set this provider filters against.

NAMES ONLY: never emit file contents, only paths / classes / referenced names.

Public API
----------
scan_assets(root, known_literals=None, *, max_files=..., max_bytes=...) -> list[dict]
    Walk ``root`` for .uasset/.umap files and return opaque asset records,
    deterministically ordered. Malformed / locked files are skipped (never
    raises). ``known_literals`` (from build_literal_index) tightens referenced
    -name precision when supplied.
"""
from __future__ import annotations

import logging
import os
import re
import struct
from pathlib import Path

log = logging.getLogger(__name__)

_UASSET_MAGIC = 0x9E2A83C1
_ASSET_SUFFIXES: frozenset[str] = frozenset({".uasset", ".umap"})

# Read caps. The name table lives near the start of the package; a few hundred KB
# is plenty and bounds the work on multi-MB cooked assets.
_MAX_READ_BYTES = 512 * 1024
_MAX_NAME_TABLE = 100_000
_MAX_FNAME_LEN = 1024

# Directories that never hold hand-authored source assets we care about; skipped
# during the walk so a cooked build tree does not explode the scan.
_SKIP_DIRS: frozenset[str] = frozenset(
    {
        "Intermediate",
        "Binaries",
        "DerivedDataCache",
        "Build",
        "Saved",
        ".git",
        ".svn",
        "node_modules",
        "graphify-out",
        ".vs",
    }
)

# A dotted tag-shaped literal (mirrors literals._DOTTED_LITERAL): PascalCase /
# numeric segments, first segment capitalised, depth 2..6. Used to pick tag
# references out of the name table without a project literal set.
_DOTTED_TAG = re.compile(
    r"^[A-Z][A-Za-z0-9]*(?:\.(?:[A-Z][A-Za-z0-9]*|[0-9]+)){1,5}$", re.ASCII
)

# /Script/Module.Class import references (bare and quoted-inner forms).
_SCRIPT_REF = re.compile(
    r"^/Script/(?P<module>[A-Za-z][A-Za-z0-9_]*)\."
    r"(?P<cls>[A-Za-z_][A-Za-z0-9_]*)$",
    re.ASCII,
)
_SCRIPT_REF_QUOTED = re.compile(
    r"^/Script/[A-Za-z][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*"
    r"'/Script/(?P<module>[A-Za-z][A-Za-z0-9_]*)\."
    r"(?P<cls>[A-Za-z_][A-Za-z0-9_]*)'$",
    re.ASCII,
)

# Blueprint container / reflection classes that appear in every asset import
# table but are NEVER the asset's runtime parent - skip them when choosing
# asset_class so a Blueprint resolves to its real C++ base.
_CLASS_PLACEHOLDERS: frozenset[str] = frozenset(
    {
        "Blueprint",
        "BlueprintGeneratedClass",
        "AnimBlueprint",
        "AnimBlueprintGeneratedClass",
        "WidgetBlueprint",
        "WidgetBlueprintGeneratedClass",
        "ControlRigBlueprint",
        "Class",
        "Object",
        "Package",
        "MetaData",
    }
)

_UE_CLASS_NAME = re.compile(r"^[UAFIES][A-Z][A-Za-z0-9_]+$", re.ASCII)

# Every little-endian int32 encoding of an accepted length (2..256): [L,00,00,00]
# for 2..255, [00,01,00,00] for 256. No other prefix can start a record.
_LEN_PREFIX = re.compile(rb"[\x02-\xff]\x00\x00\x00|\x00\x01\x00\x00")


def _scan_fstrings(data: bytes) -> list[str]:
    """Walk the bytes for <int32 length><ASCII bytes><NUL> records (the UE
    FString serialisation used by every name-table entry) and return them in
    file order. Version-independent: no PackageFileSummary offsets needed.

    Advances by seeking the next `_LEN_PREFIX` candidate rather than by one byte,
    which is output-identical because a record can only begin at such a prefix.
    """
    out: list[str] = []
    n = len(data)
    search = _LEN_PREFIX.search
    pos = 4  # skip the magic
    while pos + 5 <= n:
        length = int.from_bytes(data[pos : pos + 4], "little", signed=True)
        if 2 <= length <= 256 and pos + 4 + length <= n:
            body = data[pos + 4 : pos + 4 + length]
            if body.endswith(b"\x00"):
                payload = body.rstrip(b"\x00")
                if payload and all(32 <= b < 127 for b in payload):
                    out.append(payload.decode("ascii", errors="replace"))
                    pos += 4 + length
                    continue
        m = search(data, pos + 1)
        if m is None:
            break
        pos = m.start()
    return out


def _parse_name_table(data: bytes) -> list[str] | None:
    """Best-effort structured parse of PackageFileSummary -> name table. Returns
    the FName strings, or None on any parse failure (caller falls back to the raw
    FString scan). Only reads the fields needed to locate the name table."""
    if len(data) < 32 or struct.unpack_from("<I", data, 0)[0] != _UASSET_MAGIC:
        return None
    try:
        pos = 4
        legacy = struct.unpack_from("<i", data, pos)[0]
        pos += 4
        if legacy > -2 or legacy < -10:
            return None
        pos += 4  # legacy UE3 version
        pos += 4  # file version UE4
        if legacy <= -8:
            pos += 4  # file version UE5
        pos += 4  # file version licensee
        if legacy != -4:
            cv_count = struct.unpack_from("<i", data, pos)[0]
            pos += 4
            if cv_count < 0 or cv_count > 1024:
                return None
            pos += cv_count * 20  # 16-byte GUID + 4-byte version each
        pos += 4  # total header size
        # FolderName FString (int32 length + payload).
        flen = struct.unpack_from("<i", data, pos)[0]
        pos += 4
        if flen > 0:
            pos += flen
        elif flen < 0:
            pos += (-flen) * 2
        pos += 4  # package flags
        name_count = struct.unpack_from("<i", data, pos)[0]
        pos += 4
        name_offset = struct.unpack_from("<i", data, pos)[0]
        pos += 4
        if name_count <= 0 or name_count > _MAX_NAME_TABLE:
            return None
        if name_offset <= 0 or name_offset >= len(data):
            return None
    except (struct.error, IndexError):
        return None

    names: list[str] = []
    p = name_offset
    try:
        for _ in range(name_count):
            if p + 4 > len(data):
                break
            length = struct.unpack_from("<i", data, p)[0]
            p += 4
            if length > 0:
                if length > _MAX_FNAME_LEN or p + length > len(data):
                    break
                s = data[p : p + length].rstrip(b"\x00").decode(
                    "latin-1", errors="replace"
                )
                p += length
            elif length < 0:
                chars = -length
                if chars > _MAX_FNAME_LEN or p + chars * 2 > len(data):
                    break
                s = data[p : p + chars * 2].rstrip(b"\x00").decode(
                    "utf-16-le", errors="replace"
                )
                p += chars * 2
            else:
                s = ""
            # Each entry carries trailing hash bytes (4 for UE4.18+/UE5).
            p += 4
            if s:
                names.append(s)
    except (struct.error, IndexError):
        pass
    return names or None


def _classify(strings: list[str]) -> str:
    """Pick the best-effort asset_class from the name-table strings: the first
    /Script import that is not a Blueprint/reflection placeholder. Falls back to
    the first bare UE-class-shaped name, then '' when nothing is recoverable."""
    script_refs: list[tuple[str, str]] = []
    bare_class = ""
    for s in strings:
        m = _SCRIPT_REF.match(s) or _SCRIPT_REF_QUOTED.match(s)
        if m is not None:
            script_refs.append((m.group("module"), m.group("cls")))
        elif not bare_class and _UE_CLASS_NAME.match(s) and s not in _CLASS_PLACEHOLDERS:
            bare_class = s
    for _module, cls in script_refs:
        if cls in _CLASS_PLACEHOLDERS:
            continue
        if cls.endswith("Blueprint") or cls.endswith("BlueprintGeneratedClass"):
            continue
        return cls
    return bare_class


def _referenced_names(strings: list[str], known_literals: set[str] | None) -> list[str]:
    """Registered names the asset references: dotted tag-shaped strings, plus any
    string in the project's known-literal set. Deduped and sorted."""
    found: set[str] = set()
    for s in strings:
        if _DOTTED_TAG.match(s):
            found.add(s)
        elif known_literals is not None and s in known_literals:
            found.add(s)
    return sorted(found)


def scan_asset_file(
    path: Path, known_literals: set[str] | None = None
) -> dict | None:
    """Parse a single .uasset/.umap into an opaque record, or None if it is not a
    recognisable package (bad magic / too small / unreadable). Never raises."""
    try:
        size = path.stat().st_size
        with path.open("rb") as fh:
            data = fh.read(min(size, _MAX_READ_BYTES))
    except OSError as exc:
        log.warning("unreal_assets: cannot read %s: %s", path, exc)
        return None
    if len(data) < 8 or struct.unpack_from("<I", data, 0)[0] != _UASSET_MAGIC:
        log.warning("unreal_assets: skipping non-uasset %s", path)
        return None
    # Prefer the structured name table; fall back to the raw FString scan.
    strings = _parse_name_table(data)
    if not strings:
        strings = _scan_fstrings(data)
    return {
        "asset_class": _classify(strings),
        "referenced_names": _referenced_names(strings, known_literals),
    }


def _iter_asset_files(root: Path, max_files: int) -> list[Path]:
    """Deterministically ordered .uasset/.umap paths under ``root``, capped at
    ``max_files``. Build-output directories are PRUNED during the walk (os.walk
    with in-place dir filtering) rather than descended and filtered after - a
    cooked Unreal tree can hold tens of thousands of files under Intermediate /
    DerivedDataCache that must never be traversed."""
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for fn in filenames:
            if os.path.splitext(fn)[1].lower() in _ASSET_SUFFIXES:
                found.append(Path(dirpath) / fn)
                if len(found) >= max_files:
                    found.sort()
                    return found
    found.sort()
    return found


def scan_assets(
    root: Path,
    known_literals: set[str] | None = None,
    *,
    max_files: int = 50_000,
) -> list[dict]:
    """Produce opaque asset records for every .uasset/.umap under ``root``.

    Returns [{path, asset_class, referenced_names}, ...] sorted by path. ``path``
    is repo-relative and POSIX-separated. Malformed / locked / non-package files
    are skipped with a warning. ``known_literals`` (the project's registered-name
    set from build_literal_index) tightens referenced-name precision."""
    root = Path(root)
    out: list[dict] = []
    for path in _iter_asset_files(root, max_files):
        rec = scan_asset_file(path, known_literals)
        if rec is None:
            continue
        try:
            rel = path.resolve().relative_to(root.resolve()).as_posix()
        except (ValueError, OSError):
            rel = path.as_posix()
        out.append(
            {
                "path": rel,
                "asset_class": rec["asset_class"],
                "referenced_names": rec["referenced_names"],
            }
        )
    out.sort(key=lambda r: r["path"])
    return out
