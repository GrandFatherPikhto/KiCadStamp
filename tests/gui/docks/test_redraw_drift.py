# tests/gui/docks/test_redraw_drift.py
"""К2-Ф7/Ф8/Ф9 of plan_2026_11_16_reread_forest_from_board — the Redraw move journal.

The contract after the 17.11 acceptance:

* Ф7 — the measurement walks the WHOLE FOREST, not the door's own tree, and a `module`
  marker in the set is expanded to its content (`expand_modules`). Without that the
  guard was blind to module content — i.e. exactly to the 16.11 case;
* Ф8 — a part that TURNS by more than 1° is named too, at zero distance: a two-pad part
  rotated 180° keeps its centre and swaps its pads;
* Ф9 — Denis, 17.11: no window at all. The result is ONE Log line and the run goes on;
  no door branches on it and `redraw_drift` holds no `QMessageBox`.

The board is faked at the two seams the measurement really uses — the layout
(`layout_tree_from_anchor`) and the live pose (`child_live_pose`).
"""
import ast
from pathlib import Path
from types import SimpleNamespace

import gui.docks.redraw_drift as drift_mod
from gui.docks.redraw_drift import (copper_records_in, expand_modules,
                                    log_redraw_drift, measure_drift)
from kicadstamp.domain.geometry import Vector2
from kicadstamp.trees import Tree, TreeAnchor, TreeNode

REPO = Path(__file__).resolve().parents[3]


def _node(ref, kind="placement", *, xy=(0.0, 0.0), children=()):
    return TreeNode(ref=ref, kind=kind, xy=xy, polar=None, rotation=0.0,
                    name=None, group=None, children=list(children))


def _tree(name, nodes):
    return Tree(name=name, anchor=TreeAnchor(is_origin=True), nodes=list(nodes))


def _dock(*trees, adapter=object()):
    dock = SimpleNamespace(
        _cfg=SimpleNamespace(trees=list(trees)), _trees=list(trees),
        _ctx=SimpleNamespace(sheet_names={}), _active_op=None,
        _root_path="/tmp/root", _main_window=SimpleNamespace(connection=None))
    dock._live_adapter = lambda: adapter
    return dock


def _wire(monkeypatch, *, predicted_mm, live_mm, predicted_deg=None,
          live_deg=None):
    """The two seams of one measurement: where the layout puts it, where it stands."""
    predicted_deg = predicted_deg or {}
    live_deg = live_deg or {}
    monkeypatch.setattr(
        drift_mod, "layout_tree_from_anchor",
        lambda *a, **k: {ref: (Vector2.from_xy_mm(x, y), predicted_deg.get(ref, 0.0))
                         for ref, (x, y) in predicted_mm.items()})
    monkeypatch.setattr(
        drift_mod, "child_live_pose",
        lambda cfg, adapter, sheets, ref, kind, **k:
            (Vector2.from_xy_mm(*live_mm[ref]), live_deg.get(ref, 0.0), False))


def _warnings(caplog) -> list:
    return [record.getMessage() for record in caplog.records
            if record.levelname == "WARNING"]


# ── о: a move below both thresholds is silent ─────────────────────────────

def test_o_a_three_micrometre_move_says_nothing(monkeypatch, caplog):
    tree = _tree("main", [_node("u1")])
    _wire(monkeypatch, predicted_mm={"u1": (0.0, 0.0)}, live_mm={"u1": (0.003, 0.0)})

    with caplog.at_level("WARNING"):
        log_redraw_drift(_dock(tree), tree, {"u1"})

    assert _warnings(caplog) == []


# ── Ф7: the whole forest is measured, and a marker expands to its content ─

def _main_and_fpga():
    c1 = _node("C1", xy=(2.0, 0.0))
    u1 = _node("U1", xy=(0.0, 0.0), children=[c1])
    fpga = _tree("fpga", [u1])
    main = _tree("main", [_node("mh"), _node("fpga", "module", xy=(10.0, 10.0))])
    return main, fpga


def test_f7_the_module_door_names_the_content_it_activates(monkeypatch, caplog):
    """The door passes the MARKER (`{fpga}`); the content `U1`/`C1` lives in another
    tree, so walking the owner's own nodes found nothing and the 16.11 case went
    unnamed."""
    main, fpga = _main_and_fpga()
    _wire(monkeypatch,
          predicted_mm={"U1": (10.0, 10.0), "C1": (12.0, 10.0)},
          live_mm={"U1": (10.0, 13.08), "C1": (12.0, 13.08)})

    with caplog.at_level("WARNING"):
        log_redraw_drift(_dock(main, fpga), main, {"fpga"})

    line = _warnings(caplog)[0]
    assert "2 part(s)" in line and "3.080" in line
    assert "U1" in line and "C1" in line


def test_f7_the_whole_tree_door_names_marker_content_too(monkeypatch, caplog):
    main, fpga = _main_and_fpga()
    _wire(monkeypatch,
          predicted_mm={"U1": (10.0, 10.0), "C1": (12.0, 10.0)},
          live_mm={"U1": (10.0, 13.08), "C1": (12.0, 13.08)})

    with caplog.at_level("WARNING"):
        log_redraw_drift(_dock(main, fpga), main, {"mh", "fpga"})

    assert "U1" in _warnings(caplog)[0]


def test_f7_expand_modules_walks_nested_markers():
    inner = _tree("inner", [_node("R1")])
    middle = _tree("middle", [_node("M1"), _node("inner", "module")])
    outer = _tree("outer", [_node("O1"), _node("middle", "module")])
    forest = {"inner": inner, "middle": middle, "outer": outer}

    assert expand_modules({"outer"}, forest) == {"outer", "O1", "middle", "M1",
                                                 "inner", "R1"}
    assert expand_modules({"O1"}, forest) == {"O1"}


def test_f7_the_index_sees_a_node_of_another_tree(monkeypatch):
    """`measure_drift` decides kind/anchor from the FOREST index, never from the
    door's own list."""
    main, fpga = _main_and_fpga()
    _wire(monkeypatch, predicted_mm={"U1": (10.0, 10.0)},
          live_mm={"U1": (10.0, 10.0)})

    rows = measure_drift(object(), SimpleNamespace(trees=[main, fpga]), main,
                         {"fpga"}, {})

    assert rows == []          # 0 mm, 0°: found, and not worth naming


# ── Ф8: a turn is a reason to name a part, at zero distance ───────────────

def test_f8_a_half_turn_at_zero_distance_is_named_with_its_angle(monkeypatch,
                                                                 caplog):
    tree = _tree("main", [_node("C1")])
    _wire(monkeypatch, predicted_mm={"C1": (0.0, 0.0)},
          live_mm={"C1": (0.0, 0.0)},
          predicted_deg={"C1": 0.0}, live_deg={"C1": 180.0})

    with caplog.at_level("WARNING"):
        log_redraw_drift(_dock(tree), tree, {"C1"})

    line = _warnings(caplog)[0]
    assert "C1 0.000 mm 180°" in line


def test_the_turn_template_keeps_BOTH_placeholders():
    """The catalogue nearly ate the angle: `update_i18n` copied the mm-only message
    onto the new msgid, and `.format` silently drops a keyword the template does not
    name — the line came out as «C1 0.000 mm». The template must carry `{deg}`."""
    template = drift_mod._("{ref} {mm:.3f} mm {deg:.0f}°")
    assert "{mm" in template and "{deg" in template


def test_n2_a_part_that_both_moved_and_turned_is_named_with_both(monkeypatch,
                                                                caplog):
    """Н2: a part that moved 3 mm AND turned 180° used to lose its distance — the line
    showed only «U1 180°». Both numbers belong in it."""
    tree = _tree("main", [_node("C1")])
    _wire(monkeypatch, predicted_mm={"C1": (0.0, 0.0)},
          live_mm={"C1": (3.08, 0.0)},
          predicted_deg={"C1": 0.0}, live_deg={"C1": 180.0})

    with caplog.at_level("WARNING"):
        log_redraw_drift(_dock(tree), tree, {"C1"})

    line = _warnings(caplog)[0]
    assert "3.080 mm" in line and "180°" in line


def test_n1_a_still_run_with_copper_says_nothing(monkeypatch, caplog):
    """Н1: the copper line used to appear for EVERY run whose set held a `net_trace`,
    even when nothing moved — the docstring promises silence instead."""
    tree = _tree("main", [_node("c1", "net_trace"), _node("u1")])
    _wire(monkeypatch, predicted_mm={"c1": (0.0, 0.0), "u1": (0.0, 0.0)},
          live_mm={"c1": (0.0, 0.0), "u1": (0.0, 0.0)})

    with caplog.at_level("WARNING"):
        log_redraw_drift(_dock(tree), tree, {"c1", "u1"})

    assert copper_records_in(SimpleNamespace(trees=[tree]), {"c1"}) == 1
    assert _warnings(caplog) == []


def test_f8_a_tiny_turn_is_silent(monkeypatch, caplog):
    tree = _tree("main", [_node("C1")])
    _wire(monkeypatch, predicted_mm={"C1": (0.0, 0.0)},
          live_mm={"C1": (0.0, 0.0)},
          predicted_deg={"C1": 0.0}, live_deg={"C1": 0.005})

    with caplog.at_level("WARNING"):
        log_redraw_drift(_dock(tree), tree, {"C1"})

    assert _warnings(caplog) == []


def test_f8_the_biggest_move_comes_first(monkeypatch):
    tree = _tree("main", [_node("u1"), _node("u2")])
    _wire(monkeypatch, predicted_mm={"u1": (0.0, 0.0), "u2": (0.0, 0.0)},
          live_mm={"u1": (0.2, 0.0), "u2": (1.5, 0.0)})

    rows = measure_drift(object(), SimpleNamespace(trees=[tree]), tree,
                         {"u1", "u2"}, {})

    assert [row.ref for row in rows] == ["u2", "u1"]


def test_a_part_the_layout_does_not_place_is_not_counted(monkeypatch):
    tree = _tree("main", [_node("mnt", "mount"), _node("c1", "net_trace")])
    _wire(monkeypatch, predicted_mm={}, live_mm={"mnt": (5.0, 0.0),
                                                 "c1": (5.0, 0.0)})

    assert measure_drift(object(), SimpleNamespace(trees=[tree]), tree,
                         {"mnt", "c1"}, {}) == []


# ── copper: counted from the run's own set, markers expanded ─────────────

def test_the_recreated_copper_is_counted_from_the_runs_own_set():
    inner = _tree("inner", [_node("c1", "net_trace")])
    main = _tree("main", [_node("c2", "net_trace"), _node("u1"),
                          _node("inner", "module")])
    cfg = SimpleNamespace(trees=[main, inner])

    assert copper_records_in(cfg, {"c2", "u1"}) == 1
    assert copper_records_in(cfg, {"inner"}) == 1
    assert copper_records_in(cfg, {"u1"}) == 0


# ── no board / busy socket / no tree: silence, and the run goes on ───────

def test_without_a_board_nothing_is_written(monkeypatch, caplog):
    tree = _tree("main", [_node("u1")])
    _wire(monkeypatch, predicted_mm={"u1": (0.0, 0.0)},
          live_mm={"u1": (3.0, 0.0)})

    with caplog.at_level("WARNING"):
        log_redraw_drift(_dock(tree, adapter=None), tree, {"u1"})

    assert _warnings(caplog) == []


def test_a_busy_socket_skips_the_measurement(monkeypatch, caplog):
    tree = _tree("main", [_node("u1")])
    _wire(monkeypatch, predicted_mm={"u1": (0.0, 0.0)},
          live_mm={"u1": (3.0, 0.0)})
    monkeypatch.setattr(drift_mod, "socket_busy", lambda connection: True)

    with caplog.at_level("WARNING"):
        log_redraw_drift(_dock(tree), tree, {"u1"})

    assert _warnings(caplog) == []


def test_no_tree_or_no_refs_writes_nothing(monkeypatch, caplog):
    tree = _tree("main", [_node("u1")])
    _wire(monkeypatch, predicted_mm={"u1": (0.0, 0.0)},
          live_mm={"u1": (3.0, 0.0)})

    with caplog.at_level("WARNING"):
        log_redraw_drift(_dock(tree), None, {"u1"})
        log_redraw_drift(_dock(tree), tree, set())

    assert _warnings(caplog) == []


# ── Ф9: no window, and no door branches on the answer ────────────────────

def _module_ast(path: str):
    return ast.parse((REPO / path).read_text(encoding="utf-8"), filename=path)


def test_f9_the_module_holds_no_message_box():
    tree = _module_ast("gui/docks/redraw_drift.py")
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    names |= {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    assert "QMessageBox" not in names
    assert "QtWidgets" not in names


def test_f9_every_door_calls_the_journal_and_never_branches_on_it():
    """The four doors call `log_redraw_drift(...)` as a STATEMENT: not one of the calls
    sits inside an `if` test, so no door can refuse a redraw because of a drift."""
    for path in ("gui/docks/trees_accept.py", "gui/docks/trees_dock.py"):
        tree = _module_ast(path)
        calls = [node for node in ast.walk(tree)
                 if isinstance(node, ast.Call)
                 and getattr(node.func, "id", None) == "log_redraw_drift"]
        assert calls, path
        in_tests = {id(node) for node in ast.walk(tree)
                    if isinstance(node, ast.If) for node in ast.walk(node.test)}
        assert not [call for call in calls if id(call) in in_tests], path


def test_f9_the_four_doors_are_still_the_four():
    assert sum(1 for node in ast.walk(_module_ast("gui/docks/trees_accept.py"))
               if isinstance(node, ast.Call)
               and getattr(node.func, "id", None) == "log_redraw_drift") == 1
    assert sum(1 for node in ast.walk(_module_ast("gui/docks/trees_dock.py"))
               if isinstance(node, ast.Call)
               and getattr(node.func, "id", None) == "log_redraw_drift") == 2
