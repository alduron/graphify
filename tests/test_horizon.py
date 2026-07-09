"""Tests for the extraction-horizon features (Plan 08 Phase 5):

  - literals.py         registered-name literal index (tags / events / config keys)
  - unreal_assets.py    opaque Unreal .uasset provider
  - horizon.py          the assembled, versioned snapshot sections

The C++ / ini fixtures live under tests/fixtures/ue_horizon/. The .uasset test
fixtures are BUILT here by _make_uasset() rather than committed as binaries: the
helper hand-crafts the minimal Unreal package layout the scanners read (a magic
number plus a name table of length-prefixed FString records), which keeps the
fixtures readable and version-controllable.
"""
from __future__ import annotations

import struct
from pathlib import Path

from graphify.horizon import HORIZON_SCHEMA_VERSION, build_horizon_sections
from graphify.literals import build_literal_index, extract_file_literals
from graphify.unreal_assets import scan_asset_file, scan_assets

FIXTURES = Path(__file__).parent / "fixtures" / "ue_horizon"
_UASSET_MAGIC = 0x9E2A83C1


# ---------------------------------------------------------------------------
# .uasset fixture builders
# ---------------------------------------------------------------------------

def _make_uasset_raw(names: list[str]) -> bytes:
    """Minimal package: magic + a run of length-prefixed FString records. This
    matches the version-independent FString shape the raw scanner reads (the
    structured summary parse deliberately bails on it, exercising the fallback)."""
    buf = bytearray(struct.pack("<I", _UASSET_MAGIC))
    for name in names:
        payload = name.encode("ascii") + b"\x00"
        buf += struct.pack("<i", len(payload))  # length includes the NUL
        buf += payload
    return bytes(buf)


def _make_uasset_summary(names: list[str]) -> bytes:
    """A package with a real (minimal) PackageFileSummary so the structured
    name-table parse succeeds. Field order mirrors unreal_assets._parse_name_table
    for a UE5 (legacy = -8) header."""
    # Twelve int32 summary fields precede the name table; it starts at offset 48.
    name_offset = 48
    summary = struct.pack(
        "<Iiiiiii i i i ii",
        _UASSET_MAGIC,   # magic
        -8,              # legacy file version (UE5)
        -1,              # legacy UE3 version
        522,             # file version UE4
        1009,            # file version UE5
        0,               # licensee version
        0,               # custom-version count
        0,               # total header size (unused by the parser)
        0,               # FolderName FString length (empty)
        0,               # package flags
        len(names),      # name count
        name_offset,     # name offset
    )
    assert len(summary) == name_offset, len(summary)
    table = bytearray()
    for name in names:
        payload = name.encode("ascii") + b"\x00"
        table += struct.pack("<i", len(payload))
        table += payload
        table += b"\x00\x00\x00\x00"  # trailing FName hash bytes
    return bytes(summary) + bytes(table)


# ---------------------------------------------------------------------------
# literals: per-file extraction
# ---------------------------------------------------------------------------

def _kinds_by_name(records: list[dict]) -> dict[str, str]:
    return {r["name"]: r["kind"] for r in records}


def test_cpp_gameplay_tag_defines_are_indexed() -> None:
    recs = extract_file_literals(
        FIXTURES / "Source" / "AetherPulse" / "Combat" / "CombatNativeTags.cpp"
    )
    kinds = _kinds_by_name(recs)
    assert kinds.get("Ability.Burn") == "gameplay_tag"
    assert kinds.get("State.Actor.Corroded") == "gameplay_tag"
    # Numeric leaf segment is a valid tag segment.
    assert kinds.get("HitZone.Thruster.01") == "gameplay_tag"


def test_cpp_request_and_binding_literals() -> None:
    recs = extract_file_literals(
        FIXTURES / "Source" / "AetherPulse" / "Combat" / "BurnAbility.cpp"
    )
    kinds = _kinds_by_name(recs)
    # RequestGameplayTag(FName("...")) -> gameplay_tag
    assert kinds.get("Ability.Burn") == "gameplay_tag"
    # AddUFunction(this, FName("...")) -> event
    assert kinds.get("HandleBurnApplied") == "event"
    # BindAction("...") -> event
    assert kinds.get("FireWeapon") == "event"


def test_cpp_dynamic_delegate_declarations_are_events() -> None:
    recs = extract_file_literals(
        FIXTURES / "Source" / "AetherPulse" / "Combat" / "CombatNativeTags.h"
    )
    kinds = _kinds_by_name(recs)
    assert kinds.get("FOnBurnApplied") == "event"
    # _RetVal variant: the delegate name is the SECOND macro argument.
    assert kinds.get("FOnBurnQuery") == "event"
    # UE_DECLARE_GAMEPLAY_TAG_EXTERN declares the C++ constant, not the string:
    # no tag literal should be emitted for the extern-only header.
    assert not any(r["kind"] == "gameplay_tag" for r in recs)


def test_negative_arbitrary_strings_and_comments_not_indexed() -> None:
    recs = extract_file_literals(
        FIXTURES / "Source" / "AetherPulse" / "Combat" / "BurnAbility.cpp"
    )
    names = {r["name"] for r in recs}
    assert "Burning the target right now" not in names   # has spaces
    assert "Content/Abilities/GA_Burn.uasset" not in names  # path with slashes
    assert "1.2.3" not in names                          # numeric-first
    assert "system.config.value" not in names            # lowercase segments
    assert "Not.A.Real.Tag" not in names                 # only in a comment


def test_ini_gameplay_tags_indexed_only_in_tag_section() -> None:
    recs = extract_file_literals(FIXTURES / "Config" / "DefaultGameplayTags.ini")
    kinds = _kinds_by_name(recs)
    assert kinds.get("Ability.Burn") == "gameplay_tag"
    assert kinds.get("Ability.Freeze") == "gameplay_tag"
    # Redirect old/new names both index.
    assert kinds.get("Ability.Ignite") == "gameplay_tag"
    # Value in a non-gameplay-tag section must not appear.
    assert not any("Not a tag value" in r["name"] for r in recs)


# ---------------------------------------------------------------------------
# literals: cross-file aggregation + config_key promotion
# ---------------------------------------------------------------------------

def test_config_key_promotion_across_files() -> None:
    combat = FIXTURES / "Source" / "AetherPulse" / "Combat"
    files = [combat / "CombatNativeTags.cpp", combat / "BurnAbility.cpp"]
    index = build_literal_index(files, root=FIXTURES)
    kinds = {(r["file"], r["name"]): r["kind"] for r in index}
    # "Game.Config.MaxPlayers" appears in both files -> config_key everywhere.
    hits = [r for r in index if r["name"] == "Game.Config.MaxPlayers"]
    assert len(hits) == 2
    assert {r["kind"] for r in hits} == {"config_key"}
    # A gameplay_tag stays a gameplay_tag (never reclassified).
    assert any(
        r["name"] == "Ability.Burn" and r["kind"] == "gameplay_tag" for r in index
    )


def test_single_file_dotted_literal_stays_name() -> None:
    # Only one file that references a dotted literal seen nowhere else.
    combat = FIXTURES / "Source" / "AetherPulse" / "Combat"
    index = build_literal_index([combat / "CombatNativeTags.cpp"], root=FIXTURES)
    kinds = _kinds_by_name(index)
    # With just this file, Game.Config.MaxPlayers occurs once -> weak "name".
    assert kinds.get("Game.Config.MaxPlayers") == "name"


def test_literal_index_is_relative_and_deterministic() -> None:
    combat = FIXTURES / "Source" / "AetherPulse" / "Combat"
    files = [combat / "BurnAbility.cpp", combat / "CombatNativeTags.cpp"]
    a = build_literal_index(files, root=FIXTURES)
    b = build_literal_index(list(reversed(files)), root=FIXTURES)
    assert a == b  # order-independent, deterministic
    assert all(not r["file"].startswith("/") and "\\" not in r["file"] for r in a)
    assert all(set(r) == {"file", "name", "kind", "line"} for r in a)


# ---------------------------------------------------------------------------
# unreal_assets: class + referenced-name extraction
# ---------------------------------------------------------------------------

def test_asset_class_from_script_import(tmp_path: Path) -> None:
    names = [
        "/Script/Engine.Blueprint",                 # placeholder - skipped
        "/Script/Engine.BlueprintGeneratedClass",   # placeholder - skipped
        "/Script/GameplayAbilities.GameplayAbility",  # the real parent
        "Ability.Burn",
    ]
    p = tmp_path / "GA_Burn.uasset"
    p.write_bytes(_make_uasset_raw(names))
    rec = scan_asset_file(p)
    assert rec is not None
    assert rec["asset_class"] == "GameplayAbility"
    assert "Ability.Burn" in rec["referenced_names"]


def test_asset_referenced_names_from_summary_parse(tmp_path: Path) -> None:
    names = ["/Script/Engine.DataTable", "State.Actor.Corroded", "SomeField"]
    p = tmp_path / "DT_Enemies.uasset"
    p.write_bytes(_make_uasset_summary(names))
    rec = scan_asset_file(p)
    assert rec is not None
    assert rec["asset_class"] == "DataTable"
    assert rec["referenced_names"] == ["State.Actor.Corroded"]


def test_asset_known_literal_filter(tmp_path: Path) -> None:
    # An identifier-shaped (non-dotted) name is only referenced when it is in the
    # project's known-literal set - this keeps precision high.
    names = ["/Script/Engine.DataTable", "HandleBurnApplied", "RandomFName"]
    p = tmp_path / "DT_Events.uasset"
    p.write_bytes(_make_uasset_raw(names))
    rec = scan_asset_file(p, known_literals={"HandleBurnApplied"})
    assert rec is not None
    assert rec["referenced_names"] == ["HandleBurnApplied"]
    assert "RandomFName" not in rec["referenced_names"]


def test_malformed_uasset_does_not_crash(tmp_path: Path) -> None:
    bad_magic = tmp_path / "junk.uasset"
    bad_magic.write_bytes(b"NOPE" + b"\x00" * 64)
    assert scan_asset_file(bad_magic) is None

    truncated = tmp_path / "tiny.uasset"
    truncated.write_bytes(b"\x01\x02")
    assert scan_asset_file(truncated) is None

    # A well-magicked but garbage body must degrade, never raise.
    garbage = tmp_path / "garbage.uasset"
    garbage.write_bytes(struct.pack("<I", _UASSET_MAGIC) + b"\xff" * 200)
    rec = scan_asset_file(garbage)
    assert rec is None or isinstance(rec, dict)


def test_scan_assets_deterministic_and_relative(tmp_path: Path) -> None:
    (tmp_path / "Content").mkdir()
    (tmp_path / "Content" / "GA_Burn.uasset").write_bytes(
        _make_uasset_raw(["/Script/GameplayAbilities.GameplayAbility", "Ability.Burn"])
    )
    (tmp_path / "Content" / "DT_Enemies.uasset").write_bytes(
        _make_uasset_raw(["/Script/Engine.DataTable", "State.Actor.Corroded"])
    )
    # Build output must be skipped.
    (tmp_path / "Intermediate").mkdir()
    (tmp_path / "Intermediate" / "Skip.uasset").write_bytes(
        _make_uasset_raw(["/Script/Engine.DataTable"])
    )
    assets = scan_assets(tmp_path)
    paths = [a["path"] for a in assets]
    assert paths == sorted(paths)
    assert paths == ["Content/DT_Enemies.uasset", "Content/GA_Burn.uasset"]
    assert scan_assets(tmp_path) == assets  # deterministic
    assert all("\\" not in p for p in paths)


# ---------------------------------------------------------------------------
# horizon: assembled sections
# ---------------------------------------------------------------------------

def _copy_fixture_project(tmp_path: Path) -> Path:
    """Copy the committed C++/ini fixture tree into tmp and drop two .uasset
    consumers next to it, giving a self-contained mini UE project."""
    import shutil

    root = tmp_path / "proj"
    shutil.copytree(FIXTURES, root)
    content = root / "Content" / "Abilities"
    content.mkdir(parents=True)
    (content / "GA_Burn.uasset").write_bytes(
        _make_uasset_raw(["/Script/GameplayAbilities.GameplayAbility", "Ability.Burn"])
    )
    (content / "DT_Enemies.uasset").write_bytes(
        _make_uasset_summary(["/Script/Engine.DataTable", "State.Actor.Corroded"])
    )
    return root


def test_build_horizon_sections_end_to_end(tmp_path: Path) -> None:
    root = _copy_fixture_project(tmp_path)
    code_files = list((root / "Source").rglob("*.cpp")) + list(
        (root / "Source").rglob("*.h")
    )
    sections = build_horizon_sections(code_files, root)

    assert sections["horizon_schema"] == HORIZON_SCHEMA_VERSION
    lit_kinds = {r["name"]: r["kind"] for r in sections["literals"]}
    assert lit_kinds.get("Ability.Burn") == "gameplay_tag"
    assert lit_kinds.get("FOnBurnApplied") == "event"
    assert lit_kinds.get("FireWeapon") == "event"

    # Assets found, class + tag references present, filtered to known literals.
    by_path = {a["path"]: a for a in sections["assets"]}
    ga = by_path["Content/Abilities/GA_Burn.uasset"]
    assert ga["asset_class"] == "GameplayAbility"
    assert "Ability.Burn" in ga["referenced_names"]
    dt = by_path["Content/Abilities/DT_Enemies.uasset"]
    assert dt["asset_class"] == "DataTable"
    assert "State.Actor.Corroded" in dt["referenced_names"]


def test_build_horizon_sections_deterministic(tmp_path: Path) -> None:
    root = _copy_fixture_project(tmp_path)
    code_files = list((root / "Source").rglob("*.cpp"))
    assert build_horizon_sections(code_files, root) == build_horizon_sections(
        code_files, root
    )
