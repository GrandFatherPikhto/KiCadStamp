# kicadstamp/board_dedupe.py
"""Duplicate copper (vias + track segments) on the LIVE board: find them,
report them, remove the extra copies.

Ported from ``tools/dedupe_vias_tracks.py``, which stays as-is for Denis's
habit of running it by hand (plan 2026-10-01 §2.5). This module is the
IPC-free core both the CLI (``kicadstamp dedupe``) and the GUI panel
("Дубли меди") call, so the two surfaces can never disagree about what a
duplicate is.

Why duplicates exist (the 2026-07-29 Power duplicate incident, kept from the
tool's docstring): ``registry.json`` bookkeeping only protects against
duplicates within ONE registry file. If the same physical ``clone_placement``
is ever applied under two different ``registry_path``/``track_registry_path``
(``registry_path`` added to a config after some vias already existed under the
auto-derived path, or a config renamed without migrating its registry), each
``reconcile()`` only knows its OWN UUIDs and happily creates a second copy on
top of the first. This module deliberately ignores the registries entirely and
looks at GROUND TRUTH — the live board — so it works whatever is out of sync.
It does not touch a registry either: once the extra live item is gone, the next
normal ``apply`` run's ``reconcile()`` prunes the stale registry entry itself.

Grouping (greedy, NOT full transitive clustering — a conscious limitation):
real duplicates come from re-running an identical planner, so they land at
near-exactly the same spot, not spread across a chain of near-misses. A chain
A--B--C where A is within tolerance of B and B within tolerance of C but A is
NOT within tolerance of C therefore yields two groups (A+B, then C alone),
not one — acceptable here, and the alternative (union-find) would merge more
than the board can justify.
  * vias:   same net, position within POSITION_TOLERANCE_MM of the group's
            FIRST member. Drill/diameter are NOT part of the key (mirrors
            PlacementRegistry._live_matches' tolerance-only comparison); a
            mismatch inside a group is a WARNING in the report.
  * tracks: same net and layer, (start, end) as an UNORDERED pair (A->B and
            B->A are the same physical track) within tolerance.

The KEPT member of a group is the FIRST one the adapter returned. That is
deterministic for a given scan, and pins the "which copy survives" cell; it is
also defensible because, by the grouping key, the kept and deleted items are
physically indistinguishable.

The report text this module produces is deliberately FIXED ENGLISH, not run
through ``_()``. Two reasons, both hard requirements rather than taste:
  1. the GUI's "Копировать" must produce byte-for-byte the same text the CLI
     prints, so the report can be pasted straight into a bug report — a
     translated report would only agree within one locale;
  2. the text is read by a machine as much as by a human (grep for a UUID).
The user-facing words AROUND the report (CLI prompts, panel buttons, the
"removed N" summary) DO go through ``_()`` as usual.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Callable, Sequence, Tuple, TypeVar

from .constants import POSITION_TOLERANCE_MM
from .domain.board import Track, Via
from .domain.geometry import BoardLayer
from .utils.layers import layer_to_str
from .utils.units import MM

__all__ = [
    "ACTION_LOGGER_NAME",
    "apply_dedupe",
    "apply_summary",
    "find_track_duplicate_groups",
    "find_via_duplicate_groups",
    "format_report",
    "scan_copper_duplicates",
    "track_position_text",
    "via_position_text",
    "via_sizes",
]

#: The logger the per-deletion audit lines go through. A dedicated NAME (not
#: this module's own) so ``actions.log`` can be grepped for exactly the
#: deletions: the CLI attaches the file handler (``setup_logging(log_file=...)``)
#: and the GUI attaches the root config's handler in ``gui/dock_hub.py``, so the
#: same line lands in the profile's ``actions.log`` on both surfaces, which is
#: what the 2026-10-01 investigation needs the next time duplicates appear.
ACTION_LOGGER_NAME = "kicadstamp.actions"

logger = logging.getLogger(__name__)
_actions_logger = logging.getLogger(ACTION_LOGGER_NAME)

T = TypeVar("T")

#: The tolerance in BOARD UNITS (nanometres), not mm. Comparison happens on the
#: integer positions the adapter reports, so "within POSITION_TOLERANCE_MM" is
#: EXACT: 0.01 mm is 10000 nm, and two vias 10000 nm apart ARE one group, 10001
#: are not. Comparing ``x/MM`` floats instead would make the boundary depend on
#: binary rounding (1.01 - 1.0 == 0.010000000000000009 > 0.01), i.e. a via
#: exactly ON the tolerance would be silently split off — measured, and the
#: reason the boundary cell below can pin both sides.
TOLERANCE_NM = int(round(POSITION_TOLERANCE_MM * MM))


def _layer_str(layer: BoardLayer) -> str:
    """Copper layer of a track, as KiCad spells it ('F.Cu'/'In1.Cu'/'B.Cu').

    Deliberately the shared mapper rather than the old tool's
    ``"B.Cu" if layer == BL_B_Cu else "F.Cu"`` ternary: the project's boards are
    4-copper-layer, and the binary form would print an inner-layer track as
    'F.Cu' (the same silent inner-layer collapse domain/geometry.py Step 0 and
    utils/layers.py exist to prevent)."""
    return layer_to_str(layer)


# ── grouping ────────────────────────────────────────────────────────────────

def _find_duplicate_groups(items: Sequence[T], key_fn: Callable[[T], tuple],
                           pos_fn: Callable[[T], Tuple[int, ...]],
                           tol_nm: int) -> list[list[T]]:
    """Groups of items sharing ``key_fn(item)`` whose ``pos_fn(item)``
    coordinates are all within ``tol_nm`` board units of the group's FIRST
    member.

    Returns only groups with 2+ members (the actual duplicates). Greedy, in the
    adapter's own order — see the module docstring for why that is enough and
    what it deliberately does not do."""
    by_key: dict[tuple, list[T]] = defaultdict(list)
    for it in items:
        by_key[key_fn(it)].append(it)

    duplicate_groups: list[list[T]] = []
    for bucket in by_key.values():
        groups: list[Tuple[Tuple[int, ...], list[T]]] = []
        for it in bucket:
            pos = pos_fn(it)
            for rep_pos, group_items in groups:
                if all(abs(a - b) <= tol_nm for a, b in zip(pos, rep_pos)):
                    group_items.append(it)
                    break
            else:
                groups.append((pos, [it]))
        duplicate_groups.extend(group_items for _, group_items
                                in groups if len(group_items) > 1)
    return duplicate_groups


def _via_pos_nm(via: Via) -> Tuple[int, int]:
    return (via.position.x, via.position.y)


def _via_pos_mm(via: Via) -> Tuple[float, float]:
    return (via.position.x / MM, via.position.y / MM)


def via_position_text(via: Via) -> str:
    """``'(1.0000, 2.0000)'`` — the position exactly as the REPORT prints it.

    Public and used by BOTH the report and the GUI table. Acceptance finding
    (plan_2026_10_01 §5.1): the panel used to assemble its own string, so a
    group's row and the text the panel copies named the same copper in a
    different order — the two views must not be able to disagree."""
    x_mm, y_mm = _via_pos_mm(via)
    return f"({x_mm:.4f}, {y_mm:.4f})"


def _via_key(via: Via) -> tuple:
    return (via.net_name,)


def find_via_duplicate_groups(vias: Sequence[Via]) -> list[list[Via]]:
    """Duplicate via groups: same net, position within POSITION_TOLERANCE_MM
    of the group's first member. Drill/diameter are not part of the key."""
    return _find_duplicate_groups(vias, _via_key, _via_pos_nm, TOLERANCE_NM)


def _track_pos_nm(track: Track) -> Tuple[int, int, int, int]:
    s = (track.start.x, track.start.y)
    e = (track.end.x, track.end.y)
    s, e = (s, e) if s <= e else (e, s)   # A->B and B->A are the same track
    return (s[0], s[1], e[0], e[1])


def _track_pos_mm(track: Track) -> Tuple[float, float, float, float]:
    """The SAME ordering as ``_track_pos_nm`` (derived from it, so the report
    and the grouping key can never disagree about which end is "start")."""
    sx, sy, ex, ey = _track_pos_nm(track)
    return (sx / MM, sy / MM, ex / MM, ey / MM)


def track_position_text(track: Track) -> str:
    """``'(1.0000,0.0000) -> (5.0000,0.0000)'`` — ends NORMALISED (the pair is
    unordered) exactly as the report prints them; public for the same reason as
    :func:`via_position_text` (a track stored (5,0)->(1,0) prints as (1,0)->(5,0)
    in BOTH the report and the panel's row — §5.1)."""
    sx, sy, ex, ey = _track_pos_mm(track)
    return f"({sx:.4f},{sy:.4f}) -> ({ex:.4f},{ey:.4f})"


def _track_key(track: Track) -> tuple:
    return (track.net_name, track.layer)


def find_track_duplicate_groups(tracks: Sequence[Track]) -> list[list[Track]]:
    """Duplicate track groups: same net and layer, (start, end) as an
    unordered pair within POSITION_TOLERANCE_MM."""
    return _find_duplicate_groups(tracks, _track_key, _track_pos_nm, TOLERANCE_NM)


def scan_copper_duplicates(adapter) -> tuple[list[list[Via]], list[list[Track]]]:
    """Refresh the live board and return (via_groups, track_groups).

    ``refresh_board()`` first: the whole point is ground truth, and a cached
    adapter would report what it already knew instead of what the board holds
    now."""
    adapter.refresh_board()
    return (find_via_duplicate_groups(adapter.get_vias()),
            find_track_duplicate_groups(adapter.get_tracks()))


# ── report ──────────────────────────────────────────────────────────────────

def via_sizes(group: Sequence[Via]) -> list[tuple[float, float]]:
    """The distinct (drill_mm, diameter_mm) pairs in a group — the warning's
    payload. Rounded like the old tool's report so the text is stable; public
    because the panel shows the same numbers (§5.1: it used to recompute them,
    so a change to the rounding here would have silently missed the table)."""
    return sorted({(round(v.drill_mm, 4), round(v.diameter_mm, 4))
                   for v in group})


def _format_via_group(group: Sequence[Via], lines: list[str]) -> None:
    net = group[0].net_name or "?"
    lines.append(f"  via net={net!r} @ {via_position_text(group[0])} mm - "
                 f"{len(group)} copies")
    sizes = via_sizes(group)
    if len(sizes) > 1:
        lines.append("    [warning] drill/diameter differ within this group: "
                     f"{sizes}")
    lines.append(f"    keep   {group[0].uuid}")
    for v in group[1:]:
        lines.append(f"    delete {v.uuid}")


def _format_track_group(group: Sequence[Track], lines: list[str]) -> None:
    net = group[0].net_name or "?"
    layer = _layer_str(group[0].layer)
    lines.append(f"  track net={net!r} layer={layer} @ "
                 f"{track_position_text(group[0])} mm - {len(group)} copies")
    lines.append(f"    keep   {group[0].uuid}")
    for t in group[1:]:
        lines.append(f"    delete {t.uuid}")


def format_report(via_groups: Sequence[Sequence[Via]],
                  track_groups: Sequence[Sequence[Track]]) -> str:
    """The ONE canonical report text — printed by the CLI and copied to the
    clipboard by the GUI panel ("Копировать"). Fixed English, no ``_()``; see
    the module docstring for why."""
    total = sum(len(g) - 1 for g in via_groups) + sum(len(g) - 1 for g in track_groups)
    if total == 0:
        return "No copper duplicates found."

    lines: list[str] = []
    if via_groups:
        lines.append(f"Duplicate via groups: {len(via_groups)}")
        for group in via_groups:
            _format_via_group(group, lines)
    if track_groups:
        lines.append(f"Duplicate track groups: {len(track_groups)}")
        for group in track_groups:
            _format_track_group(group, lines)
    lines.append(f"Total: {total} extra duplicate item(s); one copy per group "
                 "is kept.")
    return "\n".join(lines)


# ── removal ─────────────────────────────────────────────────────────────────

def _journal_removal(kind: str, removed, kept_uuid: str) -> None:
    """One audit line per deleted item — type, net, position, layer, the UUID
    removed and the UUID kept (plan 2026-10-01 §2.4). Without this the next
    investigation cannot tell a dupe that was cleaned from one that never
    existed."""
    net = removed.net_name or "?"
    if kind == "via":
        x_mm, y_mm = _via_pos_mm(removed)
        where = f"at=({x_mm:.4f}, {y_mm:.4f}) mm"
    else:
        sx, sy, ex, ey = _track_pos_mm(removed)
        where = (f"at=({sx:.4f}, {sy:.4f})->({ex:.4f}, {ey:.4f}) mm "
                 f"layer={_layer_str(removed.layer)}")
    _actions_logger.info(
        f"dedupe: removed {kind} uuid={removed.uuid} net={net!r} {where} "
        f"kept={kept_uuid}")


def apply_dedupe(adapter, via_groups: Sequence[Sequence[Via]],
                 track_groups: Sequence[Sequence[Track]]) -> int:
    """Delete every group's extra copies, keeping the first of each; journal
    each deletion to ACTION_LOGGER_NAME. Returns the number actually removed.

    A ``remove_by_id`` that reports False is NOT counted and IS warned about:
    counting it would make "Removed N" lie, which is exactly the kind of quiet
    disagreement this whole module exists to end."""
    removed_count = 0
    for group in via_groups:
        kept = group[0].uuid
        for via in group[1:]:
            if adapter.remove_by_id(via.uuid):
                _journal_removal("via", via, kept)
                removed_count += 1
            else:
                logger.warning(
                    "dedupe: adapter refused to remove via %s (net=%r) — kept %s",
                    via.uuid, via.net_name, kept)
    for group in track_groups:
        kept = group[0].uuid
        for track in group[1:]:
            if adapter.remove_by_id(track.uuid):
                _journal_removal("track", track, kept)
                removed_count += 1
            else:
                logger.warning(
                    "dedupe: adapter refused to remove track %s (net=%r) — kept %s",
                    track.uuid, track.net_name, kept)
    return removed_count


def apply_summary(removed: int, groups: int) -> str:
    """The user-facing one-liner after a removal run ("removed N, kept one per
    group in M groups") — one definition, so CLI and GUI say the same words.
    Translated: unlike the report body, this is a sentence a human reads in the
    Log, not a payload pasted into a bug report. ONE literal on purpose — an
    implicitly-concatenated msgid is the documented i18n grabli (see
    techdocs/me/i18troubles.md)."""
    from .i18n import _
    return _("Removed {removed} duplicate copper item(s); kept one per group in {groups} group(s).").format(removed=removed, groups=groups)
