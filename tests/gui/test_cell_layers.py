# tests/gui/test_cell_layers.py
"""Э3 of plan_2026_09_12_cell_layer_dialog.md: the layer dialog and its
two-phase opening (P.3.2/P.3.3).

Offscreen Qt, no KiCad: the board is a duck-typed fake and `start_long_op` is
replaced by a recorder that runs the success callback SYNCHRONOUSLY — what is
under test here is the ORDER (token check -> worker read -> dialog -> remembered
choice -> continuation), not the QThread machinery (tests/gui/test_worker.py
owns that).

The remembered set lives in a throwaway gui_state.json
(tests/gui/conftest.py's autouse isolated_settings).
"""
from kipy.board_types import BoardLayer
from PyQt6.QtWidgets import QCheckBox, QDialog

import gui.docks.cell_layers as cell_layers_mod
from gui.board_layers import (
    CopperLayer,
    enabled_copper_layers,
    layer_choices,
    remembered_read_layers,
)
from gui.docks.cell_layers import CellLayersDialog, open_cell_layers_dialog
from kicadstamp.domain.board import Track
from kicadstamp.domain.geometry import BoardLayer as DomainLayer
from kicadstamp.domain.geometry import Vector2

F = BoardLayer.BL_F_Cu
IN1 = BoardLayer.BL_In1_Cu
IN2 = BoardLayer.BL_In2_Cu
B = BoardLayer.BL_B_Cu

_NAMES = {F: "F.Cu", IN1: "In1.Cu", IN2: "In2.Cu", B: "B.Cu"}


# ── fakes ───────────────────────────────────────────────────────────────────

class _FakeBoard:
    """Duck-typed live board (same surface gui/board_layers reads)."""

    def __init__(self, enabled=(F, IN1, IN2, B), visible=None, names=None,
                 copper_count=4):
        self.enabled = list(enabled)
        self.visible = list(enabled if visible is None else visible)
        self.names = dict(names or {})
        self.copper_count = copper_count

    def get_enabled_layers(self):
        return list(self.enabled)

    def get_visible_layers(self):
        return list(self.visible)

    def get_layer_name(self, layer):
        return self.names.get(layer, _NAMES[layer])

    def get_copper_layer_count(self):
        return self.copper_count


class _FakeAdapter:
    """The adapter surface `_fetch_copper_layers` uses: just `_board`."""

    def __init__(self, board):
        self._board = board


class _FakeConnection:
    """Only what the opener touches: the shared-socket token."""
    def __init__(self, busy=False):
        self.long_op_active = busy


def _selection_track(layer):
    """A live Track as the ~400ms selection tick distributes it."""
    return Track(uuid=f"t-{layer}", net_name="GND",
                 start=Vector2.from_xy_mm(0.0, 0.0),
                 end=Vector2.from_xy_mm(1.0, 0.0),
                 width_mm=0.25, layer=layer)


def _copper_rows(names, hidden=()):
    """CopperLayer rows the way Э1 hands them over."""
    return [CopperLayer(layer=index + 3, copper_name=name, display_name=name,
                        position=index + 1, visible=name not in hidden)
            for index, name in enumerate(names)]


def _record_start(monkeypatch, layers=None, error=None):
    """Replace start_long_op: record the call and run the outcome at once."""
    call = {}

    def _start(connection, widgets, fn, on_success, on_error, *args, **kwargs):
        call.update(connection=connection, widgets=tuple(widgets), fn=fn,
                    args=args, kwargs=kwargs)
        if error is not None:
            on_error(error)
        else:
            on_success(layers)
        return "controller"

    monkeypatch.setattr(cell_layers_mod, "start_long_op", _start)
    return call


def _record_dialog(monkeypatch, accepted=True, checked=None, touched=()):
    """Replace the widget: record the rows it was built with, answer exec()."""
    seen = {}

    class _Dialog:
        def __init__(self, rows, parent=None):
            seen["rows"] = list(rows)
            seen["parent"] = parent

        def exec(self):
            return (QDialog.DialogCode.Accepted if accepted
                    else QDialog.DialogCode.Rejected)

        def checked_names(self):
            if checked is not None:
                return list(checked)
            return [row.copper.copper_name for row in seen["rows"] if row.checked]

        def touched_names(self):
            return set(touched)

    monkeypatch.setattr(cell_layers_mod, "CellLayersDialog", _Dialog)
    return seen


def _box(dialog, display_name):
    """The checkbox of a row, found by the NAME THE USER SEES (no private API)."""
    for box in dialog.findChildren(QCheckBox):
        if box.text() == display_name:
            return box
    raise AssertionError(f"no checkbox labelled {display_name!r}")


# ── phase 1: the worker read ────────────────────────────────────────────────

def test_the_worker_function_reads_the_live_board():
    layers = cell_layers_mod._fetch_copper_layers(_FakeAdapter(_FakeBoard()))
    assert [copper.copper_name for copper in layers] == [
        "F.Cu", "In1.Cu", "In2.Cu", "B.Cu"]


# ── the token check (P.3.3) ─────────────────────────────────────────────────

def test_refused_while_another_operation_holds_the_socket(monkeypatch):
    """start_long_op does NOT refuse a held token, so the check is ours: nothing
    is started, nothing is shown, and the continuation never runs."""
    call = _record_start(monkeypatch, layers=[])
    seen = _record_dialog(monkeypatch)
    done = []

    result = open_cell_layers_dialog(
        None, _FakeConnection(busy=True), _FakeAdapter(_FakeBoard()),
        [_selection_track(DomainLayer.BL_F_Cu)], done.append)

    assert result is None
    assert call == {}
    assert seen == {}
    assert done == []


def test_no_adapter_means_nothing_is_started(monkeypatch):
    call = _record_start(monkeypatch, layers=[])
    assert open_cell_layers_dialog(None, _FakeConnection(), None, [], [].append) is None
    assert call == {}


# ── phase 1 -> phase 2 -> the read ──────────────────────────────────────────

def test_the_worker_reads_the_layers_before_the_dialog_opens(monkeypatch):
    """P.3.2: the board read happens on the worker (phase 1), and the dialog is
    built from ITS result — the dialog itself never touches the board."""
    board = _FakeBoard(visible=(F, IN2, B))          # In1.Cu hidden on the board
    adapter = _FakeAdapter(board)
    call = _record_start(monkeypatch, layers=enabled_copper_layers(board))
    seen = _record_dialog(monkeypatch)
    done = []
    selection = [_selection_track(DomainLayer.BL_F_Cu),
                 _selection_track(DomainLayer.BL_In2_Cu)]

    controller = open_cell_layers_dialog(
        None, _FakeConnection(), adapter, selection,
        lambda chosen, empty=(): done.append(chosen))

    assert controller == "controller"
    assert call["fn"] is cell_layers_mod._fetch_copper_layers
    assert call["args"] == (adapter,)
    assert [row.copper.copper_name for row in seen["rows"]] == [
        "F.Cu", "In1.Cu", "In2.Cu", "B.Cu"]
    # F.Cu and In2.Cu carry copper in the selection; In1.Cu and B.Cu come off.
    assert [(row.checked, row.empty) for row in seen["rows"]] == [
        (True, False), (False, True), (True, False), (False, True)]
    assert done == [["F.Cu", "In2.Cu"]]


def test_ok_remembers_the_manual_choice_and_continues(monkeypatch):
    """Э3's guarantee, end to end: the layer that came off only because nothing
    was on it that time is NOT lost, while the user's own "off" IS remembered."""
    board = _FakeBoard()
    adapter = _FakeAdapter(board)
    _record_start(monkeypatch, layers=enabled_copper_layers(board))
    _record_dialog(monkeypatch, checked=["F.Cu"], touched={"B.Cu"})
    done = []

    open_cell_layers_dialog(
        None, _FakeConnection(), adapter,
        [_selection_track(DomainLayer.BL_F_Cu)],
        lambda chosen, empty=(): done.append(chosen))

    assert done == [["F.Cu"]]                        # the read runs with what is on
    assert remembered_read_layers() == ["F.Cu", "In1.Cu", "In2.Cu"]


def test_ok_hands_the_skipped_empty_layers_to_the_read(monkeypatch):
    """The read gets BOTH halves: the layer set it may look at, and the names the
    dialog left off as EMPTY — which only the dialog knows, because a layer with
    no copper in the selection leaves no trace there (Э5's Log report)."""
    board = _FakeBoard()
    _record_start(monkeypatch, layers=enabled_copper_layers(board))
    _record_dialog(monkeypatch, checked=["F.Cu"])
    got = []

    open_cell_layers_dialog(
        None, _FakeConnection(), _FakeAdapter(board),
        [_selection_track(DomainLayer.BL_F_Cu)],
        lambda chosen, empty: got.append((chosen, empty)))

    assert got == [(["F.Cu"], ["In1.Cu", "In2.Cu", "B.Cu"])]


def test_cancel_remembers_nothing_and_starts_nothing(monkeypatch):
    board = _FakeBoard()
    _record_start(monkeypatch, layers=enabled_copper_layers(board))
    _record_dialog(monkeypatch, accepted=False)
    done = []

    open_cell_layers_dialog(None, _FakeConnection(), _FakeAdapter(board),
                            [_selection_track(DomainLayer.BL_F_Cu)], done.append)

    assert done == []
    assert remembered_read_layers() is None


def test_a_remembered_set_is_the_starting_point_of_the_next_dialog(monkeypatch):
    """Second run: the layer the user took off BY HAND comes back off even
    though it carries copper, and every other layer comes back on."""
    board = _FakeBoard()
    _record_start(monkeypatch, layers=enabled_copper_layers(board))
    _record_dialog(monkeypatch, checked=["F.Cu", "In1.Cu", "In2.Cu"],
                   touched={"B.Cu"})
    selection = [_selection_track(DomainLayer.BL_F_Cu),
                 _selection_track(DomainLayer.BL_In1_Cu),
                 _selection_track(DomainLayer.BL_In2_Cu),
                 _selection_track(DomainLayer.BL_B_Cu)]

    # First run: copper on EVERY layer, so nothing comes off by itself — only the
    # user's own "B.Cu off" is a decision, and only it is remembered.
    open_cell_layers_dialog(None, _FakeConnection(), _FakeAdapter(board),
                            selection, lambda *args: None)
    assert remembered_read_layers() == ["F.Cu", "In1.Cu", "In2.Cu"]

    # Second run, same selection: B.Cu opens OFF although it has copper.
    rows = layer_choices(enabled_copper_layers(board), remembered_read_layers(),
                         {"F.Cu", "In1.Cu", "In2.Cu", "B.Cu"})
    assert [(row.copper.copper_name, row.checked) for row in rows] == [
        ("F.Cu", True), ("In1.Cu", True), ("In2.Cu", True), ("B.Cu", False)]


def test_phase_1_failure_is_handed_to_the_error_handler(monkeypatch):
    board = _FakeBoard()
    _record_start(monkeypatch, error="KiCad is busy")
    seen = _record_dialog(monkeypatch)
    failures = []

    open_cell_layers_dialog(None, _FakeConnection(), _FakeAdapter(board), [],
                            [].append, on_error=failures.append)

    assert failures == ["KiCad is busy"]
    assert seen == {}                                # no dialog on a failed read


# ── the widget itself ───────────────────────────────────────────────────────

def test_dialog_opens_with_the_rows_state_and_reports_the_final_checked(qapp):
    rows = layer_choices(_copper_rows(("F.Cu", "In1.Cu")), None, {"F.Cu"})
    dialog = CellLayersDialog(rows)

    assert dialog.checked_names() == ["F.Cu"]
    # The opening state (built by layer_choices) is not a user's touch.
    assert dialog.touched_names() == set()

    _box(dialog, "In1.Cu").setChecked(True)
    assert dialog.touched_names() == {"In1.Cu"}
    assert dialog.checked_names() == ["F.Cu", "In1.Cu"]

    _box(dialog, "In1.Cu").setChecked(False)
    assert dialog.checked_names() == ["F.Cu"]


def test_a_row_says_why_it_is_off(qapp):
    rows = layer_choices(_copper_rows(("F.Cu", "In1.Cu", "B.Cu"),
                                      hidden=("In1.Cu",)), None, {"F.Cu"})
    dialog = CellLayersDialog(rows)

    assert dialog.note_for("F.Cu") == ""
    assert dialog.note_for("In1.Cu") == "empty in the selection · hidden on the board"
    assert dialog.note_for("B.Cu") == "empty in the selection"


def test_a_hidden_layer_is_offered_not_filtered_out(qapp):
    """Э2: we warn, we never work around the user's own visibility setting."""
    rows = layer_choices(_copper_rows(("F.Cu", "In1.Cu"), hidden=("In1.Cu",)),
                         None, {"F.Cu", "In1.Cu"})
    dialog = CellLayersDialog(rows)

    assert dialog.checked_names() == ["F.Cu", "In1.Cu"]
    assert dialog.note_for("In1.Cu") == "hidden on the board"


def test_layers_use_the_users_own_names(qapp):
    rows = [CopperLayer(layer=4, copper_name="In1.Cu", display_name="GND",
                        position=2, visible=True)]
    dialog = CellLayersDialog(layer_choices(rows, None, {"In1.Cu"}))

    assert dialog.checked_names() == ["In1.Cu"]      # the record vocabulary ...
    assert _box(dialog, "GND").isChecked()           # ... under the user's name
