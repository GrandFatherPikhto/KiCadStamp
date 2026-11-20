# tests/gui/docks/test_trees_keep_place.py
"""A tree that loses its LAST module marker keeps that marker's place (Р4 of the 19.11 decisions).

plan_2026_11_15_embedded_tree_redraw_through_module, п. 8 as decided: NO question, the place is
written into the tree's own anchor in the dock's BUFFER, and ONE Log line names the old and the new
numbers. The alternatives are all worse: without it the tree jumps back to its own anchor (measured
(−1, +4) mm on the STM32 profile), and a modal is exactly what Denis banned on 17.11.

The arithmetic is pinned twice: as numbers (the shift is the owner's own shift plus the marker's xy
turned by the owner's angle) and as a LAYOUT — after the write, the tree laid out from its own
anchor must land exactly where the module put it.
"""
import ast
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from kicadstamp.config import ClonePlacement, Config
from kicadstamp.domain.geometry import Vector2
from kicadstamp.tree_position import (layout_tree_from_anchor, layout_tree_from_base,
                                      tree_layout_base)
from kicadstamp.trees import Tree, TreeAnchor, TreeNode

import gui.docks.trees_keep_place as kp


def node(ref, kind="clone", xy=(0.0, 0.0), rotation=0.0, children=()):
    return TreeNode(ref=ref, kind=kind, xy=xy, polar=None, rotation=rotation, name=None,
                    group=None, children=list(children))


def tree(name, nodes, *, shift=(0.0, 0.0), rotation=0.0, origin=True):
    anchor = TreeAnchor(is_origin=True) if origin else TreeAnchor(point="Origin")
    anchor.shift_xy = shift
    return Tree(name=name, anchor=anchor, nodes=list(nodes), rotation=rotation)


def by_name(trees, name):
    return next(t for t in trees if t.name == name)


class _Dock:
    """Just enough dock for the hook: the buffer, `_mark_dirty`, and — for the instance
    cells — the read-only declaration index and the STICKY `_generated` set."""

    def __init__(self, trees, *, instances=None, generated=()):
        self._trees = trees
        self.dirty = 0
        self._instances = dict(instances or {})
        self._generated = set(generated)

    def _mark_dirty(self):
        self.dirty += 1

    def _instance_of(self, tree):
        return self._instances.get(getattr(tree, "name", None))


def _cfg(**extra):
    return Config(**{"clone_placements": [ClonePlacement(cluster="R1", cell="c", xy=(0.0, 0.0))],
                     **extra})


# ── the place, as numbers ─────────────────────────────────────────────────

def test_the_shift_is_the_owners_shift_plus_the_marker_turned_by_the_owner():
    """«shift = shift хозяина ⊕ xy маркера с поворотом хозяина» (the plan's own formula)."""
    power = tree("power", [], shift=(5.0, -60.0))
    main = tree("main", [], shift=(0.5, 0.25), rotation=90.0)
    marker = node("power", kind="module", xy=(10.0, 0.0), rotation=0.0)

    shift, rotation = kp.marker_place([main, power], power, marker, main)

    # Y-down +90° maps (x, y) -> (y, -x), so the marker's (10, 0) becomes (0, -10).
    assert shift == (0.5, -9.75)
    assert rotation == 90.0


def test_the_tree_rotation_rides_on_top_of_the_markers_frame():
    power = tree("power", [], rotation=15.0)
    main = tree("main", [], rotation=90.0)
    marker = node("power", kind="module", xy=(0.0, 0.0), rotation=30.0)

    _shift, rotation = kp.marker_place([main, power], power, marker, main)

    assert rotation == pytest.approx(135.0)


def test_two_different_points_are_not_computable_without_the_board():
    power = tree("power", [], origin=False)          # point "Origin"
    role_owner = Tree(name="main", anchor=TreeAnchor(role="SW_ROLE"), nodes=[])
    marker = node("power", kind="module")
    assert kp.marker_place([role_owner, power], power, marker, role_owner) is None


# ── the place, as a LAYOUT (cell «м») ─────────────────────────────────────

def test_m_the_written_anchor_reproduces_the_modules_layout():
    """Cell м: `power`'s own anchor `Origin` + (5, −60), the marker (6, −64) in `main` (origin,
    no rotation). After the write the tree laid out from its OWN anchor stands exactly where the
    layout through `main` put it — 0.000 mm on every node."""
    cfg = _cfg()
    power = tree("power", [node("R1", xy=(3.0, 4.0))], shift=(5.0, -60.0))
    main = tree("main", [node("power", kind="module", xy=(6.0, -64.0))])
    cfg.trees = [main, power]
    marker = by_name(cfg.trees, "main").nodes[0]

    through_main = layout_tree_from_base(
        by_name(cfg.trees, "main"), Vector2.from_xy(0, 0), 0.0, {"main": main, "power": power})

    dock = _Dock([main, power])
    main.nodes = []                        # the door detaches FIRST, then asks (п. 8 / Р4)
    assert kp.keep_places_after_removal(dock, [(marker, main)]) == 1
    own = layout_tree_from_anchor(power, {"power": power}, cfg=cfg, sheet_names={})

    for ref in ("R1",):
        a, _ar = through_main[ref]
        b, _br = own[ref]
        assert (a.x, a.y) == (b.x, b.y), f"{ref} moved by the anchor write"
    assert power.anchor.shift_xy == (6.0, -64.0)
    assert dock.dirty == 1


# ── what does NOT write ───────────────────────────────────────────────────

def test_o_a_second_marker_still_places_the_tree(caplog):
    """Cell о: «пока остаётся другой маркер — ничего не предлагать». The tree is still embedded,
    so its anchor must stay untouched — otherwise removing one marker would silently move the
    tree that the other marker still places."""
    power = tree("power", [], shift=(5.0, -60.0))
    main = tree("main", [node("power", kind="module", xy=(6.0, -64.0))])
    aux = tree("aux", [node("power", kind="module", xy=(-5.0, 5.0))])
    markers = kp.module_markers_in(main.nodes)
    main.nodes = []                        # the marker of `main` is deleted, `aux` keeps it

    dock = _Dock([main, aux, power])
    caplog.clear()
    with caplog.at_level(logging.INFO):
        assert kp.keep_places_after_removal(dock, [(markers[0], main)]) == 0

    assert power.anchor.shift_xy == (5.0, -60.0)
    assert dock.dirty == 0
    assert not caplog.records


def test_a_tree_that_no_longer_exists_is_skipped():
    main = tree("main", [node("gone", kind="module", xy=(1.0, 1.0))])
    marker = main.nodes[0]
    main.nodes = []
    dock = _Dock([main])
    assert kp.keep_places_after_removal(dock, [(marker, main)]) == 0


def test_a_place_that_needs_the_board_is_a_yellow_line_and_no_write(caplog):
    """«свести без платы нельзя и платы нет → якорь не трогается, одна жёлтая строка»."""
    power = tree("power", [], origin=False)
    owner = Tree(name="main", anchor=TreeAnchor(role="SW_ROLE"), nodes=[])
    marker = node("power", kind="module", xy=(1.0, 1.0))
    dock = _Dock([owner, power])

    caplog.clear()
    with caplog.at_level(logging.WARNING):
        assert kp.keep_places_after_removal(dock, [(marker, owner)]) == 0

    assert power.anchor.shift_xy == (0.0, 0.0)
    assert dock.dirty == 0
    assert any("NOT moved" in r.getMessage() for r in caplog.records)


# ── the other two doors (cells «т» and «ф») ───────────────────────────────

def test_t_a_marker_whose_ref_changed_keeps_its_old_trees_place(caplog):
    """Cell т: the marker's Ref was edited onto another name (or onto nothing), so the OLD tree
    is no longer embedded — the same silent write, the same one line."""
    power = tree("power", [], shift=(5.0, -60.0))
    main = tree("main", [node("power2", kind="module", xy=(6.0, -64.0))])
    marker = main.nodes[0]
    dock = _Dock([main, power])

    caplog.clear()
    with caplog.at_level(logging.INFO):
        assert kp.keep_places_after_ref_change(dock, marker, ("module", "power"), main) == 1

    assert power.anchor.shift_xy == (6.0, -64.0)
    line = "\n".join(r.getMessage() for r in caplog.records)
    assert "'power'" in line and "'power2'" in line and "(6.000, -64.000)" in line


def test_t_an_unchanged_ref_writes_nothing():
    power = tree("power", [], shift=(5.0, -60.0))
    main = tree("main", [node("power", kind="module", xy=(6.0, -64.0))])
    dock = _Dock([main, power])
    assert kp.keep_places_after_ref_change(dock, main.nodes[0], ("module", "power"), main) == 0
    assert power.anchor.shift_xy == (5.0, -60.0)


def test_t_a_non_module_node_is_never_a_place():
    solo = tree("solo", [node("R1")])
    dock = _Dock([solo])
    assert kp.keep_places_after_ref_change(dock, solo.nodes[0], ("clone", "R1"), solo) == 0


def test_f_deleting_the_OWNER_tree_still_keeps_the_place(caplog):
    """Cell ф: the owner goes away with its markers, which is the same fact — the embedded tree
    keeps the place that owner gave it (computed from the owner's values, still alive here)."""
    power = tree("power", [], shift=(5.0, -60.0))
    main = tree("main", [node("power", kind="module", xy=(6.0, -64.0))], shift=(1.0, 0.0))
    markers = [(m, main) for m in kp.module_markers_in(main.nodes)]
    dock = _Dock([main, power])
    dock._trees.remove(main)               # what _on_delete_tree does

    caplog.clear()
    with caplog.at_level(logging.INFO):
        assert kp.keep_places_after_removal(dock, markers) == 1

    # The owner's own shift is part of the place: (1, 0) + (6, −64).
    assert power.anchor.shift_xy == (7.0, -64.0)
    assert any("no longer placed" in r.getMessage() for r in caplog.records)


# ── the rule stays silent and file-free ───────────────────────────────────

def test_the_hook_holds_no_dialog_and_writes_no_file():
    source = Path(kp.__file__).read_text(encoding="utf-8")
    parsed = ast.parse(source)
    names = {n.id for n in ast.walk(parsed) if isinstance(n, ast.Name)}
    attrs = {n.attr for n in ast.walk(parsed) if isinstance(n, ast.Attribute)}
    imported = {(alias.asname or alias.name) for item in ast.walk(parsed)
                if isinstance(item, ast.ImportFrom) for alias in item.names}
    assert "QMessageBox" not in names | attrs | imported
    assert "show_message" not in imported, "a place that cannot be moved is ONE yellow Log line"
    assert "write_data" not in names | attrs, "the buffer only — Save is the user's"


def test_the_layout_helper_agrees_with_the_module_rule():
    """The place is only correct because both sides are taken at the SAME stage: `marker_place`
    uses the marker's own xy (not the already-pivoted base). A tree with a pivot makes that
    visible — the layout must still agree after the write."""
    cfg = _cfg()
    power = tree("power", [node("R1", xy=(1.0, 0.0))], shift=(5.0, -60.0))
    power.pivot_xy = (2.0, 3.0)
    main = tree("main", [node("power", kind="module", xy=(6.0, -64.0))])
    cfg.trees = [main, power]
    marker = main.nodes[0]

    through_main = layout_tree_from_base(
        main, Vector2.from_xy(0, 0), 0.0, {"main": main, "power": power})
    main.nodes = []
    kp.keep_places_after_removal(_Dock([main, power]), [(marker, main)])
    own = layout_tree_from_anchor(power, {"power": power}, cfg=cfg, sheet_names={})

    a, _ = through_main["R1"]
    b, _ = own["R1"]
    assert (a.x, a.y) == (b.x, b.y)


def test_the_tree_rotation_is_what_makes_a_turned_owner_work():
    """A rotated owner is a ROTATION, not only a shift: the write moves the tree's own angle too,
    which is why the plan says «меняются shift и rotation»."""
    power = tree("power", [node("R1", xy=(1.0, 0.0))], shift=(0.0, 0.0))
    main = tree("main", [node("power", kind="module", xy=(0.0, 0.0))], rotation=90.0)
    before = tree_layout_base(None, _cfg(), power, {}, {"power": power})

    marker = main.nodes[0]
    main.nodes = []
    kp.keep_places_after_removal(_Dock([main, power]), [(marker, main)])

    assert power.rotation == 90.0
    after = tree_layout_base(None, _cfg(), power, {}, {"power": power})
    assert after[1] != before[1], "the angle must follow the owner"


# ── м2 (Ф1 приёмки 19.11): место следует ВСЕЙ цепочке, а не якорю хозяина ───

def _chain_of_three(shift=(10.0, 5.0), rotation=90.0):
    """`top` ⊃ маркер `main` (20, 0) ⊃ маркер `power` (6, −64); у `main` свой сдвиг (1, 1)."""
    power = tree("power", [node("R1", xy=(3.0, 4.0))], shift=(5.0, -60.0))
    main = tree("main", [node("power", kind="module", xy=(6.0, -64.0))], shift=(1.0, 1.0))
    top = tree("top", [node("main", kind="module", xy=(20.0, 0.0))],
               shift=shift, rotation=rotation)
    return [top, main, power]


@pytest.mark.parametrize("shift,rotation", [((0.0, 0.0), 0.0),
                                            ((10.0, 5.0), 0.0),
                                            ((10.0, 5.0), 90.0)])
def test_m2_the_kept_place_follows_the_whole_chain(shift, rotation):
    """The probe of the 19.11 acceptance, as a LAYOUT: with `top` shifted and turned 90° the old
    composition (owner's own anchor) wrote (−60.000, +35.000) mm of error. After the write the tree
    laid out from its own anchor must land exactly where the chain put it — 0.000 mm."""
    trees = _chain_of_three(shift, rotation)
    top, main, power = trees
    by_name = {t.name: t for t in trees}
    cfg = _cfg()
    base = tree_layout_base(None, cfg, top, {}, by_name)
    through = layout_tree_from_base(top, base[0], base[1], by_name)

    marker = main.nodes[0]
    dock = _Dock(trees)
    plans = kp.lost_places(dock, [(marker, main)])     # computed BEFORE the detach (Ф1)
    main.nodes = []
    assert kp.apply_lost_places(dock, plans) == 1

    own = layout_tree_from_anchor(power, {"power": power}, cfg=cfg, sheet_names={})
    a, _a_rot = through["R1"]
    b, _b_rot = own["R1"]
    assert (a.x, a.y) == (b.x, b.y), "the kept place must equal the chain's"


def test_m2_deleting_the_whole_host_keeps_the_place_too():
    """The same for `_on_delete_tree`: the owner goes away with its markers, and the frame it had
    (through `top`) is what the embedded tree's own anchor must keep."""
    trees = _chain_of_three((10.0, 5.0), 90.0)
    top, main, power = trees
    by_name = {t.name: t for t in trees}
    cfg = _cfg()
    base = tree_layout_base(None, cfg, top, {}, by_name)
    through = layout_tree_from_base(top, base[0], base[1], by_name)

    dock = _Dock(trees)
    plans = kp.lost_places(dock, [(m, main) for m in kp.module_markers_in(main.nodes)])
    dock._trees.remove(main)
    assert kp.apply_lost_places(dock, plans) == 1

    own = layout_tree_from_anchor(power, {"power": power}, cfg=cfg, sheet_names={})
    a, _ = through["R1"]
    b, _ = own["R1"]
    assert (a.x, a.y) == (b.x, b.y)


def test_m2_a_host_placed_by_TWO_markers_is_a_yellow_line_not_a_guess(caplog):
    """Ф1 п.2: неоднозначная вложенность (у хозяина два маркера) — якорь не тронут, одна жёлтая
    строка. Молчаливая догадка здесь была бы ровно тем прыжком, от которого Р4 защищает."""
    trees = _chain_of_three()
    top, main, power = trees
    trees.append(tree("aux", [node("main", kind="module", xy=(-5.0, 5.0))]))
    marker = main.nodes[0]
    dock = _Dock(trees)
    plans = kp.lost_places(dock, [(marker, main)])
    main.nodes = []

    caplog.clear()
    with caplog.at_level(logging.WARNING):
        assert kp.apply_lost_places(dock, plans) == 0

    assert power.anchor.shift_xy == (5.0, -60.0)
    assert dock.dirty == 0
    assert any("does not reduce to one point" in r.getMessage() for r in caplog.records)


# ── a GENERATED `tree_instances` copy keeps nothing (Ф4, 2026-11-20, decision «в» of
#    plan_2026_11_19_forest_index_keys_and_instances) ────────────────────────
#
# The copy's place lives in the DECLARATION, and the dock never writes that section
# (`_do_save` drops every generated tree), so a silent write into the copy would be lost
# by the next expansion while the Log claimed success. Nothing is written, `_mark_dirty`
# is not called, and ONE yellow line carries the numbers for Tools → Trees → Instances…

def test_z3_an_instance_keeps_nothing_and_says_the_numbers(caplog):
    """Cell «з3»: the copy and the declaration stay as they are, the buffer is not
    dirtied, and the line names the place AND what the declaration says now."""
    copy = tree("Switch_B", [])
    main = tree("main", [], shift=(10.0, 5.0))
    marker = node("Switch_B", kind="module", xy=(40.0, -24.0))
    inst = SimpleNamespace(template="Switch_A", name="Switch_B",
                           anchor={"point": "Origin", "shift": [1.5, -2.5]},
                           rotation=30.0)
    dock = _Dock([main, copy], instances={"Switch_B": inst},
                 generated={"Switch_B"})
    before = (copy.anchor.shift_xy, copy.rotation)

    caplog.clear()
    with caplog.at_level(logging.WARNING):
        assert kp.keep_places_after_removal(dock, [(marker, main)]) == 0

    assert (copy.anchor.shift_xy, copy.rotation) == before
    assert dock.dirty == 0
    assert len(caplog.records) == 1
    line = caplog.records[0].getMessage()
    assert "is an instance of 'Switch_A'" in line
    assert "origin + shift (50.000, -19.000), rotation 0.0°" in line
    assert "(now: point 'Origin' shift (1.500, -2.500), rotation 30.0°)" in line
    assert "Instances…" in line
    assert "keeps that place" not in line


def test_z3b_an_instance_still_placed_elsewhere_keeps_quiet(caplog):
    """Cell «з3б»: another marker still places the copy — nothing is written, nothing is
    said (the silence rule of Р4 covers instances too)."""
    copy = tree("Switch_B", [])
    main = tree("main", [], shift=(10.0, 5.0))
    marker = node("Switch_B", kind="module", xy=(40.0, -24.0))
    aux = tree("aux", [node("Switch_B", kind="module", xy=(-5.0, 5.0))])
    inst = SimpleNamespace(template="Switch_A", name="Switch_B",
                           anchor=None, rotation=None)
    dock = _Dock([main, copy, aux], instances={"Switch_B": inst},
                 generated={"Switch_B"})

    caplog.clear()
    with caplog.at_level(logging.WARNING):
        assert kp.keep_places_after_removal(dock, [(marker, main)]) == 0

    assert caplog.records == []
    assert dock.dirty == 0
    assert copy.anchor.shift_xy == (0.0, 0.0)


def test_z3v_a_copy_whose_declaration_is_gone_keeps_nothing(caplog):
    """Cell «з3в»: `_generated` is STICKY — a copy whose declaration was deleted is still
    not an ordinary tree (a write would be dropped by Save), so it gets its own line."""
    copy = tree("Switch_B", [])
    main = tree("main", [], shift=(10.0, 5.0))
    marker = node("Switch_B", kind="module", xy=(40.0, -24.0))
    dock = _Dock([main, copy], generated={"Switch_B"})

    caplog.clear()
    with caplog.at_level(logging.WARNING):
        assert kp.keep_places_after_removal(dock, [(marker, main)]) == 0

    assert dock.dirty == 0
    assert copy.anchor.shift_xy == (0.0, 0.0)
    assert len(caplog.records) == 1
    line = caplog.records[0].getMessage()
    assert "declaration that is gone" in line and "nothing is kept" in line
