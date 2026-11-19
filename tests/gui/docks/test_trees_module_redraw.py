# tests/gui/docks/test_trees_module_redraw.py
"""The doors of an EMBEDDED tree's tab: the tree is redrawn THROUGH its module.

plan_2026_11_15_embedded_tree_redraw_through_module, п. 1-3 and 5 (Denis, 15.11: «хочется
поработать с деревом отдельно, а фигушки», and 19.11: Р2 — nest the chain, refuse only two
markers).

The rule module (`trees_embedding`) answers WHO places the tree; this one ACTS on it: one
three-state entry (`redraw_embedded`) that every door calls, so a door can neither forget the
refusal nor start a redraw from the tree's own anchor by accident.
"""
import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

import gui.docks.trees_module_redraw as tmr
from gui.docks.trees_embedding import Placement
from kicadstamp.trees import Tree, TreeAnchor, TreeNode


def node(ref, kind="placement", xy=(0.0, 0.0), children=()):
    return TreeNode(ref=ref, kind=kind, xy=xy, polar=None, rotation=0.0, name=None,
                    group=None, children=list(children))


def tree(name, nodes, *, shift=(0.0, 0.0)):
    return Tree(name=name, anchor=TreeAnchor(point="Origin", shift_xy=shift),
                nodes=list(nodes))


def by_name(trees, name):
    return next(t for t in trees if t.name == name)


class _Dock:
    """Just enough TreesDock for the doors: the buffer, the run gates and the status row."""

    def __init__(self, trees, *, cfg="cfg", root="/r.sexp", confirm=True):
        self._trees = trees
        self._cfg = cfg
        self._root_path = root
        self._ctx = SimpleNamespace(sheet_names={})
        self._active_op = None
        self._confirm = confirm
        self.status = []
        self._main_window = SimpleNamespace(connection=object())

    def _show_status(self, text):
        self.status.append(text)

    def _confirm_first_run_redraw(self):
        return self._confirm

    def _finish_redraw(self, result):        # pragma: no cover — the worker's callback
        pass

    def _on_redraw_failed(self, message):    # pragma: no cover
        pass


@pytest.fixture
def started(monkeypatch):
    """Every `start_long_op` the module calls, and the journal lines it would have written."""
    calls = []

    def _start(connection, widgets, worker, finish, failed, payload, **kwargs):
        calls.append({"worker": worker, "payload": payload, "widgets": list(widgets)})
        return object()

    monkeypatch.setattr(tmr, "start_long_op", _start)
    monkeypatch.setattr(tmr, "log_redraw_drift", lambda *a, **k: None)
    return calls


@pytest.fixture
def said(monkeypatch):
    shown = []
    monkeypatch.setattr(tmr, "show_message", lambda text, style, log: shown.append(text))
    return shown


def _embedded():
    """`main` places `power` by its marker; `power` has a root and one child."""
    return [tree("main", [node("power", kind="module", xy=(6.0, -64.0))]),
            tree("power", [node("R1", children=[node("R2", xy=(1.0, 2.0))])])]


def _chain():
    """`outer` places `mid`, `mid` places `inner`.

    The two records are `clone` placements on purpose: the chain-depth cell links these trees to
    a real `Config` (as the planner does), and a `placement` ref must name an Entity there."""
    return [tree("outer", [node("mid", kind="module", xy=(40.0, 40.0))]),
            tree("mid", [node("M1", kind="clone"), node("inner", kind="module", xy=(30.0, 30.0))]),
            tree("inner", [node("I1", kind="clone")])]


# ── the refusal that stays: two or more markers (п. 5) ─────────────────────

def test_two_markers_refuse_and_name_every_one(said):
    trees = _embedded() + [tree("aux", [node("power", kind="module", xy=(-5.0, 5.0))])]
    assert tmr.refuse_embedded_redraw(trees, by_name(trees, "power")) is True
    assert len(said) == 1
    assert "'power' in 'main'" in said[0] and "'power' in 'aux'" in said[0]


def test_one_marker_is_served_not_refused(said):
    trees = _embedded()
    assert tmr.refuse_embedded_redraw(trees, by_name(trees, "power")) is False
    assert said == []


def test_a_standalone_tree_is_never_refused(said):
    trees = _embedded()
    assert tmr.refuse_embedded_redraw(trees, by_name(trees, "main")) is False
    assert said == []


# ── the startup line (п. 6) ────────────────────────────────────────────────

def test_the_line_names_the_marker_and_its_owner():
    trees = _embedded()
    chain = [Placement(tree="power", owner="main", marker="power")]
    line = tmr.embedded_redraw_line(by_name(trees, "power"), chain)
    assert "'power' in 'main'" in line
    assert "power" in line


def test_a_nested_chain_is_printed_outermost_first():
    trees = _chain()
    inner = by_name(trees, "inner")
    chain = [Placement(tree="inner", owner="mid", marker="inner"),
             Placement(tree="mid", owner="outer", marker="mid")]
    line = tmr.embedded_redraw_line(inner, chain)
    assert line.index("'mid' in 'outer'") < line.index("'inner' in 'mid'")


# ── the three-state answer every door calls ────────────────────────────────

def test_a_tree_nobody_places_answers_None_and_starts_nothing(started, said):
    trees = _embedded()
    assert tmr.redraw_embedded(_Dock(trees), by_name(trees, "main")) is None
    assert started == [] and said == []


def test_a_two_marker_tree_answers_False(started, said):
    trees = _embedded() + [tree("aux", [node("power", kind="module", xy=(-5.0, 5.0))])]
    assert tmr.redraw_embedded(_Dock(trees), by_name(trees, "power")) is False
    assert started == [], "a refused tree must not start a worker"
    assert len(said) == 1


def test_an_embedded_tree_answers_True_and_hands_over_the_chain(started):
    trees = _embedded()
    payloads = []
    dock = _Dock(trees)

    assert tmr.redraw_embedded(dock, by_name(trees, "power"),
                               only_names={"R2"}) is True

    assert len(started) == 1
    payload = started[0]["payload"]
    assert payload["selected_refs"] == {"power"}, "the MARKER, never the tree's own refs"
    assert payload["only_names"] == {"R2"}
    assert payload["trees"] is trees, "the full forest rides in the payload"
    assert started[0]["worker"] is tmr.run_curated_forest_redraw_worker
    assert payloads == []


def test_the_whole_tree_run_sends_only_names_None(started):
    """A whole-tree Redraw applies everything its chain plans — the tree plus the content of the
    modules it embeds, i.e. exactly what the module's own Redraw in the owner moves."""
    trees = _embedded()
    assert tmr.redraw_embedded(_Dock(trees), by_name(trees, "power")) is True
    assert started[0]["payload"]["only_names"] is None


def test_a_nested_tree_hands_over_the_WHOLE_chain(started):
    trees = _chain()
    assert tmr.redraw_embedded(_Dock(trees), by_name(trees, "inner"),
                               only_names={"I1"}) is True
    assert started[0]["payload"]["selected_refs"] == {"inner", "mid"}


def test_a_second_marker_level_alone_would_take_the_hosts_own_anchor():
    """The fact the chain exists for, pinned without a worker: one level is NOT enough. The
    stage-2 planner takes the owner's own anchor unless that owner is module-placed in the SAME
    run (measured in Ч0 п.3 of the plan: `flow_roots=['mid']` instead of `['outer']`)."""
    from kicadstamp.config import ClonePlacement, Config
    from kicadstamp.link_trees import link_trees
    from kicadstamp.tree_position import curated_forest_module_content

    cfg = Config(clone_placements=[ClonePlacement(cluster=ref, cell="c", xy=(0.0, 0.0))
                                   for ref in ("M1", "I1")])
    cfg.trees = _chain()
    linked = link_trees(cfg, cfg.trees)
    assert curated_forest_module_content(linked, {"inner"})[1] == ["mid"]
    assert curated_forest_module_content(linked, {"inner", "mid"})[1] == ["outer"]


def test_nothing_to_redraw_when_the_dock_has_no_root(started):
    trees = _embedded()
    dock = _Dock(trees, root="")
    assert tmr.redraw_embedded(dock, by_name(trees, "power")) is False
    assert started == []
    assert dock.status, "the status row must say why nothing happened"


def test_the_first_run_heads_up_can_stop_the_run(started):
    trees = _embedded()
    assert tmr.redraw_embedded(_Dock(trees, confirm=False),
                               by_name(trees, "power")) is False
    assert started == []


# ── one rule, one place (Р4) ───────────────────────────────────────────────

def test_the_module_holds_no_message_box():
    source = Path(tmr.__file__).read_text(encoding="utf-8")
    parsed = ast.parse(source)
    names = {n.id for n in ast.walk(parsed) if isinstance(n, ast.Name)}
    attrs = {n.attr for n in ast.walk(parsed) if isinstance(n, ast.Attribute)}
    imported = {(alias.asname or alias.name) for item in ast.walk(parsed)
                if isinstance(item, ast.ImportFrom) for alias in item.names}
    assert "QMessageBox" not in names | attrs | imported
    assert "QtWidgets" not in imported


def test_all_four_doors_ask_this_module():
    """The doors live in three files (two of them a 6900-line giant) — a grep-free AST cell keeps
    a reader from "just" adding a fifth door that lays the tree out from its own anchor."""
    doors = {
        "gui/docks/trees_dock.py": ("redraw_embedded",),
        "gui/docks/trees_accept.py": ("redraw_embedded", "refuse_embedded_redraw",
                                      "start_embedded_redraw"),
    }
    root = Path(__file__).resolve().parents[3]
    for rel, expected in doors.items():
        source = (root / rel).read_text(encoding="utf-8")
        for name in expected:
            assert f"{name}(" in source, f"{rel} must go through {name}()"
