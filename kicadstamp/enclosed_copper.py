# kicadstamp/enclosed_copper.py
"""«Select enclosed copper» — the copper between the components of ONE cell
instance (plan ``plan_2026_10_05_select_enclosed_copper.md``; Denis 2026-10-05,
reworked by С1 after his live check).

Denis: «добавить пункт меню "Выделить замкнутую медь" — выделить медь между
компонентами кластера»; «Merge: "Выделить кластер" — выделится всё, как прочитано
[that is the existing "Select cell"]; "Выделить замкнутую медь" — там только медь
между падами компонент кластера». On the FPGA board the cell's copper is mixed
with the hand-routed buses to three DACs, so picking the cell's own copper by
hand is slow and error-prone. The chain is: "Select enclosed copper" →
"Re-read by selection".

The rule (Qt-free, no board handle of its own):

* the instance's components come from the caller (the board footprints of the
  (cluster, sheet) instance, resolved by ``resolve_context_footprints`` — the ONE
  instance rule, never a copy here);
* a CONNECTED piece of copper is TAKEABLE when it touches
  ``MIN_INSTANCE_PADS`` DISTINCT instance pads (TWO by default: a piece reaching
  only ONE pad is a dead-end, «висячая дорожка», not "the copper between the
  cluster's pads") and touches NO foreign pad — of any other component on the
  board (another cluster, another instance of the same cell, a component without
  a Cluster such as a connector, a spoke capacitor);
* inside a takeable piece the branches that HANG off it are TRIMMED — see
  ``prune_dangling`` below and in ``explode_connectivity`` (С1-2).

A piece that reaches a foreign pad is no longer dropped WHOLE: it is CARVED
(``plan_2026_10_09_enclosed_copper_carve``, Denis 2026-10-09: «выбираю "Select
enclosed copper", но линия FPGA pin 105 ⇒ R41 pin 1 не выделяется»). The parts
that reach the foreign pad are cut off exactly like any other dangling branch
(``prune_dangling`` — an instance pad is the only anchor), and the remainder is
re-checked with the SAME rule (``copper_pieces`` again): every sub-piece that
touches ≥ ``MIN_INSTANCE_PADS`` DISTINCT instance pads and NO foreign pad is
taken. The re-check is the ONE guard of the carve — it catches a piece that
EMPTIED to nothing but the copper reaching a foreign pad (``prune_dangling``
returns such a piece whole under its С2-3 leg) as well as a foreign pad BETWEEN
two instance pads: A — F — B holds both tracks up, so the pruning never sees them
dangle, yet the remainder still touches F and nothing is taken.

Connectivity is NOT re-invented: ``kicadstamp/explode_connectivity.py``
(``copper_pieces`` / ``prune_dangling``) over ``geometry/copper_connect``
(capsules, discs, T-joints, µm gaps, track-track on their own layer, a pad by its
own area in its own axes — the "copper connectivity on the BOARD is by SHAPE"
contract). There is no own union-find and no own "does it touch".
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .constants import CLUSTER_FIELD_NAME
from .domain.board import Via
from .explode_connectivity import (
    build_cell_islands,
    cell_pad_areas,
    cell_pads,
    copper_pieces,
    foreign_labels,
    pad_areas,
    prune_dangling,
)

__all__ = ["EnclosedResult", "enclosed_copper", "MIN_INSTANCE_PADS"]

# A piece is "the copper BETWEEN the cluster's pads" only when it reaches TWO
# DIFFERENT instance pads (С1-1; Denis 2026-10-05 after the live FPGA check: «у
# меня там есть висячие дорожки… они ну никак не относятся к "закрытой меди"»).
# The islands are built WITHOUT the cell's own copper, so one island == one
# instance pad (``build_cell_islands`` labels them ``cell:0``, ``cell:1``, ...);
# this count is therefore the number of DISTINCT instance pads a piece touches,
# read off the piece's classes as the distinct ``cell:<i>`` labels
# (``cell_pads``). The menu always calls with the default; the parameter stays so
# the guard cells can pin the single-pad behaviour (``min_cell_pads=1``).
MIN_INSTANCE_PADS = 2


@dataclass
class EnclosedResult:
    """What «Select enclosed copper» highlights, plus the honest counters.

    The three "not taken" counters are about THIS INSTANCE only (С2-1): a piece
    that touches NO instance pad is not a candidate at all and is not counted
    (counting the whole board made «к чужим падам» read ~400 on the FPGA, almost
    none of it related to the instance).

    ``items`` — the instance's components FOLLOWED BY the copper to select (the
    takeable pieces, hanging branches already trimmed) — the ONE list
    ``select_items`` takes.
    ``pieces`` — how many copper pieces were taken.
    ``carved`` — how many pieces were taken PARTIALLY (a CARVE, plan
        ``plan_2026_10_09_enclosed_copper_carve``): they reached a foreign pad,
        the foreign-reaching branches were cut off, and at least one remainder
        piece was taken.
    ``not_taken_foreign`` — pieces touching an instance pad AND a foreign pad
        from which NOTHING could be carved (the remainder emptied, fell below
        ``min_cell_pads``, or still touched a foreign pad — A — F — B).
    ``not_taken_dangling`` — pieces touching an instance pad but FEWER than
        ``min_cell_pads`` of them (a one-pad dead-end under the default 2).
    ``pruned`` — hanging elements trimmed off the taken pieces (С1-2), plus the
        elements cut off by a carve that DID take at least one piece.
    """

    items: list = field(default_factory=list)
    pieces: int = 0
    carved: int = 0
    not_taken_foreign: int = 0
    not_taken_dangling: int = 0
    pruned: int = 0


def _foreign_class_fps(adapter, board_footprints, instance_fps) -> dict:
    """{label: [footprints]} for EVERY board component EXCEPT the instance.

    The label is the component's Cluster when it has one (components of the same
    cluster share it); a component WITHOUT a Cluster gets its OWN label — so a
    connector / test point / spoke capacitor is FOREIGN too, exactly like a
    component of another cluster.
    """
    instance_uuids = {getattr(fp, "uuid", None) for fp in instance_fps}
    out: dict = {}
    for fp in board_footprints:
        if getattr(fp, "uuid", None) in instance_uuids:
            continue
        cluster = adapter.get_field_value(fp, CLUSTER_FIELD_NAME) or None
        if cluster:
            label = str(cluster)
        else:
            label = f"fp:{getattr(fp, 'ref', None) or getattr(fp, 'uuid', '?')}"
        out.setdefault(label, []).append(fp)
    return out


def _split_copper(items) -> tuple:
    """``(tracks, vias)`` of a mixed copper list — ``copper_pieces`` wants them
    apart, and the carve remainder comes back as one mixed list."""
    return ([it for it in items if not isinstance(it, Via)],
            [it for it in items if isinstance(it, Via)])


def _take_remainder(kept, islands, foreign_pads, min_cell_pads) -> tuple:
    """The TAKEABLE pieces of a CARVED remainder (plan
    ``plan_2026_10_09_enclosed_copper_carve``).

    Re-runs the ONE owner (``copper_pieces``) over ``kept`` with the SAME islands
    and foreign pads, then keeps every sub-piece touching ≥ ``min_cell_pads``
    DISTINCT instance pads and NO foreign pad. This is the only place a foreign
    pad BETWEEN two instance pads (A — F — B) is caught: the pruning cannot see
    it as dangling, but the re-classified remainder still touches F. Returns
    ``(sub_items, taken_pieces)``."""
    tracks, vias = _split_copper(kept)
    taken_items: list = []
    taken = 0
    for sub in copper_pieces(tracks, vias, islands, foreign_pads):
        if foreign_labels(sub.classes):
            continue
        if len(cell_pads(sub.classes)) < min_cell_pads:
            continue
        taken_items.extend(sub.items)
        taken += 1
    return taken_items, taken


def enclosed_copper(adapter, instance_fps,
                    min_cell_pads: int = MIN_INSTANCE_PADS) -> EnclosedResult:
    """The copper enclosed by ``instance_fps`` (the components of ONE instance).

    ``instance_fps`` is the board footprints of the (cluster, sheet) instance —
    the caller resolves them with ``resolve_context_footprints`` (the ONE
    instance rule). An empty instance selects nothing, and reports nothing
    dropped. The board is read through the adapter (the worker's own adapter).

    A piece reaching a foreign pad is CARVED, not dropped whole: prune it against
    the instance pads only (an EMPTY result is not taken — ``keep_whole_when_
    emptied=False``), then re-check the remainder with the same rule. Nothing
    here re-implements connectivity — ``copper_pieces`` / ``prune_dangling`` /
    ``foreign_labels`` / ``cell_pads`` are the only judges of touching."""
    instance_fps = list(instance_fps or ())
    if not instance_fps:
        return EnclosedResult()

    tracks = list(adapter.get_tracks() or [])
    vias = list(adapter.get_vias() or [])
    instance_pads = cell_pad_areas(adapter, instance_fps)
    islands = build_cell_islands(instance_pads, [])
    foreign_pads = pad_areas(adapter, _foreign_class_fps(
        adapter, adapter.get_footprints() or [], instance_fps))

    result = EnclosedResult(items=list(instance_fps))
    for piece in copper_pieces(tracks, vias, islands, foreign_pads):
        pads_touched = cell_pads(piece.classes)
        if not pads_touched:
            # No instance pad: this piece is not a candidate for THIS instance
            # and is not counted (С2-1 — the whole board is NOT the question).
            continue
        if foreign_labels(piece.classes):
            # CARVE: the branches that reach the foreign pad are cut off as
            # dangling (an instance pad is the ONLY anchor), then the remainder
            # is re-checked with the SAME rule — anything still touching a
            # foreign pad is NOT taken. That ONE guard covers both the emptied
            # remnant (``prune_dangling`` hands it back whole under С2-3) and the
            # foreign pad BETWEEN two instance pads (A — F — B).
            kept, removed = prune_dangling(piece.items, instance_pads)
            taken_items, taken = _take_remainder(kept, islands, foreign_pads,
                                                 min_cell_pads)
            if not taken:
                # Nothing survived the re-check. No carve, so no ``pruned``
                # either — the piece was not taken at all.
                result.not_taken_foreign += 1
                continue
            result.pruned += removed
            result.carved += 1
            result.pieces += taken
            result.items.extend(taken_items)
            continue
        if len(pads_touched) < min_cell_pads:
            result.not_taken_dangling += 1
            continue
        kept, removed = prune_dangling(piece.items, instance_pads)
        result.pruned += removed
        result.pieces += 1
        result.items.extend(kept)
    return result
