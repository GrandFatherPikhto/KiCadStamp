# tests/gui/docks/test_trees_mounts_launch.py
"""Т6/Т6.1 of plan_2026_11_03_add_mounts_table — the ONE launcher both «Add mounts…»
menu items share (the dock adds one line per menu and decides nothing).

What is pinned here, with a stand-in dock and no board:

  * the PARENT (Д1): a click on the tree's root passes NO parent, a click on a node
    passes that node's ref — which is what the window remembers as `parent_ref`;
  * the ADDRESS (Д1а) in BOTH spellings: a `mount`/`component` node carries a role
    anchor, a `placement` node carries none and its RECORD supplies cluster/sheet
    (the grammar accepts a nested `(anchor …)` on mount and component nodes only —
    Т6.1 found the first version prefilling from an anchor a cell-record node can
    never have);
  * the Role CANDIDATES are the roles of the CELL the record places — `record.cell`,
    never the record's own name — in the cell's order, each listed once;
  * a copper container offers NO item (it may hold only `net_trace`, so mounts there
    would be refused after the pads were already ticked);
  * the window is built on the DOCK's state and is not retained (§23).

EVERY TREE FIXTURE GOES THROUGH THE LOADER (`_loaded`). A node the grammar refuses —
a `placement` node carrying an anchor, say — must fail right here, or a cell goes
green on a tree a live config can never produce. That is the second half of Т6.1.
"""
from types import SimpleNamespace

import pytest

from gui.docks import trees_mounts_launch as launch
from kicadstamp.exceptions import ValidationError
from kicadstamp.trees import (Tree, TreeAnchor, TreeNode, tree_from_dict,
                              tree_to_dict)


# ── fixtures: built by the product's own loader ───────────────────────────

def _loaded(tree: Tree) -> Tree:
    """The loader's own verdict on a fixture tree — an impossible node fails HERE."""
    return tree_from_dict(tree_to_dict(tree))


def _tree(*nodes) -> Tree:
    return _loaded(Tree(name="fpga", anchor=TreeAnchor(is_origin=True),
                        nodes=list(nodes)))


def _placement(ref="FPGA_POWER"):
    """A CELL-RECORD node: no anchor, its address lives in the record."""
    return TreeNode(ref=ref, kind="placement", xy=(0.0, 0.0), polar=None,
                    rotation=0.0, name=None, group=None, children=[])


def _mount(ref="M1", anchor=None):
    """A mount node — the grammar REQUIRES its role anchor ("a mount node is a point
    of reference and places nothing itself"), and it is one of the two kinds that
    accept a nested `(anchor …)` at all."""
    return TreeNode(ref=ref, kind="mount", xy=(0.0, 0.0), polar=None,
                    rotation=0.0, name=None, group=None, children=[],
                    anchor=anchor or TreeAnchor(role="FPGA", anchor_sheet="FPGA",
                                                anchor_cluster="FPGA"))


def _component(ref="FPGA", anchor=None):
    return TreeNode(ref=ref, kind="component", xy=(0.0, 0.0), polar=None,
                    rotation=0.0, name=None, group=None, children=[], anchor=anchor)


def _dock(cfg=None, connection=None, sheet_names=None):
    """The three things the launcher reads off a dock, and nothing else."""
    return SimpleNamespace(
        _cfg=cfg, _main_window=SimpleNamespace(connection=connection),
        _ctx=SimpleNamespace(sheet_names=sheet_names or {}))


def _cfg(*entities, cells=None):
    return SimpleNamespace(entities=list(entities), clone_placements=[],
                           cells=cells or {})


def _entity(name, cell, cluster=None, sheet=None):
    return SimpleNamespace(name=name, cell=cell, cluster=cluster, sheet=sheet,
                           uuid=None)


def _cell(*roles):
    return SimpleNamespace(components=[{"role": role} for role in roles])


def _captured(monkeypatch):
    """What `open_mounts_dialog` was called with (the window itself is stubbed)."""
    seen: dict = {}

    def _open(parent, **kwargs):
        seen["parent"] = parent
        seen.update(kwargs)
        return object()          # a window; the launcher must not keep it

    monkeypatch.setattr(launch, "open_mounts_dialog", _open)
    return seen


class _Signal:
    def __init__(self):
        self.slots = []

    def connect(self, slot):
        self.slots.append(slot)


class _Action:
    def __init__(self, text):
        self.text = text
        self.triggered = _Signal()

    def trigger(self):
        for slot in self.triggered.slots:
            slot()


class _Menu:
    """A QMenu stand-in that records what was added and can fire it."""

    def __init__(self):
        self.added = []

    def addAction(self, text):
        action = _Action(text)
        self.added.append(action)
        return action


# ── Д1: which parent ──────────────────────────────────────────────────────

def test_the_root_passes_no_parent(monkeypatch):
    dock = _dock()
    seen = _captured(monkeypatch)
    tree = _tree()
    assert launch.open_mounts_for(dock, tree) is None, "the window must not be retained"
    assert seen["parent"] is dock
    assert seen["tree_name"] == "fpga"
    assert seen["parent_ref"] is None


def test_a_node_passes_its_ref_as_the_parent(monkeypatch):
    seen = _captured(monkeypatch)
    launch.open_mounts_for(_dock(), _tree(_placement("FPGA_POWER")), _placement())
    assert seen["parent_ref"] == "FPGA_POWER"


# ── Д1а, spelling 1: the node carries a role anchor ───────────────────────

def test_a_role_anchor_prefills_the_address(monkeypatch):
    seen = _captured(monkeypatch)
    anchor = TreeAnchor(role="FPGA", anchor_sheet="FPGA", anchor_cluster="FPGA")
    node = _mount(anchor=anchor)
    launch.open_mounts_for(_dock(), _tree(node), node)
    assert (seen["role"], seen["sheet"], seen["cluster"]) == ("FPGA", "FPGA", "FPGA")


def test_another_anchor_prefills_nothing(monkeypatch):
    """A component node may be anchored by a RECORD ref — that names no instance
    address, so the fields stay empty (Д2: a mount anchor is always a role anchor)."""
    seen = _captured(monkeypatch)
    launch.open_mounts_for(_dock(), _tree(_component(anchor=TreeAnchor(ref="N1"))),
                           _component())
    assert (seen["role"], seen["sheet"], seen["cluster"]) == (None, None, None)


# ── Д1а, spelling 2: a cell-record node — the RECORD is the address ───────

def test_a_placement_record_prefills_cluster_and_sheet(monkeypatch):
    """Т6.1: the node Д1а was written for carries NO anchor; the record does the work."""
    cfg = _cfg(_entity("FPGA_POWER", "fpga_cell", cluster="FPGA", sheet="FPGA"),
               cells={"fpga_cell": _cell("FPGA", "VCCAUX")})
    seen = _captured(monkeypatch)
    launch.open_mounts_for(_dock(cfg=cfg), _tree(_placement()), _placement())
    assert (seen["sheet"], seen["cluster"]) == ("FPGA", "FPGA")
    assert seen["role"] is None, "two roles — nothing to choose for the user"


def test_the_record_name_need_not_be_the_cell_name(monkeypatch):
    """The hints come from `record.cell`, never from `cfg.cells[node.ref]` — and the
    decoy is deliberate: a cell CALLED like the record sits in the config with the
    wrong roles, and the answer must come from `fpga_cell`."""
    cfg = _cfg(_entity("FPGA_POWER", "fpga_cell", cluster="FPGA"),
               cells={"FPGA_POWER": _cell("WRONG"),
                      "fpga_cell": _cell("FPGA", "VCCAUX")})
    seen = _captured(monkeypatch)
    launch.open_mounts_for(_dock(cfg=cfg), _tree(_placement()), _placement())
    assert seen["role_choices"] == ["FPGA", "VCCAUX"]


def test_a_cell_with_one_role_fills_the_role(monkeypatch):
    cfg = _cfg(_entity("DAC_BUF", "dac_cell", cluster="Channel_0", sheet="Channel_0"),
               cells={"dac_cell": _cell("DAC_BUF")})
    seen = _captured(monkeypatch)
    launch.open_mounts_for(_dock(cfg=cfg), _tree(_placement("DAC_BUF")), _placement("DAC_BUF"))
    assert (seen["role"], seen["cluster"], seen["sheet"]) == (
        "DAC_BUF", "Channel_0", "Channel_0")


def test_a_missing_record_prefills_nothing(monkeypatch):
    seen = _captured(monkeypatch)
    launch.open_mounts_for(_dock(cfg=_cfg()), _tree(_placement()), _placement())
    assert (seen["role"], seen["sheet"], seen["cluster"]) == (None, None, None)
    assert seen["role_choices"] == []


# ── the Role candidates ───────────────────────────────────────────────────

def test_the_choices_are_the_cells_roles_in_its_own_order_once(monkeypatch):
    """`cell_role_order` repeats a role per SLOT; a picker that says «FPGA» thirty
    times helps nobody, so the launcher drops the repeats and keeps the order."""
    cfg = _cfg(_entity("FPGA_POWER", "fpga_cell"),
               cells={"fpga_cell": _cell("VCCAUX", "FPGA", "VCCAUX")})
    seen = _captured(monkeypatch)
    launch.open_mounts_for(_dock(cfg=cfg), _tree(_placement()), _placement())
    assert seen["role_choices"] == ["VCCAUX", "FPGA"]


def test_a_node_that_names_no_record_offers_no_choices(monkeypatch):
    """A mount node names no `placement` record, so no cell — and no hints."""
    cfg = _cfg(cells={"fpga_cell": _cell("FPGA")})
    seen = _captured(monkeypatch)
    node = _mount()
    launch.open_mounts_for(_dock(cfg=cfg), _tree(node), node)
    assert seen["role_choices"] == []


def test_the_root_offers_no_choices(monkeypatch):
    cfg = _cfg(cells={"fpga": _cell("R")})
    seen = _captured(monkeypatch)
    launch.open_mounts_for(_dock(cfg=cfg), _tree())
    assert seen["role_choices"] == []


# ── the menu item: offered, or honestly withheld ──────────────────────────

def test_the_item_is_added_and_opens_the_table(monkeypatch):
    seen = _captured(monkeypatch)
    menu = _Menu()
    tree = _tree(_placement())
    action = launch.add_mounts_item(menu, _dock(), tree, _placement())
    assert action is not None
    assert [a.text for a in menu.added] == ["Add mounts…"]
    action.trigger()
    assert seen["parent_ref"] == "FPGA_POWER"


def test_a_copper_container_offers_no_item():
    """The container may hold only `net_trace` (the loader fatals on anything else),
    so mounts under it are refused on OK — after the user ticked pads. Do not offer."""
    menu = _Menu()
    copper = TreeNode(ref="copper", kind="copper", xy=(0.0, 0.0), polar=None,
                      rotation=0.0, name=None, group=None, children=[])
    assert launch.add_mounts_item(menu, _dock(), _tree(), copper) is None
    assert menu.added == []


# ── the fixture rule itself ───────────────────────────────────────────────

def test_a_placement_node_with_an_anchor_is_refused_by_the_loader():
    """WHY the Т6 fixtures were wrong: the grammar allows a nested anchor on `mount`
    and `component` only, so `placement + anchor` is a tree that cannot exist. Every
    fixture in this file therefore goes through `_loaded`."""
    bad = Tree(name="t", anchor=TreeAnchor(is_origin=True),
               nodes=[_placement()])
    bad.nodes[0].anchor = TreeAnchor(role="FPGA")
    with pytest.raises(ValidationError):
        tree_from_dict(tree_to_dict(bad))


# ── the dock's own state, and the window that is not kept ─────────────────

def test_the_window_is_built_on_the_docks_state(monkeypatch):
    connection = object()
    cfg = _cfg()
    names = {"/uuid/": "FPGA"}
    seen = _captured(monkeypatch)
    dock = _dock(cfg=cfg, connection=connection, sheet_names=names)
    launch.open_mounts_for(dock, _tree(_placement()), _placement())
    assert seen["connection"] is connection
    assert seen["cfg"] is cfg
    assert seen["sheet_names"] == names


def test_no_connection_is_not_refused_here(monkeypatch):
    """The window says «the mounts table cannot be read: there is no board
    connection» on «Read pads» — in the window AND the Log, where the user looks.
    Refusing HERE would leave the click silent (a Log line behind a closed window)."""
    seen = _captured(monkeypatch)
    launch.open_mounts_for(_dock(), _tree(), None)
    assert seen["connection"] is None
    assert "tree_name" in seen, "the window must still open"
