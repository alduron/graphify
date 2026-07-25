"""Extraction-horizon sections for the snapshot payload (Plan 08 Phase 5).

Assembles the two additive, NAMES-ONLY sections that let the knowledge loop reach
past what graphify can parse - registered-name literals and opaque Unreal assets -
into a single versioned block the CLI forwards with the snapshot and the API
consumes.

Output contract (top-level keys added to graphify-out/graph.json):

    "horizon_schema": 1
    "literals": [ {file, name, kind, line}, ... ]     # kind in
                 gameplay_tag | event | config_key | name
    "assets":   [ {path, asset_class, referenced_names[]}, ... ]

The version stamp lets an older CLI ignore the sections gracefully (it reads only
nodes / links) while a new CLI keys off horizon_schema. Both lists are
deterministically ordered so identical inputs produce byte-identical snapshots.

Public API
----------
build_horizon_sections(code_files, root, *, extra_files=None) -> dict
    Compute both sections. ``code_files`` are the source files already collected
    for AST extraction; this module additionally discovers .ini config and
    .uasset/.umap assets under ``root`` (those extensions are not code and are
    not in ``code_files``). The asset provider filters referenced names against
    the literal index for precision.
"""
from __future__ import annotations

import os
from pathlib import Path

from graphify.literals import build_literal_index
from graphify.unreal_assets import scan_assets

HORIZON_SCHEMA_VERSION = 1

_INI_SUFFIX = ".ini"
# Directories that never hold hand-authored source we index; mirrors the asset
# provider skip set so the ini discovery does not descend into build output.
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
_MAX_INI_FILES = 20_000


def _discover_ini_files(root: Path, max_files: int = _MAX_INI_FILES) -> list[Path]:
    """Deterministically ordered *.ini files under ``root``, build-output
    directories pruned during the walk. ini config carries gameplay-tag
    registries the literal index needs but that never appear in ``code_files``."""
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for fn in filenames:
            if fn.lower().endswith(_INI_SUFFIX):
                found.append(Path(dirpath) / fn)
                if len(found) >= max_files:
                    found.sort()
                    return found
    found.sort()
    return found


def build_horizon_sections(
    code_files: list[Path],
    root: Path,
    *,
    extra_files: list[Path] | None = None,
) -> dict:
    """Build the versioned horizon sections dict.

    Never raises: any per-file failure is swallowed inside the underlying
    scanners so an extraction is never blocked by a malformed asset or config.
    """
    root = Path(root)
    literal_files: list[Path] = list(code_files)
    literal_files.extend(_discover_ini_files(root))
    if extra_files:
        literal_files.extend(extra_files)

    literals = build_literal_index(literal_files, root=root)
    known_literals = {rec["name"] for rec in literals}
    assets = scan_assets(root, known_literals=known_literals)

    return {
        "horizon_schema": HORIZON_SCHEMA_VERSION,
        "literals": literals,
        "assets": assets,
    }
