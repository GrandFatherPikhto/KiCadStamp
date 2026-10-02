# tests/domain/test_board_dedupe.py
"""Cells for kicadstamp/board_dedupe.py — the IPC-free core of the copper
duplicate cleanup (plan_2026_10_01_dedupe_into_kicadstamp, К1/§2.1).

Property table, not a pile of look-alike cases (rule 35). What the grouping key
is made of, and what it deliberately is NOT:

  | field            | via key | track key | effect of a difference          |
  |------------------|---------|-----------|---------------------------------|
  | net              | yes     | yes       | separate groups                 |
  | layer            | n/a     | yes       | separate groups                 |
  | position         | yes     | yes       | within tolerance -> one group   |
  | drill/diameter   | NO      | n/a       | same group + report warning     |
  | track end order  | n/a     | unordered | A->B and B->A are one group     |

Every cell names a PLAN number (rule 37) in its docstring; the function name
describes the property and carries no plan number.
"""
import logging

import pytest

from kicadstamp.board_dedupe import (ACTION_LOGGER_NAME, apply_dedupe,
                                     apply_summary, find_track_duplicate_groups,
                                     find_via_duplicate_groups, format_report,
                                     scan_copper_duplicates)
from kicadstamp.domain.board import Track, Via
from kicadstamp.domain.geometry import BoardLayer, Vector2
from kicadstamp.utils.units import MM


def _mm(value: float) -> int:
    """Exact board units for a millimetre literal (round, so 1.01 mm is
    1_010_000 nm and not 1_009_999)."""
    return int(round(value * MM))


def _via(uuid: str, net, x_mm: float, y_mm: float,
         drill: float = 0.3, diameter: float = 0.6) -> Via:
    return Via(uuid=uuid, position=Vector2(_mm(x_mm), _mm(y_mm)),
               net_name=net, drill_mm=drill, diameter_mm=diameter)


def _track(uuid: str, net, layer: BoardLayer,
           start_mm, end_mm, width: float = 0.25) -> Track:
    return Track(uuid=uuid,
                 start=Vector2(_mm(start_mm[0]), _mm(start_mm[1])),
                 end=Vector2(_mm(end_mm[0]), _mm(end_mm[1])),
                 net_name=net, width_mm=width, layer=layer)


class _SpyAdapter:
    """Records every remove_by_id and reads back through the same seam the
    real adapter exposes; `refuse` makes a chosen uuid look already gone."""

    def __init__(self, vias=(), tracks=(), refuse=()):
        self._vias = list(vias)
        self._tracks = list(tracks)
        self.refused = set(refuse)
        self.refresh_count = 0
        self.removed: list[str] = []

    def refresh_board(self):
        self.refresh_count += 1

    def get_vias(self):
        return list(self._vias)

    def get_tracks(self):
        return list(self._tracks)

    def remove_by_id(self, uuid_str: str) -> bool:
        self.removed.append(uuid_str)
        return uuid_str not in self.refused


# ── grouping key: what separates groups ─────────────────────────────────────

def test_vias_on_different_nets_never_merge():
    """С5 (plan_2026_10_01_dedupe_into_kicadstamp §2.1) — the net is part of the
    key: two vias at the SAME spot on different nets are two nets' copper, not
    duplicates."""
    vias = [_via("a", "GND", 1.0, 2.0), _via("b", "VCC", 1.0, 2.0)]
    assert find_via_duplicate_groups(vias) == []


def test_tracks_on_different_layers_never_merge():
    """С5 (§2.1) — the track layer is part of the key: an F.Cu and a B.Cu track
    with the same net and the same endpoints are two real tracks."""
    tracks = [_track("a", "SIG", BoardLayer.BL_F_Cu, (1.0, 1.0), (2.0, 2.0)),
              _track("b", "SIG", BoardLayer.BL_B_Cu, (1.0, 1.0), (2.0, 2.0))]
    assert find_track_duplicate_groups(tracks) == []


def test_track_endpoints_are_an_unordered_pair():
    """С6 (§2.1) — A->B and B->A are the SAME physical track: one group of two,
    with the first-returned member kept (order of the pair does not matter)."""
    tracks = [_track("a", "SIG", BoardLayer.BL_F_Cu, (1.0, 1.0), (2.0, 2.0)),
              _track("b", "SIG", BoardLayer.BL_F_Cu, (2.0, 2.0), (1.0, 1.0))]
    groups = find_track_duplicate_groups(tracks)
    assert len(groups) == 1
    assert [t.uuid for t in groups[0]] == ["a", "b"]


@pytest.mark.parametrize("offset_nm, grouped", [
    (0, True),        # identical
    (9_900, True),    # just inside
    (10_000, True),   # EXACTLY POSITION_TOLERANCE_MM (0.01 mm == 10000 nm)
    (10_001, False),  # just outside
])
def test_via_position_tolerance_boundary_is_exact(offset_nm, grouped):
    """С7 (§2.1) — the tolerance is compared in board units, so the exact
    boundary is meaningful: 10000 nm apart is ONE group, 10001 is not. With a
    float mm comparison 1.01-1.0 == 0.010000000000000009 > 0.01 and the exact
    case would have been silently split."""
    vias = [_via("a", "GND", 1.0, 2.0),
            Via(uuid="b", position=Vector2(_mm(1.0) + offset_nm, _mm(2.0)),
                net_name="GND", drill_mm=0.3, diameter_mm=0.6)]
    assert (len(find_via_duplicate_groups(vias)) == 1) is grouped


@pytest.mark.parametrize("copies", [2, 4])
def test_any_number_of_copies_forms_exactly_one_group(copies):
    """С8 (§2.1) — x2 and x4 both collapse to ONE group; the extra count is
    what gets deleted, never a second group."""
    vias = [_via(f"v{i}", "GND", 1.0, 2.0) for i in range(copies)]
    groups = find_via_duplicate_groups(vias)
    assert len(groups) == 1
    assert len(groups[0]) == copies


def test_via_drill_mismatch_stays_in_the_group_and_warns():
    """С9 (§2.1) — a different drill/diameter is NOT part of the key (the same
    rule PlacementRegistry._live_matches uses), so the vias stay one group, and
    the mismatch is surfaced in the report instead of splitting the group."""
    vias = [_via("a", "GND", 1.0, 2.0, drill=0.3, diameter=0.6),
            _via("b", "GND", 1.0, 2.0, drill=0.4, diameter=0.8)]
    groups = find_via_duplicate_groups(vias)
    assert len(groups) == 1
    assert "[warning] drill/diameter differ within this group" in format_report(
        groups, [])


def test_empty_board_has_no_groups():
    """С10 (§2.1) — nothing on the board is not a duplicate group."""
    assert find_via_duplicate_groups([]) == []
    assert find_track_duplicate_groups([]) == []
    assert format_report([], []) == "No copper duplicates found."


# ── the kept member ─────────────────────────────────────────────────────────

def test_kept_member_is_the_first_returned_by_the_adapter():
    """С11 (§2.1) — the survivor is DETERMINISTIC: the first member in the
    adapter's own order. Reversing the input reverses the survivor, which is
    what makes the "which one stays" cell meaningful and not a coincidence."""
    vias = [_via("b", "GND", 1.0, 2.0), _via("a", "GND", 1.0, 2.0)]
    assert find_via_duplicate_groups(vias)[0][0].uuid == "b"

    adapter = _SpyAdapter(vias=vias)
    apply_dedupe(adapter, find_via_duplicate_groups(vias), [])
    assert adapter.removed == ["a"]      # "b" (first) survives


# ── the report is the ONE shared text ───────────────────────────────────────

def test_report_text_is_stable_and_complete():
    """С12 (§2.1/§2.3) — the canonical report is pinned byte for byte: the CLI
    prints it and the GUI's "Копировать" must put the SAME text on the
    clipboard, so any change here is a deliberate change to both surfaces."""
    vias = [_via("v1", "GND", 1.0, 2.0, drill=0.3, diameter=0.6),
            _via("v2", "GND", 1.0, 2.0, drill=0.4, diameter=0.8),
            _via("v3", "VCC", 5.0, 6.0), _via("v4", "VCC", 5.0, 6.0)]
    tracks = [_track("t1", "SIG", BoardLayer.BL_F_Cu, (1.0, 1.0), (2.0, 2.0)),
              _track("t2", "SIG", BoardLayer.BL_F_Cu, (1.0, 1.0), (2.0, 2.0))]
    expected = "\n".join([
        "Duplicate via groups: 2",
        "  via net='GND' @ (1.0000, 2.0000) mm - 2 copies",
        "    [warning] drill/diameter differ within this group: "
        "[(0.3, 0.6), (0.4, 0.8)]",
        "    keep   v1",
        "    delete v2",
        "  via net='VCC' @ (5.0000, 6.0000) mm - 2 copies",
        "    keep   v3",
        "    delete v4",
        "Duplicate track groups: 1",
        "  track net='SIG' layer=F.Cu @ (1.0000,1.0000) -> (2.0000,2.0000) mm "
        "- 2 copies",
        "    keep   t1",
        "    delete t2",
        "Total: 3 extra duplicate item(s); one copy per group is kept.",
    ])
    assert format_report(find_via_duplicate_groups(vias),
                         find_track_duplicate_groups(tracks)) == expected


def test_report_names_inner_copper_layers_truthfully():
    """С13 (§2.1) — an inner-layer track is named 'In1.Cu', never collapsed to
    'F.Cu'. The old tool's binary ternary did collapse it; a 4-layer board with
    a duplicate on a plane would have been reported on the wrong layer."""
    tracks = [_track("t1", "GND", BoardLayer.BL_In1_Cu, (1.0, 1.0), (2.0, 2.0)),
              _track("t2", "GND", BoardLayer.BL_In1_Cu, (1.0, 1.0), (2.0, 2.0))]
    report = format_report([], find_track_duplicate_groups(tracks))
    assert "layer=In1.Cu" in report
    assert "layer=F.Cu" not in report


# ── removal + journal ───────────────────────────────────────────────────────

def test_apply_removes_only_the_extras_and_journals_each(caplog):
    """С14 (§2.4) — remove_by_id is called for the extras ONLY (one survivor
    per group), and each deletion writes one audit line naming the UUID removed
    AND the UUID kept, so the next investigation can read what happened."""
    vias = [_via("v1", "GND", 1.0, 2.0), _via("v2", "GND", 1.0, 2.0),
            _via("v3", "GND", 1.0, 2.0), _via("v4", "GND", 1.0, 2.0)]
    tracks = [_track("t1", "SIG", BoardLayer.BL_F_Cu, (0.0, 0.0), (1.0, 1.0)),
              _track("t2", "SIG", BoardLayer.BL_F_Cu, (0.0, 0.0), (1.0, 1.0))]
    adapter = _SpyAdapter(vias=vias, tracks=tracks)

    with caplog.at_level(logging.INFO, logger=ACTION_LOGGER_NAME):
        removed = apply_dedupe(adapter,
                               find_via_duplicate_groups(vias),
                               find_track_duplicate_groups(tracks))

    # 4 vias -> 3 extras; 2 tracks -> 1 extra.
    assert removed == 4
    assert adapter.removed == ["v2", "v3", "v4", "t2"]
    lines = [r.getMessage() for r in caplog.records
             if r.name == ACTION_LOGGER_NAME]
    assert len(lines) == 4
    assert "removed via uuid=v2 net='GND'" in lines[0]
    assert "kept=v1" in lines[0]
    assert "removed track uuid=t2" in lines[3]
    assert "kept=t1" in lines[3]


def test_apply_does_not_count_a_refused_removal(caplog):
    """С14b (§2.2/§2.4) — an adapter that reports a removal failed must not be
    counted as removed: "Removed N" would lie, which is the silent disagreement
    this module exists to end. It is warned about instead."""
    vias = [_via("v1", "GND", 1.0, 2.0), _via("v2", "GND", 1.0, 2.0)]
    adapter = _SpyAdapter(vias=vias, refuse={"v2"})

    with caplog.at_level(logging.WARNING):
        removed = apply_dedupe(adapter, find_via_duplicate_groups(vias), [])

    assert adapter.removed == ["v2"]
    assert removed == 0
    assert any(r.name == "kicadstamp.board_dedupe"
               and "refused to remove via v2" in r.getMessage()
               for r in caplog.records)


def test_scan_refreshes_before_reading_ground_truth():
    """С15 (§2.1/§2.2) — the scan asks the board for fresh truth first: the
    whole point of ignoring the registries is reading what is REALLY there, so
    a cached adapter must not answer from memory."""
    adapter = _SpyAdapter(vias=[_via("v1", "GND", 1.0, 2.0),
                                _via("v2", "GND", 1.0, 2.0)])
    via_groups, track_groups = scan_copper_duplicates(adapter)
    assert adapter.refresh_count == 1
    assert [v.uuid for v in via_groups[0]] == ["v1", "v2"]
    assert track_groups == []


def test_apply_summary_names_removed_and_groups():
    """С16 (§2.3) — one summary sentence for both surfaces; it carries BOTH
    numbers ("removed N", "in M groups") the plan asks the Log to show."""
    text = apply_summary(3, 2)
    assert "3" in text and "2" in text
