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
                   node_kind="placement", nodes=None,
                   uniform_cluster=None) -> dict:
    """A minimal valid config: one role-anchored template tree `dac_buf_tpl`
    whose two entities `dac_buf`/`pif_avdd` live on one nested level.

    The template is COMPOSITE by default (the two entities carry genuinely
    different clusters DAC_BUF/PIF_AVDD — the ch0_dac_buf shape). Pass
    `uniform_cluster` to make it HOMOGENEOUS (both entities share one cluster)
    — the only case where the v1.2 `cluster:` override still applies to every
    generated copy (see TestClusterCompositeGuard)."""
    main_cluster = uniform_cluster if uniform_cluster is not None else "DAC_BUF"
    sub_cluster = uniform_cluster if uniform_cluster is not None else "PIF_AVDD"
    return {
        "cells": {},
        "entities": [
            {"name": "dac_buf", "cell": "c_dac", "cluster": main_cluster},
            {"name": "pif_avdd", "cell": "c_pif", "cluster": sub_cluster,
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
        """Regression (v1.4 keeps it fatal): an explicit NON-role anchor
        (origin/ref/point) is neither a role-anchored NOR an auto-anchored
        template — the fix only relaxes the NO-(anchor ...)-at-all auto case;
        origin/ref/point anchors are still not parameterized by sheet."""
        p = _write(tmp_path, "t.sexp", _template_data(
            [{"template": "dac_buf_tpl", "name": "ch1_dac_buf", "sheet": "Channel_1"}],
            anchor={"origin": True}))
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
    """v1.2 (2026-09-03, plan tree_instances_cluster) on a HOMOGENEOUS template:
    the OPTIONAL `cluster:` declaration override is substituted into every
    generated Entity copy's cluster AND the generated role anchor's cluster —
    mirroring `sheet`. v1.2.1 (2026-09-08, composite-guard) RESTRICTS this
    unconditional per-copy substitution to homogeneous templates (see
    TestClusterCompositeGuard) — these fixtures carry a homogeneous template
    (uniform_cluster) on purpose, so the v1.2 behaviour they pin is exactly the
    pre-fix behaviour (the non-regression). When a declaration carries NO
    cluster, nothing changes (back-compat: the copies inherit the template
    Entity's own cluster exactly as before)."""

    def test_cluster_override_substituted_into_copies_and_role_anchor(self, tmp_path):
        p = _write(tmp_path, "t.sexp", _template_data([
            {"template": "dac_buf_tpl", "name": "ch1_dac_buf", "sheet": "Channel_1",
             "cluster": "CLUST_A"},
        ], uniform_cluster="DAC_BUF"))
        cfg, _ = load_config(str(p))

        # cfg.tree_instances keeps the RAW declaration including cluster.
        assert cfg.tree_instances == [
            TreeInstance(template="dac_buf_tpl", name="ch1_dac_buf",
                         sheet="Channel_1", cluster="CLUST_A"),
        ]
        # Template entities keep their OWN clusters (deep-copy, never mutated);
        # the homogeneous fixture's template entities both carry DAC_BUF.
        assert _entity_by_name(cfg, "dac_buf").cluster == "DAC_BUF"
        assert _entity_by_name(cfg, "pif_avdd").cluster == "DAC_BUF"
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


class TestParamsOverride:
    """v1.3 (2026-09-07, plan tree_instances_params_override): the OPTIONAL
    `params:` declaration override is MERGED (per-key) into every generated
    Entity copy's OWN `params` — the {placeholder}-substitution values
    net_resolution.resolve_net reads from a role's net_template. Same
    "override wins, rest inherited" semantics as `cluster`, per-key instead
    of whole-field; a declaration WITHOUT `params` (None) inherits the
    template Entity's params unchanged (back-compat)."""

    def test_params_override_replaces_in_generated_entity(self, tmp_path):
        """Template Entity parametrized to Channel_0; the declaration overrides
        channel_sheet to Channel_1 -> every generated copy (top-level and
        nested) resolves to Channel_1; the template Entity itself keeps its own
        params untouched (deep-copy, never mutated)."""
        data = _template_data([
            {"template": "dac_buf_tpl", "name": "ch1_dac_buf", "sheet": "Channel_1",
             "params": {"channel_sheet": "Channel_1"}},
        ])
        data["entities"][0]["params"] = {"channel_sheet": "Channel_0"}
        p = _write(tmp_path, "t.sexp", data)
        cfg, _ = load_config(str(p))

        # cfg.tree_instances keeps the RAW declaration including params.
        assert cfg.tree_instances == [
            TreeInstance(template="dac_buf_tpl", name="ch1_dac_buf",
                         sheet="Channel_1", params={"channel_sheet": "Channel_1"}),
        ]
        # Template entities keep their OWN params (deep-copy, never mutated).
        assert _entity_by_name(cfg, "dac_buf").params == \
            {"channel_sheet": "Channel_0"}
        assert _entity_by_name(cfg, "pif_avdd").params == {}
        # Generated Entity copies (every nesting level) get the override; a
        # nested template Entity with NO own params still gets the override.
        assert _entity_by_name(cfg, "dac_buf__ch1_dac_buf").params == \
            {"channel_sheet": "Channel_1"}
        assert _entity_by_name(cfg, "pif_avdd__ch1_dac_buf").params == \
            {"channel_sheet": "Channel_1"}

    def test_params_override_merge_keeps_untouched_keys(self, tmp_path):
        """MERGE, not whole-dict replace: a key NOT named in the declaration
        keeps the template Entity's value; only the named key is overridden."""
        data = _template_data([
            {"template": "dac_buf_tpl", "name": "ch1_dac_buf", "sheet": "Channel_1",
             "params": {"b": "9"}},
        ])
        data["entities"][0]["params"] = {"a": "1", "b": "2"}
        p = _write(tmp_path, "t.sexp", data)
        cfg, _ = load_config(str(p))
        assert _entity_by_name(cfg, "dac_buf__ch1_dac_buf").params == \
            {"a": "1", "b": "9"}

    def test_params_none_keeps_template_params_unchanged(self, tmp_path):
        """THE back-compat regression: a declaration without `params:` must
        behave EXACTLY as before — each generated copy inherits its template
        Entity's own params 1:1."""
        data = _template_data([
            {"template": "dac_buf_tpl", "name": "ch1_dac_buf", "sheet": "Channel_1"},
        ])
        data["entities"][0]["params"] = {"a": "1"}
        p = _write(tmp_path, "t.sexp", data)
        cfg, _ = load_config(str(p))
        assert cfg.tree_instances[0].params is None
        assert _entity_by_name(cfg, "dac_buf__ch1_dac_buf").params == {"a": "1"}
        # nested template Entity had no params -> generated copy has none either
        assert _entity_by_name(cfg, "pif_avdd__ch1_dac_buf").params == {}

    def test_params_override_not_a_dict_is_fatal_on_expansion(self):
        """dict-level expansion runs BEFORE _load_tree_instance inside
        load_config (see the module docstring), so a non-mapping params first
        hits expand_tree_instances' own duplicated guard."""
        from kicadstamp.config.tree_instances import expand_tree_instances
        with pytest.raises(ValidationError, match="non-mapping params"):
            expand_tree_instances({"entities": [], "trees": [], "net_traces": [],
                                   "tree_instances": [
                                       {"template": "dac_buf_tpl",
                                        "name": "ch1_dac_buf",
                                        "sheet": "Channel_1",
                                        "params": "not-a-dict"}]})

    def test_params_override_not_a_dict_is_fatal_on_loader(self):
        """The loader's OWN guard (entries.py::_load_tree_instance) — the same
        strictness when the declaration is parsed directly (e.g. GUI single-
        entry validation), reached without dict-level expansion."""
        from kicadstamp.config import load_tree_instance
        with pytest.raises(ValidationError, match="non-mapping params"):
            load_tree_instance({"template": "dac_buf_tpl",
                                "name": "ch1_dac_buf",
                                "sheet": "Channel_1",
                                "params": "not-a-dict"})

    def test_end_to_end_net_template_placeholder_resolves_per_instance(
            self, tmp_path):
        """The scenario this whole plan exists for: a role whose net_template
        is '/{channel_sheet}/DAC/+3V3_AVDD', template Entity parametrized to
        Channel_0, tree_instance declarations overriding channel_sheet per
        instance — net_resolution.resolve_net must resolve each generated
        Entity to ITS OWN channel (Channel_1 vs Channel_2), not the template's.
        (net_resolution.py is untouched — this proves the fix is purely in
        which params a generated Entity carries.)"""
        from kicadstamp.net_resolution import resolve_net
        data = _template_data([
            {"template": "dac_buf_tpl", "name": "ch1_dac_buf", "sheet": "Channel_1",
             "params": {"channel_sheet": "Channel_1"}},
            {"template": "dac_buf_tpl", "name": "ch2_dac_buf", "sheet": "Channel_2",
             "params": {"channel_sheet": "Channel_2"}},
        ])
        # the template Entity is parametrized to its OWN channel (Channel_0)
        data["entities"][0]["params"] = {"channel_sheet": "Channel_0"}
        p = _write(tmp_path, "t.sexp", data)
        cfg, _ = load_config(str(p))
        net_template = "/{channel_sheet}/DAC/+3V3_AVDD"

        tpl = _entity_by_name(cfg, "dac_buf")
        assert resolve_net(net_template, tpl.params, tpl.net_overrides) == \
            "/Channel_0/DAC/+3V3_AVDD"
        for name, expected in (
                ("dac_buf__ch1_dac_buf", "/Channel_1/DAC/+3V3_AVDD"),
                ("dac_buf__ch2_dac_buf", "/Channel_2/DAC/+3V3_AVDD")):
            ent = _entity_by_name(cfg, name)
            assert resolve_net(net_template, ent.params, ent.net_overrides) == expected


class TestTreeInstanceWriterCluster:
    """Persistence of the OPTIONAL cluster axis (2026-09-03, plan
    tree_instances_cluster): upsert_tree_instances writes a row's non-empty
    `cluster` into the declaration; an empty/absent cluster omits the key.
    The fixture template is HOMOGENEOUS (uniform_cluster) so the materialized
    per-copy override the writer tests reach is the v1.2 behaviour (the
    composite-guard's per-copy skip is covered by TestClusterCompositeGuard)."""

    @staticmethod
    def _read_instances(p) -> list:
        from kicadstamp.config.sexp_format import sexp_to_dict
        return list(sexp_to_dict(p.read_text(encoding="utf-8"))
                    .get("tree_instances") or [])

    def _file(self, tmp_path):
        p = _write(tmp_path, "w.sexp", _template_data([], uniform_cluster="DAC_BUF"))
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


def _composite_dac_buf_data(instances) -> dict:
    """A template shaped like the live ch0_dac_buf: one DAC_BUF main entity
    plus three PIF_AVDD/PIF_CLKVDD/PIF_DVDD sub-blocks (four placement nodes,
    four genuinely different template clusters) — the COMPOSITE case that the
    v1.2.1 composite-guard must NOT blanket-override per copy."""
    return {
        "cells": {},
        "entities": [
            {"name": "dac_buf", "cell": "c_dac", "cluster": "DAC_BUF"},
            {"name": "pif_avdd", "cell": "c_pif", "cluster": "PIF_AVDD"},
            {"name": "pif_clkvdd", "cell": "c_pif", "cluster": "PIF_CLKVDD"},
            {"name": "pif_dvdd", "cell": "c_pif", "cluster": "PIF_DVDD"},
        ],
        "trees": [{
            "name": "dac_buf_tpl",
            "anchor": {"role": "DAC_BUF"},
            "nodes": [
                {"ref": "dac_buf", "kind": "placement", "xy": [1.0, 2.0],
                 "rotation": 90.0},
                {"ref": "pif_avdd", "kind": "placement", "xy": [0.5, 0.0]},
                {"ref": "pif_clkvdd", "kind": "placement", "xy": [1.5, 0.0]},
                {"ref": "pif_dvdd", "kind": "placement", "xy": [2.5, 0.0]},
            ],
        }],
        "tree_instances": instances,
    }


class TestClusterCompositeGuard:
    """v1.2.1 (2026-09-08, plan tree_instances_cluster_composite_guard): the
    per-copy half of the declaration's `cluster:` override only applies to a
    HOMOGENEOUS template (its nodes carry at most ONE distinct cluster value).
    A COMPOSITE template (several genuinely different clusters — the ch0_dac_buf
    shape: a DAC_BUF main entity + PIF_AVDD/PIF_CLKVDD/PIF_DVDD sub-blocks)
    skips the per-copy override entirely (each copy keeps its own template
    cluster), because a blanket overwrite would erase exactly the per-node
    distinction role_narrowing's Cluster step resolves on (live bug
    done_2026_09_08_role_narrowing_live_probe.md). The generated role anchor's
    cluster is STILL overridden (an external-anchor narrowing, separate)."""

    # ── 1. unit tests on the structural helper ────────────────────────────────

    def test_helper_empty_node_list(self):
        from kicadstamp.config.tree_instances import _template_generated_clusters
        assert _template_generated_clusters([], {}) == set()

    def test_helper_single_shared_cluster_is_homogeneous(self):
        from kicadstamp.config.tree_instances import _template_generated_clusters
        entities = {"a": {"cluster": "DAC_BUF"}, "b": {"cluster": "DAC_BUF"}}
        nodes = [{"ref": "a", "kind": "placement"},
                 {"ref": "b", "kind": "placement"}]
        assert _template_generated_clusters(nodes, entities) == {"DAC_BUF"}

    def test_helper_mixed_clusters_are_composite(self):
        from kicadstamp.config.tree_instances import _template_generated_clusters
        entities = {"a": {"cluster": "DAC_BUF"}, "b": {"cluster": "PIF_AVDD"}}
        nodes = [{"ref": "a", "kind": "placement"},
                 {"ref": "b", "kind": "placement"}]
        assert _template_generated_clusters(nodes, entities) == \
            {"DAC_BUF", "PIF_AVDD"}

    def test_helper_missing_entity_record_is_skipped(self):
        """A placement node whose ref has no entities: entry would be a
        load-time fatal elsewhere — the probe must not crash on it."""
        from kicadstamp.config.tree_instances import _template_generated_clusters
        entities = {"a": {"cluster": "DAC_BUF"}}
        nodes = [{"ref": "a", "kind": "placement"},
                 {"ref": "no_such_entity", "kind": "placement"}]
        assert _template_generated_clusters(nodes, entities) == {"DAC_BUF"}

    def test_helper_nested_children_counted_recursively(self):
        from kicadstamp.config.tree_instances import _template_generated_clusters
        entities = {"a": {"cluster": "DAC_BUF"},
                    "b": {"cluster": "PIF_AVDD"},
                    "c": {"cluster": "PIF_DVDD"}}
        nodes = [{"ref": "a", "kind": "placement", "children": [
            {"ref": "b", "kind": "placement", "children": [
                {"ref": "c", "kind": "placement"}]}]}]
        assert _template_generated_clusters(nodes, entities) == \
            {"DAC_BUF", "PIF_AVDD", "PIF_DVDD"}

    def test_helper_net_trace_nodes_skipped(self):
        """net_trace nodes never carry a per-copy override (v1.2 design §3) and
        their ref names a net, not an entity — the probe ignores them."""
        from kicadstamp.config.tree_instances import _template_generated_clusters
        entities = {"a": {"cluster": "DAC_BUF"}}
        nodes = [{"ref": "a", "kind": "placement"},
                 {"ref": "/Channel_0/DAC/+3V3", "kind": "net_trace"}]
        assert _template_generated_clusters(nodes, entities) == {"DAC_BUF"}

    # ── 2. homogeneous non-regression: byte-identical pre-fix behaviour ───────

    def test_homogeneous_template_override_applied_to_every_copy(self, tmp_path):
        """THE back-compat guarantee: on a HOMOGENEOUS template the per-copy
        `cluster:` override still lands on EVERY generated Entity copy (every
        nesting level) exactly as before the composite-guard — a composite
        template (default _template_data fixture) is the ONLY case whose
        behaviour changed."""
        p = _write(tmp_path, "t.sexp", _template_data([
            {"template": "dac_buf_tpl", "name": "ch1_dac_buf", "sheet": "Channel_1",
             "cluster": "CLUST_A"},
        ], uniform_cluster="DAC_BUF"))
        cfg, _ = load_config(str(p))
        assert _entity_by_name(cfg, "dac_buf__ch1_dac_buf").cluster == "CLUST_A"
        assert _entity_by_name(cfg, "pif_avdd__ch1_dac_buf").cluster == "CLUST_A"
        # Role anchor override is unconditional (external-anchor concept) ...
        assert _tree_by_name(cfg, "ch1_dac_buf").anchor.anchor_cluster == "CLUST_A"

    # ── 3. composite end-to-end (ch0_dac_buf form) ────────────────────────────

    def test_composite_override_skipped_each_copy_keeps_own_cluster(self, tmp_path):
        """The live scenario of plan_2026_09_08_role_narrowing_live_probe.md,
        synthetic: a ch0_dac_buf-shaped COMPOSITE template (DAC_BUF + 3 PIF
        sub-blocks), declaration `cluster: DAC_BUF`. The per-copy override is
        SKIPPED — the DAC_BUF main copy keeps DAC_BUF only because that is its
        OWN template cluster (proving the override did NOT run: the three PIF
        copies keep PIF_AVDD/PIF_CLKVDD/PIF_DVDD, NOT DAC_BUF)."""
        p = _write(tmp_path, "t.sexp", _composite_dac_buf_data([
            {"template": "dac_buf_tpl", "name": "ch1_dac_buf", "sheet": "Channel_1",
             "cluster": "DAC_BUF"},
        ]))
        cfg, _ = load_config(str(p))
        assert _entity_by_name(cfg, "dac_buf__ch1_dac_buf").cluster == "DAC_BUF"
        assert _entity_by_name(cfg, "pif_avdd__ch1_dac_buf").cluster == "PIF_AVDD"
        assert _entity_by_name(cfg, "pif_clkvdd__ch1_dac_buf").cluster == "PIF_CLKVDD"
        assert _entity_by_name(cfg, "pif_dvdd__ch1_dac_buf").cluster == "PIF_DVDD"
        # Template entities themselves are never mutated (deep-copy expansion).
        assert _entity_by_name(cfg, "pif_avdd").cluster == "PIF_AVDD"
        assert _entity_by_name(cfg, "dac_buf").cluster == "DAC_BUF"

    # ── 4. anchor override is NOT affected by compositeness ───────────────────

    def test_composite_anchor_cluster_still_overridden(self, tmp_path):
        """The declaration's `cluster:` lands on the generated role anchor EVEN
        for a composite template — the two substitutions (per-copy vs role
        anchor / anchor_cluster) are separate concepts, per the module
        docstring's anchor_cluster/cluster split."""
        p = _write(tmp_path, "t.sexp", _composite_dac_buf_data([
            {"template": "dac_buf_tpl", "name": "ch1_dac_buf", "sheet": "Channel_1",
             "cluster": "CLUST_OVERRIDE"},
        ]))
        cfg, _ = load_config(str(p))
        assert _tree_by_name(cfg, "ch1_dac_buf").anchor.anchor_cluster == "CLUST_OVERRIDE"
        # ... while the copies keep their own template clusters (no per-copy
        # override) — proving the anchor line was not the only thing set.
        assert _entity_by_name(cfg, "pif_avdd__ch1_dac_buf").cluster == "PIF_AVDD"

    # ── 5. live-shaped role_narrowing regression (no step-5 reliance) ─────────

    def test_role_narrowing_cluster_step_now_narrows_3_to_1(self, tmp_path):
        """Closes the chain from done_2026_09_08_role_narrowing_live_probe.md:
        a MATERIALIZED pif_avdd copy (post-composite-guard, so it carries
        cluster=PIF_AVDD, sheet=Channel_1) run through the real cascade
        (_narrow_ambiguous_candidates -> sheet -> Cluster) with three live
        Channel_1 PIF candidates (C144=PIF_CLKVDD, C149=PIF_AVDD,
        C153=PIF_DVDD, all on net +3V3) — the Cluster step now narrows 3->1 by
        PIF_AVDD. No anchor_position is passed at all, so step 5 (physical
        proximity) is provably unused."""
        from unittest.mock import MagicMock
        from kicadstamp.constants import CLUSTER_FIELD_NAME
        from kicadstamp.placement.services.role_narrowing import (
            _narrow_ambiguous_candidates,
        )

        p = _write(tmp_path, "t.sexp", _composite_dac_buf_data([
            {"template": "dac_buf_tpl", "name": "ch1_dac_buf", "sheet": "Channel_1",
             "cluster": "DAC_BUF"},
        ]))
        cfg, _ = load_config(str(p))
        materialized = _entity_by_name(cfg, "pif_avdd__ch1_dac_buf")
        assert materialized.cluster == "PIF_AVDD"
        assert materialized.sheet == "Channel_1"

        # Three live PIF candidates of Channel_1, each with a different live
        # Cluster (the form of C144/C149/C153 from the live probe).
        candidates = []
        live_fields = {}
        for ref, cluster in (("C144", "PIF_CLKVDD"),
                             ("C149", "PIF_AVDD"),
                             ("C153", "PIF_DVDD")):
            fp = MagicMock()
            fp.ref = ref
            fp.sheet_path_uuids = ("ch1-sheet-uuid", f"{ref}-own-uuid")
            candidates.append(fp)
            live_fields[fp] = {CLUSTER_FIELD_NAME: cluster}

        adapter = MagicMock()
        adapter.get_field_value.side_effect = \
            lambda fp, field, default=None: live_fields[fp].get(field, default)

        narrowed, _note = _narrow_ambiguous_candidates(
            candidates, materialized, adapter,
            selected_refs=set(), anchor_position=None,
            clone_name=materialized.name, role="C_IN_BYPASS",
            sheet_names={"ch1-sheet-uuid": "Channel_1"})
        assert [fp.ref for fp in narrowed] == ["C149"]


def _auto_net_trace_template_data(instances) -> dict:
    """An AUTO-anchored template shaped like the live ch0_dac_buf copper: NO
    (anchor ...) at all and EXACTLY ONE top-level placement node (the root
    `dac_buf`) — the only legal auto shape — whose children mix a nested
    placement (`pif_avdd`) with kind=net_trace DAC-copper nodes. Every net (and
    the root Entity's own sheet) starts on /Channel_0/: `old_sheet` for the
    net_trace leading-segment rewrite must come from the ROOT ENTITY record —
    there is no anchor.sheet to read (exactly the case the v1.4 fix adds)."""
    nets = ["/Channel_0/DAC/+3V3_AVDD", "/Channel_0/DAC/+3V3A_AVDD"]
    records = []
    for net in nets:
        records.append({
            "net": net, "anchor_role": "DAC_BUF", "anchor_sheet": "Channel_0",
            "tracks": [{
                "start_along_mm": 0.0, "start_across_mm": 0.0,
                "end_along_mm": 1.0, "end_across_mm": 2.0,
                "width_mm": 0.3, "layer": "F.Cu", "net": net}],
            "vias": [{
                "offset_along_mm": 0.5, "offset_across_mm": 0.5,
                "drill_mm": 0.3, "diameter_mm": 0.6, "net": net}],
        })
    return {
        "cells": {},
        "entities": [
            {"name": "dac_buf", "cell": "c_dac", "cluster": "DAC_BUF",
             "sheet": "Channel_0"},
            {"name": "pif_avdd", "cell": "c_pif", "cluster": "PIF_AVDD",
             "sheet": "Channel_0"},
        ],
        "net_traces": records,
        "trees": [{
            "name": "dac_buf_tpl",     # NO (anchor ...) -> auto-anchored
            "nodes": [{
                "ref": "dac_buf", "kind": "placement", "xy": [1.0, 2.0],
                "rotation": 90.0,
                "children": [
                    {"ref": "pif_avdd", "kind": "placement",
                     "xy": [0.5, 0.0]},
                    {"ref": nets[0], "kind": "net_trace"},
                    {"ref": nets[1], "kind": "net_trace"},
                ],
            }],
        }],
        "tree_instances": instances,
    }


class TestAutoAnchorTemplates:
    """v1.4 (2026-09-08, plan tree_instances_auto_root_template_support): an
    auto-anchored template — NO (anchor ...) at all (TreeAnchor.is_auto) with
    EXACTLY ONE top-level placement node — is now a valid tree_instances
    template, alongside the role-anchored one. For an auto template the anchor
    IS its root node, so sheet/cluster reach the generated copies through the
    same per-node machinery (no separate anchor substitution), old_sheet for
    net_trace rewriting is the root Entity's own sheet, and the generated tree
    stays auto-anchored. The old test_auto_anchor_missing_is_fatal is rewritten
    here as the success case — its fixture is exactly the valid auto shape."""

    @staticmethod
    def _pop_anchor(data):
        del data["trees"][0]["anchor"]  # no (anchor ...) at all -> AUTO
        return data

    def test_single_top_level_node_without_anchor_is_valid(self, tmp_path):
        """Rewrite of the old test_auto_anchor_missing_is_fatal: the default
        single-top-level-node template WITHOUT (anchor ...) is now a VALID auto
        template — load succeeds, the generated tree is auto-anchored, every
        copy (nested included) gets the instance sheet, and the composite
        template's per-copy clusters are left untouched (DAC_BUF/PIF_AVDD)."""
        data = self._pop_anchor(_template_data([
            {"template": "dac_buf_tpl", "name": "ch1_dac_buf",
             "sheet": "Channel_1"}]))
        p = _write(tmp_path, "t.sexp", data)
        cfg, _ = load_config(str(p))
        tree = _tree_by_name(cfg, "ch1_dac_buf")
        assert tree.anchor.is_auto is True
        assert tree.anchor.role is None
        # template itself stays auto too (deep-copy expansion, never mutates)
        assert _tree_by_name(cfg, "dac_buf_tpl").anchor.is_auto is True
        # every copy (root + nested child) gets the instance sheet
        assert _entity_by_name(cfg, "dac_buf__ch1_dac_buf").sheet == "Channel_1"
        assert _entity_by_name(cfg, "pif_avdd__ch1_dac_buf").sheet == "Channel_1"
        # composite template (DAC_BUF/PIF_AVDD) -> per-copy clusters untouched
        assert _entity_by_name(cfg, "dac_buf__ch1_dac_buf").cluster == "DAC_BUF"
        assert _entity_by_name(cfg, "pif_avdd__ch1_dac_buf").cluster == "PIF_AVDD"
        # the root node got the __{instance} suffix like any other node
        assert tree.nodes[0].ref == "dac_buf__ch1_dac_buf"
        assert tree.nodes[0].children[0].ref == "pif_avdd__ch1_dac_buf"

    def test_multiple_top_level_nodes_without_anchor_is_fatal(self, tmp_path):
        """An auto template with TWO top-level nodes can never auto-anchor
        (auto-anchor resolution needs EXACTLY ONE) — a clear fatal at expansion
        time (the new v1.4 message), not a crash later at redraw."""
        nodes = [
            {"ref": "dac_buf", "kind": "placement", "xy": [1.0, 2.0]},
            {"ref": "pif_avdd", "kind": "placement", "xy": [0.5, 0.0]},
        ]
        data = self._pop_anchor(_template_data(
            [{"template": "dac_buf_tpl", "name": "ch1_dac_buf",
              "sheet": "Channel_1"}],
            nodes=nodes))
        p = _write(tmp_path, "t.sexp", data)
        with pytest.raises(ValidationError,
                           match="auto-anchored with exactly one top-level "
                                 "placement node"):
            load_config(str(p))

    def test_top_level_node_missing_entity_is_fatal(self, tmp_path):
        """An auto template whose single top-level node references no existing
        entities: record is a fatal at expansion time (same "no matching
        entities:" wording as the non-root case), separately from the
        >1-top-level-node fatal above."""
        data = self._pop_anchor(_template_data(
            [{"template": "dac_buf_tpl", "name": "ch1_dac_buf",
              "sheet": "Channel_1"}],
            nodes=[{"ref": "no_entity", "kind": "placement",
                    "xy": [0.0, 0.0]}]))
        p = _write(tmp_path, "t.sexp", data)
        with pytest.raises(ValidationError, match="no matching entities"):
            load_config(str(p))

    def test_generated_tree_stays_auto(self, tmp_path):
        """The generated tree must itself stay auto-anchored: the deep copy of
        a template with no (anchor ...) carries no 'anchor' key at all (raw
        dict level), so it loads with is_auto=True — the generated Channel_1
        clone keeps the SAME self-resolving root-node anchor its template has
        (important when it too gets embedded as a module)."""
        from kicadstamp.config.tree_instances import expand_tree_instances
        data = self._pop_anchor(_template_data([
            {"template": "dac_buf_tpl", "name": "ch1_dac_buf",
             "sheet": "Channel_1"}]))
        out = expand_tree_instances(data)
        gen_raw = out["trees"][1]
        assert gen_raw["name"] == "ch1_dac_buf"
        assert "anchor" not in gen_raw   # never inherited an explicit one
        p = _write(tmp_path, "t.sexp", data)
        cfg, _ = load_config(str(p))
        gen = _tree_by_name(cfg, "ch1_dac_buf")
        assert gen.anchor.is_auto is True
        assert not gen.anchor.is_origin and gen.anchor.role is None

    def test_composite_cluster_override_guard_still_applies(self, tmp_path):
        """§1 claim, pinned: the v1.2.1 composite-guard walks the template's
        nodes generically — an auto-anchored COMPOSITE template (default
        DAC_BUF/PIF_AVDD fixture, no anchor) with a declaration `cluster:
        OVERRIDE` still skips the per-copy override for EVERY copy, the root one
        included; and since an auto tree has no anchor, the override lands
        nowhere else either (generated anchor_cluster stays None)."""
        data = self._pop_anchor(_template_data([
            {"template": "dac_buf_tpl", "name": "ch1_dac_buf",
             "sheet": "Channel_1", "cluster": "OVERRIDE"}]))
        p = _write(tmp_path, "t.sexp", data)
        cfg, _ = load_config(str(p))
        assert _entity_by_name(cfg, "dac_buf__ch1_dac_buf").cluster == "DAC_BUF"
        assert _entity_by_name(cfg, "pif_avdd__ch1_dac_buf").cluster == "PIF_AVDD"
        assert _tree_by_name(cfg, "ch1_dac_buf").anchor.anchor_cluster is None

    def test_homogeneous_cluster_override_applies_to_root_too(self, tmp_path):
        """Back-compat mirror of the non-auto homogeneous case: an auto template
        whose nodes all share one cluster (uniform_cluster="SAME") keeps the
        unconditional per-copy override — the root copy gets OVERRIDE too."""
        data = self._pop_anchor(_template_data([
            {"template": "dac_buf_tpl", "name": "ch1_dac_buf",
             "sheet": "Channel_1", "cluster": "OVERRIDE"}],
            uniform_cluster="SAME"))
        p = _write(tmp_path, "t.sexp", data)
        cfg, _ = load_config(str(p))
        assert _entity_by_name(cfg, "dac_buf__ch1_dac_buf").cluster == "OVERRIDE"
        assert _entity_by_name(cfg, "pif_avdd__ch1_dac_buf").cluster == "OVERRIDE"

    def test_net_trace_children_rewritten_from_root_entity_sheet(self, tmp_path):
        """ch0_dac_buf-shaped end-to-end (plan regression): an AUTO template
        (root `dac_buf` + a nested PIF placement + DAC copper net_trace
        children, all on /Channel_0/) instantiated to Channel_1 with a
        `cluster: DAC_BUF` declaration. Expansion must NOT fatal — old_sheet is
        derived from the ROOT ENTITY's sheet (there is no anchor.sheet), every
        net_trace copy is rewritten /Channel_0/... -> /Channel_1/..., and the
        composite-guard keeps the PIF copy's own cluster (per-copy override
        skipped)."""
        p = _write(tmp_path, "t.sexp", _auto_net_trace_template_data([
            {"template": "dac_buf_tpl", "name": "ch1_dac_buf",
             "sheet": "Channel_1", "cluster": "DAC_BUF"}]))
        cfg, _ = load_config(str(p))
        tree = _tree_by_name(cfg, "ch1_dac_buf")
        assert tree.anchor.is_auto is True
        # the generated tree's net_trace child nodes point at the REWRITTEN nets
        nt_refs = sorted(n.ref for n in tree.nodes[0].children
                         if n.kind == "net_trace")
        assert nt_refs == ["/Channel_1/DAC/+3V3A_AVDD",
                           "/Channel_1/DAC/+3V3_AVDD"]
        # net_traces: generated copies on Channel_1, template ones untouched /0
        for net in ("/Channel_1/DAC/+3V3_AVDD", "/Channel_1/DAC/+3V3A_AVDD"):
            nt = next(x for x in cfg.net_traces if x.net == net)
            assert nt.anchor_sheet == "Channel_1"
            assert nt.tracks[0].net == net and nt.vias[0].net == net
        for net in ("/Channel_0/DAC/+3V3_AVDD", "/Channel_0/DAC/+3V3A_AVDD"):
            nt = next(x for x in cfg.net_traces if x.net == net)
            assert nt.anchor_sheet == "Channel_0"
        # placement copies: instance sheet; composite-guard kept per-copy
        # clusters (root DAC_BUF, PIF child PIF_AVDD — NOT the DAC_BUF override)
        assert _entity_by_name(cfg, "dac_buf__ch1_dac_buf").sheet == "Channel_1"
        assert _entity_by_name(cfg, "dac_buf__ch1_dac_buf").cluster == "DAC_BUF"
        assert _entity_by_name(cfg, "pif_avdd__ch1_dac_buf").cluster == "PIF_AVDD"
