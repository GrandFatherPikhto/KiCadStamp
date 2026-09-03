#!/usr/bin/env python3
"""Tests for `tree_instances:` dict-level expansion (2026-09-02, plan
techdocs/handoff/deepseek/plan_2026_09_02_tree_instances.md P0 — revision:
dict-level, config/tree_instances.py::expand_tree_instances).

The raw `tree_instances:` declarations materialize into full Tree + Entity
records BEFORE the per-entry loaders run, so the generated records flow through
the SAME _load_tree/_load_entity machinery (rule 2 / duplicate-name checks) as
hand-written ones — these tests pin that behaviour down.
"""
from pathlib import Path

import pytest

from kicadstamp.config import load_config, TreeInstance
from kicadstamp.config.sexp_format import dict_to_sexp
from kicadstamp.exceptions import ValidationError


def _write(tmp_path, name, data) -> Path:
    p = tmp_path / name
    p.write_text(dict_to_sexp(data), encoding="utf-8")
    return p


def _template_data(instances, entity_sheet=None, anchor=None,
                   node_kind="placement", nodes=None) -> dict:
    """A minimal valid config: one role-anchored template tree `dac_buf_tpl`
    whose two entities `dac_buf`/`pif_avdd` live on one nested level."""
    return {
        "cells": {},
        "entities": [
            {"name": "dac_buf", "cell": "c_dac", "cluster": "DAC_BUF"},
            {"name": "pif_avdd", "cell": "c_pif", "cluster": "PIF_AVDD",
             **({"sheet": entity_sheet} if entity_sheet is not None else {})},
        ],
        "trees": [{
            "name": "dac_buf_tpl",
            "anchor": anchor if anchor is not None else {"role": "DAC_BUF"},
            "nodes": nodes if nodes is not None else [{
                "ref": "dac_buf", "kind": node_kind, "xy": [1.0, 2.0],
                "rotation": 90.0,
                "children": [{"ref": "pif_avdd", "kind": "placement",
                              "xy": [0.5, 0.0]}],
            }],
        }],
        "tree_instances": instances,
    }


def _tree_by_name(cfg, name):
    return next(t for t in cfg.trees if t.name == name)


def _entity_by_name(cfg, name):
    return next(e for e in cfg.entities if e.name == name)


class TestSimpleExpansion:
    def test_two_instances_materialize_trees_and_entities(self, tmp_path):
        p = _write(tmp_path, "t.sexp", _template_data([
            {"template": "dac_buf_tpl", "name": "ch1_dac_buf", "sheet": "Channel_1"},
            {"template": "dac_buf_tpl", "name": "ch2_dac_buf", "sheet": "Channel_2"},
        ]))
        cfg, _ = load_config(str(p))

        # The template stays; both instances become ordinary cfg.trees entries.
        assert [t.name for t in cfg.trees] == \
            ["dac_buf_tpl", "ch1_dac_buf", "ch2_dac_buf"]

        # cfg.tree_instances keeps the RAW declarations (GUI index source).
        assert cfg.tree_instances == [
            TreeInstance(template="dac_buf_tpl", name="ch1_dac_buf", sheet="Channel_1"),
            TreeInstance(template="dac_buf_tpl", name="ch2_dac_buf", sheet="Channel_2"),
        ]

        # Entity copies: renamed refs, instance sheet, template fields kept.
        assert {e.name for e in cfg.entities} == {
            "dac_buf", "pif_avdd",
            "dac_buf__ch1_dac_buf", "pif_avdd__ch1_dac_buf",
            "dac_buf__ch2_dac_buf", "pif_avdd__ch2_dac_buf",
        }
        for suffix, sheet in (("ch1_dac_buf", "Channel_1"), ("ch2_dac_buf", "Channel_2")):
            ent = _entity_by_name(cfg, f"dac_buf__{suffix}")
            assert ent.sheet == sheet
            assert ent.cell == "c_dac"
            assert ent.cluster == "DAC_BUF"
            nested = _entity_by_name(cfg, f"pif_avdd__{suffix}")
            assert nested.sheet == sheet
            assert nested.cell == "c_pif"

        # Generated trees: name/anchor sheet/node refs (recursively).
        for name, sheet in (("ch1_dac_buf", "Channel_1"), ("ch2_dac_buf", "Channel_2")):
            tree = _tree_by_name(cfg, name)
            assert tree.anchor.anchor_sheet == sheet
            assert tree.anchor.role == "DAC_BUF"
            node = tree.nodes[0]
            assert node.ref == f"dac_buf__{name}"
            assert node.children[0].ref == f"pif_avdd__{name}"

    def test_geometry_identical_to_template_and_each_other(self, tmp_path):
        p = _write(tmp_path, "t.sexp", _template_data([
            {"template": "dac_buf_tpl", "name": "ch1_dac_buf", "sheet": "Channel_1"},
            {"template": "dac_buf_tpl", "name": "ch2_dac_buf", "sheet": "Channel_2"},
        ]))
        cfg, _ = load_config(str(p))

        def _geom(tree):
            node = tree.nodes[0]
            child = node.children[0]
            return (node.xy, node.rotation, child.xy, child.rotation)

        tpl = _tree_by_name(cfg, "dac_buf_tpl")
        ch1 = _tree_by_name(cfg, "ch1_dac_buf")
        ch2 = _tree_by_name(cfg, "ch2_dac_buf")
        assert _geom(ch1) == _geom(tpl)
        assert _geom(ch2) == _geom(tpl)
        assert _geom(ch1) == _geom(ch2)

    def test_template_is_still_an_ordinary_tree_q3(self, tmp_path):
        """Q3: a template stays a normal, independently usable tree — nothing
        special flags it; its own entities are untouched (still sheetless)."""
        p = _write(tmp_path, "t.sexp", _template_data([
            {"template": "dac_buf_tpl", "name": "ch1_dac_buf", "sheet": "Channel_1"},
        ]))
        cfg, _ = load_config(str(p))
        tpl = _tree_by_name(cfg, "dac_buf_tpl")
        assert not hasattr(tpl, "is_instance")
        assert _entity_by_name(cfg, "dac_buf").sheet is None
        assert _entity_by_name(cfg, "pif_avdd").sheet is None


class TestNesting:
    def test_suffix_applied_on_every_level(self, tmp_path):
        """A 3-level template — the __{instance.name} suffix must land on every
        nested level, not just top-level nodes."""
        nodes = [{
            "ref": "dac_buf", "kind": "placement", "xy": [0.0, 0.0],
            "children": [{
                "ref": "pif_avdd", "kind": "placement", "xy": [1.0, 0.0],
                "children": [{
                    "ref": "deep", "kind": "placement", "xy": [2.0, 0.0],
                }],
            }],
        }]
        data = _template_data(
            [{"template": "dac_buf_tpl", "name": "ch1_dac_buf", "sheet": "Channel_1"}],
            nodes=nodes)
        data["entities"].append({"name": "deep", "cell": "c_deep"})
        p = _write(tmp_path, "t.sexp", data)
        cfg, _ = load_config(str(p))
        tree = _tree_by_name(cfg, "ch1_dac_buf")
        l1 = tree.nodes[0]
        l2 = l1.children[0]
        l3 = l2.children[0]
        assert l1.ref == "dac_buf__ch1_dac_buf"
        assert l2.ref == "pif_avdd__ch1_dac_buf"
        assert l3.ref == "deep__ch1_dac_buf"
        assert _entity_by_name(cfg, "deep__ch1_dac_buf").sheet == "Channel_1"


class TestFatals:
    def test_template_not_found(self, tmp_path):
        p = _write(tmp_path, "t.sexp", _template_data([
            {"template": "no_such_tree", "name": "ch1_dac_buf", "sheet": "Channel_1"},
        ]))
        with pytest.raises(ValidationError, match="template tree 'no_such_tree' not found"):
            load_config(str(p))

    def test_template_entity_own_sheet_is_overwritten_q2(self, tmp_path):
        """Q2 (revised 2026-09-02): a template Entity MAY carry its own real
        sheet (needed for the template's own live re-readability by
        Role+Sheet+Cluster); expansion does NOT fatal, the template keeps its
        own sheet untouched, and the generated COPY unconditionally gets the
        instance sheet (same overwrite pattern as the role-anchor sheet)."""
        p = _write(tmp_path, "t.sexp", _template_data(
            [{"template": "dac_buf_tpl", "name": "ch1_dac_buf", "sheet": "Channel_1"}],
            entity_sheet="Channel_0"))
        cfg, _ = load_config(str(p))
        # template entity keeps its own sheet (template stays live/re-readable)
        assert _entity_by_name(cfg, "pif_avdd").sheet == "Channel_0"
        # generated copies unconditionally get the instance sheet
        assert _entity_by_name(cfg, "pif_avdd__ch1_dac_buf").sheet == "Channel_1"
        assert _entity_by_name(cfg, "dac_buf__ch1_dac_buf").sheet == "Channel_1"
        # expansion never mutates the template tree (deep copies only)
        assert _tree_by_name(cfg, "dac_buf_tpl").nodes[0].ref == "dac_buf"

    def test_non_role_anchor_is_fatal(self, tmp_path):
        p = _write(tmp_path, "t.sexp", _template_data(
            [{"template": "dac_buf_tpl", "name": "ch1_dac_buf", "sheet": "Channel_1"}],
            anchor={"origin": True}))
        with pytest.raises(ValidationError, match="must be role-anchored"):
            load_config(str(p))

    def test_auto_anchor_missing_is_fatal(self, tmp_path):
        data = _template_data(
            [{"template": "dac_buf_tpl", "name": "ch1_dac_buf", "sheet": "Channel_1"}])
        data["trees"][0].pop("anchor")  # no (anchor ...) at all -> AUTO anchor
        p = _write(tmp_path, "t.sexp", data)
        with pytest.raises(ValidationError, match="must be role-anchored"):
            load_config(str(p))

    def test_non_placement_node_kind_is_fatal(self, tmp_path):
        p = _write(tmp_path, "t.sexp", _template_data(
            [{"template": "dac_buf_tpl", "name": "ch1_dac_buf", "sheet": "Channel_1"}],
            node_kind="module"))
        with pytest.raises(ValidationError, match="unsupported node kind 'module'"):
            load_config(str(p))

    def test_placement_node_without_entity_is_fatal(self, tmp_path):
        data = _template_data(
            [{"template": "dac_buf_tpl", "name": "ch1_dac_buf", "sheet": "Channel_1"}],
            nodes=[{"ref": "no_entity", "kind": "placement", "xy": [0.0, 0.0]}])
        p = _write(tmp_path, "t.sexp", data)
        with pytest.raises(ValidationError, match="no matching entities"):
            load_config(str(p))

    def test_declaration_missing_field_is_fatal(self, tmp_path):
        p = _write(tmp_path, "t.sexp", _template_data([
            {"template": "dac_buf_tpl", "name": "ch1_dac_buf"},  # no sheet
        ]))
        with pytest.raises(ValidationError, match="missing required sheet"):
            load_config(str(p))

    def test_tree_instances_not_a_list_is_fatal(self):
        """Unit-level: the s-expr writer cannot even express a dict-valued
        section (it assumes list sections are lists), so the guard in
        expand_tree_instances is exercised on the raw dict directly."""
        from kicadstamp.config.tree_instances import expand_tree_instances
        with pytest.raises(ValidationError, match="must be a list"):
            expand_tree_instances(
                {"tree_instances": {"template": "x", "name": "y", "sheet": "z"}})


class TestDuplicateAndRuleTwo:
    def test_two_instances_with_same_name_are_fatal(self, tmp_path):
        """Two declarations with the same name materialize duplicate generated
        records — the EXISTING duplicate-name checks catch them (no new code)."""
        p = _write(tmp_path, "t.sexp", _template_data([
            {"template": "dac_buf_tpl", "name": "dup", "sheet": "Channel_1"},
            {"template": "dac_buf_tpl", "name": "dup", "sheet": "Channel_2"},
        ]))
        with pytest.raises(ValidationError, match="unique name"):
            load_config(str(p))

    def test_instance_name_colliding_with_hand_written_tree_is_fatal(self, tmp_path):
        """An instance name equal to a hand-written tree -> generated tree
        collides -> the trees duplicate-name check fatals."""
        data = _template_data([
            {"template": "dac_buf_tpl", "name": "dac_buf_tpl", "sheet": "Channel_1"},
        ])
        p = _write(tmp_path, "t.sexp", data)
        with pytest.raises(ValidationError, match="unique name"):
            load_config(str(p))


class TestInclude:
    def test_template_in_one_file_instance_in_another(self, tmp_path):
        """Include-graph: the template (tree+entities) lives in one included
        file, the tree_instance declaration in another, the root includes both."""
        _write(tmp_path, "tpl.sexp", {
            "entities": [
                {"name": "dac_buf", "cell": "c_dac", "cluster": "DAC_BUF"},
                {"name": "pif_avdd", "cell": "c_pif", "cluster": "PIF_AVDD"},
            ],
            "trees": [{
                "name": "dac_buf_tpl", "anchor": {"role": "DAC_BUF"},
                "nodes": [{
                    "ref": "dac_buf", "kind": "placement", "xy": [1.0, 2.0],
                    "children": [{"ref": "pif_avdd", "kind": "placement",
                                  "xy": [0.5, 0.0]}],
                }],
            }],
        })
        _write(tmp_path, "inst.sexp", {
            "tree_instances": [
                {"template": "dac_buf_tpl", "name": "ch1_dac_buf", "sheet": "Channel_1"},
            ],
        })
        root = _write(tmp_path, "root.sexp", {
            "cells": {},
            "include": ["tpl.sexp", "inst.sexp"],
        })
        cfg, _ = load_config(str(root))
        assert [t.name for t in cfg.trees] == ["dac_buf_tpl", "ch1_dac_buf"]
        assert cfg.tree_instances == [
            TreeInstance(template="dac_buf_tpl", name="ch1_dac_buf", sheet="Channel_1"),
        ]
        assert _tree_by_name(cfg, "ch1_dac_buf").anchor.anchor_sheet == "Channel_1"


class TestSexpRoundTrip:
    def test_tree_instances_section_round_trips_through_sexp(self, tmp_path):
        """dict_to_sexp/sexp_to_dict survive the new list section verbatim."""
        data = _template_data([
            {"template": "dac_buf_tpl", "name": "ch1_dac_buf", "sheet": "Channel_1"},
        ])
        text = dict_to_sexp(data)
        from kicadstamp.config.sexp_format import sexp_to_dict
        back = sexp_to_dict(text)
        assert back["tree_instances"] == data["tree_instances"]
        # ... and the materialized records load from the round-tripped text.
        p = tmp_path / "rt.sexp"
        p.write_text(text, encoding="utf-8")
        cfg, _ = load_config(str(p))
        assert _tree_by_name(cfg, "ch1_dac_buf").anchor.anchor_sheet == "Channel_1"


class TestTreeInstanceWriter:
    """Persistence behind Tools -> "Instances..." (2026-09-02, P3):
    config_writer.upsert_tree_instances rewrites ONE template's short
    tree_instances: rows (create/edit/delete); the dialog never generates —
    materialization happens at the next load."""

    @staticmethod
    def _read_instances(p) -> list:
        from kicadstamp.config.sexp_format import sexp_to_dict
        return list(sexp_to_dict(p.read_text(encoding="utf-8"))
                    .get("tree_instances") or [])

    def _file(self, tmp_path, instances=None):
        data = _template_data(instances or [])
        return _write(tmp_path, "w.sexp", data)

    def test_create_writes_rows_and_materializes_on_load(self, tmp_path):
        from kicadstamp.config_writer import upsert_tree_instances
        p = self._file(tmp_path)
        changed = upsert_tree_instances(
            p, "dac_buf_tpl", [{"name": "ch1_dac_buf", "sheet": "Channel_1"}])
        assert changed is True
        assert self._read_instances(p) == [{
            "template": "dac_buf_tpl", "name": "ch1_dac_buf", "sheet": "Channel_1"}]
        cfg, _ = load_config(str(p))
        assert sorted(t.name for t in cfg.trees) == ["ch1_dac_buf", "dac_buf_tpl"]

    def test_edit_replaces_template_rows_and_preserves_others(self, tmp_path):
        from kicadstamp.config_writer import upsert_tree_instances
        p = self._file(tmp_path, [
            {"template": "dac_buf_tpl", "name": "ch1_dac_buf", "sheet": "Channel_1"},
            {"template": "other_tpl", "name": "other", "sheet": "Channel_9"},
        ])
        changed = upsert_tree_instances(p, "dac_buf_tpl", [
            {"name": "ch1_dac_buf", "sheet": "Channel_1_NEW"},
            {"name": "ch2_dac_buf", "sheet": "Channel_2"},
        ])
        assert changed is True
        rows = self._read_instances(p)
        assert {"template": "other_tpl", "name": "other", "sheet": "Channel_9"} in rows
        dac = [r for r in rows if r["template"] == "dac_buf_tpl"]
        assert dac == [
            {"template": "dac_buf_tpl", "name": "ch1_dac_buf", "sheet": "Channel_1_NEW"},
            {"template": "dac_buf_tpl", "name": "ch2_dac_buf", "sheet": "Channel_2"},
        ]

    def test_delete_all_rows_drops_the_section(self, tmp_path):
        from kicadstamp.config_writer import upsert_tree_instances
        p = self._file(tmp_path, [
            {"template": "dac_buf_tpl", "name": "ch1_dac_buf", "sheet": "Channel_1"},
        ])
        changed = upsert_tree_instances(p, "dac_buf_tpl", [])
        assert changed is True
        assert self._read_instances(p) == []

    def test_no_change_is_a_noop(self, tmp_path):
        from kicadstamp.config_writer import upsert_tree_instances
        rows = [{"name": "ch1_dac_buf", "sheet": "Channel_1"}]
        p = self._file(tmp_path)
        upsert_tree_instances(p, "dac_buf_tpl", rows)
        before = p.read_bytes()
        changed = upsert_tree_instances(p, "dac_buf_tpl", rows)
        assert changed is False
        assert p.read_bytes() == before


def _net_trace_template_data(instances, anchor_sheet="Channel_0",
                             net="/Channel_0/DAC/+3V3_AVDD",
                             include_record=True, template_anchor=None) -> dict:
    """A role-anchored template tree `dac_buf_tpl` carrying ONE placement node
    (`dac_buf`) + ONE kind=net_trace node (`net`), plus the matching flat
    net_traces: record. `net` is a board net path whose leading segment equals
    `anchor_sheet` (the template's own sheet), as in the real dac_buf copper."""
    record = {
        "net": net, "anchor_role": "DAC_BUF", "anchor_sheet": anchor_sheet,
        "tracks": [{
            "start_along_mm": 0.0, "start_across_mm": 0.0,
            "end_along_mm": 1.0, "end_across_mm": 2.0,
            "width_mm": 0.3, "layer": "F.Cu", "net": net,
        }],
        "vias": [{
            "offset_along_mm": 0.5, "offset_across_mm": 0.5,
            "drill_mm": 0.3, "diameter_mm": 0.6, "net": net,
        }],
    }
    return {
        "cells": {},
        "entities": [
            {"name": "dac_buf", "cell": "c_dac", "cluster": "DAC_BUF",
             "sheet": anchor_sheet},
        ],
        "net_traces": [record] if include_record else [],
        "trees": [{
            "name": "dac_buf_tpl",
            "anchor": template_anchor if template_anchor is not None
                     else {"role": "DAC_BUF", "sheet": anchor_sheet},
            "nodes": [
                {"ref": "dac_buf", "kind": "placement", "xy": [1.0, 2.0],
                 "rotation": 90.0},
                {"ref": net, "kind": "net_trace"},
            ],
        }],
        "tree_instances": instances,
    }


class TestNetTrace:
    """v1.1 (2026-09-02, plan_2026_09_02_tree_instances_net_trace.md, design
    §10 "Вариант Б"): a template's kind=net_trace node materializes one
    net_traces: copy per instance — the net's LEADING sheet segment is
    substituted (NOT the placement __{instance} suffix), because a net_trace
    node's ref is a real board net name that must survive into
    net_trace_planner/KiCad."""

    @staticmethod
    def _nt_by_net(cfg, net):
        return next(nt for nt in cfg.net_traces if nt.net == net)

    def test_net_trace_nodes_materialize_one_record_per_instance(self, tmp_path):
        p = _write(tmp_path, "t.sexp", _net_trace_template_data([
            {"template": "dac_buf_tpl", "name": "ch1_dac_buf", "sheet": "Channel_1"},
            {"template": "dac_buf_tpl", "name": "ch2_dac_buf", "sheet": "Channel_2"},
        ]))
        cfg, _ = load_config(str(p))

        # template record untouched + one generated copy per instance; the
        # distinct nets keep the existing net_traces dedup (one record per net)
        # happy — load_config above would have fataled on a duplicate net.
        assert [nt.net for nt in cfg.net_traces] == [
            "/Channel_0/DAC/+3V3_AVDD",
            "/Channel_1/DAC/+3V3_AVDD",
            "/Channel_2/DAC/+3V3_AVDD",
        ]
        # template record keeps its own sheet (deep-copy expansion, never mutates)
        assert self._nt_by_net(cfg, "/Channel_0/DAC/+3V3_AVDD").anchor_sheet == "Channel_0"

        for inst, sheet in (("ch1_dac_buf", "Channel_1"),
                            ("ch2_dac_buf", "Channel_2")):
            new_net = f"/{sheet}/DAC/+3V3_AVDD"
            nt = self._nt_by_net(cfg, new_net)
            assert nt.anchor_role == "DAC_BUF"
            # net + every track/via net rewritten to the instance's sheet;
            # anchor_sheet unconditionally overwritten (Q2 pattern)
            assert nt.anchor_sheet == sheet
            assert nt.tracks[0].net == new_net
            assert nt.vias[0].net == new_net
            # the generated tree's net_trace node references the NEW net (not a
            # __{instance} suffix) -> linking by_key["net_trace:" + net] resolves
            tree = _tree_by_name(cfg, inst)
            nt_node = next(n for n in tree.nodes if n.kind == "net_trace")
            assert nt_node.ref == new_net
            assert tree.anchor.anchor_sheet == sheet
            # the placement sibling is still suffixed as before (v1 unchanged)
            assert any(n.ref == f"dac_buf__{inst}" for n in tree.nodes)

        # the template's own net_trace node ref is untouched
        tpl = _tree_by_name(cfg, "dac_buf_tpl")
        assert next(n for n in tpl.nodes if n.kind == "net_trace").ref == \
            "/Channel_0/DAC/+3V3_AVDD"

    def test_net_leading_segment_not_template_sheet_is_fatal(self, tmp_path):
        """A net whose leading segment isn't the template's anchor sheet is NOT
        this template's copper — the rewrite must be a fatal, never silent."""
        data = _net_trace_template_data(
            [{"template": "dac_buf_tpl", "name": "ch1_dac_buf", "sheet": "Channel_1"}],
            anchor_sheet="Channel_0",
            net="/Foo/DAC/+3V3_AVDD")
        p = _write(tmp_path, "t.sexp", data)
        with pytest.raises(ValidationError, match="net path"):
            load_config(str(p))

    def test_net_trace_node_without_record_is_fatal(self, tmp_path):
        """A net_trace node referencing a net with no net_traces: record is a
        fatal (symmetric to test_placement_node_without_entity_is_fatal)."""
        data = _net_trace_template_data(
            [{"template": "dac_buf_tpl", "name": "ch1_dac_buf", "sheet": "Channel_1"}],
            net="/Channel_0/DAC/+3V3_AVDD",
            include_record=False)
        p = _write(tmp_path, "t.sexp", data)
        with pytest.raises(ValidationError, match="no matching net_traces"):
            load_config(str(p))

    def test_net_trace_node_requires_template_anchor_sheet(self, tmp_path):
        """old_sheet comes from the template's role-anchor sheet — a template
        carrying net_trace copper but a role anchor without a sheet cannot
        decide what to rewrite and is a fatal."""
        data = _net_trace_template_data(
            [{"template": "dac_buf_tpl", "name": "ch1_dac_buf", "sheet": "Channel_1"}],
            net="/Channel_0/DAC/+3V3_AVDD",
            template_anchor={"role": "DAC_BUF"})  # role anchor, NO sheet
        p = _write(tmp_path, "t.sexp", data)
        with pytest.raises(ValidationError, match="anchor sheet"):
            load_config(str(p))

    def test_non_net_trace_kinds_stay_fatal(self, tmp_path):
        """Regression: only net_trace is lifted out of the v1 kind ban —
        a module node inside a template is still a fatal (the existing
        test_non_placement_node_kind_is_fatal covers 'module' via node_kind;
        this pins the same for 'chain' beside a valid net_trace node)."""
        data = _net_trace_template_data(
            [{"template": "dac_buf_tpl", "name": "ch1_dac_buf", "sheet": "Channel_1"}])
        data["trees"][0]["nodes"].append(
            {"ref": "other", "kind": "chain", "xy": [3.0, 4.0]})
        p = _write(tmp_path, "t.sexp", data)
        with pytest.raises(ValidationError, match="unsupported node kind 'chain'"):
            load_config(str(p))


class TestClusterOverride:
    """v1.2 (2026-09-03, plan tree_instances_cluster): the OPTIONAL `cluster:`
    declaration override is substituted into every generated Entity copy's
    cluster AND the generated role anchor's cluster — mirroring `sheet`. When a
    declaration carries NO cluster, nothing changes (back-compat: the copies
    inherit the template Entity's own cluster exactly as before)."""

    def test_cluster_override_substituted_into_copies_and_role_anchor(self, tmp_path):
        p = _write(tmp_path, "t.sexp", _template_data([
            {"template": "dac_buf_tpl", "name": "ch1_dac_buf", "sheet": "Channel_1",
             "cluster": "CLUST_A"},
        ]))
        cfg, _ = load_config(str(p))

        # cfg.tree_instances keeps the RAW declaration including cluster.
        assert cfg.tree_instances == [
            TreeInstance(template="dac_buf_tpl", name="ch1_dac_buf",
                         sheet="Channel_1", cluster="CLUST_A"),
        ]
        # Template entities keep their OWN clusters (deep-copy, never mutated).
        assert _entity_by_name(cfg, "dac_buf").cluster == "DAC_BUF"
        assert _entity_by_name(cfg, "pif_avdd").cluster == "PIF_AVDD"
        # Generated Entity copies (every nesting level) get the override.
        assert _entity_by_name(cfg, "dac_buf__ch1_dac_buf").cluster == "CLUST_A"
        assert _entity_by_name(cfg, "pif_avdd__ch1_dac_buf").cluster == "CLUST_A"
        # The generated role anchor gets the override too ...
        assert _tree_by_name(cfg, "ch1_dac_buf").anchor.anchor_cluster == "CLUST_A"
        # ... while the template's own anchor cluster stays absent.
        assert _tree_by_name(cfg, "dac_buf_tpl").anchor.anchor_cluster is None

    def test_no_cluster_inherits_template_entity_cluster_unchanged(self, tmp_path):
        """THE back-compat regression: a declaration without `cluster:` must
        behave EXACTLY as before — each generated copy inherits the cluster of
        its own template Entity (dac_buf -> DAC_BUF, nested pif_avdd ->
        PIF_AVDD), and the generated anchor keeps the template's (absent)
        cluster."""
        p = _write(tmp_path, "t.sexp", _template_data([
            {"template": "dac_buf_tpl", "name": "ch1_dac_buf", "sheet": "Channel_1"},
        ]))
        cfg, _ = load_config(str(p))
        assert cfg.tree_instances[0].cluster is None
        assert _entity_by_name(cfg, "dac_buf__ch1_dac_buf").cluster == "DAC_BUF"
        assert _entity_by_name(cfg, "pif_avdd__ch1_dac_buf").cluster == "PIF_AVDD"
        assert _tree_by_name(cfg, "ch1_dac_buf").anchor.anchor_cluster is None

    def test_empty_cluster_is_fatal(self, tmp_path):
        """Same discipline as `sheet`: `cluster:` present but empty is a fatal
        (omit the key entirely to inherit — an empty override is meaningless)."""
        p = _write(tmp_path, "t.sexp", _template_data([
            {"template": "dac_buf_tpl", "name": "ch1_dac_buf", "sheet": "Channel_1",
             "cluster": ""},
        ]))
        with pytest.raises(ValidationError, match="empty cluster"):
            load_config(str(p))

    def test_unknown_key_still_fatal(self):
        """`cluster` is now a KNOWN key — but a typo'd sibling key must STILL
        fatal (check_unknown_keys keeps guarding the declaration record). The
        s-expr writer already rejects a stray key at serialization (typed
        record), so this exercises the LOADER's own check on the raw dict."""
        from kicadstamp.config import load_tree_instance
        with pytest.raises(ValidationError, match="unknown fields in tree_instances"):
            load_tree_instance({"template": "dac_buf_tpl", "name": "ch1_dac_buf",
                                "sheet": "Channel_1", "cluter": "CLUST_A"})

    def test_net_trace_anchor_cluster_ignores_declaration_cluster(self, tmp_path):
        """The design §3 split, enforced in code: a declaration-level `cluster:`
        override rewrites the Entity copies (and role anchor), but MUST NOT
        leak into net_trace materialization — a net_trace's anchor_cluster is
        a different concept (external anchor search), never overwritten here."""
        p = _write(tmp_path, "t.sexp", _net_trace_template_data([
            {"template": "dac_buf_tpl", "name": "ch1_dac_buf", "sheet": "Channel_1",
             "cluster": "CLUST_A"},
        ]))
        cfg, _ = load_config(str(p))
        nt = next(nt for nt in cfg.net_traces if nt.net == "/Channel_1/DAC/+3V3_AVDD")
        assert nt.anchor_cluster is None   # NOT rewritten by the declaration
        # ... but the placement sibling DID get the override — the declaration
        # was not simply dropped, only net_trace ignores it.
        assert _entity_by_name(cfg, "dac_buf__ch1_dac_buf").cluster == "CLUST_A"


class TestTreeInstanceWriterCluster:
    """Persistence of the OPTIONAL cluster axis (2026-09-03, plan
    tree_instances_cluster): upsert_tree_instances writes a row's non-empty
    `cluster` into the declaration; an empty/absent cluster omits the key."""

    @staticmethod
    def _read_instances(p) -> list:
        from kicadstamp.config.sexp_format import sexp_to_dict
        return list(sexp_to_dict(p.read_text(encoding="utf-8"))
                    .get("tree_instances") or [])

    def _file(self, tmp_path):
        p = _write(tmp_path, "w.sexp", _template_data([]))
        return p

    def test_row_with_cluster_writes_cluster_key_and_materializes(self, tmp_path):
        from kicadstamp.config_writer import upsert_tree_instances
        p = self._file(tmp_path)
        changed = upsert_tree_instances(p, "dac_buf_tpl", [
            {"name": "ch1_dac_buf", "sheet": "Channel_1", "cluster": "CLUST_A"}])
        assert changed is True
        assert self._read_instances(p) == [{
            "template": "dac_buf_tpl", "name": "ch1_dac_buf", "sheet": "Channel_1",
            "cluster": "CLUST_A"}]
        cfg, _ = load_config(str(p))
        assert _entity_by_name(cfg, "dac_buf__ch1_dac_buf").cluster == "CLUST_A"

    def test_blank_cluster_is_omitted_not_written(self, tmp_path):
        """A row with a blank cluster writes NO cluster key at all (the file
        stays clean for declarations that don't need the cluster axis) — the
        key is never persisted as null/empty."""
        from kicadstamp.config_writer import upsert_tree_instances
        p = self._file(tmp_path)
        changed = upsert_tree_instances(p, "dac_buf_tpl", [
            {"name": "ch1_dac_buf", "sheet": "Channel_1", "cluster": ""}])
        assert changed is True
        assert self._read_instances(p) == [{
            "template": "dac_buf_tpl", "name": "ch1_dac_buf", "sheet": "Channel_1"}]
