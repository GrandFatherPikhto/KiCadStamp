# kicadstamp/channel_copy.py
"""
channel_copy.py — live copy of a whole channel's placement (variant B of the
A/B/C triple, see plan_2026_08_15_channel_copy.md / plan_2026_08_17_
channel_copy_resume.md): move every footprint of Channel_0 to the position its
twin has on Channel_1, plus the channel's vias/tracks, applying one rigid
transform (anchor + rotation + shift, optional mirror) to the whole
construction.

Unlike variant A (sheet_templates) and variant C (extract-as-template), this
works with a LIVE board through the IPC adapter: the twin map (which ref on
Channel_1 corresponds to which ref on Channel_0) is built from
fp.sheet_path.path UUID chains, NOT from Role names — so repeated Role schemes
between PIF instances inside one channel are irrelevant (the structural limit
that made variant C fatal for the whole channel, see the resume plan).

Key decisions (from the plan, verified against the code):
  - twin key = inner_key = "/" + "/".join(path[1:]) — path[0] is the channel
    sheet uuid (unique per physical footprint when combined with path[1:],
    recon_symbol_uuid_bridge.py), path[1:] is IDENTICAL for twins of all
    channels (cloned sheets share the sub-chain and the symbol uuid).
  - transformation: X' = R(angle)·(X − anchor_src) + anchor_dst, rot' = rot+angle
    (rotation via rotate_local_offset — the engine's own KiCad Y-down
    convention, geometry/spoke_layout.py); with --mirror the point is then
    X-mirrored about the vertical axis through anchor_src (same _mirror_x as
    ClonePlacement.mirror, geometry/clone_geometry.py) and the angle becomes
    (180° − φ) mod 360°. Order of operations (documented, they do not commute):
    rotate first, then mirror.
  - net mapping: TwinMap.twin_net (cloner/models.py) — local nets
    "/Channel_0/..." -> "/Channel_1/...", global nets pass through unchanged.
  - idempotency is position+net based (tolerances), NEVER the registry — a
    double run on the same dst must not duplicate components/vias/tracks
    (tracks reuse track_matches from registry.py, the shared bidirectional
    predicate from Stage 1).
  - Sheet/Cluster fields are never touched — every PIF stays in its own
    cluster and its own sheet, so the filters stay reusable (the two-sheet
    problem disappears by construction).

Structure: pure library. plan_channel_copy() computes a ChannelCopyPlan;
execute_channel_copy() applies it through BatchExecutor (one undo log for the
whole run, moves -> refresh -> vias -> tracks); channel_copy() is the
high-level convenience wrapper in author.py style. The CLI (kicadstamp_cli.py,
subcommand channel-copy) is a thin wrapper on top.
"""

import logging
from dataclasses import dataclass, field

from kipy.board_types import BoardLayer
from kipy.geometry import Vector2, Angle

from .cloner.models import TwinMap
from .config import Config
from .constants import (DEFAULT_BATCH_SIZE, POSITION_TOLERANCE_MM,
                        ANGLE_TOLERANCE_DEG, ROLE_FIELD_NAME)
from .exceptions import ValidationError, format_fatal_error
from .geometry.spoke_layout import rotate_local_offset
from .i18n import _
from .placement.commands import MoveCommand, ViaCommand, TrackCommand
from .placement.executor import BatchExecutor
from .registry import track_matches
from .utils.units import MM

logger = logging.getLogger(__name__)

# Foreign-bbox margin around the source channel's footprints (mm) — same value
# cloner/pcb.py uses (bbox_margin_mm=1.0) for its foreign-copper scan.
_FOREIGN_BBOX_MARGIN_MM = 1.0


def _path_uuids(fp) -> list[str]:
    """UUID chain of a footprint's sheet_path.path, as plain strings."""
    return [str(u.value) for u in fp.sheet_path.path]


def _layer_to_board(layer: str) -> BoardLayer:
    """'F.Cu'/'B.Cu' -> BoardLayer (same mapping the planners use)."""
    return BoardLayer.BL_B_Cu if layer == "B.Cu" else BoardLayer.BL_F_Cu


def _board_layer_to_str(layer: BoardLayer) -> str:
    """BoardLayer -> 'F.Cu'/'B.Cu'."""
    return "B.Cu" if layer == BoardLayer.BL_B_Cu else "F.Cu"


def _channel_name_of_fp(adapter, fp) -> str | None:
    """Name of the channel a footprint belongs to, derived from the LOCAL
    hierarchical net of any of its pads ("/Channel_0/DAC/+3V3_AVDD" -> "Channel_0").
    None when the footprint carries no local net on any pad (e.g. a purely
    global-net component, or a footprint whose pads have no nets yet)."""
    for pad in adapter.get_footprint_pads(fp):
        net = getattr(getattr(pad, "net", None), "name", None)
        if net and net.startswith("/Channel_"):
            return net.split("/")[1]
    return None


# ── Task 2.1: twin map from the LIVE board ───────────────────────────────────


def build_channel_groups(adapter, src_uuid: str | None = None) -> dict[str, dict[str, str]]:
    """Group every footprint on the board by its twin inner_key.

    Returns inner_key -> {channel_uuid -> ref}. inner_key = path[1:] of the
    sheet_path chain — identical for twins across channels, unique within a
    channel (the full chain is unique per physical footprint and path[0] is
    fixed within a channel). Footprints without a usable hierarchy chain
    (sheet_path.path shorter than 2) are skipped — they cannot be twins.
    """
    groups: dict[str, dict[str, str]] = {}
    for fp in adapter.get_footprints():
        chain = _path_uuids(fp)
        if len(chain) < 2:
            continue
        inner = "/" + "/".join(chain[1:])
        groups.setdefault(inner, {})[chain[0]] = fp.reference_field.text.value
    return groups


def build_live_twin_map(adapter, pivot_ref: str,
                        groups: dict[str, dict[str, str]] | None = None
                        ) -> tuple[str, dict[str, dict[str, str]]]:
    """Build the twin map anchored on a pivot footprint of the source channel.

    Returns (src_uuid, groups) where:
      - src_uuid: channel sheet uuid (path[0]) of the pivot footprint — the
        root of the source channel;
      - groups: inner_key -> {channel_uuid -> ref} for EVERY footprint on the
        board (see build_channel_groups) — the dst twins are looked up in it.

    `groups` may be passed in to avoid a second full-board scan when the caller
    (channel_copy, --pivot-role path) already built the twin map; otherwise it
    is built here.

    Fatal if the pivot is not on the board or carries no hierarchy chain.
    """
    all_fps = adapter.get_footprints()
    pivot = None
    for fp in all_fps:
        if fp.reference_field.text.value == pivot_ref:
            pivot = fp
            break
    if pivot is None:
        raise ValidationError(format_fatal_error(
            _("pivot footprint {ref!r} not found on the board").format(ref=pivot_ref),
            [_("channel-copy needs a pivot component that exists on the live board "
               "to anchor the source channel to")]))
    chain = _path_uuids(pivot)
    if len(chain) < 2:
        raise ValidationError(format_fatal_error(
            _("pivot footprint {ref!r} has no usable sheet hierarchy").format(ref=pivot_ref),
            [_("its sheet_path.path has fewer than 2 UUIDs — a twin map cannot be "
               "built for a footprint outside the sheet hierarchy")]))
    src_uuid = chain[0]
    if groups is None:
        groups = build_channel_groups(adapter)
    return src_uuid, groups


# ── Task 2.1 (cont.): channel name <-> uuid resolution ───────────────────────


def _channel_sheet_uuids(groups: dict[str, dict[str, str]]) -> set[str]:
    """Sheet uuids that ACTUALLY are channels: path[0] values that appear as a
    member of a twin group with 2+ members. A shared root sheet (whose
    components carry /Channel_N/ local nets but are NOT twins — e.g. a
    connector tying the channels together) is excluded, so its uuid can never
    be mistaken for a channel (found live 2026-08-17 on 3CH-AWG-TIA: the root
    sheet uuid a49990ef... was being mapped to every channel name)."""
    out: set[str] = set()
    for members in groups.values():
        if len(members) >= 2:
            out.update(members.keys())
    return out


def resolve_channel_uuids(adapter, src_uuid: str, src_channel: str,
                          dst_channels: list[str],
                          groups: dict[str, dict[str, str]] | None = None) -> dict[str, str]:
    """Map channel names to their sheet UUIDs, using the local-net prefix of
    footprints' pads (the channel name is verified against the pivot's channel
    uuid, exactly as the plan's "имя канала из CLI, сверка с префиксом
    локальной цепи пада пивота").

    When groups (the twin map) is given, only footprints whose path[0] is a
    REAL channel sheet uuid (appears in a 2+ member twin group) contribute —
    components of a shared root sheet carrying /Channel_N/ local nets are
    ignored (see _channel_sheet_uuids).

    Returns {src_channel: src_uuid, dst_channel: dst_uuid, ...}. Fatal when a
    dst channel is not present on the board, or when the src name resolves to a
    uuid different from the pivot's own channel uuid (name/uuid mismatch)."""
    channel_uuids = _channel_sheet_uuids(groups) if groups is not None else None
    name_to_uuid: dict[str, str] = {}
    for fp in adapter.get_footprints():
        name = _channel_name_of_fp(adapter, fp)
        if name is None:
            continue
        chain = _path_uuids(fp)
        if not chain:
            continue
        ch_uuid = chain[0]
        if channel_uuids is not None and ch_uuid not in channel_uuids:
            continue
        prev = name_to_uuid.setdefault(name, ch_uuid)
        if prev != ch_uuid:
            raise ValidationError(format_fatal_error(
                _("channel name {name!r} is ambiguous on the board").format(name=name),
                [_("it maps to more than one sheet uuid ({a} and {b}) — the "
                   "channels cannot be told apart by local nets").format(a=prev, b=ch_uuid)]))

    if src_channel not in name_to_uuid:
        # The vias/tracks of the copy are filtered by the literal /src_channel/
        # prefix, so an unknown --src must be FATAL, not silently skipped —
        # otherwise a typo would move the components (filtered by the pivot's
        # real uuid) while quietly dropping ALL vias and tracks.
        raise ValidationError(format_fatal_error(
            _("source channel {src!r} not found on the board").format(src=src_channel),
            [_("no footprint with a local net of channel {src!r} was found — the copy's "
               "vias/tracks are filtered by this name, so a typo in --src would "
               "silently drop them all; check --src / --pivot").format(src=src_channel)]))
    if name_to_uuid[src_channel] != src_uuid:
        raise ValidationError(format_fatal_error(
            _("source channel {src!r} does not match the pivot's channel").format(src=src_channel),
            [_("the pivot's sheet uuid is {pivot_uuid} but channel {src!r} resolved "
               "to {resolved_uuid} on this board — check --src / --pivot")
             .format(pivot_uuid=src_uuid, src=src_channel, resolved_uuid=name_to_uuid[src_channel])]))

    result = {src_channel: src_uuid}
    for dst in dst_channels:
        if dst not in name_to_uuid:
            raise ValidationError(format_fatal_error(
                _("destination channel {dst!r} not found on the board").format(dst=dst),
                [_("no footprint with a local net of channel {dst!r} was found — "
                   "is the channel's schematic present and annotated?").format(dst=dst)]))
        result[dst] = name_to_uuid[dst]
    return result


# ── Task 2.2: transform resolution ───────────────────────────────────────────


@dataclass
class ChannelTransform:
    """Rigid transform for the whole construction: rotate about anchor_src by
    angle_deg, mirror about the vertical axis through anchor_src (optional),
    then translate by (anchor_dst - anchor_src)."""
    anchor_src: Vector2
    anchor_dst: Vector2
    angle_deg: float
    mirror: bool = False


def _resolve_pivot_anchor(adapter, fp, pad_number: str | None) -> Vector2:
    """anchor point of a pivot footprint: pad centre when --pivot-pad is given,
    footprint centre otherwise."""
    if pad_number is None:
        return fp.position
    pad = adapter.get_pad_by_number(fp, pad_number)
    if pad is None:
        raise ValidationError(format_fatal_error(
            _("pad {pad!r} not found on pivot {ref!r}").format(pad=pad_number,
                                                               ref=fp.reference_field.text.value),
            [_("--pivot-pad must name an existing pad number of the pivot footprint")]))
    return pad.position


def resolve_transform(adapter, *, pivot_ref: str | None, pivot_role: str | None,
                      pivot_pad: str | None, src_uuid: str, src_channel: str,
                      dst_uuid: str, groups: dict[str, dict[str, str]],
                      offset: tuple[float, float] = (0.0, 0.0),
                      target_dst: tuple[float, float] | None = None,
                      src_point: tuple[float, float] | None = None,
                      dst_point: tuple[float, float] | None = None,
                      angle_deg: float = 0.0, mirror: bool = False) -> ChannelTransform:
    """Resolve the transform's anchor pair. Exactly one of the two reference
    modes must be given:
      - pivot mode (--pivot REF or --pivot-role ROLE): anchor_src = current
        position of the pivot on the source, anchor_dst = position of the
        pivot's twin on the dst (+ --offset; --target-dst X,Y overrides it
        explicitly when the twin is not placed yet);
      - points mode (--src-point X,Y --dst-point X,Y): no component involved.
    Fatal on conflict or when none is given."""
    modes = [pivot_ref is not None, pivot_role is not None,
             src_point is not None or dst_point is not None]
    if sum(1 for m in modes if m) != 1:
        raise ValidationError(format_fatal_error(
            _("exactly one reference mode is required for channel-copy"),
            [_("give --pivot REF (or --pivot-role ROLE), or --src-point X,Y with "
               "--dst-point X,Y — and not more than one of them")]))

    if src_point is not None or dst_point is not None:
        if src_point is None or dst_point is None:
            raise ValidationError(format_fatal_error(
                _("--src-point and --dst-point must be given together"),
                [_("points mode needs both endpoints of the anchor shift")]))
        anchor_src = Vector2.from_xy_mm(*src_point)
        anchor_dst = Vector2.from_xy_mm(*dst_point)
        return ChannelTransform(anchor_src=anchor_src, anchor_dst=anchor_dst,
                                angle_deg=angle_deg, mirror=mirror)

    # pivot mode
    if pivot_ref is not None:
        pivot_fp = adapter.get_footprint(pivot_ref)
        if pivot_fp is None:
            raise ValidationError(format_fatal_error(
                _("pivot footprint {ref!r} not found on the board").format(ref=pivot_ref),
                [_("channel-copy needs a pivot component that exists on the live board")]))
    else:
        # --pivot-role: find the unique footprint on the source channel with
        # this Role field.
        candidates = []
        for fp in adapter.get_footprints():
            chain = _path_uuids(fp)
            if not chain or chain[0] != src_uuid:
                continue
            if adapter.get_field_value(fp, ROLE_FIELD_NAME) == pivot_role:
                candidates.append(fp)
        if not candidates:
            raise ValidationError(format_fatal_error(
                _("no footprint with role {role!r} on the source channel").format(role=pivot_role),
                [_("--pivot-role must match the Role field of a source-channel component")]))
        if len(candidates) > 1:
            raise ValidationError(format_fatal_error(
                _("role {role!r} is ambiguous on the source channel").format(role=pivot_role),
                [_("more than one source-channel footprint has this role — use "
                   "--pivot with a refdes instead")]))
        pivot_fp = candidates[0]

    ref = pivot_fp.reference_field.text.value
    anchor_src = _resolve_pivot_anchor(adapter, pivot_fp, pivot_pad)

    inner = "/" + "/".join(_path_uuids(pivot_fp)[1:])
    twin_ref = groups.get(inner, {}).get(dst_uuid)
    if target_dst is not None:
        # --offset applies to an explicit --target-dst too — it is documented as
        # "extra shift added to the pivot's destination position", no exception.
        anchor_dst = Vector2.from_xy_mm(target_dst[0] + offset[0],
                                        target_dst[1] + offset[1])
    elif twin_ref is not None:
        twin_fp = adapter.get_footprint(twin_ref)
        if twin_fp is None:
            # twin in the map but not on the board — fall back to explicit point
            raise ValidationError(format_fatal_error(
                _("twin {twin!r} of pivot {ref!r} is in the twin map but not on the board")
                .format(twin=twin_ref, ref=ref),
                [_("give --target-dst X,Y to place the construction at an explicit point "
                   "instead")]))
        anchor_dst = _resolve_pivot_anchor(adapter, twin_fp, pivot_pad)
        anchor_dst = Vector2.from_xy(anchor_dst.x + int(offset[0] * MM),
                                     anchor_dst.y + int(offset[1] * MM))
    else:
        raise ValidationError(format_fatal_error(
            _("pivot {ref!r} has no twin on the destination channel").format(ref=ref),
            [_("no footprint with the pivot's inner key was found on the destination "
               "channel — is the dst channel present on the board? If the twin is not "
               "placed yet, give --target-dst X,Y explicitly")]))

    return ChannelTransform(anchor_src=anchor_src, anchor_dst=anchor_dst,
                            angle_deg=angle_deg, mirror=mirror)


# ── Task 2.3: geometry transformation ────────────────────────────────────────


def transform_point(p: Vector2, tr: ChannelTransform) -> Vector2:
    """X' = Mirror_x(anchor_src, R(angle)·(X − anchor_src)) + anchor_dst (mirror
    only when tr.mirror). Rotation uses the engine's own rotate_local_offset
    convention (KiCad Y-down). Order of operations (documented): rotate first,
    then mirror — they do not commute."""
    dx_mm = (p.x - tr.anchor_src.x) / MM
    dy_mm = (p.y - tr.anchor_src.y) / MM
    rotated = rotate_local_offset(dx_mm, dy_mm, tr.angle_deg)
    if tr.mirror:
        rotated = Vector2.from_xy(-rotated.x, rotated.y)
    return Vector2.from_xy(tr.anchor_dst.x + rotated.x, tr.anchor_dst.y + rotated.y)


def transform_angle(angle_deg: float, tr: ChannelTransform) -> float:
    """rot' = (180° − (rot + angle)) mod 360° with mirror, (rot + angle) mod
    360° without — the same convention as ClonePlacement.mirror
    (geometry/clone_geometry.py comp_angle)."""
    phi = angle_deg + tr.angle_deg
    return (180.0 - phi) % 360.0 if tr.mirror else phi % 360.0


def transform_layer(layer: BoardLayer, tr: ChannelTransform) -> BoardLayer:
    """With --mirror the layer of every copied element is inverted (F.Cu<->B.Cu)
    — the same physical rule as ClonePlacement.mirror ("mirror without layer
    change is physically meaningless"). Without mirror the layer passes through."""
    if not tr.mirror:
        return layer
    return BoardLayer.BL_B_Cu if layer == BoardLayer.BL_F_Cu else BoardLayer.BL_F_Cu


def _point_close(a: Vector2, b: Vector2) -> bool:
    """Two points match within POSITION_TOLERANCE_MM (0.01 mm), in nm."""
    return (abs(a.x - b.x) <= POSITION_TOLERANCE_MM * MM
            and abs(a.y - b.y) <= POSITION_TOLERANCE_MM * MM)


def _angle_close(a_deg: float, b_deg: float) -> bool:
    """Two angles match within ANGLE_TOLERANCE_DEG (0.1°), wrapping."""
    return abs((a_deg - b_deg + 180) % 360 - 180) <= ANGLE_TOLERANCE_DEG


def _twin_net(net: str, src_ch: str, dst_ch: str) -> str:
    """Local net -> twin net, DELEGATING to TwinMap.twin_net
    (cloner/models.py) — the single place the remap logic lives. TwinMap is
    used as a namespace: its twin_net ignores self (it is a pure function of
    (net, src_ch, dst_ch)), so it is called with an explicit None."""
    return TwinMap.twin_net(None, net, src_ch, dst_ch)


# ── Tasks 2.4-2.7: planning ──────────────────────────────────────────────────


@dataclass
class ForeignReport:
    """Global (non-channel) copper found inside the source channel's bbox."""
    segments: int = 0
    vias: int = 0
    nets: set[str] = field(default_factory=set)
    include_global: bool = False


@dataclass
class ChannelCopyPlan:
    """The whole copy plan for one dst channel — printed verbatim by dry-run,
    executed (moves -> refresh -> vias -> tracks) by execute_channel_copy."""
    src_channel: str
    dst_channel: str
    transform: ChannelTransform
    moves: list[MoveCommand] = field(default_factory=list)
    vias: list[ViaCommand] = field(default_factory=list)
    tracks: list[TrackCommand] = field(default_factory=list)
    foreign: ForeignReport = field(default_factory=ForeignReport)


def _fp_matches_position(fp, position: Vector2, angle_deg: float, layer: BoardLayer) -> bool:
    """Idempotency of a component move: skip when the twin already stands at
    the target position/angle/layer within the tolerances."""
    return (_point_close(fp.position, position)
            and _angle_close(fp.orientation.degrees, angle_deg)
            and fp.layer == layer)


def _via_already_exists(live_vias, position: Vector2, net_name: str) -> bool:
    """Idempotency of a via: skip when a via already sits at the position on
    the same net within the tolerance (position+net, no registry)."""
    for v in live_vias:
        live_net = v.net.name if v.net else None
        if live_net != net_name:
            continue
        if _point_close(v.position, position):
            return True
    return False


def plan_channel_copy(adapter, *, src_uuid: str, dst_uuid: str,
                      src_channel: str, dst_channel: str,
                      transform: ChannelTransform,
                      groups: dict[str, dict[str, str]],
                      include_global: bool = False) -> ChannelCopyPlan:
    """Plan the whole copy of src_channel -> dst_channel. Returns a
    ChannelCopyPlan WITHOUT touching the board:
      - footprints of the source (path[0] == src_uuid) -> MoveCommand for the
        twin on the dst (dst_ref from groups), skipping twins already standing
        at the target;
      - vias/tracks of the source (net prefix /src_channel/) -> transformed
        ViaCommand/TrackCommand with the net remapped via twin_net, skipping
        ones already present at the target (position+net for vias,
        track_matches for tracks);
      - global copper inside the source bbox (foreign) — reported (warn),
        copied too only when include_global is set (net NOT remapped)."""
    all_fps = adapter.get_footprints()
    live_vias = adapter.get_vias()
    live_tracks = adapter.get_tracks()

    src_prefix = f"/{src_channel}/"
    plan = ChannelCopyPlan(src_channel=src_channel, dst_channel=dst_channel,
                           transform=transform)
    missing_twins = []

    # ── Task 2.4: component moves ───────────────────────────────────────────
    fp_by_ref = {fp.reference_field.text.value: fp for fp in all_fps}
    for fp in all_fps:
        chain = _path_uuids(fp)
        if not chain or chain[0] != src_uuid:
            continue
        inner = "/" + "/".join(chain[1:])
        dst_ref = groups.get(inner, {}).get(dst_uuid)
        ref = fp.reference_field.text.value
        if dst_ref is None:
            missing_twins.append(ref)
            continue
        new_pos = transform_point(fp.position, transform)
        new_angle = transform_angle(fp.orientation.degrees, transform)
        new_layer = transform_layer(fp.layer, transform)
        dst_fp = fp_by_ref.get(dst_ref)
        if dst_fp is not None and _fp_matches_position(dst_fp, new_pos, new_angle, new_layer):
            logger.debug(_("  {ref}: already at the target position, skipped").format(ref=dst_ref))
            continue
        plan.moves.append(MoveCommand(ref=dst_ref, position=new_pos,
                                      angle=Angle.from_degrees(new_angle),
                                      layer=new_layer))
        logger.info(_("  move {src_ref} -> {dst_ref}: ({x:.3f}, {y:.3f}) mm, angle={a:.1f}°")
                    .format(src_ref=ref, dst_ref=dst_ref,
                            x=new_pos.x / MM, y=new_pos.y / MM, a=new_angle))

    if missing_twins:
        logger.warning(_("No twin on {dst} for {count} source footprints (skipped): {refs}")
                       .format(dst=dst_channel, count=len(missing_twins),
                               refs=", ".join(sorted(missing_twins))))

    # ── Task 2.5: vias, Task 2.6: tracks ────────────────────────────────────
    for v in live_vias:
        net = v.net.name if v.net else None
        if net is None or not net.startswith(src_prefix):
            continue
        new_pos = transform_point(v.position, transform)
        new_net = _twin_net(net, src_channel, dst_channel)
        if _via_already_exists(live_vias, new_pos, new_net):
            logger.debug(_("  via ({x:.3f}, {y:.3f}) mm, net={net}: already exists, skipped")
                         .format(x=new_pos.x / MM, y=new_pos.y / MM, net=new_net))
            continue
        plan.vias.append(ViaCommand(
            position=new_pos,
            drill_mm=v.drill_diameter / MM,
            diameter_mm=v.diameter / MM,
            net_name=new_net,
            owner_ref=dst_channel,
            registry_key=None,  # channel-copy never participates in the registry
        ))
        logger.info(_("  via ({x:.3f}, {y:.3f}) mm, net={net}")
                    .format(x=new_pos.x / MM, y=new_pos.y / MM, net=new_net))

    for t in live_tracks:
        net = t.net.name if t.net else None
        if net is None or not net.startswith(src_prefix):
            continue
        new_start = transform_point(t.start, transform)
        new_end = transform_point(t.end, transform)
        new_net = _twin_net(net, src_channel, dst_channel)
        new_layer = transform_layer(t.layer, transform)
        cmd = TrackCommand(start=new_start, end=new_end,
                           width_mm=t.width / MM, net_name=new_net,
                           layer=new_layer, owner_ref=dst_channel,
                           registry_key=None)
        if any(track_matches(live, cmd) for live in live_tracks):
            logger.debug(_("  track ({sx:.3f}, {sy:.3f}) -> ({ex:.3f}, {ey:.3f}) mm, "
                           "net={net}: already exists, skipped")
                         .format(sx=new_start.x / MM, sy=new_start.y / MM,
                                 ex=new_end.x / MM, ey=new_end.y / MM, net=new_net))
            continue
        plan.tracks.append(cmd)
        logger.info(_("  track ({sx:.3f}, {sy:.3f}) -> ({ex:.3f}, {ey:.3f}) mm, "
                      "net={net}, width={w} mm")
                    .format(sx=new_start.x / MM, sy=new_start.y / MM,
                            ex=new_end.x / MM, ey=new_end.y / MM,
                            net=new_net, w=cmd.width_mm))

    # ── Task 2.7: foreign copper inside the source bbox ─────────────────────
    report, foreign_vias, foreign_tracks = _plan_foreign(
        all_fps, live_vias, live_tracks, src_prefix, src_uuid, transform,
        include_global, dst_channel)
    plan.foreign = report
    plan.vias.extend(foreign_vias)
    plan.tracks.extend(foreign_tracks)
    if report.segments or report.vias:
        if include_global:
            logger.info(_("Foreign copper inside the source bbox WILL be copied "
                          "(--include-global): {segs} tracks, {vias} vias, nets: {nets}")
                        .format(segs=report.segments, vias=report.vias,
                                nets=", ".join(sorted(report.nets)) or "-"))
        else:
            logger.warning(_("Foreign copper inside the source bbox is NOT copied by "
                             "default: {segs} tracks, {vias} vias, nets: {nets}. "
                             "Rerun with --include-global to copy it too.")
                           .format(segs=report.segments, vias=report.vias,
                                   nets=", ".join(sorted(report.nets)) or "-"))

    logger.info(_("{src} -> {dst}: {moves} moves, {vias} vias, {tracks} tracks planned")
                .format(src=src_channel, dst=dst_channel,
                        moves=len(plan.moves), vias=len(plan.vias), tracks=len(plan.tracks)))
    return plan


def _plan_foreign(all_fps, live_vias, live_tracks, src_prefix: str,
                  src_uuid: str, transform: ChannelTransform,
                  include_global: bool, dst_channel: str
                  ) -> tuple[ForeignReport, list[ViaCommand], list[TrackCommand]]:
    """Global copper (net not starting with /src_channel/) whose start/end/centre
    lies inside the source channel's footprint bbox + 1 mm margin — same scan
    as cloner/pcb.py. Returns (report, vias, tracks): the vias/tracks are empty
    unless include_global is set (net NOT remapped — it is a global net)."""
    xs: list[int] = []
    ys: list[int] = []
    for fp in all_fps:
        chain = _path_uuids(fp)
        if chain and chain[0] == src_uuid:
            xs.append(fp.position.x)
            ys.append(fp.position.y)
    report = ForeignReport(include_global=include_global)
    if not xs:
        return report, [], []
    margin = int(_FOREIGN_BBOX_MARGIN_MM * MM)
    x0, x1 = min(xs) - margin, max(xs) + margin
    y0, y1 = min(ys) - margin, max(ys) + margin

    def inside(p: Vector2) -> bool:
        return x0 <= p.x <= x1 and y0 <= p.y <= y1

    foreign_vias: list[ViaCommand] = []
    foreign_tracks: list[TrackCommand] = []
    for v in live_vias:
        net = v.net.name if v.net else None
        if net is None or net.startswith(src_prefix) or not inside(v.position):
            continue
        report.vias += 1
        report.nets.add(net)
        if include_global:
            new_pos = transform_point(v.position, transform)
            if not _via_already_exists(live_vias, new_pos, net):
                foreign_vias.append(ViaCommand(position=new_pos,
                                               drill_mm=v.drill_diameter / MM,
                                               diameter_mm=v.diameter / MM,
                                               net_name=net,
                                               owner_ref=dst_channel))
    for t in live_tracks:
        net = t.net.name if t.net else None
        if net is None or net.startswith(src_prefix):
            continue
        if not (inside(t.start) or inside(t.end)):
            continue
        report.segments += 1
        report.nets.add(net)
        if include_global:
            new_start = transform_point(t.start, transform)
            new_end = transform_point(t.end, transform)
            new_layer = transform_layer(t.layer, transform)
            cmd = TrackCommand(start=new_start, end=new_end,
                               width_mm=t.width / MM, net_name=net,
                               layer=new_layer, owner_ref=dst_channel)
            if not any(track_matches(live, cmd) for live in live_tracks):
                foreign_tracks.append(cmd)
    return report, foreign_vias, foreign_tracks


# ── Task 2.8: execution ──────────────────────────────────────────────────────


def execute_channel_copy(adapter, plan: ChannelCopyPlan, *,
                         config: Config | None = None,
                         batch_size: int = DEFAULT_BATCH_SIZE,
                         check_collisions: bool = True,
                         collision_margin_mm: float = 0.2) -> tuple[list[str], list[str], list[str]]:
    """Apply a ChannelCopyPlan through BatchExecutor — moves, then vias, then
    tracks, ONE undo log for the whole copy run (the executor's default
    moves -> refresh -> vias -> tracks order, see plan_2026_08_15_
    channel_copy.md). channel-copy never uses the registry, so all commands are
    passed with registry_key=None (the executors are invoked without a
    registry — see placement/executor/batch_executor.py).

    Returns (failed_refs, failed_vias, failed_tracks)."""
    cfg = config or Config()
    executor = BatchExecutor(adapter, cfg, batch_size=batch_size)
    failed_refs, failed_vias, failed_tracks = executor.execute(
        plan.moves, plan.vias, plan.tracks,
        check_collisions=check_collisions,
        collision_margin_mm=collision_margin_mm)
    if failed_refs:
        logger.warning(_("Failed to move: {refs}").format(refs=sorted(set(failed_refs))))
    if failed_vias:
        logger.warning(_("Failed to create vias near: {refs}").format(refs=sorted(set(failed_vias))))
    if failed_tracks:
        logger.warning(_("Failed to create tracks near: {refs}").format(refs=sorted(set(failed_tracks))))
    return failed_refs, failed_vias, failed_tracks


# ── High-level convenience entry point (author.py style) ─────────────────────


def channel_copy(adapter, *, src: str, dst: str,
                 pivot: str | None = None, pivot_role: str | None = None,
                 pivot_pad: str | None = None,
                 offset: tuple[float, float] = (0.0, 0.0),
                 target_dst: tuple[float, float] | None = None,
                 src_point: tuple[float, float] | None = None,
                 dst_point: tuple[float, float] | None = None,
                 angle_deg: float = 0.0, mirror: bool = False,
                 include_global: bool = False,
                 dry_run: bool = False,
                 batch_size: int = DEFAULT_BATCH_SIZE,
                 check_collisions: bool = True,
                 collision_margin_mm: float = 0.2) -> ChannelCopyPlan:
    """Full channel copy: build the live twin map, resolve the transform, plan,
    and (unless dry_run) execute. Returns the ChannelCopyPlan — for dry-run the
    caller formats it into the report; for a real run the caller can still
    inspect what was planned.

    Reference modes (exactly one):
      - pivot mode: --pivot REF (with optional --pivot-pad P) or --pivot-role ROLE;
      - points mode: --src-point X,Y --dst-point X,Y.
    """
    has_pivot = pivot is not None or pivot_role is not None
    has_points = src_point is not None or dst_point is not None
    if has_pivot == has_points:
        raise ValidationError(format_fatal_error(
            _("channel_copy needs exactly one reference mode"),
            [_("give pivot (or pivot_role), or src_point+dst_point — not both and "
               "not neither")]))

    groups: dict[str, dict[str, str]] | None = None
    if has_pivot:
        if pivot is None and pivot_role is not None:
            groups = build_channel_groups(adapter)
            pivot = _find_pivot_ref_by_role(adapter, pivot_role, src, groups)
        src_uuid, groups = build_live_twin_map(adapter, pivot, groups=groups)
    else:
        groups = build_channel_groups(adapter)
        src_uuid = _src_uuid_by_channel(adapter, src, groups)

    channel_uuids = resolve_channel_uuids(adapter, src_uuid, src, [dst], groups=groups)
    dst_uuid = channel_uuids[dst]

    transform = resolve_transform(
        adapter,
        pivot_ref=pivot, pivot_role=None, pivot_pad=pivot_pad,
        src_uuid=src_uuid, src_channel=src, dst_uuid=dst_uuid,
        groups=groups,
        offset=offset, target_dst=target_dst,
        src_point=src_point, dst_point=dst_point,
        angle_deg=angle_deg, mirror=mirror)

    plan = plan_channel_copy(adapter, src_uuid=src_uuid, dst_uuid=dst_uuid,
                             src_channel=src, dst_channel=dst,
                             transform=transform, groups=groups,
                             include_global=include_global)
    if not dry_run:
        execute_channel_copy(adapter, plan, batch_size=batch_size,
                             check_collisions=check_collisions,
                             collision_margin_mm=collision_margin_mm)
    return plan


def _find_pivot_ref_by_role(adapter, role: str, src_channel: str,
                            groups: dict[str, dict[str, str]]) -> str:
    """Ref of the unique footprint with the given Role on the source channel —
    used when --pivot-role is given instead of --pivot. Only real channel sheet
    footprints (see _channel_sheet_uuids) whose local net matches src_channel
    are considered, so a shared root sheet can never leak a candidate."""
    channel_uuids = _channel_sheet_uuids(groups)
    candidates = []
    for fp in adapter.get_footprints():
        chain = _path_uuids(fp)
        if not chain or chain[0] not in channel_uuids:
            continue
        if _channel_name_of_fp(adapter, fp) != src_channel:
            continue
        if adapter.get_field_value(fp, ROLE_FIELD_NAME) == role:
            candidates.append(fp.reference_field.text.value)
    if not candidates:
        raise ValidationError(format_fatal_error(
            _("no footprint with role {role!r} on channel {ch!r}").format(role=role, ch=src_channel),
            [_("--pivot-role must match the Role field of a source-channel component")]))
    if len(candidates) > 1:
        raise ValidationError(format_fatal_error(
            _("role {role!r} is ambiguous on channel {ch!r}").format(role=role, ch=src_channel),
            [_("more than one source-channel footprint has this role — use --pivot "
               "with a refdes instead")]))
    return candidates[0]


def _src_uuid_by_channel(adapter, src_channel: str,
                         groups: dict[str, dict[str, str]]) -> str:
    """Sheet uuid of the source channel, resolved from local nets (used by the
    points mode where no pivot exists to anchor the twin map). Only real
    channel sheet footprints are considered (see _channel_sheet_uuids), so a
    shared root sheet with /Channel_N/ local nets cannot win."""
    channel_uuids = _channel_sheet_uuids(groups)
    for fp in adapter.get_footprints():
        chain = _path_uuids(fp)
        if not chain or chain[0] not in channel_uuids:
            continue
        if _channel_name_of_fp(adapter, fp) == src_channel:
            return chain[0]
    raise ValidationError(format_fatal_error(
        _("source channel {ch!r} not found on the board").format(ch=src_channel),
        [_("no footprint with a local net of channel {ch!r} was found — is the "
           "channel's schematic present and annotated?").format(ch=src_channel)]))


# ── Dry-run report formatting ────────────────────────────────────────────────


def format_channel_copy_report(plan: ChannelCopyPlan) -> list[str]:
    """Structured dry-run report for a ChannelCopyPlan — one line per planned
    move/via/track plus the transform header and the foreign-copper summary.
    The CLI prints it; a future GUI panel could render it."""
    lines: list[str] = []
    tr = plan.transform
    lines.append("\n=== CHANNEL COPY (DRY RUN) ===")
    lines.append(_("Copy: {src} -> {dst}").format(src=plan.src_channel, dst=plan.dst_channel))
    lines.append(_("Transform: anchor {sx:.3f},{sy:.3f} -> {dx:.3f},{dy:.3f} mm, "
                   "angle={a:.1f}°{mirror}")
                 .format(sx=tr.anchor_src.x / MM, sy=tr.anchor_src.y / MM,
                         dx=tr.anchor_dst.x / MM, dy=tr.anchor_dst.y / MM,
                         a=tr.angle_deg,
                         mirror=_(", mirrored") if tr.mirror else ""))
    lines.append(_("Moves ({count}):").format(count=len(plan.moves)))
    for m in plan.moves:
        lines.append(_("  {ref}: ({x:.3f}, {y:.3f}) mm, angle={angle:.1f}°")
                     .format(ref=m.ref, x=m.position.x / MM, y=m.position.y / MM,
                             angle=m.angle.degrees))
    lines.append(_("Vias ({count}):").format(count=len(plan.vias)))
    for v in plan.vias:
        lines.append(_("  via for {owner}: ({x:.3f}, {y:.3f}) mm, net={net}")
                     .format(owner=v.owner_ref, x=v.position.x / MM, y=v.position.y / MM,
                             net=v.net_name))
    lines.append(_("Tracks ({count}):").format(count=len(plan.tracks)))
    for t in plan.tracks:
        lines.append(_("  track for {owner}: ({sx:.3f}, {sy:.3f}) -> ({ex:.3f}, {ey:.3f}) mm, "
                       "net={net}, width={w} mm")
                     .format(owner=t.owner_ref, sx=t.start.x / MM, sy=t.start.y / MM,
                             ex=t.end.x / MM, ey=t.end.y / MM, net=t.net_name, w=t.width_mm))
    f = plan.foreign
    if f.segments or f.vias:
        lines.append(_("Foreign copper inside the source bbox: {segs} tracks, {vias} vias, "
                       "nets: {nets}{copied}")
                     .format(segs=f.segments, vias=f.vias,
                             nets=", ".join(sorted(f.nets)) or "-",
                             copied=_(" — will be copied (--include-global)")
                             if f.include_global else _(" — NOT copied (use --include-global to copy)")))
    else:
        lines.append(_("Foreign copper inside the source bbox: none"))
    return lines
