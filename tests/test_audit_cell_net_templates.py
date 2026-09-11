"""Tests for kicadstamp/diagnostics/audit_cell_net_templates.py — the
read-only audit that flags Cell components whose net_template is a hardcoded
absolute net path ('/Sheet/...') while the SAME Cell is used by Entities on
more than one distinct sheet (the exact reuse pattern that silently resolves
the WRONG instance's real copper — live bug, Denis 2026-09-07, see
techdocs/handoff/deepseek/plan_2026_09_07_cell_net_template_audit.md).

The audit is a pure pass over a loaded Config (load_config is offline — no
live KiCad/adapter), so the synthetic cases below are real config files built
with dict_to_sexp and loaded through the normal loader, exactly like every
other config test in this suite.
"""
from pathlib import Path

import pytest

from kicadstamp.config import load_config
from kicadstamp.config.sexp_format import dict_to_sexp
from kicadstamp.diagnostics.audit_cell_net_templates import (
    _leading_sheet_segment,
    find_hardcoded_net_templates,
    format_findings,
    main,
)

def _write(path: Path, data: dict) -> Path:
    path.write_text(dict_to_sexp(data), encoding="utf-8")
    return path


def _cells_and_entities(cell_roles, entities):
    """cell_roles: dict cell_name -> list[dict] of component slot dicts
    (each at least {'role': ...}); entities: list[dict] Entity records."""
    return {"cells": {name: {"components": roles}
                      for name, roles in cell_roles.items()},
            "entities": entities}


# --------------------------------------------------------------------------
# core audit
# --------------------------------------------------------------------------

class TestFindHardcodedNetTemplates:
    def test_flags_hardcoded_roles_of_multi_sheet_cell(self, tmp_path):
        """The pif_avdd live-bug shape: one Cell, Entities on Channel_0 AND
        Channel_1, two roles hardcoded to '/Channel_0/DAC/+3V3_AVDD'. Both
        must be flagged, the generic '+3V3' role must not."""
        path = _write(tmp_path / "cfg.sexp", _cells_and_entities(
            {"pif_avdd": [
                {"role": "C_IN_BYPASS", "net_template": "+3V3"},
                {"role": "C_OUT_BULK",
                 "net_template": "/Channel_0/DAC/+3V3_AVDD"},
                {"role": "C_OUT_BYPASS",
                 "net_template": "/Channel_0/DAC/+3V3_AVDD"},
            ]},
            [
                {"name": "e0", "cell": "pif_avdd", "sheet": "Channel_0",
                 "cluster": "PIF_AVDD"},
                {"name": "e1", "cell": "pif_avdd", "sheet": "Channel_1",
                 "cluster": "DAC_BUF"},
            ]))
        cfg, _ctx = load_config(str(path))
        findings = find_hardcoded_net_templates(cfg)

        assert [f.role for f in findings] == ["C_OUT_BULK", "C_OUT_BYPASS"]
        for f in findings:
            assert f.cell == "pif_avdd"
            assert f.net_template == "/Channel_0/DAC/+3V3_AVDD"
            assert f.locked_sheet == "Channel_0"
            assert f.sheets == ["Channel_0", "Channel_1"]

    def test_materialized_tree_instance_entity_caught(self, tmp_path):
        """The live-bug mechanism: the SECOND sheet usage arrives ONLY via a
        tree_instances: declaration (no hand-written Channel_1 Entity).
        load_config materializes it into cfg.entities, so the audit still
        sees two sheets — point 4 of the plan is unnecessary."""
        template_tree = {
            "name": "ch0_pif",
            "anchor": {"role": "X", "sheet": "Channel_0"},
            "nodes": [
                {"ref": "e_template", "kind": "placement"},
            ],
        }
        cfg_dict = {
            "cells": {"pif_avdd": {"components": [
                {"role": "C_OUT_BULK",
                 "net_template": "/Channel_0/DAC/+3V3_AVDD"},
            ]}},
            # the template Entity (Channel_0) + the tree_instance declaration
            # that will generate the Channel_1 copy at load
            "entities": [
                {"name": "e_template", "cell": "pif_avdd",
                 "sheet": "Channel_0", "cluster": "PIF_AVDD"},
            ],
            "trees": [template_tree],
            "tree_instances": [
                {"template": "ch0_pif", "name": "ch1_pif",
                 "sheet": "Channel_1", "cluster": "DAC_BUF"},
            ],
        }
        path = _write(tmp_path / "cfg.sexp", cfg_dict)
        cfg, _ctx = load_config(str(path))
        materialized = [e for e in cfg.entities if e.name.endswith("__ch1_pif")]
        assert materialized, "tree_instance must materialize an Entity copy"
        assert materialized[0].sheet == "Channel_1"

        findings = find_hardcoded_net_templates(cfg)
        assert len(findings) == 1
        assert findings[0].cell == "pif_avdd"
        assert findings[0].sheets == ["Channel_0", "Channel_1"]

    def test_single_sheet_cell_absolute_net_template_not_reported(
            self, tmp_path):
        """Regression: a Cell used on ONE sheet only is not reported, even
        when its net_template is an absolute path — no cross-sheet reuse, no
        way for the literal to pick the wrong instance."""
        path = _write(tmp_path / "cfg.sexp", _cells_and_entities(
            {"pif_avdd": [
                {"role": "C_OUT_BULK",
                 "net_template": "/Channel_0/DAC/+3V3_AVDD"},
            ]},
            [
                {"name": "e0", "cell": "pif_avdd", "sheet": "Channel_0",
                 "cluster": "PIF_AVDD"},
            ]))
        cfg, _ctx = load_config(str(path))
        assert find_hardcoded_net_templates(cfg) == []

    def test_cell_without_entities_not_reported(self, tmp_path):
        """A Cell defined but referenced by NO Entity (even sheetless) can
        never be redrawn wrongly — not reported."""
        path = _write(tmp_path / "cfg.sexp", {
            "cells": {"c0": {"components": [
                {"role": "R1", "net_template": "/Channel_0/DAC/+3V3"},
            ]}},
            "entities": [],
        })
        cfg, _ctx = load_config(str(path))
        assert find_hardcoded_net_templates(cfg) == []

    def test_generic_non_slash_net_template_never_reported(self, tmp_path):
        """Regression: a generic (rail-name) net_template WITHOUT a leading
        '/' ('+3V3', '-5V', '+3V3_OSCILL') is never sheet-locked and must
        never be reported, no matter how many sheets use the Cell."""
        path = _write(tmp_path / "cfg.sexp", _cells_and_entities(
            {"c0": [
                {"role": "R1", "net_template": "+3V3"},
                {"role": "R2", "net_template": "+3V3_OSCILL"},
            ]},
            [
                {"name": "e0", "cell": "c0", "sheet": "Channel_0"},
                {"name": "e1", "cell": "c0", "sheet": "Channel_1"},
            ]))
        cfg, _ctx = load_config(str(path))
        assert find_hardcoded_net_templates(cfg) == []

    def test_dangling_cell_reference_skipped(self, tmp_path):
        """Defensive: an Entity whose cell name is NOT in cfg.cells (dangling
        ref, caught by validation elsewhere) must not crash the audit and is
        simply not reported — there is no Cell data to inspect."""
        path = _write(tmp_path / "cfg.sexp", {
            "cells": {},
            "entities": [
                {"name": "e0", "cell": "no_such_cell", "sheet": "Channel_0"},
                {"name": "e1", "cell": "no_such_cell", "sheet": "Channel_1"},
            ],
        })
        cfg, _ctx = load_config(str(path))
        assert find_hardcoded_net_templates(cfg) == []


class TestLeadingSheetSegment:
    def test_absolute_path_returns_sheet(self):
        assert _leading_sheet_segment(
            "/Channel_0/DAC/+3V3_AVDD") == "Channel_0"
        assert _leading_sheet_segment("/Power/GND") == "Power"

    def test_generic_net_or_malformed_returns_none(self):
        assert _leading_sheet_segment("+3V3") is None
        assert _leading_sheet_segment("/") is None
        assert _leading_sheet_segment("") is None
        assert _leading_sheet_segment("Channel_0/DAC") is None


class TestFormatFindings:
    def test_empty_findings_message(self):
        assert format_findings([]) == \
            "No hardcoded sheet-specific net_template found."

    def test_grouped_by_cell_report(self):
        from kicadstamp.diagnostics.audit_cell_net_templates import Finding
        report = format_findings([
            Finding(cell="pif_avdd", role="C_OUT_BULK",
                    net_template="/Channel_0/DAC/+3V3_AVDD",
                    locked_sheet="Channel_0",
                    sheets=["Channel_0", "Channel_1"]),
            Finding(cell="pif_avdd", role="C_OUT_BYPASS",
                    net_template="/Channel_0/DAC/+3V3_AVDD",
                    locked_sheet="Channel_0",
                    sheets=["Channel_0", "Channel_1"]),
        ])
        lines = report.splitlines()
        assert lines[0] == \
            "Cell 'pif_avdd' \u2014 used on sheets: Channel_0, Channel_1"
        assert lines[1] == \
            "  role 'C_OUT_BULK': net_template hardcoded to " \
            "'/Channel_0/DAC/+3V3_AVDD' (sheet-locked to Channel_0)"
        assert lines[2] == \
            "  role 'C_OUT_BYPASS': net_template hardcoded to " \
            "'/Channel_0/DAC/+3V3_AVDD' (sheet-locked to Channel_0)"

    def test_unparsable_locked_sheet_omits_suffix(self):
        """A '/'-prefixed net_template whose leading segment is empty (e.g.
        the degenerate '/') has locked_sheet None — the formatter must omit
        the '(sheet-locked to ...)' suffix instead of crashing."""
        from kicadstamp.diagnostics.audit_cell_net_templates import Finding
        report = format_findings([
            Finding(cell="c0", role="R1", net_template="/",
                    locked_sheet=None, sheets=["A", "B"]),
        ])
        assert "role 'R1': net_template hardcoded to '/'" in report
        assert "(sheet-locked to" not in report


# --------------------------------------------------------------------------
# CLI (prints the finding, always exits 0)
# --------------------------------------------------------------------------

class TestMain:
    def test_main_prints_finding_and_returns_zero(self, tmp_path, capsys):
        path = _write(tmp_path / "cfg.sexp", _cells_and_entities(
            {"c0": [
                {"role": "C_OUT_BULK",
                 "net_template": "/Channel_0/DAC/+3V3_AVDD"},
            ]},
            [
                {"name": "e0", "cell": "c0", "sheet": "Channel_0"},
                {"name": "e1", "cell": "c0", "sheet": "Channel_1"},
            ]))
        rc = main([str(path)])
        out = capsys.readouterr().out
        assert rc == 0
        assert "Cell 'c0'" in out
        assert "used on sheets: Channel_0, Channel_1" in out
        assert "C_OUT_BULK" in out
        assert "/Channel_0/DAC/+3V3_AVDD" in out
        assert "sheet-locked to Channel_0" in out

    def test_main_no_findings_message_and_zero(self, tmp_path, capsys):
        path = _write(tmp_path / "cfg.sexp", {"cells": {}, "entities": []})
        rc = main([str(path)])
        out = capsys.readouterr().out
        assert rc == 0
        assert "No hardcoded sheet-specific net_template found." in out

    def test_main_missing_file_prints_error_returns_zero(self, capsys):
        """A config that fails to load must not crash the run — the error is
        printed and the exit code stays 0 (diagnostic, not a CI gate)."""
        rc = main([str(Path("/nonexistent/does-not-exist.sexp"))])
        out = capsys.readouterr().out
        assert rc == 0
        assert "error loading" in out

    def test_main_multiple_configs_prints_separators(self, tmp_path, capsys):
        first = _write(tmp_path / "a.sexp", {"cells": {}, "entities": []})
        second = _write(tmp_path / "b.sexp", {"cells": {}, "entities": []})
        rc = main([str(first), str(second)])
        out = capsys.readouterr().out
        assert rc == 0
        assert f"=== {first} ===" in out
        assert f"=== {second} ===" in out
        assert out.count("No hardcoded sheet-specific net_template found.") == 2


# --------------------------------------------------------------------------
# the original live-bug shape (Task W: a committed fixture, no local file)
# --------------------------------------------------------------------------

class TestRealProfile:
    """Regression guard for the original live bug (2026-09-07): pif_avdd
    reused across sheets while C_OUT_BULK/C_OUT_BYPASS are hardcoded to
    '/Channel_0/DAC/+3V3_AVDD'. Was pinned to the author's personal,
    gitignored profile file — which can be empty/absent/renamed on any machine
    at any time (Task W, 2026-09-11: it went to zero cells mid-session while it
    was being restructured, and this module ERRORed instead of skipping). The
    synthetic case below is identical in shape to the live bug this audit was
    written for and needs no local file at all."""

    def test_pif_avdd_hardcoded_roles_flagged(self, tmp_path):
        path = _write(tmp_path / "cfg.sexp", _cells_and_entities(
            {"pif_avdd": [
                {"role": "C_OUT_BULK",
                 "net_template": "/Channel_0/DAC/+3V3_AVDD"},
                {"role": "C_OUT_BYPASS",
                 "net_template": "/Channel_0/DAC/+3V3_AVDD"},
            ]},
            [
                {"name": "e0", "cell": "pif_avdd", "sheet": "Channel_0",
                 "cluster": "PIF_AVDD"},
                {"name": "e1", "cell": "pif_avdd", "sheet": "Channel_1",
                 "cluster": "DAC_BUF"},
            ]))
        cfg, _ctx = load_config(str(path))
        findings = find_hardcoded_net_templates(cfg)
        pif = {f.role: f for f in findings if f.cell == "pif_avdd"}
        assert set(pif) == {"C_OUT_BULK", "C_OUT_BYPASS"}
        for role, f in pif.items():
            assert f.net_template == "/Channel_0/DAC/+3V3_AVDD"
            assert f.locked_sheet == "Channel_0"
            assert f.sheets == ["Channel_0", "Channel_1"]

    def test_render_of_real_profile_contains_pif_avdd(self, tmp_path):
        path = _write(tmp_path / "cfg.sexp", _cells_and_entities(
            {"pif_avdd": [
                {"role": "C_OUT_BULK",
                 "net_template": "/Channel_0/DAC/+3V3_AVDD"},
                {"role": "C_OUT_BYPASS",
                 "net_template": "/Channel_0/DAC/+3V3_AVDD"},
            ]},
            [
                {"name": "e0", "cell": "pif_avdd", "sheet": "Channel_0",
                 "cluster": "PIF_AVDD"},
                {"name": "e1", "cell": "pif_avdd", "sheet": "Channel_1",
                 "cluster": "DAC_BUF"},
            ]))
        cfg, _ctx = load_config(str(path))
        report = format_findings(find_hardcoded_net_templates(cfg))
        assert "Cell 'pif_avdd'" in report
        assert "role 'C_OUT_BULK': net_template hardcoded to " \
               "'/Channel_0/DAC/+3V3_AVDD' (sheet-locked to Channel_0)" \
               in report
        assert "role 'C_OUT_BYPASS': net_template hardcoded to " \
               "'/Channel_0/DAC/+3V3_AVDD' (sheet-locked to Channel_0)" \
               in report
