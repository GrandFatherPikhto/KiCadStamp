#!/usr/bin/env python3
"""
diagnostic_keepout.py — keepout and via position diagnostics (KiCadStamp).

Loads the config, plans moves, builds keepout, and prints detailed information.
Uses the new KiCadStamp API.

Run:
    python diagnostic_keepout.py <config.yaml>
"""

import sys
import logging
from pathlib import Path

# Add project root to sys.path if running from root
sys.path.insert(0, str(Path(__file__).parent.parent))

from kicadstamp.config import load_config
from kicadstamp.adapter_factory import create_board_adapter
from kicadstamp.placement.planner import PlacementPlanner
from kicadstamp.geometry.keepout import build_keepout
from kicadstamp.utils.units import MM
from kicadstamp.i18n import _

logger = logging.getLogger(__name__)


def main():
    if len(sys.argv) < 2:
        print(_("Usage: python diagnostic_keepout.py <config.yaml>"))
        sys.exit(1)

    config_path = sys.argv[1]

    logging.basicConfig(level=logging.INFO, format='%(message)s')
    logger.info(_("Loading config: {path}").format(path=config_path))
    cfg, _ctx = load_config(config_path)

    logger.info(_("Connecting to KiCad..."))
    # BARE: Role/Cluster does not enter this probe's answer at all (plan Т2а)
    adapter = create_board_adapter(use_store=False)
    adapter.refresh_board()

    logger.info(_("Creating planner..."))
    planner = PlacementPlanner(adapter, cfg)

    # Plan moves (this fills _planned and _planned_vias)
    moves = planner.plan_moves()
    planned_components = planner._planned  # list of PlacedComponentInfo
    planned_vias = planner._planned_vias   # list of ViaCommand (all non‑thermal vias)

    if not planned_components and not planned_vias:
        logger.error(_("No planned components or vias!"))
        return

    # Build keepout from IC pads and components (for diagnostics) — first
    # active thermal_via_arrays entry only, this script is a single-target
    # diagnostic, not a full plan_vias() replay.
    active_tvas = [t for t in cfg.thermal_via_arrays if not t.retired]
    target_fp = adapter.get_footprint(active_tvas[0].anchor_ref) if active_tvas else None
    if target_fp is None:
        logger.info(_("No active thermal_via_arrays entry — keepout diagnostics skipped"))
        return
    keepout_rects = planner.via_planner._build_keepout(target_fp, planned_components)
    # Could also add existing vias to keepout? But for pad diagnostics it's enough.

    logger.info(_("Built {count} keepout rectangles").format(count=len(keepout_rects)))

    # Print keepout information
    print("\n=== KEEPOUT RECTANGLES ===")
    for i, rect in enumerate(keepout_rects):
        print(_("  [{i}] X: {xmin:.3f}..{xmax:.3f} mm, Y: {ymin:.3f}..{ymax:.3f} mm")
              .format(i=i, xmin=rect.min_x/MM, xmax=rect.max_x/MM,
                      ymin=rect.min_y/MM, ymax=rect.max_y/MM))

    # Check component positions against keepout
    print("\n=== COMPONENT POSITIONS vs KEEPOUT ===")
    for info in planned_components:
        pos = info.dest
        in_keepout = False
        for rect in keepout_rects:
            if (rect.min_x <= pos.x <= rect.max_x and
                rect.min_y <= pos.y <= rect.max_y):
                in_keepout = True
                break
        status = _("INSIDE") if in_keepout else _("CLEAR")
        print(_("  {ref:6} pos=({x:7.3f}, {y:7.3f}) mm  -> {status}")
              .format(ref=info.ref, x=pos.x/MM, y=pos.y/MM, status=status))

    # Check via positions (spoke‑level and component‑level)
    print("\n=== VIA POSITIONS vs KEEPOUT ===")
    for via_cmd in planned_vias:
        pos = via_cmd.position
        in_keepout = False
        for rect in keepout_rects:
            if (rect.min_x <= pos.x <= rect.max_x and
                rect.min_y <= pos.y <= rect.max_y):
                in_keepout = True
                break
        status = _("INSIDE") if in_keepout else _("CLEAR")
        print(_("  via for {owner:6} ({x:7.3f}, {y:7.3f}) mm  -> {status}")
              .format(owner=via_cmd.owner_ref, x=pos.x/MM, y=pos.y/MM, status=status))

    # Additionally: thermal vias (if not retired) – they are not yet in planned_vias, need separate handling
    # But for completeness we could call planner.plan_vias() and show thermal vias, but they might be
    # shifted due to keepout; leave as is for now.

    print("\n=== DONE ===")


if __name__ == "__main__":
    main()