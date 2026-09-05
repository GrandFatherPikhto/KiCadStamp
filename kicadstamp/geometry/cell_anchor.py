# kicadstamp/geometry/cell_anchor.py
"""Cell internal "mount point" (design_2026_09_05_cell_zero_anchor_forms.md v2).

A Cell's stored local offsets (offset_along_mm/offset_across_mm of its
components/vias, start/end of its tracks, xy of its nested clone_placements)
always live in ONE stable frame, anchored at the cell's bbox default corner
(the corner the extractor uses as the default origin — board lower-left,
`template_selection._bbox_origin`). Setting a cell anchor NEVER rewrites those
offsets (the old mutation/rebase approach — design_2026_09_04 — is superseded).

The cell's ANCHOR is a separate MOUNT POINT A expressed in that same stored
frame: the cell point that must coincide with the placement's origin when the
cell is materialized. Absent anchor = A = (0,0) (the bbox corner — the default
mount; fresh default-extracted cells already behave this way, so subtracting A
is a no-op for them).

Geometry places cell content as

    element_world = origin + rotate(element_offset - A, rotation_deg)

i.e. ALWAYS subtracts A (see clone_geometry.apply_clone_geometry and
spoke_layout.apply_spoke_geometry). This module is the SINGLE source of truth
for resolving A from a Cell:

  - cell.anchor_xy (x, y)                 -> that exact point;
  - cell.anchor_role (no anchor_xy)       -> that component's stored centre offset;
  - cell.anchor_role + cell.anchor_pad
    (no anchor_xy)                        -> legacy rebase-by-pad (mutated)
                                             cell: the pad sits at the mount by
                                             construction and pad geometry is
                                             not stored, so the stored mount is
                                             (0,0);
  - neither                               -> (0,0) (default bbox corner).

Pure Qt-free geometry; never touches the live board. Raises ValidationError
(defensive — the loader already guarantees anchor_role names a component) on
an unknown anchor_role.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from ..exceptions import ValidationError, format_fatal_error
from ..i18n import _

if TYPE_CHECKING:
    from ..config import Cell

__all__ = ["cell_mount_offset"]


def cell_mount_offset(cell: "Cell") -> tuple[float, float]:
    """(along_mm, across_mm) of the cell's MOUNT point A in its stored frame.

    The mount is the cell point that lands on the placement origin at
    materialization. Absent anchor = (0,0) — the default bbox corner. Pure
    cell data, no live board."""
    if cell.anchor_xy is not None:
        return (float(cell.anchor_xy[0]), float(cell.anchor_xy[1]))
    if cell.anchor_role is not None:
        if cell.anchor_pad is not None:
            # Legacy rebase-by-pad (design 2026-09-04) mutated the cell so the
            # pad IS the stored mount (0,0); pad geometry is not stored, so A
            # cannot be anything else. New (v2) pad anchors also store
            # anchor_xy and are handled by the branch above.
            return (0.0, 0.0)
        for component in cell.components:
            if component.role == cell.anchor_role:
                return (float(component.offset_along_mm),
                        float(component.offset_across_mm))
        raise ValidationError(format_fatal_error(
            _("cell {name!r}: anchor_role {role!r} is not a component of this cell")
            .format(name=cell.name, role=cell.anchor_role),
            [_("anchor_role must name one of this cell's own components; the "
               "mount point cannot be derived from a role that does not "
               "exist")]))
    return (0.0, 0.0)
