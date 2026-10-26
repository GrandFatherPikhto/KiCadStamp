# tests/net/test_internode_cell_copper.py
"""Cells for `kicadstamp/internode_cell_copper.py` — cell copper given back to the cells.

Plan: plan_2026_10_23_internode_without_cell_copper. The defect it fixes was measured live on
v103 (`ch0_dac_buf`, 22.10): the via and the B.Cu stretch of a "spoke" CELL sat between the same
two nodes as the link, so «Select inter-node copper» selected them and the re-read recorded them
again — one piece of copper, two owners.

What is pinned here, property by property:

  * OWNERSHIP is the registry key alone (`place_check=False`), so a cell that was never redrawn
    (its copper is not where the frame would put it) still gets its copper BACK — and the strict
    door, asked the same question, refuses it (cell 5 of the plan);
  * a cell record the registry has no key for is COUNTED and its copper is not given back
    (plan §2.6), and the report line says so;
  * `config_path=None` is "no profile": nothing is subtracted and the warning says it — never
    silence (plan §2.3);
  * ONE read of the registry files and ONE read of the board per run, however many nodes the
    tree has (plan §2.1);
  * `cut_cell_copper` keeps `pads` (the record's SIGNATURE) and filters `track_pads`/`via_pads`
    with their items; a piece that is cell copper ENTIRELY comes back empty and is counted by the
    caller instead of being selected or written (plan §2.5);
  * the subtraction runs in `_classify_area` — the ONE step both «Select» and «Reread» go through
    (T5-1), which is what keeps the two doors agreeing.

The registry/board builders are the ones `tests/selection/test_registry_pair_frame.py` already
uses (ONE way to write a registry key in this tree) — imported, not copied.
"""
from types import SimpleNamespace

import pytest

from kicadstamp.config import format_version
from kicadstamp.constants import CELL_LEVEL_ROLE_PLACEHOLDER
from kicadstamp.internode_capture import _classify_area
from kicadstamp.internode_cell_copper import (
    CellCopperMap,
    cut_cell_copper,
    report_line,
    tree_cell_copper,
)
from kicadstamp.domain.board import Track, Via
from kicadstamp.domain.geometry import BoardLayer, Vector2
from kicadstamp.internode_copper import CopperUnit
from tests.selection.test_registry_pair_frame import (
    CLUSTER,
    _Adapter,
    _Cfg,
    _cell,
    _live_track,
    _live_via,
    _own_key,
    _registries,
    _track_entry,
    _via_entry,
)


@pytest.fixture(autouse=True)
def gate(monkeypatch):
    """Format 3 — the live product format; every key below is a uuid key."""
    monkeypatch.setattr(format_version, "CURRENT_FORMAT", 3)


def _tree(node_count=1):
    """A tree whose nodes are the entities `_Cfg` carries (all `cell="dac_buf"`)."""
    return SimpleNamespace(
        name="t", nodes=[SimpleNamespace(kind=None, ref=f"dac{i}", children=[])
                         for i in range(node_count)])


def _many_entity_cfg(count):
    """`count` entity nodes, each with its OWN sheet, all naming the SAME cell — the map must ask
    the registry `count` times but read the board ONCE."""
    return SimpleNamespace(
        cells={**_Cfg(_cell()).cells},
        entities=[SimpleNamespace(name=f"dac{i}", uuid=f"uuid-ent-{i}", cell="dac_buf",
                                  cluster=CLUSTER, sheet=f"S{i}", retired=False)
                  for i in range(count)],
        clone_placements=[])


def _unit(tracks=(), vias=(), pads=(("FPGA", "1"), ("DAC", "3"))):
    from kicadstamp.internode_copper import PadRef

    pad_refs = tuple(PadRef(ref=r, pad=p) for r, p in pads)
    return CopperUnit(tracks=list(tracks), vias=list(vias), pads=pad_refs,
                      track_pads=[() for _ in tracks], via_pads=[() for _ in vias])


# ── cell 5: ownership is the KEY, even away from the record's place ─────────

def test_a_cell_away_from_its_planned_place_still_gets_its_copper_back(tmp_path):
    """The instance was never redrawn, so the frame would refuse the pair — the KEY does not.
    `place_check=False` + `frame_check=False` is exactly what the new path asks for."""
    from kicadstamp.absent_copper_prune import registry_record_copper_map

    away = (30.0, 30.0)                    # NOT where the record puts via 0 (10, 10)
    cell = _cell()
    _registries(tmp_path, {_own_key(cell, 0): _via_entry("uv", *away)}, {})
    adapter = _adapter_with(vias=[_live_via("uv", *away)])
    cfg = _Cfg(cell)

    select_door = registry_record_copper_map(
        adapter, str(tmp_path / "config.sexp"), cfg, "dac_buf", CLUSTER, None,
        place_check=False, frame_check=False)
    strict_door = registry_record_copper_map(
        adapter, str(tmp_path / "config.sexp"), cfg, "dac_buf", CLUSTER, None,
        place_check=True, frame_check=True)

    assert select_door.by_record == \
        {("via", CELL_LEVEL_ROLE_PLACEHOLDER, 0): "uv"}
    assert select_door.not_rigid is None and select_door.frame_residual_mm is None
    # The strict door is unchanged and still refuses a pair the frame cannot place.
    assert strict_door.by_record == {} and strict_door.disagreed == 1
    assert strict_door.not_rigid is False


def test_the_select_door_keeps_a_pair_the_place_cannot_confirm(tmp_path):
    """«Select cell» (the rule this fix borrows): with a RIGID frame that disagrees, the pair is
    KEPT and only counted — selecting is harmless. The STRICT door, asked the same question,
    refuses it. (A mutation that makes the select door refuse reddens this cell.)"""
    from kicadstamp.absent_copper_prune import registry_record_copper_map

    away = (30.0, 30.0)
    cell = _cell()
    _registries(tmp_path, {_own_key(cell, 0): _via_entry("uv", *away)}, {})
    adapter = _adapter_with(vias=[_live_via("uv", *away)])
    cfg = _Cfg(cell)

    lenient = registry_record_copper_map(
        adapter, str(tmp_path / "config.sexp"), cfg, "dac_buf", CLUSTER, None,
        place_check=False, frame_check=True)

    assert lenient.by_record == {("via", CELL_LEVEL_ROLE_PLACEHOLDER, 0): "uv"}
    assert lenient.disagreed == 1 and lenient.not_rigid is False


def test_the_tree_map_asks_by_key_not_by_place(tmp_path):
    """The map the TREE builds must ask the SELECT door: a cell that was never redrawn owns its
    copper by KEY, even though the frame cannot place the pair (cell 5 of the plan, through the
    real `tree_cell_copper`)."""
    away = (30.0, 30.0)
    cell = _cell()
    _registries(tmp_path, {_own_key(cell, 0): _via_entry("uv", *away)}, {})
    adapter = _adapter_with(vias=[_live_via("uv", *away)])

    mapping = tree_cell_copper(adapter, _Cfg(cell), str(tmp_path / "config.sexp"), _tree())

    assert mapping.uuids == {"uv"}
    assert mapping.vias == 1


def _adapter_with(*, vias=(), tracks=()):
    """The selection fixture's adapter, with the FPGA footprint at `FPGA_LIVE`."""
    from tests.selection.test_registry_pair_frame import _Fp, FPGA_LIVE

    return _Adapter(footprints=[_Fp("IC9", "FPGA", CLUSTER, *FPGA_LIVE)],
                    vias=list(vias), tracks=list(tracks))


# ── the map of one tree ────────────────────────────────────────────────────

def test_the_tree_map_collects_the_cells_own_via_and_track(tmp_path):
    """A redrawn cell: its via 0 and its track 0 are uuids of the board, and they land in the
    map (counts by kind and per cell), so both doors can give them back."""
    from tests.selection.test_registry_pair_frame import FPGA_LIVE

    cell = _cell()
    _registries(tmp_path,
                {_own_key(cell, 0): _via_entry("uv", *FPGA_LIVE)},
                {_own_key(cell, 0): _track_entry("ut", *FPGA_LIVE,
                                                 FPGA_LIVE[0] + 1.0, FPGA_LIVE[1])})
    adapter = _adapter_with(vias=[_live_via("uv", *FPGA_LIVE)],
                            tracks=[_live_track("ut", *FPGA_LIVE,
                                                FPGA_LIVE[0] + 1.0, FPGA_LIVE[1])])

    mapping = tree_cell_copper(adapter, _Cfg(cell), str(tmp_path / "config.sexp"), _tree())

    assert mapping.uuids == {"uv", "ut"}
    assert (mapping.vias, mapping.tracks) == (1, 1)
    assert mapping.by_cell == {"dac_buf": 2}
    assert mapping.without_registry == 0


def test_a_cell_record_without_a_registry_key_is_counted_and_not_given_back(tmp_path):
    """Plan §2.6: the cell was never redrawn, so the registry knows nothing about its copper.
    Nothing is subtracted — and the report line NAMES the count instead of staying silent."""
    cell = _cell()
    _registries(tmp_path, {}, {})
    adapter = _adapter_with()

    mapping = tree_cell_copper(adapter, _Cfg(cell), str(tmp_path / "config.sexp"), _tree())

    assert mapping.uuids == set()
    assert mapping.without_registry == 2          # one via + one track, both keyless
    line = report_line(mapping, 0)
    assert "2 cell record(s) have no registry key" in line


def test_no_profile_means_no_subtraction_and_a_warning(tmp_path):
    """Plan §2.3: `config_path=None` is an explicit "no profile" — nothing is given back and the
    warning says so. Never a silent "behaves as before"."""
    mapping = tree_cell_copper(_adapter_with(), _Cfg(_cell()), None, _tree())

    assert mapping.uuids == set()
    assert len(mapping.warnings) == 1
    assert "cell copper is NOT subtracted" in mapping.warnings[0]


def test_one_board_read_per_run_however_many_nodes_the_tree_has(tmp_path):
    """Plan §2.1: the map is asked per node, and each ask used to read the whole board and both
    registry files. The run-scoped seam keeps that at ONE reading, whatever the node count."""
    calls = {"footprint": 0, "via": 0, "track": 0}

    class _Counting(_Adapter):
        def get_footprints(self):
            calls["footprint"] += 1
            return super().get_footprints()

        def get_vias(self):
            calls["via"] += 1
            return super().get_vias()

        def get_tracks(self):
            calls["track"] += 1
            return super().get_tracks()

    from tests.selection.test_registry_pair_frame import _Fp, FPGA_LIVE

    adapter = _Counting(footprints=[_Fp("IC9", "FPGA", CLUSTER, *FPGA_LIVE)], vias=[], tracks=[])
    _registries(tmp_path, {}, {})
    cfg = _many_entity_cfg(5)

    mapping = tree_cell_copper(adapter, cfg, str(tmp_path / "config.sexp"), _tree(5))

    assert calls == {"footprint": 1, "via": 1, "track": 1}
    # All five nodes were asked (same cell, five instances): the map saw five instances' keys.
    assert mapping.without_registry == 10          # 2 records x 5 instances


# ── cut_cell_copper: the piece loses the copper the cell owns ──────────────

def test_a_bridge_loses_the_cells_via_and_bcu_track_and_keeps_its_pads():
    """Plan cells 1 and 4: the F.Cu stretch stays, the cell's via and B.Cu go; `pads` — the
    record's signature — is untouched, so the re-read still matches the old record and reports
    `updated` (its geometry changed) instead of adding a second one.

    The comparison is imported from its new home (`internode_nodes.same_geometry`) since Ч1 of
    plan_2026_10_26_reread_updated_noise moved it next to `_items_from_unit`, which writes the
    numbers it compares; `internode_capture` now only calls it. The claim of THIS cell is
    unchanged: a lost segment is a change (`updated`), not a re-ordering."""
    from kicadstamp.internode_nodes import same_geometry as _same_geometry
    from kicadstamp.config import NetTrace

    fcu = Track(uuid="u-fcu", net_name="N", start=Vector2.from_xy_mm(0.0, 0.0),
                end=Vector2.from_xy_mm(5.0, 0.0), width_mm=0.25,
                layer=BoardLayer.BL_F_Cu)
    via = Via(uuid="u-cell-via", net_name="N", position=Vector2.from_xy_mm(5.0, 0.0),
              drill_mm=0.3, diameter_mm=0.6)
    bcu = Track(uuid="u-cell-bcu", net_name="N", start=Vector2.from_xy_mm(5.0, 0.0),
                end=Vector2.from_xy_mm(9.0, 0.0), width_mm=0.25,
                layer=BoardLayer.BL_B_Cu)
    unit = _unit(tracks=[fcu, bcu], vias=[via])

    trimmed, given = cut_cell_copper(unit, {"u-cell-via", "u-cell-bcu"})

    assert given == 2
    assert [t.uuid for t in trimmed.tracks] == ["u-fcu"]
    assert trimmed.vias == []
    assert trimmed.pads == unit.pads                     # the signature never changes
    assert trimmed.track_pads == [()] and trimmed.via_pads == []

    old = NetTrace(net="N", anchor_role="FPGA", name="bridge", pads=sorted(
        f"{p.ref}.{p.pad}" for p in unit.pads),
        tracks=[SimpleNamespace(start_along_mm=0.0, start_across_mm=0.0,
                                end_along_mm=9.0, end_across_mm=0.0, width_mm=0.25,
                                layer="F.Cu")], vias=[])
    new = NetTrace(net="N", anchor_role="FPGA", name="bridge", pads=old.pads,
                   tracks=[SimpleNamespace(start_along_mm=0.0, start_across_mm=0.0,
                                           end_along_mm=5.0, end_across_mm=0.0,
                                           width_mm=0.25, layer="F.Cu")], vias=[])
    assert _same_geometry(old, new) is False             # -> "updated", not "unchanged"


def test_a_piece_that_is_cell_copper_entirely_comes_back_empty():
    """Plan cell 3: there is nothing inter-node left in it, so the caller counts it as a whole
    piece and selects/writes nothing (the object is not passed on as an empty unit)."""
    via = Via(uuid="u-cell-via", net_name="N", position=Vector2.from_xy_mm(1.0, 1.0),
              drill_mm=0.3, diameter_mm=0.6)
    unit = _unit(vias=[via])

    trimmed, given = cut_cell_copper(unit, {"u-cell-via"})

    assert given == 1 and not trimmed.tracks and not trimmed.vias


def test_a_unit_with_nothing_owned_is_returned_as_the_same_object():
    """No copy, no churn: the classifier's own unit goes on unchanged."""
    unit = _unit(tracks=[Track(uuid="u1", net_name="N", start=Vector2.from_xy_mm(0.0, 0.0),
                               end=Vector2.from_xy_mm(1.0, 0.0), width_mm=0.25,
                               layer=BoardLayer.BL_F_Cu)])

    trimmed, given = cut_cell_copper(unit, {"somebody-elses-uuid"})

    assert trimmed is unit and given == 0


# ── the step lives in the ONE classifier both doors use ────────────────────

def test_the_classifier_is_where_the_subtraction_happens(tmp_path):
    """Both «Select inter-node copper» and the re-read go through `_classify_area` (T5-1), so
    the subtraction is done THERE — a run with no profile still carries the map slot and the
    warning, which is how a caller can tell "asked and found nothing" from "never asked"."""
    from tests.net.test_internode_capture_modules import _Board, _fpga_channel_fps, _cfg_fpga, \
        _tree_fpga, _bridge_fpga_to_dac

    fps, pads = _fpga_channel_fps(1)
    cfg = _cfg_fpga(1)
    board = _Board(fps, pads)

    classification = _classify_area(
        board, cfg, _tree_fpga(cfg), config_path=None,
        area_items=_bridge_fpga_to_dac(0), area_footprints=fps,
        sheet_names={"fpga": "FPGA", "ch0": "Channel_0", "dac0": "DAC"})

    assert isinstance(classification.cell_copper, CellCopperMap)
    assert classification.subtracted == 0 and classification.cell_only == 0
    assert any("cell copper is NOT subtracted" in w for w in classification.warnings)


def test_an_entry_that_duplicates_cell_copper_is_named_not_called_missing(
        tmp_path, monkeypatch):
    """Д-2 (acceptance 23.10): the piece is still on the board and its pads belong to a stored
    `net_traces:` entry — so the re-read must NOT say "the copper is no longer on the board".
    It names the entry as a duplicate of cell copper instead, and the decision stays with the
    human. Mutation 'the duplicate goes to `missing`' turns this red."""
    from kicadstamp.config import format_version, NetTrace
    from kicadstamp.internode_capture import plan_internode_reread
    from tests.gui.docks.test_trees_dock_internode_reread import _cell_copper_root, _board
    from kicadstamp.config.loader import load_config

    monkeypatch.setattr(format_version, "CURRENT_FORMAT", 2)
    root = _cell_copper_root(tmp_path)
    cfg, _ctx = load_config(str(root))
    tree = next(t for t in cfg.trees if t.name == "t")
    board, fps, items = _board()
    board.get_tracks.return_value = list(items)
    board.get_vias.return_value = []

    import kicadstamp.internode_cell_copper as module

    monkeypatch.setattr(module, "tree_cell_copper",
                        lambda *a, **k: CellCopperMap(uuids={"t1"}, tracks=1,
                                                      by_cell={"c": 1}))

    plan = plan_internode_reread(board, cfg, tree, config_path=str(root),
                                 area_items=list(items), area_footprints=fps,
                                 sheet_names={})

    assert isinstance(cfg.net_traces[0], NetTrace)      # the fixture really carries an entry
    assert plan.missing == []                           # nothing left the board
    assert plan.cell_duplicates == ["dup__a__b"]
    assert any("duplicates cell copper" in w for w in plan.warnings), plan.warnings


def test_a_piece_that_is_cell_copper_entirely_is_counted_and_dropped(
        tmp_path, monkeypatch):
    """Plan §2.5 + cell 3: the unit loses ALL its copper, so it is neither selected nor written —
    it is counted as a whole piece, and the report line names that count. The map is stubbed
    because the ownership itself has its own cells above; what is pinned here is the CLASSIFIER's
    handling of the result."""
    from tests.net.test_internode_capture_modules import (
        _Board, _bridge_fpga_to_dac, _cfg_fpga, _fpga_channel_fps, _tree_fpga)

    fps, pads = _fpga_channel_fps(1)
    cfg = _cfg_fpga(1)
    bridge = _bridge_fpga_to_dac(0)
    owned = {item.uuid for item in bridge}

    import kicadstamp.internode_cell_copper as module

    monkeypatch.setattr(module, "tree_cell_copper",
                        lambda *a, **k: CellCopperMap(uuids=set(owned), by_cell={"c": len(owned)},
                                                      tracks=2, vias=1))

    classification = _classify_area(
        _Board(fps, pads), cfg, _tree_fpga(cfg), config_path=str(tmp_path / "config.sexp"),
        area_items=bridge, area_footprints=fps,
        sheet_names={"fpga": "FPGA", "ch0": "Channel_0", "dac0": "DAC"})

    assert classification.units == []
    assert classification.cell_only == 1
    assert any("1 piece(s) are cell copper entirely" in w
               for w in classification.warnings), classification.warnings
