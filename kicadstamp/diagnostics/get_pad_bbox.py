#!/usr/bin/env python3
"""
kicadstamp/diagnostics/get_pad_bbox.py

Diagnostic script to get the bounding box of a pad.
Shows the real dimensions used for keepout construction.
"""

import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

import argparse
import logging
from kicadstamp.kicad.adapter import KiCadBoardAdapter
from kicadstamp.utils.units import MM
from kicadstamp.i18n import _

logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description=_("Get bounding box of a pad"))
    parser.add_argument("--ref", default="IC1", help=_("Refdes of the target component"))
    parser.add_argument("--pad", help=_("Pad number (if not specified, show all)"))
    # 20 s kept for consistency with the sibling diagnostics tools (Э4,
    # plan_2026_09_13_timeout_sweep). Unlike the other five this is a SINGLE
    # cheap pad read that does not need the long budget — reported to Denis
    # instead of silently changed here.
    parser.add_argument("--timeout", type=int, default=20000, help=_("IPC timeout in ms"))
    parser.add_argument("--verbose", action="store_true", help=_("Verbose output"))
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    adapter = KiCadBoardAdapter(timeout_ms=args.timeout)
    adapter.refresh_board()

    fp = adapter.get_footprint(args.ref)
    if fp is None:
        logger.error(_("Component {ref} not found").format(ref=args.ref))
        sys.exit(1)

    pads = adapter.get_footprint_pads(fp)
    if not pads:
        logger.error(_("{ref} has no pads").format(ref=args.ref))
        sys.exit(1)

    if args.pad:
        pads = [p for p in pads if p.number == args.pad]
        if not pads:
            logger.error(_("Pad {pad} not found on {ref}").format(pad=args.pad, ref=args.ref))
            sys.exit(1)

    # Get bounding boxes for all pads in one request
    bboxes = adapter.get_bounding_boxes(pads)
    logger.info(_("Retrieved {count} bounding boxes").format(count=len(bboxes)))

    for pad, bbox in zip(pads, bboxes):
        if bbox is None:
            logger.info(_("Pad {num}: bounding box missing").format(num=pad.number))
            continue
        w = bbox.size.x / MM
        h = bbox.size.y / MM
        logger.info(_("Pad {num}: size {w:.3f} x {h:.3f} mm, position ({x:.3f}, {y:.3f}) mm")
                    .format(num=pad.number, w=w, h=h,
                            x=bbox.pos.x/MM, y=bbox.pos.y/MM))

    # If a specific pad is requested, show more detailed information (copper layer)
    if args.pad:
        pad = pads[0]
        from kicadstamp.geometry.thermal_grid import get_pad_size
        size = get_pad_size(pad)
        if size:
            logger.info(_("Copper layer of pad {num}: {w:.3f} x {h:.3f} mm")
                        .format(num=pad.number, w=size[0]/MM, h=size[1]/MM))


if __name__ == "__main__":
    main()
