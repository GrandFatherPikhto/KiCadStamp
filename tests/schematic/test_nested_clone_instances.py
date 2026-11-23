#!/usr/bin/env python3
# tests/schematic/test_nested_clone_instances.py
"""The INSTANCE ADDRESS of a component in a NESTED clone — Р1/Р2/Р3/Р4 of
plan_2026_11_23_nested_clone_sheet_instances (Т2), on the live HP-ISO-Channel-
v099 shape.

The old rule was «the first named segment», which was written for
`Channel_0/DAC` (clones AT THE ROOT). It merges the two copies of
`Signal In A` / `Signal In B` — one file, two places — into ONE instance of 12
components, so a selection of one copy is honestly refused as partial.

Р1 answers with the SHORTEST chain prefix whose sheet file occurs EXACTLY ONCE
under it; Р2 writes those names joined by "/"; Р3 teaches `sheet_in_path` that
a multi-segment address is a PREFIX; Р4 keeps the old answer for every map
without a hierarchy.

The cells follow the plan's ш1–ш8 and ш10, plus Denis's two Imprint decisions
(«the same instance» via `sheet_in_path`; moving INTO a nested clone refuses in
one red line).
"""
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from kicadstamp.config import Entity
from kicadstamp.config.loader import LazySheetNameMap
from kicadstamp.config.models import ImprintComponentRecord, ImprintConfig
from kicadstamp.domain.board import Footprint, Pad
from kicadstamp.domain.geometry import BoardLayer, Vector2
from kicadstamp.exceptions import ValidationError
from kicadstamp.imprint_apply import plan_imprint
from kicadstamp.selection_narrowing import FootprintInfo, group_selection
from kicadstamp.sheet_names import (
    ROOT_SHEET,
    all_instance_addresses,
    instance_sheet_of,
    sheet_in_path,
    sheet_of_names,
)
from kicadstamp.utils.units import MM
from gui.docks import reead


# ── building a schematic tree the way KiCad writes it ───────────────────────

def _sheet(uuid, name, sheetfile=None):
    lines = [f'(sheet (uuid "{uuid}")', f'  (property "Sheetname" "{name}")']
    if sheetfile is not None:
        lines.append(f'  (property "Sheetfile" "{sheetfile}")')
    lines.append(')')
    return '\n'.join(lines)


def _write(path, *blocks):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('(kicad_sch\n' + '\n'.join(blocks) + '\n)\n', encoding='utf-8')
    return str(path.resolve())


def _fp(chain):
    """A footprint as the ONE shared reader sees it (the LAST uuid is its own)."""
    return SimpleNamespace(sheet_path_uuids=tuple(chain))


def _map(root: str, base: Path) -> LazySheetNameMap:
    """The map a profile with `root_sheet: <root>` builds — every .kicad_sch
    under `base` listed for the NAMES, the tree itself walked from the root."""
    files = sorted(str(p) for p in base.rglob("*.kicad_sch"))
    return LazySheetNameMap(str(base / "config.sexp"), None, files,
                            root_sheet=root)


@pytest.fixture
def hp_iso(tmp_path):
    """root -> ISO In (once) -> Signal In A|B (one file); ISO Out -> 4 blocks."""
    signal_in = _write(tmp_path / "signal_in.kicad_sch")
    signal_out = _write(tmp_path / "signal_out.kicad_sch")
    iso_in = _write(
        tmp_path / "iso_in.kicad_sch",
        _sheet("b-sia", "Signal In A", "signal_in.kicad_sch"),
        _sheet("b-sib", "Signal In B", "signal_in.kicad_sch"))
    iso_out = _write(
        tmp_path / "iso_out.kicad_sch",
        *[_sheet(f"b-so{letter}", f"Signal Out {letter}", "signal_out.kicad_sch")
          for letter in "ABCD"])
    root = _write(
        tmp_path / "root.kicad_sch",
        _sheet("b-iso-in", "ISO In", "iso_in.kicad_sch"),
        _sheet("b-iso-out", "ISO Out", "iso_out.kicad_sch"))
    return {"root": root, "iso_in": iso_in, "iso_out": iso_out,
            "signal_in": signal_in, "signal_out": signal_out,
            "base": tmp_path,
            "map": _map(root, tmp_path)}


# ── ш1: the live case ───────────────────────────────────────────────────────

def test_the_two_nested_clones_get_two_different_addresses(hp_iso):
    m = hp_iso["map"]
    a = _fp(("b-iso-in", "b-sia", "own-a"))
    b = _fp(("b-iso-in", "b-sib", "own-b"))
    assert instance_sheet_of(a, m) == "ISO In/Signal In A"
    assert instance_sheet_of(b, m) == "ISO In/Signal In B"


def test_the_sig_out_fan_out_gets_one_address_per_block(hp_iso):
    m = hp_iso["map"]
    for letter in "ABCD":
        fp = _fp(("b-iso-out", f"b-so{letter}", "own"))
        assert instance_sheet_of(fp, m) == f"ISO Out/Signal Out {letter}"


def test_the_same_component_on_a_plain_dict_keeps_the_old_answer(hp_iso):
    """ш6 — Р4: the hierarchy is the only thing that changes the answer."""
    a = _fp(("b-iso-in", "b-sia", "own-a"))
    plain = {"b-iso-in": "ISO In", "b-sia": "Signal In A"}
    assert instance_sheet_of(a, plain) == "ISO In"
    assert instance_sheet_of(a, None) is None
    assert instance_sheet_of(a, {}) is None


# ── ш5: a component on the clone sheet ITSELF ───────────────────────────────

def test_a_component_on_the_clone_sheet_itself_keeps_one_name(hp_iso):
    m = hp_iso["map"]
    assert instance_sheet_of(_fp(("b-iso-in", "own")), m) == "ISO In"
    # a component on the ROOT sheet is the root marker, as always
    assert instance_sheet_of(_fp(("own",)), m) == ROOT_SHEET


# ── ш2: clones at the ROOT stay byte-identical ──────────────────────────────

def test_a_root_channel_with_its_dac_inside_keeps_one_name(tmp_path):
    """The HP-Channel shape: `Channel_0/DAC` — the answer must stay `Channel_0`
    whether every channel owns its own files or they SHARE one (экз counts
    places inside the sheet's own file, not in the whole project)."""
    dac = _write(tmp_path / "dac.kicad_sch")
    root_blocks = []
    for channel in range(4):
        ch = _write(tmp_path / f"ch{channel}.kicad_sch",
                    _sheet(f"b-dac{channel}", "DAC", "dac.kicad_sch"))
        root_blocks.append(_sheet(f"b-ch{channel}", f"Channel_{channel}",
                                  f"ch{channel}.kicad_sch"))
    root = _write(tmp_path / "root.kicad_sch", *root_blocks)
    m = _map(root, tmp_path)
    for channel in range(4):
        fp = _fp((f"b-ch{channel}", f"b-dac{channel}", "own"))
        assert instance_sheet_of(fp, m) == f"Channel_{channel}"


def test_one_shared_channel_file_still_gives_one_name(tmp_path):
    """The same shape with ONE channel file standing under four root blocks: the
    count is local to `файл(b1)`, so `экз(ch, dac) == 1` and the name stays one
    segment — a shared file must not invent `Channel_0/DAC`."""
    dac = _write(tmp_path / "dac.kicad_sch")
    ch = _write(tmp_path / "ch.kicad_sch", _sheet("b-dac", "DAC", "dac.kicad_sch"))
    root = _write(tmp_path / "root.kicad_sch",
                  *[_sheet(f"b-ch{i}", f"Channel_{i}", "ch.kicad_sch")
                    for i in range(4)])
    m = _map(root, tmp_path)
    for i in range(4):
        assert instance_sheet_of(_fp((f"b-ch{i}", "b-dac", "own")), m) == \
            f"Channel_{i}"


# ── ш3: one file under two DIFFERENT plain sheets ───────────────────────────

def test_one_file_under_two_plain_sheets_gives_the_plain_names(tmp_path):
    ldo = _write(tmp_path / "ldo.kicad_sch")
    power = _write(tmp_path / "power.kicad_sch", _sheet("b-ldo-p", "LDO", "ldo.kicad_sch"))
    analog = _write(tmp_path / "analog.kicad_sch", _sheet("b-ldo-a", "LDO", "ldo.kicad_sch"))
    root = _write(tmp_path / "root.kicad_sch",
                  _sheet("b-power", "Power", "power.kicad_sch"),
                  _sheet("b-analog", "Analog", "analog.kicad_sch"))
    m = _map(root, tmp_path)
    assert instance_sheet_of(_fp(("b-power", "b-ldo-p", "own")), m) == "Power"
    assert instance_sheet_of(_fp(("b-analog", "b-ldo-a", "own")), m) == "Analog"


# ── ш4 / ш4б: clone of a clone, and clones under plain sheets ───────────────

def test_a_clone_of_a_clone_names_every_hop(tmp_path):
    signal = _write(tmp_path / "signal.kicad_sch")
    iso1 = _write(tmp_path / "iso1.kicad_sch",
                  _sheet("b-1a", "Signal In A", "signal.kicad_sch"),
                  _sheet("b-1b", "Signal In B", "signal.kicad_sch"))
    iso2 = _write(tmp_path / "iso2.kicad_sch",
                  _sheet("b-2a", "Signal In A", "signal.kicad_sch"),
                  _sheet("b-2b", "Signal In B", "signal.kicad_sch"))
    root = _write(tmp_path / "root.kicad_sch",
                  _sheet("b-iso1", "ISO In 1", "iso1.kicad_sch"),
                  _sheet("b-iso2", "ISO In 2", "iso2.kicad_sch"))
    m = _map(root, tmp_path)
    got = {
        instance_sheet_of(_fp(("b-iso1", "b-1a", "o")), m),
        instance_sheet_of(_fp(("b-iso1", "b-1b", "o")), m),
        instance_sheet_of(_fp(("b-iso2", "b-2a", "o")), m),
        instance_sheet_of(_fp(("b-iso2", "b-2b", "o")), m),
    }
    assert got == {"ISO In 1/Signal In A", "ISO In 1/Signal In B",
                   "ISO In 2/Signal In A", "ISO In 2/Signal In B"}


def test_clones_deep_under_plain_sheets_name_every_hop(tmp_path):
    signal = _write(tmp_path / "signal.kicad_sch")
    mid = _write(tmp_path / "mid.kicad_sch",
                 _sheet("b-ma", "Signal In A", "signal.kicad_sch"),
                 _sheet("b-mb", "Signal In B", "signal.kicad_sch"))
    wrap = _write(tmp_path / "wrap.kicad_sch", _sheet("b-mid", "Mid", "mid.kicad_sch"))
    root = _write(tmp_path / "root.kicad_sch", _sheet("b-wrap", "Wrap", "wrap.kicad_sch"))
    m = _map(root, tmp_path)
    assert instance_sheet_of(_fp(("b-wrap", "b-mid", "b-ma", "o")), m) == \
        "Wrap/Mid/Signal In A"
    assert instance_sheet_of(_fp(("b-wrap", "b-mid", "b-mb", "o")), m) == \
        "Wrap/Mid/Signal In B"


# ── ш8: an unresolved uuid never becomes an address ─────────────────────────

def test_a_chain_the_dictionary_cannot_name_answers_none(hp_iso):
    m = hp_iso["map"]
    assert instance_sheet_of(_fp(("no-such-block", "own")), m) is None


def test_a_partially_named_chain_falls_back_to_the_old_answer(hp_iso):
    """A block the TREE does not know cannot be turned into a path — saying
    less (the old first-segment rule) beats inventing a place."""
    m = hp_iso["map"]
    assert instance_sheet_of(_fp(("b-iso-in", "unknown-block", "own")), m) == "ISO In"


# ── ш7: the multi-segment PREFIX predicate ──────────────────────────────────

def test_a_multi_segment_address_matches_a_prefix_and_nothing_else():
    chain = ["ISO In", "Signal In A"]
    assert sheet_in_path(chain, "ISO In/Signal In A")
    assert sheet_in_path(chain + ["DAC"], "ISO In/Signal In A")   # deeper is fine
    assert not sheet_in_path(["ISO In", "Signal In B"], "ISO In/Signal In A")
    assert not sheet_in_path(["Other", "ISO In", "Signal In A"],
                             "ISO In/Signal In A")
    assert not sheet_in_path(["ISO In"], "ISO In/Signal In A")
    assert not sheet_in_path([], "ISO In/Signal In A")
    assert not sheet_in_path(chain, "/")


def test_a_single_name_still_matches_any_segment():
    assert sheet_in_path(["ISO In", "Signal In A"], "Signal In A")
    assert sheet_in_path(["ISO In", "Signal In A"], "ISO In")
    assert not sheet_in_path(["ISO Out", "Signal Out A"], "ISO In")
    assert sheet_in_path([], "/")


# ── the combos: a nested address must be PICKABLE, not only typeable ────────

def test_every_instance_address_is_listed_for_the_sheet_combos(hp_iso):
    got = all_instance_addresses(hp_iso["map"])
    assert "ISO In" in got and "ISO Out" in got
    assert "ISO In/Signal In A" in got and "ISO In/Signal In B" in got
    assert "ISO Out/Signal Out D" in got
    assert all_instance_addresses({"u": "S"}) == []


# ── ш10: through the REAL callers, not just the function ────────────────────

def _sel(ref, cluster, chain):
    return SimpleNamespace(ref=ref, cluster=cluster, fp=_fp(chain),
                           sheet=[None] * len(chain), uuid=f"u-{ref}")


def _clone_selection(count=6):
    a = [_sel(f"A{i}", "SIG_IN", ("b-iso-in", "b-sia", f"own-A{i}"))
         for i in range(count)]
    b = [_sel(f"B{i}", "SIG_IN", ("b-iso-in", "b-sib", f"own-B{i}"))
         for i in range(count)]
    return a, b


def test_reead_accepts_a_fully_selected_nested_clone(hp_iso):
    """ш1's real half: 6 of 12 components — today's rule refuses «selected 6 of
    12», because both copies used to be ONE instance."""
    a, b = _clone_selection()
    rejections: list = []
    clusters = reead.fully_selected_clusters(
        a, a + b, (), (), sheet_names=hp_iso["map"], rejections=rejections)
    assert rejections == []
    assert [(c.cluster, c.sheet, len(c.refs)) for c in clusters] == \
        [("SIG_IN", "ISO In/Signal In A", 6)]


def test_reead_reports_the_two_clones_as_two_clusters(hp_iso):
    a, b = _clone_selection()
    clusters = reead.fully_selected_clusters(
        a + b, a + b, (), (), sheet_names=hp_iso["map"])
    by_sheet = {c.sheet: len(c.refs) for c in clusters}
    assert by_sheet == {"ISO In/Signal In A": 6, "ISO In/Signal In B": 6}


def test_the_mixed_read_groups_the_two_clones_apart(hp_iso):
    a, b = _clone_selection()
    m = hp_iso["map"]
    infos = [FootprintInfo(item=s.fp, role="R", cluster=s.cluster,
                           sheet=tuple(reead._sheet_chain(s, m)))
             for s in a + b]
    groups = group_selection(infos, (), sheet_names=m)
    assert {key[1] for key in groups} == {"ISO In/Signal In A",
                                          "ISO In/Signal In B"}
    assert all(len(members) == 6 for members in groups.values())


def test_check_placement_names_the_same_instance(hp_iso):
    from tools import check_placement
    assert check_placement._instance_of(
        _fp(("b-iso-in", "b-sia", "own")), hp_iso["map"]) == "ISO In/Signal In A"
    assert check_placement._instance_of(_fp(("own",)), hp_iso["map"]) == ROOT_SHEET


# ── ш9: Р5, the yellow line for an address that now names several ──────────

def _address_cfg(sheet):
    return SimpleNamespace(
        entities=[SimpleNamespace(name="SIG_IN_A", cluster="SIG_IN",
                                  sheet=sheet, cell="sig")],
        clone_placements=[], net_traces=[])


def test_an_old_address_that_now_names_two_instances_gets_one_yellow_line(
        hp_iso, caplog):
    import kicadstamp.validation as validation

    validation._ambiguous_warned.clear()
    with caplog.at_level(logging.WARNING, logger="kicadstamp.validation"):
        told = validation.warn_ambiguous_sheet_addresses(
            _address_cfg("ISO In"), hp_iso["map"])
    assert told == 1
    lines = [r.getMessage() for r in caplog.records
             if r.levelno == logging.WARNING]
    assert len(lines) == 1
    assert "entity SIG_IN_A: sheet 'ISO In' names 2 instances of SIG_IN" in lines[0]
    assert "ISO In/Signal In A" in lines[0] and "ISO In/Signal In B" in lines[0]
    assert "set the sheet to one of them" in lines[0]
    # ONE line per record per process, not one per read
    with caplog.at_level(logging.WARNING, logger="kicadstamp.validation"):
        assert validation.warn_ambiguous_sheet_addresses(
            _address_cfg("ISO In"), hp_iso["map"]) == 0
    validation._ambiguous_warned.clear()


def test_an_exact_address_or_no_hierarchy_stays_silent(hp_iso, caplog):
    import kicadstamp.validation as validation

    validation._ambiguous_warned.clear()
    with caplog.at_level(logging.WARNING, logger="kicadstamp.validation"):
        assert validation.warn_ambiguous_sheet_addresses(
            _address_cfg("ISO In/Signal In A"), hp_iso["map"]) == 0
        plain = {"b-iso-in": "ISO In", "b-sia": "Signal In A"}
        assert validation.warn_ambiguous_sheet_addresses(
            _address_cfg("ISO In"), plain) == 0
    assert [r for r in caplog.records if r.levelno == logging.WARNING] == []


# ── Imprint: «the same instance» is a predicate, not a string compare ───────

def _imp_rec(source_sheet):
    return ImprintConfig(
        name="psu", pivot=(0.0, 0.0), source_sheet=source_sheet,
        components=[ImprintComponentRecord(ref="R1", offset_along_mm=0.0,
                                           offset_across_mm=0.0,
                                           rotation_deg=0.0)],
        vias=[], tracks=[])


def _imp_adapter():
    fp = Footprint(ref="R1", uuid="uuid-R1",
                   position=Vector2.from_xy_mm(10.0, 10.0), angle_deg=0.0,
                   layer=BoardLayer.BL_F_Cu, sheet_path_uuids=())

    class _A:
        def get_footprints(self):
            return [fp]

        def get_footprint(self, ref):
            return fp if ref == "R1" else None

        def get_tracks(self):
            return []

        def get_vias(self):
            return []

        def get_footprint_pads(self, footprint):
            return [Pad(number="1", net_name="N", position=footprint.position)]

    return _A()


def test_an_imprint_in_place_at_a_nested_clone_still_places():
    ent = Entity(name="E1", imprint="amp", sheet="ISO In/Signal In A")
    plan = plan_imprint(ent, _imp_rec("ISO In/Signal In A"), _imp_adapter(),
                        Vector2.from_xy_mm(100, 50), 0.0)
    assert plan.mode == "in_place"
    assert plan.moves[0].position == Vector2.from_xy_mm(100, 50)


def test_an_imprint_inside_the_channel_it_names_is_in_place():
    """`entity.sheet "Channel_0"` ⇄ a region recorded on `Channel_0/DAC`: the
    OLD string compare called those different sheets; `sheet_in_path` says the
    entity names this chain, so the region stays where it is."""
    ent = Entity(name="E1", imprint="psu", sheet="Channel_0")
    plan = plan_imprint(ent, _imp_rec("Channel_0/DAC"), _imp_adapter(),
                        Vector2.from_xy_mm(100, 50), 0.0)
    assert plan.mode == "in_place"


def test_an_imprint_onto_a_nested_clone_refuses_in_one_line():
    ent = Entity(name="E1", imprint="amp", sheet="ISO In/Signal In B")
    with pytest.raises(ValidationError) as info:
        plan_imprint(ent, _imp_rec("ISO In/Signal In A"), _imp_adapter(),
                     Vector2.from_xy_mm(100, 50), 0.0)
    text = str(info.value)
    assert "onto a nested clone sheet (ISO In/Signal In B) is not supported yet" in text
    assert "the net remap knows only top-level sheets" in text


def test_a_map_without_a_hierarchy_is_the_old_answer():
    fp = _fp(("b-iso-in", "b-sia", "own"))
    plain = {"b-iso-in": "ISO In", "b-sia": "Signal In A"}
    assert instance_sheet_of(fp, plain) == sheet_of_names(["ISO In", "Signal In A"])
    assert instance_sheet_of(fp, plain) == "ISO In"
