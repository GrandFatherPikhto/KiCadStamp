# tests/net/test_extra_copper.py
"""Cells а–к of plan_2026_11_10_extra_copper_by_selection: the THIRD owner of copper.

«Read extra copper from selection» writes what the classifier leaves alone — a
dead end from a node's pad into a pour — as its own `net_traces:` record with
`(category "extra")`. What is pinned here:

  * а — a dead end becomes ONE record: the node's own pad is the anchor and the
    signature, the track and the via are its geometry;
  * б — inter-node copper is REFUSED, not taken: it has another owner;
  * в — a piece a cell's record owns is refused with the CELL's name, by the same
    ownership map the inter-node re-read subtracts by;
  * г — a pad outside the tree, and a piece with no pad at all: two refusals, each
    naming its own reason;
  * д — two nets in one selection: one record, one refusal, and the report lists
    both;
  * е — a repeat refreshes the record's geometry and keeps its name and uuid;
  * ж — the inter-node re-read does not touch an extra record — not `updated`,
    not `missing`, not `unchanged`;
  * з — a piece that BECAME inter-node and matches an extra record is not taken:
    one line names the record that owns it;
  * и — the s-expr round trip carries `category` and leaves a record without it
    untouched (the format number is not bumped);
  * к — a tree instance copies the record WITH its category and its own sheet.

The board/config doubles are `tests/net/test_extra_copper_s0.py`'s (imported, not
copied — one way to build an area in this tree).
"""
import dataclasses
from types import SimpleNamespace

from kicadstamp.config import NetTrace, load_config
from kicadstamp.trees import TreeNode
from kicadstamp.config.sexp_format import dict_to_sexp, sexp_to_dict
from kicadstamp.extra_copper import extra_report_lines, plan_extra_copper
from kicadstamp.internode_capture import apply_reread_plan, plan_internode_reread
from kicadstamp.internode_cell_copper import CellCopperMap
from kicadstamp.net_trace_category import CATEGORY_EXTRA, is_extra

from tests.net.test_extra_copper_s0 import (
    _AreaBoard,
    _cfg,
    _fp,
    _pad,
    _track,
    _tree,
    _via,
)


def _stub_area():
    """а's shape: C17.2 → a track → a via into the pour. ONE pad of the tree's
    node, so the classifier calls it STUB — a dead end, nobody's bridge."""
    fps = [_fp("R1", role="A", cluster="A")]
    pads = {"R1": [_pad("1", "N", 10, 10)]}
    track = _track(10, 10, 10, 8, "N")
    via = _via(10, 8, "N")
    return _AreaBoard(fps, pads, tracks=[track], vias=[via]), fps, [track, via]


def _pair_area():
    """A real bridge: R1.1 → track → R2.1, two nodes of the tree = INTERNODE."""
    fps = [_fp("R1", role="A", cluster="A"), _fp("R2", role="B", cluster="B")]
    pads = {"R1": [_pad("1", "N", 10, 10)], "R2": [_pad("1", "N", 20, 10)]}
    track = _track(10, 10, 20, 10, "N")
    return _AreaBoard(fps, pads, tracks=[track]), fps, [track]


def _plan(board, fps, items, cfg=None, tree=None, **kw):
    return plan_extra_copper(
        board, cfg or _cfg(), tree or _tree(net_trace_refs=()), config_path=None,
        area_items=items, area_footprints=fps, **kw)


def _refusals(plan):
    """The per-piece refusal lines (п.5) — every other warning here is the
    «profile is not known, cell copper is not subtracted» note of `config_path=None`."""
    return [w for w in plan.warnings if w.startswith("skipped copper")]


# ── а: a dead end becomes one record ───────────────────────────────────────

def test_a_a_dead_end_becomes_one_extra_record():
    board, fps, items = _stub_area()

    plan = _plan(board, fps, items)

    assert len(plan.added) == 1, plan.warnings
    record = plan.added[0].record
    assert record.category == CATEGORY_EXTRA and is_extra(record)
    # the node's own pad is BOTH the anchor and the signature
    assert record.pads == ["A.1"]
    assert (record.anchor_role, record.anchor_cluster, record.anchor_pad) == \
        ("A", "A", "1")
    assert len(record.tracks) == 1 and len(record.vias) == 1
    assert plan.added[0].track_count == 1 and plan.added[0].via_count == 1
    assert plan.added[0].identity == record.name and record.name
    assert _refusals(plan) == []


def test_a_an_empty_selection_is_one_refusal_and_no_read():
    board, fps, _items = _stub_area()

    plan = _plan(board, fps, [])

    assert plan.added == [] and plan.records == []
    assert len(plan.warnings) == 1 and "no track and no via" in plan.warnings[0]


# ── б: inter-node copper is another owner's ────────────────────────────────

def test_b_an_inter_node_piece_is_refused_not_taken():
    board, fps, items = _pair_area()

    plan = _plan(board, fps, items)

    assert plan.added == [] and plan.records == []
    assert plan.discarded.get("internode") == 1
    assert len(_refusals(plan)) == 1
    assert "Reread " in _refusals(plan)[0] and "inter-node copper" in _refusals(plan)[0]


# ── в: cell copper is another owner's too, and the cell is named ───────────

def test_v_a_cell_owned_piece_is_refused_with_the_cell_name(monkeypatch):
    board, fps, items = _stub_area()
    monkeypatch.setattr(
        "kicadstamp.internode_cell_copper.subtract_units",
        lambda adapter, cfg, tree, config_path, units, *, sheet_names=None:
        (list(units), CellCopperMap(uuids={items[1].uuid}), 0, 0, [], []))

    plan = _plan(board, fps, items)

    assert plan.added == [] and plan.records == []
    assert plan.discarded.get("cell") == 1
    assert len(_refusals(plan)) == 1 and "cell 'c'" in _refusals(plan)[0]


# ── г: a pad outside the tree, and a piece with no pad at all ───────────────

def test_g_a_foreign_pad_and_an_unmoored_piece_are_two_refusals():
    fps = [_fp("R1", role="A", cluster="A"), _fp("R9")]
    pads = {"R1": [_pad("1", "N", 10, 10)], "R9": [_pad("1", "N", 30, 10)]}
    foreign = _track(10, 10, 30, 10, "N")
    lonely = _via(50, 50, "X")
    board = _AreaBoard(fps, pads, tracks=[foreign], vias=[lonely])

    plan = _plan(board, fps, [foreign, lonely])

    assert plan.added == [] and plan.records == []
    assert plan.discarded.get("foreign") == 1
    assert plan.discarded.get("unmoored") == 1
    lines = _refusals(plan)
    assert len(lines) == 2
    assert any("belong to no node of tree" in line for line in lines)
    assert any("touches no pad at all" in line for line in lines)


# ── д: two nets, one good piece ────────────────────────────────────────────

def test_d_two_nets_give_one_record_and_one_refusal():
    good_board, fps, good_items = _stub_area()
    foreign = _track(10, 10, 30, 10, "M")          # R1 → a pad outside the tree
    fps = fps + [_fp("R9")]
    pads = {"R1": good_board._pads["R1"], "R9": [_pad("1", "M", 30, 10)]}
    board = _AreaBoard(fps, pads, tracks=[good_items[0], foreign],
                       vias=[good_items[1]])

    plan = _plan(board, fps, [good_items[0], good_items[1], foreign])

    assert len(plan.added) == 1
    assert plan.added[0].record.net == "N"
    lines = _refusals(plan)
    assert len(lines) == 1 and "'M'" in lines[0]
    report = extra_report_lines("t", plan)
    assert report[0] == "Tree 't': extra copper read."
    assert any("added:" in line for line in report)
    assert any("not taken:" in line for line in report)


# ── е: a repeat refreshes geometry, keeps the identity ────────────────────

def test_e_a_repeat_keeps_the_name_and_the_uuid(monkeypatch):
    board, fps, items = _stub_area()
    first = _plan(board, fps, items)
    stored = dataclasses.replace(first.added[0].record, uuid="uuid-extra")
    cfg = dataclasses.replace(_cfg(), net_traces=[stored])
    tree = _tree(net_trace_refs=(stored.name,))

    # the same pads and net, a LONGER track and the via moved with it
    longer = _track(10, 10, 10, 5, "N")
    via = _via(10, 5, "N")
    moved = _AreaBoard(fps, {"R1": [_pad("1", "N", 10, 10)]},
                       tracks=[longer], vias=[via])

    plan = _plan(moved, fps, [longer, via], cfg=cfg, tree=tree)

    assert plan.added == []
    assert len(plan.updated) == 1, plan.warnings
    fresh, counts = plan.updated[0]
    assert fresh.identity == stored.name
    assert fresh.record.uuid == "uuid-extra"
    assert is_extra(fresh.record)
    assert counts[0] == 1 and counts[2] == 1     # still one track each side


def test_e_an_untouched_extra_record_is_not_looked_at():
    board, fps, items = _stub_area()
    first = _plan(board, fps, items)
    stored = dataclasses.replace(first.added[0].record, uuid="uuid-extra",
                                 pads=["B.9"])          # ANOTHER piece
    cfg = dataclasses.replace(_cfg(), net_traces=[stored])
    tree = _tree(net_trace_refs=(stored.name,))

    plan = _plan(board, fps, items, cfg=cfg, tree=tree)

    assert len(plan.added) == 1 and plan.updated == [] and plan.unchanged == []


# ── ж: the inter-node re-read leaves extra records alone ──────────────────

def test_zh_the_inter_node_reread_never_touches_an_extra_record():
    board, fps, items = _stub_area()
    plan_extra = _plan(board, fps, items)
    stored = dataclasses.replace(plan_extra.added[0].record, uuid="uuid-extra")
    cfg = dataclasses.replace(_cfg(), net_traces=[stored])
    tree = _tree(net_trace_refs=(stored.name,))

    plan = plan_internode_reread(board, cfg, tree, config_path=None,
                                 area_items=items, area_footprints=fps)

    assert plan.added == [] and plan.updated == [] and plan.unchanged == []
    assert plan.missing == [] and plan.not_in_selection == []
    assert plan.records == []
    applied = apply_reread_plan(cfg, plan)
    assert applied.net_traces == [stored]


# ── з: a piece that BECAME inter-node is not taken from its owner ─────────
#
# Н1 of the acceptance: a dead end ALWAYS has ONE pad (the door accepts only
# STUB), so «the same pad set» could never fire — the rule is a SUBSET, on the
# same net. Both cells below start from the REAL form: the extra record is
# written by `plan_extra_copper` itself, then the dead end grows a track to a
# second node.

def _door_written_dead_end():
    """The extra record the door itself writes for `_stub_area` (pads ["A.1"])."""
    board, fps, items = _stub_area()
    written = _plan(board, fps, items)
    assert len(written.added) == 1, written.warnings
    stored = dataclasses.replace(written.added[0].record, uuid="uuid-extra")
    assert stored.pads == ["A.1"] and is_extra(stored)
    return stored


def _bridge_board():
    """The stub's track, now reaching R2.1 — one INTERNODE piece `A.1 → B.1`."""
    fps = [_fp("R1", role="A", cluster="A"), _fp("R2", role="B", cluster="B")]
    pads = {"R1": [_pad("1", "N", 10, 10)], "R2": [_pad("1", "N", 20, 10)]}
    track = _track(10, 10, 20, 10, "N")
    return _AreaBoard(fps, pads, tracks=[track]), fps, [track]


def test_z_a_bridge_over_an_extra_dead_end_is_not_taken():
    stored = _door_written_dead_end()
    cfg = dataclasses.replace(_cfg(), net_traces=[stored])
    tree = _tree(net_trace_refs=(stored.name,))
    board, fps, items = _bridge_board()

    plan = plan_internode_reread(board, cfg, tree, config_path=None,
                                 area_items=items, area_footprints=fps)

    assert plan.added == [] and plan.updated == [] and plan.missing == []
    lines = [w for w in plan.warnings if stored.name in w]
    assert len(lines) == 1
    assert "extra record" in lines[0]
    assert "delete that record, then re-read" in lines[0]


def test_z2_a_legacy_record_does_not_swallow_the_shadowed_piece():
    """The order of the checks (точная подпись → тень → `legacy_by_net`): an old
    bridge record without `pads:` on the same net must NOT take the piece before
    the shadow can name the extra record that owns part of it."""
    stored = _door_written_dead_end()
    legacy = NetTrace(net="N", anchor_role="A", name="old_bridge")
    cfg = dataclasses.replace(_cfg(), net_traces=[legacy, stored])
    tree = _tree(net_trace_refs=(legacy.name, stored.name))
    board, fps, items = _bridge_board()

    plan = plan_internode_reread(board, cfg, tree, config_path=None,
                                 area_items=items, area_footprints=fps)

    assert plan.added == [] and plan.updated == []
    assert len([w for w in plan.warnings if stored.name in w]) == 1
    assert apply_reread_plan(cfg, plan).net_traces == [legacy, stored]


# ── повторы и имена: ключ (площадки, сеть), имена одного прогона ──────────

def test_e3_the_same_pad_on_another_net_is_a_new_record():
    """Е3: the repeat key is «the same pads AND the same net» — a pads-only key
    would refresh a record of ANOTHER net."""
    board, fps, items = _stub_area()
    first = _plan(board, fps, items)
    stored = dataclasses.replace(first.added[0].record, uuid="uuid-other",
                                 net="M")
    cfg = dataclasses.replace(_cfg(), net_traces=[stored])
    tree = _tree(net_trace_refs=(stored.name,))

    plan = _plan(board, fps, items, cfg=cfg, tree=tree)

    assert len(plan.added) == 1 and plan.updated == []
    assert plan.added[0].record.net == "N"
    assert plan.added[0].record.pads == stored.pads


def test_e4_two_dead_ends_of_one_run_get_two_names():
    """Е4: two vias of one node on one net produce the SAME `generate_trace_name`
    — the run's `existing_names` is what keeps the second record distinguishable."""
    fps = [_fp("R1", role="A", cluster="A")]
    pads = {"R1": [_pad("1", "GND", 10, 10), _pad("2", "GND", 14, 10)]}
    tracks = [_track(10, 10, 10, 8, "GND"), _track(14, 10, 14, 8, "GND")]
    vias = [_via(10, 8, "GND"), _via(14, 8, "GND")]
    board = _AreaBoard(fps, pads, tracks=tracks, vias=vias)

    plan = _plan(board, fps, [*tracks, *vias])

    assert len(plan.added) == 2, plan.warnings
    names = [c.record.name for c in plan.added]
    assert all(names) and names[0] != names[1]


def test_e5_a_name_a_local_node_holds_is_suffixed():
    """Е5: `generate_trace_name` already avoids the config's RECORD names; what
    only `free_node_ref` answers is the tree's LOCAL names — a `kind "copper"`
    container of the same name is exactly the shape that would make the tree
    unloadable once the record's node is added."""
    board, fps, items = _stub_area()
    natural = _plan(board, fps, items).added[0].record.name
    tree = _tree(net_trace_refs=(), nodes=(TreeNode(
        ref=natural, kind="copper", xy=None, polar=None, rotation=0.0,
        name=None, group=None, children=[]),))

    plan = _plan(board, fps, items, tree=tree)

    name = plan.added[0].record.name
    assert name != natural and name.startswith(natural)


# ── и: the s-expr round trip carries the category, and its absence ────────

def test_i_the_category_round_trips_and_an_old_record_stays_clean():
    data = {"net_traces": [
        {"net": "N", "anchor_role": "A", "name": "n__a", "pads": ["A.1"],
         "category": CATEGORY_EXTRA},
        {"net": "M", "anchor_role": "B", "name": "m__b"},
    ]}

    text = dict_to_sexp(data, format_number=2)
    assert '(category "extra")' in text
    assert text.count("(category") == 1

    back = sexp_to_dict(text)["net_traces"]
    assert [rec.get("category") for rec in back] == [CATEGORY_EXTRA, None]

    # an OLD file — no category anywhere — reads exactly as before
    old = sexp_to_dict(dict_to_sexp(
        {"net_traces": [{"net": "M", "anchor_role": "B"}]}, format_number=2))
    assert "category" not in old["net_traces"][0]


# ── к: a tree instance copies the record with its category ────────────────

def test_k_a_tree_instance_copies_the_category_and_its_own_sheet(tmp_path):
    data = {
        "cells": {"c": {}},
        "entities": [{"name": "dac_buf", "cell": "c", "cluster": "DAC_BUF",
                      "sheet": "Channel_0"}],
        "net_traces": [{"name": "extra_rec", "net": "N",
                        "anchor_role": "DAC_BUF", "anchor_sheet": "Channel_0",
                        "pads": ["DAC_BUF.1"], "category": CATEGORY_EXTRA}],
        "trees": [{"name": "tpl",
                   "anchor": {"role": "DAC_BUF", "sheet": "Channel_0"},
                   "nodes": [{"ref": "dac_buf", "kind": "placement",
                              "xy": [1.0, 2.0]},
                             {"ref": "extra_rec", "kind": "net_trace"}]}],
        "tree_instances": [{"template": "tpl", "name": "tpl_ch1",
                            "sheet": "Channel_1"}],
    }
    path = tmp_path / "t.sexp"
    path.write_text(dict_to_sexp(data, format_number=2), encoding="utf-8")

    cfg, _warnings = load_config(str(path))

    copy = next(nt for nt in cfg.net_traces
                if (nt.name or "").endswith("__tpl_ch1"))
    assert is_extra(copy) and copy.anchor_sheet == "Channel_1"


# ── л (the pure half): the rows are split by owner ────────────────────────

def test_l_the_group_rows_split_extra_from_inter_node_copper():
    from gui.docks.trees_copper_groups import copper_group_rows

    nodes = [SimpleNamespace(kind="net_trace", ref="n__a"),
             SimpleNamespace(kind="net_trace", ref="extra_rec"),
             SimpleNamespace(kind="net_trace", ref="n__b")]

    rows = copper_group_rows({"extra_rec"}, nodes)

    assert [title for title, _group in rows] == ["Copper ({count})",
                                                 "Extra copper ({count})"]
    assert [n.ref for n in rows[0][1]] == ["n__a", "n__b"]
    assert [n.ref for n in rows[1][1]] == ["extra_rec"]
    # nothing extra → ONE row, exactly as before this door existed
    assert len(copper_group_rows(set(), nodes)) == 1
