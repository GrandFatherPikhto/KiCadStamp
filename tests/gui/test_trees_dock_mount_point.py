# tests/gui/test_trees_dock_mount_point.py
"""The mount node's POINT MARKER (plan_2026_09_16_mount_point_marker).

A kind "mount" node's offset is the ONE offset in the tree with nothing to drag
on the board (Ф5), so the node form draws it itself: a CIRCLE at the point a
save would put the node's point, and a SQUARE at the base it is measured from
(Р2 — deliberately not a second circle, so "what moves" can never be confused
with "what it is measured against"). The circle is dragged in KiCad and read
back into the Cartesian fields, exactly like the cell anchor's marker (Ф4).

The board here is the same duck-typed fake `gui/board_overlay.py` and
`gui/overlay_markers.py` already speak (tests/gui/test_trees_dock.py builds it),
reached through a RECORDING double over the real overlay owner: the figures
really land on the fake board, so "one circle, one square" is observable rather
than asserted on a call log, and every call the form makes is recorded too.

The base pose is pinned with the file's own established idiom
(`_resolve_node_base_pose` monkeypatched) — it is the base ROTATION this task's
numbers are about, and pinning it keeps the arithmetic honest for 0°/30°/90°
instead of re-deriving a live component's pose.
"""
import logging
from types import SimpleNamespace

import pytest

from kicadstamp.domain.geometry import Vector2
from kicadstamp.exceptions import ValidationError
from kicadstamp.tree_position import node_position
from kicadstamp.utils.units import MM

from kipy.board_types import BoardCircle, BoardRectangle
from kipy.geometry import Vector2 as KipyVector2

import gui.board_overlay as board_overlay_mod
import gui.overlay_markers as markers_mod
import gui.docks.trees_dock as td_mod
from gui.docks.trees_dock import (
    NodeFormWidget,
    _NodeDialog,
    _mount_base_key,
    _mount_point_key,
)

from tests.gui.test_trees_dock import (  # noqa: F401 — sync_long_ops is a fixture
    _OverlayAdapter,
    _dock_with,
    _no_modal,
    _node_form_for,
    _tree_circles,
    sync_long_ops,
)

# A tree with ONE mount node anchored to a role from OUTSIDE the tree (the LIVE
# method), with its own offset — so the drawn point is never the base itself.
MOUNT_CFG = {
    "trees": [
        {"name": "t1", "anchor": {"origin": True},
         "nodes": [{"ref": "M1", "kind": "mount", "anchor": {"role": "FPGA"},
                    "xy": [3.0, -2.0], "rotation": 0.0}]},
    ],
}

_BASE_POS_MM = (10.0, 20.0)


class _MarkerSpy:
    """Recording double over the REAL overlay owner (Э1's сторожа ask for one).

    Every method the form calls is appended to `calls` and then delegated, so
    the figures really land on the fake board and `read_position` returns the
    real centre — the spy adds observation, it does not replace behaviour.
    `drag()` is the "the user moved the circle in KiCad" half the read tests
    need."""

    def __init__(self):
        self._owner = markers_mod.owner
        self.calls: list = []

    # ── the surface NodeFormWidget uses ───────────────────────────────────
    def ensure_marker(self, adapter, key, x_mm, y_mm, **kwargs):
        self.calls.append(("marker", key, x_mm, y_mm))
        return self._owner.ensure_marker(adapter, key, x_mm, y_mm, **kwargs)

    def ensure_bbox(self, adapter, key, x1_mm, y1_mm, x2_mm, y2_mm, **kwargs):
        self.calls.append(("bbox", key, x1_mm, y1_mm, x2_mm, y2_mm))
        return self._owner.ensure_bbox(adapter, key, x1_mm, y1_mm, x2_mm,
                                       y2_mm, **kwargs)

    def read_position(self, adapter, key):
        self.calls.append(("read", key))
        return self._owner.read_position(adapter, key)

    def forget_key(self, key):
        self.calls.append(("forget", key))
        return self._owner.forget_key(key)

    # ── the surface the DOCK uses on the same namespace ───────────────────
    def forget_scope(self, scope):
        self.calls.append(("forget_scope", scope))
        return self._owner.forget_scope(scope)

    def has_key(self, key):
        return self._owner.has_key(key)

    def keys(self):
        return self._owner.keys()

    def uuid_for(self, key):
        return self._owner.uuid_for(key)

    # ── the test's own lever ──────────────────────────────────────────────
    def drag(self, adapter, key, dx_mm, dy_mm):
        """The user dragged `key`'s circle by (dx, dy) mm in KiCad."""
        uuid = self._owner.uuid_for(key)
        assert uuid, f"no shape is owned by {key!r}"
        for shape in adapter._board.shapes:
            if isinstance(shape, BoardCircle) and str(shape.id.value) == uuid:
                shape.center = KipyVector2.from_xy(
                    shape.center.x + int(round(dx_mm * MM)),
                    shape.center.y + int(round(dy_mm * MM)))
                return
        raise AssertionError(f"shape {uuid!r} is not on the fake board")


def _install_spy(monkeypatch) -> _MarkerSpy:
    spy = _MarkerSpy()
    monkeypatch.setattr(markers_mod, "owner", spy)
    return spy


def _rects(adapter):
    """The SQUARES on the fake board — the base markers (Ф3: no cross primitive
    exists, the base uses the already-known bbox)."""
    return [s for s in adapter._board.shapes if isinstance(s, BoardRectangle)]


def _fields(form) -> tuple:
    """The two Cartesian offset fields as numbers."""
    return (float(form.offset_widget.x_edit.text()),
            float(form.offset_widget.y_edit.text()))


def _mount_setup(main_window, tmp_path, monkeypatch, *, base_rot=0.0,
                 base_pos_mm=_BASE_POS_MM, xy=(3.0, -2.0), rotation=0.0,
                 resolver=None, live=True):
    """(dock, tree, mount node, adapter, spy, form) for MOUNT_CFG, with the base
    pose pinned and the marker calls recorded."""
    dock, _root = _dock_with(main_window, tmp_path, MOUNT_CFG)
    tree = dock._current_tree()
    mount = tree.nodes[0]
    mount.xy = tuple(xy)
    mount.rotation = rotation
    adapter = _OverlayAdapter() if live else None
    main_window.connection.board = (SimpleNamespace(adapter=adapter)
                                    if adapter is not None else None)
    base_pos = Vector2.from_xy_mm(*base_pos_mm)
    if resolver is None:
        def resolver(*_args, **_kwargs):          # noqa: F811 — the pin itself
            return base_pos, base_rot, False
    monkeypatch.setattr(td_mod, "_resolve_node_base_pose", resolver)
    spy = _install_spy(monkeypatch)
    form = _node_form_for(dock, tree, None, existing=mount, adapter=adapter)
    return SimpleNamespace(
        dock=dock, tree=tree, mount=mount, adapter=adapter, spy=spy, form=form,
        base_pos=base_pos, base_rot=base_rot,
        point_key=_mount_point_key("t1", "M1"),
        base_key=_mount_base_key("t1", "M1"))


# ═══════════════════════════════════════════════════════════════════════════
# Э1 — the three buttons and the two figures
# ═══════════════════════════════════════════════════════════════════════════

def _row_shown(form) -> bool:
    """Is the mount-point row shown BY THE FORM?

    isVisibleTo(form) is useless here: the row lives on the Position tab, and
    Qt hides a non-current tab page, so it reads False even for a shown row
    (measured: diagnostics/probe_mount_point_marker.py). Asking the row's own
    PARENT — the row's own setVisible() is the only thing that can hide it from
    there — is the honest question."""
    row = form.mount_point_row
    return row.isVisibleTo(row.parentWidget())


def test_the_row_is_offered_for_a_mount_node_alone(
        main_window, tmp_path, monkeypatch):
    """Т1.1/Р1: the row follows the mount node — every other kind (they have a
    live component, their own circle, or the tree anchor pair, Ф5) has no
    mount-point controls at all."""
    env = _mount_setup(main_window, tmp_path, monkeypatch)
    form = env.form

    assert _row_shown(form) is True
    assert form.show_mount_point_button.isEnabled() is True

    for kind in ("clone", "placement", "component", "net_trace", "copper",
                 "external", "module"):
        form.kind_combo.setCurrentIndex(form.kind_combo.findData(kind))
        assert _row_shown(form) is False, kind
        assert form.show_mount_point_button.isEnabled() is False, kind

    form.kind_combo.setCurrentIndex(form.kind_combo.findData("mount"))
    assert _row_shown(form) is True
    assert form.show_mount_point_button.isEnabled() is True


@pytest.mark.parametrize("base_rot", [0.0, 90.0, 30.0])
def test_show_places_the_circle_exactly_where_the_layout_would(
        base_rot, main_window, tmp_path, monkeypatch):
    """Т1.1/Р3 (numbers): the circle lands at node_position(build_node(),
    base_pos, base_rot) — the composition the redraw itself uses, for a
    non-zero offset and a rotated base."""
    env = _mount_setup(main_window, tmp_path, monkeypatch, base_rot=base_rot)

    env.form._on_show_mount_point()

    expected = node_position(env.form.build_node(), env.base_pos, base_rot)
    circles = _tree_circles(env.adapter)
    assert len(circles) == 1
    # ±1 nm, and no more: the overlay is stored in WHOLE nanometres
    # (board_overlay.draw_marker does int(x_mm * MM)) and the composition
    # crosses mm once. A real error here is millimetres wide (a missing
    # rotation, a dropped base), never one nanometre.
    assert circles[0].center.x == pytest.approx(expected.x, abs=1)
    assert circles[0].center.y == pytest.approx(expected.y, abs=1)
    assert ("marker", env.point_key) in [(c[0], c[1]) for c in env.spy.calls]
    marker_call = [c for c in env.spy.calls if c[0] == "marker"][0]
    assert marker_call[2] == pytest.approx(expected.x / MM, abs=1e-6)
    assert marker_call[3] == pytest.approx(expected.y / MM, abs=1e-6)


def test_show_counts_an_unsaved_field_edit_and_never_stacks(
        main_window, tmp_path, monkeypatch):
    """Т1.1: the button reads the fields as they ARE, so an unapplied X edit
    moves the circle by exactly that much; pressing again MOVES the same
    figures instead of drawing a second pair (the owner is idempotent by key)."""
    env = _mount_setup(main_window, tmp_path, monkeypatch)   # base rotation 0
    form = env.form

    form._on_show_mount_point()
    first = _tree_circles(env.adapter)[0].center
    form.offset_widget.x_edit.setText("7.5")                 # not saved yet
    form._on_show_mount_point()

    circles = _tree_circles(env.adapter)
    assert len(circles) == 1
    assert circles[0].center.x - first.x == int(round(4.5 * MM))
    assert circles[0].center.y == first.y
    assert len(_rects(env.adapter)) == 1


def test_show_marks_the_base_with_a_square_of_the_marker_size(
        main_window, tmp_path, monkeypatch):
    """Т1.1/Р2: the base is a SQUARE through the already-existing ensure_bbox,
    centred on the base with a side of 2 × overlay_marker_radius_mm() — and it
    is NOT a circle, so the draggable point is never mistaken for it."""
    env = _mount_setup(main_window, tmp_path, monkeypatch)

    env.form._on_show_mount_point()

    radius = board_overlay_mod.overlay_marker_radius_mm()
    bx, by = env.base_pos.x / MM, env.base_pos.y / MM
    assert ("bbox", env.base_key, bx - radius, by - radius,
            bx + radius, by + radius) in env.spy.calls
    rects = _rects(env.adapter)
    assert len(rects) == 1
    assert (rects[0].top_left.x, rects[0].top_left.y) == (
        int((bx - radius) * MM), int((by - radius) * MM))
    assert (rects[0].bottom_right.x, rects[0].bottom_right.y) == (
        int((bx + radius) * MM), int((by + radius) * MM))
    # Exactly ONE circle, and it is the point (which is offset from the base).
    circles = _tree_circles(env.adapter)
    assert len(circles) == 1
    assert (circles[0].center.x, circles[0].center.y) != (
        int(bx * MM), int(by * MM))


def test_the_marker_works_in_add_mode_where_no_node_exists_yet(
        main_window, tmp_path, monkeypatch):
    """Т1.1 in ADD mode: there is no node object yet, so the keys are built from
    the name typed into the ref field — the button must not need a saved node."""
    dock, _root = _dock_with(main_window, tmp_path, MOUNT_CFG)
    tree = dock._current_tree()
    adapter = _OverlayAdapter()
    main_window.connection.board = SimpleNamespace(adapter=adapter)
    base_pos = Vector2.from_xy_mm(*_BASE_POS_MM)
    monkeypatch.setattr(td_mod, "_resolve_node_base_pose",
                        lambda *_a, **_k: (base_pos, 0.0, False))
    spy = _install_spy(monkeypatch)
    form = NodeFormWidget(dock, [], set(), "Add node", cfg=dock._cfg,
                          adapter=adapter, sheet_names={}, tree=tree,
                          parent_node=None)
    form.kind_combo.setCurrentIndex(form.kind_combo.findData("mount"))
    form.mount_anchor_widget.load(mode="anchor", role="FPGA")
    form.offset_widget.load(x=3.0, y=-2.0)
    form.ref_combo.setCurrentText("M_NEW")
    form._refresh_for_new_anchor()

    form._on_show_mount_point()

    assert spy.has_key(_mount_point_key("t1", "M_NEW")) is True
    assert spy.has_key(_mount_base_key("t1", "M_NEW")) is True
    assert len(_tree_circles(adapter)) == 1

    form._on_clear_mount_point()

    _assert_nothing_left(SimpleNamespace(spy=spy, adapter=adapter,
                                         point_key=_mount_point_key("t1", "M_NEW"),
                                         base_key=_mount_base_key("t1", "M_NEW")),
                         form)


def test_buttons_follow_the_figures_they_own(
        main_window, tmp_path, monkeypatch):
    """Т1.2: "Read" needs something shown AND a base; "Remove" needs the
    remembered keys alone — so a form whose base stopped resolving can still
    take its own figures down."""
    env = _mount_setup(main_window, tmp_path, monkeypatch)
    form = env.form

    assert form.read_mount_point_button.isEnabled() is False
    assert form.clear_mount_point_button.isEnabled() is False

    form._on_show_mount_point()
    assert form.read_mount_point_button.isEnabled() is True
    assert form.clear_mount_point_button.isEnabled() is True

    form._on_clear_mount_point()
    assert form.read_mount_point_button.isEnabled() is False
    assert form.clear_mount_point_button.isEnabled() is False


# ═══════════════════════════════════════════════════════════════════════════
# Э2 — reading the dragged circle back into the form
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("base_rot", [0.0, 30.0])
def test_show_then_read_without_dragging_changes_nothing(
        base_rot, main_window, tmp_path, monkeypatch):
    """Т2.1 round trip: the circle is drawn from the fields and read straight
    back against the same base — the fields must not drift (±1e-3 mm), which is
    the proof that draw and save share ONE frame."""
    env = _mount_setup(main_window, tmp_path, monkeypatch, base_rot=base_rot)
    form = env.form
    before = _fields(form)

    form._on_show_mount_point()
    form._on_read_mount_point()

    after = _fields(form)
    assert after[0] == pytest.approx(before[0], abs=1e-3)
    assert after[1] == pytest.approx(before[1], abs=1e-3)


def test_reading_after_a_drag_moves_the_offset_by_the_drag(
        main_window, tmp_path, monkeypatch):
    """Т2.1/Р4 (numbers): the offset in the board frame is the circle's centre
    minus the base, and the ROTATION is not touched — a rotated base included."""
    env = _mount_setup(main_window, tmp_path, monkeypatch, base_rot=30.0)
    form = env.form
    form._on_show_mount_point()
    before = _fields(form)
    before_rot = form.rotation_edit.text()

    env.spy.drag(env.adapter, env.point_key, 2.0, -1.0)
    form._on_read_mount_point()

    after = _fields(form)
    assert after[0] == pytest.approx(before[0] + 2.0, abs=1e-3)
    assert after[1] == pytest.approx(before[1] - 1.0, abs=1e-3)
    assert form.rotation_edit.text() == before_rot


def test_a_deleted_circle_is_a_log_line_and_touches_no_field(
        main_window, tmp_path, monkeypatch, caplog):
    """Т2.3/Р8: the user removed the circle in KiCad — a Log line, the fields
    untouched, the keys forgotten, and NO modal (a visualisation never nags)."""
    _no_modal(monkeypatch)
    env = _mount_setup(main_window, tmp_path, monkeypatch)
    form = env.form
    form._on_show_mount_point()
    before = _fields(form)

    env.adapter.remove_by_ids([env.spy.uuid_for(env.point_key)])
    with caplog.at_level(logging.WARNING):
        form._on_read_mount_point()

    assert _fields(form) == before
    assert form._mount_marker_keys is None
    assert env.spy.has_key(env.point_key) is False
    assert any("Mount point" in r.getMessage() for r in caplog.records)


# ═══════════════════════════════════════════════════════════════════════════
# Э3 — cleanup: after a read, by the button, on leaving the node, on close
# ═══════════════════════════════════════════════════════════════════════════

def _assert_nothing_left(env, form=None):
    assert env.spy.has_key(env.point_key) is False
    assert env.spy.has_key(env.base_key) is False
    assert _tree_circles(env.adapter) == []
    assert _rects(env.adapter) == []
    assert ("forget", env.point_key) in env.spy.calls
    assert ("forget", env.base_key) in env.spy.calls
    if form is not None:
        assert form._mount_marker_keys is None


def test_a_successful_read_takes_both_figures_down(
        main_window, tmp_path, monkeypatch):
    """Т2.2/Р5: after a successful read BOTH keys are forgotten and both shapes
    are handed to the removal — no leftover circle on the layer."""
    env = _mount_setup(main_window, tmp_path, monkeypatch)
    env.form._on_show_mount_point()
    assert len(_tree_circles(env.adapter)) == 1 and len(_rects(env.adapter)) == 1

    env.form._on_read_mount_point()

    _assert_nothing_left(env, env.form)


def test_the_remove_button_takes_both_figures_down_and_spares_the_fields(
        main_window, tmp_path, monkeypatch):
    """Т3.1: "Remove from board" is the same cleanup WITHOUT a read — the
    fields keep their values."""
    env = _mount_setup(main_window, tmp_path, monkeypatch)
    form = env.form
    form._on_show_mount_point()
    before = _fields(form)

    form._on_clear_mount_point()

    _assert_nothing_left(env, form)
    assert _fields(form) == before


def test_leaving_the_node_takes_the_figures_down(
        main_window, tmp_path, monkeypatch):
    """Т3.2: selecting another row replaces the panel's form, and the form's
    own figures go with it (the previous page is dropped with deleteLater —
    no closeEvent is ever delivered to a plain QWidget)."""
    env = _mount_setup(main_window, tmp_path, monkeypatch)
    tree_widget = env.dock._current_tree_widget()
    tree_widget.setCurrentItem(env.dock._node_items["M1"])
    form = env.dock._embedded_form_of(
        env.dock._panel_page(env.dock._active_form_panel()))
    assert isinstance(form, NodeFormWidget)
    form._on_show_mount_point()
    assert env.spy.has_key(env.point_key)

    # the user clicks the tree's anchor pseudo-root row
    tree_widget.setCurrentItem(tree_widget.invisibleRootItem().child(0))

    _assert_nothing_left(env)


def test_closing_the_node_dialog_takes_the_figures_down(
        main_window, tmp_path, monkeypatch):
    """Т3.2 (modal path): Edit's Close, Add's Cancel and OK alike funnel through
    QDialog.done() — the dialog's form cleans up exactly once."""
    env = _mount_setup(main_window, tmp_path, monkeypatch)
    dialog = _NodeDialog(
        env.dock, env.dock._all_ref_candidates(), env.dock._used_refs(),
        "Edit node", cfg=env.dock._cfg, adapter=env.adapter, sheet_names={},
        tree=env.tree, parent_node=None, existing=env.mount)

    dialog._form._on_show_mount_point()
    assert len(_tree_circles(env.adapter)) == 1

    dialog.done(0)

    _assert_nothing_left(env, dialog._form)


def test_changing_the_mount_anchor_drops_the_stale_figures(
        main_window, tmp_path, monkeypatch):
    """Т1.3: the figures were drawn FROM the old base, so an anchor change takes
    them down (reading them back against the new base would be a lie) and
    "Read from board" goes unavailable until something is shown again."""
    env = _mount_setup(main_window, tmp_path, monkeypatch)
    form = env.form
    form._on_show_mount_point()
    assert env.spy.has_key(env.point_key)

    form.mount_anchor_widget.load(mode="anchor", role="OTHER_ROLE")

    _assert_nothing_left(env, form)
    assert form.read_mount_point_button.isEnabled() is False
    assert form.show_mount_point_button.isEnabled() is True


def test_removal_uses_the_remembered_keys_not_the_current_ref(
        main_window, tmp_path, monkeypatch):
    """Р6/Т11: the form removes the keys it DREW under, so a rename between
    "Show" and "Remove" cannot leave an orphan circle behind."""
    env = _mount_setup(main_window, tmp_path, monkeypatch)
    form = env.form
    form._on_show_mount_point()
    renamed_key = _mount_point_key("t1", "M1_RENAMED")

    env.mount.ref = "M1_RENAMED"
    form._on_clear_mount_point()

    _assert_nothing_left(env, form)
    assert env.spy.has_key(renamed_key) is False
    assert env.spy.keys() == []


def test_a_root_switch_drops_the_whole_mount_point_namespace(
        main_window, tmp_path, monkeypatch, sync_long_ops):
    """Т3.3: a real root change belongs to another project — the whole
    `mount-point` namespace goes with it, exactly like the tree pair."""
    env = _mount_setup(main_window, tmp_path, monkeypatch)
    env.form._on_show_mount_point()
    assert env.spy.has_key(env.point_key)

    other_root = tmp_path / "other_root.sexp"
    other_root.write_text("(trees)", encoding="utf-8")
    env.dock.set_root_file(other_root)

    assert env.spy.keys() == []
    assert _tree_circles(env.adapter) == []
    assert _rects(env.adapter) == []


# ═══════════════════════════════════════════════════════════════════════════
# Refusals: the busy socket (правило 3 двери) and the missing base (Р7)
# ═══════════════════════════════════════════════════════════════════════════

def test_a_busy_socket_refuses_every_action_without_sending(
        main_window, tmp_path, monkeypatch):
    """Правило 3: while another owner holds the shared kipy REQ socket, "Show"
    and "Read" send NOTHING (no interleaved second transaction, the live
    symptom being "Error received reply from KiCad: Operation canceled") and
    "Remove" keeps its keys so the whole-layer sweep still finds the shapes."""
    env = _mount_setup(main_window, tmp_path, monkeypatch)
    form = env.form
    before = _fields(form)

    # 1. "Show" while the socket is busy: not one call reaches the board.
    main_window.connection.long_op_active = True
    form._on_show_mount_point()

    assert env.spy.calls == []
    assert env.adapter.created == []
    assert form._mount_marker_keys is None
    assert _fields(form) == before

    # 2. Shown first, THEN the socket goes busy: "Read" must not read either —
    # the figures stay on the board and the fields keep their values.
    main_window.connection.long_op_active = False
    form._on_show_mount_point()
    main_window.connection.long_op_active = True
    before_read = _fields(form)
    form._on_read_mount_point()

    assert _fields(form) == before_read
    assert not any(call[0] == "read" for call in env.spy.calls)

    # 3. …and a "Remove" that arrives while the socket is busy drops NOTHING.
    form._on_clear_mount_point()

    assert env.spy.has_key(env.point_key) is True
    assert len(_tree_circles(env.adapter)) == 1


def test_without_a_live_board_the_show_and_read_buttons_are_unavailable(
        main_window, tmp_path, monkeypatch):
    """Р7: no adapter at all — the two actions that need a base are disabled,
    and the reason is the SAME text the form already shows under the fields
    (no new wording is invented)."""
    env = _mount_setup(main_window, tmp_path, monkeypatch, live=False)

    assert env.form.show_mount_point_button.isEnabled() is False
    assert env.form.read_mount_point_button.isEnabled() is False
    assert env.form.offset_frame_label.isVisibleTo(env.form) is True


def test_an_unresolvable_base_disables_show_and_read(
        main_window, tmp_path, monkeypatch):
    """Р7: a live board whose base does not resolve (a role the board does not
    carry) is the same state — the buttons are unavailable, nothing is drawn."""
    def _boom(*_args, **_kwargs):
        raise ValidationError("role FPGA is not on this board")

    env = _mount_setup(main_window, tmp_path, monkeypatch, resolver=_boom)

    assert env.form.show_mount_point_button.isEnabled() is False
    assert env.form.read_mount_point_button.isEnabled() is False
    assert env.spy.calls == []
