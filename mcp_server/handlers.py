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
    collector = _CollectHandler()
    root = logging.getLogger()
    root.addHandler(collector)
    try:
        report = run_apply(options)
    finally:
        root.removeHandler(collector)

    if report is not None:  # dry run -> the planned report
        return "\n".join(report)
    if collector.lines:
        return "\n".join(collector.lines)
    return "apply completed"
