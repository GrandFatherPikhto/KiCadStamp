# tests/gui/docks/test_trees_reread_dialog.py
"""Cells л (Cancel writes nothing), the Apply-into-the-buffer path, the mode switch
and the menu entry of «Reread positions from board…» (plan_2026_11_16_reread_forest_
from_board, commit 1).

What is celled here is the boundary the plan calls out: the TABLE decides nothing by
itself — it renders `build_plan` over ONE read — and Apply is the only writer, into
the CONFIG BUFFER. Cancel must leave the buffer byte for byte as it was, so a user
who opened the table to look at the numbers and closed it loses nothing.
"""
from types import SimpleNamespace

from PyQt6.QtWidgets import QDialog

import gui.docks.trees_menu_actions as menu_mod
import gui.docks.trees_reread_dialog as dialog_mod
from gui.docks.trees_reread_dialog import (RereadForestDialog, apply_plan,
                                           open_reread_table,
                                           run_reread_positions)
from kicadstamp.trees import Tree, TreeAnchor, TreeNode


def _raw(*, angles=None, votes=None, nodes=(), shifts=None):
    angles = dict(angles or {})
    return {"angles": angles,
            "shifts": dict(shifts or {name: (0.0, 0.0) for name in angles}),
            "votes": dict(votes or {}), "nodes": list(nodes), "modules": [],
            "embedded": [], "skips": []}


def _node(tree, ref, offset, stored, *, source="anchor", anchor_tree="main",
          polar=False):
    return {"tree": tree, "anchor_tree": anchor_tree, "ref": ref,
            "offset": offset, "rotation": 0.0, "stored": stored,
            "stored_rotation": 0.0, "polar": polar, "base_rot": 0.0,
            "source": source, "error": None}


def _tree_node(ref, *, xy=(0.0, 0.0), polar=None):
    return TreeNode(ref=ref, kind="placement", xy=xy, polar=polar, rotation=0.0,
                    name=None, group=None, children=[])


def _dock(trees, *, adapter=object()):
    """A dock double: the five seams `run_reread_positions` and `apply_plan` use."""
    touched = {"dirty": 0, "rebuilt": 0, "started": []}
    dock = SimpleNamespace(
        _trees=list(trees), _cfg=None, _active_op=None,
        _ctx=SimpleNamespace(sheet_names={}),
        _main_window=SimpleNamespace(connection=None))
    dock._live_adapter = lambda: adapter
    dock._mark_dirty = lambda: touched.__setitem__("dirty", touched["dirty"] + 1)
    dock._rebuild_tabs = lambda: touched.__setitem__("rebuilt",
                                                     touched["rebuilt"] + 1)
    return dock, touched


def _moved_read():
    """`main` whose only node stands 1 mm off its layout, so the anchor absorbs it."""
    return _raw(angles={"main": 0.0}, votes={"main": [("mh", 1.0, 0.0)]},
                nodes=[_node("main", "mh", (1.0, 0.0), (0.0, 0.0))])


# ── Apply is the ONLY writer, and it writes the BUFFER ─────────────────────

def test_apply_writes_the_anchor_into_the_buffer_and_marks_it_dirty(qapp,
                                                                   monkeypatch):
    mh = _tree_node("mh")
    tree = Tree(name="main", anchor=TreeAnchor(is_origin=True), nodes=[mh])
    dock, touched = _dock([tree])
    monkeypatch.setattr(RereadForestDialog, "exec",
                        lambda self: QDialog.DialogCode.Accepted)

    assert open_reread_table(dock, _moved_read()) is True

    assert tree.anchor.shift_xy == (1.0, 0.0)
    assert touched["dirty"] == 1
    assert touched["rebuilt"] == 1


def test_l_cancel_writes_nothing_at_all(qapp, monkeypatch):
    """л: closing the table without Apply leaves the buffer untouched — not even the
    anchor shift, which is the one number this flow exists to change."""
    mh = _tree_node("mh")
    tree = Tree(name="main", anchor=TreeAnchor(is_origin=True), nodes=[mh])
    dock, touched = _dock([tree])
    monkeypatch.setattr(RereadForestDialog, "exec",
                        lambda self: QDialog.DialogCode.Rejected)

    assert open_reread_table(dock, _moved_read()) is False

    assert tree.anchor.shift_xy is None
    assert mh.xy == (0.0, 0.0)
    assert touched["dirty"] == 0


def test_a_node_row_is_written_in_the_shape_the_node_stores(qapp):
    """A Polar node is written back as (radius, angle) and its `xy` is cleared —
    the same pair the node form shows, never a Cartesian rewrite behind its back.

    The reads carry CARTESIAN mm (the plan compares millimetres); only the WRITTEN
    value is polar: 5 mm at 30° is (4.330127…, −2.5) in KiCad's own sign
    (`atan2(-y, x)`), and after a +2 mm Y move it is 4.3589 mm at 6.5868°.
    """
    node = _tree_node("J1", xy=None, polar=(5.0, 30.0))
    tree = Tree(name="power", anchor=TreeAnchor(is_origin=True), nodes=[node])
    dock, _touched = _dock([tree])
    stored = (4.330127018922193, -2.5)
    raw = _raw(angles={"power": 0.0},
               nodes=[_node("power", "J1", (stored[0], stored[1] + 2.0), stored,
                            anchor_tree="power", polar=True)])
    dialog = RereadForestDialog(None, raw)

    written = apply_plan(dock, dialog.plan)

    assert written == 1
    assert node.xy is None
    # A nanometre, not a float epsilon: the frame pair snaps to the project's own
    # 1e-6 mm grid (`_snap_mm`), and the angle inherits that rounding.
    assert abs(node.polar[0] - 4.358898943540674) < 1e-5
    assert abs(node.polar[1] - 6.586778341342203) < 1e-5


def test_a_node_that_did_not_move_is_not_written(qapp):
    node = _tree_node("mh")
    tree = Tree(name="main", anchor=TreeAnchor(is_origin=True), nodes=[node])
    dock, _touched = _dock([tree])
    raw = _raw(angles={"main": 0.0},
               nodes=[_node("main", "mh", (0.0, 0.0), (0.0, 0.0))])

    assert apply_plan(dock, RereadForestDialog(None, raw).plan) == 0
    assert node.xy == (0.0, 0.0)


# ── the switch re-decides the SAME read ───────────────────────────────────

def test_the_mode_switch_re_decides_without_a_second_read(qapp):
    """а6, the dialog half: flipping to «keep anchors» drops the anchor row and
    turns the same numbers into a node row — and nothing here can read a board."""
    dialog = RereadForestDialog(None, _moved_read())
    assert dialog.plan.anchors_moved == 1
    assert dialog.plan.changed == 0

    dialog.mode_combo.setCurrentIndex(1)

    assert dialog.plan.anchors_moved == 0
    assert dialog.plan.changed == 1


def test_a_tree_box_keeps_that_anchor_alone(qapp):
    dialog = RereadForestDialog(None, _moved_read())
    assert dialog.plan.anchors_moved == 1

    dialog._boxes["main"].setChecked(False)

    assert dialog.plan.anchors_moved == 0
    assert dialog.plan.changed == 1
    assert "main" in dialog._kept_trees


def test_a_still_board_says_so_and_disables_apply(qapp):
    raw = _raw(angles={"main": 0.0},
               nodes=[_node("main", "mh", (0.0, 0.0), (0.0, 0.0))])
    dialog = RereadForestDialog(None, raw)

    assert dialog.apply_button.isEnabled() is False
    assert dialog.table.rowCount() == 1
    assert "nothing moved" in dialog.table.item(0, 4).text()


def test_a_skip_is_a_row_with_its_reason(qapp):
    """A skip is never a silent absence — the reason is on screen, in the table."""
    raw = _raw(angles={}, )
    raw["skips"] = [{"tree": "x", "ref": "u1",
                     "reason": "this node's instance is MIRRORED on the live board"}]
    dialog = RereadForestDialog(None, raw)

    assert dialog.table.rowCount() == 1
    assert "MIRRORED" in dialog.table.item(0, 4).text()


# ── the entry point ───────────────────────────────────────────────────────

def test_without_a_board_the_flow_refuses_and_starts_no_worker(monkeypatch):
    mh = _tree_node("mh")
    tree = Tree(name="main", anchor=TreeAnchor(is_origin=True), nodes=[mh])
    dock, touched = _dock([tree], adapter=None)
    said = []
    monkeypatch.setattr(dialog_mod, "show_message",
                        lambda text, *a, **k: said.append(text))

    assert run_reread_positions(dock) is False
    assert "connect" in said[0].lower()
    assert touched["started"] == []


def test_with_no_trees_the_flow_says_so(monkeypatch):
    dock, _touched = _dock([])
    said = []
    monkeypatch.setattr(dialog_mod, "show_message",
                        lambda text, *a, **k: said.append(text))

    assert run_reread_positions(dock) is False
    assert "no trees" in said[0].lower()


def test_the_worker_payload_carries_the_shared_adapter_and_a_copy_of_sheets(
        monkeypatch):
    """Ф4 of the acceptance: the connection's cached snapshot rides along, so the
    whole forest is read against ONE identity half (the same value a form's
    `_live_snapshot` hands to the anchor read)."""
    mh = _tree_node("mh")
    tree = Tree(name="main", anchor=TreeAnchor(is_origin=True), nodes=[mh])
    dock, touched = _dock([tree])
    dock._ctx = SimpleNamespace(sheet_names={"sheet-uuid": "Sheet"})
    dock._main_window = SimpleNamespace(connection=SimpleNamespace(snapshot="SNAP"))
    seen = {}

    def _start(connection, widgets, worker, finish, failed, payload, **kwargs):
        seen["payload"] = payload
        seen["worker"] = worker
        return "op"

    monkeypatch.setattr(dialog_mod, "start_long_op", _start)
    monkeypatch.setattr(dialog_mod, "socket_busy", lambda connection: False)

    assert run_reread_positions(dock) is True

    assert seen["payload"]["adapter"] is dock._live_adapter()
    assert seen["payload"]["sheet_names"] == {"sheet-uuid": "Sheet"}
    assert seen["payload"]["sheet_names"] is not dock._ctx.sheet_names
    assert seen["payload"]["snapshot"] == "SNAP"
    assert [t.name for t in seen["payload"]["trees"]] == ["main"]
    assert seen["worker"] is dialog_mod.read_forest
    assert dock._active_op == "op"


def test_the_menu_entry_exists_and_calls_the_flow(real_main_window, monkeypatch):
    seen = []
    monkeypatch.setattr(menu_mod, "run_reread_positions",
                        lambda owner, **kwargs: seen.append((owner, kwargs)))

    real_main_window.reread_positions_action.trigger()

    assert [owner for owner, _kwargs in seen] == [real_main_window]
    # The action itself travels back as the operation's guard widget (Э2).
    assert seen[0][1]["trigger"] is real_main_window.reread_positions_action
    assert "Reread positions" in real_main_window.reread_positions_action.text()
