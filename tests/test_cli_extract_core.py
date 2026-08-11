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
                output="out.yaml",
                origin_component_pad="3",
            )

    def test_origin_pad_requires_role_even_with_other_args(self):
        with pytest.raises(PlacerError, match="--origin-by-component-pad"):
            extract_template(
                adapter=object(),
                name="cell",
                output="out.yaml",
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
            adapter=object(), name="cell", output=str(tmp_path / "out.yaml"),
            rule_nets={"+3V3_VCCIO"},
        )

        assert captured["rule_nets"] == {"+3V3_VCCIO"}
