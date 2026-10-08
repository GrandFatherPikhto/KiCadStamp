# tests/net/test_net_trace_single_board_read.py
"""Part А1 cells (plan ``plan_2026_10_08_narrowing_net_traces_cost``): the board's
copper is read ONCE per operation, not once per ``net_traces:`` record.

The live finding: ONE "Update from selection" walked every record of the config and,
for each, read the WHOLE board (219 vias / 1256 tracks in the 3ch-awg-tia-v103
profile) — measured «до» on 08.10.2026 with
``kicadstamp/diagnostics/probe_narrowing_net_traces_cost.py``: 73 records → 292
board reads (215 350 items) for one click. The cells below pin:

  * `read_live_copper` is the ONE reader, and ``live=`` makes both matching entries
    (``match_net_trace_pieces`` / ``find_live_copper``) use it instead of the board;
  * ONE narrowing reads the board exactly once (it used to be once per record, and
    twice per kind: the via half and the track half each walked the whole list);
  * the ownership map is computed ONCE for both kinds, so the yellow "could not
    match" line of an unresolvable record is printed ONCE (it used to be printed
    twice — once per kind);
  * the "Разнос" table (``explode._nt_rows``) reads once for the whole table;
  * the RESULT is unchanged: the existing ``tests/selection`` cells for what is
    subtracted and what is transferred stay green without a single edit (rule 33).
"""
import json
from types import SimpleNamespace

import pytest

import kicadstamp.net_trace_planner as planner_mod
from kicadstamp.config import NetTrace, TemplateTrack, TemplateVia
from kicadstamp.config import format_version
from kicadstamp.constants import CLUSTER_FIELD_NAME, ROLE_FIELD_NAME
from kicadstamp.domain.board import Footprint, Track, Via
from kicadstamp.domain.geometry import BoardLayer, Vector2
from kicadstamp.net_trace_planner import (
    TRACK, VIA, find_live_copper, match_net_trace_pieces, net_trace_registry_key,
    read_live_copper,
)
from tests.fakes.format3 import det_uuid, registry_schema


@pytest.fixture(params=(2, 3), ids=("format2", "format3"))
def gate(request, monkeypatch):
    monkeypatch.setattr(format_version, "CURRENT_FORMAT", request.param)
    return request.param


class _CountingAdapter:
    """The fake board that COUNTS every copper read — the whole point of А1.

    ``get_vias``/``get_tracks`` raise when the cell forbids them (the "the given
    read must be used, the board must not be touched" cells)."""

    def __init__(self, fields, footprints=(), vias=(), tracks=(), forbid_reads=False):
        self._fields = fields
        self._footprints = list(footprints)
        self._vias = list(vias)
        self._tracks = list(tracks)
        self.forbid_reads = forbid_reads
        self.reads = [0, 0]          # [get_vias, get_tracks]

    def _read(self, kind):
        if self.forbid_reads:
            raise AssertionError(f"the board's {kind} was read although `live=` was given")
        self.reads[0 if kind == "vias" else 1] += 1
        return list(self._vias if kind == "vias" else self._tracks)

    def get_vias(self):
        return self._read("vias")

    def get_tracks(self):
        return self._read("tracks")

    def get_field_value(self, fp, name):
        role, cluster = self._fields[fp.ref]
        return role if name == ROLE_FIELD_NAME else cluster

    def get_footprints(self):
        return list(self._footprints)

    def get_footprint(self, ref):
        return next((f for f in self._footprints if f.ref == ref), None)

    def get_selected_items(self):
        return []


def _fp(ref, role, cluster, x_mm=0.0, y_mm=0.0, chain=()):
    fp = Footprint(ref=ref, uuid=f"uuid-{ref}",
                   position=Vector2.from_xy_mm(x_mm, y_mm), angle_deg=0.0,
                   layer=BoardLayer.BL_F_Cu)
    fp.sheet_path_uuids = tuple(chain)
    return fp


def _via(uuid, net="NT1", x_mm=0.0, y_mm=0.0):
    return Via(uuid=uuid, position=Vector2.from_xy_mm(x_mm, y_mm), net_name=net,
               drill_mm=0.3, diameter_mm=0.6)


def _track(uuid, net="NT1"):
    return Track(uuid=uuid, net_name=net,
                 start=Vector2.from_xy_mm(0.0, 0.0),
                 end=Vector2.from_xy_mm(1.0, 0.0),
                 width_mm=0.25, layer=BoardLayer.BL_F_Cu)


def _record(name, net="NT1"):
    """ONE ``net_traces:`` record WITH copper (a record without copper never
    reaches the matcher — its expectations are empty — so it could not measure
    anything). Its anchor_role is ``FPGA`` and the fake board holds NO such
    footprint, so the anchor never resolves and the match falls back to the
    registry tier — which is exactly the live case that used to read the whole
    board once per record."""
    return NetTrace(
        net=net, name=name, anchor_role="FPGA",
        uuid=det_uuid(f"net_traces:{name}"),
        tracks=[TemplateTrack(start_along_mm=0.0, start_across_mm=0.0,
                              end_along_mm=1.0, end_across_mm=0.0, width_mm=0.2,
                              net=net, layer="F.Cu")],
        vias=[TemplateVia(offset_along_mm=0.0, offset_across_mm=0.0, net=net,
                          drill_mm=0.3, diameter_mm=0.6)],
    )


def _cfg(net_traces):
    cell = SimpleNamespace(uuid=det_uuid("cells:dac_buf"))
    entity = SimpleNamespace(name="dac0", uuid=det_uuid("entities:dac0"),
                             cell="dac_buf", cluster="DAC_BUF",
                             sheet="Channel_0")
    return SimpleNamespace(entities=[entity], clone_placements=[],
                           cells={"dac_buf": cell}, net_traces=list(net_traces),
                           chains=[])


def _config_path(tmp_path):
    p = tmp_path / "config.sexp"
    p.write_text("", encoding="utf-8")
    return str(p)


def _write_registries(tmp_path, via_entries=None, track_entries=None):
    """The two registry files the narrowing loads (`load_registry_entries`), in
    the schema the format gate expects."""
    schema = registry_schema()
    (tmp_path / "registry").mkdir(exist_ok=True)
    (tmp_path / "registry" / "config.registry.json").write_text(
        json.dumps({"schema_version": schema, **(via_entries or {})}),
        encoding="utf-8")
    (tmp_path / "tracks").mkdir(exist_ok=True)
    (tmp_path / "tracks" / "config.tracks.registry.json").write_text(
        json.dumps({"schema_version": schema, **(track_entries or {})}),
        encoding="utf-8")


def _narrow(tmp_path, adapter, cfg, *, vias, tracks, transfer=False):
    from gui.mixed_selection import narrow_mixed_selection

    return narrow_mixed_selection(
        config_path=_config_path(tmp_path), adapter=adapter,
        footprints=[adapter.get_footprints()[0]], vias=list(vias),
        tracks=list(tracks), cfg=cfg, sheet_names={"ch0": "Channel_0"},
        cell_name="dac_buf", cell_roles={"DA", "DB"},
        remembered_cluster="DAC_BUF", remembered_sheet="Channel_0",
        explode_transfer=transfer,
        explode_journal=({"cell": "dac_buf", "cluster": "DAC_BUF",
                          "sheet": "Channel_0"} if transfer else None))


# ── the ONE reader ──────────────────────────────────────────────────────────

def test_match_pieces_uses_the_given_read(gate):
    """`live=` is used as given — the board is never touched."""
    nt = _record("NT1")
    adapter = _CountingAdapter({}, forbid_reads=True)
    expectations = planner_mod.Expectation(
        kind=VIA, index=0, registry_key=net_trace_registry_key(nt, 0))
    matched = match_net_trace_pieces(
        adapter, [expectations], via_registry=SimpleNamespace(entries={}),
        track_registry=SimpleNamespace(entries={}),
        live={VIA: [], TRACK: []})
    assert [m.live for m in matched] == [None]


def test_find_live_copper_takes_the_given_read(gate):
    """The same for find_live_copper (the tier-1 uuid path needs the live item)."""
    nt = _record("NT1")
    live_via = _via("v-1")
    adapter = _CountingAdapter({}, forbid_reads=True)
    stub_registry = SimpleNamespace(entries={
        net_trace_registry_key(nt, 0): SimpleNamespace(uuid="v-1")})
    result = find_live_copper(
        adapter, nt, via_registry=stub_registry,
        track_registry=SimpleNamespace(entries={}),
        live={VIA: [live_via], TRACK: []})
    assert [i.uuid for i in result.found] == ["v-1"]


def test_read_live_copper_returns_both_kinds():
    adapter = _CountingAdapter({}, vias=[_via("v-1")], tracks=[_track("t-1")])
    live = read_live_copper(adapter)
    assert {i.uuid for i in live[VIA]} == {"v-1"}
    assert {i.uuid for i in live[TRACK]} == {"t-1"}
    assert adapter.reads == [1, 1]


# ── one narrowing: ONE board read, ONE walk ─────────────────────────────────

def test_one_narrowing_reads_the_board_once(gate, tmp_path):
    """The key cell: K records → the board is read ONCE.

    Mutation: put the read back inside the per-record loop (or skip the shared
    map) and this cell sees 2×K reads for the vias alone."""
    _write_registries(tmp_path)
    cfg = _cfg([_record("NT1"), _record("NT2"), _record("NT3")])
    fp = _fp("C1", "DA", "DAC_BUF", 10.0, 10.0, ("ch0",))
    adapter = _CountingAdapter({"C1": ("DA", "DAC_BUF")}, footprints=[fp])

    prelude = _narrow(tmp_path, adapter, cfg, vias=[_via("sel-v")],
                      tracks=[_track("sel-t")])

    assert prelude is not None and prelude.refusal is None
    assert len(cfg.net_traces) == 3
    assert adapter.reads == [1, 1], adapter.reads


def test_one_narrowing_walks_the_records_once(gate, tmp_path, monkeypatch):
    """...and each record is visited ONCE, not once per kind (the via half and
    the track half used to walk the whole list separately)."""
    _write_registries(tmp_path)
    cfg = _cfg([_record("NT1"), _record("NT2"), _record("NT3")])
    fp = _fp("C1", "DA", "DAC_BUF", 10.0, 10.0, ("ch0",))
    adapter = _CountingAdapter({"C1": ("DA", "DAC_BUF")}, footprints=[fp])
    calls = []
    original = planner_mod.find_live_copper

    def _counted(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(planner_mod, "find_live_copper", _counted)
    _narrow(tmp_path, adapter, cfg, vias=[_via("sel-v")], tracks=[_track("sel-t")])
    assert len(calls) == 3, calls


def test_the_ownership_map_serves_both_kinds(gate, tmp_path):
    """ONE map: `read_net_trace_owned` gives exactly what the subtraction removes,
    and it is computed for the union of the two lists."""
    from kicadstamp.selection_narrowing import (
        read_net_trace_owned, subtract_net_trace_copper)

    _write_registries(tmp_path)
    nt = _record("NT1")
    cfg = _cfg([nt])
    selected_via = _via("sel-v")
    adapter = _CountingAdapter({}, vias=[selected_via])
    owned, notes = read_net_trace_owned(
        [selected_via], cfg.net_traces, adapter,
        via_entries={net_trace_registry_key(nt, 0): SimpleNamespace(uuid="sel-v")},
        track_entries={}, live=read_live_copper(adapter))
    sub = subtract_net_trace_copper(
        [selected_via], cfg.net_traces, adapter, via_entries={}, track_entries={},
        owned=owned, notes=notes)
    assert owned["sel-v"].identity == "NT1"       # the RECORD's identity
    assert [i.uuid for i in sub.removed] == ["sel-v"]
    assert adapter.reads == [1, 1], "the walk must not read the board again"


def test_an_unresolvable_record_is_reported_once(gate, tmp_path):
    """The yellow «cannot match its copper» line used to be printed TWICE (the
    walk ran once for vias, once for tracks)."""
    _write_registries(tmp_path)
    cfg = _cfg([_record("NT1"), _record("NT2")])
    fp = _fp("C1", "DA", "DAC_BUF", 10.0, 10.0, ("ch0",))
    adapter = _CountingAdapter({"C1": ("DA", "DAC_BUF")}, footprints=[fp])

    prelude = _narrow(tmp_path, adapter, cfg, vias=[_via("sel-v")],
                      tracks=[_track("sel-t")])

    texts = [text for text, _level in prelude.log_lines]
    unresolved = [t for t in texts if "it was not subtracted" in t]
    assert len(unresolved) == len(set(unresolved)) == 2, texts


def test_the_transfer_path_reads_the_board_once(gate, tmp_path):
    """Р3 (explode transfer): the same one read, and the owned piece is named.

    The piece is found by GEOMETRY (the anchor resolves: FPGA stands at (10,10)
    and the record's via sits at the anchor), which is the tl;dr of the transfer
    itself — a registry-OWNED piece is subtracted earlier, on the "foreign keys"
    step, and never reaches this path."""
    _write_registries(tmp_path)
    nt = _record("NT1")
    cfg = _cfg([nt])
    fp = _fp("C1", "DA", "DAC_BUF", 0.0, 0.0, ("ch0",))
    anchor = _fp("U1", "FPGA", None, 10.0, 10.0)
    selected_via = _via("sel-v", net="NT1", x_mm=10.0, y_mm=10.0)
    adapter = _CountingAdapter({"C1": ("DA", "DAC_BUF"), "U1": ("FPGA", None)},
                               footprints=[fp, anchor], vias=[selected_via])

    prelude = _narrow(tmp_path, adapter, cfg, vias=[selected_via], tracks=[],
                      transfer=True)

    assert [t.uuid for t in prelude.vias] == ["sel-v"], "the piece STAYS in the read"
    assert [t.identity for t in prelude.transfers], prelude.transfers
    assert adapter.reads == [1, 1], adapter.reads


# ── the "Разнос" table ──────────────────────────────────────────────────────

def test_the_explode_table_reads_the_board_once(gate, tmp_path):
    """``explode._nt_rows`` matches EVERY touching record against the SAME board —
    it used to read that board once per record."""
    from kicadstamp.explode import _nt_rows

    cfg = SimpleNamespace(net_traces=[_record("NT1"), _record("NT2"),
                                      _record("NT3")])
    for nt in cfg.net_traces:
        nt.anchor_sheet = "ch0"          # every record "touches" the cell
    adapter = _CountingAdapter({})

    rows = _nt_rows(cfg, adapter, {"ch0": "Channel_0"}, (), {}, {}, {}, {},
                    ("C1", "ch0"))

    assert rows == []                    # no copper on the fake board
    assert adapter.reads == [1, 1], adapter.reads
