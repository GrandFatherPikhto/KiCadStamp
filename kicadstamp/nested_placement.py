# kicadstamp/nested_placement.py
"""ONE home for a nested CellPlacement's convention — the synthesized role-only
cell, its comparable geometry and its write/report shape (2026-10-13, доделка of
plan_2026_10_13_single_slot_rotation).

A composite cell's INNER CellPlacement is read (Refresh geometry from selection,
`cell_geometry_refresh`) and applied (`ClonePlacement`'s calculator) by two
different paths, and those two paths must agree on:

  * the synthesized ONE-component cell a `role:`-only nested placement means
    (`role_only_cell`) — it used to be built by a COPY in each path, which is
    exactly how the two sides of a re-read drift apart;
  * what "this nested record's geometry" IS (`nested_target_norm` — the same
    tuple the write path stores, so "did anything change?" is ONE equality and a
    re-read of an unmoved board is bit-for-bit idempotent);
  * what is WRITTEN for it (`nested_new_geo` — a DEFAULT value is not stored at
    all, matching the nested-cell editor's own writer,
    `gui/docks/cell_editor.py::_build_nested_dict`);
  * what is SAID about it (`nested_report_line` — one Log line per CHANGED
    record: name + old -> new, never a bare counter).

They live HERE and not in `kicadstamp/cell_geometry_refresh.py`: that file is a
giant and may only shrink, and the role-only cell is shared with the calculator.
"""
from __future__ import annotations

from .config import Cell, TemplateComponentSlot
from .i18n import _


def role_only_cell(role: str) -> Cell:
    """The synthesized one-component Cell of a `role:`-only nested placement: a
    single slot at the cell's own (0, 0), angle 0.

    ONE host: the apply-time calculator (`clone_position_calculator.
    _resolve_content`) and the re-read path ask the SAME function."""
    return Cell(name=f"__role__{role}",
                components=[TemplateComponentSlot(
                    role=role, offset_along_mm=0.0, offset_across_mm=0.0,
                    angle_deg=0.0)])


def nested_target_norm(record: dict) -> tuple[float, float, float, bool]:
    """The comparable identity of a nested record's geometry: the same tuple
    shape `nested_new_geo` writes, so "did anything change?" is one equality test
    and a re-read of an unmoved board is bit-for-bit idempotent."""
    xy = record.get("xy") or [0.0, 0.0]
    return (round(float(xy[0]), 4), round(float(xy[1]), 4),
            round(float(record.get("rotation_deg", 0.0)), 4),
            bool(record.get("mirror", False)))


def nested_new_geo(norm: tuple) -> dict:
    """The update dict for one nested record, in the SAME convention the
    nested-cell editor's own writer uses (gui/docks/cell_editor.py::
    _build_nested_dict): a DEFAULT value is not stored at all — xy only when
    not (0, 0), rotation_deg only when not 0, mirror only when True."""
    x, y, rot, mirror = norm
    new: dict = {}
    if (x, y) != (0.0, 0.0):
        new["xy"] = [x, y]
    if rot != 0.0:
        new["rotation_deg"] = rot
    if mirror:
        new["mirror"] = True
    return new


def nested_report_line(record: dict, before: tuple, after: tuple) -> str:
    """One Log line per CHANGED nested placement (H.2.4 style: name + old ->
    new, never a bare counter)."""
    content = record.get("cell")
    content = (f"cell:{content}" if content is not None
               else f"role:{record.get('role', '')}")
    return _("nested {name!r} ({content}): xy ({x0}, {y0}) rot {r0}° "
             "mirror={m0} -> xy ({x1}, {y1}) rot {r1}° mirror={m1}").format(
        name=record.get("name", "?"), content=content,
        x0=before[0], y0=before[1], r0=before[2], m0=str(before[3]).lower(),
        x1=after[0], y1=after[1], r1=after[2], m1=str(after[3]).lower())
