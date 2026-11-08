# kicadstamp/cell_copper_place.py
"""The PLACE proof of cell copper — a live piece standing where a cell record puts it.

Plan: plan_2026_11_08_internode_cell_copper_by_place (§2 of its acceptance, 08.11). The
registry writes a key only on REDRAW, so a cell that was READ and never redrawn has no key
at all, and its copper was handed to the inter-node classifier as its own — measured live on
HP-Power-v099. The place is the second proof, and it only READS.

WHY A KEYED SLOT IS STILL ASKED. Reading a cell SHIFTS the record numbers, so a key may
point at a piece of another record of the same cell (measured: `dcdc24p15n5` on SW A had 15
such keys; `track|17` led to a GND_24V piece while SOURCE lay exactly at its own place — that
piece kept being selected as inter-node copper). So a slot is asked by place when it has NO
live key OR when the piece its key found does not STAND AT the record's place. The keyed
piece itself stays given back either way: the KEY is proof on its own — the copper was moved
to another layer, its uuid is the same, and it is still the cell's copper. Net and layer are
never asked of a key.

WHAT «AT THE PLACE» MEANS: the XY inside the cell frame (`pair_agrees_with_record`, the very
comparison the registry path uses, at `RIGID_TOLERANCE_MM`) AND the copper LAYER. The layer
is asked because the place is the only proof here: an `F.Cu` record would otherwise take the
`B.Cu` piece lying exactly underneath it — a two-sided power track, and a record naming its
net through `net_from_role` shows it is the same net too.

MIRRORING. A record's `layer` is stored in the CELL's own frame: `None` means the cell's own
layer. A MIRRORED instance stands on the other side of the same board, so the expected layer
goes through `utils.layers.mirror_layer` with the frame's own mirror — F.Cu <-> B.Cu, an inner
layer unchanged (design Р15/Р16). A via has no layer at all.

AN UNKNOWN LAYER NEVER ACCUSES: no layer on the record AND none on the cell, or a live layer
that is not copper at all — the piece is then judged by XY and net alone. That is the leniency
`registry_match.same_net` applies to a missing net, and the reason is the same: a datum the
caller does not have must not take copper away from its own record.

COUNTERS. `answered` is a SET of slots: a slot whose place holds a matching piece is answered
even when that piece was already given to an earlier record (that is what the final "not given
back at all" count is built from — a subtraction over pieces could not say it). The piece
itself is counted ONCE, through the caller's `taken` set.

READ-ONLY: no board access, no registry access, no config write. The frame is built from the
footprints the run already read; the pieces come from the board snapshot it already has.
"""
from __future__ import annotations

from dataclasses import dataclass, field

__all__ = [
    "PlacePass",
    "expected_layer",
    "instance_frame",
    "layer_agrees",
    "piece_at_the_place",
    "piece_matches",
    "place_pass",
    "slots_asked",
]


@dataclass
class PlacePass:
    """What ONE cell's place pass found.

    `asked` — the slots asked by place (no live key, or a key pointing elsewhere);
    `keyless` — the subset with no live key AT ALL (the only case the caller warns about);
    `answered` — the slots whose place holds a matching piece, given or already taken;
    `found` — the live uuids newly given back, `tracks`/`vias` how many of each.
    """

    asked: set = field(default_factory=set)
    keyless: set = field(default_factory=set)
    answered: set = field(default_factory=set)
    found: set = field(default_factory=set)
    tracks: int = 0
    vias: int = 0


def instance_frame(cell, footprints, adapter):
    """The cell frame of ONE live instance, built by the PUBLIC wrapper over the very
    construction «Refresh geometry from selection» uses (`cell_frame_from_live`), or None
    when this instance cannot be measured (no role of the cell among its footprints —
    nothing to fit the frame against).

    `footprints` is the instance's OWN live component list (`resolve_context_footprints`),
    already read by the caller: the frame needs `adapter.get_field_value` only, never a
    second whole-board read. READ-ONLY.

    The CELL'S LAYER is handed down (`cell_frame_from_live(cell_layer=...)`), so the frame's
    mirror is the side the instance PHYSICALLY stands on — `cell_frame.py` forbids a frame to
    CHOOSE its mirror from geometry, and a cell of two parts (7 of the 13 nodes on HP-Power)
    gives a fit that cannot tell the two sides apart: the XY and the LAYER of every record
    would then be checked against a frame that may be flipped. A cell naming no layer keeps
    the historical free fit (None)."""
    from .cell_geometry_refresh import cell_frame_from_live
    from .cell_origin import cell_slot_dicts

    if not footprints:
        return None
    return cell_frame_from_live(cell_slot_dicts(cell), list(footprints), adapter,
                                origin_role=getattr(cell, "anchor_role", None),
                                cell_layer=getattr(cell, "layer", None))


def expected_layer(record, cell, frame):
    """The live copper-layer NAME a track record's piece must show, or None when that cannot
    be said at all (nothing named on either side, or a name that is not a copper layer).

    `record.layer` when it has one, else the CELL's layer — the same inheritance the clone
    path applies (a slot without its own layer takes the cell's). A mirrored instance stands
    on the other side, so the answer is `utils.layers.mirror_layer` of that name: F.Cu <->
    B.Cu, an inner layer unchanged (design Р15/Р16 — mirroring is a SIDE, not a stackup
    flip). Both the parse and the name are the `utils.layers` ones, never a second table."""
    from .utils.layers import layer_from_str_strict, layer_to_str, mirror_layer

    name = getattr(record, "layer", None) or getattr(cell, "layer", None)
    if not name:
        return None
    try:
        name = layer_to_str(layer_from_str_strict(str(name)))
    except ValueError:
        return None                      # not a copper layer name: nothing to accuse with
    return mirror_layer(name) if getattr(frame, "mirror", False) else name


def layer_agrees(item, record, cell, frame) -> bool:
    """True <=> nothing contradicts the LAYER of this piece.

    A VIA has no layer (a point through the stack), so it always agrees. A track agrees when
    the layer it lies on is the one `expected_layer` names — and when either side cannot be
    read at all, which is never an accusation (see the module docstring)."""
    from .utils.layers import layer_to_str

    live_layer = getattr(item, "layer", None)
    if live_layer is None:
        return True
    expected = expected_layer(record, cell, frame)
    if expected is None:
        return True
    try:
        actual = layer_to_str(live_layer)
    except ValueError:
        return True                      # not copper: still nothing to accuse with
    return actual == expected


def piece_at_the_place(points, item, record, cell, frame, tol_mm) -> bool:
    """True <=> the piece STANDS WHERE THE RECORD PUTS IT: the XY inside the frame
    (`pair_agrees_with_record`, the ONE comparison of the registry path) and the layer.

    The NET is deliberately NOT asked here: this is the test of a piece a KEY found, and the
    key is proof on its own — a re-routed net on that same copper does not make it somebody
    else's. The place pass asks the net separately, in `piece_matches`."""
    from .absent_copper_prune import pair_agrees_with_record

    if not pair_agrees_with_record(points, item, frame, tol_mm):
        return False
    return layer_agrees(item, record, cell, frame)


def piece_matches(points, item, record, cell, frame, tol_mm) -> bool:
    """True <=> this piece may be GIVEN to this record by place: it stands at the place
    (`piece_at_the_place`) AND carries the record's net.

    `same_net` is the SELECT door's own leniency (a record naming its net through
    `net_from_role` cannot be resolved without the run's clone params, so a side without a
    net accuses nobody) — never a second net rule."""
    from .registry_match import same_net

    if not piece_at_the_place(points, item, record, cell, frame, tol_mm):
        return False
    return same_net(item, record, getattr(record, "net", None))


def _slot_parts(cell, slot):
    """`((points, record))` of one slot — `((None, None))` for an ORPHAN slot (a record the
    cell no longer holds: a number that shifted past the end of the list)."""
    from .absent_copper_prune import record_of, record_points

    kind, role_part, index = slot
    return record_points(cell, kind, role_part, index), record_of(cell, kind, role_part, index)


def slots_asked(cell, slots, keyed, live_by_uuid, frame, tol_mm) -> tuple:
    """`(asked, keyless)` — which slots the place pass must ask, and which of them have no
    live key at all.

    A slot is asked when it has NO live key, or when the piece its key found does not stand
    at the record's place. A slot whose keyed piece DOES stand there is NOT asked: the key
    already gave that piece back, and asking again would let it take a NEIGHBOURING piece
    that lies within the tolerance (the records of one cell are a few tenths of a millimetre
    apart)."""
    asked: set = set()
    keyless: set = set()
    for slot in sorted(slots):
        uuid = keyed.get(slot)
        if not uuid:
            keyless.add(slot)
            asked.add(slot)
            continue
        points, record = _slot_parts(cell, slot)
        item = live_by_uuid.get(uuid)
        if (points is None or record is None or item is None
                or not piece_at_the_place(points, item, record, cell, frame, tol_mm)):
            asked.add(slot)
    return asked, keyless


def place_pass(cell, slots, keyed, live, live_by_uuid, frame, taken, tol_mm) -> PlacePass:
    """Run ONE cell's place pass over its slots and answer a `PlacePass`.

    `keyed` — `{slot: live_uuid}` from the registry map (a key with no live piece is absent);
    `live` — the board snapshot `{"via": [...], "track": [...]}`; `live_by_uuid` — the same
    snapshot indexed once per run; `frame` — a RIGID frame (a non-rigid one must not be
    passed: the caller refuses every pair then, with the residual as the reason); `taken` —
    the uuids already given back, updated IN PLACE so one piece is never counted twice.

    A slot finds at most ONE piece: the first of its kind that stands at its place, carries
    its net and lies on its layer. When that piece is already taken, the slot is still
    ANSWERED (its place is demonstrably occupied by its own copper) — only the piece is not
    counted again; a later piece at the same place may still be its own and is given then."""
    out = PlacePass()
    out.asked, out.keyless = slots_asked(cell, slots, keyed, live_by_uuid, frame, tol_mm)
    for slot in sorted(out.asked):
        kind, role_part, index = slot
        points, record = _slot_parts(cell, slot)
        if points is None or record is None:
            continue                     # an ORPHAN slot puts its copper nowhere
        occupied = False
        for item in (live.get(kind) or ()):
            if not piece_matches(points, item, record, cell, frame, tol_mm):
                continue
            occupied = True
            uuid = getattr(item, "uuid", None)
            if uuid is not None and uuid not in taken:
                taken.add(uuid)
                out.found.add(uuid)
                if kind == "via":
                    out.vias += 1
                else:
                    out.tracks += 1
                break
        if occupied:
            out.answered.add(slot)
    return out
