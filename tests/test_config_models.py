#!/usr/bin/env python3
"""Tests for the net_from_role / net_from_role_pad fields on TemplateVia and
TemplateTrack (plan step 1, handoff_2026_08_11_net_from_role_core_implementation.md).

Rules decided with Denis (2026-08-11, see plan §2):
  - Fatal ONLY when BOTH `net` and `net_from_role` are set on the same record
    (collision — unclear which should win).
  - `net_from_role_pad` without `net_from_role` — fatal (meaningless alone).
  - "Both None" is NOT an error and NOT a new case: TemplateVia.net=None already
    has two legitimate meanings by usage site (spoke_layout rule-net inheritance
    for ManualSpoke/Rule; fatal at apply for ClonePlacement — clone_geometry.py:30).
    Nothing is added for the both-None case here.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from kicadstamp.config import load_config, TemplateVia, TemplateTrack, TemplateComponentSlot
from kicadstamp.exceptions import ValidationError

MINIMAL = "layer: B.Cu\nrules: []\n"


def _via_cell_yaml(via_body: str) -> str:
    return MINIMAL + f"""
cells:
  c:
    vias:
      - {via_body}
"""


def _slot_cell_yaml(slot_body: str) -> str:
    return MINIMAL + f"""
cells:
  c:
    components:
      - {slot_body}
"""


def _track_cell_yaml(track_body: str) -> str:
    return MINIMAL + f"""
cells:
  c:
    tracks:
      - {track_body}
"""


class TestViaNetFromRole:
    def test_net_from_role_loads(self, tmp_path):
        p = tmp_path / "t.yaml"
        p.write_text(_via_cell_yaml(
            "offset_along_mm: 1.0\n        net_from_role: C_IN_BULK"), encoding="utf-8")
        cfg, _ = load_config(str(p))
        v = cfg.cells["c"].vias[0]
        assert isinstance(v, TemplateVia)
        assert v.net is None
        assert v.net_from_role == "C_IN_BULK"
        assert v.net_from_role_pad is None

    def test_net_from_role_with_pad_loads(self, tmp_path):
        p = tmp_path / "t.yaml"
        p.write_text(_via_cell_yaml(
            "offset_along_mm: 1.0\n        net_from_role: LDO\n        "
            "net_from_role_pad: '1'"), encoding="utf-8")
        cfg, _ = load_config(str(p))
        v = cfg.cells["c"].vias[0]
        assert v.net is None
        assert v.net_from_role == "LDO"
        assert v.net_from_role_pad == "1"

    def test_net_and_net_from_role_together_is_fatal(self, tmp_path):
        p = tmp_path / "t.yaml"
        p.write_text(_via_cell_yaml(
            "offset_along_mm: 1.0\n        net: '{GND}'\n        "
            "net_from_role: C_IN_BULK"), encoding="utf-8")
        with pytest.raises(ValidationError, match="net and via.net_from_role"):
            load_config(str(p))

    def test_net_from_role_pad_without_role_is_fatal(self, tmp_path):
        p = tmp_path / "t.yaml"
        p.write_text(_via_cell_yaml(
            "offset_along_mm: 1.0\n        net_from_role_pad: '1'"), encoding="utf-8")
        with pytest.raises(ValidationError, match="without via.net_from_role"):
            load_config(str(p))

    def test_net_null_still_loads_rule_net_convention(self, tmp_path):
        """Regression guard: net: null (both None) must stay valid — it is the
        ManualSpoke rule-net convention (spoke_layout via.net or rule_net)."""
        p = tmp_path / "t.yaml"
        p.write_text(_via_cell_yaml(
            "offset_along_mm: 1.0\n        net: null"), encoding="utf-8")
        cfg, _ = load_config(str(p))
        v = cfg.cells["c"].vias[0]
        assert v.net is None
        assert v.net_from_role is None


class TestTrackNetFromRole:
    def test_net_from_role_loads(self, tmp_path):
        p = tmp_path / "t.yaml"
        p.write_text(_track_cell_yaml(
            "start_along_mm: 0.0\n        end_along_mm: 1.0\n        "
            "net_from_role: C_OUT_BULK"), encoding="utf-8")
        cfg, _ = load_config(str(p))
        t = cfg.cells["c"].tracks[0]
        assert isinstance(t, TemplateTrack)
        assert t.net is None
        assert t.net_from_role == "C_OUT_BULK"
        assert t.net_from_role_pad is None

    def test_net_and_net_from_role_together_is_fatal(self, tmp_path):
        p = tmp_path / "t.yaml"
        p.write_text(_track_cell_yaml(
            "start_along_mm: 0.0\n        end_along_mm: 1.0\n        "
            "net: '+3V3'\n        net_from_role: C_OUT_BULK"), encoding="utf-8")
        with pytest.raises(ValidationError, match="net and track.net_from_role"):
            load_config(str(p))

    def test_net_from_role_pad_without_role_is_fatal(self, tmp_path):
        p = tmp_path / "t.yaml"
        p.write_text(_track_cell_yaml(
            "start_along_mm: 0.0\n        end_along_mm: 1.0\n        "
            "net_from_role_pad: '2'"), encoding="utf-8")
        with pytest.raises(ValidationError, match="without track.net_from_role"):
            load_config(str(p))


class TestTemplateComponentSlotNetTemplatePad:
    """net_template_pad on TemplateComponentSlot (plan 2026-08-16,
    net_template_pad): mirrors TemplateVia/TemplateTrack's net_from_role_pad —
    OPTIONAL, only meaningful together with net_template, fatal if set without
    it (loader's _load_template_component_slot, same dependency shape as the
    track loader's net_from_role_pad-without-net_from_role check)."""

    def test_net_template_pad_default_is_none(self):
        slot = TemplateComponentSlot(role="LDO_ADJ")
        assert slot.net_template is None
        assert slot.net_template_pad is None

    def test_net_template_with_pad_loads(self, tmp_path):
        p = tmp_path / "t.yaml"
        p.write_text(_slot_cell_yaml(
            "role: LDO_ADJ\n        net_template: 'NET_{p}'\n        net_template_pad: '3'"),
            encoding="utf-8")
        cfg, _ = load_config(str(p))
        slot = cfg.cells["c"].components[0]
        assert isinstance(slot, TemplateComponentSlot)
        assert slot.net_template == "NET_{p}"
        assert slot.net_template_pad == "3"

    def test_net_template_without_pad_loads(self, tmp_path):
        p = tmp_path / "t.yaml"
        p.write_text(_slot_cell_yaml(
            "role: LDO_ADJ\n        net_template: 'NET_{p}'"), encoding="utf-8")
        cfg, _ = load_config(str(p))
        assert cfg.cells["c"].components[0].net_template_pad is None

    def test_net_template_pad_without_net_template_is_fatal(self, tmp_path):
        p = tmp_path / "t.yaml"
        p.write_text(_slot_cell_yaml(
            "role: LDO_ADJ\n        net_template_pad: '3'"), encoding="utf-8")
        with pytest.raises(ValidationError, match="without net_template in slot"):
            load_config(str(p))

    def test_net_null_still_loads(self, tmp_path):
        p = tmp_path / "t.yaml"
        p.write_text(_track_cell_yaml(
            "start_along_mm: 0.0\n        end_along_mm: 1.0\n        "
            "net: null"), encoding="utf-8")
        cfg, _ = load_config(str(p))
        t = cfg.cells["c"].tracks[0]
        assert t.net is None
        assert t.net_from_role is None
