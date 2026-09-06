# kicadstamp/scheme_list_apply.py
"""Apply/Redraw branch for scheme_list-based Entities (plan_2026_09_05_scheme_
list.md §4, plan_2026_09_06_scheme_list_p4_apply.md).

A scheme_list Entity NEVER goes through the cell clone machinery
(placement/entity_placement._to_clone would produce ClonePlacement(cell=None)).
Instead this module materializes each scheme_list placement node into the SAME
commands the rest of the tool executes (MoveCommand/ViaCommand/TrackCommand +
BatchExecutor), at the caller level (apply_pipeline, GUI Redraw).

Recorded offsets/rotations are RAW (board frame at capture, see
scheme_list_capture.py); the record carries the anchor's absolute angle at
capture as `anchor_rotation_deg`. When a placement node moves the anchor to
(node_pos, node_rot), every recorded element is first re-expressed in the
record's LOCAL frame (rotate the raw offset by -anchor_rotation_deg, the same
trick child_local_offset uses) and then rotated by the node's actual rotation
(child_absolute_position) — otherwise a recorded region whose anchor was
captured at a non-zero angle gets DOUBLE-rotated (the d3326e4 bug class). The
element's absolute angle = node_rot + relative_rotation_deg(element.rotation,
anchor_rotation_deg), so the anchor itself lands at node_rot and the region's
relative geometry is preserved.

Modes:
  in place       Entity.sheet empty or == record.source_sheet  -> move the
                 recorded refs themselves, nets literal.
  onto sibling   Entity.sheet set and != source_sheet          -> resolve the
                 recorded components' TWINS on the target sheet (live twin map
                 via channel_copy.build_channel_groups) and remap local nets
                 with TwinMap.twin_net. An incomplete twin set is a single
                 fatal listing ALL missing targets — never a silent partial
                 apply.
"""
from dataclasses import dataclass, field

from .config.models import (
    Entity,
    SchemeListComponentRecord,
    SchemeListConfig,
)
from .domain.geometry import Angle, BoardLayer, Vector2
from .exceptions import ValidationError, format_fatal_error
from .tree_position import (
    child_absolute_position,
    node_own_anchor_base,
    node_position,
    relative_rotation_deg,
)
from .geometry.spoke_layout import rotate_local_offset
from .utils.layers import layer_from_str
from .cloner.models import TwinMap
from .channel_copy import build_channel_groups, sheet_name_of_fp
from .i18n import _


@dataclass
class SchemeListNode:
    """One scheme_list placement node, with its absolute (pos, rot) — the same
    composition entity_placement._walk uses for cell-based placement nodes."""

    entity: Entity
    scheme_list: SchemeListConfig
    position: Vector2
    rotation_deg: float


@dataclass
class SchemeListApplyPlan:
    """Planned commands for ONE scheme_list placement node. Pure geometry —
    nothing applied; the caller decides how to execute."""

    entity_name: str
    mode: str  # "in_place" | "onto_sibling"
    moves: list = field(default_factory=list)      # MoveCommand
    vias: list = field(default_factory=list)       # ViaCommand
    tracks: list = field(default_factory=list)     # TrackCommand
    # recorded source ref -> target refdes actually moved (diagnostics)
    ref_map: dict[str, str] = field(default_factory=dict)

    @property
    def empty(self) -> bool:
        return not (self.moves or self.vias or self.tracks)


# ── geometry: the rotation-compensation formula (plan §4, KРИТИЧНО) ─────────

def _local_offset_mm(along_mm: float, across_mm: float,
                     anchor_rotation_deg: float) -> Vector2:
    """Raw board-frame offset -> the element's offset in the record's LOCAL
    frame: rotate by -anchor_rotation_deg (child_local_offset's trick)."""
    return rotate_local_offset(along_mm, across_mm, -anchor_rotation_deg)


def _absolute_target(local_offset: Vector2, node_pos: Vector2,
                     node_rotation_deg: float) -> Vector2:
    """Local (record-frame) offset -> absolute position at the node target:
    rotate by the node's actual rotation and translate (child_absolute_position)."""
    return child_absolute_position(node_pos, node_rotation_deg, local_offset)


def _element_abs(along_mm: float, across_mm: float, anchor_rotation_deg: float,
                 node_pos: Vector2, node_rotation_deg: float) -> Vector2:
    """The full formula for one recorded offset: local frame, then node frame."""
    return _absolute_target(
        _local_offset_mm(along_mm, across_mm, anchor_rotation_deg),
        node_pos, node_rotation_deg)


def _component_angle_deg(component_rotation_deg: float, anchor_rotation_deg: float,
                         node_rotation_deg: float) -> float:
    return (node_rotation_deg
            + relative_rotation_deg(component_rotation_deg, anchor_rotation_deg)) % 360.0


# ── helpers shared by both modes ────────────────────────────────────────────

def _remap_net(net: str | None, src_name: str | None, dst_name: str | None) -> str | None:
    """Literal net -> target net. Local nets (/{src}/...) remap to the dst
    sheet; global nets pass through (TwinMap.twin_net is a pure function of
    (net, src_ch, dst_ch) — called with an explicit None self)."""
    if not net:
        return net
    if src_name and dst_name and src_name != dst_name:
        return TwinMap.twin_net(None, net, src_name, dst_name)
    return net


def _fatal_problems(problems: list[str]) -> None:
    if problems:
        raise ValidationError(format_fatal_error(
            _("cannot apply scheme list: {count} problem(s)").format(count=len(problems)),
            problems))


def _layer_board(layer_str: str | None) -> BoardLayer:
    return layer_from_str(layer_str) if layer_str else BoardLayer.BL_F_Cu


# ── twin resolution (onto sibling) ──────────────────────────────────────────

def _twin_sheet_uuids(groups: dict[str, dict[str, str]]) -> set[str]:
    """Sheet uuids that are REAL twins: path[0] members of a twin group with
    2+ members (shared-root footprints with /Channel_N/-style local nets are
    excluded — the same rule as channel_copy._channel_sheet_uuids)."""
    out: set[str] = set()
    for members in groups.values():
        if len(members) >= 2:
            out.update(members.keys())
    return out


def _name_to_twin_uuid(adapter, groups: dict[str, dict[str, str]]) -> dict[str, str]:
    """Top-level sheet name -> its uuid, restricted to REAL twin sheets (built
    from the generalized sheet_name_of_fp local-net prefix)."""
    twin_uuids = _twin_sheet_uuids(groups)
    name_to_uuid: dict[str, str] = {}
    for fp in adapter.get_footprints():
        name = sheet_name_of_fp(adapter, fp)
        if not name:
            continue
        chain = list(fp.sheet_path_uuids)
        if not chain or chain[0] not in twin_uuids:
            continue
        name_to_uuid.setdefault(name, chain[0])
    return name_to_uuid


def _inner_key(chain: tuple[str, ...]) -> str | None:
    """path[1:] as '/a/b' — None when the footprint has no usable hierarchy."""
    if len(chain) < 2:
        return None
    return "/" + "/".join(chain[1:])


def _resolve_onto_sibling(adapter, record: SchemeListConfig, entity: Entity,
                          groups: dict[str, dict[str, str]],
                          ) -> tuple[str, str, dict[str, str], list[str]]:
    """Resolve (src_name, dst_name, ref_map, problems) for onto-sibling mode.

    src_name/dst_name — the top-level sheet names used for the net remap.
    ref_map — recorded source ref -> twin refdes on the target sheet.
    problems — every recorded ref with no resolvable twin (single fatal list).
    """
    problems: list[str] = []
    all_fps = adapter.get_footprints()
    fp_by_ref = {fp.ref: fp for fp in all_fps}
    dst_name = entity.sheet or ""

    # Source name: explicit record.source_sheet, else the anchor's own local net.
    anchor_fp = fp_by_ref.get(record.anchor_ref)
    if anchor_fp is None:
        problems.append(_("anchor {ref!r} is not on the board").format(ref=record.anchor_ref))
        return record.source_sheet or "", dst_name, {}, problems
    src_anchor_name = sheet_name_of_fp(adapter, anchor_fp)
    src_name = record.source_sheet or src_anchor_name or ""
    anchor_chain = tuple(anchor_fp.sheet_path_uuids)
    anchor_inner = _inner_key(anchor_chain)
    if anchor_inner is None:
        problems.append(_("anchor {ref!r} has no usable sheet hierarchy").format(
            ref=record.anchor_ref))
        return src_name, dst_name, {}, problems

    name_to_uuid = _name_to_twin_uuid(adapter, groups)
    if not dst_name or dst_name not in name_to_uuid:
        problems.append(_("target sheet {sheet!r} is not a twin on the board").format(
            sheet=dst_name))
        return src_name, dst_name, {}, problems
    dst_uuid = name_to_uuid[dst_name]

    group = groups.get(anchor_inner, {})
    ref_map: dict[str, str] = {}
    for comp in record.components:
        fp = fp_by_ref.get(comp.ref)
        if fp is None:
            problems.append(_("recorded component {ref!r} is not on the source "
                              "sheet").format(ref=comp.ref))
            continue
        chain = tuple(fp.sheet_path_uuids)
        inner = _inner_key(chain)
        if inner is None:
            problems.append(_("recorded component {ref!r} has no usable sheet "
                              "hierarchy").format(ref=comp.ref))
            continue
        twin_ref = groups.get(inner, {}).get(dst_uuid)
        if not twin_ref:
            problems.append(_("{ref!r} has no twin on sheet {sheet!r}").format(
                ref=comp.ref, sheet=dst_name))
            continue
        ref_map[comp.ref] = twin_ref
    # Anchor's own twin must exist (its group is the anchor's channel).
    if ref_map.get(record.anchor_ref) is None and not problems:
        problems.append(_("anchor {ref!r} has no twin on sheet {sheet!r}").format(
            ref=record.anchor_ref, sheet=dst_name))
    return src_name, dst_name, ref_map, problems


# ── public planner ──────────────────────────────────────────────────────────

def plan_scheme_list(entity: Entity, record: SchemeListConfig, adapter,
                     node_pos: Vector2, node_rotation_deg: float,
                     groups: dict[str, dict[str, str]] | None = None,
                     ) -> SchemeListApplyPlan:
    """Plan the commands that materialize one scheme_list placement node.

    Pure computation (no board writes): resolves target refdes (direct for in
    place, twins for onto sibling), applies the anchor-rotation compensation
    formula to every recorded component/via/track, remaps local nets for the
    twin case and returns MoveCommand/ViaCommand/TrackCommand lists. Missing
    refs/twins are a single fatal listing ALL problems.

    `node_pos`/`node_rotation_deg` are the node's ABSOLUTE target for the
    anchor (the same composition entity_placement uses). `groups` may be passed
    to reuse one full-board scan across several nodes; otherwise built here.
    """
    entity_name = entity.name or entity.scheme_list or "scheme_list"
    dst_name = entity.sheet or ""
    src_name = record.source_sheet or ""
    onto = bool(dst_name) and dst_name != src_name

    all_fps = adapter.get_footprints()
    fp_by_ref = {fp.ref: fp for fp in all_fps}

    problems: list[str] = []
    mode = "onto_sibling" if onto else "in_place"
    ref_map: dict[str, str] = {}
    src_net_name = src_name

    if onto:
        if groups is None:
            groups = build_channel_groups(adapter)
        src_net_name, dst_name, ref_map, problems = _resolve_onto_sibling(
            adapter, record, entity, groups)
    else:
        for comp in record.components:
            if comp.ref not in fp_by_ref:
                problems.append(_("recorded component {ref!r} is not on the "
                                  "board").format(ref=comp.ref))
            else:
                ref_map[comp.ref] = comp.ref
    _fatal_problems(problems)

    # Layer of each target footprint (kept on its CURRENT side — no v1 mirror).
    anchor_rot = record.anchor_rotation_deg
    moves = []
    for comp in record.components:
        target_ref = ref_map[comp.ref]
        target_fp = fp_by_ref.get(target_ref)
        if target_fp is None:
            # Should be unreachable after the checks above; guard for safety.
            _fatal_problems([_("target {ref!r} is not on the board").format(ref=target_ref)])
        pos = _element_abs(comp.offset_along_mm, comp.offset_across_mm,
                           anchor_rot, node_pos, node_rotation_deg)
        angle_deg = _component_angle_deg(comp.rotation_deg, anchor_rot,
                                         node_rotation_deg)
        moves.append(_move_command(
            ref=target_ref, position=pos, angle_deg=angle_deg,
            layer=target_fp.layer, owner_ref=entity_name))

    vias = []
    for i, via in enumerate(record.vias):
        pos = _element_abs(via.offset_along_mm, via.offset_across_mm,
                           anchor_rot, node_pos, node_rotation_deg)
        vias.append(_via_command(
            position=pos, drill_mm=via.drill_mm, diameter_mm=via.diameter_mm,
            net=_remap_net(via.net, src_net_name, dst_name),
            owner_ref=entity_name, key=f"scheme_list:{entity_name}:via:{i}"))

    tracks = []
    for i, tr in enumerate(record.tracks):
        start = _element_abs(tr.start_along_mm, tr.start_across_mm,
                             anchor_rot, node_pos, node_rotation_deg)
        end = _element_abs(tr.end_along_mm, tr.end_across_mm,
                           anchor_rot, node_pos, node_rotation_deg)
        tracks.append(_track_command(
            start=start, end=end, width_mm=tr.width_mm,
            net=_remap_net(tr.net, src_net_name, dst_name),
            layer=_layer_board(tr.layer), owner_ref=entity_name,
            key=f"scheme_list:{entity_name}:track:{i}"))

    return SchemeListApplyPlan(
        entity_name=entity_name, mode=mode,
        moves=moves, vias=vias, tracks=tracks, ref_map=ref_map)


# ── command builders ────────────────────────────────────────────────────────

def _move_command(ref, position, angle_deg, layer, owner_ref):
    from .placement.commands import MoveCommand
    return MoveCommand(ref=ref, position=position,
                       angle=Angle.from_degrees(float(angle_deg)),
                       layer=layer, owner_ref=owner_ref)


def _via_command(position, drill_mm, diameter_mm, net, owner_ref, key):
    from .placement.commands import ViaCommand
    return ViaCommand(position=position, drill_mm=drill_mm, diameter_mm=diameter_mm,
                      net_name=net, owner_ref=owner_ref, registry_key=key)


def _track_command(start, end, width_mm, net, layer, owner_ref, key):
    from .placement.commands import TrackCommand
    return TrackCommand(start=start, end=end, width_mm=width_mm, net_name=net,
                        layer=layer, owner_ref=owner_ref, registry_key=key)
