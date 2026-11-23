# tests/selection/test_cell_read_takes_inter_node_copper.py
"""Т1 of plan_2026_11_20_cell_read_takes_inter_node_copper (Денис, 20.11):
«давай дадим приоритет кластеру. Если читаем кластер, пусть автоматически
выпиливает из межнодовой меди» — a selected piece the REGISTRY already knows as a
``net_traces`` piece (a ``net:`` key) is TRANSFERRED to the cell, never subtracted.

The live case (HP-Channel-v099, Log 15:41:48, 20.11): «Update from selection» of
``spoke_lv`` subtracted ``net:b166cc84-…: 4`` — the four pieces of
``5v_idc__driver__spoke_p5v_2`` — on the FOREIGN-KEYS step, before the transfer
could ever see them (``subtract_foreign_copper``; ``is_own_key`` says ``net:`` is
foreign, kicadstamp/selection_narrowing.py:497).

Denis's guards (20.11) are cells here too: the kind of a piece is NOT in the key
(via #0 and track #0 of one record share it) so it comes from the registry FILE
and is checked against the live item (т3в/т3г), and a key that no longer names a
real piece (the record was shortened after the keys were issued) or a piece
standing on another net is SUBTRACTED with a yellow line (т3б).

  т1  the live case: 1 via + 3 tracks, KEYED in the registry, the record touches
      the instance -> all four are kept AND named for the transfer;
  т2  control: the same copper NOT in the registry -> today's GEOMETRY path
      gives the same answer;
  т3  the KEY alone: the walk owns nothing (the matcher is stubbed out, which is
      what a shifted geometry does to it) -> the piece is still transferred, with
      the index read from the key;
  т3б the record was SHORTENED after its keys were issued (index out of range),
      and a piece standing on ANOTHER net -> subtracted, with a yellow line;
  т3в via #0 AND track #0 of ONE record (the SAME key — the kind is not in it) ->
      both transferred, each into its own section;
  т3г the registry FILE's kind disagrees with the live item's type -> subtracted,
      with a yellow line.
"""
import json
from types import SimpleNamespace

import pytest

import kicadstamp.net_trace_planner as planner_mod
from kicadstamp.config import NetTrace, TemplateTrack, TemplateVia, format_version
from kicadstamp.constants import ROLE_FIELD_NAME
from kicadstamp.domain.board import Footprint, Track, Via
from kicadstamp.domain.geometry import BoardLayer, Vector2
from kicadstamp.net_trace_planner import net_trace_registry_key
from tests.fakes.format3 import det_uuid, registry_schema

RECORD = "5v_idc__driver__spoke_p5v_2"
NET = "+5V_IDC"


@pytest.fixture(params=(2, 3), ids=("format2", "format3"))
def gate(request, monkeypatch):
    monkeypatch.setattr(format_version, "CURRENT_FORMAT", request.param)
    return request.param


class _Adapter:
    """Just enough board for the narrowing: fields, footprints, copper."""

    def __init__(self, fields, footprints=(), vias=(), tracks=(), names=None):
        self._fields = fields
        self._footprints = list(footprints)
        self._vias = list(vias)
        self._tracks = list(tracks)
        self._names = dict(names or {})

    def field_names(self, fp):
        return frozenset(self._names.get(fp.ref, ()))

    def get_field_value(self, fp, name):
        role, cluster = self._fields[fp.ref]
        return role if name == ROLE_FIELD_NAME else cluster

    def get_footprints(self):
        return list(self._footprints)

    def get_footprint(self, ref):
        return next((f for f in self._footprints if f.ref == ref), None)

    def get_vias(self):
        return list(self._vias)

    def get_tracks(self):
        return list(self._tracks)

    def get_pad_by_number(self, fp, pad):
        return None

    def get_footprint_pads(self, fp):
        return []

    def get_selected_items(self):
        # The role resolver asks for the selection when narrowing a role; an
        # empty one makes every role resolve over the whole board.
        return []


def _fp(ref, role, cluster, x_mm=0.0, y_mm=0.0, chain=()):
    fp = Footprint(ref=ref, uuid=f"uuid-{ref}",
                   position=Vector2.from_xy_mm(x_mm, y_mm), angle_deg=0.0,
                   layer=BoardLayer.BL_F_Cu)
    fp.sheet_path_uuids = tuple(chain)
    return fp


def _via(uuid, net=NET, x_mm=30.0, y_mm=30.0):
    return Via(uuid=uuid, position=Vector2.from_xy_mm(x_mm, y_mm), net_name=net,
               drill_mm=0.3, diameter_mm=0.6)


def _track(uuid, net=NET, x_mm=30.0, y_mm=30.0):
    return Track(uuid=uuid, net_name=net,
                 start=Vector2.from_xy_mm(x_mm, y_mm),
                 end=Vector2.from_xy_mm(x_mm + 1.0, y_mm),
                 width_mm=0.2, layer=BoardLayer.BL_B_Cu)


def _record(name=RECORD, *, vias=1, tracks=3, net=NET):
    return NetTrace(
        net=net, name=name, anchor_role="FPGA",
        uuid=det_uuid(f"net_traces:{name}"),
        vias=[TemplateVia(offset_along_mm=0.0, offset_across_mm=0.0, net=net,
                          drill_mm=0.3, diameter_mm=0.6) for _ in range(vias)],
        # Distinct segments, like the live "tail": track i runs from i to i+1
        # along the anchor's axis (three IDENTICAL templates would plan one
        # segment, and only one live piece could ever match it).
        tracks=[TemplateTrack(start_along_mm=float(i), start_across_mm=0.0,
                              end_along_mm=float(i) + 1.0, end_across_mm=0.0,
                              width_mm=0.2, net=net, layer="B.Cu")
                for i in range(tracks)],
    )


def _cfg(net_traces):
    cell = SimpleNamespace(uuid=det_uuid("cells:spoke_lv"))
    entity = SimpleNamespace(name="spoke_p5v_2", uuid=det_uuid("entities:spoke"),
                             cell="spoke_lv", cluster="SPOKE_P5V",
                             sheet="SwitchA")
    return SimpleNamespace(entities=[entity], clone_placements=[],
                           cells={"spoke_lv": cell}, net_traces=list(net_traces))


def _via_entry(uuid, net=NET, x_mm=30.0, y_mm=30.0):
    return {"uuid": uuid, "x_mm": x_mm, "y_mm": y_mm, "net": net,
            "drill_mm": 0.3, "diameter_mm": 0.6}


def _track_entry(uuid, net=NET, x_mm=30.0, y_mm=30.0):
    return {"uuid": uuid, "start_x_mm": x_mm, "start_y_mm": y_mm,
            "end_x_mm": x_mm + 1.0, "end_y_mm": y_mm, "width_mm": 0.2,
            "net": net, "layer": "B.Cu"}


def _write_registries(tmp_path, via_entries=None, track_entries=None):
    """The two registry files `load_registry_entries` reads, in the schema the
    format gate expects (the same two paths the sibling net-trace cells use)."""
    schema = registry_schema()
    (tmp_path / "registry").mkdir(exist_ok=True)
    (tmp_path / "registry" / "config.registry.json").write_text(
        json.dumps({"schema_version": schema, **(via_entries or {})}),
        encoding="utf-8")
    (tmp_path / "tracks").mkdir(exist_ok=True)
    (tmp_path / "tracks" / "config.tracks.registry.json").write_text(
        json.dumps({"schema_version": schema, **(track_entries or {})}),
        encoding="utf-8")


_THIS_INSTANCE = {"cell": "spoke_lv", "cluster": "SPOKE_P5V", "sheet": "SwitchA"}


def _narrow(tmp_path, adapter, cfg, *, vias, tracks, transfer=True,
            journal=None, footprints=None):
    from gui.mixed_selection import narrow_mixed_selection

    return narrow_mixed_selection(
        config_path=str(tmp_path / "config.sexp"), adapter=adapter,
        footprints=(list(footprints) if footprints is not None
                    else [adapter.get_footprints()[0]]), vias=list(vias),
        tracks=list(tracks), cfg=cfg, sheet_names={"sa": "SwitchA"},
        cell_name="spoke_lv", cell_roles={"DA", "DB"},
        remembered_cluster="SPOKE_P5V", remembered_sheet="SwitchA",
        explode_transfer=transfer,
        explode_journal=(journal if journal is not None
                         else (_THIS_INSTANCE if transfer else None)))


@pytest.fixture
def board(tmp_path):
    """C1 (this cluster, on SwitchA) + U1 (the record's anchor, standing AWAY
    from the pieces, so no piece is found by geometry by accident)."""
    (tmp_path / "config.sexp").write_text("", encoding="utf-8")
    fp = _fp("C1", "DA", "SPOKE_P5V", 0.0, 0.0, ("sa", "c1-own"))
    anchor = _fp("U1", "FPGA", None, 10.0, 10.0)
    return SimpleNamespace(tmp_path=tmp_path, footprints=[fp, anchor],
                           fields={"C1": ("DA", "SPOKE_P5V"),
                                   "U1": ("FPGA", None)})


def _texts(prelude):
    return [text for text, _level in prelude.log_lines]


def _uuids(items):
    return [getattr(item, "uuid", None) for item in items]


# ── т1: the live case — KEYED pieces are transferred, not subtracted ────────

def test_t1_keyed_pieces_of_the_record_go_to_the_cell(gate, board):
    """1 via + 3 tracks, all four KEYED in the registry, the record touches the
    instance: the cell gains all four and the record is named for the transfer.

    RED before Т1: `subtract_foreign_copper` said `net:` is foreign and removed
    all four — the live «subtracted from selection: … net:…: 4»."""
    nt = _record()
    cfg = _cfg([nt])
    sel_via = _via("sel-v")
    sel_tracks = [_track(f"sel-t{i}") for i in range(3)]
    _write_registries(
        board.tmp_path,
        via_entries={net_trace_registry_key(nt, 0): _via_entry("sel-v")},
        track_entries={net_trace_registry_key(nt, i): _track_entry(f"sel-t{i}")
                       for i in range(3)})
    adapter = _Adapter(board.fields, footprints=board.footprints,
                       vias=[sel_via], tracks=sel_tracks)

    prelude = _narrow(board.tmp_path, adapter, cfg, vias=[sel_via],
                      tracks=sel_tracks)

    assert prelude is not None and prelude.refusal is None
    assert _uuids(prelude.vias) == ["sel-v"], "the via STAYS in the read"
    assert sorted(_uuids(prelude.tracks)) == ["sel-t0", "sel-t1", "sel-t2"]
    assert sorted((t.kind, t.index) for t in prelude.transfers) == [
        ("track", 0), ("track", 1), ("track", 2), ("via", 0)]
    assert {t.identity for t in prelude.transfers} == {RECORD}
    assert prelude.subtracted_net_traces == (), \
        "nothing of this record may be reported as subtracted"
    texts = _texts(prelude)
    assert any("transferred from net_traces" in t for t in texts), texts
    assert not any("subtracted from selection" in t for t in texts), texts


def test_t1b_the_key_alone_needs_no_explode_door(gate, board):
    """Р1: «при ЛЮБОМ чтении». The same KEYED pieces with the explode door CLOSED
    (no journal, no transfer flag — the ordinary "Update from selection") are
    transferred just the same: the registry claim does not depend on the door.

    The GEOMETRY-only path still does (Т3 opens it) — this cell says which half
    moved and pins it."""
    nt = _record()
    cfg = _cfg([nt])
    sel_via = _via("sel-v")
    sel_tracks = [_track(f"sel-t{i}") for i in range(3)]
    _write_registries(
        board.tmp_path,
        via_entries={net_trace_registry_key(nt, 0): _via_entry("sel-v")},
        track_entries={net_trace_registry_key(nt, i): _track_entry(f"sel-t{i}")
                       for i in range(3)})
    adapter = _Adapter(board.fields, footprints=board.footprints,
                       vias=[sel_via], tracks=sel_tracks)

    prelude = _narrow(board.tmp_path, adapter, cfg, vias=[sel_via],
                      tracks=sel_tracks, transfer=False)

    assert prelude is not None and prelude.refusal is None
    assert sorted((t.kind, t.index) for t in prelude.transfers) == [
        ("track", 0), ("track", 1), ("track", 2), ("via", 0)]
    assert prelude.subtracted_net_traces == ()


# ── т2: control — the GEOMETRY path gives the same answer ───────────────────

def test_t2_unregistered_pieces_of_the_record_go_to_the_cell(gate, board):
    """The same four pieces with an EMPTY registry: today's geometry path already
    transfers them — the control that Т1 must not break."""
    nt = _record()
    cfg = _cfg([nt])
    # Geometry needs the pieces where the record plans them: at its anchor.
    sel_via = _via("sel-v", x_mm=10.0, y_mm=10.0)
    sel_tracks = [_track(f"sel-t{i}", x_mm=10.0 + i, y_mm=10.0) for i in range(3)]
    _write_registries(board.tmp_path)
    adapter = _Adapter(board.fields, footprints=board.footprints,
                       vias=[sel_via], tracks=sel_tracks)

    prelude = _narrow(board.tmp_path, adapter, cfg, vias=[sel_via],
                      tracks=sel_tracks)

    assert prelude is not None and prelude.refusal is None
    assert _uuids(prelude.vias) == ["sel-v"]
    assert len(prelude.tracks) == 3
    assert {t.identity for t in prelude.transfers} == {RECORD}


# ── т3: the KEY alone — the walk names nothing, the key still transfers ─────

def test_t3_the_index_comes_from_the_key_when_the_walk_finds_nothing(
        gate, board, monkeypatch):
    """A shifted piece: the matcher owns nothing (stubbed out — that is exactly
    what a geometry that no longer matches does to the walk), yet the registry
    key still names piece #2 of the record, and the transfer uses THAT index.

    RED before Т1: nothing reached the transfer at all."""
    nt = _record()
    cfg = _cfg([nt])
    sel_tracks = [_track("sel-t2")]
    _write_registries(
        board.tmp_path,
        track_entries={net_trace_registry_key(nt, 2): _track_entry("sel-t2")})

    def _owns_nothing(_adapter, record, **_kw):
        return planner_mod.LiveCopper(nt=record, identity=RECORD, pieces=[])

    monkeypatch.setattr(planner_mod, "find_live_copper", _owns_nothing)
    adapter = _Adapter(board.fields, footprints=board.footprints,
                       tracks=sel_tracks)

    prelude = _narrow(board.tmp_path, adapter, cfg, vias=[], tracks=sel_tracks)

    assert prelude is not None and prelude.refusal is None
    assert _uuids(prelude.tracks) == ["sel-t2"], "the piece STAYS in the read"
    assert [(t.kind, t.index) for t in prelude.transfers] == [("track", 2)]


# ── т3б: the guard — a key that no longer names a real piece ────────────────

def test_t3b_a_shortened_record_or_another_net_subtracts_with_a_line(gate, board):
    """Denis, 20.11: «индекс из ключа вне списка или кусок другой цепи → не
    переносить, вычесть, жёлтая строка».

    (1) the record was SHORTENED after its keys were issued: the key says track #2,
        the record holds one track;
    (2) the piece stands on another net than the record's own piece.
    Both are subtracted (never silently read into the cell) and both are named."""
    short = _record(tracks=1)
    cfg = _cfg([short])
    stale_track = _track("sel-stale")
    other_net_track = _track("sel-other", net="+3V3")
    _write_registries(
        board.tmp_path,
        track_entries={net_trace_registry_key(short, 2): _track_entry("sel-stale"),
                       net_trace_registry_key(short, 0):
                           _track_entry("sel-other", net="+3V3")})
    adapter = _Adapter(board.fields, footprints=board.footprints,
                       tracks=[stale_track, other_net_track])

    prelude = _narrow(board.tmp_path, adapter, cfg, vias=[],
                      tracks=[stale_track, other_net_track])

    assert prelude is not None and prelude.refusal is None
    assert prelude.tracks == [], "neither piece may be read into the cell"
    assert prelude.transfers == ()
    texts = _texts(prelude)
    assert any("no longer has that piece" in t for t in texts), texts
    assert any("stands on net" in t for t in texts), texts
    assert sum(count for _label, count in prelude.subtracted_net_traces) == 2


# ── т3в: via #0 and track #0 of ONE record share the SAME key ───────────────

def test_t3v_a_via_and_a_track_of_one_record_both_go_to_their_own_section(
        gate, board):
    """«переходное №0 + дорожка №0 одной записи — обе уходят в свои секции».

    The key carries NO kind, so via #0 and track #0 of one record produce the
    same key byte for byte; the kind comes from the registry FILE and each piece
    lands in its own list of the transfer."""
    nt = _record(vias=1, tracks=1)
    cfg = _cfg([nt])
    key = net_trace_registry_key(nt, 0)
    sel_via = _via("sel-v")
    sel_track = _track("sel-t")
    _write_registries(board.tmp_path,
                      via_entries={key: _via_entry("sel-v")},
                      track_entries={key: _track_entry("sel-t")})
    assert net_trace_registry_key(nt, 0) == key, "one key for both sections"
    adapter = _Adapter(board.fields, footprints=board.footprints,
                       vias=[sel_via], tracks=[sel_track])

    prelude = _narrow(board.tmp_path, adapter, cfg, vias=[sel_via],
                      tracks=[sel_track])

    assert prelude is not None and prelude.refusal is None
    assert _uuids(prelude.vias) == ["sel-v"]
    assert _uuids(prelude.tracks) == ["sel-t"]
    assert sorted((t.kind, t.index) for t in prelude.transfers) == [
        ("track", 0), ("via", 0)]


# ── т3г: the kind is checked against the LIVE item's type ──────────────────

def test_t3g_a_kind_mismatch_subtracts_with_a_line(gate, board):
    """The registry FILE says track, the live item is a via: the key came from a
    registry that no longer describes the board, so the piece is NOT transferred
    — it is subtracted, and the line says why."""
    nt = _record(tracks=1)
    cfg = _cfg([nt])
    sel_via = _via("sel-v")
    _write_registries(
        board.tmp_path,
        track_entries={net_trace_registry_key(nt, 0): _track_entry("sel-v")})
    adapter = _Adapter(board.fields, footprints=board.footprints,
                       vias=[sel_via])

    prelude = _narrow(board.tmp_path, adapter, cfg, vias=[sel_via], tracks=[])

    assert prelude is not None and prelude.refusal is None
    assert prelude.vias == [], "the piece may not be read into the cell"
    assert prelude.transfers == ()
    texts = _texts(prelude)
    assert any("while the live item is a" in t for t in texts), texts
    assert sum(count for _label, count in prelude.subtracted_net_traces) == 1
    # С2: the line names the RECORD, never its registry uuid (`net:<uuid>`).
    assert any(RECORD in t for t in texts), texts
    assert not any("net:" in t for t in texts), texts


# ── т4: Р2 — the boundary is the instance, not the selection frame ──────────

def test_t4_a_record_between_other_nodes_is_subtracted_and_named(gate, board):
    """Р2: the record's OWN address names another instance and none of its ends
    stands on a component of this one — its piece is copper BETWEEN two other
    nodes, so it is subtracted as before, and ONE yellow line names the record.

    RED before Т2: the keyed piece was transferred wherever the record sat."""
    nt = _record(tracks=1)
    nt.anchor_role = "DRV"
    nt.anchor_cluster = "OTHER_CL"
    nt.anchor_sheet = "OtherSheet"
    cfg = _cfg([nt])
    sel_track = _track("sel-t")
    _write_registries(
        board.tmp_path,
        track_entries={net_trace_registry_key(nt, 0): _track_entry("sel-t")})
    far = _fp("U7", "DRV", "OTHER_CL", 50.0, 50.0)
    adapter = _Adapter({**board.fields, "U7": ("DRV", "OTHER_CL")},
                       footprints=[*board.footprints, far], tracks=[sel_track])

    prelude = _narrow(board.tmp_path, adapter, cfg, vias=[], tracks=[sel_track])

    assert prelude is not None and prelude.refusal is None
    assert prelude.tracks == [], "the piece may not be read into the cell"
    assert prelude.transfers == ()
    texts = _texts(prelude)
    assert any("does not touch this instance" in t for t in texts), texts
    assert sum(count for _label, count in prelude.subtracted_net_traces) == 1


def test_t4b_a_pieces_far_end_on_this_instance_makes_the_record_touch_it(
        gate, board):
    """Р2's OTHER half (Ч0 п.4: «клетка должна проверять именно это, а не только
    адрес»): the record's own address names ANOTHER instance, but the far end of
    its piece (`net_from_role`) stands on a component of THIS one — the piece is
    copper of this instance's node, so it IS transferred."""
    nt = _record(tracks=1)
    nt.anchor_role = "DRV"
    nt.anchor_cluster = "OTHER_CL"
    nt.anchor_sheet = "OtherSheet"
    nt.tracks[0].net_from_role = "DA"      # the far end: C1, a component HERE
    cfg = _cfg([nt])
    sel_track = _track("sel-t")
    _write_registries(
        board.tmp_path,
        track_entries={net_trace_registry_key(nt, 0): _track_entry("sel-t")})
    far = _fp("U7", "DRV", "OTHER_CL", 50.0, 50.0)
    adapter = _Adapter({**board.fields, "U7": ("DRV", "OTHER_CL")},
                       footprints=[*board.footprints, far], tracks=[sel_track])

    prelude = _narrow(board.tmp_path, adapter, cfg, vias=[], tracks=[sel_track])

    assert prelude is not None and prelude.refusal is None
    assert _uuids(prelude.tracks) == ["sel-t"], "the piece STAYS in the read"
    assert [(t.kind, t.index) for t in prelude.transfers] == [("track", 0)]
    assert prelude.subtracted_net_traces == ()


def test_t4v_a_geometry_piece_between_other_nodes_is_subtracted(gate, board):
    """Р2 on the GEOMETRY path too (Т3): the piece sits exactly where the record
    plans it (so the walk finds it without any registry key), but the record's own
    address names ANOTHER instance — it is copper between other nodes, so it is
    subtracted and one line names the record.

    RED before Т3: the geometry half of the map was transferred whole, and Р2 only
    reached the keyed pieces."""
    nt = _record(tracks=1)
    nt.anchor_role = "DRV"
    nt.anchor_cluster = "OTHER_CL"
    nt.anchor_sheet = None
    cfg = _cfg([nt])
    sel_track = _track("sel-t", x_mm=50.0, y_mm=50.0)   # where the record plans it
    _write_registries(board.tmp_path)                   # NOT in the registry
    far = _fp("U7", "DRV", "OTHER_CL", 50.0, 50.0)
    adapter = _Adapter({**board.fields, "U7": ("DRV", "OTHER_CL")},
                       footprints=[*board.footprints, far], tracks=[sel_track])

    prelude = _narrow(board.tmp_path, adapter, cfg, vias=[], tracks=[sel_track])

    assert prelude is not None and prelude.refusal is None
    assert prelude.tracks == [], "the piece may not be read into the cell"
    assert prelude.transfers == ()
    texts = _texts(prelude)
    assert any("does not touch this instance" in t for t in texts), texts


# ── С3: the two halves of «another cluster» are told apart BY the narrow ───

def test_c3_the_two_halves_of_another_cluster_are_told_apart(gate, board):
    """С3: a component of a DIFFERENT cluster and a component with NO Cluster field
    are two different defects — the narrow tells them apart (it is the one place
    that reads every selected component's Cluster field) and carries the refs, so
    the read's line can name them.

    RED with the mutant «both halves are one reason»: the two lists collapse."""
    cfg = _cfg([])
    r12 = _fp("R12", "DB", "OTHER_CL", 20.0, 20.0)
    j5 = _fp("J5", "DB", None, 22.0, 20.0)
    adapter = _Adapter({**board.fields, "R12": ("DB", "OTHER_CL"),
                        "J5": ("DB", None)},
                       footprints=[*board.footprints, r12, j5])

    # The selection is the instance's own component + the two strangers (U1, the
    # record anchor, is on the BOARD but not selected).
    prelude = _narrow(board.tmp_path, adapter, cfg, vias=[], tracks=[],
                      footprints=[board.footprints[0], r12, j5])

    assert prelude is not None and prelude.refusal is None
    assert prelude.other_cluster_refs == ("R12",), prelude.other_cluster_refs
    assert prelude.no_cluster_refs == ("J5",), prelude.no_cluster_refs


# ── С4: a look-alike field name behind a missing tag ───────────────────────

def test_c4_a_lookalike_field_name_is_said_out_loud(gate, board):
    """С4: J5 carries `Claster` instead of `Cluster`, so the read treats it as
    untagged — ONE yellow line says why. `Clusters2025` is a DIFFERENT field (two
    edits away? no — it is four) and stays silent."""
    cfg = _cfg([])
    j5 = _fp("J5", "DB", None, 22.0, 20.0)
    j6 = _fp("J6", "DB", None, 24.0, 20.0)
    adapter = _Adapter({**board.fields, "J5": ("DB", None), "J6": ("DB", None)},
                       footprints=[*board.footprints, j5, j6],
                       names={"J5": ("Claster",), "J6": ("Clusters2025",)})

    prelude = _narrow(board.tmp_path, adapter, cfg, vias=[], tracks=[],
                      footprints=[board.footprints[0], j5, j6])
    texts = _texts(prelude)

    assert any("looks like 'Cluster'" in t for t in texts), texts
    assert sum(1 for t in texts if "looks like" in t) == 1, texts


def test_c4_an_effective_cluster_earns_no_line(gate, board):
    """С4: the line is about the EFFECTIVE value. Here an override supplied the
    Cluster (the read sees a tag, from its value store), while the footprint itself
    carries `Claster` — the component is read as ANOTHER CLUSTER, which the reason
    line already says; calling it «untagged because of a typo» would be a second,
    wrong story about the same component."""
    cfg = _cfg([])
    j5 = _fp("J5", "DB", None, 22.0, 20.0)
    adapter = _Adapter({**board.fields, "J5": ("DB", "OTHER_CL")},
                       footprints=[*board.footprints, j5],
                       names={"J5": ("Claster",)})

    prelude = _narrow(board.tmp_path, adapter, cfg, vias=[], tracks=[],
                      footprints=[board.footprints[0], j5])
    texts = _texts(prelude)

    assert prelude.other_cluster_refs == ("J5",), prelude.other_cluster_refs
    assert not any("looks like" in t for t in texts), texts


# ── М9: a `net:` key whose RECORD is gone keeps the ORPHAN path ────────────

def test_m9_a_net_key_of_a_vanished_record_is_read_as_an_orphan(gate, board):
    """М9 (acceptance 22.11): a `net:` key whose record no longer exists in the
    config OWNS NOTHING (plan_2026_10_27_orphan_anchor_keys_own_nothing), so the
    piece must go the OLD way — read, counted as an orphan, never subtracted as if
    somebody owned it.

    RED with the mutant «`_split_net_keyed` ignores orphans»: the piece reaches
    `_keyed_decisions`, `net_trace_record_for_key` finds no record, and it is
    SUBTRACTED with «the record no longer has that piece»."""
    cfg = _cfg([])                      # the record is GONE from the config
    sel_track = _track("sel-t")
    _write_registries(
        board.tmp_path,
        track_entries={"net:ghost-record|ghost-record|None|0":
                       _track_entry("sel-t")})
    adapter = _Adapter(board.fields, footprints=board.footprints,
                       tracks=[sel_track])

    prelude = _narrow(board.tmp_path, adapter, cfg, vias=[], tracks=[sel_track])

    assert prelude is not None and prelude.refusal is None
    assert _uuids(prelude.tracks) == ["sel-t"], "the piece STAYS in the read"
    assert prelude.transfers == ()
    texts = _texts(prelude)
    assert any("no longer in the config" in t for t in texts), texts
    assert not any("no longer has that piece" in t for t in texts), texts


# ── т8: Р5 — the door's journal no longer decides anything ──────────────────

def test_t8_a_foreign_journal_no_longer_decides_the_transfer(gate, board):
    """Р5: the Explode tab asks for a re-read with the journal of ANOTHER instance;
    the read resolves this one. Until Т3 that turned the transfer off and said «the
    instance being read is not the exploded one». Now the boundary is Р2 (the
    record touches this instance), so the piece is transferred and the old line is
    gone."""
    nt = _record()
    cfg = _cfg([nt])
    sel_via = _via("sel-v")
    _write_registries(
        board.tmp_path,
        via_entries={net_trace_registry_key(nt, 0): _via_entry("sel-v")})
    adapter = _Adapter(board.fields, footprints=board.footprints,
                       vias=[sel_via])

    prelude = _narrow(board.tmp_path, adapter, cfg, vias=[sel_via], tracks=[],
                      journal={"cell": "spoke_lv", "cluster": "SOME_OTHER",
                               "sheet": "X"})

    assert prelude is not None and prelude.refusal is None
    assert _uuids(prelude.vias) == ["sel-v"]
    assert [(t.kind, t.index) for t in prelude.transfers] == [("via", 0)]
    texts = _texts(prelude)
    assert not any("not the exploded one" in t for t in texts), texts
