# tests/gui/test_cell_anchor_role_clobber.py
"""Guards for the CellDock ``anchor_role`` clobber
(plan_2026_10_05_celldock_anchor_role_clobber; Claude, 2026-10-05).

Two layers:

* the PURE decision half ``gui/docks/cell_form_guard.py`` — driven directly;
* the FORM — loading a cell must not silently swap its stored ``anchor_role``
  for the first role, and an untouched picker must never become the source of
  truth on save.
"""
import logging

from kicadstamp.config.sexp_format import dict_to_sexp
from gui.docks.cell_editor import CellDock
from gui.docks.cell_form_guard import anchor_role_selection, effective_anchor_role


def _slot(role):
    return {"role": role, "offset_along_mm": 0.0, "offset_across_mm": 0.0,
            "angle_deg": 0.0}


def _write_cells(root, cells):
    root.write_text(dict_to_sexp({"cells": cells}, format_number=2),
                    encoding="utf-8")


def _dock(main_window, tmp_path, cells):
    root = tmp_path / "root.sexp"
    _write_cells(root, cells)
    dock = CellDock(main_window)
    dock.set_root_path(root)
    return dock, root


A_AND_B = {
    "A": {"layer": "F.Cu", "components": [_slot("R1"), _slot("R2")]},
    "B": {"layer": "F.Cu", "anchor_role": "ZZ",
          "components": [_slot("AA"), _slot("ZZ")]},
}


# ── the pure decision half ──────────────────────────────────────────────────

def test_anchor_role_selection():
    assert anchor_role_selection(["AA", "ZZ"], "ZZ") == ("ZZ", None)
    assert anchor_role_selection(["AA", "ZZ"], "") == ("", None)
    assert anchor_role_selection(["AA", "ZZ"], None) == ("", None)
    value, warning = anchor_role_selection(["AA", "ZZ"], "GONE")
    assert value == "" and warning and "GONE" in warning


def test_effective_anchor_role_invariant():
    # "none" mode -> nothing is written, whatever the picker shows
    assert effective_anchor_role("none", "ZZ", "FPGA", True) is None
    # untouched -> the LOADED value wins, verbatim
    assert effective_anchor_role("role", "AA", "FPGA", False) == "FPGA"
    # touched -> the user's pick is the answer
    assert effective_anchor_role("role", "AA", "FPGA", True) == "AA"
    # touched but emptied -> None
    assert effective_anchor_role("role", "", "FPGA", True) is None


# ── guard 1: cell A, then cell B — the saved role survives ──────────────────

def test_open_a_then_b_keeps_bs_anchor_role(main_window, tmp_path):
    """G1: A (roles R1/R2) then B (AA/ZZ, anchor_role ZZ). The picker must land
    on ZZ and a Save must write ZZ — NOT the alphabetically first AA."""
    dock, root = _dock(main_window, tmp_path, A_AND_B)
    dock.load_entry("A", root)
    dock.load_entry("B", root)

    assert dock.anchor_role_combo.currentText() == "ZZ"
    built = dock._build_cell_dict()
    assert built is not None
    assert built[1]["anchor_role"] == "ZZ"


# ── guard 2: B opened FIRST (empty list before the load) ─────────────────────

def test_b_first_with_empty_list_keeps_zz(main_window, tmp_path):
    """G2: same, but B is the very first load — the combo was empty before, so
    the old bug had nothing to fall back to and still auto-picked AA."""
    dock, root = _dock(main_window, tmp_path, A_AND_B)
    dock.load_entry("B", root)

    assert dock.anchor_role_combo.currentText() == "ZZ"
    built = dock._build_cell_dict()
    assert built is not None and built[1]["anchor_role"] == "ZZ"


# ── guard 3: a stored role that is NOT among the roles is kept + warned ─────

def test_unknown_anchor_role_is_kept_and_warned(main_window, tmp_path, caplog):
    """G3: anchor_role 'GONE' is not a role of the cell -> it is NOT silently
    replaced by the first role; the record keeps GONE and a warning is shown."""
    dock, root = _dock(main_window, tmp_path, {
        "C": {"layer": "F.Cu", "anchor_role": "GONE",
              "components": [_slot("AA"), _slot("ZZ")]}})
    with caplog.at_level(logging.INFO):
        dock.load_entry("C", root)

    assert dock._loaded_anchor_role == "GONE"
    assert dock._anchor_role_touched is False
    assert any("is not a role of this cell" in r.message for r in caplog.records)
    # untouched -> the LOADED role is what the record keeps
    assert effective_anchor_role(
        "role", dock.anchor_role_combo.currentText(),
        dock._loaded_anchor_role, dock._anchor_role_touched) == "GONE"


# ── guard 4: an explicit user pick still wins ───────────────────────────────

def test_user_pick_is_written(main_window, tmp_path):
    """G4: the invariant never blocks an edit — picking AA writes AA."""
    dock, root = _dock(main_window, tmp_path, A_AND_B)
    dock.load_entry("B", root)

    dock.anchor_role_combo.setCurrentText("AA")

    assert dock._anchor_role_touched is True
    built = dock._build_cell_dict()
    assert built is not None and built[1]["anchor_role"] == "AA"


# ── guard 5: a cell without anchor_role writes none ─────────────────────────

def test_cell_without_anchor_role_writes_none(main_window, tmp_path):
    dock, root = _dock(main_window, tmp_path, {
        "D": {"layer": "F.Cu", "components": [_slot("AA")]}})
    dock.load_entry("D", root)

    assert dock.anchor_mode_combo.currentData() == "none"
    built = dock._build_cell_dict()
    assert built is not None and "anchor_role" not in built[1]
