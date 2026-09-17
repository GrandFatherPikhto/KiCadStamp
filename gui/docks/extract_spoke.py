# gui/docks/extract_spoke.py
"""Qt-free glue for Tools -> "Extract spoke..." — stage 5 of the spoke work
(plan_2026_09_17_spoke_s5_extract_spoke.md §2 Р2-Р5, §3 Door; design
design_2026_09_17_spoke_cell_editing.md §0/§1/§2.5).

Two halves, both without Qt (the same shape gui/docks/tree_from_selection.py has
for "Extract tree..."):

  * read_spoke_context(adapter, cfg, sheet_names, root_path) — the dialog's
    WORKER: ONE board read, board refreshed first (door П3.1), collecting
    everything the dialog shows — the selection and its refusals, the spoke
    evidence, the cell candidates with the frame of each PLACED instance, every
    chain on the selection's non-plane nets with its resolved anchor and pads,
    the foreign pads a brand-new chain could anchor to, and the ORDER of the
    component pools. Plain records come back, so every decision the dialog makes
    runs through kicadstamp/spoke_extraction's pure functions on the UI thread —
    clicking a pad or a cell never needs a second board read.

  * write_spoke_extraction(...) — what OK performs, also in the worker: re-read
    the selection and compare it with the pair the dialog was built on (Р6: a
    stale dialog must NOT write), extract the cell when it is new, then write
    `cells:` and the chain — with everything validated (stage_spoke_write) before
    the first file is touched.

The pool ORDER is read here, once, with the real ComponentPool and handed over as
data: the pool is a board read (Role/Cluster fields + pad nets), while the
dialog must answer "which pair will this spoke get" for every pad, cell and chain
the user can still pick. OrderedPool replays that exact consumption on the UI
thread (tests/test_spoke_extraction.py pins the replay against a real pool).
"""
import logging
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from kicadstamp.cluster_matching import cluster_prefix_match
from kicadstamp.config import load_chain
from kicadstamp.config_writer import read_data, upsert_list_entry, write_data
from kicadstamp.constants import CLUSTER_FIELD_NAME, ROLE_FIELD_NAME
from kicadstamp.domain.board import Footprint
from kicadstamp.exceptions import ValidationError
from kicadstamp.i18n import _
from kicadstamp.placement.services.component_resolver import ComponentResolver
from kicadstamp.sheet_names import resolve_sheet_path_names
from kicadstamp.spoke_extraction import (
    PadPoint,
    SpokeSelection,
    check_spoke_selection,
    pads_by_distance,
    spoke_criterion,
    split_plane_nets,
    stage_spoke_write,
)
from kicadstamp.template_extraction import extract_template_from_selection
from kicadstamp.utils.safe_write import backup_file
from kicadstamp.utils.units import MM

from ..cell_identification import KIND_SPOKE, Identification
from .live_position import _live_cluster_frame
from .rename import collect_chains_by_net

logger = logging.getLogger(__name__)


def chain_identity(entry: dict) -> Any:
    """upsert_list_entry's key_fn for chains: — the raw-dict twin of
    chain_effective_name (name if set, else net), exactly like ChainDock's own
    `_chain_identity`, so a rewritten chain replaces its own record in place."""
    return (entry or {}).get("name") or (entry or {}).get("net")


# ── what the worker hands the dialog ───────────────────────────────────────

@dataclass(frozen=True)
class CellFrame:
    """Where a PLACED instance of a cell stands: the world position of the
    cell's own origin, the instance's rotation and its mirror flag (read by
    _live_cluster_frame from the identified pair)."""
    origin_x_mm: float
    origin_y_mm: float
    rotation_deg: float
    mirror: bool


@dataclass(frozen=True)
class CellChoice:
    """One row of the dialog's cell combo: an EXISTING cell with the same role
    set (its frame read from the board, where possible) or the "new cell"
    entry the pair would create. `problem` is set when this choice cannot be
    used for THIS pair (a mirrored instance — ManualSpoke has no mirror field,
    so a spoke could never reproduce it)."""
    name: str
    is_new: bool
    frame: Optional[CellFrame] = None
    problem: Optional[str] = None


@dataclass(frozen=True)
class PairComponent:
    """One component of the selected pair, with what the dialog needs to pick a
    cell origin: its centre and its pads (the "Role/Pad" override)."""
    ref: str
    role: str
    x_mm: float
    y_mm: float
    pads: tuple = ()


@dataclass(frozen=True)
class ChainChoice:
    """One chain on a net of the pair: where it lives, its resolved anchor, its
    pads on that net (nearest first), its current spokes and the ORDER of the
    pools it consumes. Everything the pool verdict needs, so the dialog can
    re-answer "what will this spoke get" without another board read."""
    net: str
    name: str
    entry: dict
    file: Optional[str]
    anchor_ref: str
    anchor_role: Optional[str]
    anchor_cluster: Optional[str]
    anchor_sheet: Optional[str]
    pads: tuple = ()            # PadPoint, nearest first
    spokes: tuple = ()          # the raw spoke dicts as stored
    pools: dict = field(default_factory=dict)   # {cluster: {role: [refs]}}


@dataclass(frozen=True)
class ForeignPad:
    """A pad of another footprint on the pair's net — what a brand-new chain
    anchors to when the net has no chain at all (plan Р3)."""
    net: str
    ref: str
    role: Optional[str]
    cluster: Optional[str]
    sheet: Optional[str]
    pad: str
    x_mm: float
    y_mm: float


@dataclass(frozen=True)
class OrphanNet:
    """A net of the pair that has NO chain yet: the foreign pads it could anchor
    to (nearest first) plus the pool order that chain would consume — read here
    so the dialog can answer "what pair would this spoke get" for a chain that
    does not exist yet, with the same honesty as for one that does."""
    net: str
    pads: tuple = ()
    pools: dict = field(default_factory=dict)


@dataclass(frozen=True)
class SpokeContext:
    """Everything "Extract spoke..." shows, read once."""
    problems: tuple = ()          # selection refusals — empty means usable
    selection: Optional[SpokeSelection] = None
    repeated: dict = field(default_factory=dict)
    pair: tuple = ()              # PairComponent per selected footprint
    cells: tuple = ()             # CellChoice, the new-cell row first
    chains: tuple = ()            # ChainChoice
    orphans: tuple = ()           # OrphanNet — nets with no chain yet
    planes: tuple = ()            # ((net, members), ...) dropped as planes
    notes: tuple = ()             # informational lines (skipped chains, ...)


# ── the read ───────────────────────────────────────────────────────────────

def _sheet_chain(adapter, fp, sheet_names) -> tuple:
    """The footprint's sheet chain with readable names — the project's ONE
    resolution (kicadstamp.sheet_names.resolve_sheet_path_names), the same
    best-effort rule the cell-anchor page's own worker uses: a resolution
    failure narrows nothing."""
    if not sheet_names:
        return tuple(getattr(fp, "sheet_path_uuids", None) or ())
    try:
        return tuple(resolve_sheet_path_names(fp, sheet_names) or ())
    except Exception:  # noqa: BLE001 — best-effort chain, never fatal
        return ()


def _selection_records(adapter, footprints, sheet_names) -> list:
    return [{"ref": fp.ref,
             "role": adapter.get_field_value(fp, ROLE_FIELD_NAME),
             "cluster": adapter.get_field_value(fp, CLUSTER_FIELD_NAME),
             "sheet": _sheet_chain(adapter, fp, sheet_names)}
            for fp in footprints]


class _Record:
    """The .ref/.role/.cluster shape check_spoke_selection reads — kept local so
    the read layer does not depend on gui/cell_identification's dataclass."""
    __slots__ = ("ref", "role", "cluster", "sheet")

    def __init__(self, ref, role, cluster, sheet=()):
        self.ref, self.role, self.cluster, self.sheet = ref, role, cluster, sheet


def _pad_point(pad, ref="", role=None) -> PadPoint:
    return PadPoint(pad=str(pad.number), x_mm=pad.position.x / MM,
                    y_mm=pad.position.y / MM, ref=str(ref), role=role)


def _pool_orders(adapter, cfg, net, roles, clusters) -> dict:
    """{cluster: {role: [refs]}} — the pools of one chain, read ONCE and handed
    over in their CONSUMPTION order.

    Built with the real ComponentResolver/ComponentPool (the same objects
    compute_raw_positions uses, so the order — natural refdes sort included —
    cannot drift), then drained through the pool's public contract to become
    plain data: nothing here touches a private field."""
    if not roles:
        return {}
    pools = ComponentResolver.build_pools(adapter, net, set(roles), set(clusters))
    orders: dict = {}
    for cluster, pool in pools.items():
        order: dict = {}
        for role in sorted(roles):
            try:
                remaining = pool.remaining_count(role)
            except ValidationError:          # a role the pool does not know
                continue
            order[role] = [pool.pop(role, "?") for _ in range(remaining)]
        orders[cluster] = order
    return orders


def _chain_roles_needed(cfg, spoke_entries) -> set:
    """The roles a chain's cells ask for, retired spokes skipped — the exact set
    chain_roles_needed builds, computed from the RAW entries the file holds."""
    roles: set = set()
    for spoke in spoke_entries or ():
        if (spoke or {}).get("retired"):
            continue
        cell = (getattr(cfg, "cells", None) or {}).get((spoke or {}).get("cell"))
        if cell is not None:
            roles.update(slot.role for slot in (getattr(cell, "components", None) or ()))
    return roles


def _cell_choices(adapter, cfg, selection, cluster, sheet_names, footprints) -> list:
    """The cell combo: the "new cell" row plus every existing cell whose role set
    is exactly the pair's, each with the frame of its placed instance.

    A frame that cannot be read (no placed instance of that cell on this board
    yet) leaves the choice usable but frameless — the dialog then treats it as
    "read the position from the pair" like the new-cell case, which is honest:
    the offsets it would store are the ones the recipe computes from the board.
    A MIRRORED instance is refused instead: ManualSpoke has no mirror field
    (kicadstamp/geometry/spoke_layout.py: "ManualSpoke does not support mirror"),
    so the spoke's redraw could never put the pair back where it is."""
    names = sorted(
        name for name, cell in (getattr(cfg, "cells", None) or {}).items()
        if {slot.role for slot in (getattr(cell, "components", None) or ())}
        == set(selection.role_to_ref))
    out = [CellChoice(name="", is_new=True)]
    for name in names:
        cell = cfg.cells[name]
        try:
            origin, rotation, mirror = _live_cluster_frame(
                adapter, cell, cluster, "", sheet_names, selection.role_to_ref)
            frame = CellFrame(origin_x_mm=origin.x / MM, origin_y_mm=origin.y / MM,
                              rotation_deg=float(rotation), mirror=bool(mirror))
        except ValidationError as exc:
            logger.info("Extract spoke: no frame for cell %r: %s", name, exc)
            frame = None
            mirror = False
        problem = None
        if frame is not None and frame.mirror:
            problem = _("this cell's instance stands MIRRORED, and a spoke has "
                        "no mirror field — extract the cell as a NEW one from "
                        "this pair instead")
        out.append(CellChoice(name=name, is_new=False, frame=frame,
                              problem=problem))
    return out


def read_spoke_context(adapter, cfg, sheet_names, root_path) -> SpokeContext:
    """THE board read of the dialog (door П3.1: worker thread only).

    Board refreshed first — the poll tick is a no-op while connected, so an old
    cache would answer "is this a spoke?" with a stale role multiplicity, which
    is the one direction that matters. Then: the selection, the spoke evidence,
    the cell candidates, the chains with their anchors/pads/pools, and the
    foreign pads of the nets that have no chain yet."""
    adapter.refresh_board()
    footprints = [item for item in (adapter.get_selected_items() or ())
                  if isinstance(item, Footprint)]
    selected = [_Record(**rec) for rec in
                _selection_records(adapter, footprints, sheet_names)]
    selection, problems = check_spoke_selection(selected)
    if selection is None:
        return SpokeContext(problems=tuple(problems))

    cluster = selection.cluster
    refs = set(selection.refs)
    centre_x = sum(fp.position.x for fp in footprints) / len(footprints) / MM
    centre_y = sum(fp.position.y for fp in footprints) / len(footprints) / MM
    centre = (centre_x, centre_y)

    pair = tuple(PairComponent(ref=fp.ref,
                               role=selection.role_to_ref and
                               next(r for r, ref in selection.role_to_ref.items()
                                    if ref == fp.ref),
                               x_mm=fp.position.x / MM, y_mm=fp.position.y / MM,
                               pads=tuple(_pad_point(p, fp.ref)
                                          for p in adapter.get_footprint_pads(fp)))
               for fp in footprints)

    # The spoke EVIDENCE: how often each of the pair's roles occurs in its
    # cluster (the whole board, not just the selection).
    counts: Counter = Counter()
    for fp in adapter.get_footprints():
        role = adapter.get_field_value(fp, ROLE_FIELD_NAME)
        if role not in selection.role_to_ref:
            continue
        fp_cluster = adapter.get_field_value(fp, CLUSTER_FIELD_NAME) or ""
        if cluster_prefix_match(fp_cluster, cluster):
            counts[role] += 1
    repeated = spoke_criterion(counts)

    # The nets of the pair's pads, with the plane rule applied to them.
    nets: list = []
    for fp in footprints:
        for pad in adapter.get_footprint_pads(fp):
            if pad.net_name and pad.net_name not in nets:
                nets.append(pad.net_name)
    member_refs: dict = {net: set() for net in nets}
    foreign_pads: dict = {net: [] for net in nets}
    for fp in adapter.get_footprints():
        if fp.ref in refs:
            continue
        fp_role = adapter.get_field_value(fp, ROLE_FIELD_NAME)
        fp_cluster = adapter.get_field_value(fp, CLUSTER_FIELD_NAME)
        fp_sheet = _sheet_chain(adapter, fp, sheet_names)
        for pad in adapter.get_footprint_pads(fp):
            if pad.net_name not in member_refs:
                continue
            member_refs[pad.net_name].add(fp.ref)
            foreign_pads[pad.net_name].append(ForeignPad(
                net=pad.net_name, ref=fp.ref, role=fp_role, cluster=fp_cluster,
                sheet=fp_sheet[0] if fp_sheet else None, pad=str(pad.number),
                x_mm=pad.position.x / MM, y_mm=pad.position.y / MM))
    kept, planes = split_plane_nets(
        (net, len(member_refs[net])) for net in nets)

    cells = _cell_choices(adapter, cfg, selection, cluster, sheet_names, footprints)

    chains: list = []
    notes: list = []
    orphans: list = []
    resolver = ComponentResolver(adapter, cfg, sheet_names)
    for net in kept:
        entries = collect_chains_by_net(root_path, net) if root_path else []
        live_entries = [(path, entry) for path, entry in entries
                        if not (entry or {}).get("retired")]
        if not live_entries:
            # No chain on this net yet: the dialog offers to create one, anchored
            # to the nearest FOREIGN pad — with the pool order such a chain would
            # consume, so its verdict is as honest as an existing chain's.
            orphans.append(OrphanNet(
                net=net,
                pads=tuple(sorted(
                    foreign_pads[net],
                    key=lambda p: (p.x_mm - centre_x) ** 2 + (p.y_mm - centre_y) ** 2)),
                pools=_pool_orders(adapter, cfg, net,
                                   set(selection.role_to_ref), {cluster})))
            continue
        for path, entry in live_entries:
            anchor_role = entry.get("anchor_role")
            if entry.get("anchor_point") and not anchor_role:
                notes.append(_("chain {name} is anchored to a point — a spoke "
                               "needs a component's pads, so it is not offered")
                             .format(name=chain_identity(entry)))
                continue
            try:
                anchor = resolver.resolve_anchor_fp(
                    entry.get("anchor_ref"), anchor_role, entry.get("anchor_sheet"),
                    entry.get("anchor_cluster"),
                    label=_("chain (net {net!r})").format(net=net))
            except ValidationError as exc:
                notes.append(_("chain {name} anchor unresolved — {error}").format(
                    name=chain_identity(entry), error=str(exc).strip().splitlines()[-1]))
                continue
            pads = [_pad_point(pad, anchor.ref, anchor_role)
                    for pad in adapter.get_footprint_pads(anchor)
                    if pad.net_name == net]
            if not pads:
                notes.append(_("chain {name} anchor {ref} has no pad on the net")
                             .format(name=chain_identity(entry), ref=anchor.ref))
                continue
            spokes = tuple(entry.get("spokes") or ())
            roles = _chain_roles_needed(cfg, spokes) | set(selection.role_to_ref)
            clusters = {s.get("cluster") for s in spokes
                        if not (s or {}).get("retired")} | {cluster}
            chains.append(ChainChoice(
                net=net, name=str(chain_identity(entry)), entry=dict(entry),
                file=str(path) if path else None, anchor_ref=anchor.ref,
                anchor_role=anchor_role, anchor_cluster=entry.get("anchor_cluster"),
                anchor_sheet=entry.get("anchor_sheet"),
                pads=tuple(pads_by_distance(pads, centre)), spokes=spokes,
                pools=_pool_orders(adapter, cfg, net, roles, clusters)))

    return SpokeContext(problems=(), selection=selection, repeated=repeated,
                        pair=pair, cells=tuple(cells), chains=tuple(chains),
                        orphans=tuple(orphans), planes=tuple(planes),
                        notes=tuple(notes))


def spoke_identification(data: Optional[SpokeContext]) -> Optional[Identification]:
    """The stage-1 identification of the pair this dialog was read from (Р8).

    kind="spoke" with the repeated roles as the EVIDENCE, and the role -> refdes
    map of the selection: that is what makes the cell editor open on THIS pair
    instead of complaining that a role of the cell has no footprint in the
    cluster. Refdes still reach nothing but gui_state.json (design §1)."""
    if data is None or data.selection is None:
        return None
    return Identification(cluster=data.selection.cluster, sheet=None,
                          role_to_ref=dict(data.selection.role_to_ref),
                          kind=KIND_SPOKE,
                          repeated_roles=tuple(sorted(data.repeated.items())))


# ── the write ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class SpokeWriteResult:
    """What OK did: ok + the messages to show, and what it wrote (for the status
    line, the Log and the stage-1 identification that follows)."""
    ok: bool
    messages: tuple = ()
    cell_name: str = ""
    cell_new: bool = False
    replaced: bool = False
    chain_written: bool = False
    stale: bool = False
    pad: str = ""


def write_spoke_extraction(adapter, *, root_path: Path, cell_name: str,
                           cell_is_new: bool, chain_file: Optional[Path],
                           chain_entry: dict, spoke: dict, replace: bool,
                           origin_role: Optional[str] = None,
                           origin_pad: Optional[str] = None,
                           expected_refs=()) -> SpokeWriteResult:
    """The worker half of OK (door П3.2 — the cell extraction reads the board).

    Order, and why:
      1. the LIVE selection is re-read and compared with the pair the dialog was
         built on (Р6). A mismatch writes NOTHING and says so: the dialog's
         verdicts (which pair the pool gives, where the parts stand) describe the
         OLD selection, and a config written from them is the worst kind of lie —
         it is already in the file;
      2. the new cell is extracted from THAT selection (footprints + copper);
      3. the spoke is staged and the whole chain validated (stage_spoke_write) —
         a refusal here still writes nothing;
      4. only then are the files touched, each with its own timestamped backup:
         `cells:` in the ROOT config, the chain in the file it actually lives in.
    """
    adapter.refresh_board()
    live = [item for item in (adapter.get_selected_items() or ())
            if isinstance(item, Footprint)]
    live_refs = tuple(sorted(fp.ref for fp in live))
    if live_refs != tuple(sorted(str(r) for r in (expected_refs or ()))):
        return SpokeWriteResult(
            ok=False, stale=True, messages=(
                _("the board selection changed since this dialog was read "
                  "({was} -> {now}) — nothing was written; read the selection "
                  "again and repeat").format(
                      was=", ".join(sorted(str(r) for r in (expected_refs or ()))) or "?",
                      now=", ".join(live_refs) or "?"),))

    cell_dict = None
    if cell_is_new:
        try:
            extracted = extract_template_from_selection(
                adapter, cell_name, items=list(adapter.get_selected_items() or ()),
                origin_component_role=origin_role,
                origin_component_pad=origin_pad if origin_role else None,
                origin_component_cluster=(spoke or {}).get("cluster"))
        except ValidationError as exc:
            return SpokeWriteResult(ok=False, messages=(str(exc).strip(),))
        cell_dict = (extracted or {}).get(cell_name)
        if cell_dict is None:
            return SpokeWriteResult(
                ok=False, cell_name=cell_name, cell_new=True,
                messages=(_("the cell {cell!r} could not be extracted from the "
                            "selection — nothing was written").format(cell=cell_name),))

    plan, problems = stage_spoke_write(chain_entry, spoke, replace=replace)
    if plan is None:
        return SpokeWriteResult(ok=False, cell_name=cell_name,
                                cell_new=cell_is_new, messages=tuple(problems))

    target = Path(chain_file) if chain_file else Path(root_path)
    try:
        backup_file(root_path)
        data = read_data(root_path)
        if cell_dict is not None:
            data.setdefault("cells", {})[cell_name] = cell_dict
            write_data(root_path, data)
        if target.resolve() != Path(root_path).resolve():
            backup_file(target)
        upsert_list_entry(target, "chains", plan.chain, key_fn=chain_identity)
    except OSError as exc:
        return SpokeWriteResult(
            ok=False, cell_name=cell_name, cell_new=cell_is_new,
            replaced=plan.replaced,
            messages=(_("Write failed: {error}").format(error=exc),))

    load_chain(plan.chain)                     # the round-trip check
    return SpokeWriteResult(ok=True, cell_name=cell_name, cell_new=cell_is_new,
                            replaced=plan.replaced, chain_written=True,
                            pad=plan.pad)
