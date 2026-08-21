# kicadstamp/net_trace_planner.py
"""
net_trace_planner.py — apply/redraw side of net_traces: resolve each
record's anchor LIVE on the current board, expand the local along/across
offsets back to absolute positions, and produce TrackCommand/ViaCommand that
flow through the SAME registry-based idempotency as any Cell's copper.

Key decisions (see techdocs/handoff/deepseek/plan_2026_08_21_net_traces.md §3):
  - the anchor fields (anchor_role/anchor_sheet/anchor_cluster/anchor_pad) are
    the SAME set extract-net used as its origin — resolve_footprint_by_role,
    the shared Rule/ClonePlacement search over the whole live board;
  - local -> absolute via the shared local_to_absolute with rotation_deg=0
    (a net trace is a translation-following bundle, not a rotatable cell);
  - the registry IS used (unlike channel-copy): the record's net is a stable,
    unique config identity, so it becomes the registry key's template_name —
    standard idempotency (position change -> delete old UUID + create new),
    NO positional pre-check as the idempotency mechanism (see the plan's §4
    item 3).

adopt_net_trace_copper() — ONE-TIME ownership claim: a net trace is captured
from ALREADY-EXISTING hand-routed copper, so the very first apply (board
unchanged since extract) must not duplicate it. Before reconcile, any planned
net-trace via/track whose live item is already sitting exactly at the planned
position (and is not owned by another registry key) is claimed into the
registry, so reconcile sees "already correctly placed" and skips it — and
when the anchor later moves, reconcile deletes that same claimed UUID and
recreates it at the new position (no orphaned duplicate at the old place).
This is a one-time migration, NOT a per-run positional pre-check: after
adoption the registry owns the copper exactly like any Cell-created item.
"""
import logging

from kipy.board_types import BoardLayer

from .config import NetTrace
from .exceptions import ValidationError, format_fatal_error
from .geometry.spoke_layout import local_to_absolute
from .placement.commands import ViaCommand, TrackCommand
from .placement.services.clone_role_resolver import resolve_footprint_by_role
from .registry import make_registry_key, track_matches
from .i18n import _

logger = logging.getLogger(__name__)


def net_trace_anchor_id(nt: NetTrace) -> str:
    """Registry anchor_id for one net trace — `net:<net>`. The net is unique
    per record (load-time check) and stable, so it is a safe registry anchor
    id; it is also the registry-key protection prefix shared with
    apply_pipeline's _compute_all_anchor_ids (see registry.py reconcile's
    protected-prefix list)."""
    return f"net:{nt.net}"


def _layer_to_board(layer: str | None) -> BoardLayer:
    """'F.Cu'/'B.Cu' (or None -> default F.Cu) -> BoardLayer."""
    return BoardLayer.BL_B_Cu if layer == "B.Cu" else BoardLayer.BL_F_Cu


def _resolve_anchor(adapter, nt: NetTrace, sheet_names: dict[str, str]):
    """Resolve the anchor footprint (shared resolve_footprint_by_role search)
    and the anchor point (pad centre if anchor_pad, else footprint centre)."""
    label = _("net_traces entry (net {net!r})").format(net=nt.net)
    anchor_fp = resolve_footprint_by_role(
        adapter, nt.anchor_role, nt.anchor_sheet, nt.anchor_cluster,
        sheet_names, label=label,
    )
    if nt.anchor_pad is not None:
        pad = adapter.get_pad_by_number(anchor_fp, nt.anchor_pad)
        if pad is None:
            ref = anchor_fp.reference_field.text.value
            raise ValidationError(format_fatal_error(
                _("{label}: anchor pad {pad!r} not found on {ref}").format(
                    label=label, pad=nt.anchor_pad, ref=ref),
                [_("anchor_pad must name an existing pad number of the anchor "
                   "footprint; remove it to anchor on the footprint centre")]))
        return anchor_fp, pad.position
    return anchor_fp, anchor_fp.position


def plan_net_traces(adapter, net_traces: list[NetTrace],
                    sheet_names: dict[str, str] | None = None,
                    ) -> tuple[list[ViaCommand], list[TrackCommand]]:
    """Expand every active (non-retired, non-skip) NetTrace into absolute
    ViaCommand/TrackCommand, anchors resolved LIVE from the current board.

    Returns (vias, tracks). Every command carries a registry_key built from
    net_trace_anchor_id (see net_trace_anchor_id) so the standard registry
    reconcile/execute path gives idempotency and "follow the moved anchor".
    """
    _sn = sheet_names or {}
    vias: list[ViaCommand] = []
    tracks: list[TrackCommand] = []
    for nt in net_traces:
        if nt.retired or nt.skip:
            logger.info(_("net_traces entry (net {net!r}): retired/skip, not planned")
                        .format(net=nt.net))
            continue
        # NOTE: never name the discarded footprint `_` here — the i18n helper
        # is imported as `_` at module level, and an assignment would shadow it
        # for the whole function (UnboundLocalError on the logger calls above).
        _anchor_fp, anchor = _resolve_anchor(adapter, nt, _sn)
        anchor_id = net_trace_anchor_id(nt)
        for i, t in enumerate(nt.tracks):
            net_name = t.net or nt.net  # explicit; fall back to the record's net
            tracks.append(TrackCommand(
                start=local_to_absolute(anchor, t.start_along_mm, t.start_across_mm, 0.0),
                end=local_to_absolute(anchor, t.end_along_mm, t.end_across_mm, 0.0),
                width_mm=t.width_mm,
                net_name=net_name,
                layer=_layer_to_board(t.layer),
                owner_ref=nt.net,
                registry_key=make_registry_key(anchor_id, nt.net, None, i),
            ))
        for i, v in enumerate(nt.vias):
            net_name = v.net or nt.net
            vias.append(ViaCommand(
                position=local_to_absolute(anchor, v.offset_along_mm, v.offset_across_mm, 0.0),
                drill_mm=v.drill_mm,
                diameter_mm=v.diameter_mm,
                net_name=net_name,
                owner_ref=nt.net,
                registry_key=make_registry_key(anchor_id, nt.net, None, i),
            ))
        logger.info(_("net_traces entry (net {net!r}): {tracks} tracks, {vias} vias planned")
                    .format(net=nt.net, tracks=len(nt.tracks), vias=len(nt.vias)))
    return vias, tracks


def adopt_net_trace_copper(adapter, via_registry, track_registry,
                           net_trace_vias: list[ViaCommand],
                           net_trace_tracks: list[TrackCommand]) -> None:
    """ONE-TIME ownership claim of already-existing copper into the registries
    (see the module docstring — avoids duplicating hand-routed copper on the
    first apply after extract). Safe only for commands with a registry_key
    (net-trace commands always have one). A live item is claimed only when it
    matches the planned geometry AND its UUID is not already owned by ANY
    registry entry (either registry) — never steals copper another mechanism
    already owns."""
    owned_via_uuids = {e.uuid for e in via_registry.entries.values()}
    live_vias = adapter.get_vias()
    for cmd in net_trace_vias:
        if cmd.registry_key is None or cmd.registry_key in via_registry.entries:
            continue
        for v in live_vias:
            if str(v.id.value) in owned_via_uuids:
                continue
            if via_registry._live_matches(v, cmd):
                via_registry.entries[cmd.registry_key] = via_registry._build_entry(cmd, str(v.id.value))
                owned_via_uuids.add(str(v.id.value))
                logger.info(_("net_trace: adopted existing via ({x:.3f}, {y:.3f}) mm into the "
                              "placement registry").format(x=cmd.position.x / 1e6, y=cmd.position.y / 1e6))
                break

    owned_track_uuids = {e.uuid for e in track_registry.entries.values()}
    live_tracks = adapter.get_tracks()
    for cmd in net_trace_tracks:
        if cmd.registry_key is None or cmd.registry_key in track_registry.entries:
            continue
        for t in live_tracks:
            if str(t.id.value) in owned_track_uuids:
                continue
            if track_matches(t, cmd):
                track_registry.entries[cmd.registry_key] = track_registry._build_entry(cmd, str(t.id.value))
                owned_track_uuids.add(str(t.id.value))
                logger.info(_("net_trace: adopted existing track ({sx:.3f}, {sy:.3f}) -> "
                              "({ex:.3f}, {ey:.3f}) mm into the track registry")
                            .format(sx=cmd.start.x / 1e6, sy=cmd.start.y / 1e6,
                                    ex=cmd.end.x / 1e6, ey=cmd.end.y / 1e6))
                break

    via_registry._save_entries(via_registry.entries)
    track_registry._save_entries(track_registry.entries)


__all__ = [
    "net_trace_anchor_id",
    "plan_net_traces",
    "adopt_net_trace_copper",
]
