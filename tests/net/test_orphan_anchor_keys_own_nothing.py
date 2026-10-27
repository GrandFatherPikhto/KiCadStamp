# tests/net/test_orphan_anchor_keys_own_nothing.py
"""A registry key whose RECORD is gone from the config owns nothing.

Ч1 of plan_2026_10_27_orphan_anchor_keys_own_nothing (Denis, 27.10: «сирота — по
ЛЮБОМУ префиксу; prune уже удаляет по этому определению для всех»). The live defect
on v103: the entity `fpga_oscill_fpga` was rebuilt (cell uuid e8b2c497 -> e00d0b65),
its 25 registry keys stayed behind, and

  * READING the neighbouring instance subtracted 18 live items as «another record's
    copper» (they belong to a record that no longer exists);
  * the first real Redraw PRUNES those keys — i.e. deletes 21 tracks + 4 vias of
    copper that is still on the board and still needed.

The rule (§3 of the plan): (1) on READ an orphan key owns nothing; (2) it does not
occupy the item in the at-current-place pass or in `match_planned_copper`; (3) prune
never names a uuid for deletion that a LIVE key of this run holds (the key goes, the
copper stays), and never names one the board does not carry.

The home of the predicate is `kicadstamp.anchor_ids`: the SAME builder
(`config_anchor_ids`) that `reconcile` protects from a prune, so «what prune deletes»
and «what owns nothing when read» cannot drift apart.
"""
from __future__ import annotations

import json
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from kicadstamp.adopt_at_current_place import adopt_cell_copper_at_current_place
from kicadstamp.anchor_ids import (anchor_part, config_anchor_ids, key_is_orphan,
                                   orphan_keys, orphan_uuids)
from kicadstamp.config import ClonePlacement, format_version
from kicadstamp.constants import CELL_LEVEL_ROLE_PLACEHOLDER, ROLE_FIELD_NAME
from kicadstamp.domain.board import Footprint, Track, Via
from kicadstamp.domain.geometry import BoardLayer, Vector2
from kicadstamp.net_trace_planner import net_trace_anchor_id
from kicadstamp.placement.commands import TrackCommand, ViaCommand
from kicadstamp.placement.services.clone_position_calculator import (
    clone_registry_identity,
    entity_anchor_id,
)
from kicadstamp.placement.services.via_planner import thermal_anchor_id
from kicadstamp.registry import (PlacementRegistry, TrackRegistry,
                                 make_registry_key, record_key_part)
from kicadstamp.registry_match import match_planned_copper
from kicadstamp.selection_narrowing import subtract_foreign_copper
from tests.fakes.format3 import det_uuid, registry_schema

CELL = "dac_buf"
CLUSTER = "DAC_BUF"
NET = "GND"
#: Where the live instance sits: a record stored at (0,0) lands exactly here.
LIVE = (10.0, 10.0)


@pytest.fixture(params=(2, 3), ids=("format2", "format3"))
def gate(request, monkeypatch):
    monkeypatch.setattr(format_version, "CURRENT_FORMAT", request.param)
    return request.param


# ── records, keys, copper: the same shapes the product builds ────────────────

def _entity(name, cell=CELL, cluster=CLUSTER, sheet=None, retired=False):
    """An Entity as the loader yields it (name is required and unique)."""
    return SimpleNamespace(name=name, uuid=det_uuid(f"entities:{name}"),
                           cell=cell, cluster=cluster, sheet=sheet,
                           retired=retired)


def _net_trace(identity, retired=False):
    return SimpleNamespace(name=identity, uuid=det_uuid(f"net_traces:{identity}"),
                           net=identity, retired=retired)


def _thermal(name, retired=False):
    return SimpleNamespace(name=name,
                           uuid=det_uuid(f"thermal_via_arrays:{name}"),
                           net=NET, retired=retired)


def _cell(vias=None):
    """The fixture cell: ONE cell-level via record at (0,0) and a role FPGA there."""
    comps = [SimpleNamespace(role="FPGA", offset_along_mm=0.0,
                             offset_across_mm=0.0, angle_deg=0.0,
                             net_template=None, vias=[])]
    return SimpleNamespace(
        uuid=det_uuid(f"cells:{CELL}"), anchor_role="FPGA", anchor_xy=None,
        vias=(vias if vias is not None else
              [SimpleNamespace(offset_along_mm=0.0, offset_across_mm=0.0)]),
        tracks=[], components=comps)


def _clone(name="dac0", xy=(0.0, 0.0)):
    """The REAL model: the at-current-place pass resolves the instance's roles
    through the planner's calculator, which reads the record, not a dict."""
    return ClonePlacement(cluster=CLUSTER, cell=CELL, xy=xy, name=name,
                          uuid=det_uuid(f"clone_placements:{name}"), nets={})


def _cfg(*, entities=(), clones=(), net_traces=(), thermal=(), cells=None):
    return SimpleNamespace(cells=cells or {CELL: _cell()},
                           entities=list(entities),
                           clone_placements=list(clones),
                           net_traces=list(net_traces),
                           thermal_via_arrays=list(thermal))


def _cell_identity():
    return record_key_part(CELL, det_uuid(f"cells:{CELL}"))


def _own_key(clone, cell):
    """The key the planner writes for the instance's cell-level via record."""
    anchor_id, _cell_name, key_part = clone_registry_identity(clone, {CELL: cell})
    return make_registry_key(anchor_id, key_part, CELL_LEVEL_ROLE_PLACEHOLDER, 0)


def _orphan_key(record, cell=None):
    """A key whose anchor the config no longer produces — the v103 shape: the
    anchor is built exactly as the planner builds it, its record is just gone."""
    anchor_id = (entity_anchor_id(record) if hasattr(record, "cluster")
                 else net_trace_anchor_id(record))
    return make_registry_key(anchor_id, (cell or _cell()).uuid,
                             CELL_LEVEL_ROLE_PLACEHOLDER, 0)


def _copper(uuid, net=NET):
    return SimpleNamespace(uuid=uuid, net_name=net)


def _via_entry(uuid, x_mm=0.0, y_mm=0.0):
    return {"uuid": uuid, "x_mm": x_mm, "y_mm": y_mm, "net": NET,
            "drill_mm": 0.3, "diameter_mm": 0.6}


def _track_entry(uuid, x1=0.0, y1=0.0, x2=1.0, y2=0.0):
    return {"uuid": uuid, "start_x_mm": x1, "start_y_mm": y1, "end_x_mm": x2,
            "end_y_mm": y2, "width_mm": 0.25, "net": NET, "layer": "F.Cu"}


# ── the board stand-in (only what these doors read) ─────────────────────────

class _Adapter:
    def __init__(self, footprints=(), vias=(), tracks=()):
        self._footprints = list(footprints)
        self._vias = list(vias)
        self._tracks = list(tracks)

    def get_field_value(self, fp, name):
        return fp.role if name == ROLE_FIELD_NAME else fp.cluster

    def get_footprints(self):
        return list(self._footprints)

    def get_footprint(self, ref):
        return next((fp for fp in self._footprints if fp.ref == ref), None)

    def get_vias(self):
        return list(self._vias)

    def get_tracks(self):
        return list(self._tracks)

    def get_selected_items(self):
        return []

    def get_footprint_pads(self, fp):
        return []

    def get_pad_by_number(self, fp, number):
        return None

    def temporarily_ignore_selection(self, flag):
        return nullcontext()


class _Fp(Footprint):
    def __init__(self, ref, role, cluster, x_mm, y_mm, nets=()):
        super().__init__(ref=ref, uuid=f"uuid-{ref}", angle_deg=0.0,
                         position=Vector2.from_xy_mm(x_mm, y_mm),
                         layer=BoardLayer.BL_F_Cu)
        self.role = role
        self.cluster = cluster
        self.sheet_path_uuids = ()
        self.pads = [SimpleNamespace(number=str(i), net_name=n)
                     for i, n in enumerate(nets, start=1)]


def _live_via(uuid, x_mm, y_mm, net=NET):
    return Via(uuid=uuid, net_name=net, position=Vector2.from_xy_mm(x_mm, y_mm),
               drill_mm=0.3, diameter_mm=0.6)


def _live_track(uuid, x1=0.0, y1=0.0, x2=1.0, y2=0.0, net=NET):
    return Track(uuid=uuid, net_name=net, start=Vector2.from_xy_mm(x1, y1),
                 end=Vector2.from_xy_mm(x2, y2), width_mm=0.25,
                 layer=BoardLayer.BL_F_Cu)


def _track_cmd(key, x1=0.0, y1=0.0, x2=1.0, y2=0.0):
    return TrackCommand(start=Vector2.from_xy_mm(x1, y1),
                        end=Vector2.from_xy_mm(x2, y2), width_mm=0.25,
                        net_name=NET, layer=BoardLayer.BL_F_Cu, owner_ref="IC9",
                        registry_key=key)


def _via_cmd(key, x_mm=0.0, y_mm=0.0):
    return ViaCommand(position=Vector2.from_xy_mm(x_mm, y_mm), drill_mm=0.3,
                      diameter_mm=0.6, net_name=NET, owner_ref="IC9",
                      registry_key=key)


def _registries(adapter, tmp_path, *, via_entries=None, track_entries=None):
    via_path = tmp_path / "registry" / "config.registry.json"
    trk_path = tmp_path / "tracks" / "config.tracks.registry.json"
    for path, entries in ((via_path, via_entries or {}),
                          (trk_path, track_entries or {})):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"schema_version": registry_schema(),
                                    **entries}), encoding="utf-8")
    return (PlacementRegistry(adapter, str(via_path)),
            TrackRegistry(adapter, str(trk_path)))


# ── cells 1–2: the READ door ────────────────────────────────────────────────

def test_copper_under_an_orphan_key_is_read_and_named(gate):
    """Cell 1 (§7.4): the key's record is GONE from the config — the copper is
    READ (kept, counted APART), never called «another record's copper»: a claim of
    «another record» needs a record that still exists."""
    live = _entity("dac0")
    gone = _entity("fpga_oscill", cell="fpga_oscill_fpga")
    cfg = _cfg(entities=[live])                      # `gone` is NOT in the config
    key_live = make_registry_key(entity_anchor_id(live), "other_cell", None, 0)
    key_orphan = _orphan_key(gone)
    via_entries = {key_live: _via_entry("u_live"),
                   key_orphan: _via_entry("u_orphan")}

    orphans = orphan_keys(via_entries, {}, config_anchor_ids(cfg))
    assert orphans == frozenset({key_orphan})

    sub = subtract_foreign_copper(
        [_copper("u_live"), _copper("u_orphan")],
        {"u_live": key_live, "u_orphan": key_orphan},
        _cell_identity(), {}, (CLUSTER, None), orphan_keys=orphans)

    assert [i.uuid for i in sub.kept] == ["u_orphan"]
    assert [i.uuid for i in sub.removed] == ["u_live"]
    assert sub.report == ((anchor_part(key_live), 1),)
    assert sub.orphan == ((anchor_part(key_orphan), 1),)


def test_the_orphan_line_names_the_anchor_and_says_what_happened(gate):
    """The Log line is INFO (Denis, 27.10: «чтение сделало то, что просили») and it
    names the anchors exactly as the subtraction line does — `name:<uuid>: 1` is
    what makes a line of 18 items useful."""
    from gui.mixed_selection import _orphan_line

    gone = _entity("fpga_oscill", cell="fpga_oscill_fpga")
    key_orphan = _orphan_key(gone)
    sub = subtract_foreign_copper([_copper("u_orphan")],
                                  {"u_orphan": key_orphan}, _cell_identity(), {},
                                  (CLUSTER, None), orphan_keys={key_orphan})

    line = _orphan_line([sub])

    assert line is not None
    assert "no longer in the config" in line
    assert anchor_part(key_orphan) in line
    assert "1" in line


def test_copper_under_a_live_records_key_is_still_subtracted(gate):
    """Cell 2 (§7.4): a record that DOES exist keeps its claim — the rule is about
    keys that outlived their record, not about keys of other records."""
    other = _entity("pif_avdd", cell="pif_avdd")
    cfg = _cfg(entities=[other])
    key_other = make_registry_key(entity_anchor_id(other), "other_cell", None, 0)
    via_entries = {key_other: _via_entry("u_other")}

    orphans = orphan_keys(via_entries, {}, config_anchor_ids(cfg))

    sub = subtract_foreign_copper([_copper("u_other")],
                                  {"u_other": key_other}, _cell_identity(), {},
                                  (CLUSTER, None), orphan_keys=orphans)
    assert orphans == frozenset()
    assert sub.kept == ()
    assert [i.uuid for i in sub.removed] == ["u_other"]
    assert sub.orphan == ()


def test_a_net_orphan_is_read_like_a_name_orphan(gate):
    """Cell 8 (§7.4, rewritten by Denis): the rule has NO prefix branch — a `net:`
    key whose record is gone is the same nobody's copper as a `name:` one. The
    definition is exactly the one prune already deletes by."""
    live_nt = _net_trace("NT_LIVE")
    gone_nt = _net_trace("NT_GONE")
    cfg = _cfg(net_traces=[live_nt])
    key_live = make_registry_key(net_trace_anchor_id(live_nt), "other_cell", None, 0)
    key_orphan = make_registry_key(net_trace_anchor_id(gone_nt), _cell_identity(),
                                   None, 0)
    track_entries = {key_live: _track_entry("t_live"),
                     key_orphan: _track_entry("t_orphan", y2=5.0)}

    orphans = orphan_keys({}, track_entries, config_anchor_ids(cfg))
    assert orphans == frozenset({key_orphan})

    sub = subtract_foreign_copper(
        [_copper("t_live"), _copper("t_orphan")],
        {"t_live": key_live, "t_orphan": key_orphan},
        _cell_identity(), {}, (CLUSTER, None), orphan_keys=orphans)

    assert [i.uuid for i in sub.kept] == ["t_orphan"]
    assert [i.uuid for i in sub.removed] == ["t_live"]
    assert sub.orphan == ((anchor_part(key_orphan), 1),)


# ── cell 3: the at-current-place pass ───────────────────────────────────────

def test_the_current_place_pass_adopts_copper_an_orphan_key_held(gate, tmp_path):
    """Cell 3: an orphan entry must not occupy the item. Before the rule, `owned`
    contained its uuid, the pass skipped the record standing right on that copper,
    and the same run's prune then deleted the copper — the trail."""
    cell = _cell()
    clone = _clone()
    adapter = _Adapter(footprints=[_Fp("IC9", "FPGA", CLUSTER, *LIVE)],
                       vias=[_live_via("uv", *LIVE)])
    gone = _entity("fpga_oscill", cell="fpga_oscill_fpga")
    orphan = _orphan_key(gone, cell)
    via_reg, _trk_reg = _registries(adapter, tmp_path,
                                    via_entries={orphan: _via_entry("uv", *LIVE)})

    report = adopt_cell_copper_at_current_place(
        adapter, _cfg(clones=[clone]), [SimpleNamespace(kind="clone", obj=clone)],
        via_reg, _trk_reg, write=True)

    assert report.adopted == 1
    assert [item.uuid for _kind, _key, item in report.bound] == ["uv"]
    # The orphan key is NOT the pass's business (it is not a key of this call):
    # prune drops it, but only AFTER the copper is safely bound — cells 4 and 5.
    assert orphan in via_reg.entries
    assert _own_key(clone, cell) in via_reg.entries


def test_match_planned_copper_without_orphan_uuids_is_unchanged(gate, tmp_path):
    """Cell 9: the parameter defaults to EMPTY — a caller that does not know the
    anchors behaves exactly as before (the orphan entry still blocks geometry)."""
    gone = _entity("fpga_oscill", cell="fpga_oscill_fpga")
    orphan = _orphan_key(gone, _cell())
    adapter = _Adapter(vias=[_live_via("uv", *LIVE)])
    via_reg, _trk = _registries(adapter, tmp_path,
                                via_entries={orphan: _via_entry("uv", *LIVE)})
    commands = [_via_cmd(None, *LIVE)]

    blocked = match_planned_copper(via_reg, commands, live_items=adapter.get_vias())
    freed = match_planned_copper(via_reg, commands, live_items=adapter.get_vias(),
                                 orphan_uuids={"uv"})

    assert blocked[0].live is None
    assert freed[0].live is not None and freed[0].live.uuid == "uv"


# ── cells 4–7: prune ────────────────────────────────────────────────────────

def _reconcile_setup(gate, tmp_path, entries, live_tracks):
    """(registry, commands, known anchors) for ONE run of the live record."""
    cell = _cell()
    clone = _clone()
    own = _own_key(clone, cell)
    adapter = _Adapter(footprints=[_Fp("IC9", "FPGA", CLUSTER, *LIVE)],
                       tracks=live_tracks)
    _via_reg, trk_reg = _registries(adapter, tmp_path, track_entries=entries)
    return trk_reg, [_track_cmd(own)], config_anchor_ids(_cfg(clones=[clone]))


def test_prune_does_not_delete_a_uuid_a_live_key_of_this_run_holds(gate, tmp_path):
    """Cell 4: the orphan key and the live record's key hold ONE uuid (the pass of
    cell 3 did exactly that). The orphan key goes, the copper STAYS — otherwise
    the run deletes the item its own record has just bound."""
    gone = _entity("fpga_oscill", cell="fpga_oscill_fpga")
    orphan = _orphan_key(gone, _cell())
    cell = _cell()
    own = _own_key(_clone(), cell)
    reg, commands, known = _reconcile_setup(
        gate, tmp_path,
        {own: _track_entry("u"), orphan: _track_entry("u")},
        live_tracks=[_live_track("u")])

    to_create, to_delete = reg.reconcile(commands, known_anchor_ids=known)

    assert to_create == []
    assert to_delete == []
    assert orphan not in reg.entries
    assert own in reg.entries and reg.entries[own].uuid == "u"


def test_one_uuid_is_named_for_deletion_once(gate, tmp_path, caplog):
    """Cell 4b (Д-1 of the acceptance): the orphan and a MOVING live key hold one
    uuid. The command loop already put it in `to_delete` (the record's item stands
    off the planned place, so the record recreates it) and popped that entry —
    `live_uuids` is built AFTER the loop and cannot see it. Prune must not name the
    uuid a second time: `remove_by_ids` sends one packet, and a duplicate id in it
    may reject the WHOLE packet — nothing deleted while the new copper is drawn."""
    gone = _entity("fpga_oscill", cell="fpga_oscill_fpga")
    orphan = _orphan_key(gone, _cell())
    own = _own_key(_clone(), _cell())
    reg, commands, known = _reconcile_setup(
        gate, tmp_path,
        {own: _track_entry("u"), orphan: _track_entry("u")},
        live_tracks=[_live_track("u", y1=5.0, y2=5.0)])

    with caplog.at_level("INFO"):
        to_create, to_delete = reg.reconcile(commands, known_anchor_ids=known)

    assert to_create == commands                 # the moved record is recreated
    assert to_delete == ["u"]                    # named ONCE, not ['u', 'u']
    assert orphan not in reg.entries
    said = [record.getMessage() for record in caplog.records
            if orphan in record.getMessage()]
    assert said, "the dropped key must be named in the Log"
    assert "already being deleted" in said[0]


def test_prune_still_deletes_the_copper_nobody_holds(gate, tmp_path):
    """Cell 5: an orphan whose item is on the board and held by NOBODY is still
    deleted — the rule frees the key, it does not keep stale copper alive."""
    gone = _entity("fpga_oscill", cell="fpga_oscill_fpga")
    orphan = _orphan_key(gone, _cell())
    cell = _cell()
    own = _own_key(_clone(), cell)
    reg, commands, known = _reconcile_setup(
        gate, tmp_path,
        {own: _track_entry("u"), orphan: _track_entry("o", y1=5.0, y2=5.0)},
        live_tracks=[_live_track("u"), _live_track("o", y1=5.0, y2=5.0)])

    to_create, to_delete = reg.reconcile(commands, known_anchor_ids=known)

    assert to_create == []
    assert to_delete == ["o"]
    assert orphan not in reg.entries


def test_a_known_anchor_filtered_out_is_not_pruned(gate, tmp_path):
    """Cell 6: prune's `known_anchor_ids` protection is untouched — an anchor the
    config still produces is not an orphan just because `--only` left it out."""
    other = _entity("pif_avdd", cell="pif_avdd")
    key_other = make_registry_key(entity_anchor_id(other), "pif_avdd", None, 0)
    cell = _cell()
    own = _own_key(_clone(), cell)
    reg, commands, _known = _reconcile_setup(
        gate, tmp_path,
        {own: _track_entry("u"), key_other: _track_entry("o", y1=5.0, y2=5.0)},
        live_tracks=[_live_track("u"), _live_track("o", y1=5.0, y2=5.0)])
    known = config_anchor_ids(_cfg(entities=[other], clones=[_clone()]))

    to_create, to_delete = reg.reconcile(commands, known_anchor_ids=known)

    assert to_create == [] and to_delete == []
    assert key_other in reg.entries


def test_a_dead_uuid_is_not_named_for_deletion(gate, tmp_path, caplog):
    """Cell 7: 6 of the 25 v103 orphan keys point at uuids the board no longer
    carries. Naming them for deletion puts a board write on a missing id and makes
    the Log claim a deletion that cannot happen — the key alone goes."""
    gone = _entity("fpga_oscill", cell="fpga_oscill_fpga")
    orphan = _orphan_key(gone, _cell())
    cell = _cell()
    own = _own_key(_clone(), cell)
    reg, commands, known = _reconcile_setup(
        gate, tmp_path,
        {own: _track_entry("u"), orphan: _track_entry("dead", y1=5.0, y2=5.0)},
        live_tracks=[_live_track("u")])

    with caplog.at_level("INFO"):
        to_create, to_delete = reg.reconcile(commands, known_anchor_ids=known)

    assert to_create == [] and to_delete == []
    assert orphan not in reg.entries
    said = [record.getMessage() for record in caplog.records
            if orphan in record.getMessage()]
    assert said, "the dropped key must be named in the Log"
    assert not any("deleting" in line for line in said)


# ── the rule itself, and the guard that keeps it ONE truth ──────────────────

def test_anchor_part_is_the_first_part_and_orphans_are_prefix_blind(gate):
    anchors = {"name:X", "net:Y", "thermal:Z"}
    assert anchor_part("net:Y|cell|r|0") == "net:Y"
    assert anchor_part("") == ""
    assert key_is_orphan("net:gone|cell|r|0", anchors)
    assert key_is_orphan("pad:1|cell|r|0", anchors)
    assert key_is_orphan("anchor:IC9:1:0.0000:0.0000|cell|r|0", anchors)
    assert not key_is_orphan("thermal:Z|cell|r|0", anchors)
    assert not key_is_orphan("name:X|cell|r|0", anchors)


def test_a_uuid_a_live_key_also_holds_is_never_an_orphan_uuid(gate):
    """The mirror of the Ч1-of-27.10 defect must not appear here: an orphan may not
    take ownership AWAY from a record that still exists."""
    live = _entity("dac0")
    gone = _entity("fpga_oscill", cell="fpga_oscill_fpga")
    cfg = _cfg(entities=[live])
    shared = make_registry_key(entity_anchor_id(live), "other_cell", None, 0)
    orphan = _orphan_key(gone)
    only = _orphan_key(_entity("gone2", cell="x"))
    via_entries = {shared: SimpleNamespace(uuid="u"),
                   orphan: SimpleNamespace(uuid="u"),
                   only: SimpleNamespace(uuid="only")}

    assert orphan_uuids(via_entries, {}, config_anchor_ids(cfg)) == frozenset({"only"})


def test_every_anchor_kind_the_builders_produce_is_a_config_anchor(gate):
    """THE guard (Denis, 27.10: «если `config_anchor_ids` упустит живой род
    якорей — СТОП»): every kind the PLANNER keys with must be in the set, or a live
    record would look like an orphan and its copper would read as nobody's (while
    prune deletes it too — the same builder feeds `known_anchor_ids`)."""
    clone = _clone()
    ent = _entity("pif_avdd", cell="pif_avdd")
    nt = _net_trace("NT")
    th = _thermal("TH")
    cfg = _cfg(entities=[ent], clones=[clone], net_traces=[nt], thermal=[th])

    anchors = config_anchor_ids(cfg)
    clone_anchor, _cell_name, _key_part = clone_registry_identity(clone, cfg.cells)

    assert clone_anchor in anchors
    assert entity_anchor_id(ent) in anchors
    assert net_trace_anchor_id(nt) in anchors
    assert thermal_anchor_id(th) in anchors

    # A retired record produces nothing (a retired anchor IS an orphan).
    retired = _thermal("OLD", retired=True)
    assert thermal_anchor_id(retired) not in config_anchor_ids(
        _cfg(thermal=[retired]))
