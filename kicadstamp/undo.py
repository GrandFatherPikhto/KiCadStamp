# kicadstamp/undo.py

import json
import logging
from pathlib import Path
from kipy.geometry import Vector2
from kicadstamp.kicad.adapter import KiCadBoardAdapter
from kicadstamp.utils.layers import layer_from_str
from kicadstamp.utils.units import MM
from kicadstamp.i18n import _

logger = logging.getLogger(__name__)


def undo_last_operation(json_path: Path, adapter=None) -> bool:
    """Undoes the operation described in the JSON file.

    ``adapter`` — an IBoardAdapter to restore through (dependency injection,
    2026-08-25, P0-5 of the architecture audit: the function used to
    construct its own KiCadBoardAdapter, which made it untestable without a
    live KiCad). When None (the production CLI path), a fresh
    KiCadBoardAdapter is created. The board is always refreshed first.
    """
    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    if adapter is None:
        adapter = KiCadBoardAdapter()
    adapter.refresh_board()

    # 1. Restore moved components
    for item in data.get('moves', []):
        ref = item['ref']
        uuid_str = item.get('uuid')
        # UUID first (survives re-annotation between apply and undo — see
        # move_executor.py's uuid capture); ref is only a fallback for logs
        # written before the uuid field existed.
        fp = adapter.get_footprint_by_id(uuid_str) if uuid_str else None
        if fp is None:
            fp = adapter.get_footprint(ref)
        if fp is None:
            logger.warning(_("Component {ref} not found, skipping").format(ref=ref))
            continue

        # Determine original layer
        orig_layer_str = item.get('original_layer', 'F.Cu')
        orig_layer = layer_from_str(orig_layer_str)

        # If current layer differs from original — flip
        if fp.layer != orig_layer:
            logger.debug(_("Restoring {ref} to layer {layer} (flip)").format(ref=ref, layer=orig_layer_str))
            adapter.flip_selected([fp])
            # After flip, re‑read the footprint (by uuid — its refdes may
            # have changed since we found it)
            fp = adapter.get_footprint_by_id(uuid_str) if uuid_str else adapter.get_footprint(ref)
            if fp is None:
                continue

        # Restore position and angle
        orig_x = item['original_position']['x']
        orig_y = item['original_position']['y']
        orig_angle = item['original_angle_deg']
        fp.position = Vector2.from_xy(int(orig_x), int(orig_y))
        fp.angle_deg = orig_angle
        adapter.update_items([fp])
        logger.debug(_("Restored {ref} to position ({x:.3f}, {y:.3f}) mm, angle {angle:.1f}°")
                     .format(ref=ref, x=orig_x/MM, y=orig_y/MM, angle=orig_angle))

    # 2. Delete created vias (by UUID)
    for via_data in data.get('created_vias', []):
        uuid_str = via_data.get('uuid')
        if uuid_str:
            if adapter.remove_by_id(uuid_str):
                logger.debug(_("Deleted via with UUID {uuid}").format(uuid=uuid_str))

    # 2b. Delete created tracks (by UUID) — tracks were not moved, so only deletion is needed
    for track_data in data.get('created_tracks', []):
        uuid_str = track_data.get('uuid')
        if uuid_str:
            if adapter.remove_by_id(uuid_str):
                logger.debug(_("Deleted track with UUID {uuid}").format(uuid=uuid_str))

    # 3. Delete the operation file (to prevent undoing twice)
    try:
        json_path.unlink()
        logger.debug(_("File {name} deleted.").format(name=json_path.name))
    except Exception as e:
        logger.warning(_("Failed to delete file {name}: {e}").format(name=json_path.name, e=e))

    return True