#!/usr/bin/env python3
"""Anchor-as-mount-point geometry (design_2026_09_05 v2): a cell whose stored
offsets live in the bbox frame and carry an anchor A=(2,1) (mount on the
MOUNT component). apply_clone_geometry / apply_spoke_geometry must place
content so A coincides with the placement origin:
    element_world = origin + rotate(element_offset - A, rotation)
including vias, tracks, rotation and the mirrored case (mirror stays about the
MOUNT/origin — per-element subtraction, NOT an origin shift)."""
import sys
import math
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from kicadstamp.domain.geometry import Vector2

from kicadstamp.config import (
    ClonePlacement, ManualSpoke, Cell, TemplateVia, TemplateComponentSlot, TemplateTrack,
)
from kicadstamp.geometry.cell_anchor import cell_mount_offset
from kicadstamp.geometry.clone_geometry import (
    apply_clone_geometry, clone_layout_origin, clone_origin_from_component,
)
from kicadstamp.geometry.spoke_layout import apply_spoke_geometry

MM = 1_000_000


def _real_rotate(x_mm, y_mm, angle_deg):
    """The REAL kipy Vector2.rotate() formula (the oracle test_spoke_layout
    uses) — independent of local_to_absolute, so expectations are not
    tautological."""
    theta = math.radians(angle_deg)
    return (y_mm * math.sin(theta) + x_mm * math.cos(theta),
            y_mm * math.cos(theta) - x_mm * math.sin(theta))


def _p(x_mm, y_mm):
    return Vector2.from_xy(int(round(x_mm * MM)), int(round(y_mm * MM)))


def _near(v, x_mm, y_mm, tol=1e-6):
    assert abs(v.x / MM - x_mm) < tol, (v.x / MM, x_mm)
    assert abs(v.y / MM - y_mm) < tol, (v.y / MM, y_mm)


def _anchored_cell() -> Cell:
    """Content in the bbox frame; anchor A=(2.0, 1.0) sits on MOUNT. In
    mount-relative terms MOUNT is at (0,0), OTHER at (2,1)."""
    return Cell(
        name="c",
        vias=[TemplateVia(offset_along_mm=3.0, offset_across_mm=1.0, net="GND")],
        tracks=[TemplateTrack(start_along_mm=1.0, start_across_mm=0.5,
                              end_along_mm=3.0, end_across_mm=1.0,
                              width_mm=0.25, net="VCC")],
        components=[
            TemplateComponentSlot(
                role="MOUNT", offset_along_mm=2.0, offset_across_mm=1.0,
                angle_deg=0.0,
                vias=[TemplateVia(offset_along_mm=2.0, offset_across_mm=2.0,
                                  net="GND")]),
            TemplateComponentSlot(role="OTHER", offset_along_mm=4.0,
                                  offset_across_mm=2.0, angle_deg=90.0),
        ],
        anchor_xy=(2.0, 1.0),
        anchor_role="MOUNT",
    )


class TestApplyCloneGeometryAnchor:
    def test_mount_lands_on_origin_and_content_is_reduced(self):
        cell = _anchored_cell()
        clone = ClonePlacement(cluster="x", cell="c", xy=(10.0, 20.0))
        layout = apply_clone_geometry(clone, cell, {"MOUNT": "U1", "OTHER": "R2"})

        _near(layout.origin, 10.0, 20.0)          # mount point
        by_role = {c.role: c for c in layout.components}
        _near(by_role["MOUNT"].position, 10.0, 20.0)   # A lands on origin
        _near(by_role["OTHER"].position, 12.0, 21.0)   # +(4-2, 2-1)
        assert by_role["OTHER"].angle_deg == 90.0

    def test_anchor_role_only_resolves_through_the_role_offset(self):
        cell = Cell(
            name="c",
            components=[
                TemplateComponentSlot(role="MOUNT", offset_along_mm=2.0,
                                      offset_across_mm=1.0, angle_deg=0.0),
                TemplateComponentSlot(role="OTHER", offset_along_mm=4.0,
                                      offset_across_mm=2.0, angle_deg=0.0),
            ],
            anchor_role="MOUNT",   # no anchor_xy -> A = MOUNT's offset
        )
        clone = ClonePlacement(cluster="x", cell="c", xy=(10.0, 20.0))
        layout = apply_clone_geometry(clone, cell, {"MOUNT": "U1", "OTHER": "R2"})
        by_role = {c.role: c for c in layout.components}
        _near(by_role["MOUNT"].position, 10.0, 20.0)
        _near(by_role["OTHER"].position, 12.0, 21.0)

    def test_vias_and_tracks_follow_the_reduction(self):
        cell = _anchored_cell()
        clone = ClonePlacement(cluster="x", cell="c", xy=(10.0, 20.0))
        layout = apply_clone_geometry(clone, cell, {"MOUNT": "U1", "OTHER": "R2"})

        _near(layout.vias[0].position, 11.0, 20.0)        # cell via (3,1)-A=(1,0)
        mount = next(c for c in layout.components if c.role == "MOUNT")
        _near(mount.vias[0].position, 10.0, 21.0)          # slot via (2,2)-A=(0,1)
        _near(layout.tracks[0].start, 9.0, 19.5)          # start (1,.5)-A=(-1,-.5)
        _near(layout.tracks[0].end, 11.0, 20.0)           # end (3,1)-A=(1,0)

    def test_rotation_uses_the_reduced_offset(self):
        cell = _anchored_cell()
        clone = ClonePlacement(cluster="x", cell="c", xy=(10.0, 20.0),
                               rotation_deg=90.0)
        layout = apply_clone_geometry(clone, cell, {"MOUNT": "U1", "OTHER": "R2"})
        by_role = {c.role: c for c in layout.components}
        _near(by_role["MOUNT"].position, 10.0, 20.0)
        ex, ey = _real_rotate(2.0, 1.0, 90.0)             # OTHER reduced (2,1)
        _near(by_role["OTHER"].position, 10.0 + ex, 20.0 + ey)
        vx, vy = _real_rotate(1.0, 0.0, 90.0)             # cell via reduced (1,0)
        _near(layout.vias[0].position, 10.0 + vx, 20.0 + vy)

    def test_mirror_stays_about_the_mount(self):
        cell = _anchored_cell()
        clone = ClonePlacement(cluster="x", cell="c", xy=(10.0, 20.0))
        layout = apply_clone_geometry(clone, cell, {"MOUNT": "U1", "OTHER": "R2"},
                                      mirror=True)
        by_role = {c.role: c for c in layout.components}
        _near(layout.origin, 10.0, 20.0)                   # mount not shifted
        _near(by_role["MOUNT"].position, 10.0, 20.0)       # stays on the mount
        _near(by_role["OTHER"].position, 8.0, 21.0)        # x mirrored about 10
        _near(layout.vias[0].position, 9.0, 20.0)          # (11,20) -> (9,20)

    def test_clone_layout_origin_matches_mount(self):
        cell = _anchored_cell()
        clone = ClonePlacement(cluster="x", cell="c", xy=(10.0, 20.0))
        layout = apply_clone_geometry(clone, cell, {"MOUNT": "U1", "OTHER": "R2"})
        assert clone_layout_origin(clone, None) == layout.origin


class TestApplySpokeGeometryAnchor:
    def test_spoke_mounts_the_cell_anchor_on_the_spoke_origin(self):
        cell = _anchored_cell()
        spoke = ManualSpoke(pad="1", cell="c", shift_x_mm=1.0, shift_y_mm=2.0)
        pad = _p(100.0, 200.0)
        layout = apply_spoke_geometry(
            pad, spoke, cell, "GND", {"MOUNT": "U1", "OTHER": "R2"})
        _near(layout.origin, 101.0, 202.0)
        by_role = {c.role: c for c in layout.components}
        _near(by_role["MOUNT"].position, 101.0, 202.0)
        _near(by_role["OTHER"].position, 103.0, 203.0)
        _near(layout.vias[0].position, 102.0, 202.0)


class TestCloneOriginInverseAnchor:
    """Forward (apply_clone_geometry) then inverse (clone_origin_from_component,
    with A) must recover the MOUNT origin for an anchored cell — from a
    NON-mount reference component, proving A is added back."""

    def _other_slot(self, cell):
        return next(c for c in cell.components if c.role == "OTHER")

    def test_plain_round_trip_recovers_the_mount(self):
        cell = _anchored_cell()
        clone = ClonePlacement(cluster="x", cell="c", xy=(10.0, 20.0))
        layout = apply_clone_geometry(clone, cell, {"MOUNT": "U1", "OTHER": "R2"})
        other = next(c for c in layout.components if c.role == "OTHER")
        ax_mm, ay_mm = cell_mount_offset(cell)
        origin, rotation = clone_origin_from_component(
            other.position, other.angle_deg, self._other_slot(cell), False,
            ax_mm, ay_mm)
        _near(origin, 10.0, 20.0)           # the mount, not the bbox corner
        assert rotation == 0.0

    def test_rotated_round_trip_recovers_the_mount(self):
        cell = _anchored_cell()
        clone = ClonePlacement(cluster="x", cell="c", xy=(10.0, 20.0),
                               rotation_deg=90.0)
        layout = apply_clone_geometry(clone, cell, {"MOUNT": "U1", "OTHER": "R2"})
        other = next(c for c in layout.components if c.role == "OTHER")
        ax_mm, ay_mm = cell_mount_offset(cell)
        origin, rotation = clone_origin_from_component(
            other.position, other.angle_deg, self._other_slot(cell), False,
            ax_mm, ay_mm)
        _near(origin, 10.0, 20.0)
        assert abs(rotation - 90.0) < 1e-6

    def test_mirrored_round_trip_recovers_the_mount(self):
        cell = _anchored_cell()
        clone = ClonePlacement(cluster="x", cell="c", xy=(10.0, 20.0))
        layout = apply_clone_geometry(clone, cell, {"MOUNT": "U1", "OTHER": "R2"},
                                      mirror=True)
        other = next(c for c in layout.components if c.role == "OTHER")
        ax_mm, ay_mm = cell_mount_offset(cell)
        origin, rotation = clone_origin_from_component(
            other.position, other.angle_deg, self._other_slot(cell), True,
            ax_mm, ay_mm)
        _near(origin, 10.0, 20.0)
        assert rotation == 0.0
