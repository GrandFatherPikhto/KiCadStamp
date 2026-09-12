# gui/docks/copper_select.py
"""
Read-only live-copper linking between a `net_traces:` record and the board
(plan_2026_09_12_select_copper_by_record, stage Э2; design §12.1).

"Select this record's copper on the board" is a READ-ONLY action in the sense
that matters: `adapter.select_items()` sets the PCB editor's SELECTION, which is
editor UI state, not board data — no `begin_commit`, no copper edit, no config
write. The registries are opened READ-ONLY too (constructed but never saved).

Everything that touches the live board runs on the WORKER thread
(gui/worker.start_long_op), the shared kipy socket's only in-flight owner, so the
UI never freezes and a second IPC request cannot interleave into this one.

The matching itself is `kicadstamp.net_trace_planner.find_live_copper` — the same
routine `apply` adopts copper through, so "select" can never point at different
copper than a redraw manages (the "one mechanism" contract).
"""
import logging
from dataclasses import dataclass, field

from kicadstamp.i18n import _

logger = logging.getLogger(__name__)


def _live_adapter():
    """A fresh live-board adapter for the worker. Same construction the other
    live-reading workers use (trees_dock.run_internode_reread_worker,
    cascade), so the whole op owns the board connection on the worker thread."""
    from kicadstamp.kicad.adapter import KiCadBoardAdapter
    adapter = KiCadBoardAdapter(timeout_ms=20000)
    adapter.refresh_board()
    return adapter


def _readonly_registries(adapter, config_path):
    """The via/track registries of `config_path`, LOADED ONLY. Nothing here ever
    calls `_save_entries` — the read path must not claim ownership (plan P.3.2)."""
    from kicadstamp.registry import (PlacementRegistry, TrackRegistry,
                                     registry_path_for_config,
                                     track_registry_path_for_config)
    path = str(config_path)
    return (PlacementRegistry(adapter, registry_path_for_config(path)),
            TrackRegistry(adapter, track_registry_path_for_config(path)))


def resolve_record(cfg, *, identity=None, net=None):
    """(NetTrace | None, error | None) — ONE `net_traces:` record for the action.

    `identity` is the record's own identity (`name:`, else a legacy `net:`), the
    exact string a tree node's ref holds; `net` is the form's net. A `net` with
    two records (two bridges of one net, legal since 2026-09-12) is an honest
    ambiguity error, never a silent guess. Pure, Qt-free."""
    from kicadstamp.config import net_trace_effective_name

    records = list(getattr(cfg, "net_traces", None) or [])
    if identity:
        matches = [nt for nt in records if net_trace_effective_name(nt) == identity]
        if len(matches) == 1:
            return matches[0], None
        return None, _("no net_traces record {name!r} in the config").format(
            name=identity)
    if not net:
        return None, _("no net_traces record is selected")
    matches = [nt for nt in records if nt.net == net]
    if len(matches) == 1:
        return matches[0], None
    if not matches:
        return None, _("no net_traces record for net {net!r} in the config").format(
            net=net)
    return None, _(
        "net {net!r} has {count} net_traces records — open one in the Config "
        "tree first").format(net=net, count=len(matches))


def _tier_label(by_registry: int, by_geometry: int):
    """Human wording for WHICH search tier found the copper. "by geometry" is
    worth knowing: it means the copper has not been adopted by the registry yet
    (hand-routed since the last apply)."""
    if by_registry and by_geometry:
        return _("by registry and geometry")
    if by_registry:
        return _("by registry")
    if by_geometry:
        return _("by geometry")
    return None


def run_select_record_copper_worker(payload: dict) -> dict:
    """start_long_op worker entry point for "Select copper on board" (Э2).
    Plain data in, plain data out; every live-board read/write (the adapter and
    the selection) happens HERE, never on the UI thread.

    The selection is REPLACED wholesale by adapter.select_items() — the report
    carries the previous selection size so the UI can warn about it in the Log.
    """
    from kicadstamp.domain.board import Track, Via
    from kicadstamp.net_trace_planner import find_live_copper

    adapter = _live_adapter()
    via_registry, track_registry = _readonly_registries(adapter, payload["config_path"])
    result = find_live_copper(adapter, payload["record"],
                              via_registry=via_registry,
                              track_registry=track_registry,
                              sheet_names=payload.get("sheet_names") or {})
    # Read the CURRENT selection before replacing it (select_items clears it).
    try:
        previous = len(adapter.get_selected_items() or [])
    except Exception:  # noqa: BLE001 — a selection read can never block the action
        previous = 0
    items = result.found
    adapter.select_items(items)
    return {
        "identity": result.identity,
        "found": len(items),
        "expected": result.expected_count,
        "missing": result.missing_count,
        "tracks": sum(1 for i in items if isinstance(i, Track)),
        "vias": sum(1 for i in items if isinstance(i, Via)),
        "tier": _tier_label(result.found_by_registry, result.found_by_geometry),
        "reason": result.reason,
        "previous_selection": previous,
    }


def select_copper_report_lines(report: dict) -> list[str]:
    """The action's outcome as LOG LINES (never a dialog — design §12/plan Э2).
    Full record name; the tier is named; "nothing found" is a normal answer with
    its reason, not an error."""
    name = report.get("identity") or ""
    found = int(report.get("found") or 0)
    expected = int(report.get("expected") or 0)
    reason = report.get("reason")

    if found == 0:
        lines = [_("Board copper for {name!r}: not found.").format(name=name)]
        if expected == 0:
            lines.append(_("  the record stores no tracks or vias"))
        else:
            lines.append(_("  none of the {count} expected copper piece(s) is on "
                           "the board").format(count=expected))
        if reason:
            lines.append("  " + reason)
    else:
        lines = [_("Board copper for {name!r}: selected {found} of {expected} "
                   "piece(s) ({tier}).").format(
                       name=name, found=found, expected=expected,
                       tier=report.get("tier") or _("tier unknown"))]
        lines.append(_("  {tracks} track(s), {vias} via(s)").format(
            tracks=report.get("tracks", 0), vias=report.get("vias", 0)))
        if report.get("missing"):
            lines.append(_("  {count} piece(s) not found on the board").format(
                count=report["missing"]))
        if reason:
            lines.append("  " + reason)
    # Always: select_items() REPLACES the selection wholesale.
    lines.append(_("  note: the previous board selection was replaced "
                   "({count} item(s) before)").format(
                       count=report.get("previous_selection", 0)))
    return lines


# ── Copper -> record: "whose copper is this?" (Э3, design §12.2) ─────────────

@dataclass
class IdentifyResult:
    """Which `net_traces:` records the board SELECTION belongs to.

    `identified` maps a record identity to how many selected pieces it owns.
    `unidentified` is the number of selected pieces that belong to NO record —
    a USEFUL answer, not an error: it means that copper is free to capture.
    `owned_elsewhere` counts pieces owned by a non-net_traces registry key
    (a rule/cell) — they are somebody's, just not a net_traces record's.
    `unknown_records` are registry identities with no matching record in the
    config (a stale registry entry). `reasons` carries per-record planning
    failures (an unresolvable anchor means geometry cannot be compared)."""
    total: int = 0
    identified: dict[str, int] = field(default_factory=dict)
    unidentified: int = 0
    owned_elsewhere: int = 0
    unknown_records: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)


def _registry_key_identity(key: str):
    """The `net_traces:` record identity inside a registry key, or None when
    the key belongs to another mechanism. Keys are
    `anchor_id|template_name|role|index` and a net trace's anchor_id is
    `net:<identity>` (net_trace_anchor_id)."""
    anchor_id = key.split("|", 1)[0]
    if anchor_id.startswith("net:"):
        return anchor_id[len("net:"):]
    return None


def _uuid_identity_map(entries: dict) -> dict[str, str]:
    """uuid -> record identity for every net-trace registry entry. TIER 1 of
    Э3: the registry is an exact, uuid-based answer — no geometry involved."""
    out: dict[str, str] = {}
    for key, entry in entries.items():
        identity = _registry_key_identity(key)
        if identity is not None:
            out.setdefault(entry.uuid, identity)
    return out


def identify_selected_copper(adapter, cfg, selected, *, via_registry,
                             track_registry, sheet_names=None) -> IdentifyResult:
    """READ-ONLY: which `net_traces:` records own the selected board copper?

    Strict tier order (plan Э3, design §12.2):
      1. the registries, by uuid — exact, and needs no anchor;
      2. geometry, through the SAME match_net_trace_pieces the apply path and
         find_live_copper use, for copper no registry knows.
    Writes nothing."""
    from kicadstamp.config import net_trace_effective_name
    from kicadstamp.domain.board import Track, Via
    from kicadstamp.net_trace_planner import (
        TIER_GEOMETRY, TRACK, VIA, Expectation, match_net_trace_pieces,
        plan_net_traces,
    )

    tracks = [i for i in selected if isinstance(i, Track)]
    vias = [i for i in selected if isinstance(i, Via)]
    result = IdentifyResult(total=len(tracks) + len(vias))
    records = {net_trace_effective_name(nt): nt
               for nt in (getattr(cfg, "net_traces", None) or [])}

    via_map = _uuid_identity_map(via_registry.entries)
    track_map = _uuid_identity_map(track_registry.entries)
    owned_vias = {e.uuid for e in via_registry.entries.values()}
    owned_tracks = {e.uuid for e in track_registry.entries.values()}

    remaining_uuids: set[str] = set()
    for v in vias:
        identity = via_map.get(v.uuid)
        if identity is not None:
            result.identified[identity] = result.identified.get(identity, 0) + 1
            if identity not in records:
                result.unknown_records.append(identity)
        elif v.uuid in owned_vias:
            result.owned_elsewhere += 1
        else:
            remaining_uuids.add(v.uuid)
    for t in tracks:
        identity = track_map.get(t.uuid)
        if identity is not None:
            result.identified[identity] = result.identified.get(identity, 0) + 1
            if identity not in records:
                result.unknown_records.append(identity)
        elif t.uuid in owned_tracks:
            result.owned_elsewhere += 1
        else:
            remaining_uuids.add(t.uuid)

    if remaining_uuids:
        expectations: list[Expectation] = []
        for identity, nt in records.items():
            if nt.retired or nt.skip:
                continue  # apply does not place it; tier 1 already covered it
            try:
                planned_vias, planned_tracks = plan_net_traces(
                    adapter, [nt], sheet_names=dict(sheet_names or {}))
            except Exception as e:  # noqa: BLE001 — one bad anchor must not kill the rest
                result.reasons.append(_(
                    "record {name!r}: its anchor could not be resolved live "
                    "({error}) — its geometry was not compared").format(
                        name=identity, error=e))
                continue
            for i, cmd in enumerate(planned_vias):
                expectations.append(Expectation(VIA, i, cmd.registry_key, cmd))
            for i, cmd in enumerate(planned_tracks):
                expectations.append(Expectation(TRACK, i, cmd.registry_key, cmd))
        if expectations:
            matched = match_net_trace_pieces(
                adapter, expectations, via_registry=via_registry,
                track_registry=track_registry)
            for piece in matched:
                if (piece.tier != TIER_GEOMETRY or piece.live is None
                        or piece.live.uuid not in remaining_uuids):
                    continue
                identity = piece.expectation.registry_key.split("|", 2)[1]
                result.identified[identity] = result.identified.get(identity, 0) + 1
                remaining_uuids.discard(piece.live.uuid)

    result.unidentified = len(remaining_uuids)
    return result


def identify_copper_report_lines(result: IdentifyResult,
                                 tree_node_identities=frozenset()) -> list[str]:
    """The reverse lookup's answer as LOG LINES (never a dialog). "No record"
    is a useful answer: that copper is free to capture. An identified record
    that is a tree node is marked."""
    if result.total == 0:
        return [_("Nothing is selected on the board — select copper first.")]
    lines = [_("Selected copper: {total} piece(s).").format(total=result.total)]
    for identity in sorted(result.identified):
        marker = (_(" (tree node)") if identity in tree_node_identities else "")
        lines.append(_("  {name}: {count} piece(s){marker}").format(
            name=identity, count=result.identified[identity], marker=marker))
    for identity in sorted(set(result.unknown_records)):
        lines.append(_(
            "  {name}: the registry knows this copper, but the config has no "
            "such record").format(name=identity))
    if result.owned_elsewhere:
        lines.append(_(
            "  {count} piece(s) belong to another mechanism (not a net_traces "
            "record)").format(count=result.owned_elsewhere))
    if result.unidentified:
        lines.append(_(
            "  {count} piece(s) belong to no net_traces record — this copper "
            "can be captured").format(count=result.unidentified))
    lines.extend("  " + reason for reason in result.reasons)
    return lines


def run_identify_copper_worker(payload: dict) -> IdentifyResult:
    """start_long_op worker entry point for "Whose copper is this?" (Э3).
    Plain data in, plain data out; the selection read and every board call
    happen HERE, on the worker. READ-ONLY: nothing is selected or written."""
    adapter = _live_adapter()
    selected = list(adapter.get_selected_items() or [])
    via_registry, track_registry = _readonly_registries(adapter, payload["config_path"])
    return identify_selected_copper(
        adapter, payload["cfg"], selected,
        via_registry=via_registry, track_registry=track_registry,
        sheet_names=payload.get("sheet_names") or {})
