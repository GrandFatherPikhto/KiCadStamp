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
