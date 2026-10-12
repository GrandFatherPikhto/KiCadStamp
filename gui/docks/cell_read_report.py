# gui/docks/cell_read_report.py
"""The Log lines and the preview rows of a CELL READ — the Qt-free half of
CellDock's report, moved out of the giant (2026-10-12, Г1 of
plan_2026_10_12_reread_frame_mirror: giants only shrink).

Nothing here touches a widget. CellDock calls these to turn a plan into the lines
it prints (H.2.4: "перечитывание целла — целиком на моей совести. В лог говорим:
добавили то-то, удалили то-то") and into the rows of the Import preview, so a GUI
test can exercise the exact wording without a QDialog event loop. The dock
IMPORTS THESE NAMES BACK, so every existing `gui.docks.cell_editor.<name>` user —
tests included — keeps working unchanged.
"""
from typing import Any, Dict, List, Optional

from kicadstamp.i18n import _


def _problem_lines(text: str) -> list[str]:
    """Split a collected fatal (format_fatal_error text) into one line per
    problem, for the Н4.7 rule: refusals are red Log lines, never a dialog."""
    return [line.strip() for line in str(text or "").splitlines() if line.strip()]


def component_report_line(sign: str, record: dict) -> str:
    """One '+ '/'- ' Log line naming ONE added/removed COMPONENT record (Н4.2):
    `+ component R70 R_SD_PROT (12.3, -4.5) angle 90°`."""
    role = record.get("role", "?")
    if sign == "-":
        return _("{sign} component {role}").format(sign=sign, role=role)
    return _("{sign} component {role} ({along}, {across}) angle {angle}°").format(
        sign=sign, role=role,
        along=record.get("offset_along_mm", 0.0),
        across=record.get("offset_across_mm", 0.0),
        angle=record.get("angle_deg", 0.0))


def record_report_line(sign: str, record: dict, kind: str) -> str:
    """One '+ '/'- ' Log line naming ONE added/removed via/track record
    (H.2.4 of plan_2026_09_10_cell_refresh_symmetric_and_no_dialog.md).

    Denis: "перечитывание целла — целиком на моей совести. В лог говорим:
    добавили то-то, удалили то-то." A bare counter does not say WHICH record
    changed — exactly the complaint that started this task ("4 record(s) in the
    cell, 3 live item(s)" named nothing). The line carries the net (a literal,
    or `net_from_role ROLE/PAD`), the track's layer, its width and the
    coordinates in the CELL's own mm frame, e.g.
      - track net_from_role C_OUT_BYPASS/1 F.Cu w=0.254 (2.135,-5.04) -> (3.335,-5.04)
      + via net_from_role C_IN_BULK/1 (0.8625,1.374)
    """
    if record.get("net_from_role"):
        pad = record.get("net_from_role_pad")
        net = (_("net_from_role {role}/{pad}").format(
                   role=record["net_from_role"], pad=pad)
               if pad else
               _("net_from_role {role}").format(role=record["net_from_role"]))
    elif record.get("net"):
        net = str(record["net"])
    else:
        net = _("(no net)")
    layer = record.get("layer")
    layer_txt = (" " + str(layer)) if (kind == "track" and layer) else ""
    if kind == "via":
        coords = "({along},{across})".format(
            along=record.get("offset_along_mm", 0.0),
            across=record.get("offset_across_mm", 0.0))
        return _("{sign} via {net}{layer} {coords}").format(
            sign=sign, net=net, layer=layer_txt, coords=coords)
    coords = "({sx},{sy}) -> ({ex},{ey})".format(
        sx=record.get("start_along_mm", 0.0), sy=record.get("start_across_mm", 0.0),
        ex=record.get("end_along_mm", 0.0), ey=record.get("end_across_mm", 0.0))
    return _("{sign} track {net}{layer} w={width} {coords}").format(
        sign=sign, net=net, layer=layer_txt,
        width=record.get("width_mm", ""), coords=coords)


def _net_display(entry: Dict[str, Any]) -> str:
    """Net column text for a via/track row — literal net: as-is, or
    role:<role>[/pad:<pad>] for net_from_role (same "merge two possible
    shapes into one column" precedent as the Nested tab's Content column)."""
    role = entry.get("net_from_role")
    if role:
        pad = entry.get("net_from_role_pad")
        return f"role:{role}/pad:{pad}" if pad else f"role:{role}"
    return str(entry.get("net", ""))


# ── Import-preview helpers (2026-09-03, plan
#    fpga_oscill_missing_copper_and_cell_import §B.3) — pure (no widget
#    access) so the GUI test can exercise the exact rows the dialog shows
#    without a QDialog event loop. The refresh path's old/new/Δ preview
#    helpers and dialog were removed 2026-09-06 (Denis: no line-by-line
#    review — Update from selection applies the plan directly).


def _fmt_mm(value: Optional[float]) -> str:
    """Display form for a geometry value — mm numbers at 4 decimals (matches
    the extractor's storage precision), None rendered as '—' (not-yet-written
    offset key, means 0 in the refresh model)."""
    return "—" if value is None else f"{value:.4f}"


def _record_position(record: Dict[str, Any], kind: str) -> str:
    """Human-readable position for one import-preview row — the record's own
    local geometry relative to the cell's zero-offset origin (the values the
    Apply button would write into the cell). A via shows its single offset
    point, a track its start → end pair."""
    if kind == "via":
        return "({x}, {y})".format(x=_fmt_mm(record.get("offset_along_mm")),
                                   y=_fmt_mm(record.get("offset_across_mm")))
    return "({sx}, {sy}) → ({ex}, {ey})".format(
        sx=_fmt_mm(record.get("start_along_mm")),
        sy=_fmt_mm(record.get("start_across_mm")),
        ex=_fmt_mm(record.get("end_along_mm")),
        ey=_fmt_mm(record.get("end_across_mm")))


def import_preview_rows(plan) -> List[List[str]]:
    """Build the read-only preview rows for an ImportPlan — one row per NEW
    via/track record the Apply button would append. Columns are [kind,
    position, net_source] where position is the record's local geometry
    relative to the cell's zero-offset origin and net_source is the
    classified net (literal / role:[pad:] / rule-net null — the same
    _net_display convention as the dock's own via/track tables). Pure (no
    widget access) so the GUI test can exercise the exact rows the dialog
    shows without a QDialog event loop."""
    rows: List[List[str]] = []
    for record in plan.new_via_records:
        rows.append([_("Via"), _record_position(record, "via"), _net_display(record)])
    for record in plan.new_track_records:
        rows.append([_("Track"), _record_position(record, "track"), _net_display(record)])
    return rows
