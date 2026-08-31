#!/usr/bin/env python3
"""Unit tests for the extract command's library core (kicadstamp/cli_extract.py)
— the pure logic that the thin CLI wrapper kicadstamp/cli.py.cmd_extract calls.
No live KiCad board needed: the validation paths tested here raise before any
board I/O happens."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from kicadstamp.cli_extract import EXTRACT_PROFILE_KNOWN_KEYS, extract_template
from kicadstamp.exceptions import PlacerError


class TestExtractTemplateValidation:
    """Validation that happens before any board I/O — reachable with a dummy
    adapter, proving the core reports bad arguments via PlacerError instead of
    sys.exit/input (see П.2)."""

    def test_origin_pad_without_role_is_fatal(self):
        # The adapter is never touched — the pad-without-role guard raises
        # before extract_template_from_selection is called.
        with pytest.raises(PlacerError, match="--origin-by-component-pad"):
            extract_template(
                adapter=object(),
                name="cell",
                output="out.sexp",
                origin_component_pad="3",
            )

    def test_origin_pad_requires_role_even_with_other_args(self):
        with pytest.raises(PlacerError, match="--origin-by-component-pad"):
            extract_template(
                adapter=object(),
                name="cell",
                output="out.sexp",
                params={"channel": 1},
                net_template_map={"DAC1_DB1": "DAC{channel}_DB1"},
                origin_component_pad="3",
            )


class TestRuleNets:
    """rule_nets (2026-08-05, --rule-net) — the CLI-core passthrough down to
    extract_template_from_selection(), and the profile-file known-keys
    allowlist that would otherwise fatal on a rule_nets: key found live."""

    def test_rule_nets_is_a_known_extract_profile_key(self):
        assert 'rule_nets' in EXTRACT_PROFILE_KNOWN_KEYS

    def test_rule_nets_forwarded_to_extract_template_from_selection(self, monkeypatch, tmp_path):
        captured = {}

        def _fake(adapter, name, **kwargs):
            captured.update(kwargs)
            return {name: {"vias": [], "components": [], "tracks": [], "layer": "F.Cu"}}

        import kicadstamp.cli_extract as cli_extract_mod
        monkeypatch.setattr(cli_extract_mod, "extract_template_from_selection", _fake)

        extract_template(
            adapter=object(), name="cell", output=str(tmp_path / "out.sexp"),
            rule_nets={"+3V3_VCCIO"},
        )

        assert captured["rule_nets"] == {"+3V3_VCCIO"}


class TestRawSelection:
    """raw_selection (2026-08-24, --raw-selection) — the opt-in bypass of the
    pad-connectivity filter: a profile known key and a passthrough down to
    extract_template_from_selection()."""

    def test_raw_selection_is_a_known_extract_profile_key(self):
        assert 'raw_selection' in EXTRACT_PROFILE_KNOWN_KEYS

    def test_raw_selection_forwarded_to_extract_template_from_selection(self, monkeypatch, tmp_path):
        captured = {}

        def _fake(adapter, name, **kwargs):
            captured.update(kwargs)
            return {name: {"vias": [], "components": [], "tracks": [], "layer": "F.Cu"}}

        import kicadstamp.cli_extract as cli_extract_mod
        monkeypatch.setattr(cli_extract_mod, "extract_template_from_selection", _fake)

        extract_template(
            adapter=object(), name="cell", output=str(tmp_path / "out.sexp"),
            raw_selection=True,
        )

        assert captured["raw_selection"] is True

    def test_raw_selection_defaults_to_false(self, monkeypatch, tmp_path):
        captured = {}

        def _fake(adapter, name, **kwargs):
            captured.update(kwargs)
            return {name: {"vias": [], "components": [], "tracks": [], "layer": "F.Cu"}}

        import kicadstamp.cli_extract as cli_extract_mod
        monkeypatch.setattr(cli_extract_mod, "extract_template_from_selection", _fake)

        extract_template(adapter=object(), name="cell", output=str(tmp_path / "out.sexp"))

        assert captured["raw_selection"] is False


class TestOriginComponentClusterSheet:
    """origin_by_component_cluster/origin_by_component_sheet (2026-08-31,
    Origin 'By component role' — no Cluster/Sheet): profile known keys (so a
    --profile re-run does not reject a saved cluster/sheet-narrowed recipe)
    and a passthrough down to extract_template_from_selection()."""

    def test_cluster_and_sheet_are_known_extract_profile_keys(self):
        assert 'origin_by_component_cluster' in EXTRACT_PROFILE_KNOWN_KEYS
        assert 'origin_by_component_sheet' in EXTRACT_PROFILE_KNOWN_KEYS

    def test_cluster_and_sheet_forwarded_to_extract_template_from_selection(self, monkeypatch, tmp_path):
        captured = {}

        def _fake(adapter, name, **kwargs):
            captured.update(kwargs)
            return {name: {"vias": [], "components": [], "tracks": [], "layer": "F.Cu"}}

        import kicadstamp.cli_extract as cli_extract_mod
        monkeypatch.setattr(cli_extract_mod, "extract_template_from_selection", _fake)

        extract_template(
            adapter=object(), name="cell", output=str(tmp_path / "out.sexp"),
            origin_component_role="FPGA",
            origin_component_cluster="FPGA/MAIN",
            origin_component_sheet="Channel_1",
        )

        assert captured["origin_component_role"] == "FPGA"
        assert captured["origin_component_cluster"] == "FPGA/MAIN"
        assert captured["origin_component_sheet"] == "Channel_1"


class TestCmdExtractRawSelection:
    """argparse --raw-selection reaches cmd_extract and is threaded through
    the thin CLI wrapper down to the library core."""

    def test_raw_selection_flag_reaches_extract_template(self, monkeypatch):
        from types import SimpleNamespace
        import kicadstamp.cli as cli_mod
        import kicadstamp.cli_extract as cli_extract_mod
        import kicadstamp.kicad.adapter as adapter_mod

        class _FakeAdapter:
            def refresh_board(self):
                pass

        # cli.py imports KiCadBoardAdapter/extract_template lazily inside
        # cmd_extract, so patch the modules they are imported FROM.
        monkeypatch.setattr(adapter_mod, "KiCadBoardAdapter", lambda timeout_ms: _FakeAdapter())
        captured = {}

        def _fake_extract(adapter, **kwargs):
            captured.update(kwargs)

        monkeypatch.setattr(cli_extract_mod, "extract_template", _fake_extract)

        args = SimpleNamespace(
            name="cell", output="out.sexp", timeout_ms=100,
            param=None, net_template=None, net_template_role=None, rule_net=None,
            origin_by_via_net=None, origin_by_component_role=None,
            origin_by_component_pad=None, origin_by_component_cluster=None,
            origin_by_component_sheet=None, profile=None, profiles=None,
            raw_selection=True,
        )
        cli_mod.cmd_extract(args)

        assert captured["raw_selection"] is True
        assert captured["name"] == "cell"
        assert captured["output"] == "out.sexp"


class TestExtractTemplateSexpOutput:
    """extract_template's output format is selected by file suffix — a .sexp
    output path must produce REAL s-expr text (sexp_to_dict reads it back),
    not silent YAML (2026-08-28, sexp_output_writers_fix)."""

    @staticmethod
    def _fake_template(adapter, name, **kwargs):
        return {name: {"vias": [], "components": [], "tracks": [], "layer": "F.Cu"}}

    def test_sexp_output_roundtrips(self, monkeypatch, tmp_path):
        """--output foo.sexp writes s-expr that sexp_to_dict reads back into
        the same cells: content the YAML path would produce (compared against
        the default-stripped canonical form — the s-expr writer omits fields
        equal to their dataclass default)."""
        from kicadstamp.config.sexp_format import _strip_defaults, sexp_to_dict

        template = {
            "vias": [{"offset_along_mm": 1.0, "offset_across_mm": 2.0,
                      "net": "GND", "drill_mm": 0.3, "diameter_mm": 0.6}],
            "components": [], "tracks": [], "layer": "F.Cu",
        }

        def _fake(adapter, name, **kwargs):
            return {name: template}

        import kicadstamp.cli_extract as cli_extract_mod
        monkeypatch.setattr(cli_extract_mod, "extract_template_from_selection", _fake)

        out = tmp_path / "out.sexp"
        extract_template(adapter=object(), name="cell1", output=str(out))

        text = out.read_text(encoding="utf-8")
        assert text.lstrip().startswith("(kicadstamp-config")  # s-expr, not YAML
        expected = _strip_defaults({"cells": {"cell1": template}})
        assert sexp_to_dict(text) == expected

    def test_sexp_upsert_merges_two_cells(self, monkeypatch, tmp_path):
        """A pre-existing .sexp with one cells: name + a new extract of another
        name -> BOTH present after the write (the same merge/upsert semantics
        that are already tested for YAML)."""
        from kicadstamp.config.sexp_format import dict_to_sexp, sexp_to_dict

        out = tmp_path / "cells.sexp"
        out.write_text(dict_to_sexp({
            "cells": {"existing": {"vias": [], "components": [], "tracks": [],
                                   "layer": "B.Cu"}},
        }), encoding="utf-8")

        import kicadstamp.cli_extract as cli_extract_mod
        monkeypatch.setattr(cli_extract_mod, "extract_template_from_selection",
                            self._fake_template)

        extract_template(adapter=object(), name="cell1", output=str(out))

        data = sexp_to_dict(out.read_text(encoding="utf-8"))
        assert "existing" in data["cells"]
        assert "cell1" in data["cells"]

    def test_sexp_output_ignores_annotations(self, monkeypatch, tmp_path):
        """render_uncertain_comments was removed from extract_template with
        the YAML output branch (2026-08-28, core_yaml_removal — the function/
        module stay alive elsewhere, but this write path no longer references
        it at all). Annotations are still collected from the extractor but are
        simply not inserted into .sexp output: the file stays a valid s-expr."""
        from kicadstamp.config.sexp_format import sexp_to_dict

        def _fake(adapter, name, **kwargs):
            annotations = kwargs.get("annotations")
            if annotations is not None:
                annotations.append(("role", "field", "hint"))
            return {name: {"vias": [], "components": [], "tracks": [],
                           "layer": "F.Cu"}}

        import kicadstamp.cli_extract as cli_extract_mod
        monkeypatch.setattr(cli_extract_mod, "extract_template_from_selection", _fake)

        out = tmp_path / "out.sexp"
        extract_template(adapter=object(), name="cell1", output=str(out))

        text = out.read_text(encoding="utf-8")
        assert text.lstrip().startswith("(kicadstamp-config")
        sexp_to_dict(text)  # still a valid s-expr
