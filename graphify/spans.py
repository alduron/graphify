"""Per-symbol line spans and body hashes, derived after extraction (AetherGraph Plan 44 section 2a).

WHY. A consumer that wants to know "did this symbol CHANGE" cannot answer it from a start line: a
refactor that shifts a file down twenty lines moves every symbol without changing any of them, and a
detector built on line numbers alone would call the whole file stale. What distinguishes the two is a
hash of the symbol's own text, which nothing in the extractor emitted.

WHY NOT AT THE EMISSION SITES. There are 56 `nodes.append` calls in extract.py plus the per-language
extractor modules, and none of them keeps the tree-sitter node around after computing its start line.
Threading a span through all of them would touch every language at once for one field. This runs
instead at `_safe_extract_with_xaml_root`, the single choke point both the parallel and the sequential
paths go through, where the file's whole node list and its path are both in hand.

HOW THE SPAN IS DERIVED, and its limits. A node's span runs from its own start line to the line
before the next distinct start line in the same file, then containers extend over their members. So:

  - a leaf function's span is its body, exactly;
  - a class's span covers its methods, because the `method` edges extend it over them - which is what
    matters most, since knowledge is usually anchored to a class rather than to one of its methods;
  - a file node's span is only its header region, because `contains` edges deliberately do NOT
    extend. Extending them would make every file-anchored node change whenever ANY line in the file
    changed, and file anchors are common, so that is precisely the flag storm to avoid.

This is an approximation of the AST end, not the AST end. It errs toward UNDER-detection - a change
inside a member that no edge connects to its owner will not move the owner's hash - and that is the
safe direction: a missed change costs a stale node nobody flagged, where a false change costs a flag
against every node anchored to that symbol.

WHITESPACE IS NORMALISED OUT. Trailing whitespace per line and trailing blank lines are stripped
before hashing, so a re-indent or a stray newline does not read as a behaviour change. Nothing true of
a symbol's prose stops being true because its body moved a column.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

# Edges whose target is part of the SOURCE's own body, so the source's span must cover it. 'method'
# is class -> member. 'contains' is deliberately absent: it is file -> symbol, and letting it extend
# would make every file node span its whole file (see the module docstring).
_SPAN_CONTAINER_RELATIONS = frozenset({"method"})

# Guard against a pathological cycle in the containment edges. Depth, not visited-set, because a
# legitimately deep nesting is fine and only a cycle needs stopping.
_MAX_CONTAINER_DEPTH = 16

_LINE_RE = re.compile(r"^L(\d+)$")


def _start_line(node: dict) -> int | None:
    """The node's own start line, or None when it carries no usable location.

    A sourceless stub (source_file '') is a cross-file reference placeholder, not a definition, so it
    has no body here to hash and is skipped rather than given the file's first span.
    """
    if not node.get("source_file"):
        return None
    match = _LINE_RE.match(str(node.get("source_location") or ""))
    if match is None:
        return None
    line = int(match.group(1))
    return line if line >= 1 else None


def _normalised(lines: list[str]) -> str:
    """The slice as it is hashed: no trailing whitespace, no trailing blank lines."""
    stripped = [line.rstrip() for line in lines]
    while stripped and not stripped[-1]:
        stripped.pop()
    return "\n".join(stripped)


def annotate_symbol_spans(result: dict, path: Path) -> dict:
    """Add `end_line` and `body_sha` to every located node in one file's extraction result.

    Mutates and returns ``result``. Never raises: an unreadable file, a node with no location, or an
    empty result leaves the nodes exactly as they were. Extraction must not fail over a derived
    field.
    """
    nodes = result.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        return result
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return result
    lines = text.splitlines()
    if not lines:
        return result

    located = [(node, line) for node in nodes if (line := _start_line(node)) is not None]
    if not located:
        return result

    ends = _naive_ends(located, len(lines))
    _extend_over_members(result, located, ends)

    for node, start in located:
        end = ends[str(node.get("id"))]
        node["end_line"] = end
        node["body_sha"] = hashlib.sha256(
            _normalised(lines[start - 1 : end]).encode("utf-8")
        ).hexdigest()
    return result


def _naive_ends(located: list[tuple[dict, int]], last_line: int) -> dict[str, int]:
    """Each node ends the line before the next distinct start line; the last runs to EOF.

    Nodes SHARING a start line (a decorator and its function, a class and its constructor) all get
    the same span, which is correct: nothing in the text distinguishes them.
    """
    starts = sorted({line for _, line in located})
    next_start = {
        line: (starts[i + 1] if i + 1 < len(starts) else None)
        for i, line in enumerate(starts)
    }
    ends: dict[str, int] = {}
    for node, start in located:
        following = next_start[start]
        ends[str(node.get("id"))] = (following - 1) if following else last_line
    return ends


def _extend_over_members(
    result: dict, located: list[tuple[dict, int]], ends: dict[str, int]
) -> None:
    """Grow each container's end to cover the members its edges claim, transitively.

    Without this a class's span is just its header, so a change to a method body would never move the
    class's hash - and a class is what knowledge is usually anchored to.
    """
    members: dict[str, list[str]] = {}
    for edge in result.get("edges") or []:
        if not isinstance(edge, dict) or edge.get("relation") not in _SPAN_CONTAINER_RELATIONS:
            continue
        source, target = str(edge.get("source") or ""), str(edge.get("target") or "")
        if source and target and source != target and target in ends:
            members.setdefault(source, []).append(target)
    if not members:
        return

    def reach(node_id: str, depth: int) -> int:
        end = ends.get(node_id, 0)
        if depth >= _MAX_CONTAINER_DEPTH:
            return end
        for member in members.get(node_id, ()):
            end = max(end, reach(member, depth + 1))
        return end

    for node, _ in located:
        node_id = str(node.get("id"))
        if node_id in members:
            ends[node_id] = reach(node_id, 0)


__all__ = ["annotate_symbol_spans"]
