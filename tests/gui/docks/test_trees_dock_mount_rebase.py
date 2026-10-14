# tests/gui/docks/test_trees_dock_mount_rebase.py
"""Сторожа Ч2 of plan_2026_10_14_tree_group_move_and_mount_rebase: "Change
anchor from selection…" on a mount node, WITH the angle addendum
("смена опоры сама по себе группу не поворачивает").

This file owns its own fixture (a mount node with two record children and one
child-mount, loaded from a written config), so the cells call the real flow
through the dock and read the numbers off the real tree.

WHAT IS PINNED DOWN HERE:
  * "Group stays in place" — the mount's absolute BOARD pose is the same before
    and after, POSITION AND ANGLE (the new support stands at 90°, so the stored
    numbers really change; the place does not);
  * "Group moves to the new anchor" — the group travels with the anchor WITHOUT
    turning: its stored offset keeps meaning the same board vector, so the
    absolute angle is the same and the whole group is translated by the anchor's
    displacement;
  * the OLD anchor gone (the case the action exists for) — the mount's numbers
    stay as they are, every DIRECT child is re-read off the board against the
    node's new pose, a child-mount with its own anchor is left alone, and the Log
    says BOTH facts (the axes and the source);
  * ONE child the board cannot answer for removes "Group stays in place" from the
    list and names the reason — the row is not offered at all (Ч2 п.7);
  * a selection that is not exactly one footprint or one pad, and a NEW anchor
    the loader refuses (ambiguity/cycle), are ONE Log line each with the tree
    byte for byte as it was;
  * the form is reloaded for the new anchor and the config is marked changed —
    and the board is only READ (the fake adapter is never asked to write).

NOT covered here, named rather than implied: the live-board behaviour of the
reads (Denis's live checks), the cost of the resolves (the signs name the class
of cost the re-hang's own probe measured) and the entity page's Anchor tab,
whose cells live in tests/gui/docks/test_anchor_tab.py.
"""
from types import SimpleNamespace

import pytest
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtWidgets import QInputDialog

from kicadstamp.config.sexp_format import dict_to_sexp
from kicadstamp.domain.board import Footprint, Via
from kicadstamp.domain.geometry import Vector2
from kicadstamp.sheet_names import ROOT_SHEET
from kicadstamp.tree_position import (local_offset_to_board_mm,
                                      local_rotation_to_board_deg)
from kicadstamp.trees import tree_to_sexp
from kicadstamp.utils.units import MM

import gui.docks.trees_dock as trees_dock_mod
from gui.docks.trees_dock import TreesDock
from gui.docks.trees_mount_rebase import (_AXES_FROM_NEW_ANCHOR,
                                          _CHILDREN_UNREADABLE,
                                          _OLD_ANCHOR_GONE, _SELECT_ONE)

from tests.fakes.write_later import write_later

#: The base of each mount anchor in this fixture, in board mm / degrees. The old
#: support stands straight, the new one stands TURNED 90° — that is what the
#: angle addendum is about.
OLD_BASE_MM, OLD_BASE_DEG = 10.0, 0.0
NEW_BASE_MM, NEW_BASE_DEG = 30.0, 90.0

#: The mount node's own numbers: 2 mm from its anchor, no turn of its own.
MOUNT_XY = (2.0, 0.0)


def _row_node(dock, ref):
    """The TreeNode a ROW carries — what the flow reads from the selection."""
    return dock._node_items[ref].data(0, Qt.ItemDataRole.UserRole)


def _dock_with(main_window, tmp_path):
    """A TreesDock whose tree is ONE mount node with two record children and one
    child-mount — the shape a re-anchor is asked for."""
    root = tmp_path / "root.sexp"
    write_later(root, dict_to_sexp({
        "trees": [{
            "name": "rebase_tree", "anchor": {"origin": True},
            "nodes": [
                {"ref": "MNT", "kind": "mount", "xy": list(MOUNT_XY),
                 "anchor": {"role": "OLD_MOUNT"},
                 "children": [
                     {"ref": "R_ONE", "kind": "external", "xy": [3.0, 0.0]},
                     {"ref": "R_TWO", "kind": "external", "xy": [4.0, 0.0]},
                     {"ref": "M_CH", "kind": "mount", "xy": [1.0, 0.0],
                      "anchor": {"role": "INNER"}},
                 ]},
            ]}]}, format_number=2))
    dock = TreesDock(main_window)
    dock.set_root_file(root)
    assert dock._cfg is not None, "the throwaway root config must load"
    return dock


def _bases(monkeypatch, *, old_gone=False, new_fails=None):
    """Pin BOTH anchor poses by ROLE: the tree's own anchor answers (0, 0), the
    old support (10 mm, 0°) and the new one (30 mm, 90°)."""
    def _fake(cfg, adapter, sheet_names, tree, parent_node, base_anchor=None,
              *args, **kwargs):
        role = getattr(getattr(parent_node, "anchor", None), "role", None)
        if role == "OLD_MOUNT" and old_gone:
            raise trees_dock_mod.ValidationError(
                "the old mount's anchor is not on the board")
        if role == "NEW_MOUNT" and new_fails:
            raise trees_dock_mod.ValidationError(new_fails)
        if role == "NEW_MOUNT":
            return (Vector2.from_xy(int(round(NEW_BASE_MM * MM)), 0),
                    NEW_BASE_DEG, False)
        if role == "OLD_MOUNT" or role == "INNER":
            return (Vector2.from_xy(int(round(OLD_BASE_MM * MM)), 0),
                    OLD_BASE_DEG, False)
        return (Vector2.from_xy(0, 0), 0.0, False)

    monkeypatch.setattr(trees_dock_mod, "_resolve_node_base_pose", _fake)


def _footprint(role="NEW_MOUNT", cluster="CL_NEW", ref="U1", sheet_chain=()):
    fp = Footprint(ref=ref, uuid="fp-" + ref, position=Vector2.from_xy(0, 0),
                   angle_deg=0.0, layer="F.Cu")
    fp.role = role
    fp.cluster = cluster
    fp.sheet_path_uuids = list(sheet_chain)
    return fp


class _Board:
    """The duck-typed adapter the flow reads the SELECTION through."""

    def __init__(self, items=()):
        self.items = list(items)

    def get_selected_items(self):
        return list(self.items)

    def get_footprints(self):
        return [item for item in self.items if isinstance(item, Footprint)]

    def get_field_value(self, fp, field_name):
        return {"Role": getattr(fp, "role", None),
                "Cluster": getattr(fp, "cluster", None)}.get(field_name)

    def get_footprint_pads(self, fp):
        return []


def _select(monkeypatch, dock, items):
    """Give the dock a live adapter whose selection is `items`."""
    board = _Board(items)
    monkeypatch.setattr(dock, "_live_adapter", lambda: board)
    return board


def _pick(monkeypatch, label=None, record=None):
    """Answer the picker. `label=None` takes the DEFAULT row (index 0) — which is
    how the cells check that "stays in place" really is the default."""
    def _fake_get_item(parent, title, prompt, labels, current, editable):
        if record is not None:
            record["labels"] = list(labels)
            record["current"] = current
        return (labels[current] if label is None else label), True

    monkeypatch.setattr(QInputDialog, "getItem", _fake_get_item)


def _abs_pose(node, base_mm, base_deg):
    """The node's absolute BOARD pose from its stored numbers — the frame a
    redraw lays it from (the same two primitives the product uses)."""
    bx, by = local_offset_to_board_mm((node.xy[0], node.xy[1]), base_deg)
    return ((base_mm + bx, by),
            local_rotation_to_board_deg(node.rotation, base_deg))


def _reloads(dock, monkeypatch):
    """Record the forced form reloads (Ч2 п.4)."""
    seen: list = []
    monkeypatch.setattr(dock, "_reload_node_form", lambda: seen.append(True))
    return seen


# ── "Group stays in place" (the angle addendum's first branch) ────────────

def test_stays_in_place_keeps_the_absolute_pose_and_the_angle(
        main_window, tmp_path, monkeypatch):
    """The heart of Ч2: the new support stands at 90° to the old one, so the
    numbers MUST change — and the place must not, angle included.

    Mutation check: re-express with the new base's POSITION but not its angle and
    the absolute angle below comes back +90° out; skip the re-expression entirely
    and the place itself jumps by 20 mm."""
    dock = _dock_with(main_window, tmp_path)
    tree = dock._current_tree()
    mount = tree.nodes[0]
    _bases(monkeypatch)
    _select(monkeypatch, dock, [_footprint()])
    _pick(monkeypatch)
    reloaded = _reloads(dock, monkeypatch)
    before = _abs_pose(mount, OLD_BASE_MM, OLD_BASE_DEG)

    dock._mount_anchor_flow(tree, _row_node(dock, "MNT"))

    assert _abs_pose(mount, NEW_BASE_MM, NEW_BASE_DEG) == before, \
        "the mount must not move on the board — position AND angle"
    assert (mount.xy, mount.rotation) == ((-0.0, -18.0), -90.0), \
        "the numbers are re-expressed through the new base"
    assert mount.anchor.role == "NEW_MOUNT"
    assert mount.anchor.anchor_cluster == "CL_NEW"
    assert mount.anchor.anchor_sheet == ROOT_SHEET, \
        "a component on the root sheet is addressed by the root marker"
    assert mount.anchor.anchor_pad is None
    assert [child.xy for child in mount.children] == [(3.0, 0.0), (4.0, 0.0),
                                                     (1.0, 0.0)], \
        "the children ride on the mount's pose and are not touched"
    assert dock._dirty is True
    assert reloaded == [True], "the panel must be re-filled for the new anchor"


def test_moves_translates_the_group_and_does_not_turn_it(
        main_window, tmp_path, monkeypatch):
    """"Group moves to the new anchor" is a MOVE WITHOUT A TURN (the angle
    addendum): the stored offset keeps meaning the same board vector from the
    anchor, so the whole group travels by the anchor's displacement (20 mm here)
    and its absolute angle is untouched.

    Mutation check: re-express it like "stays in place" and the group does not
    move at all; keep the positions and drop the angle and it arrives turned 90°.
    """
    dock = _dock_with(main_window, tmp_path)
    tree = dock._current_tree()
    mount = tree.nodes[0]
    _bases(monkeypatch)
    _select(monkeypatch, dock, [_footprint()])
    _pick(monkeypatch, "Group moves to the new anchor")
    _reloads(dock, monkeypatch)
    before_pos, before_deg = _abs_pose(mount, OLD_BASE_MM, OLD_BASE_DEG)

    dock._mount_anchor_flow(tree, _row_node(dock, "MNT"))

    after_pos, after_deg = _abs_pose(mount, NEW_BASE_MM, NEW_BASE_DEG)
    assert after_pos == (before_pos[0] + (NEW_BASE_MM - OLD_BASE_MM),
                         before_pos[1]), \
        "the group travels with the anchor, by its displacement"
    assert after_deg == before_deg, "…and does not turn"
    assert (mount.xy, mount.rotation) == ((-0.0, 2.0), -90.0)
    assert mount.anchor.role == "NEW_MOUNT"


# ── the OLD anchor is gone (Д1 of the Ч1 acceptance) ─────────────────────

def test_the_old_anchor_gone_rereads_every_direct_child(
        main_window, tmp_path, monkeypatch, caplog):
    """The case the action exists for. The old support is not on the board any
    more, so nothing can hold the mount still: its numbers stay as they are, its
    axes become the new anchor's (SAID in the Log, because the numbers do not
    show it), and both record children are re-read against the new pose. The
    child-MOUNT is skipped — its frame is its own anchor.

    Mutation check: write the children's numbers only, without reading them, and
    the re-read values below never appear; drop the axes line and the Log
    assertion fails while the rest still passes."""
    dock = _dock_with(main_window, tmp_path)
    tree = dock._current_tree()
    mount = tree.nodes[0]
    _bases(monkeypatch, old_gone=True)
    _select(monkeypatch, dock, [_footprint()])
    _pick(monkeypatch)

    def _read_live(cfg, adapter, sheet_names, tree_, parent_node, ref, kind,
                   component_address=None, *args, **kwargs):
        if ref == "M_CH":
            raise AssertionError("a child-mount must not be re-read")
        return (({"R_ONE": 13.0, "R_TWO": 14.0}[ref] - NEW_BASE_MM, 0.0), 0.0)
    monkeypatch.setattr(trees_dock_mod, "_resolve_live_offset", _read_live)
    _reloads(dock, monkeypatch)
    numbers = (mount.xy, mount.polar, mount.rotation)

    caplog.clear()
    dock._mount_anchor_flow(tree, _row_node(dock, "MNT"))

    assert (mount.xy, mount.polar, mount.rotation) == numbers, \
        "the mount's own numbers are NOT touched when there is no old place"
    assert [child.xy for child in mount.children] == \
        [(-17.0, 0.0), (-16.0, 0.0), (1.0, 0.0)], \
        "both record children were re-read against the new pose; the child-mount \
was left alone"
    assert mount.anchor.role == "NEW_MOUNT"
    told = [r.message for r in caplog.records]
    assert _AXES_FROM_NEW_ANCHOR.format(mount="MNT", angle="90") in told, \
        f"the axes change must be said — got {told!r}"
    assert _OLD_ANCHOR_GONE.format(mount="MNT", n=2) in told, \
        f"the source of the children's numbers must be said — got {told!r}"


def test_a_child_that_cannot_be_read_withholds_stays_in_place(
        main_window, tmp_path, monkeypatch, caplog):
    """Ч2 п.7: "stays in place" is honoured by re-reading every direct child, so
    ONE child of a kind that has no detail on the board takes the row OUT of the
    list — and the reason is in the Log, not in a silent missing option.

    Mutation check: offer the row anyway and `labels` below carries both."""
    dock = _dock_with(main_window, tmp_path)
    tree = dock._current_tree()
    mount = tree.nodes[0]
    _bases(monkeypatch, old_gone=True)
    _select(monkeypatch, dock, [_footprint()])
    record: dict = {}
    _pick(monkeypatch, "Group moves to the new anchor", record)

    def _read_live(cfg, adapter, sheet_names, tree_, parent_node, ref, kind,
                   component_address=None, *args, **kwargs):
        if ref == "M_CH":
            raise AssertionError("a child-mount must not be re-read")
        if ref == "R_TWO":
            raise trees_dock_mod.ValidationError("no live record for R_TWO")
        return ((13.0 - NEW_BASE_MM, 0.0), 0.0)
    monkeypatch.setattr(trees_dock_mod, "_resolve_live_offset", _read_live)
    _reloads(dock, monkeypatch)
    before = tree_to_sexp(tree)

    caplog.clear()
    dock._mount_anchor_flow(tree, _row_node(dock, "MNT"))

    assert record["labels"] == ["Group moves to the new anchor"], \
        f"the unhonourable row must not be offered — got {record['labels']!r}"
    told = [r.message for r in caplog.records]
    assert any("R_TWO" in m and "stays in place" in m for m in told), \
        f"the reason must be in the Log — got {told!r}"
    assert mount.anchor.role == "NEW_MOUNT"
    assert [child.xy for child in mount.children] == [(3.0, 0.0), (4.0, 0.0),
                                                     (1.0, 0.0)], \
        "nothing was re-read: a half re-read group must not exist"
    assert tree_to_sexp(tree) != before, "…and the anchor itself did change"


# ── the selection (Ч2 п.1) ───────────────────────────────────────────────

@pytest.mark.parametrize("what", ["two footprints", "a via"])
def test_a_selection_the_action_cannot_use_is_one_log_line(
        main_window, tmp_path, monkeypatch, caplog, what):
    """Exactly ONE footprint or ONE pad: two footprints are ambiguous, a track or
    a via is not a component at all — one Log line, and the config is not
    touched."""
    dock = _dock_with(main_window, tmp_path)
    tree = dock._current_tree()
    if what == "two footprints":
        items = [_footprint(ref="U1"), _footprint(ref="U2")]
    else:
        items = [Via(uuid="v1", position=Vector2.from_xy(0, 0), net_name=None,
                     drill_mm=0.3, diameter_mm=0.6)]
    _bases(monkeypatch)
    _select(monkeypatch, dock, items)
    _pick(monkeypatch, record={})
    reloaded = _reloads(dock, monkeypatch)
    before = tree_to_sexp(tree)

    caplog.clear()
    dock._mount_anchor_flow(tree, _row_node(dock, "MNT"))

    assert tree_to_sexp(tree) == before, "a refused selection changes nothing"
    assert dock._dirty is False and reloaded == []
    told = [r.message for r in caplog.records]
    assert _SELECT_ONE in told, f"one line must name the shape that works — {told!r}"


def test_nothing_selected_is_told_in_the_shared_halves_own_words(
        main_window, tmp_path, monkeypatch, caplog):
    """An empty selection goes through the SHARED classification (Ч2-1), so its
    refusal is the wording every anchor read has always used — and the action's
    own label rides in it, which is how the user knows WHERE the refusal came
    from."""
    dock = _dock_with(main_window, tmp_path)
    tree = dock._current_tree()
    _bases(monkeypatch)
    _select(monkeypatch, dock, [])
    _pick(monkeypatch, record={})
    _reloads(dock, monkeypatch)
    before = tree_to_sexp(tree)

    caplog.clear()
    dock._mount_anchor_flow(tree, _row_node(dock, "MNT"))

    assert tree_to_sexp(tree) == before
    told = [r.message for r in caplog.records]
    assert any("nothing is selected" in m and "mount anchor" in m for m in told), \
        f"got {told!r}"


# ── the NEW anchor must resolve (Ч2 п.2) ─────────────────────────────────

def test_a_new_anchor_the_loader_refuses_is_named_and_changes_nothing(
        main_window, tmp_path, monkeypatch, caplog):
    """Ambiguity, a module crossing and a cycle are the LOADER's own verdicts,
    raised by the resolver with its own words — the action only carries them into
    the Log and leaves the node alone. The cell stands in for all three: the
    wording is the resolver's and this action never invents one."""
    dock = _dock_with(main_window, tmp_path)
    tree = dock._current_tree()
    mount = tree.nodes[0]
    _bases(monkeypatch, new_fails="the role is placed in more than one cell")
    _select(monkeypatch, dock, [_footprint()])
    _pick(monkeypatch, record={})
    reloaded = _reloads(dock, monkeypatch)
    before = tree_to_sexp(tree)

    caplog.clear()
    dock._mount_anchor_flow(tree, _row_node(dock, "MNT"))

    assert tree_to_sexp(tree) == before, "a refused new anchor changes nothing"
    assert mount.anchor.role == "OLD_MOUNT"
    assert dock._dirty is False and reloaded == []
    told = [r.message for r in caplog.records]
    assert any("more than one cell" in m for m in told), f"got {told!r}"


def test_the_anchor_is_not_touched_when_the_new_one_does_not_resolve(
        main_window, tmp_path, monkeypatch):
    """The new anchor is put on the node IN PLACE while it is resolved (the
    loader's cycle check needs the node's real place in the tree) — and it must be
    taken off again on the failure path, or a refused action would leave the node
    pointing at a support that does not resolve."""
    dock = _dock_with(main_window, tmp_path)
    tree = dock._current_tree()
    mount = tree.nodes[0]
    _bases(monkeypatch, new_fails="cycle")
    _select(monkeypatch, dock, [_footprint()])
    _pick(monkeypatch, record={})

    dock._mount_anchor_flow(tree, _row_node(dock, "MNT"))

    assert mount.anchor.role == "OLD_MOUNT"
    assert (mount.xy, mount.rotation) == (MOUNT_XY, 0.0)


# ── Ч2 п.4: the form shows the new anchor, the config is dirty ───────────

def test_the_node_form_shows_the_new_anchor_after_the_command(
        main_window, tmp_path, monkeypatch):
    """The panel's IDENTITY is the node's ref, which does not change — so the
    ordinary selection-changed refresh would keep the STALE editor. This cell
    drives the real seam (`_reload_node_form`) and reads the anchor off the form
    that comes back: the mount picker must be in its anchor mode with the NEW
    role in it."""
    dock = _dock_with(main_window, tmp_path)
    tree = dock._current_tree()
    _bases(monkeypatch)
    _select(monkeypatch, dock, [_footprint()])
    _pick(monkeypatch)
    widget = dock._current_tree_widget()
    widget.clearSelection()
    dock._node_items["MNT"].setSelected(True)

    dock._mount_anchor_flow(tree, _row_node(dock, "MNT"))

    form = dock._build_node_form(tree, _row_node(dock, "MNT"))
    anchor_widget = form.mount_anchor_widget
    assert anchor_widget.mode == "anchor"
    assert anchor_widget.anchor_role_edit.currentText() == "NEW_MOUNT"
    assert dock._current_node_ref == "MNT"


# ── Ч2 п.1: the context menu carries the entry, for a mount node alone ────

def _context_menu_labels(dock, item, monkeypatch):
    """Runs _on_context_menu for `item` with QMenu.exec no-oped and returns the
    added labels in order (the idiom tests/gui/docks/test_trees_dock_copper_
    pseudo_node.py uses — the real QAction per label is what the dock connects)."""
    monkeypatch.setattr(trees_dock_mod.QMenu, "exec", lambda self, *a, **k: None)
    captured = []
    original = trees_dock_mod.QMenu.addAction

    def _record(self, text, *a, **k):
        captured.append(text)
        return original(self, text, *a, **k)

    monkeypatch.setattr(trees_dock_mod.QMenu, "addAction", _record)
    tree_widget = dock._current_tree_widget()
    monkeypatch.setattr(tree_widget, "itemAt", lambda pos: item)
    dock._on_context_menu(QPoint(0, 0))
    return captured


def test_the_menu_offers_the_action_for_a_mount_node_alone(
        main_window, tmp_path, monkeypatch):
    """Ч2 п.1: the entry belongs to a mount node — another kind has no anchor of
    its own to re-seat, so offering it there would be an action that cannot work.

    Mutation check: drop the kind test and the record node's row carries it too."""
    dock = _dock_with(main_window, tmp_path)

    labels = _context_menu_labels(dock, dock._node_items["MNT"], monkeypatch)
    assert trees_dock_mod._("Change anchor from selection…") in labels
    assert trees_dock_mod._("Move to…") in labels, "the existing action stays"

    labels = _context_menu_labels(dock, dock._node_items["R_ONE"], monkeypatch)
    assert trees_dock_mod._("Change anchor from selection…") not in labels
    assert trees_dock_mod._("Move to…") in labels
