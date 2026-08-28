#!/usr/bin/env python3
"""Phase 3 — clone-plan: auto-generate the clone_placements: block from the
file-based snapshot (netlist TwinMap + .kicad_pcb), plus the net_matching
(Kuhn+SCC) verification of the role<->net correspondence (Step 3.1/3.2)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from kicadstamp.cloner.netlist import parse_netlist, build_twin_map
from kicadstamp.cloner.pcb import PcbDocument
from kicadstamp.cloner.plan import (
    plan_clone_placements, verify_channel_net_mapping, clone_placements_to_dict,
    role_to_nets_for_channel,
)
from kicadstamp.exceptions import PlacerError

# ── synthetic fixtures (KiCad 10-ish s-expr) ─────────────────────────────────

_NET = """\
(export (version "D")
  (components
    (comp (ref "U1") (value "ADC") (footprint "ADC")
      (sheetpath (names "/Channel_0/DAC/") (tstamps "/ch0uuid/dac-sheet/"))
      (tstamps "dac-sheet/sym-u1"))
    (comp (ref "C1") (value "10u") (footprint "C_0603")
      (sheetpath (names "/Channel_0/DAC/") (tstamps "/ch0uuid/dac-sheet/"))
      (tstamps "dac-sheet/sym-c1"))
    (comp (ref "U1A") (value "ADC") (footprint "ADC")
      (sheetpath (names "/Channel_1/DAC/") (tstamps "/ch1uuid/dac-sheet/"))
      (tstamps "dac-sheet/sym-u1"))
    (comp (ref "C1A") (value "10u") (footprint "C_0603")
      (sheetpath (names "/Channel_1/DAC/") (tstamps "/ch1uuid/dac-sheet/"))
      (tstamps "dac-sheet/sym-c1")))
  (nets
    (net (code 1) (name "/Channel_0/DAC/DB0"))
    (net (code 2) (name "/Channel_1/DAC/DB0"))))
"""


def _pcb(*, ch1_dac_net_id: int = 10, ch1_dac_net: str = "/Channel_1/DAC/DB0"):
    return f"""\
(kicad_pcb (version 20240108) (generator "pytest")
  (net 0 "")
  (net 1 "/Channel_0/DAC/DB0")
  (net 2 "GND")
  (net 3 "+3V3")
  (net {ch1_dac_net_id} "{ch1_dac_net}")
  (footprint "ADC"
    (layer "F.Cu")
    (at 10 20 0)
    (uuid "fp-u1")
    (path "/ch0uuid/dac-sheet/sym-u1")
    (property "Reference" "U1")
    (property "Role" "DAC_ADC")
    (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu" "B.Cu") (net 1))
    (pad "2" smd rect (at 0 1) (size 1 1) (layers "F.Cu" "B.Cu") (net 2)))
  (footprint "C_0603"
    (layer "F.Cu")
    (at 15 20 0)
    (uuid "fp-c1")
    (path "/ch0uuid/dac-sheet/sym-c1")
    (property "Reference" "C1")
    (property "Role" "C_OUT_BULK")
    (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu" "B.Cu") (net 3))
    (pad "2" smd rect (at 0 1) (size 1 1) (layers "F.Cu" "B.Cu") (net 2)))
  (footprint "ADC"
    (layer "F.Cu")
    (at 110 20 0)
    (uuid "fp-u1a")
    (path "/ch1uuid/dac-sheet/sym-u1")
    (property "Reference" "U1A")
    (property "Role" "DAC_ADC")
    (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu" "B.Cu") (net {ch1_dac_net_id}))
    (pad "2" smd rect (at 0 1) (size 1 1) (layers "F.Cu" "B.Cu") (net 2)))
  (footprint "C_0603"
    (layer "F.Cu")
    (at 115 20 0)
    (uuid "fp-c1a")
    (path "/ch1uuid/dac-sheet/sym-c1")
    (property "Reference" "C1A")
    (property "Role" "C_OUT_BULK")
    (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu" "B.Cu") (net 3))
    (pad "2" smd rect (at 0 1) (size 1 1) (layers "F.Cu" "B.Cu") (net 2))))
"""


def _fixture(tmp_path, pcb_text=None):
    net_path = tmp_path / "channels.net"
    net_path.write_text(_NET, encoding="utf-8")
    pcb_path = tmp_path / "channels.kicad_pcb"
    pcb_path.write_text(pcb_text or _pcb(), encoding="utf-8")
    comps, local_by_ch, _global = parse_netlist(str(net_path))
    twin = build_twin_map(comps, local_by_ch)
    doc = PcbDocument(str(pcb_path))
    return twin, doc


# ── parser extension: Role + pad nets per footprint ──────────────────────────

class TestPcbFootprintRoleNets:
    def test_role_and_pad_nets_parsed(self, tmp_path):
        _twin, doc = _fixture(tmp_path)
        by_ref = {fp.ref: fp for fp in doc.footprints}
        u1 = by_ref["U1"]
        assert u1.role == "DAC_ADC"
        assert u1.pad_nets == ["/Channel_0/DAC/DB0", "GND"]
        c1 = by_ref["C1"]
        assert c1.role == "C_OUT_BULK"
        assert c1.pad_nets == ["+3V3", "GND"]
        u1a = by_ref["U1A"]
        assert u1a.role == "DAC_ADC"
        assert u1a.pad_nets == ["/Channel_1/DAC/DB0", "GND"]

    def test_footprint_without_role_is_none(self, tmp_path):
        pcb = _pcb().replace('(property "Role" "DAC_ADC")', "", 1)
        _twin, doc = _fixture(tmp_path, pcb)
        by_ref = {fp.ref: fp for fp in doc.footprints}
        assert by_ref["U1"].role is None


# ── clone-plan: auto clone_placements (a LIST of records — the config shape) ─

def _by_name(placements, name):
    return next(p for p in placements if p["name"] == name)


class TestPlanClonePlacements:
    def test_default_target_generates_runnable_entry(self, tmp_path):
        twin, doc = _fixture(tmp_path)
        placements, diagnostics = plan_clone_placements(
            twin=twin, doc=doc, source_channel="Channel_0", cell="dac")
        assert diagnostics == []          # Kuhn proves the mapping, no ambiguity
        assert [p["name"] for p in placements] == ["Channel_1"]
        entry = placements[0]
        assert entry["cluster"] == "Channel_1"
        assert entry["cell"] == "dac"
        assert entry["params"] == {"channel": 1}
        # local-net role -> twin prefix remap
        assert entry["nets"]["DAC_ADC"] == "/Channel_1/DAC/DB0"
        # bridging/global multi-net role -> NOT guessed; cell net_template handles it
        assert "C_OUT_BULK" not in entry["nets"]
        # default xy = the target channel's own bbox origin
        assert entry["xy"] == [110.0, 20.0]

    def test_explicit_xy_overrides_bbox_origin(self, tmp_path):
        twin, doc = _fixture(tmp_path)
        placements, _ = plan_clone_placements(
            twin=twin, doc=doc, source_channel="Channel_0", cell="dac",
            xy=(200.0, 300.0))
        assert placements[0]["xy"] == [200.0, 300.0]

    def test_cluster_tag_and_anchor(self, tmp_path):
        twin, doc = _fixture(tmp_path)
        placements, _ = plan_clone_placements(
            twin=twin, doc=doc, source_channel="Channel_0", cell="dac",
            cluster="DAC_CH", anchor_role="FPGA", anchor_sheet="Channel_1")
        entry = placements[0]
        # name = save identity, independent of the shared cluster tag
        assert entry["name"] == "Channel_1"
        assert entry["cluster"] == "DAC_CH"
        assert entry["anchor_role"] == "FPGA"
        assert entry["anchor_sheet"] == "Channel_1"

    def test_explicit_target_channels(self, tmp_path):
        twin, doc = _fixture(tmp_path)
        placements, _ = plan_clone_placements(
            twin=twin, doc=doc, source_channel="Channel_0", cell="dac",
            target_channels=["Channel_1"])
        assert [p["name"] for p in placements] == ["Channel_1"]

    def test_unknown_source_channel_raises(self, tmp_path):
        twin, doc = _fixture(tmp_path)
        with pytest.raises(ValueError, match="Channel_9"):
            plan_clone_placements(
                twin=twin, doc=doc, source_channel="Channel_9", cell="dac")

    def test_wrap_under_clone_placements_key(self):
        wrapped = clone_placements_to_dict([{"name": "Channel_1", "cell": "dac"}])
        assert wrapped == {"clone_placements": [{"name": "Channel_1", "cell": "dac"}]}

    def test_generated_block_loads_via_sexp_and_config_loader(self, tmp_path):
        """Acceptance (Step 3.1): the generated clone_placements block, written
        with the SAME dict->s-expr converter the config uses, round-trips and
        LOADS into a valid ClonePlacement — apply can execute it without edits."""
        twin, doc = _fixture(tmp_path)
        placements, diagnostics = plan_clone_placements(
            twin=twin, doc=doc, source_channel="Channel_0", cell="dac")
        assert diagnostics == []

        from kicadstamp.config.sexp_format import dict_to_sexp, sexp_to_dict
        from kicadstamp.config import load_clone_placement

        text = dict_to_sexp(clone_placements_to_dict(placements))
        assert "(kicadstamp-config" in text
        parsed = sexp_to_dict(text)
        record = parsed["clone_placements"][0]

        cp = load_clone_placement(record)
        assert cp.name == "Channel_1"
        assert cp.cluster == "Channel_1"
        assert cp.cell == "dac"
        assert cp.params == {"channel": 1}
        assert cp.nets == {"DAC_ADC": "/Channel_1/DAC/DB0"}
        assert cp.xy == (110.0, 20.0)


# ── net_matching verification (Kuhn + SCC, diagnostics not stop) ─────────────

class TestVerifyChannelNetMapping:
    def test_clean_channels_no_diagnostics(self, tmp_path):
        twin, doc = _fixture(tmp_path)
        _p, diagnostics = plan_clone_placements(
            twin=twin, doc=doc, source_channel="Channel_0", cell="dac")
        assert diagnostics == []

    def test_non_isomorphic_target_reports_diagnostic_not_raise(self):
        src = {"DAC_ADC": ["/Channel_0/DAC/DB0", "GND"], "C_OUT_BULK": ["+3V3", "GND"]}
        # target misses the DAC_ADC role entirely -> no perfect matching
        tgt = {"C_OUT_BULK": ["+3V3", "GND"]}
        diagnostics = verify_channel_net_mapping(
            src, tgt, "Channel_0", "Channel_1")
        assert any("net_matching" in d for d in diagnostics)

    def test_empty_evidence_returns_no_diagnostics(self):
        assert verify_channel_net_mapping({}, {"A": ["GND"]}, "c0", "c1") == []
        assert verify_channel_net_mapping({"A": ["GND"]}, {}, "c0", "c1") == []

    def test_role_to_nets_collects_pads_in_order(self, tmp_path):
        _twin, doc = _fixture(tmp_path)
        ch0_fps = [fp for fp in doc.footprints
                   if fp.path.startswith("/ch0uuid/")]
        role_nets = role_to_nets_for_channel(ch0_fps)
        assert role_nets == {
            "DAC_ADC": ["/Channel_0/DAC/DB0", "GND"],
            "C_OUT_BULK": ["+3V3", "GND"],
        }


class TestClonePlanCli:
    """cmd_clone_plan — the thin CLI wrapper writes a valid s-expr block that
    parses back into the expected clone_placements records."""

    def test_writes_sexp_block(self, tmp_path):
        from types import SimpleNamespace
        from kicadstamp.cli import cmd_clone_plan
        from kicadstamp.config.sexp_format import sexp_to_dict
        _fixture(tmp_path)  # writes channels.net / channels.kicad_pcb
        out = tmp_path / "clone.sexp"
        args = SimpleNamespace(
            net=str(tmp_path / "channels.net"),
            pcb=str(tmp_path / "channels.kicad_pcb"),
            source="Channel_0", cell="dac", cluster=None, targets=None,
            xy=None, anchor_role=None, anchor_sheet=None, output=str(out))
        cmd_clone_plan(args)
        parsed = sexp_to_dict(out.read_text(encoding="utf-8"))
        assert "clone_placements" in parsed
        record = parsed["clone_placements"][0]
        assert record["cell"] == "dac"
        assert record["nets"] == {"DAC_ADC": "/Channel_1/DAC/DB0"}

    def test_missing_required_args_raises(self):
        from types import SimpleNamespace
        from kicadstamp.cli import cmd_clone_plan
        args = SimpleNamespace(net="a.net", pcb="b.kicad_pcb",
                               source=None, cell=None)
        with pytest.raises(PlacerError, match="--source/--cell"):
            cmd_clone_plan(args)

    def test_bad_xy_raises(self, tmp_path):
        from types import SimpleNamespace
        from kicadstamp.cli import cmd_clone_plan
        _fixture(tmp_path)
        args = SimpleNamespace(
            net=str(tmp_path / "channels.net"),
            pcb=str(tmp_path / "channels.kicad_pcb"),
            source="Channel_0", cell="dac", cluster=None, targets=None,
            xy="not-a-pair", anchor_role=None, anchor_sheet=None, output=None)
        with pytest.raises(PlacerError, match="--xy"):
            cmd_clone_plan(args)
