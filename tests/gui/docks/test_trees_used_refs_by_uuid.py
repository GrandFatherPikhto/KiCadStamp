#!/usr/bin/env python3
"""Cells а–ж of plan_2026_11_14_used_refs_by_uuid.md — the node form's
"already used" surface, measured by the RECORD's UUID instead of its name.

The defect (Denis, 14.11): in tree `fpga` the mounts `fpga_vccint_5`, … carry the
same names as the ENTITIES of cell `fpga_spoke`, so every one of those entities
was greyed in the node form and could not be added. Two causes, both covered here:

  * the GUI kept its OWN copy of the local-kind list and it was missing "mount"
    (cells а, д, ж);
  * occupancy was compared by BARE NAME, so two different records of one name
    took each other (cells б, в, г, г2).

The fixture has TWO trees on purpose: `t1` holds the node that occupies a name,
and the new node is added to `t2` — the shape of Denis's own case, where the
mount lives in `fpga` and the entity is needed elsewhere. What must NOT change:
a REAL repeat of the same record stays greyed and refused (cell в); a mount still
may not share its ref with a positioned node of ITS OWN tree
(`trees._validate_local_refs`, cell а's second half); and the loader's rule 2 —
"a ref appears in at most one node in the file" — still refuses a duplicate REF
for a ref that names no record at all.
"""
from __future__ import annotations

import pytest
from PyQt6.QtCore import Qt

import gui.docks.trees_dock as trees_dock_mod
from gui.docks.trees_dock import TreesDock, _NodeDialog
from kicadstamp.config import load_config
from kicadstamp.config.sexp_format import dict_to_sexp
from kicadstamp.exceptions import ValidationError
from kicadstamp.trees import (TreeAnchor, TreeNode, node_kind_label,
                              tree_from_dict, tree_to_dict)

#: Records enough for every cell: two sections sharing ONE name (SHARED_X as an
#: ENTITY and as a POINT), a CELL named like an ENTITY (SHARED_Y), and two plain
#: entities (ENT_SHARED, ENT_REAL).
USED_CFG = {
    "cells": {"SHARED_Y": {}, "cell_base": {}},
    "entities": [
        {"name": "ENT_SHARED", "cluster": "c", "cell": "cell_base"},
        {"name": "ENT_REAL", "cluster": "c", "cell": "cell_base"},
        {"name": "SHARED_X", "cluster": "c", "cell": "cell_base"},
        {"name": "SHARED_Y", "cluster": "c", "cell": "cell_base"},
    ],
    "points": {"SHARED_X": {"xy": [1.0, 1.0]}},
    "trees": [
        {"name": "t1", "anchor": {"origin": True}, "nodes": []},
        {"name": "t2", "anchor": {"origin": True}, "nodes": []},
    ],
}

#: t1 holds the occupier, t2 the node a cell adds.
T1 = 0
T2 = 1


def _dock(main_window, tmp_path):
    root = tmp_path / "root.sexp"
    root.write_text(dict_to_sexp(USED_CFG, format_number=2), encoding="utf-8")
    dock = TreesDock(main_window)
    dock.set_root_file(root)
    assert dock._cfg is not None, "the fixture config failed to load (see the Log)"
    return dock


def _uuid_of(dock, section: str, name: str) -> str:
    """The record's uuid as the LOADED config carries it (the format-2 -> 4
    upgrade stamps them)."""
    records = getattr(dock._cfg, section)
    records = records.values() if isinstance(records, dict) else records
    for rec in records:
        if getattr(rec, "name", None) == name:
            return rec.uuid
    raise AssertionError(f"no {section} record {name!r}")


def _mount_anchor() -> TreeAnchor:
    return TreeAnchor(role="FPGA_A", anchor_sheet="Root", anchor_cluster="fpga",
                      anchor_pad="5")


def _add_node(dock, ref: str, kind, *, ref_uuid=None, anchor=None,
              tree=T1) -> TreeNode:
    """A node as if it were ALREADY in that tree — appended straight onto the
    loaded buffer, so _used_ref_index() (which reads the buffer, exactly like the
    dump/save path) sees it."""
    node = TreeNode(ref=ref, kind=kind, xy=(0.0, 0.0), polar=None, rotation=0.0,
                    name=None, group=None, children=[], anchor=anchor,
                    ref_uuid=ref_uuid)
    dock._trees[tree].nodes.append(node)
    return node


def _dialog(dock, kind=None, existing=None, tree=T2):
    """An ADD-mode node dialog wired the way _prompt_node wires it (EDIT mode when
    `existing` is given — the only way to reach a kind the menu no longer offers,
    which shows as its "(retired)" row)."""
    dlg = _NodeDialog(
        dock, dock._all_ref_candidates(), dock._used_refs(), "Add node",
        cfg=dock._cfg, adapter=None, sheet_names={},
        tree=dock._trees[tree], parent_node=None, existing=existing,
        net_trace_candidates=dock._net_trace_record_candidates(),
        used_ref_index=dock._used_ref_index())
    if kind is not None:
        idx = dlg.kind_combo.findData(kind)
        assert idx >= 0, f"kind {kind!r} is not offered"
        dlg.kind_combo.setCurrentIndex(idx)
    return dlg


def _retired(kind: str) -> TreeNode:
    """An EXISTING node of a retired kind, so the Kind combo offers that kind's
    own row (and its Ref combo shows that kind's list)."""
    return TreeNode(ref="PROBE_NODE", kind=kind, xy=(0.0, 0.0), polar=None,
                    rotation=0.0, name=None, group=None, children=[])


def _row(dlg, text: str):
    row = dlg.ref_combo.findText(text)
    assert row >= 0, (f"{text!r} is not offered: "
                      f"{[dlg.ref_combo.itemText(i) for i in range(dlg.ref_combo.count())]}")
    return row


def _enabled(dlg, text: str) -> bool:
    item = dlg.ref_combo.model().item(_row(dlg, text))
    assert item is not None
    return item.isEnabled()


def _accepted(dlg, ref: str):
    """Type `ref` into the form and build it — the dialog must accept it."""
    dlg.ref_combo.setCurrentText(ref)
    return dlg.build_node()


def _refusal(monkeypatch, dlg, ref: str) -> str:
    """Type `ref`, build, and return the refusal text (the build must fail)."""
    warnings: list = []
    monkeypatch.setattr(trees_dock_mod.QMessageBox, "warning",
                        lambda *a, **k: warnings.append(a) or None)
    dlg.ref_combo.setCurrentText(ref)
    built = dlg.build_node()
    assert warnings, "build_node was ACCEPTED — expected a refusal"
    assert built is None
    return str(warnings[-1][2])


# ── а ───────────────────────────────────────────────────────────────────────
def test_a_a_mount_and_an_entity_of_one_name_do_not_block_each_other(
        main_window, tmp_path):
    """The live defect: the mount `ENT_SHARED` stands in t1, and the ENTITY of
    that very name must still be offered and accepted in t2 — a mount is a local
    name, so rule 2 (one ref, one node) does not apply to it."""
    dock = _dock(main_window, tmp_path)
    _add_node(dock, "ENT_SHARED", "mount", anchor=_mount_anchor())

    assert "ENT_SHARED" not in dock._used_refs()      # a mount is not a record
    dlg = _dialog(dock, "placement")
    assert _enabled(dlg, "ENT_SHARED") is True
    built = _accepted(dlg, "ENT_SHARED")
    assert built is not None and built.ref == "ENT_SHARED"

    # ... and the RESULT is a legal file: rule 2 ignores the mount.
    dock._trees[T2].nodes.append(built)
    tree_from_dict(tree_to_dict(dock._trees[T2]))     # no fatal


def test_a2_the_same_pair_inside_ONE_tree_is_still_a_load_time_fatal(
        main_window, tmp_path):
    """What this fix does NOT loosen (plan "Не делать"): a mount's ref still may
    not collide with a POSITIONED node of its OWN tree — the tree's later stages
    must be able to say which node is meant (`trees._validate_local_refs`)."""
    dock = _dock(main_window, tmp_path)
    _add_node(dock, "ENT_SHARED", "mount", anchor=_mount_anchor())
    _add_node(dock, "ENT_SHARED", "placement", tree=T1)

    with pytest.raises(ValidationError, match="collide with a positioned node"):
        tree_from_dict(tree_to_dict(dock._trees[T1]))


# ── б ───────────────────────────────────────────────────────────────────────
def test_b_a_point_and_an_entity_of_one_name_do_not_block_each_other(
        main_window, tmp_path):
    """Two RECORDS of one name (a POINT and an ENTITY): the point's own node
    carries the POINT's uuid, so the ENTITY `SHARED_X` stays available — before
    2026-11-14 the bare-name comparison greyed it out. Only the ROW is asserted:
    a node of each in ONE file is the loader's rule 2 conflict, which this fix
    does not touch."""
    dock = _dock(main_window, tmp_path)
    _add_node(dock, "SHARED_X", "point",
              ref_uuid=_uuid_of(dock, "points", "SHARED_X"))

    assert _enabled(_dialog(dock, "placement"), "SHARED_X") is True


# ── в ───────────────────────────────────────────────────────────────────────
def test_v_a_real_repeat_of_the_same_record_stays_greyed_and_refused(
        main_window, tmp_path, monkeypatch):
    """The record's OWN uuid is in the tree: the row is disabled, its tooltip
    names the tree and the node, and build_node refuses with the same wording."""
    dock = _dock(main_window, tmp_path)
    _add_node(dock, "ENT_REAL", "placement",
              ref_uuid=_uuid_of(dock, "entities", "ENT_REAL"))

    dlg = _dialog(dock, "placement")
    assert _enabled(dlg, "ENT_REAL") is False
    tip = dlg.ref_combo.itemData(_row(dlg, "ENT_REAL"), Qt.ItemDataRole.ToolTipRole)
    assert "already stands in tree" in tip and "t1" in tip

    text = _refusal(monkeypatch, dlg, "ENT_REAL")
    assert "t1" in text and "ENT_REAL" in text
    assert "exactly one position source" in text


# ── г ───────────────────────────────────────────────────────────────────────
def test_g_an_old_node_without_a_uuid_holds_its_own_section_only(
        main_window, tmp_path):
    """An OLD node (no `ref_uuid`) named SHARED_X occupies the ENTITY of that
    name — the loader resolves it there — but NOT the POINT of the same name."""
    dock = _dock(main_window, tmp_path)
    _add_node(dock, "SHARED_X", "placement")          # no ref_uuid: an old node

    assert _enabled(_dialog(dock, "placement"), "SHARED_X") is False
    assert _enabled(_dialog(dock, "point", _retired("point")), "SHARED_X") is True


# ── г2 ──────────────────────────────────────────────────────────────────────
def test_g2_an_old_node_without_a_kind_holds_its_name_in_every_section(
        main_window, tmp_path):
    """A node with NO kind is resolved by the loader by searching EVERY section,
    so it may BE any of those records: the name is greyed in all of them."""
    dock = _dock(main_window, tmp_path)
    _add_node(dock, "SHARED_X", None)

    assert _enabled(_dialog(dock, "placement"), "SHARED_X") is False
    assert _enabled(_dialog(dock, "point", _retired("point")), "SHARED_X") is False


# ── д ───────────────────────────────────────────────────────────────────────
def test_d_module_copper_and_component_nodes_occupy_nothing(
        main_window, tmp_path):
    """The other three LOCAL kinds are names local to one tree, exactly like a
    mount: an entity of that name stays selectable (the GUI's own drifted copy of
    the list used to get this wrong for "mount" only)."""
    dock = _dock(main_window, tmp_path)
    for kind in ("module", "copper", "component"):
        _add_node(dock, "ENT_SHARED", kind)

    assert "ENT_SHARED" not in dock._used_refs()
    dlg = _dialog(dock, "placement")
    assert _enabled(dlg, "ENT_SHARED") is True
    built = _accepted(dlg, "ENT_SHARED")
    assert built is not None and built.ref == "ENT_SHARED"

    dock._trees[T2].nodes.append(built)
    tree_from_dict(tree_to_dict(dock._trees[T2]))     # no fatal


# ── е ───────────────────────────────────────────────────────────────────────
def test_e_unique_ref_does_not_renumber_beside_a_mount(main_window, tmp_path):
    """The auto-numbering works on NAMES (a new node's ref must not collide), so
    it reads the same index — and a MOUNT's name is not in it. Before the fix a
    mount `ENT_SHARED` made the dock rename a brand-new node to ENT_SHARED_1."""
    dock = _dock(main_window, tmp_path)
    _add_node(dock, "ENT_SHARED", "mount", anchor=_mount_anchor())
    used = dock._used_refs()
    assert TreesDock._unique_ref("ENT_SHARED", used) == "ENT_SHARED"

    # A REAL record node still forces the suffix — the numbering itself is intact.
    _add_node(dock, "ENT_REAL", "placement",
              ref_uuid=_uuid_of(dock, "entities", "ENT_REAL"))
    assert TreesDock._unique_ref("ENT_REAL", dock._used_refs()) == "ENT_REAL_1"


# ── ж ───────────────────────────────────────────────────────────────────────
def test_zh_the_local_kind_list_has_one_owner():
    """ONE list, the loader's: gui/docks/trees_node_form.py keeps no copy of its
    own (its copy had drifted — it was missing "mount")."""
    from kicadstamp import trees as trees_mod
    from gui.docks import trees_node_form

    assert trees_node_form.LOCAL_REF_KINDS is trees_mod.LOCAL_REF_KINDS
    assert "mount" in trees_node_form.LOCAL_REF_KINDS
    # the private name is an ALIAS, not a second tuple (the old mutation rigs
    # patch it by name).
    assert trees_mod._LOCAL_REF_KINDS is trees_mod.LOCAL_REF_KINDS


# ═══ §1 (приёмка f5b02f80): ONE tree — a LOCAL name against a record's name ══
# The loader forbids a local ref (mount / copper container / component node) from
# colliding with a positioned node's ref IN ITS OWN TREE, and that is a load-time
# fatal (`trees._validate_local_refs`). f5b02f80 correctly opened the position
# where the two live in DIFFERENT trees (cells а / и) — and wrongly opened the
# SAME tree too: Apply accepted the node and the next `load_config` died on
# "mount refs must be distinct so a node can be named unambiguously".
#
# One property, parametrized by the local kind (rule 35: `к` is not a separate
# cell, `copper` is a parameter of `з`).

LOCAL_KINDS = ("mount", "copper", "component")


def _local_node(dock, ref: str, kind: str, tree: int = T1) -> TreeNode:
    """A local node of `kind` standing in that tree. Every one of the three kinds
    carries an address/anchor in the grammar, so all of them get one."""
    return _add_node(dock, ref, kind, anchor=_mount_anchor(), tree=tree)


@pytest.mark.parametrize("local_kind", LOCAL_KINDS)
def test_z_a_record_named_like_a_local_node_of_the_SAME_tree_is_refused(
        main_window, tmp_path, monkeypatch, local_kind):
    """Клетки з и к: the local node `ENT_SHARED` stands in t1 and the node is being
    added to t1 — the file would not load ("collide with a positioned node of the
    same tree"), so the row is greyed with ITS OWN hint (no record stands
    anywhere, so "already stands in tree …" would name the wrong thing) and Apply
    refuses in the LOADER's words. Nothing is touched on the way: neither the
    buffer nor the dirty flag."""
    dock = _dock(main_window, tmp_path)
    _local_node(dock, "ENT_SHARED", local_kind)
    before = [(n.ref, n.kind) for n in dock._trees[T1].nodes]

    dlg = _dialog(dock, "placement", tree=T1)
    assert _enabled(dlg, "ENT_SHARED") is False
    tip = dlg.ref_combo.itemData(_row(dlg, "ENT_SHARED"),
                                 Qt.ItemDataRole.ToolTipRole)
    assert "this tree already has" in tip
    assert node_kind_label(local_kind) in tip and "ENT_SHARED" in tip
    assert "already stands in tree" not in tip

    text = _refusal(monkeypatch, dlg, "ENT_SHARED")
    assert f"{local_kind} node ref(s) ENT_SHARED" in text
    assert "collide with a positioned node of the same tree" in text

    assert [(n.ref, n.kind) for n in dock._trees[T1].nodes] == before
    assert dock._dirty is False
    load_config(str(tmp_path / "root.sexp"))          # the file on disk still reads


@pytest.mark.parametrize("local_kind", LOCAL_KINDS)
def test_i_a_record_named_like_a_local_node_of_ANOTHER_tree_stays_available(
        main_window, tmp_path, local_kind):
    """Клетка и: the same name in t1 does not block t2 — a local name lives in ONE
    tree. The node is accepted AND the result loads (the half f5b02f80 did right,
    now for all three local kinds)."""
    dock = _dock(main_window, tmp_path)
    _local_node(dock, "ENT_SHARED", local_kind)

    dlg = _dialog(dock, "placement", tree=T2)
    assert _enabled(dlg, "ENT_SHARED") is True
    built = _accepted(dlg, "ENT_SHARED")
    assert built is not None and built.ref == "ENT_SHARED"

    dock._trees[T2].nodes.append(built)
    tree_from_dict(tree_to_dict(dock._trees[T2]))      # no fatal


@pytest.mark.parametrize("local_kind", ("mount", "copper"))
def test_m_a_local_node_renamed_onto_a_positioned_node_is_refused(
        main_window, tmp_path, monkeypatch, local_kind):
    """Клетки м (mount) и м2 (copper): the REVERSE direction, in the node window.
    `vccint_5` stands in t1 as a local node and `fpga_vccint_5` as a positioned
    one; renaming the local node onto the positioned node's ref is refused in the
    LOADER's words. Before §1 Apply accepted it — the same defect as the direct
    direction, just reached from the other side."""
    dock = _dock(main_window, tmp_path)
    local = _local_node(dock, "vccint_5", local_kind)
    _add_node(dock, "fpga_vccint_5", "placement",
              ref_uuid=_uuid_of(dock, "entities", "ENT_REAL"))
    before = [(n.ref, n.kind) for n in dock._trees[T1].nodes]

    dlg = _dialog(dock, local_kind, existing=local, tree=T1)
    text = _refusal(monkeypatch, dlg, "fpga_vccint_5")
    assert f"{local_kind} node ref(s) fpga_vccint_5" in text
    assert "collide with a positioned node of the same tree" in text

    assert [(n.ref, n.kind) for n in dock._trees[T1].nodes] == before
    assert dock._dirty is False
    load_config(str(tmp_path / "root.sexp"))

    # The SAME local node under its OWN ref is not a refusal (it is not "another
    # node"): an edit that changes only the offset must still go through.
    dlg = _dialog(dock, local_kind, existing=local, tree=T1)
    assert _accepted(dlg, "vccint_5") is not None
