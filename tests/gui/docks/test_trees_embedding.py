# tests/gui/docks/test_trees_embedding.py
"""The rule "which tree places which" — ONE scan, ONE chain (plan_2026_11_15_embedded_tree_redraw_
through_module, Т1 / Р2).

Every cell here is pure structure: three synthetic trees, no board, no dock, no Qt widget. The
point of the module is that the tab, the render, the anchor form and the position READ all ask the
SAME question — so the question is pinned once, here, instead of three times in three cells.

The old "refuse any module-placed tree" answer is pinned too (`refuse_embedded_tree`): Т1 only ADDS
the chain, the doors and this helper flip together in Т3, and a tab left without both would lay
the content out from its own anchor again.
"""
import ast
from pathlib import Path

import pytest

from gui.docks import trees_embedding as emb
from kicadstamp.trees import Tree, TreeAnchor, TreeNode


def node(ref, kind="placement", xy=(0.0, 0.0), rot=0.0, children=()):
    return TreeNode(ref=ref, kind=kind, xy=xy, polar=None, rotation=rot,
                    name=None, group=None, children=list(children))


def tree(name, nodes, *, shift=(0.0, 0.0), rotation=0.0, pivot_xy=None):
    return Tree(name=name, anchor=TreeAnchor(point="Origin", shift_xy=shift),
                nodes=list(nodes), rotation=rotation, pivot_xy=pivot_xy)


def by_name(trees, name):
    return next(t for t in trees if t.name == name)


def _three_levels():
    """`outer` places `mid` by its marker `mid`; `mid` places `inner` by its marker `inner`."""
    inner = tree("inner", [node("I1"), node("I2")])
    mid = tree("mid", [node("M1"), node("inner", kind="module", xy=(30.0, 30.0))])
    outer = tree("outer", [node("mid", kind="module", xy=(40.0, 40.0)), node("O1")])
    return [outer, mid, inner]


# ── the scan ───────────────────────────────────────────────────────────────

def test_a_tree_nobody_places_has_no_chain_and_no_root():
    """Cell: a standalone tree keeps today's behaviour — nothing to activate, its own anchor is
    the truth (`flow_root` None, not the tree's own name, so a caller cannot mistake "not placed"
    for "placed by itself")."""
    trees = _three_levels()
    standalone = by_name(trees, "outer")
    assert emb.placements(trees, standalone) == []
    assert emb.placement_chain(trees, standalone) == []
    assert emb.flow_root(trees, standalone) is None
    assert emb.chain_selection(trees, standalone) == set()
    assert emb.embedding_marker(trees, standalone) is None


def test_a_marker_deep_under_another_node_still_places_its_tree():
    """The scan walks the WHOLE node tree: a marker may hang under a `mount`/`copper`/plain node
    of the owner, and a top-level-only scan would report "nobody places this tree" — the exact
    shape that silently re-creates the Grid-Origin redraw."""
    deep = tree("deep", [node("D1")])
    owner = tree("holder", [node("mount", kind="mount", children=[
        node("deep", kind="module", xy=(1.0, 2.0))])])
    trees = [owner, deep]
    assert emb.placements(trees, deep) == [
        emb.Placement(tree="deep", owner="holder", marker="deep")]


def test_the_chain_walks_every_level_up_to_the_free_root():
    """Р2: for `inner` the selection has to carry BOTH markers (`inner` in `mid` AND `mid` in
    `outer`). With only the first level active, `curated_forest_module_content` takes `mid` for a
    free root and lays the content from `mid`'s own anchor (the Ч0 п.3 measurement)."""
    trees = _three_levels()
    inner = by_name(trees, "inner")
    chain = emb.placement_chain(trees, inner)
    assert chain == [
        emb.Placement(tree="inner", owner="mid", marker="inner"),
        emb.Placement(tree="mid", owner="outer", marker="mid"),
    ]
    assert emb.chain_selection(trees, inner) == {"inner", "mid"}
    assert emb.flow_root(trees, inner) == "outer"


def test_the_second_level_is_still_an_owner_of_its_own():
    """The same chain, asked one level out: `mid` is placed by `outer` alone, and its OWN anchor
    is not the frame its content lives in."""
    trees = _three_levels()
    mid = by_name(trees, "mid")
    assert [step.marker for step in emb.placement_chain(trees, mid)] == ["mid"]
    assert emb.flow_root(trees, mid) == "outer"


def test_a_marker_on_the_tree_itself_is_not_a_placement():
    """A self-marker is a config fatal and never a placement — counting it would make every
    such buffer unredrawable with "placed by more than one module"."""
    trees = [tree("solo", [node("solo", kind="module")])]
    solo = by_name(trees, "solo")
    assert emb.placements(trees, solo) == []


# ── two markers: the refusal that stays (п. 5 / Р2) ─────────────────────────

def _two_markers():
    child = tree("switch_a", [node("S1")])
    main = tree("main", [node("switch_a", kind="module", xy=(32.0, -35.0))])
    aux = tree("aux", [node("switch_a", kind="module", xy=(-8.0, 4.0))])
    return [main, aux, child]


def test_two_markers_are_both_named():
    trees = _two_markers()
    child = by_name(trees, "switch_a")
    found = emb.placements(trees, child)
    assert [(p.owner, p.marker) for p in found] == [("main", "switch_a"), ("aux", "switch_a")]
    line = emb.several_markers_line("switch_a", found)
    assert "'switch_a' in 'main'" in line
    assert "'switch_a' in 'aux'" in line
    assert "more than one module" in line


def test_two_markers_refuse_and_say_so_once(monkeypatch):
    """Cell д: the ONLY refusal left once the doors work in the module's frame."""
    said = []
    monkeypatch.setattr(emb, "show_message", lambda text, style, log: said.append(text))
    trees = _two_markers()
    assert emb.refuse_embedded_tree(trees, by_name(trees, "switch_a")) is True
    assert len(said) == 1
    assert "'main'" in said[0] and "'aux'" in said[0]


def test_one_marker_is_refused_by_THIS_helper_until_the_doors_use_the_chain(monkeypatch):
    """The pre-Т3 answer, pinned on purpose: the sentence still sends the reader to the module.

    Т3 switches the four doors to `chain_selection` and narrows this function to the 2+ case in
    the SAME commit; this cell is then replaced by "one marker is served, not refused". Keeping
    it now is what stops the helper from flipping early and leaving a tab that neither refuses
    nor knows the module.
    """
    said = []
    monkeypatch.setattr(emb, "show_message", lambda text, style, log: said.append(text))
    trees = _three_levels()
    inner = by_name(trees, "inner")
    assert emb.refuse_embedded_tree(trees, inner, node_ref="I1") is True
    assert len(said) == 1
    assert "placed by module 'inner' in tree 'mid'" in said[0]
    assert "node 'I1'" in said[0]


def test_nobody_placing_the_tree_is_never_a_refusal(monkeypatch):
    said = []
    monkeypatch.setattr(emb, "show_message", lambda text, style, log: said.append(text))
    trees = _three_levels()
    assert emb.refuse_embedded_tree(trees, by_name(trees, "outer")) is False
    assert said == []


# ── a buffer cycle must not hang ───────────────────────────────────────────

def test_a_marker_cycle_in_the_buffer_stops_the_walk():
    """`link_trees` rejects a cycle, but the DOCK BUFFER is editable: `inner` in `mid` and `mid`
    in `inner` is reachable mid-edit. The walk must stop on the revisit instead of looping, and
    the short chain is the honest answer (the caller then lays from what it reached)."""
    inner = tree("inner", [node("mid", kind="module")])
    mid = tree("mid", [node("inner", kind="module")])
    trees = [mid, inner]
    chain = emb.placement_chain(trees, by_name(trees, "inner"))
    # One step, then the walk sees that `mid`'s own marker points back at `inner` and stops:
    # there IS no free root in this buffer, and inventing a second step would claim one.
    assert [step.marker for step in chain] == ["inner"]
    assert emb.flow_root(trees, by_name(trees, "inner")) == "mid"


# ── the rule stays Qt-free of dialogs (Р4) ─────────────────────────────────

def test_the_module_holds_no_message_box():
    """Р4 of the 19.11 decisions: no window anywhere on this path, not even an import of the
    widget class — every refusal is ONE Log line through `show_message`."""
    source = Path(emb.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = set()
    for item in ast.walk(tree):
        if isinstance(item, ast.ImportFrom):
            imported |= {(alias.asname or alias.name) for alias in item.names}
        elif isinstance(item, ast.Import):
            imported |= {(alias.asname or alias.name.split(".")[0]) for alias in item.names}
    assert "QMessageBox" not in imported
    assert "QtWidgets" not in imported
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert "QMessageBox" not in names | attrs


@pytest.mark.parametrize("count", [1, 2])
def test_every_placement_names_its_own_owner_and_marker(count):
    """`Placement.where()` is the ONE wording: two owners of the same marker ref must not read as
    "the same marker twice"."""
    child = tree("t", [node("T1")])
    owners = [tree(f"o{i}", [node("t", kind="module")]) for i in range(count)]
    found = emb.placements([*owners, child], child)
    assert [p.where() for p in found] == [f"'t' in 'o{i}'" for i in range(count)]
