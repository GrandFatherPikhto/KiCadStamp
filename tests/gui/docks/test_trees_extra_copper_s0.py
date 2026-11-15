# tests/gui/docks/test_trees_extra_copper_s0.py
"""§0 п.6 of plan_2026_11_10_extra_copper_by_selection — the door.

`TreesDock._prompt_node` renames a NEW node's free-typed ref (auto-numbering,
phase 5.5) AFTER `build_node` has accepted the form, so the auto-number could
land on a LOCAL name of the target tree (a copper container here: its ban stays
after plan_2026_11_14_mount_and_record_may_share_a_name lifts the mount's) and
the next `load_config` would refuse the file. The door now asks THE rule
(`trees_local_refs.free_node_ref`) and keeps counting until the rule allows the
name.

§0.1 (the acceptance of `38bff313`) gave that rule the second half of the SAME
question — the names a node ALREADY holds — so the auto-number can no longer
land on one of them and let the next Save fatal on `link_trees`.
"""
from PyQt6.QtWidgets import QDialog

from gui.docks.trees_dock import TreesDock, _NodeDialog
from kicadstamp.config.sexp_format import dict_to_sexp
from kicadstamp.trees import TreeNode, tree_from_dict, tree_to_dict

CFG = {
    "cells": {"cell_base": {}},
    "entities": [{"name": "ENT_A", "cluster": "c", "cell": "cell_base"}],
    "trees": [{"name": "t1", "anchor": {"origin": True}, "nodes": []}],
}


def _dock(main_window, tmp_path):
    root = tmp_path / "root.sexp"
    root.write_text(dict_to_sexp(CFG, format_number=2), encoding="utf-8")
    dock = TreesDock(main_window)
    dock.set_root_file(root)
    assert dock._cfg is not None
    return dock, root


def _add(dock, ref, kind):
    node = TreeNode(ref=ref, kind=kind, xy=(0.0, 0.0), polar=None, rotation=0.0,
                    name=None, group=None, children=[])
    dock._trees[0].nodes.append(node)
    return node


def test_the_auto_number_skips_a_local_name_of_the_same_tree(
        main_window, tmp_path, monkeypatch):
    """`GND` is taken by an external node, so the door must number it — and a
    copper container already carries `GND_1`, which the loader forbids. ONE rule
    counts `_1`, `_2`, … from the BASE ref and skips both bans, so the answer is
    `GND_2` (before §0.1 the two-step door gave the chained `GND_1_2`), and the
    resulting tree must load."""
    dock, _root = _dock(main_window, tmp_path)
    _add(dock, "GND", "external")
    _add(dock, "GND_1", "copper")
    tree = dock._trees[0]

    built = TreeNode(ref="GND", kind="external", xy=(1.0, 1.0), polar=None,
                     rotation=0.0, name=None, group=None)
    monkeypatch.setattr(_NodeDialog, "exec",
                        lambda self: QDialog.DialogCode.Accepted)
    monkeypatch.setattr(_NodeDialog, "build_node", lambda self: built)
    # The cell is about the NAME the door hands out: staging/repaint would write a
    # format-3 file, which needs the project graph this headless fixture has none of.
    monkeypatch.setattr(dock, "_stage_trees", lambda: None)
    monkeypatch.setattr(dock, "_rebuild_tabs", lambda: None)

    dock._add_node_flow(tree)

    assert tree.nodes[-1].ref == "GND_2"
    tree_from_dict(tree_to_dict(tree))       # the loader's local-name rule passes


def test_the_auto_number_avoids_a_name_a_node_already_holds(
        main_window, tmp_path, monkeypatch):
    """§0.1 дыра 2, the door's half: `x_1` is a LOCAL name of the tree (a copper
    container) and `x_1_1` is a name another node already carries. The old door
    asked `_unique_ref` first, got `x_1` back, and the local half then pushed it
    onto a suffix WITHOUT looking at the used names — a name the next Save
    refuses. The one rule must skip both: neither `x_1` nor `x_1_1`, but `x_1_2`
    (the FIRST suffix it may take is exactly the taken one, so a rule that
    forgets `taken` cannot pass by luck)."""
    dock, _root = _dock(main_window, tmp_path)
    _add(dock, "x_1", "copper")
    _add(dock, "x_1_1", "external")
    tree = dock._trees[0]

    built = TreeNode(ref="x_1", kind="external", xy=(1.0, 1.0), polar=None,
                     rotation=0.0, name=None, group=None)
    monkeypatch.setattr(_NodeDialog, "exec",
                        lambda self: QDialog.DialogCode.Accepted)
    monkeypatch.setattr(_NodeDialog, "build_node", lambda self: built)
    monkeypatch.setattr(dock, "_stage_trees", lambda: None)
    monkeypatch.setattr(dock, "_rebuild_tabs", lambda: None)

    dock._add_node_flow(tree)

    assert tree.nodes[-1].ref == "x_1_2"
    assert tree.nodes[-1].ref not in {"x_1", "x_1_1"}
    tree_from_dict(tree_to_dict(tree))       # the loader's rules both pass
