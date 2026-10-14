# gui/docks/trees_mount_point.py
"""The mount-point MARKER flow of the node form (Э1/Э2/Э3 of
plan_2026_09_16_mount_point_marker), moved out of `gui/docks/trees_dock.py`:
that file is a giant and may only shrink (rule 45), so the three BUTTON HANDLERS
live here as functions taking the form, while the form keeps thin methods with
the SAME names — the existing guards call `form._on_show_mount_point()` and
friends by name, and a renamed seam would leave them green against nothing.

A mount node's point is the ONE offset with nothing to drag on the board (Ф5), so
the form draws it: a CIRCLE at the point and a SQUARE at the base it is measured
from (Р2). The board is only ever touched through `overlay_markers.owner` and
`board_overlay` (правило 1 двери), every action that reaches the socket asks
`socket_busy()` FIRST and then refuses, and each of the three handlers keeps its
`with ui_thread_board_read(...)` sign — the door's sign travels WITH the code it
covers (tools/door_lint.py).

What stayed in the form, deliberately: `_mount_point_ref`, `_mount_marker_keys`,
`_forget_mount_marker_keys`, `_remove_mount_marker_shapes`, `_update_mount_point_buttons`
and `cleanup` — they are the form's own STATE (which figures it owns, which buttons
are enabled), not the flow of one button, and the tests read that state off the form.
"""
import logging

from kicadstamp.i18n import _
from kicadstamp.tree_position import node_position
from kicadstamp.utils.units import MM

from .. import board_overlay, overlay_markers
from ..connection import ui_thread_board_read
from ..worker import socket_busy
from ._common import ERROR_STYLE as _ERROR_STYLE, WARN_STYLE as _WARN_STYLE, show_message

logger = logging.getLogger(__name__)


def _mount_marker_keys(tree_name, ref):
    """The form's own overlay-key builder, reached through the module that owns
    the naming (trees_dock._mount_marker_keys) — the body moved here with the
    flow, the NAME did not, and `from .trees_dock import ...` at module level
    would be a cycle (trees_dock imports THIS module)."""
    from . import trees_dock
    return trees_dock._mount_marker_keys(tree_name, ref)


def show_mount_point(form) -> None:
    """"Show point on board" (Т1.1): draw the circle where a SAVE would put
    the node's point, and the square at the base it is measured from.

    The point is computed the ONE way a layout computes it (Р3): the node
    the form would build (build_node — the very validator a save runs, so a
    node that could not be saved gets no marker either) placed with
    node_position against the form's own base pose (_base_pose, the SAME
    frame _prefill displayed and build_node converts back through). Unsaved
    field edits therefore count, which is the whole point of the button."""
    with ui_thread_board_read(
            reason="draw this form's mount-point figures at their CURRENT "
                   "positions — a board operation (the shapes read plus the "
                   "create/repaint pair), not an identity scan"):
        adapter = form._live_adapter()
    if adapter is None:
        show_message(_("No live board connection — connect KiCad first."),
                     _ERROR_STYLE, logger)
        return
    if form.kind_combo.currentData() != "mount" or form._tree is None:
        return
    if socket_busy(form._live_connection()):
        return
    pose = form._base_pose()
    if pose is None:
        return
    node = form.build_node()
    if node is None:
        # build_node has already said what is missing (it is the save-time
        # validator): a marker for a node that cannot be saved would show
        # the user a point the config will never carry.
        return
    base_pos, base_rot = pose
    point = node_position(node, base_pos, base_rot)
    point_key, base_key = _mount_marker_keys(form._tree.name,
                                             form._mount_point_ref())
    radius = board_overlay.overlay_marker_radius_mm()
    # Remembered BEFORE the draw: a draw that fails half way (the circle
    # down, the square not) still leaves the form owning those keys, so
    # "Remove" and the close hook take down whatever really exists. The
    # owner is idempotent by key, so pressing again MOVES the same figures
    # instead of stacking a second pair (Т1.1).
    form._mount_marker_keys = (point_key, base_key)
    form._update_mount_point_buttons()
    try:
        overlay_markers.owner.ensure_marker(
            adapter, point_key, point.x / MM, point.y / MM)
    except Exception as e:  # noqa: BLE001 — a visualisation never raises
        show_message(
            _("Mount point: the marker was not drawn — the overlay layer "
              "{layer!r} is not enabled on this board, or the board read "
              "failed ({error}).").format(
                  layer=board_overlay.overlay_layer_name(), error=e),
            _ERROR_STYLE, logger)
        return
    try:
        # Р2: the base is a SQUARE of the marker's own diameter, so "what
        # moves" can never be mistaken for "what it is measured against".
        # ensure_bbox already exists — no new primitive is invented (Ф3).
        overlay_markers.owner.ensure_bbox(
            adapter, base_key,
            base_pos.x / MM - radius, base_pos.y / MM - radius,
            base_pos.x / MM + radius, base_pos.y / MM + radius)
    except Exception as e:  # noqa: BLE001 — the circle is already down
        show_message(
            _("Mount point: the marker was drawn, but the base square was "
              "not ({error}).").format(error=e),
            _WARN_STYLE, logger)


def read_mount_point(form) -> None:
    """"Read from board" (Э2): the offset in the BOARD frame is the circle's
    centre MINUS the base (Р4/Т2.1) and goes into the Cartesian fields of the
    form — the config is NOT written (only Save writes it) and the rotation
    is not touched. Both figures come down afterwards (Р5/Т2.2), exactly
    like the cell anchor's "Read position"."""
    with ui_thread_board_read(
            reason="read the drawn mount-point marker back — it refreshes the "
                   "board FIRST, so the next resolve pays a cold whole-board "
                   "read on top of this one (Кk)"):
        adapter = form._live_adapter()
    if adapter is None:
        show_message(_("No live board connection — connect KiCad first."),
                     _ERROR_STYLE, logger)
        return
    if socket_busy(form._live_connection()):
        return
    if form._mount_marker_keys is None:
        show_message(_("Mount point: no point is shown on the board — "
                       "press “Show point on board” first."),
                     _WARN_STYLE, logger)
        return
    pose = form._base_pose()
    if pose is None:
        return
    point_key = form._mount_marker_keys[0]
    xy = overlay_markers.owner.read_position(adapter, point_key)
    if xy is None:
        # Т2.3: the circle is gone (deleted in KiCad, swept) — a Log line,
        # the fields untouched, the keys forgotten (there is no shape left).
        show_message(_("Mount point: the marker is not on the board any "
                       "more — the offset was not read."),
                     _WARN_STYLE, logger)
        form._forget_mount_marker_keys()
        form._update_mount_point_buttons()
        return
    base_pos, _base_rot = pose
    form.offset_widget.x_edit.setText(f"{xy[0] - base_pos.x / MM:.3f}")
    form.offset_widget.y_edit.setText(f"{xy[1] - base_pos.y / MM:.3f}")
    uuids = form._forget_mount_marker_keys()
    form._update_mount_point_buttons()
    form._remove_mount_marker_shapes(adapter, uuids)


def clear_mount_point(form) -> None:
    """"Remove from board" (Т3.1): both figures, without reading anything."""
    if form._mount_marker_keys is None:
        return
    if socket_busy(form._live_connection()):
        return
    uuids = form._forget_mount_marker_keys()
    form._update_mount_point_buttons()
    with ui_thread_board_read(
            reason="remove the mount-point figures on the button — an "
                   "operation on the board (remove + repaint), not a read"):
        adapter = form._live_adapter()
    form._remove_mount_marker_shapes(adapter, uuids)
