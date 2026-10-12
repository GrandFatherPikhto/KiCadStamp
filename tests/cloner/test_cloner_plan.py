#!/usr/bin/env python3
"""Phase 3 net verification — the role<->net correspondence check (Step 3.2)
that `channel_copy.py` (Clone Sheets) calls.

The Phase 3 `clone-plan` generator (`plan_clone_placements`) was removed with the
root `clone_placements:` section (plan_2026_10_08_remove_placer.md); what is kept
is `verify_channel_net_mapping`, and these cells guard it (Kuhn + Tarjan SCC,
diagnostics not stop). The footprint parser cells (Role + pad nets) are kept too
— the verifier reads exactly those.
"""

from kicadstamp.cloner.netlist import parse_netlist, build_twin_map
from kicadstamp.cloner.pcb import PcbDocument
from kicadstamp.cloner.plan import verify_channel_net_mapping

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


# ── net_matching verification (Kuhn + SCC, diagnostics not stop) ─────────────

class TestVerifyChannelNetMapping:
    def test_clean_channels_no_diagnostics(self):
        # Same structure on both sides -> a clean matching, no diagnostics. The
        # verifier is the diagnostic layer `channel_copy.py` calls.
        nets = {"DAC_ADC": ["/Channel_0/DAC/DB0", "GND"],
                "C_OUT_BULK": ["+3V3", "GND"]}
        assert verify_channel_net_mapping(nets, dict(nets),
                                          "Channel_0", "Channel_1") == []

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
