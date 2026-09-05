#!/usr/bin/env python3
"""cell_mount_offset — the Cell's mount point A in its stored frame
(design_2026_09_05_cell_zero_anchor_forms.md v2): anchor_xy -> that point,
anchor_role -> that component's centre, anchor_role+anchor_pad (legacy, no xy)
-> (0,0) (the pad sits at the mount by the old mutation), neither -> (0,0)
(the default bbox corner)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from kicadstamp.config import _load_cell, Cell, TemplateComponentSlot
from kicadstamp.exceptions import ValidationError
from kicadstamp.geometry.cell_anchor import cell_mount_offset


def _cell(entry: dict) -> Cell:
    """A loader-built Cell (validates anchor_role against components, as the
    real config path always does)."""
    return _load_cell("t", entry)


def test_no_anchor_is_the_default_bbox_corner():
    cell = _cell({"components": [{"role": "A", "offset_along_mm": 0.0,
                                  "offset_across_mm": 0.0}]})
    assert cell_mount_offset(cell) == (0.0, 0.0)


def test_anchor_xy_is_returned_verbatim():
    cell = _cell({"components": [{"role": "A"}], "anchor_xy": [1.5, -2.0]})
    assert cell_mount_offset(cell) == (1.5, -2.0)


def test_anchor_role_resolves_to_the_component_centre():
    cell = _cell({"components": [{"role": "A", "offset_along_mm": 2.5,
                                  "offset_across_mm": 1.0}],
                  "anchor_role": "A"})
    assert cell_mount_offset(cell) == (2.5, 1.0)


def test_anchor_role_on_the_zero_slot_is_zero():
    cell = _cell({"components": [{"role": "FPGA"}, {"role": "CAP",
                                                    "offset_along_mm": 3.0,
                                                    "offset_across_mm": -1.0}],
                  "anchor_role": "FPGA"})
    assert cell_mount_offset(cell) == (0.0, 0.0)


def test_legacy_role_pad_without_xy_is_the_stored_zero():
    """A design-2026-09-04 rebase-by-pad cell mutated the offsets so the pad IS
    the mount; pad geometry is not stored, so A is (0,0) — not the role
    centre."""
    cell = _cell({"components": [{"role": "FPGA", "offset_along_mm": 2.5,
                                  "offset_across_mm": 1.0}],
                  "anchor_role": "FPGA", "anchor_pad": "A1"})
    assert cell_mount_offset(cell) == (0.0, 0.0)


def test_unknown_anchor_role_is_fatal():
    cell = Cell(name="t",
                components=[TemplateComponentSlot(role="A", angle_deg=0.0)],
                anchor_role="MISSING")
    with pytest.raises(ValidationError, match="MISSING"):
        cell_mount_offset(cell)
