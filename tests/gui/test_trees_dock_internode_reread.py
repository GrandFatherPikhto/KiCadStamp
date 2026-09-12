# tests/gui/test_trees_dock_internode_reread.py
"""Э4 GUI tests: the "Reread inter-node copper" finish path of TreesDock
(plan_2026_09_12_internode_copper_core, stage Э4; design §6).

The live-board half runs on a worker and is covered by
tests/test_internode_capture.py; what is pinned down HERE is the container
behaviour on the UI thread:
  * the new records become TOP-LEVEL kind="net_trace" nodes (ref = identity);
  * the records the re-read did not find are MARKED, not deleted, and the mark
    never touches the config;
  * the touched records are STAGED into the working set (Save persists them)
    while the untouched ones and the missing one stay exactly as they were.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

from kicadstamp.config import Config
from kicadstamp.config.sexp_format import dict_to_sexp, sexp_to_dict
from kicadstamp.domain.board import BoardLayer, Footprint, Track
from kicadstamp.internode_capture import apply_reread_plan, plan_internode_reread

from gui.docks.trees_dock import _STALE_NET_TRACE_TAG, TreesDock


# ── fixtures ──────────────────────────────────────────────────────────────

def _root(tmp_path, record):
    """A minimal, loadable root config: a cell, two entities (clusters A/B), a
    tree with a placement node for each and ONE net_trace node."""
    root = tmp_path / "root.sexp"
    root.write_text(dict_to_sexp({
        "cells": {"c": {"components": [{"role": "R", "offset_along_mm": 0.0,
                                        "offset_across_mm": 0.0}]}},
        "entities": [{"name": "e_a", "cell": "c", "cluster": "A"},
                     {"name": "e_b", "cell": "c", "cluster": "B"}],
        "net_traces": [record],
        "trees": [{"name": "t", "anchor": {"origin": True}, "nodes": [
            {"ref": "e_a", "kind": "placement", "xy": [0.0, 0.0]},
            {"ref": "e_b", "kind": "placement", "xy": [0.0, 0.0]},
            {"ref": record["name"], "kind": "net_trace"},
        ]}],
    }), encoding="utf-8")
    return root


def _record():
    """A NAMED record whose stored pad set does NOT match the board copper, so
    the re-read reports it as missing (and adds the fresh unit)."""
    return {
        "net": "N", "name": "gone__a__z", "pads": ["A.1", "Z.9"],
        "anchor_role": "A", "anchor_pad": "1",
        "tracks": [{"start_along_mm": 0.0, "start_across_mm": 0.0,
                    "end_along_mm": 10.0, "end_across_mm": 0.0,
                    "width_mm": 0.25, "layer": "F.Cu",
                    "net_from_role": "A", "net_from_role_pad": "1"}],
    }


def _fp(ref, role, cluster, x_mm, y_mm):
    fp = Footprint(ref=ref, uuid=f"fp-{ref}",
                   position=SimpleNamespace(x=int(x_mm * 1e6), y=int(y_mm * 1e6)),
                   angle_deg=0.0, layer=BoardLayer.BL_F_Cu)
    fp._role = role
    fp._cluster = cluster
    return fp


def _board():
    """R1 (cluster A) and R2 (cluster B) with a single track between their
    pads — the fresh inter-node unit."""
    r1 = _fp("R1", "A", "A", 10, 10)
    r2 = _fp("R2", "B", "B", 20, 10)
    pads = {
        "R1": [SimpleNamespace(number="1", net_name="N",
                               position=SimpleNamespace(x=10_000_000, y=10_000_000))],
        "R2": [SimpleNamespace(number="1", net_name="N",
                               position=SimpleNamespace(x=20_000_000, y=10_000_000))],
    }
    board = MagicMock()
    board.get_footprints.return_value = [r1, r2]
    board.get_selected_items.return_value = []
    board.get_field_value.side_effect = lambda fp, name: {
        "Role": getattr(fp, "_role", None),
        "Cluster": getattr(fp, "_cluster", None)}.get(name)
    board.get_footprint.side_effect = lambda ref: next(
        (f for f in (r1, r2) if f.ref == ref), None)
    board.get_footprint_pads.side_effect = lambda fp: pads.get(fp.ref, [])
    board.get_pad_by_number.side_effect = lambda fp, num: next(
        (p for p in pads.get(fp.ref, []) if str(p.number) == str(num)), None)
    board.get_bounding_boxes.side_effect = lambda items: [
        SimpleNamespace(pos=SimpleNamespace(x=it.position.x - 300_000,
                                            y=it.position.y - 300_000),
                        size=SimpleNamespace(x=600_000, y=600_000),
                        inflate=lambda _d: None)
        for it in items]
    track = Track(uuid="t1", start=SimpleNamespace(x=10_000_000, y=10_000_000),
                  end=SimpleNamespace(x=20_000_000, y=10_000_000),
                  net_name="N", width_mm=0.25, layer=BoardLayer.BL_F_Cu)
    return board, [r1, r2], [track]


def _dock(main_window, tmp_path):
    root = _root(tmp_path, _record())
    dock = TreesDock(main_window)
    dock.set_root_file(root)
    return dock, root


# ── the finish path ───────────────────────────────────────────────────────

def test_finish_adds_nodes_marks_missing_and_stages(main_window, tmp_path):
    dock, root = _dock(main_window, tmp_path)
    tree = dock._trees[0]
    board, fps, items = _board()
    plan = plan_internode_reread(board, dock._cfg, tree, area_items=items,
                                 area_footprints=fps)

    assert len(plan.added) == 1 and plan.missing == ["gone__a__z"]
    added_identity = plan.added[0].identity

    dock._finish_reread_internode_copper({
        "plan": plan,
        "cfg": apply_reread_plan(dock._cfg, plan),
        "tree": tree,
        "added": [added_identity],
        "narrowed_to_selection": True,
    })

    # the new unit became a TOP-LEVEL net_trace node (ref = the identity, no xy)
    node = tree.nodes[-1]
    assert node.kind == "net_trace" and node.ref == added_identity
    assert node.xy is None

    # nothing was removed: the unfound record stays a node AND a record
    assert "gone__a__z" in [n.ref for n in tree.nodes]
    assert "gone__a__z" in [nt.name for nt in dock._cfg.net_traces]

    # the stale mark is session presentation only
    assert dock._stale_net_traces == {"gone__a__z"}
    stale_node = next(n for n in tree.nodes if n.ref == "gone__a__z")
    assert _STALE_NET_TRACE_TAG in dock._node_item_text(stale_node, stale=True)
    assert _STALE_NET_TRACE_TAG not in dock._node_item_text(stale_node)

    # staged into the working set: the fresh record is in the file, the missing
    # one is untouched, and the dock is dirty (File > Save persists)
    data = sexp_to_dict(root.read_text(encoding="utf-8"))
    entries = {e.get("name") or e.get("net"): e for e in data["net_traces"]}
    assert set(entries) == {"gone__a__z", added_identity}
    fresh = entries[added_identity]
    assert fresh["pads"] == ["A.1", "B.1"]
    assert fresh["tracks"][0]["net_from_role"] == "A"
    assert "net" not in fresh["tracks"][0]
    assert dock._dirty is True


def test_finish_with_nothing_changed_keeps_the_tree_and_is_clean(main_window, tmp_path):
    """A re-read that finds nothing new and loses nothing adds no node, marks
    nothing, and still reports (the Log) — no dialog, no exception."""
    dock, root = _dock(main_window, tmp_path)
    tree = dock._trees[0]
    board, fps, items = _board()
    # make the stored record match the board exactly -> unchanged, no additions
    stored = {
        "net": "N", "name": "match__a__b", "pads": ["A.1", "B.1"],
        "anchor_role": "A", "anchor_pad": "1",
        "tracks": [{"start_along_mm": 0.0, "start_across_mm": 0.0,
                    "end_along_mm": 10.0, "end_across_mm": 0.0,
                    "width_mm": 0.25, "layer": "F.Cu",
                    "net_from_role": "A", "net_from_role_pad": "1"}],
    }
    root.unlink()
    root = _root(tmp_path, stored)
    dock.set_root_file(root)
    tree = dock._trees[0]
    plan = plan_internode_reread(board, dock._cfg, tree, area_items=items,
                                 area_footprints=fps)
    assert plan.unchanged == ["match__a__b"]
    before = len(tree.nodes)
    dock._finish_reread_internode_copper({
        "plan": plan, "cfg": apply_reread_plan(dock._cfg, plan), "tree": tree,
        "added": [], "narrowed_to_selection": False})

    assert len(tree.nodes) == before
    assert dock._stale_net_traces == set()
