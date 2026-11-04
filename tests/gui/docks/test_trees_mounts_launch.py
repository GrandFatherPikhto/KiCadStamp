# tests/gui/docks/test_trees_mounts_launch.py
"""Т6 of plan_2026_11_03_add_mounts_table — the ONE launcher both «Add mounts…»
menu items share (the dock adds two lines and decides nothing).

What is pinned here, with a stand-in dock and no board:

  * the PARENT (Д1): a click on the tree's root passes NO parent, a click on a node
    passes that node's ref — which is what the window remembers as `parent_ref`;
  * the ADDRESS (Д1а): a role anchor prefills Role/Sheet/Cluster; any other anchor —
    a record ref, a point, the origin, the self mode — prefills nothing, because a
    mount anchor is always a role anchor (Д2);
  * the Role CANDIDATES are the cell's own roles, in the cell's order, each listed
    once — read through the ONE owner `instance_candidates.cell_role_order`;
  * the window is built on the DOCK's state (connection, cfg, sheet names) and is not
    retained: the click owns no object that could go stale (§23).
"""
from types import SimpleNamespace

from gui.docks import trees_mounts_launch as launch
from kicadstamp.trees import Tree, TreeAnchor, TreeNode


class _Dock:
    """The three things the launcher reads off a dock, and nothing else."""

    def __init__(self, cfg=None, connection=None, sheet_names=None):
        self._cfg = cfg
        self._main_window = SimpleNamespace(connection=connection)
        self._ctx = SimpleNamespace(sheet_names=sheet_names or {})


def _node(ref="FPGA_A", anchor=None):
    return TreeNode(ref=ref, kind="placement", xy=(0.0, 0.0), polar=None,
                    rotation=0.0, name=None, group=None, children=[], anchor=anchor)


def _tree():
    return Tree(name="fpga", anchor=TreeAnchor(is_origin=True), nodes=[])


def _captured(monkeypatch):
    """What `open_mounts_dialog` was called with (the window itself is stubbed)."""
    seen: dict = {}

    def _open(parent, **kwargs):
        seen["parent"] = parent
        seen.update(kwargs)
        return object()          # a window; the launcher must not keep it

    monkeypatch.setattr(launch, "open_mounts_dialog", _open)
    return seen


# ── Д1: which parent ──────────────────────────────────────────────────────

def test_the_root_passes_no_parent(monkeypatch):
    dock = _Dock()
    seen = _captured(monkeypatch)
    tree = _tree()
    assert launch.open_mounts_for(dock, tree) is None, "the window must not be retained"
    assert seen["parent"] is dock
    assert seen["tree_name"] == "fpga"
    assert seen["parent_ref"] is None


def test_a_node_passes_its_ref_as_the_parent(monkeypatch):
    seen = _captured(monkeypatch)
    launch.open_mounts_for(_Dock(), _tree(), _node("FPGA_A"))
    assert seen["parent_ref"] == "FPGA_A"


# ── Д1а: the address the click already knows ──────────────────────────────

def test_a_role_anchor_prefills_the_address(monkeypatch):
    seen = _captured(monkeypatch)
    anchor = TreeAnchor(role="FPGA", anchor_sheet="FPGA", anchor_cluster="FPGA")
    launch.open_mounts_for(_Dock(), _tree(), _node("FPGA", anchor=anchor))
    assert (seen["role"], seen["sheet"], seen["cluster"]) == ("FPGA", "FPGA", "FPGA")


def test_another_anchor_prefills_nothing(monkeypatch):
    seen = _captured(monkeypatch)
    launch.open_mounts_for(_Dock(), _tree(), _node("N1", anchor=TreeAnchor(ref="N1")))
    assert (seen["role"], seen["sheet"], seen["cluster"]) == (None, None, None)


def test_a_node_without_an_anchor_prefills_nothing(monkeypatch):
    seen = _captured(monkeypatch)
    launch.open_mounts_for(_Dock(), _tree(), _node("N1"))
    assert (seen["role"], seen["sheet"], seen["cluster"]) == (None, None, None)


# ── Д1а: the Role candidates ──────────────────────────────────────────────

def test_the_choices_are_the_cells_roles_in_its_own_order_once(monkeypatch):
    """`cell_role_order` repeats a role per SLOT; a picker that says «FPGA» thirty
    times helps nobody, so the launcher drops the repeats and keeps the order."""
    cfg = SimpleNamespace(cells={"FPGA": SimpleNamespace(components=[
        {"role": "VCCAUX"}, {"role": "FPGA"}, {"role": "VCCAUX"}])})
    seen = _captured(monkeypatch)
    launch.open_mounts_for(_Dock(cfg=cfg), _tree(), _node("FPGA"))
    assert seen["role_choices"] == ["VCCAUX", "FPGA"]


def test_a_node_that_names_no_cell_offers_no_choices(monkeypatch):
    cfg = SimpleNamespace(cells={})
    seen = _captured(monkeypatch)
    launch.open_mounts_for(_Dock(cfg=cfg), _tree(), _node("FPGA"))
    assert seen["role_choices"] == []


def test_the_root_offers_no_choices(monkeypatch):
    cfg = SimpleNamespace(cells={"fpga": SimpleNamespace(components=[{"role": "R"}])})
    seen = _captured(monkeypatch)
    launch.open_mounts_for(_Dock(cfg=cfg), _tree())
    assert seen["role_choices"] == []


# ── the dock's own state, and the window that is not kept ─────────────────

def test_the_window_is_built_on_the_docks_state(monkeypatch):
    connection = object()
    cfg = SimpleNamespace(cells={})
    names = {"/uuid/": "FPGA"}
    seen = _captured(monkeypatch)
    dock = _Dock(cfg=cfg, connection=connection, sheet_names=names)
    launch.open_mounts_for(dock, _tree(), _node("FPGA_A"))
    assert seen["connection"] is connection
    assert seen["cfg"] is cfg
    assert seen["sheet_names"] == names


def test_no_connection_is_not_refused_here(monkeypatch):
    """The window says «the mounts table cannot be read: there is no board
    connection» on «Read pads» — in the window AND the Log, where the user looks.
    Refusing HERE would leave the click silent (a Log line behind a closed window)."""
    seen = _captured(monkeypatch)
    launch.open_mounts_for(_Dock(), _tree(), None)
    assert seen["connection"] is None
    assert "tree_name" in seen, "the window must still open"
