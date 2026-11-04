# tests/gui/docks/test_trees_mounts_dialog.py
"""Т5 of plan_2026_11_03_add_mounts_table — the mounts window.

The condition of §23 is the reason half of these cells exist: the window is
MODELESS, so it lives longer than one tick. It therefore keeps the TREE NAME and
`parent_ref` — never a `Tree`/`TreeNode` — and resolves both at the moment of use.
The two "what if the world changed meanwhile" cells (tree reloaded, parent deleted)
are that rule made visible.

The rest pin the table's own contract: a taken row is grey and has no checkbox, a
hand-typed name survives a template change (and typing the computed name back makes
the cell template-driven again), the net filter narrows only what is shown, a
half-typed template is a refusal in the window (never a traceback, never a modal),
and EVERY refusal is a line in the window's status area plus the Log.

No board, no dock, no config: the window is driven through its own API, which is
exactly how Т6 will drive it.
"""
import sys
import time

import pytest
from PyQt6 import sip
from PyQt6.QtCore import Qt

import gui.worker as worker_mod
from gui.docks import trees_mounts_dialog as dialog_mod
from gui.docks import trees_mounts_flow
from gui.docks.trees_mounts_dialog import MountsDialog, open_mounts_dialog
from kicadstamp.tree_mounts import MountRow
from kicadstamp.trees import Tree, TreeAnchor, TreeNode


class _Dock:
    """The two things the window asks a dock: the trees it holds NOW, and the
    staging path of the write."""

    def __init__(self, *trees):
        self._trees = list(trees)
        self.dirty = 0
        self.rebuilt = 0

    def _mark_dirty(self):
        self.dirty += 1

    def _rebuild_tabs(self):
        self.rebuilt += 1


def _parent(ref="PARENT"):
    return TreeNode(ref=ref, kind="placement", xy=(0.0, 0.0), polar=None,
                    rotation=0.0, name=None, group=None, children=[])


def _tree(*nodes, name="fpga"):
    return Tree(name=name, anchor=TreeAnchor(is_origin=True), nodes=list(nodes))


def _rows():
    return [MountRow(pad="117", net="/FPGA/+3V3_VCCIO"),
            MountRow(pad="130", net="GND"),
            MountRow(pad="7", net="unconnected-(IC1-IO_5-Pad7)")]


def _two_nets():
    """Two pads on two nets — the VCCA / VCCIO pair §24 б is about."""
    return [MountRow(pad="117", net="VCCA"), MountRow(pad="130", net="VCCIO")]


def _result(rows=None, occupied=None, role="FPGA"):
    return {"instance": {"ref": "IC1", "role": role, "cluster": "FPGA",
                         "sheet": "FPGA"},
            "rows": _rows() if rows is None else rows,
            "occupied": occupied or {}}


@pytest.fixture
def dialog(qtbot):
    def _make(*, dock=None, tree_name="fpga", parent_ref=None, **kwargs):
        made = MountsDialog(None, dock=dock or _Dock(_tree()), connection=None,
                            cfg=None, sheet_names={}, tree_name=tree_name,
                            parent_ref=parent_ref, **kwargs)
        qtbot.addWidget(made)
        return made
    return _make


def _tick(dialog, pad):
    """Ticks a row THROUGH THE TABLE (the cell's own path), not by poking state."""
    for row in range(dialog.table.rowCount()):
        if dialog.table.item(row, 1).text() == pad:
            dialog.table.item(row, 0).setCheckState(Qt.CheckState.Checked)
            return
    raise AssertionError(f"no row for pad {pad}")


def _ref_cell(dialog, pad) -> str:
    for row in range(dialog.table.rowCount()):
        if dialog.table.item(row, 1).text() == pad:
            return dialog.table.item(row, 3).text()
    raise AssertionError(f"no row for pad {pad}")


# ── the window itself ─────────────────────────────────────────────────────

class TestTheWindow:
    def test_it_is_modeless(self, dialog):
        """Д-4: one modeless window — no exec(), no QEventLoop of its own."""
        window = dialog()
        assert window.isModal() is False

    def test_it_holds_names_and_never_objects(self, dialog):
        """§23: while this window is open the dock may reload; a retained Tree or
        TreeNode would then be a tree the dock no longer has."""
        window = dialog(tree_name="fpga", parent_ref="PARENT")
        held = vars(window)
        forbidden = {"Tree", "TreeNode", "TreeAnchor", "Footprint"}
        assert not [(name, type(value).__name__) for name, value in held.items()
                    if type(value).__name__ in forbidden]
        assert (window._tree_name, window._parent_ref) == ("fpga", "PARENT")

    def test_the_table_has_the_five_columns(self, dialog):
        window = dialog()
        labels = [window.table.horizontalHeaderItem(i).text() for i in range(5)]
        assert labels[1:4] == ["Pad", "Net", "Ref"]
        assert labels[0] == ""

    def test_no_window_for_a_refusal(self, dialog, caplog):
        """Rule 43: every refusal is a line in the window AND the Log."""
        window = dialog()
        with caplog.at_level("ERROR", logger="gui.docks.trees_mounts_dialog"):
            window._tell("something went wrong")
        assert window.status_label.text() == "something went wrong"
        assert any("something went wrong" in record.message for record in caplog.records)


# ── the table: rows, greying, names, filter ───────────────────────────────

class TestTheTable:
    def test_the_rows_come_from_the_read(self, dialog):
        window = dialog()
        window.filled(_result())
        assert window.table.rowCount() == 3
        assert _ref_cell(window, "117") == "117"

    def test_a_taken_row_is_grey_and_has_no_checkbox(self, dialog):
        """Д6: the pad already carries a mount standing on it — no checkbox, and
        the existing node's ref is shown instead."""
        window = dialog()
        window.filled(_result(occupied={"130": "mnt_130"}))
        for row in range(window.table.rowCount()):
            if window.table.item(row, 1).text() == "130":
                flags = window.table.item(row, 0).flags()
                assert not (flags & Qt.ItemFlag.ItemIsUserCheckable)
                assert window.table.item(row, 4).text() == "mnt_130"
                return
        raise AssertionError("row 130 missing")

    def test_a_hand_typed_name_survives_a_template_change(self, dialog):
        """Д5: a hand-edited cell is never overwritten; typing the computed name
        back makes it template-driven again."""
        window = dialog()
        window.filled(_result())
        window.table.item(0, 3).setText("dac_3_dvdd")          # pad 117, by hand
        window.template_edit.setText("vccio_{pad}")
        assert _ref_cell(window, "117") == "dac_3_dvdd"        # kept
        assert _ref_cell(window, "130") == "vccio_130"         # re-filled

        window.table.item(0, 3).setText("vccio_117")           # back to computed
        window.template_edit.setText("m_{pad}")
        assert _ref_cell(window, "117") == "m_117"

    def test_the_net_filter_narrows_only_what_is_shown(self, dialog):
        window = dialog()
        window.filled(_result())
        window.net_filter_edit.setText("vcc")
        assert [window.table.item(r, 1).text() for r in range(window.table.rowCount())] \
            == ["117"]

    def test_a_tick_survives_the_net_filter(self, dialog):
        """§24 б: the filter narrows what is SHOWN and nothing else. The VCCA tick
        used to vanish silently the moment VCCIO was typed."""
        window = dialog()
        window.filled(_result(rows=_two_nets()))
        window.net_filter_edit.setText("vcca")
        window.mark_all_button.click()
        window.net_filter_edit.setText("vccio")
        window.mark_all_button.click()
        assert sorted(window.selected_pads()) == ["117", "130"]
        assert window.hidden_ticked() == 1
        assert "hidden by the filter" in window.status_label.text()

    def test_clear_only_clears_what_is_visible(self, dialog):
        """The same rule from the other side: «Clear» is the twin of «Mark all visible»."""
        window = dialog()
        window.filled(_result(rows=_two_nets()))
        window.mark_all_button.click()                 # both ticked
        window.net_filter_edit.setText("vccio")        # 117 is hidden now
        window.clear_button.click()
        assert window.selected_pads() == ["117"]

    def test_a_refusal_is_not_overwritten_by_the_tick_line(self, dialog):
        """The tick line is a courtesy; a refusal owns the status area until the user
        answers it (§24 б)."""
        window = dialog()
        window.filled(_result(rows=_two_nets()))
        window.accept_plan()                           # nothing ticked → refusal
        assert "no pad is ticked" in window.status_label.text()
        window.net_filter_edit.setText("vcca")         # a successful rebuild
        assert "no pad is ticked" in window.status_label.text(), \
            "a courtesy line must not overwrite the refusal"
        window.mark_all_button.click()                 # ticking IS the answer
        assert "no pad is ticked" not in window.status_label.text()
        assert window.selected_pads() == ["117"]

    def test_mark_all_visible_skips_taken_rows(self, dialog):
        window = dialog()
        window.filled(_result(occupied={"130": "mnt_130"}))
        window.mark_all_button.click()
        assert set(window.selected_pads()) == {"117", "7"}

    def test_a_conflicting_tabpin_row_says_so(self, dialog):
        window = dialog()
        window.filled(_result(rows=[MountRow(pad="9", net="/A", pads_with_this_number=2,
                                             conflicting_nets=("/A", "/B"))]))
        assert "/A" in window.table.item(0, 2).toolTip()


# ── «Read pads» ───────────────────────────────────────────────────────────

class TestReadPads:
    def test_it_starts_the_worker_with_the_tree_the_dock_holds_now(self, dialog,
                                                                   monkeypatch):
        captured = {}
        monkeypatch.setattr(dialog_mod.trees_mounts_flow, "start_read_pads",
                            lambda connection, **kwargs: captured.update(kwargs) or object())
        dock = _Dock(_tree())
        window = dialog(dock=dock, ref="IC1")
        window.read_pads()
        assert captured["tree"] is dock._trees[0]
        assert captured["on_ready"] == window.filled
        assert captured["widgets"] == (window.read_button,)
        assert window.read_button.isEnabled() is False
        assert window.status_label.text() == "reading…"

    def test_a_missing_tree_is_refused_without_starting_anything(self, dialog,
                                                                 monkeypatch):
        started = []
        monkeypatch.setattr(dialog_mod.trees_mounts_flow, "start_read_pads",
                            lambda *a, **k: started.append(a) or object())
        window = dialog(dock=_Dock(), tree_name="gone")
        window.read_pads()
        assert started == []
        assert "gone" in window.status_label.text()

    def test_nothing_started_says_so(self, dialog, monkeypatch):
        """No board / busy socket: `start_read_pads` returns None — the button comes
        back and the window says nothing was read."""
        monkeypatch.setattr(dialog_mod.trees_mounts_flow, "start_read_pads",
                            lambda *a, **k: None)
        window = dialog(ref="IC1")
        window.read_pads()
        assert window.read_button.isEnabled() is True
        assert "nothing was read" in window.status_label.text()

    def test_the_address_comes_from_the_ref_or_the_role_fields(self, dialog):
        assert dialog(ref="IC1")._address_kwargs() == {"ref": "IC1"}
        by_role = dialog(role="FPGA", cluster="FPGA", sheet="FPGA")
        assert by_role._address_kwargs() == {"role": "FPGA", "cluster": "FPGA",
                                             "sheet": "FPGA"}


# ── OK: the plan and the write ────────────────────────────────────────────

class TestOk:
    def test_without_a_read_it_refuses_before_planning(self, dialog):
        window = dialog()
        window.accept_plan()
        assert "read the pads first" in window.status_label.text()

    def test_with_nothing_ticked_it_says_why(self, dialog):
        window = dialog()
        window.filled(_result())
        window.accept_plan()
        assert "no pad is ticked" in window.status_label.text()

    def test_a_broken_template_is_a_line_not_a_traceback(self, dialog):
        """Т2.1(а) payoff: half-typed templates are normal while typing."""
        window = dialog()
        window.filled(_result())
        _tick(window, "117")
        window.template_edit.setText("vccio_{")
        window.accept_plan()
        assert "cannot be read as a template" in window.status_label.text()

    def test_the_row_the_user_typed_a_name_for_is_written_with_that_name(self, dialog):
        dock = _Dock(_tree())
        window = dialog(dock=dock)
        window.filled(_result())
        _tick(window, "117")
        window.table.item(0, 3).setText("avdd_18")
        window.accept_plan()
        assert [node.ref for node in dock._trees[0].nodes] == ["avdd_18"]
        assert dock.dirty == 1

    def test_a_reloaded_tree_receives_the_nodes_and_the_old_one_is_untouched(self,
                                                                            dialog):
        """§23: the window holds the tree NAME, so a reload between the read and OK
        lands in the tree the dock has NOW."""
        old = _tree(_parent())
        dock = _Dock(old)
        window = dialog(dock=dock, parent_ref="PARENT")
        window.filled(_result())                     # read happened on `old`
        _tick(window, "117")
        replacement = _tree(_parent())               # the dock reloaded meanwhile
        dock._trees = [replacement]
        window.accept_plan()
        assert [node.ref for node in replacement.nodes[0].children] == ["117"]
        assert old.nodes[0].children == [], "the retained tree must not be written"

    def test_a_parent_deleted_meanwhile_writes_nothing(self, dialog):
        """§23: the plan itself refuses «nowhere to hang» for a place it cannot see,
        and the window shows it."""
        dock = _Dock(_tree(_parent()))
        window = dialog(dock=dock, parent_ref="PARENT")
        window.filled(_result())
        _tick(window, "117")
        dock._trees = [_tree()]                      # the parent is gone
        window.accept_plan()
        assert "nowhere to hang" in window.status_label.text()
        assert dock._trees[0].nodes == []
        assert dock.dirty == 0

    def test_editing_the_address_after_a_read_drops_that_read(self, dialog):
        """§24 в: the rows describe the address they were read at, so editing the
        address throws them away and OK refuses until a NEW read."""
        dock = _Dock(_tree())
        window = dialog(dock=dock, ref="IC1")
        window.filled(_result())
        _tick(window, "117")
        window.ref_edit.setText("IC2")
        assert window.rows == [] and window.instance == {} and window.occupied == {}
        assert "read the pads again" in window.status_label.text()
        window.accept_plan()
        assert "read the pads first" in window.status_label.text()
        assert dock._trees[0].nodes == []
        assert dock.dirty == 0

    def test_a_taken_pad_can_never_be_selected_even_by_force(self, dialog):
        """The window refuses a taken pad TWICE — no checkbox (Д6) and a
        `selected_pads` that filters `occupied` out — so the plan's own guard
        (`test_a_pad_that_already_carries_this_mount_is_refused` in the trees cells)
        is a second line, not the only one. Nothing is written either way."""
        dock = _Dock(_tree())
        window = dialog(dock=dock)
        window.filled(_result(occupied={"117": "mnt_117"}))
        window._checked.add("117")                   # force the tick past the greying
        assert window.selected_pads() == []
        window.accept_plan()
        assert "no pad is ticked" in window.status_label.text()
        assert dock._trees[0].nodes == []
        assert dock.dirty == 0


# ── the window that is gone before its worker comes back (§24 а) ──────────

class _LiveConn:
    """What `start_read_pads` asks of a connection: a live board, a free socket."""

    def __init__(self):
        self.is_connected = True
        self.long_op_active = False
        self.board = None


class TestTheClosedWindow:
    """The read runs on a worker, so its result may come back AFTER the user closed
    the window — and the guard widget the controller restores is gone by then too.

    Two owners, two cells: the window drops a result it can no longer show, and the
    controller (gui/worker.py) finishes its own teardown without touching the dead
    button. The second one is a REAL QThread, because the defect was a thread left
    running, which no mock can show."""

    def test_a_result_that_arrives_after_the_window_closed_is_dropped(self, qtbot,
                                                                     caplog):
        """The window is closed through Qt's own path (`WA_DeleteOnClose`), never
        `sip.delete`: a deleted TOP-LEVEL widget leaves a dangling entry in the
        application's window list, and a cell whose failure aborts the whole pytest
        process is not an instrument (found while mutating this very line)."""
        window = open_mounts_dialog(None, dock=_Dock(_tree()), connection=None, cfg=None,
                                    sheet_names={}, tree_name="fpga")
        window.close()
        qtbot.wait(50)
        assert sip.isdeleted(window) is True
        assert window._window_gone() is True
        with caplog.at_level("INFO", logger="gui.docks.trees_mounts_dialog"):
            window.filled(_result())          # must not touch a dead widget
            window._tell("boom")
        assert "arrived after the window was closed" in caplog.text
        assert "boom" in caplog.text

    def test_closing_mid_read_stops_the_thread_and_raises_nothing(self, qtbot,
                                                                  monkeypatch):
        errors = []
        monkeypatch.setattr(sys, "excepthook",
                            lambda kind, value, tb: errors.append(f"{kind.__name__}: {value}"))
        monkeypatch.setattr(trees_mounts_flow, "run_read_pads_worker",
                            lambda payload: (time.sleep(0.3), _result())[1])
        connection = _LiveConn()
        window = open_mounts_dialog(None, dock=_Dock(_tree()), connection=connection,
                                    cfg=None, sheet_names={}, tree_name="fpga",
                                    ref="IC1")
        window.read_pads()
        controller = window._active_op
        assert controller is not None, "the read never started"
        window.close()
        qtbot.wait(100)
        assert sip.isdeleted(window), "WA_DeleteOnClose must have deleted the window"
        qtbot.waitUntil(lambda: controller not in worker_mod._ACTIVE_CONTROLLERS,
                        timeout=5000)
        assert errors == [], "a closed window must not raise from the worker's signal"
        assert connection.long_op_active is False, "the shared-socket token stayed up"
