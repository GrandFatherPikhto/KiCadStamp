# mcp_server/handlers.py
"""Logic layer: READ tools that inspect the live KiCad board.

Deliberately imports NO MCP SDK — only ``kicadstamp.*`` (design doc §2.1).
Each function takes the live adapter as its first argument and returns plain
JSON-serialisable dicts/lists, which makes them unit-testable with a fake
adapter and trivially wrappable by tools.py.
"""

from __future__ import annotations

import logging
from typing import Any

from kicadstamp.constants import CLUSTER_FIELD_NAME, ROLE_FIELD_NAME
from kicadstamp.domain.board import Footprint, Track, Via
from kicadstamp.i18n import _
from kicadstamp.utils.layers import layer_to_str

# Domain Vector2 positions are in board units (nm), i.e. 1e-6 of a mm.
_NM_PER_MM = 1_000_000.0


def _mm(value: float) -> float:
    return value / _NM_PER_MM


def _fp_brief(adapter, fp: Footprint) -> dict[str, Any]:
    """The compact footprint record shared by list_footprints/get_footprint."""
    return {
        "ref": fp.ref,
        "role": adapter.get_field_value(fp, ROLE_FIELD_NAME),
        "cluster": adapter.get_field_value(fp, CLUSTER_FIELD_NAME),
        "x_mm": round(_mm(fp.position.x), 3),
        "y_mm": round(_mm(fp.position.y), 3),
        "rotation_deg": round(fp.angle_deg, 2),
        "layer": layer_to_str(fp.layer),
    }


def get_board_identity(adapter) -> dict[str, Any]:
    """``{connected, board_name, kicad_version}`` for the live board."""
    board_name = adapter.get_board_filename()
    return {
        "connected": board_name is not None,
        "board_name": board_name,
        "kicad_version": adapter.get_version(),
    }


def list_footprints(adapter, ref_prefix: str | None = None) -> list[dict[str, Any]]:
    """One entry per footprint; refs filtered by an optional ref prefix."""
    return [_fp_brief(adapter, fp) for fp in adapter.get_footprints()
            if ref_prefix is None or fp.ref.startswith(ref_prefix)]


def get_footprint(adapter, ref: str) -> dict[str, Any] | None:
    """Detailed footprint — position/rotation/layer, Role/Cluster fields, its
    pads (number, net, position) and the nets on its pads. None when no such
    ref is on the board."""
    fp = adapter.get_footprint(ref)
    if fp is None:
        return None
    pads: list[dict[str, Any]] = []
    nets: set[str] = set()
    for pad in adapter.get_footprint_pads(fp):
        pads.append({
            "number": str(pad.number),
            "net": pad.net_name,
            "x_mm": round(_mm(pad.position.x), 3),
            "y_mm": round(_mm(pad.position.y), 3),
        })
        if pad.net_name:
            nets.add(pad.net_name)
    return {
        **_fp_brief(adapter, fp),
        "uuid": fp.uuid,
        "value": fp.value,
        "fields": {
            ROLE_FIELD_NAME: adapter.get_field_value(fp, ROLE_FIELD_NAME),
            CLUSTER_FIELD_NAME: adapter.get_field_value(fp, CLUSTER_FIELD_NAME),
        },
        "pads": pads,
        "nets": sorted(nets),
    }


def get_selection(adapter) -> list[dict[str, Any]]:
    """What the PCB editor currently has selected (groups expanded)."""
    result: list[dict[str, Any]] = []
    for item in adapter.get_selected_items():
        if isinstance(item, Footprint):
            result.append({"kind": "footprint", "ref": item.ref, "uuid": item.uuid})
        elif isinstance(item, Via):
            result.append({"kind": "via", "uuid": item.uuid})
        elif isinstance(item, Track):
            result.append({"kind": "track", "uuid": item.uuid})
        else:
            result.append({"kind": type(item).__name__,
                           "uuid": getattr(item, "uuid", None)})
    return result


def _track_brief(track: Track) -> dict[str, Any]:
    """The compact track record shared by list_tracks/get_items_by_uuid."""
    return {
        "uuid": track.uuid,
        "kind": "track",
        "net": track.net_name,
        "layer": layer_to_str(track.layer),
        "width_mm": round(track.width_mm, 3),
        "start_x_mm": round(_mm(track.start.x), 3),
        "start_y_mm": round(_mm(track.start.y), 3),
        "end_x_mm": round(_mm(track.end.x), 3),
        "end_y_mm": round(_mm(track.end.y), 3),
    }


def _via_brief(via: Via) -> dict[str, Any]:
    """The compact via record shared by list_vias/get_items_by_uuid."""
    return {
        "uuid": via.uuid,
        "kind": "via",
        "net": via.net_name,
        "x_mm": round(_mm(via.position.x), 3),
        "y_mm": round(_mm(via.position.y), 3),
        "drill_mm": round(via.drill_mm, 3),
        "diameter_mm": round(via.diameter_mm, 3),
    }


def get_items_by_uuid(adapter, uuids: list[str]) -> list[dict[str, Any]]:
    """Resolve board item uuids to detailed records (tracks/vias/footprints).

    Each requested uuid appears in the result EXACTLY once: found items get
    their full detail, missing ones ``{"uuid", "kind": None, "found": False}``
    instead of raising or silently skipping — that is the signal "a registry
    record is no longer on the board" used during investigations. Prefer this
    over list_tracks/list_vias when the uuids already come from get_selection.
    """
    by_uuid: dict[str, Any] = {}
    for item in adapter.get_tracks():
        by_uuid[item.uuid] = item
    for item in adapter.get_vias():
        by_uuid[item.uuid] = item
    for item in adapter.get_footprints():
        by_uuid[item.uuid] = item

    result: list[dict[str, Any]] = []
    for uuid in uuids:
        item = by_uuid.get(uuid)
        if item is None:
            result.append({"uuid": uuid, "kind": None, "found": False})
        elif isinstance(item, Track):
            result.append(_track_brief(item))
        elif isinstance(item, Via):
            result.append(_via_brief(item))
        else:
            result.append({**_fp_brief(adapter, item), "kind": "footprint",
                           "uuid": item.uuid})
    return result


def list_tracks(adapter, net: str | None = None,
                layer: str | None = None) -> list[dict[str, Any]]:
    """One entry per track; optionally filtered by net name and/or layer
    (string layer name, e.g. 'F.Cu'/'B.Cu' — the same layer_to_str as
    footprints).

    On a large board this can be large — prefer the net/layer filters, or
    get_items_by_uuid when you already have uuids from get_selection.
    """
    result: list[dict[str, Any]] = []
    for track in adapter.get_tracks():
        if net is not None and track.net_name != net:
            continue
        if layer is not None and layer_to_str(track.layer) != layer:
            continue
        result.append(_track_brief(track))
    return result


def list_vias(adapter, net: str | None = None) -> list[dict[str, Any]]:
    """One entry per via; optionally filtered by net name.

    On a large board this can be large — prefer the net filter, or
    get_items_by_uuid when you already have uuids from get_selection.
    """
    return [_via_brief(via) for via in adapter.get_vias()
            if net is None or via.net_name == net]


def list_nets(adapter) -> list[str]:
    """All board net names, sorted and deduplicated."""
    return sorted({net.name for net in adapter.get_all_nets()})


# --- Validated write --------------------------------------------------------

class _CollectHandler(logging.Handler):
    """Temporary root handler that captures INFO+ messages as plain lines."""

    def __init__(self):
        super().__init__(level=logging.INFO)
        self.setFormatter(logging.Formatter("%(message)s"))
        self.lines: list[str] = []

    def emit(self, record):
        self.lines.append(self.format(record))


def apply_config(config_path: str, *, dry_run: bool = False,
                 only: list[str] | None = None, cluster: list[str] | None = None,
                 no_selection: bool = False, timeout_ms: int = 20000,
                 batch_size: int = 10, no_collision_check: bool = False,
                 collision_margin: float = 0.2) -> str:
    """Run the existing VALIDATED apply pipeline (``run_apply``) on a config.

    Deliberately NOT routed through the shared ConnectionManager adapter:
    ``run_apply`` opens its own kipy socket (the same validated path the CLI
    ``apply`` and GUI Redraw use), so nothing here bypasses pre-validation
    (board identity, FORK-1, "never guess silently"), the registries or
    dependency ordering. Fatal ``ValidationError``/``PlacerError`` messages
    propagate as-is (never reformulated).

    Returns the dry-run report when ``dry_run`` is set, otherwise the run's
    captured INFO+ log lines (the same messages the CLI prints).
    """
    from kicadstamp.apply_pipeline import RunOptions, run_apply

    options = RunOptions(
        config_path=config_path,
        timeout_ms=timeout_ms,
        batch_size=batch_size,
        dry_run=dry_run,
        no_selection=no_selection,
        no_collision_check=no_collision_check,
        collision_margin=collision_margin,
        only=only,
        cluster=cluster,
    )
    # Temporarily lower the ROOT logger level to INFO: module loggers under
    # kicadstamp.* inherit the effective level from root, so with the default
    # WARNING the pipeline's INFO messages would be dropped before reaching
    # the collector (the very messages the CLI prints).
    collector = _CollectHandler()
    root = logging.getLogger()
    previous_level = root.level
    root.setLevel(logging.INFO)
    root.addHandler(collector)
    try:
        report = run_apply(options)
    finally:
        root.removeHandler(collector)
        root.setLevel(previous_level)

    if report is not None:  # dry run -> the planned report
        return "\n".join(report)
    if collector.lines:
        return "\n".join(collector.lines)
    return _("apply completed")


# --- Raw write (high risk, env-gated) ---------------------------------------

def raw_move_footprint(adapter, ref: str, x_mm: float, y_mm: float,
                       expected_board_name: str,
                       rotation_deg: float | None = None) -> dict[str, Any]:
    """RAW (high-risk) write: move one footprint by ref to an absolute
    position/rotation, bypassing the validated config layer entirely.

    The board-identity guard ALWAYS runs BEFORE any write (MANDATORY, not
    optional): *expected_board_name* is the board the caller expects to be
    open, and if the board actually open in KiCad differs, nothing is written
    and a clear fatal is raised — the same protection the apply path already
    has, so the raw path is not a hole in it. The connected board is ALWAYS
    included in the result too, so the caller sees which board was touched.

    Returns ``{board, ref, old, new}`` where old/new are
    ``{x_mm, y_mm, rotation_deg, layer}``.
    """
    from kicadstamp.config import Config
    from kicadstamp.domain.geometry import Vector2
    from kicadstamp.validation import check_board_identity

    board = adapter.get_board_filename()
    check_board_identity(Config(board_name=expected_board_name), adapter)

    fp = adapter.get_footprint(ref)
    if fp is None:
        raise ValueError(
            _("footprint {ref!r} not found on the board (connected board: {board!r})")
            .format(ref=ref, board=board))

    old = {
        "x_mm": round(_mm(fp.position.x), 3),
        "y_mm": round(_mm(fp.position.y), 3),
        "rotation_deg": round(fp.angle_deg, 2),
        "layer": layer_to_str(fp.layer),
    }

    fp.position = Vector2.from_xy_mm(x_mm, y_mm)
    if rotation_deg is not None:
        fp.angle_deg = rotation_deg
    ok = adapter.commit_with_retry(
        _("raw move {ref}").format(ref=ref), lambda: adapter.update_items([fp]))
    if not ok:
        raise RuntimeError(
            _("KiCad rejected the move of {ref!r} — the board was not modified")
            .format(ref=ref))

    adapter.refresh_board()
    moved = adapter.get_footprint(ref)
    new = {
        "x_mm": round(_mm(moved.position.x), 3) if moved else round(x_mm, 3),
        "y_mm": round(_mm(moved.position.y), 3) if moved else round(y_mm, 3),
        "rotation_deg": (round(moved.angle_deg, 2) if moved
                         else (rotation_deg if rotation_deg is not None else old["rotation_deg"])),
        "layer": layer_to_str(moved.layer) if moved else old["layer"],
    }
    return {"board": board, "ref": ref, "old": old, "new": new}
