# tests/explode/test_explode_transfer_redraw.py
"""Р3а-4 (plan ``plan_2026_10_05_explode_r2_r3_tab_and_reread.md`` Р3-4) — the
HEADLINE property of the ownership transfer:

    read with transfer -> "Save" -> the redraw of ``net_traces`` AND of the cell
    = 0 new / 0 removed.

Nobody draws a copy of the piece that moved and nobody deletes it: the cell gets
a NEW record whose registry key the registry does not know, so ``reconcile`` says
"create" and the unconditional positional pre-check of Phase 3 skips it (the
copper is already there); the record's remaining pieces are reclaimed through the
RELEASED keys; and a record left with no piece is named rather than dropped.

The redraw is the PRODUCT's own pipeline, mirrored exactly as the existing redraw
cells mirror it (``tests/net/test_net_trace_apply.py``,
``tests/trees/test_entity_tree_redraw_idempotent.py``): the real planners
(``plan_net_traces`` / ``ClonePositionCalculator``), the REAL registry files
through ``reconcile()``, and Phase 2/3's ordering — ``adopt_net_trace_copper`` ->
``adopt_matching_unowned`` -> ``reconcile`` -> the positional pre-check. The
transfer itself is the kernel's own ``apply_transfers``. Only the board is a
stand-in (``tests/fakes/live_board.py``).
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from kicadstamp.config import (
    Cell,
    ClonePlacement,
    Config,
    NetTrace,
    TemplateComponentSlot,
    TemplateTrack,
)
from kicadstamp.config_writer import _read_data, write_config_file
from kicadstamp.domain.geometry import Vector2
from kicadstamp.explode_transfer import NetTraceTransfer, apply_transfers
from kicadstamp.net_trace_planner import adopt_net_trace_copper, plan_net_traces
from kicadstamp.placement.services.clone_position_calculator import (
    ClonePositionCalculator,
)
from kicadstamp.registry import (
    PlacementRegistry,
    TrackRegistry,
    adopt_matching_unowned,
    filter_existing_tracks,
    registry_path_for_config,
    track_registry_path_for_config,
    load_track_registry,
)
from kicadstamp.utils.paths import registry_paths_for_config
from tests.fakes.format3 import stamp_config
from tests.fakes.live_board import FakeLiveBoardAdapter

MM = 1_000_000
NET = "+3V3_VCCIO"
REC = "rec"


@pytest.fixture(autouse=True)
def _no_format3_stamp(monkeypatch):
    """The transfer writes the config through ``upsert_list_entry``, and a
    format-3 write wants the project graph root — absent in a unit test, so the
    stamp is disabled exactly as the project's own unit tests do."""
    import kicadstamp.config_working_set as ws
    monkeypatch.setattr(ws, "is_stamp_disabled", lambda: True)


# ── the board, the cell, the record ─────────────────────────────────────────

def _make_fp():
    """U1 with Role/Cluster/Sheet = FPGA and pad '1' on +3V3_VCCIO — the ONE anchor
    both the cell's clone_placement and the net_traces record hang off."""
    fp = MagicMock()
    fp.ref = "U1"
    fp.position = Vector2.from_xy(65.0 * MM, -65.0 * MM)
    fp.angle_deg = 0.0
    fp.rotation = 0.0

    def _field(field):
        return "FPGA" if field in ("Role", "Cluster", "Sheet") else None
    fp.get_field_value = _field

    pad = MagicMock()
    pad.number = "1"
    pad.net_name = NET
    # The pad sits at the footprint's own centre, so BOTH the clone's anchor and
    # the record's anchor (a role anchor, no pad) land on the same board point and
    # the piece's local geometry maps identically on both sides.
    pad.position = fp.position
    fp.pads = {"1": pad}
    fp.pad = lambda num: fp.pads.get(str(num))
    fp.definition = MagicMock(items=[])
    return fp


def _piece() -> TemplateTrack:
    """The local geometry of the piece that MOVES into the cell — identical on
    both sides (the record's piece and the cell's new record are the same
    copper), which is what lets the cell claim it back by exact geometry."""
    return TemplateTrack(start_along_mm=2.0, start_across_mm=0.0,
                         end_along_mm=3.0, end_across_mm=0.0, width_mm=0.2,
                         layer="F.Cu", net=NET)


def _own_track() -> TemplateTrack:
    """The cell's OWN track — net taken live from the FPGA role's pad."""
    return TemplateTrack(start_along_mm=0.0, start_across_mm=0.0,
                         end_along_mm=1.0, end_across_mm=0.0, width_mm=0.2,
                         layer="F.Cu", net_from_role="FPGA",
                         net_from_role_pad="1")


def _other_track(n: float) -> TemplateTrack:
    """A record piece that does NOT move."""
    return TemplateTrack(start_along_mm=n, start_across_mm=0.0,
                         end_along_mm=n + 1.0, end_across_mm=0.0, width_mm=0.2,
                         layer="F.Cu", net=NET)


def _cell(with_piece: bool) -> Cell:
    tracks = [_own_track()] + ([_piece()] if with_piece else [])
    return Cell(name="dac_buf", uuid="uuid-cell",
                components=[TemplateComponentSlot(role="FPGA", offset_along_mm=0.0,
                                                  offset_across_mm=0.0,
                                                  angle_deg=0.0)],
                tracks=tracks)


def _clone() -> ClonePlacement:
    return ClonePlacement(name="cl1", cluster="FPGA", cell="dac_buf",
                          xy=(0.0, 0.0), anchor_role="FPGA",
                          anchor_sheet="FPGA", anchor_cluster="FPGA",
                          nets={"FPGA": NET})


def _records(tracks):
    """The one net_traces record. Its anchor is the FPGA role's own centre — the
    same board point the cell's clone_placement hangs off, so the piece's local
    geometry maps identically on both sides."""
    return [NetTrace(net="NT", name=REC, uuid="uuid-rec", anchor_role="FPGA",
                     tracks=list(tracks))]


def _cfg(*, with_piece: bool, record_tracks) -> Config:
    return stamp_config(Config(cells={"dac_buf": _cell(with_piece)},
                               clone_placements=[_clone()],
                               net_traces=_records(record_tracks)))


def _write_record_file(path) -> None:
    """The ONE thing the transfer needs a FILE for: the record's raw dict."""
    write_config_file(path, {"net_traces": [
        {"name": REC, "uuid": "uuid-rec", "net": "NT", "anchor_role": "FPGA",
         "tracks": [
             {"start_along_mm": 2.0, "start_across_mm": 0.0, "end_along_mm": 3.0,
              "end_across_mm": 0.0, "width_mm": 0.2, "layer": "F.Cu", "net": NET},
             {"start_along_mm": 5.0, "start_across_mm": 0.0, "end_along_mm": 6.0,
              "end_across_mm": 0.0, "width_mm": 0.2, "layer": "F.Cu", "net": NET},
             {"start_along_mm": 7.0, "start_across_mm": 0.0, "end_along_mm": 8.0,
              "end_across_mm": 0.0, "width_mm": 0.2, "layer": "F.Cu", "net": NET},
         ]}]}, stamp=False)


# ── the mirrored redraw (the pipeline's own pieces, in its own order) ───────

def _planned(adapter, cfg):
    """Every track command of ONE redraw: the cell's (ClonePositionCalculator,
    the planner Phase 1 uses) plus the records' (plan_net_traces)."""
    calc = ClonePositionCalculator(adapter, cfg, {})
    _placed, _vias, cell_tracks = calc.compute_raw_positions(cfg.clone_placements)
    _nt_vias, nt_tracks = plan_net_traces(adapter, list(cfg.net_traces))
    return list(cell_tracks) + list(nt_tracks)


def _draw(adapter, track_reg, cmds):
    """Create the planned commands that are not on the board yet and register
    them — the pipeline's own tail (reconcile -> pre-check -> create), with the
    registry's own ``record_created``.

    ONE ``reconcile`` for the WHOLE run, exactly as the pipeline does it: a
    second call with a SUBSET of the commands would PRUNE the first call's keys
    as "no longer in the config"."""
    live = adapter.get_tracks()
    to_create, _to_delete = track_reg.reconcile(list(cmds), live_items=live)
    for cmd in filter_existing_tracks(to_create, live):
        net = adapter.get_net_by_name(cmd.net_name)
        t = adapter.create_track(cmd.start, cmd.end, cmd.width_mm, net, cmd.layer)
        adapter.create_items([t])
        adapter.live_tracks.append(t)
        track_reg.record_created(cmd, t.uuid)


def _seed(adapter, cfg, via_reg, trk_path):
    """Draw the pre-transfer state the way ONE pipeline run would: the cell's own
    copper AND every record piece, in one reconcile. The records' copper is
    claimed by ``adopt_net_trace_copper`` first, exactly as Phase 2 does; the
    cell's commands are NOT handed to it (they are not net traces)."""
    track_reg = TrackRegistry(adapter, trk_path)
    cell_tracks = list(ClonePositionCalculator(adapter, cfg, {})
                       .compute_raw_positions(cfg.clone_placements)[2])
    nt_tracks = list(plan_net_traces(adapter, list(cfg.net_traces))[1])
    adopt_net_trace_copper(adapter, via_reg, track_reg, [], nt_tracks)
    _draw(adapter, track_reg, cell_tracks + nt_tracks)


def _redraw(adapter, cfg, via_reg, trk_path):
    """The pipeline's track half of ONE redraw, in its own order. Returns
    (created, removed, commands).

    The registry is RE-READ from its file here, exactly as a new run does — the
    release the transfer wrote is in the FILE, so a registry object left over
    from the seeding pass would still hold the released keys and prune them."""
    track_reg = TrackRegistry(adapter, trk_path)
    cmds = _planned(adapter, cfg)
    live = adapter.get_tracks()
    nt_tracks = list(plan_net_traces(adapter, list(cfg.net_traces))[1])
    adopt_net_trace_copper(adapter, via_reg, track_reg, [], nt_tracks)
    adopt_matching_unowned(track_reg, cmds, live_items=live)
    to_create, to_delete = track_reg.reconcile(cmds, live_items=live)
    to_create = filter_existing_tracks(to_create, live)
    return len(to_create), len(to_delete), cmds


def _harness(tmp_path):
    """A fake board, the two REAL registry files and the record's config file."""
    adapter = FakeLiveBoardAdapter(_make_fp())
    cfg_path = tmp_path / "config.sexp"
    _write_record_file(cfg_path)
    via_path = registry_path_for_config(str(cfg_path))
    trk_path = track_registry_path_for_config(str(cfg_path))
    return (adapter, cfg_path, PlacementRegistry(adapter, via_path), trk_path)


def _transfer(cfg_path, cfg_before):
    return apply_transfers(cfg_path, cfg_before,
                           [NetTraceTransfer(REC, "track", 0)],
                           entry_files={REC: str(cfg_path)})


# ── the headline ────────────────────────────────────────────────────────────

def test_the_redraw_before_the_transfer_is_zero_zero(tmp_path):
    """The control: the drawn state is idempotent, so a red is the transfer's
    doing and not the fixture's."""
    adapter, _cfg_path, via_reg, trk_path = _harness(tmp_path)
    cfg = _cfg(with_piece=False, record_tracks=[_piece(), _other_track(5.0),
                                                _other_track(7.0)])
    _seed(adapter, cfg, via_reg, trk_path)
    assert len(adapter.get_tracks()) == 4          # 1 cell track + 3 pieces
    created, removed, _cmds = _redraw(adapter, cfg, via_reg, trk_path)
    assert (created, removed) == (0, 0)


def test_read_with_transfer_then_save_then_redraw_is_zero_zero(tmp_path):
    """Р3-4, the HEADLINE: read with transfer -> "Save" -> the redraw of
    ``net_traces`` AND of the cell = 0 new / 0 removed."""
    adapter, cfg_path, via_reg, trk_path = _harness(tmp_path)
    cfg_before = _cfg(with_piece=False,
                      record_tracks=[_piece(), _other_track(5.0),
                                     _other_track(7.0)])
    _seed(adapter, cfg_before, via_reg, trk_path)
    live_uuids = {t.uuid for t in adapter.get_tracks()}
    assert len(live_uuids) == 4

    assert _transfer(cfg_path, cfg_before) == []      # the piece moved out
    assert len(_read_data(cfg_path)["net_traces"][0]["tracks"]) == 2

    cfg_after = _cfg(with_piece=True, record_tracks=[_other_track(5.0),
                                                    _other_track(7.0)])
    created, removed, _cmds = _redraw(adapter, cfg_after, via_reg, trk_path)
    assert (created, removed) == (0, 0)
    # Nobody re-created the copper and nobody deleted it: the board is untouched.
    assert {t.uuid for t in adapter.get_tracks()} == live_uuids


def test_a_piece_still_owned_by_its_record_is_not_drawn_twice(tmp_path):
    """Р3-4: the state BEFORE the release — the cell already describes the piece
    while the RECORD's key still owns that copper. The adoption cannot take it (a
    live item owned under another key is never stolen), so the POSITIONAL
    PRE-CHECK is the ONE thing that stops a second copy of the piece.

    The headline below makes the release-then-adopt path the first line of defence
    instead; the two protections are the pair this module pins."""
    adapter, cfg_path, via_reg, trk_path = _harness(tmp_path)
    cfg_before = _cfg(with_piece=False,
                      record_tracks=[_piece(), _other_track(5.0),
                                     _other_track(7.0)])
    _seed(adapter, cfg_before, via_reg, trk_path)

    cfg_mixed = _cfg(with_piece=True, record_tracks=[_piece(), _other_track(5.0),
                                                     _other_track(7.0)])
    created, removed, _cmds = _redraw(adapter, cfg_mixed, via_reg, trk_path)
    assert (created, removed) == (0, 0)
    assert len(adapter.get_tracks()) == 4          # no literal duplicate


def test_reset_without_save_still_redraws_zero_zero(tmp_path):
    """Р3-4: "✓ -> reset the working set -> redraw = 0 new / 0 removed".

    The reset drops the staged config, so the record still HAS the piece while the
    registry's keys for it are already released (the release is a file write, not a
    staged one). The geometry reclaim is what makes this safe — renumbering the
    keys instead of releasing them is what the guard is here to catch."""
    adapter, cfg_path, via_reg, trk_path = _harness(tmp_path)
    cfg = _cfg(with_piece=False, record_tracks=[_piece(), _other_track(5.0),
                                               _other_track(7.0)])
    _seed(adapter, cfg, via_reg, trk_path)
    before_bytes = cfg_path.read_bytes()

    assert _transfer(cfg_path, cfg) == []
    cfg_path.write_bytes(before_bytes)               # the reset: no Save

    created, removed, _cmds = _redraw(adapter, cfg, via_reg, trk_path)
    assert (created, removed) == (0, 0)
    assert len(adapter.get_tracks()) == 4


def test_the_piece_moves_to_the_cells_key_and_leaves_the_records(tmp_path):
    """Р3-4: after the transfer + redraw the moved piece is owned by the CELL's
    key (its uuid is the live copper's), and no key of the record claims it."""
    adapter, cfg_path, via_reg, trk_path = _harness(tmp_path)
    cfg_before = _cfg(with_piece=False,
                      record_tracks=[_piece(), _other_track(5.0),
                                     _other_track(7.0)])
    _seed(adapter, cfg_before, via_reg, trk_path)
    piece_uuid = next(t.uuid for t in adapter.get_tracks()
                      if t.start.x == (65.0 + 2.0) * MM)

    assert _transfer(cfg_path, cfg_before) == []
    cfg_after = _cfg(with_piece=True, record_tracks=[_other_track(5.0),
                                                    _other_track(7.0)])
    assert _redraw(adapter, cfg_after, via_reg, trk_path)[:2] == (0, 0)

    entries = load_track_registry(trk_path)
    owning = [key for key, entry in entries.items() if entry.uuid == piece_uuid]
    assert len(owning) == 1
    assert "uuid-cell" in owning[0], owning                   # the CELL's key
    assert not any(key.startswith("net:") for key in owning)   # never a record's


def test_the_record_reclaims_its_remaining_pieces(tmp_path):
    """Р3-4: the pieces that STAYED are re-owned by the record under fresh numbers
    — the released keys are why the redraw can reclaim them by geometry."""
    adapter, cfg_path, via_reg, trk_path = _harness(tmp_path)
    cfg_before = _cfg(with_piece=False,
                      record_tracks=[_piece(), _other_track(5.0),
                                     _other_track(7.0)])
    _seed(adapter, cfg_before, via_reg, trk_path)
    assert len(load_track_registry(trk_path)) == 4      # 1 cell + 3 pieces

    assert _transfer(cfg_path, cfg_before) == []
    assert len(load_track_registry(trk_path)) == 1      # the cell's own key only

    cfg_after = _cfg(with_piece=True,
                     record_tracks=[_other_track(5.0), _other_track(7.0)])
    assert _redraw(adapter, cfg_after, via_reg, trk_path)[:2] == (0, 0)

    entries = load_track_registry(trk_path)
    # Two pieces re-owned by the record (fresh numbers) + the cell's TWO tracks
    # (its own and the one that moved in) — one key each, nobody shares a uuid.
    assert len([k for k in entries if k.startswith("net:")]) == 2
    assert len([k for k in entries if "uuid-cell" in k]) == 2
    assert len({e.uuid for e in entries.values()}) == len(entries)


def test_the_copy_of_the_record_is_released_with_the_template(tmp_path):
    """Р3-4: a ``tree_instances`` COPY of the record is released with the template
    — every key of both goes, so the copy's own redraw cannot re-claim the moved
    piece (the copy's redraw itself needs a second board instance and is covered by
    the kernel cell ``test_every_key_of_the_record_and_its_copies_is_released``)."""
    adapter, cfg_path, via_reg, trk_path = _harness(tmp_path)
    cfg = _cfg(with_piece=False, record_tracks=[_piece(), _other_track(5.0),
                                               _other_track(7.0)])
    cfg.net_traces.append(NetTrace(net="NT", name=REC, uuid="uuid-copy",
                                   anchor_role="FPGA",
                                   tracks=[_piece(), _other_track(5.0),
                                           _other_track(7.0)]))
    _seed(adapter, cfg, via_reg, trk_path)

    lines = _transfer(cfg_path, cfg)
    assert not any("refused" in ln for ln in lines)          # nothing refused
    assert any("tree_instances" in ln for ln in lines)       # the copy is named
    # BOTH the template's and the copy's keys are gone; the cell's key stays.
    assert [k for k in load_track_registry(trk_path) if k.startswith("net:")] == []
    assert len(load_track_registry(trk_path)) == 1
