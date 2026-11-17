# tests/gui/docks/test_redraw_drift.py
"""Cells н, о, п, р of plan_2026_11_16_reread_forest_from_board, commit 2 — the
Redraw move guard.

The contract: a Redraw that would shift parts by more than `DRIFT_MM` (0.1 mm) ASKS
first, «Cancel» costs zero transactions, «Redraw anyway» runs exactly as before, and
ALL FOUR GUI doors ask through the ONE function (`redraw_drift.confirm_redraw_drift`).

The board is faked at the two seams the measurement really uses — the layout
(`layout_tree_from_anchor`) and the live pose (`child_live_pose`) — so every cell
states its geometry explicitly and no Qt widget is constructed.
"""
import ast
from pathlib import Path
from types import SimpleNamespace

import gui.docks.redraw_drift as drift_mod
from gui.docks.redraw_drift import (confirm_redraw_drift, copper_records_in,
                                    measure_drift)
from kicadstamp.domain.geometry import Vector2
from kicadstamp.trees import Tree, TreeAnchor, TreeNode

REPO = Path(__file__).resolve().parents[3]


def _node(ref, kind="placement", *, xy=(0.0, 0.0), children=()):
    return TreeNode(ref=ref, kind=kind, xy=xy, polar=None, rotation=0.0,
                    name=None, group=None, children=list(children))


def _tree(nodes):
    return Tree(name="main", anchor=TreeAnchor(is_origin=True), nodes=list(nodes))


def _dock(tree, *, adapter=object()):
    dock = SimpleNamespace(
        _cfg=SimpleNamespace(trees=[tree]), _trees=[tree],
        _ctx=SimpleNamespace(sheet_names={}), _active_op=None,
        _root_path="/tmp/root", _main_window=SimpleNamespace(connection=None))
    dock._live_adapter = lambda: adapter
    return dock


def _wire(monkeypatch, *, predicted_mm, live_mm):
    """The two seams of one measurement: where the layout puts it, where it stands."""
    monkeypatch.setattr(
        drift_mod, "layout_tree_from_anchor",
        lambda *a, **k: {ref: (Vector2.from_xy_mm(x, y), 0.0)
                         for ref, (x, y) in predicted_mm.items()})
    monkeypatch.setattr(
        drift_mod, "child_live_pose",
        lambda cfg, adapter, sheets, ref, kind, **k:
            (Vector2.from_xy_mm(*live_mm[ref]), 0.0, False))


# ── о: a move below the threshold is not a question ───────────────────────

def test_o_a_three_micrometre_move_does_not_ask(monkeypatch):
    tree = _tree([_node("u1")])
    _wire(monkeypatch, predicted_mm={"u1": (0.0, 0.0)},
          live_mm={"u1": (0.003, 0.0)})
    asked = []
    monkeypatch.setattr(drift_mod, "_ask", lambda *a: asked.append(a) or True)

    assert confirm_redraw_drift(_dock(tree), tree, {"u1"}) is True
    assert asked == []


def test_measure_drift_reports_nothing_below_the_threshold(monkeypatch):
    tree = _tree([_node("u1"), _node("u2")])
    _wire(monkeypatch, predicted_mm={"u1": (0.0, 0.0), "u2": (0.0, 0.0)},
          live_mm={"u1": (0.05, 0.0), "u2": (0.0, 0.0)})

    assert measure_drift(object(), SimpleNamespace(trees=[tree]), tree,
                         {"u1", "u2"}, {}) == []


# ── н: 3.08 mm asks, and Cancel writes nothing ────────────────────────────

def test_n_a_three_millimetre_move_asks_and_cancel_writes_nothing(monkeypatch):
    """н: the dialog is the REAL one (`QMessageBox.question`), not a stubbed `_ask` —
    otherwise a mutation that makes «Cancel» return True would kill nothing."""
    tree = _tree([_node("u1")])
    _wire(monkeypatch, predicted_mm={"u1": (0.0, 0.0)},
          live_mm={"u1": (3.08, 0.0)})
    asked = []
    monkeypatch.setattr(
        drift_mod.QMessageBox, "question",
        lambda *a, **k: asked.append(a) or
        drift_mod.QMessageBox.StandardButton.Cancel)

    assert confirm_redraw_drift(_dock(tree), tree, {"u1"}) is False

    assert asked, "the guard must ASK before a 3.08 mm move"
    text = asked[0][2]
    assert "3.080" in text and "u1" in text


def test_the_biggest_move_comes_first(monkeypatch):
    tree = _tree([_node("u1"), _node("u2")])
    _wire(monkeypatch, predicted_mm={"u1": (0.0, 0.0), "u2": (0.0, 0.0)},
          live_mm={"u1": (0.2, 0.0), "u2": (1.5, 0.0)})

    rows = measure_drift(object(), SimpleNamespace(trees=[tree]), tree,
                         {"u1", "u2"}, {})

    assert [row.ref for row in rows] == ["u2", "u1"]
    assert abs(rows[0].delta_mm - 1.5) < 1e-9


def test_a_part_the_layout_does_not_place_is_not_counted(monkeypatch):
    """A mount or a copper record has no layout pose — the guard names parts that
    WILL be moved, and a guess is not a number."""
    tree = _tree([_node("mnt", "mount"), _node("c1", "net_trace")])
    _wire(monkeypatch, predicted_mm={}, live_mm={"mnt": (5.0, 0.0),
                                                 "c1": (5.0, 0.0)})

    assert measure_drift(object(), SimpleNamespace(trees=[tree]), tree,
                         {"mnt", "c1"}, {}) == []


# ── п: «Redraw anyway» lets it through ────────────────────────────────────

def test_p_anyway_lets_the_run_go(monkeypatch):
    tree = _tree([_node("u1")])
    _wire(monkeypatch, predicted_mm={"u1": (0.0, 0.0)},
          live_mm={"u1": (-2.0, 2.0)})
    monkeypatch.setattr(
        drift_mod.QMessageBox, "question",
        lambda *a, **k: drift_mod.QMessageBox.StandardButton.Yes)

    assert confirm_redraw_drift(_dock(tree), tree, {"u1"}) is True


# ── copper: counted from the run's own set, never guessed ─────────────────

def test_the_recreated_copper_is_counted_from_the_runs_own_set():
    tree = _tree([_node("c1", "net_trace"), _node("c2", "net_trace"),
                  _node("u1")])

    assert copper_records_in(tree, {"c1", "c2", "u1"}) == 2
    assert copper_records_in(tree, {"u1"}) == 0


# ── no connection / no tree / busy socket: the run goes on ────────────────

def test_without_a_board_the_guard_lets_the_run_go(monkeypatch):
    tree = _tree([_node("u1")])
    dock = _dock(tree, adapter=None)
    asked = []
    monkeypatch.setattr(drift_mod, "_ask", lambda *a: asked.append(a) or False)

    assert confirm_redraw_drift(dock, tree, {"u1"}) is True
    assert asked == []


def test_a_busy_socket_means_no_question(monkeypatch):
    """The ~400 ms poll tick owns the shared socket: the check cannot read, so the
    run proceeds exactly as it did before this guard existed."""
    tree = _tree([_node("u1")])
    monkeypatch.setattr(drift_mod, "socket_busy", lambda connection: True)
    asked = []
    monkeypatch.setattr(drift_mod, "_ask", lambda *a: asked.append(a) or False)

    assert confirm_redraw_drift(_dock(tree), tree, {"u1"}) is True
    assert asked == []


def test_no_tree_or_no_refs_is_not_a_question(monkeypatch):
    asked = []
    monkeypatch.setattr(drift_mod, "_ask", lambda *a: asked.append(a) or False)

    assert confirm_redraw_drift(_dock(_tree([])), None, {"u1"}) is True
    assert confirm_redraw_drift(_dock(_tree([])), _tree([]), set()) is True
    assert asked == []


# ── р: all four doors ask through the ONE function ────────────────────────

def _calls_in(path: Path) -> int:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return sum(1 for node in ast.walk(tree)
               if isinstance(node, ast.Call)
               and getattr(node.func, "id", None) == "confirm_redraw_drift")


def test_r_every_redraw_door_calls_the_one_guard():
    """The node and the module doors live in `trees_accept.redraw_node`; «Redraw
    selected» and «Redraw whole tree» are the two calls in `trees_dock`. One
    implementation, four doors — the same shape `refuse_embedded_tree` has."""
    assert _calls_in(REPO / "gui/docks/trees_accept.py") == 1
    assert _calls_in(REPO / "gui/docks/trees_dock.py") == 2


def test_r_the_guard_is_asked_before_the_worker_starts():
    """Position in the code, not just presence: both `trees_dock` calls stand ABOVE
    the `start_long_op(...)` of their door, so a refusal costs zero transactions."""
    source = (REPO / "gui/docks/trees_dock.py").read_text(encoding="utf-8")
    guard = source.index("confirm_redraw_drift(self, target, selected_refs)")
    tree_worker = source.index("run_curated_tree_redraw_worker, self._finish_redraw")
    assert guard < tree_worker
    forest_guard = source.index("confirm_redraw_drift(self, self._current_tree(), refs)")
    forest_worker = source.index("run_curated_forest_redraw_worker, self._finish_redraw")
    assert forest_guard < forest_worker
