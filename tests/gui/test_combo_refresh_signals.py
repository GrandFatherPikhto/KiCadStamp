# tests/gui/test_combo_refresh_signals.py
"""Regression tests for the 2026-09-12 GUI freeze on ``addItems``
(techdocs/handoff/deepseek/plan/plan_2026_09_12_combo_refresh_deadlock.md,
analysis with full py-spy/gdb stacks in
techdocs/handoff/claude/note_2026_09_12_gui_freeze_addItems_deadlock.md).

Measured chain of the freeze::

    set_combo_items -> QComboBox::insertItems -> rowsInserted
      -> QComboBox::setCurrentIndex -> QWidgetLineControl::internalSetText
      -> QLineEdit::textChanged            <- NOT covered by combo.blockSignals
      -> PyQt slot proxy -> AnchorOriginWidget._emit_field_changed
      -> QObject::sender()                 <- signal mutex already held
      -> futex, forever (Qt mutexes are not recursive)

Two individually harmless assumptions combined: ``blockSignals`` on the COMBO
never covered the combo's internal line edit, and ``_wire_field_changed``
subscribed to that internal line edit through ``findChildren(QLineEdit)``.

The tests below pin all three layers of the fix:
  * the internal line edit is silenced during a repopulation (E2),
  * nothing of ours is subscribed to a combo's internals (E3),
  * an unchanged list does not touch the model at all (E1),
  * the widget's OWN line edits keep signalling (E3 must not over-cut),
  * typed text still survives both the early exit and a real repopulation.

GUI tests hang without a timeout — run them with one, e.g.
``timeout 300 .venv/bin/python -m pytest tests/gui/test_combo_refresh_signals.py``.
"""
from typing import List

from PyQt6.QtWidgets import QComboBox, QLineEdit

from gui.docks._anchor_origin import AnchorOriginWidget
from gui.docks._common import configure_searchable, set_combo_items


def _items(combo: QComboBox) -> List[str]:
    return [combo.itemText(i) for i in range(combo.count())]


def _rows_inserted_counter(combo: QComboBox) -> List[int]:
    """(counter, connect) — the counter list is appended to by the model's
    rowsInserted, i.e. by a REAL repopulation (the early exit must leave it
    at zero). The slot deliberately does not call sender()."""
    calls: List[int] = []
    combo.model().rowsInserted.connect(lambda *_args: calls.append(1))
    return calls


# ── E1: early exit when the list did not change ─────────────────────────

def test_set_combo_items_early_exit_does_not_touch_the_model(qapp):
    combo = QComboBox()
    configure_searchable(combo)
    set_combo_items(combo, ["A", "B"])
    combo.setCurrentText("B")  # the user picked B
    calls = _rows_inserted_counter(combo)

    set_combo_items(combo, ["A", "B"])

    assert calls == [], "an identical list must not repopulate the combo (E1)"
    assert _items(combo) == ["A", "B"]
    assert combo.currentText() == "B"


def test_set_combo_items_early_exit_is_order_sensitive(qapp):
    """The order inside a combo is meaningful (callers pass sorted lists), so
    a reordered list is a REAL change and must repopulate."""
    combo = QComboBox()
    configure_searchable(combo)
    set_combo_items(combo, ["A", "B"])
    calls = _rows_inserted_counter(combo)

    set_combo_items(combo, ["B", "A"])

    assert calls, "a reordered list is a change and must repopulate"
    assert _items(combo) == ["B", "A"]


def test_set_combo_items_real_change_still_repopulates(qapp):
    """The guard against the early exit being 'always exit'."""
    combo = QComboBox()
    configure_searchable(combo)
    set_combo_items(combo, ["A", "B"])
    calls = _rows_inserted_counter(combo)

    set_combo_items(combo, ["A", "B", "C"])

    assert calls, "a genuinely changed list must still reach the model"
    assert _items(combo) == ["A", "B", "C"]


def test_typed_text_survives_an_unchanged_list(qapp):
    """E1 caveat: the early exit must not drop an in-progress typed value.
    The items are unchanged, so the text is not touched at all."""
    combo = QComboBox()
    configure_searchable(combo)
    set_combo_items(combo, ["A", "B"])
    combo.setCurrentText("C_Out_Bulk")

    set_combo_items(combo, ["A", "B"])

    assert combo.currentText() == "C_Out_Bulk"


# ── E2: the combo's INTERNAL line edit is what emits, so silence it ──────

def test_repopulation_emits_no_internal_line_edit_change(qapp):
    """The exact emitter from the gdb stack: the combo's internal QLineEdit,
    which combo.blockSignals() never covered."""
    combo = QComboBox()
    configure_searchable(combo)
    set_combo_items(combo, ["A", "B"])
    edit = combo.lineEdit()
    assert edit is not None  # searchable combos are editable by design
    seen: List[str] = []
    edit.textChanged.connect(seen.append)

    set_combo_items(combo, ["X", "Y"])

    assert seen == [], "the internal line edit must be blocked during refresh (E2)"
    assert _items(combo) == ["X", "Y"]


def test_repopulation_emits_no_combo_signal(qapp):
    """The pre-existing protection, kept asserted: the combo's own signals
    stay silent across a repopulation."""
    combo = QComboBox()
    configure_searchable(combo)
    set_combo_items(combo, ["A", "B"])
    seen: List[str] = []
    combo.currentTextChanged.connect(seen.append)

    set_combo_items(combo, ["X", "Y"])

    assert seen == []


def test_repopulation_restores_the_internal_line_edit_block_state(qapp):
    """E2 caveat: restore the PREVIOUS block state instead of blindly setting
    False — the caller may already have blocked the combo."""
    combo = QComboBox()
    configure_searchable(combo)
    set_combo_items(combo, ["A", "B"])
    edit = combo.lineEdit()
    assert edit.signalsBlocked() is False

    combo.blockSignals(True)
    set_combo_items(combo, ["X", "Y"])
    assert combo.signalsBlocked() is True, "a caller's block must survive"
    assert edit.signalsBlocked() is False, "the line edit's own state is restored"

    combo.blockSignals(False)
    set_combo_items(combo, ["P", "Q"])
    assert combo.signalsBlocked() is False
    assert edit.signalsBlocked() is False


def test_non_editable_combo_has_no_line_edit(qapp):
    """combo.lineEdit() is None for a non-editable combo — the fix must not
    dereference it."""
    combo = QComboBox()
    combo.addItems(["A", "B"])
    assert combo.lineEdit() is None

    set_combo_items(combo, ["A", "B", "C"])

    assert _items(combo) == ["A", "B", "C"]


# ── E2+E3: the widget-level regression the freeze was caught on ──────────

def test_set_known_roles_emits_no_field_changed(qapp):
    """E4 test 2 — THE regression test for the freeze: refreshing the live
    Role/Cluster candidates must not fire the widget's fieldChanged, not even
    on a changed list. FAILS on the unfixed code (measured: 4 emissions, one
    per combo signal, i.e. exactly the nested emission that deadlocked)."""
    widget = AnchorOriginWidget(modes=["anchor"], anchor_fields=["cluster"])
    widget.set_known_roles(["A", "B"], ["c1"])  # first real load
    fired: List[int] = []
    widget.fieldChanged.connect(lambda: fired.append(1))

    widget.set_known_roles(["A", "B", "C"], ["c2"])

    assert fired == [], (
        "a programmatic known-lists refresh leaked out of fieldChanged; this "
        "is the nested emission of the addItems freeze (E2+E3)"
    )
    assert _items(widget.anchor_role_edit) == ["A", "B", "C"]
    assert _items(widget.anchor_cluster_edit) == ["c2"]


def test_set_known_roles_identical_list_emits_no_field_changed(qapp):
    """E1 at the widget level: the ~2s poll re-pushes unchanged lists."""
    widget = AnchorOriginWidget(modes=["anchor"], anchor_fields=["cluster"])
    widget.set_known_roles(["A", "B"], ["c1"])
    fired: List[int] = []
    widget.fieldChanged.connect(lambda: fired.append(1))

    widget.set_known_roles(["A", "B"], ["c1"])

    assert fired == []


def test_combo_internal_line_edit_is_not_wired_to_field_changed(qapp):
    """E3 directly: the widget's own line edits are wired to fieldChanged, the
    line edits OWNED by its combos are not — the combo's own
    currentTextChanged (the loop right below in _wire_field_changed) already
    reports the same user event, and the duplicate is the one that fires from
    inside Qt's record insertion.

    Isolated from E2 by blocking the combo ourselves: with the combo's own
    signals suppressed, only a DIRECT edit-to-fieldChanged connection could
    still fire here."""
    widget = AnchorOriginWidget(modes=["anchor"], anchor_fields=["cluster"])
    widget.set_known_roles(["A", "B"], ["c1"])
    combo = widget.anchor_role_edit
    edit = combo.lineEdit()
    assert edit is not None, "searchable combos are editable by design"
    fired: List[int] = []
    widget.fieldChanged.connect(lambda: fired.append(1))

    combo.blockSignals(True)
    edit.setText("X_not_in_the_list")
    combo.blockSignals(False)

    assert fired == [], (
        "a combo's internal line edit is subscribed to fieldChanged (E3)"
    )
    # The widget really does have its own, WIRED line edits (see the two
    # positive-control tests below).
    assert isinstance(widget.anchor_ref_edit, QLineEdit)


# ── E3 caveat: the widget's own line edits keep working ─────────────────

def test_own_line_edit_still_emits_field_changed(qapp):
    """E4 test 3 — the guard against E3 over-cutting: a genuine, non-combo
    QLineEdit of the widget must still raise fieldChanged."""
    widget = AnchorOriginWidget(modes=["anchor"], anchor_fields=["cluster"])
    fired: List[int] = []
    widget.fieldChanged.connect(lambda: fired.append(1))

    widget.anchor_ref_edit.setText("R12")

    assert fired, "the widget's own QLineEdit stopped signalling (E3 over-cut)"


def test_own_cartesian_line_edit_still_emits_field_changed(qapp):
    widget = AnchorOriginWidget(modes=["xy"], shift=True)
    fired: List[int] = []
    widget.fieldChanged.connect(lambda: fired.append(1))

    widget.x_edit.setText("12.5")
    widget.shift_y_edit.setText("-3")

    assert len(fired) == 2


def test_own_combo_still_emits_field_changed(qapp):
    """The anchor Role/Cluster combos are real user-facing controls: their
    currentTextChanged must keep reaching fieldChanged (only the INTERNAL line
    edit of a combo is skipped, never the combo itself)."""
    widget = AnchorOriginWidget(modes=["anchor"], anchor_fields=["cluster"])
    widget.set_known_roles(["A", "B"], ["c1"])
    fired: List[int] = []
    widget.fieldChanged.connect(lambda: fired.append(1))

    widget.anchor_role_edit.setCurrentIndex(1)

    assert fired, "the combo's own currentTextChanged stopped signalling"


# ── E4 test 4: typed text survives a real refresh ───────────────────────

def test_typed_text_survives_a_real_repopulation(qapp):
    combo = QComboBox()
    configure_searchable(combo)
    set_combo_items(combo, ["A", "B"])
    combo.setCurrentText("C_Out_Bulk")

    set_combo_items(combo, ["A", "B", "C"])

    assert combo.currentText() == "C_Out_Bulk"
    assert _items(combo) == ["A", "B", "C"]


def test_typed_text_survives_set_known_roles(qapp):
    widget = AnchorOriginWidget(modes=["anchor"], anchor_fields=["cluster"])
    widget.set_known_roles(["A", "B"], ["c1"])
    widget.anchor_role_edit.setCurrentText("C_Out_Bulk")

    widget.set_known_roles(["A", "B", "C"], ["c1"])

    assert widget.anchor_role_edit.currentText() == "C_Out_Bulk"
    assert _items(widget.anchor_role_edit) == ["A", "B", "C"]
