# tests/gui/docks/test_trees_owner_place.py
"""The anchor form of an EMBEDDED tree: locked, and showing its module's place (п. 7).

plan_2026_11_15_embedded_tree_redraw_through_module, cells к1 / к2 and the two sentences Denis
gave on 15.11: «пусть берёт координаты из дерева, куда оно встроено, а позиции якоря дерева
блокируются для ввода» and «только показ, в файл не пишется».

`main` places `power` by its marker at xy (6, −64), rotation 30; `power`'s own anchor is
`Origin` + (5, −60) and `solo` is placed by nobody.
"""
import ast
from pathlib import Path

import pytest

from kicadstamp.config.sexp_format import dict_to_sexp
from kicadstamp.trees import Tree, TreeAnchor, TreeNode

import gui.docks.trees_owner_place as op
from gui.docks.trees_dock import AnchorFormWidget, TreesDock

from tests.fakes.explore_board import FakeExploreBoard
from tests.fakes.write_later import write_later

TREES = {
    "trees": [
        {"name": "main", "anchor": {"origin": True},
         "nodes": [{"ref": "power", "kind": "module", "xy": [6.0, -64.0], "rotation": 30.0}]},
        {"name": "power", "anchor": {"origin": True, "shift": [5.0, -60.0]},
         "nodes": [{"ref": "PWR_R1", "kind": "placement", "xy": [1.0, 2.0]}]},
        {"name": "solo", "anchor": {"origin": True},
         "nodes": [{"ref": "R_SOLO", "kind": "placement", "xy": [100.0, 50.0]}]},
    ],
    "cells": {"cell_pwr": {}},
    "entities": [
        {"name": "PWR_R1", "cluster": "PWR_R1", "cell": "cell_pwr"},
        {"name": "R_SOLO", "cluster": "R_SOLO", "cell": "cell_pwr"},
    ],
}


def tree(name, nodes, *, shift=(0.0, 0.0), rotation=0.0):
    return Tree(name=name, anchor=TreeAnchor(point="Origin", shift_xy=shift),
                nodes=list(nodes), rotation=rotation)


def node(ref, kind="placement", xy=(0.0, 0.0), rotation=0.0):
    return TreeNode(ref=ref, kind=kind, xy=xy, polar=None, rotation=rotation,
                    name=None, group=None, children=[])


def by_name(trees, name):
    return next(t for t in trees if t.name == name)


def _synthetic():
    return [tree("main", [node("power", kind="module", xy=(6.0, -64.0), rotation=30.0)]),
            tree("power", [node("PWR_R1", xy=(1.0, 2.0))], shift=(5.0, -60.0)),
            tree("solo", [node("R_SOLO", xy=(100.0, 50.0))])]


# ── the rule: who places me, and with what numbers ─────────────────────────

def test_a_standalone_tree_has_no_owner_place():
    trees = _synthetic()
    assert op.owner_place(trees, by_name(trees, "solo")) is None


def test_the_place_is_the_MARKERs_xy_and_rotation_in_its_owner():
    trees = _synthetic()
    place = op.owner_place(trees, by_name(trees, "power"))
    assert place.owner == "main" and place.marker == "power"
    assert place.marker_xy == (6.0, -64.0)
    assert place.marker_rotation == 30.0
    assert place.ambiguous is False


def test_two_markers_are_ambiguous():
    trees = _synthetic() + [tree("aux", [node("power", kind="module", xy=(-5.0, 5.0))])]
    place = op.owner_place(trees, by_name(trees, "power"))
    assert place.ambiguous is True
    assert {p.owner for p in place.markers} == {"main", "aux"}


# ── the line ──────────────────────────────────────────────────────────────

def test_the_line_names_the_module_the_owner_and_the_numbers():
    trees = _synthetic()
    line = op.owner_place_line(op.owner_place(trees, by_name(trees, "power")))
    assert "placed by module 'power' in 'main'" in line
    assert "(6.000, -64.000)" in line and "30.0" in line


def test_the_line_of_a_two_marker_tree_names_every_marker():
    trees = _synthetic() + [tree("aux", [node("power", kind="module", xy=(-5.0, 5.0))])]
    line = op.owner_place_line(op.owner_place(trees, by_name(trees, "power")))
    assert "'power' in 'main'" in line and "'power' in 'aux'" in line


# ── the form (cells к1 / к2) ───────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _qt_app(qapp):
    return qapp


@pytest.fixture(autouse=True)
def _active_graph_root(tmp_path):
    from kicadstamp.config_working_set import set_active_graph_root

    set_active_graph_root(tmp_path / "active_root.sexp")
    yield
    set_active_graph_root(None)


def _dock(main_window, tmp_path, data=None):
    root = tmp_path / "owner_place.sexp"
    write_later(root, dict_to_sexp(data if data is not None else TREES, format_number=2))
    dock = TreesDock(main_window)
    dock.set_root_file(root)
    assert dock._cfg is not None, "the throwaway config failed to load — see the Log line"
    main_window.connection.board = FakeExploreBoard()
    return dock, root


def _form(dock, name):
    return dock._build_anchor_form(next(t for t in dock._trees if t.name == name))


def test_k1_an_embedded_trees_form_is_locked_and_shows_the_marker(
        main_window, tmp_path):
    """Cell к1: every anchor field is read-only, and the ONE line carries the marker's xy and
    rotation in the owner — where the user really edits them."""
    dock, _root = _dock(main_window, tmp_path)
    form = _form(dock, "power")

    for widget in (form.mode_combo, form.record_row, form.role_row, form.point_row,
                   form.self_row, form.shift_x_edit, form.shift_y_edit,
                   form.read_anchor_button, form.pivot_mode_combo, form.rotation_edit):
        assert not widget.isEnabled(), f"{widget} must be read-only on an embedded tree"

    line = form._owner_place_label.text()
    assert "placed by module 'power' in 'main'" in line
    assert "(6.000, -64.000)" in line and "30.0" in line


def test_k1_the_locked_form_writes_NOTHING_and_the_anchor_survives(main_window, tmp_path):
    """Cell к1, second half: «только показ, в файл не пишется». `apply()` refuses, the tree's
    anchor keeps its stored numbers, and the FILE is byte for byte the same — the round trip the
    plan asks for."""
    dock, root = _dock(main_window, tmp_path)
    before = root.read_text(encoding="utf-8")
    power = next(t for t in dock._trees if t.name == "power")
    anchor_before = (power.anchor.point, power.anchor.shift_xy, power.rotation)

    form = _form(dock, "power")
    assert form.apply() is False
    assert "placed by module 'power' in 'main'" in form.apply_status_label.text()

    assert (power.anchor.point, power.anchor.shift_xy, power.rotation) == anchor_before
    assert root.read_text(encoding="utf-8") == before


def test_k2_editing_the_marker_in_the_owner_changes_the_line_without_a_save(
        main_window, tmp_path):
    """Cell к2: the line follows the BUFFER. Move the marker in `main` (the same in-memory edit
    the node form makes), rebuild the tabs, and the `power` tab shows the new numbers — no Save,
    no board."""
    dock, _root = _dock(main_window, tmp_path)
    marker = next(t for t in dock._trees if t.name == "main").nodes[0]
    marker.xy = (7.5, -70.25)
    marker.rotation = 90.0
    dock._mark_dirty()
    dock._rebuild_tabs()

    line = _form(dock, "power")._owner_place_label.text()
    assert "(7.500, -70.250)" in line and "90.0" in line


def test_a_plain_tree_form_stays_editable(main_window, tmp_path):
    dock, _root = _dock(main_window, tmp_path)
    form = _form(dock, "solo")
    assert form.mode_combo.isEnabled()
    assert form.shift_x_edit.isEnabled() or form.shift_reason_label.isVisible()
    assert not form._owner_place_label.isVisible()


def test_the_marker_gone_unlocks_the_form_again(main_window, tmp_path):
    """The reverse direction of п. 7: when the last marker is removed the form opens up and the
    tree stands from its own anchor again (Р4 then writes the place into that anchor ONCE)."""
    dock, _root = _dock(main_window, tmp_path)
    assert not _form(dock, "power").mode_combo.isEnabled()

    main = next(t for t in dock._trees if t.name == "main")
    main.nodes = []
    dock._mark_dirty()
    dock._rebuild_tabs()

    form = _form(dock, "power")
    assert form.mode_combo.isEnabled(), "no marker — the tree owns its anchor again"
    assert not form._owner_place_label.isVisible()
    assert form.apply() is True


def test_unlocking_keeps_the_forms_OWN_reason_for_a_greyed_shift(main_window, tmp_path):
    """The lock must not hand back a field the FORM itself greys out («the selected anchor does
    not resolve on the live board»): re-enabling everything on the way out would make that field
    editable with numbers that mean something else. The composition of the two rules is pinned
    here because it broke once during this very step."""
    dock, _root = _dock(main_window, tmp_path)
    form = _form(dock, "power")
    form._owner_place = op.OwnerPlace(tree="power", markers=[
        op.Placement(tree="power", owner="main", marker="power")], marker_xy=(6.0, -64.0),
        marker_rotation=0.0)
    # Simulate "the tree no longer resolves live" while the lock is released.
    form._tree = None
    op.lock_anchor_form(form, [])
    assert form._owner_place_label.text() == ""


# ── one rule, one place ───────────────────────────────────────────────────

def test_the_module_holds_no_dialog_and_no_file_write():
    source = Path(op.__file__).read_text(encoding="utf-8")
    parsed = ast.parse(source)
    names = {n.id for n in ast.walk(parsed) if isinstance(n, ast.Name)}
    attrs = {n.attr for n in ast.walk(parsed) if isinstance(n, ast.Attribute)}
    assert "QMessageBox" not in names | attrs
    assert "write" not in attrs, "display only — this path writes neither the file nor the tree"
