# kicadstamp/net_trace_planner.py
"""
net_trace_planner.py — apply/redraw side of net_traces: resolve each
record's anchor LIVE on the current board, expand the local along/across
offsets back to absolute positions, and produce TrackCommand/ViaCommand that
flow through the SAME registry-based idempotency as any Cell's copper.

Key decisions (see techdocs/handoff/deepseek/plan_2026_08_21_net_traces.md §3
and plan_2026_09_08_net_trace_rotation_aware.md):
  - the anchor fields (anchor_role/anchor_sheet/anchor_cluster/anchor_pad) are
    the SAME set extract-net used as its origin — resolve_footprint_by_role,
    the shared Rule/ClonePlacement search over the whole live board;
  - local -> absolute via the shared local_to_absolute, with rotation_deg =
    relative_rotation_deg(current_anchor_rotation, captured_anchor_rotation)
    when the record stores anchor_rotation_deg (rotation-aware, the same
    delta-composition tree extraction uses), else rotation_deg=0 for legacy
    pre-fix records — see NetTrace.anchor_rotation_deg in config/models.py;
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
from dataclasses import dataclass
from typing import Any

from .domain.geometry import BoardLayer

from .config import NetTrace, net_trace_effective_name
from .exceptions import ValidationError, format_fatal_error
from .geometry.spoke_layout import local_to_absolute
from .placement.commands import ViaCommand, TrackCommand
from .placement.services.clone_role_resolver import resolve_footprint_by_role
from .net_resolution import resolve_net_from_role
from .registry import make_registry_key, track_matches, via_matches
from .tree_position import relative_rotation_deg
from .i18n import _

logger = logging.getLogger(__name__)


def net_trace_anchor_id(nt: NetTrace) -> str:
    """Registry anchor_id for one net trace — `net:<identity>`, where the
    identity is net_trace_effective_name(nt): the record's own name:, or its
    net: on a legacy record without one (2026-09-12, plan_2026_09_12_internode_
    copper_core Э2; design §11). Unique per record (load-time check) and
    stable, so it is a safe registry anchor id; it is also the registry-key
    protection prefix shared with apply_pipeline's _compute_all_anchor_ids
    (see registry.py reconcile's protected-prefix list).

    The `net:` PREFIX is deliberately KEPT as it is (design §11): it is the
    registry's protection marker for this section, and renaming it to `trace:`
    would force a registry migration on both machines for pure cosmetics. On a
    legacy record the whole key is byte-identical to before."""
    return f"net:{net_trace_effective_name(nt)}"


def _layer_to_board(layer: str | None) -> BoardLayer:
    """'F.Cu'/'B.Cu' -> BoardLayer.

    Defensive only: a net-trace track with layer None/other is already a
    LOAD-TIME fatal (see _load_net_trace in config/entries.py — a net trace
    has no cell to inherit a layer from, so a missing layer must never
    silently default to F.Cu and route copper onto the wrong side). Reaching
    this branch means a directly-constructed NetTrace bypassed the loader."""
    if layer not in ('F.Cu', 'B.Cu'):
        raise ValidationError(format_fatal_error(
            _("net_traces track has invalid layer {layer!r}").format(layer=layer),
            [_("net_traces tracks need an absolute layer: 'F.Cu' or 'B.Cu' — "
               "extract-net always writes one; there is no cell to inherit "
               "from")]))
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
            ref = anchor_fp.ref
            raise ValidationError(format_fatal_error(
                _("{label}: anchor pad {pad!r} not found on {ref}").format(
                    label=label, pad=nt.anchor_pad, ref=ref),
                [_("anchor_pad must name an existing pad number of the anchor "
                   "footprint; remove it to anchor on the footprint centre")]))
        return anchor_fp, pad.position
    return anchor_fp, anchor_fp.position


def _item_net_name(adapter, nt: NetTrace, item, sheet_names: dict[str, str],
                   label: str) -> str:
    """The net of one track/via of a net trace, resolved LIVE.

    An item either carries a literal net (a legacy record, and the plain
    `item.net or nt.net` fall-back) or a (role, pad) REFERENCE —
    net_from_role / net_from_role_pad, the same pair a Cell's via/track uses
    (2026-09-12, plan_2026_09_12_internode_copper_core Э3.1; design §15).
    A reference is resolved HERE, at apply time, exactly like a Cell's: the
    role is searched over the whole board with the SAME narrowing the record's
    own anchor uses (anchor_sheet → anchor_cluster; each narrowing step only
    applies when it actually reduces the candidate set, see
    role_narrowing._narrow_by_sheet_cluster_selection), so a per-instance copy
    of the record (tree_instances) resolves against ITS own sheet while a
    cross-sheet end — the FPGA side of a channel bridge — still resolves by its
    unique role.

    resolve_net_from_role then takes the net of net_from_role_pad, or applies
    lemma 2 (exactly one non-rule net) when the pad is omitted — and stays
    fatal on an unresolved/ambiguous role, a missing pad or a multi-net role:
    apply stops, it never guesses. The record's own `net` is only a fall-back
    for an item that carries neither."""
    role = getattr(item, "net_from_role", None)
    if role is None:
        return item.net or nt.net
    fp = resolve_footprint_by_role(
        adapter, role, nt.anchor_sheet, nt.anchor_cluster, sheet_names,
        label=label)
    return resolve_net_from_role(role, getattr(item, "net_from_role_pad", None),
                                 {role: fp.ref}, adapter)


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
        anchor_fp, anchor = _resolve_anchor(adapter, nt, _sn)
        # Rotation-aware net trace (plan_2026_09_08_net_trace_rotation_aware.md):
        # the captured along/across deltas are a RAW board-frame difference, so
        # the correct placement composes the anchor's current-vs-captured delta
        # rotation. Legacy records (anchor_rotation_deg=None) keep rotation_deg
        # =0.0 exactly as before — 100% back-compat, nothing replays silently.
        rotation_deg = (relative_rotation_deg(anchor_fp.angle_deg, nt.anchor_rotation_deg)
                        if nt.anchor_rotation_deg is not None else 0.0)
        anchor_id = net_trace_anchor_id(nt)
        # The registry key's template_name component is the record's IDENTITY
        # (net_trace_effective_name) — for a legacy record that is exactly the
        # net it always was, so no existing key changes; for a named record it
        # keeps two bridges of one net apart (net: prefix included).
        identity = net_trace_effective_name(nt)
        label = _("net_traces entry (net {net!r})").format(net=identity)
        for i, t in enumerate(nt.tracks):
            # A literal net, or a (role, pad) reference resolved live — see
            # _item_net_name.
            net_name = _item_net_name(adapter, nt, t, _sn, label)
            tracks.append(TrackCommand(
                start=local_to_absolute(anchor, t.start_along_mm, t.start_across_mm, rotation_deg),
                end=local_to_absolute(anchor, t.end_along_mm, t.end_across_mm, rotation_deg),
                width_mm=t.width_mm,
                net_name=net_name,
                layer=_layer_to_board(t.layer),
                owner_ref=nt.net,
                registry_key=make_registry_key(anchor_id, identity, None, i),
            ))
        for i, v in enumerate(nt.vias):
            net_name = _item_net_name(adapter, nt, v, _sn, label)
            vias.append(ViaCommand(
                position=local_to_absolute(anchor, v.offset_along_mm, v.offset_across_mm, rotation_deg),
                drill_mm=v.drill_mm,
                diameter_mm=v.diameter_mm,
                net_name=net_name,
                owner_ref=nt.net,
                registry_key=make_registry_key(anchor_id, identity, None, i),
            ))
        logger.info(_("net_traces entry (net {net!r}): {tracks} tracks, {vias} vias planned")
                    .format(net=nt.net, tracks=len(nt.tracks), vias=len(nt.vias)))
    return vias, tracks


# ── Read-only matching: which live copper belongs to a net trace ──────────────
# Plan plan_2026_09_12_select_copper_by_record (Э1; design §12). The matching
# half of adopt_net_trace_copper is extracted HERE so the ownership-claiming
# apply path and the READ-ONLY "select copper on board" / "whose copper is
# this?" paths share ONE implementation. Two copies would drift, and then
# "select" would highlight copper that apply does not manage (the disease this
# project fixed twice on 2026-09-12: "Move to…" and the "Nets" tab).

VIA = "via"
TRACK = "track"

# Which tier found a piece of copper. The TIER ORDER IS STRICT: the registry is
# exact (it stores the uuid), geometry is a guess that can pair the wrong piece
# (same net, same width, same layer, a shared end — distance zero).
TIER_REGISTRY = "registry"
TIER_GEOMETRY = "geometry"


@dataclass(frozen=True)
class Expectation:
    """ONE expected piece of a net trace's copper.

    `registry_key` is the registry identity of the piece (see
    make_registry_key/net_trace_anchor_id) — tier 1 needs it even when the
    anchor cannot be resolved. `command` is the PLANNED geometry (tier 2); it is
    None when planning failed (or the record is retired/skip), in which case the
    piece can still be found by the registry alone.
    """

    kind: str           # VIA | TRACK
    index: int          # 0-based within the record's vias/tracks list
    registry_key: str
    command: Any | None = None


@dataclass(frozen=True)
class MatchedCopper:
    """The outcome of matching ONE Expectation against the live board:
    the live item (Track/Via), or None; and how it was found (TIER_*), or None
    when it was not found at all."""
    expectation: Expectation
    live: Any | None
    tier: str | None


@dataclass
class LiveCopper:
    """One record's expected copper and where each piece was found.

    `reason` is set when tier 2 (geometry) could not be attempted at all — the
    anchor did not resolve live, or the record is retired/skip. That is a normal
    answer ("there is nothing to match the geometry against"), never an
    exception: the registry tier still runs in full.
    """
    nt: NetTrace
    identity: str
    pieces: list[MatchedCopper]
    reason: str | None = None

    @property
    def found(self) -> list[Any]:
        """The live board items (Track/Via) this record owns."""
        return [p.live for p in self.pieces if p.live is not None]

    @property
    def missing_count(self) -> int:
        """Expected pieces with NO live copper on the board — a normal result
        (the copper may have been partially erased), never an error."""
        return sum(1 for p in self.pieces if p.live is None)

    @property
    def expected_count(self) -> int:
        return len(self.pieces)

    @property
    def tiers(self) -> set[str]:
        return {p.tier for p in self.pieces if p.tier is not None}

    @property
    def found_by_registry(self) -> int:
        return sum(1 for p in self.pieces if p.tier == TIER_REGISTRY)

    @property
    def found_by_geometry(self) -> int:
        return sum(1 for p in self.pieces if p.tier == TIER_GEOMETRY)


def match_net_trace_pieces(adapter, expectations: list[Expectation], *,
                           via_registry, track_registry) -> list[MatchedCopper]:
    """THE single read-only matching half shared by adopt_net_trace_copper and
    find_live_copper (plan Э1). WRITES NOTHING — not the registries, not the
    board, not the config.

    Strict tier order (design §12, plan P.2):
      1. REGISTRY — the key's stored uuid, resolved against the live board;
      2. GEOMETRY — the shared track_matches/via_matches predicate against the
         planned command, for copper no registry entry knows.
    A piece found neither way is returned with live=None (partial/missing is a
    valid answer, not an error). Tier 2 NEVER takes a uuid owned by ANY registry
    entry (either record) — the same no-stealing rule adoption has always had —
    and never reuses a live item already taken by another piece of this call.
    """
    expectations = list(expectations)
    if not expectations:
        return []

    live_vias = adapter.get_vias()
    live_tracks = adapter.get_tracks()
    live_by_kind = {
        VIA: {v.uuid: v for v in live_vias},
        TRACK: {t.uuid: t for t in live_tracks},
    }
    registry_by_kind = {VIA: via_registry, TRACK: track_registry}
    owned_by_kind = {
        VIA: {e.uuid for e in via_registry.entries.values()},
        TRACK: {e.uuid for e in track_registry.entries.values()},
    }
    taken: dict[str, set[str]] = {VIA: set(), TRACK: set()}

    out: list[MatchedCopper] = []
    for exp in expectations:
        kind = exp.kind
        live_item = None
        tier = None

        # Tier 1 — the registry knows the uuid of the copper THIS record placed.
        registry = registry_by_kind[kind]
        entry = registry.entries.get(exp.registry_key) if exp.registry_key else None
        if entry is not None:
            candidate = live_by_kind[kind].get(entry.uuid)
            if candidate is not None and candidate.uuid not in taken[kind]:
                live_item, tier = candidate, TIER_REGISTRY

        # Tier 2 — geometry, for copper no registry entry knows. Only run when
        # the planned command exists (the anchor resolved live).
        if live_item is None and exp.command is not None:
            matcher = via_matches if kind == VIA else track_matches
            blocked = owned_by_kind[kind] | taken[kind]
            for candidate in (live_vias if kind == VIA else live_tracks):
                if candidate.uuid in blocked:
                    continue
                if matcher(candidate, exp.command):
                    live_item, tier = candidate, TIER_GEOMETRY
                    break

        if live_item is not None:
            taken[kind].add(live_item.uuid)
        out.append(MatchedCopper(expectation=exp, live=live_item, tier=tier))
    return out


def find_live_copper(adapter, nt: NetTrace, *, via_registry, track_registry,
                     sheet_names: dict[str, str] | None = None) -> LiveCopper:
    """READ-ONLY: find the live board copper of ONE `net_traces:` record.

    Plan `plan_2026_09_12_select_copper_by_record` Э1/Э2. Tier 1 (registry uuid)
    runs EVEN when the anchor cannot be resolved live — the registry needs no
    geometry. Tier 2 (geometry) then plans the record through the SAME
    plan_net_traces apply uses, so "select" can never highlight different copper
    than apply manages (the "one mechanism" contract, checked by a test).
    When planning fails (unresolvable anchor) or the record is retired/skip, the
    returned LiveCopper carries `reason` and the pieces found by registry only —
    a normal answer, never an exception.

    Nothing is written anywhere.
    """
    identity = net_trace_effective_name(nt)
    anchor_id = net_trace_anchor_id(nt)
    _sn = dict(sheet_names or {})

    planned_vias: list[ViaCommand] | None = None
    planned_tracks: list[TrackCommand] | None = None
    reason: str | None = None
    if nt.retired or nt.skip:
        reason = _(
            "the record is retired/skip — apply does not place it, so only the "
            "registry was consulted")
    else:
        try:
            planned_vias, planned_tracks = plan_net_traces(
                adapter, [nt], sheet_names=_sn)
        except Exception as e:  # noqa: BLE001 — a read must never raise here
            reason = _(
                "the anchor could not be resolved live ({error}) — the geometry "
                "cannot be matched").format(error=e)

    expectations: list[Expectation] = []
    for i in range(len(nt.vias)):
        command = (planned_vias[i]
                   if planned_vias is not None and i < len(planned_vias) else None)
        expectations.append(Expectation(
            kind=VIA, index=i,
            registry_key=make_registry_key(anchor_id, identity, None, i),
            command=command))
    for i in range(len(nt.tracks)):
        command = (planned_tracks[i]
                   if planned_tracks is not None and i < len(planned_tracks) else None)
        expectations.append(Expectation(
            kind=TRACK, index=i,
            registry_key=make_registry_key(anchor_id, identity, None, i),
            command=command))

    pieces = match_net_trace_pieces(adapter, expectations,
                                    via_registry=via_registry,
                                    track_registry=track_registry)
    return LiveCopper(nt=nt, identity=identity, pieces=pieces, reason=reason)


def adopt_net_trace_copper(adapter, via_registry, track_registry,
                           net_trace_vias: list[ViaCommand],
                           net_trace_tracks: list[TrackCommand]) -> None:
    """ONE-TIME ownership claim of already-existing copper into the registries
    (see the module docstring — avoids duplicating hand-routed copper on the
    first apply after extract). Safe only for commands with a registry_key
    (net-trace commands always have one).

    Delegates the matching to match_net_trace_pieces — the SAME routine
    find_live_copper uses (plan_2026_09_12_select_copper_by_record Э1), so the
    claiming path and the read-only "which copper is this record's" path can
    never disagree. A piece found by GEOMETRY is a live item no registry entry
    owns yet — that is exactly the item to claim. A REGISTRY hit is the record's
    own already-owned copper (nothing to do), and a miss claims nothing."""
    expectations: list[Expectation] = []
    for i, cmd in enumerate(net_trace_vias):
        if cmd.registry_key is not None:
            expectations.append(Expectation(VIA, i, cmd.registry_key, cmd))
    for i, cmd in enumerate(net_trace_tracks):
        if cmd.registry_key is not None:
            expectations.append(Expectation(TRACK, i, cmd.registry_key, cmd))

    matched = match_net_trace_pieces(adapter, expectations,
                                     via_registry=via_registry,
                                     track_registry=track_registry)
    for piece in matched:
        # Only a GEOMETRY hit is unclaimed copper; REGISTRY is the record's own
        # entry and a miss adopts nothing.
        if piece.tier != TIER_GEOMETRY or piece.live is None:
            continue
        exp = piece.expectation
        registry = via_registry if exp.kind == VIA else track_registry
        if exp.registry_key in registry.entries:
            continue  # claimed earlier in this same call
        registry.entries[exp.registry_key] = registry._build_entry(
            exp.command, piece.live.uuid)
        if exp.kind == VIA:
            logger.info(_("net_trace: adopted existing via ({x:.3f}, {y:.3f}) mm into the "
                          "placement registry").format(x=exp.command.position.x / 1e6,
                                                       y=exp.command.position.y / 1e6))
        else:
            logger.info(_("net_trace: adopted existing track ({sx:.3f}, {sy:.3f}) -> "
                          "({ex:.3f}, {ey:.3f}) mm into the track registry")
                        .format(sx=exp.command.start.x / 1e6, sy=exp.command.start.y / 1e6,
                                ex=exp.command.end.x / 1e6, ey=exp.command.end.y / 1e6))

    via_registry._save_entries(via_registry.entries)
    track_registry._save_entries(track_registry.entries)


# The live anchor reader, exposed for the capture/re-read path
# (kicadstamp/internode_capture.py): a re-read must measure the fresh copper
# against the SAME anchor the apply path will use, or the record it writes
# would not round-trip.
resolve_live_anchor = _resolve_anchor


__all__ = [
    "Expectation",
    "LiveCopper",
    "MatchedCopper",
    "TIER_GEOMETRY",
    "TIER_REGISTRY",
    "TRACK",
    "VIA",
    "adopt_net_trace_copper",
    "find_live_copper",
    "match_net_trace_pieces",
    "net_trace_anchor_id",
    "plan_net_traces",
    "resolve_live_anchor",
]
