# kicadstamp/internode_cell_copper.py
"""Cell copper of a tree's nodes — «which live copper already belongs to a cell's record».

Plan: plan_2026_10_23_internode_without_cell_copper. The inter-node classifier takes every piece
of copper between two nodes' pads; a "spoke"-shaped cell's own output via and its B.Cu leg sit
between the SAME two nodes, so they were selected and re-recorded as inter-node copper — measured
live on v103 (`ch0_dac_buf`, 22.10): one via and a B.Cu stretch of cell `pif_dvdd_channel_0`,
claimed by the `net_trace` of the link as well. Five DAC↔PIF links, probably everywhere a spoke
cell carries its own output via.

This module answers ONE question and cuts by its answer:

  * WHICH live copper a cell's record already owns, by TWO proofs that never overlap:
    - the REGISTRY KEY, through
      `absent_copper_prune.registry_record_copper_map(place_check=False, frame_check=False)`, the
      same ownership «Select cell» uses. A second ownership rule would drift from that one (the
      project already paid for two copies of `form_identity.replace_entry`);
    - the PLACE (08.11, plan_2026_11_08_internode_cell_copper_by_place): the registry writes a
      key only on REDRAW, so everything READ and not yet redrawn had no key and was handed to
      the inter-node classifier as its copper — measured live on HP-Power-v099, 20 of 83 pieces
      across three cells (dcdc24p15n5, driver, spoke_signal). A live piece AT a record's stored
      place — the XY inside the cell frame AND the copper layer — carrying that record's net, is
      the record's copper. A READING also SHIFTS the record numbers, so a KEY may point at
      another record's piece (`dcdc24p15n5` on SW A: 15 such keys, `track|17` leading to a
      GND_24V piece while SOURCE lay at its own place) — such a slot is asked by place too, while
      the piece the key found stays given back (a key is proof on its own, whatever its layer).
      The whole rule and its two gates live in `cell_copper_place`, and that pass only READS: the
      strict Subtract door and the registry write are not touched;
  * and `cut_cell_copper(unit, owned)` — the same `CopperUnit` with only the copper nobody owns.

Deliberately NOT here: the classification (a unit is still recognised pad-to-pad,
`classify_unit` keeps deciding what is INTERNODE) and any decision about writing — the caller
feeds the trimmed unit to the SAME `capture_unit`.

COST. `registry_record_copper_map` reads both registry files, the board's footprints and the
board's copper on EVERY call, and a tree has dozens of nodes. Here the registry files are read
ONCE, the board ONCE (`live_board_copper`), and both are passed back in through the seam — that is
what keeps «select» and «re-read» from becoming dozens of whole-board reads.

The copper is read from the WHOLE board even when the caller's area is the current selection: the
map answers «does this uuid belong to a cell», and the key's uuid may well sit outside the area.

`CopperUnit.pads` is the record's SIGNATURE (`capture_unit` builds `pads:` from it) and is never
touched — the re-read has to find the existing record by that signature. `track_pads`/`via_pads`
are aligned 1:1 with `tracks`/`vias` and are filtered WITH them.
"""
import dataclasses
from dataclasses import dataclass, field
from typing import Iterable, Optional

from .cell_copper_place import instance_frame, place_pass
from .i18n import _

__all__ = [
    "CellCopperMap",
    "cut_cell_copper",
    "duplicate_line",
    "live_board_copper",
    "registry_snapshot",
    "report_line",
    "subtract_units",
    "tree_cell_copper",
]


@dataclass
class CellCopperMap:
    """The cell copper of ONE tree: the uuids to leave alone, and what the report needs."""

    #: Live copper uuids a cell's record owns — the pieces the classifier must NOT take.
    uuids: set = field(default_factory=set)
    #: cell name -> how many pieces of its copper were found (report/notes, not a decision).
    by_cell: dict = field(default_factory=dict)
    tracks: int = 0
    vias: int = 0
    #: Pieces given back by PLACE alone (08.11): the registry key is written only on REDRAW, and
    #: a READING shifts the record numbers, so a record may have no key or a key pointing at a
    #: neighbour's piece. Its place still proves the copper is the cell's. Counted here so the
    #: report can say where the proof came from.
    by_place: int = 0
    #: This cell's RECORD SLOTS given back by NEITHER route (no live registry key, and no piece
    #: of their own net and layer at their place) — counted over slots as a set, so it can never
    #: go negative. Their copper is not given back and the report says the count.
    without_registry: int = 0
    #: Node keys whose entity names no cell: nothing to ask the registry about.
    skipped_nodes: int = 0
    warnings: list = field(default_factory=list)


def live_board_copper(adapter) -> dict:
    """The board's footprints/tracks/vias, read ONCE per run.

    Missing readers (a test double) yield empty lists — "no copper" is the honest answer there,
    the same rule `absent_copper_prune._live_items` follows."""
    def read(getter: str) -> list:
        fn = getattr(adapter, getter, None)
        return list(fn() or ()) if fn else []

    return {"footprint": read("get_footprints"),
            "via": read("get_vias"),
            "track": read("get_tracks")}


def registry_snapshot(config_path, cfg):
    """`(via_entries, track_entries, owner)` from BOTH registry files, read ONCE per run."""
    from .registry import load_registry_entries

    return load_registry_entries(config_path, cfg)


def _cell_by_node_key(cfg) -> dict:
    """`{(cluster, sheet): cell name}` — the node identity the classifier uses, mapped to the CELL
    its entity names.

    The key is `(entity.cluster, entity.sheet)`, exactly `tree_node_keys`' key: that walk keeps
    only labels and owners, and the cell has to come from the SAME entity record. An entity with
    no cell (or no cluster) contributes nothing — a tree node whose entity never named a cell has
    no registry to ask."""
    out: dict = {}
    for entity in (getattr(cfg, "entities", []) or []):
        cell = getattr(entity, "cell", None)
        if not cell:
            continue
        out.setdefault((getattr(entity, "cluster", None),
                        getattr(entity, "sheet", None)), cell)
    return out




def tree_cell_copper(adapter, cfg, config_path, tree, *, sheet_names=None,
                     live=None, registry=None) -> CellCopperMap:
    """The tree's cell copper — every node's own registry-keyed copper, collected once.

    `config_path` is the profile's config FILE (the same path every door takes); `None` means
    "no profile" and yields an EMPTY map plus a warning — the caller then subtracts nothing and
    says so, it never silently behaves as before.
    """
    out = CellCopperMap()
    if config_path is None:
        out.warnings.append(_(
            "the profile is not known — cell copper is NOT subtracted: copper that belongs to a "
            "cell's record is taken as inter-node copper"))
        return out

    # Lazy: `internode_capture` imports THIS module for the classifier, so a module-level import
    # here would be a cycle. Same reason `absent_copper_prune` imports its neighbors inside.
    from .absent_copper_prune import registry_record_copper_map
    from .internode_capture import tree_node_keys

    cells = _cell_by_node_key(cfg)
    if live is None:
        live = live_board_copper(adapter)
    if registry is None:
        try:
            registry = registry_snapshot(config_path, cfg)
        except Exception as e:  # noqa: BLE001 — an unreadable registry is a NO, not a crash
            # The registry loader REFUSES a legacy key on a format-3 config (a name key would
            # split one registry into two), and a broken pair of files must not take the whole
            # "Select/Reread" click down: nothing is given back, and the warning says why.
            out.warnings.append(_(
                "cell copper could not be read ({err}) — nothing is given back to cells").format(
                err=e))
            return out

    from .absent_copper_prune import cell_record_slots
    from .cell_frame import RIGID_TOLERANCE_MM

    # The snapshot's copper indexed by uuid ONCE per run: the place pass has to know WHERE the
    # piece a KEY found stands (a reading shifts the record numbers, so a key may point at a
    # neighbour's piece), and that question must not cost a second board read.
    live_by_uuid = {getattr(item, "uuid", None): item
                    for kind in ("via", "track") for item in (live.get(kind) or ())
                    if getattr(item, "uuid", None)}

    # Cells whose place pass could not run are named ONCE each, never once per node: the same
    # cell usually owns several nodes of a tree.
    place_refused: set = set()

    for key in tree_node_keys(tree, cfg):
        cell = cells.get(key)
        if not cell:
            out.skipped_nodes += 1
            continue
        cluster, sheet = key
        try:
            record_map = registry_record_copper_map(
                adapter, str(config_path), cfg, cell, cluster, sheet,
                sheet_names=dict(sheet_names or {}), place_check=False,
                live=live, registry=registry, frame_check=False)
        except Exception as e:  # noqa: BLE001 — one unaskable cell is not the end of the run
            # A record without a uuid is refused by the registry layer under format 3 (it would
            # split one registry into name- and uuid-keys). That is a CONFIG problem, not this
            # page's: the cell's copper is simply not given back, and it is said ONCE per cell.
            if cell not in out.by_cell:
                out.warnings.append(_(
                    "cell {cell!r}: its copper could not be asked ({err}) — it is not given "
                    "back to the cell").format(cell=cell, err=e))
            out.skipped_nodes += 1
            continue
        found = 0
        for (kind, role_part, index) in record_map.by_record:
            uuid = record_map.live_uuid(kind, role_part, index)
            if not uuid:
                continue
            out.uuids.add(uuid)
            found += 1
            if kind == "via":
                out.vias += 1
            else:
                out.tracks += 1
        if found:
            out.by_cell[cell] = out.by_cell.get(cell, 0) + found

        # ── SECOND PASS: by PLACE (08.11) ────────────────────────────────────────────────
        # The registry writes a key only on REDRAW, so everything read and not yet redrawn
        # had no key and was handed to the inter-node classifier as its copper (measured on
        # HP-Power-v099: 20 of 83 pieces, three cells). A READING also shifts the record
        # numbers, so a key may point at a neighbour's piece: such a slot is asked by place
        # too. The rule and its two gates (frame XY + layer) live in `cell_copper_place`.
        cell_obj = (getattr(cfg, "cells", {}) or {}).get(cell)
        slots = cell_record_slots(cell_obj)
        keyed = {slot: uuid for slot in slots
                 if (uuid := record_map.live_uuid(*slot))}
        answered = set(keyed)
        if slots and live.get("footprint"):
            frame = None
            try:
                from .cell_instance import resolve_context_footprints

                own_footprints = resolve_context_footprints(
                    adapter, live.get("footprint") or (), cluster, sheet,
                    dict(sheet_names or {}))
                frame = instance_frame(cell_obj, own_footprints, adapter)
            except Exception:  # noqa: BLE001 — "no frame" is a UI state, not a crash
                frame = None
            keyless = {slot for slot in slots if slot not in keyed}
            if frame is None or frame.residual_mm > RIGID_TOLERANCE_MM:
                # ONLY a slot with no live key needs a word: its proof is missing altogether.
                # A live key keeps its piece back (with a non-rigid frame the place of that
                # piece simply cannot be checked), so keys alone raise no line.
                if keyless and cell not in place_refused:
                    place_refused.add(cell)
                    if frame is None:
                        out.warnings.append(_(
                            "cell {cell!r}: the place of its records cannot be measured on "
                            "this instance — its copper is not given back by place (a "
                            "registry key is the only proof left)").format(cell=cell))
                    else:
                        out.warnings.append(_(
                            "cell {cell!r}: its cell frame is not rigid (residual "
                            "{residual} mm) — its copper is not given back by place (a "
                            "registry key is the only proof left)").format(
                                cell=cell, residual=round(frame.residual_mm, 3)))
            else:
                pass_ = place_pass(cell_obj, slots, keyed, live, live_by_uuid, frame,
                                   out.uuids, RIGID_TOLERANCE_MM)
                out.uuids |= pass_.found
                out.tracks += pass_.tracks
                out.vias += pass_.vias
                out.by_place += len(pass_.found)
                answered |= pass_.answered
                if pass_.found:
                    out.by_cell[cell] = out.by_cell.get(cell, 0) + len(pass_.found)
        # The report's "not given back" count: a record answered by NEITHER route, counted over
        # the SLOTS as a SET — never a subtraction of pieces, which could go negative. A slot
        # whose place holds a matching piece is answered even when that piece was already given
        # to another record, so the report never says "no piece at their place" about a place
        # that has one.
        out.without_registry += len(slots) - len(answered)
    return out


def cut_cell_copper(unit, owned: Iterable[str]) -> tuple:
    """(the unit without the copper `owned` holds, how many pieces were given back).

    `pads` is the record's signature and is returned UNCHANGED; `track_pads`/`via_pads` are
    filtered together with their items, because `capture_unit` reads them by position. A unit with
    nothing to give back is returned as the SAME object — the classifier's own instance, no copy.
    """
    owned = owned if isinstance(owned, (set, frozenset)) else set(owned or ())
    if not owned or (not unit.tracks and not unit.vias):
        return unit, 0

    tracks, track_pads = [], []
    for item, pads in zip(unit.tracks, unit.track_pads):
        if getattr(item, "uuid", None) in owned:
            continue
        tracks.append(item)
        track_pads.append(pads)
    vias, via_pads = [], []
    for item, pads in zip(unit.vias, unit.via_pads):
        if getattr(item, "uuid", None) in owned:
            continue
        vias.append(item)
        via_pads.append(pads)

    given = (len(unit.tracks) - len(tracks)) + (len(unit.vias) - len(vias))
    if not given:
        return unit, 0
    return dataclasses.replace(unit, tracks=tracks, vias=vias,
                               track_pads=track_pads, via_pads=via_pads), given


def subtract_units(adapter, cfg, tree, config_path, units, *, sheet_names=None) -> tuple:
    """Give the CELL copper back to the cells before either door sees the units.

    Rule (plan_2026_10_23_internode_without_cell_copper §2): the classification stays pad-to-pad,
    and from every INTERNODE unit the copper a cell's RECORD owns is removed; `pads` (the record's
    signature) is untouched, so the re-read still matches the existing record. The copper is read
    from the WHOLE board, even when the area is a selection — the ruling uuid may lie outside it.

    `config_path is None` is "no profile": nothing is subtracted and the warning says so — never
    silent, because a silently unchanged run is exactly how the defect looked.

    Returns `(kept units, the map, pieces given back, pieces that were cell copper entirely, the
    report lines, the dropped units)`. The lines go into the caller's warning list, which is what
    both doors print; the dropped units are returned so the RE-READ can tell an entry that now
    duplicates a cell's copper from one whose copper is gone (cell copper is on the board).
    """
    mapping = tree_cell_copper(adapter, cfg, config_path, tree,
                               sheet_names=sheet_names)
    lines = list(mapping.warnings)
    if config_path is None:
        return list(units), mapping, 0, 0, lines, []

    kept: list = []
    dropped: list = []
    given_total = 0
    whole = 0
    for unit in units:
        trimmed, given = cut_cell_copper(unit, mapping.uuids)
        if given and not trimmed.tracks and not trimmed.vias:
            # Cell copper ENTIRELY: neither selected nor written, only counted (rule 5).
            whole += 1
            dropped.append(unit)
            continue
        given_total += given
        kept.append(trimmed)
    line = report_line(mapping, whole)
    if line:
        lines.append(line)
    return kept, mapping, given_total, whole, lines, dropped


def duplicate_line(identity: str) -> str:
    """The report sentence for a `net_traces:` entry that now duplicates a cell's copper.

    Д-2 of the Ч1 acceptance (23.10): the piece is still on the board, its copper simply belongs
    to a cell's record now — so the re-read must NOT call it "no longer on the board". What the
    human needs is the fact and the decision left to him.
    """
    return _(
        "net_traces entry (net {net!r}) duplicates cell copper — that copper now belongs to a "
        "cell's record; delete the entry if it is not needed").format(net=identity)


def report_line(mapping: Optional[CellCopperMap], whole_pieces: int) -> Optional[str]:
    """The Log/report sentence for a run, or None when there is nothing to say.

    Four facts, each elsewhere invisible: how much copper was given back to cells, how many
    pieces of the area turned out to be cell copper ENTIRELY (taken by neither door), how many
    pieces the PLACE pass gave back (their record has no registry key — the cell was read and
    never redrawn — or a key pointing at another piece), and how many records NEITHER route could
    answer (their copper is not given back, said rather than silently ignored).

    The last clause must not claim «no live piece at their place»: a record whose place holds a
    piece already given to ANOTHER record is answered, and is not counted here at all — the count
    comes from the slots as a set (`cell_copper_place.PlacePass.answered`)."""
    if mapping is None:
        return None
    if not mapping.uuids and not whole_pieces and not mapping.without_registry:
        return None
    return _(
        "cell copper left to cells: {tracks} tracks, {vias} vias across {cells} cell(s); "
        "{whole} piece(s) are cell copper entirely; {by_place} piece(s) were given back by "
        "PLACE — no registry key, or a key pointing at another piece; {nokey} cell record(s) "
        "were not given back at all — no live registry key, and no piece of their own net and "
        "layer at their place").format(
        tracks=mapping.tracks, vias=mapping.vias, cells=len(mapping.by_cell),
        whole=whole_pieces, by_place=mapping.by_place, nokey=mapping.without_registry)
