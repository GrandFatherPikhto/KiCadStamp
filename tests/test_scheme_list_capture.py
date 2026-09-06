"""Scheme List capture (plan_2026_09_05_scheme_list.md P2) — pure capture over
a mock adapter.

Scenario geometry (mm; Y-down board frame is irrelevant here since the closure
is coordinate-only):
  R1(10,10) --t1 F.Cu--> C1(20,10) --t2 In1.Cu--> C2(24,10)
  via v1 at (20,10)                     (inner-layer track round-trip)
  J1(15,12) is a FOREIGN component (NOT recorded) whose pad GND drags a stub
  tf(15,12)->(15,14) — that stub is dropped by the closure and must surface as
  a boundary_net (not be silently captured). J1 sits inside the refs' bbox+1mm
  so its stub survives the pre-filter and reaches the closure.
"""
import pytest

from kicadstamp.domain.board import Footprint, Pad, Track, Via
from kicadstamp.domain.geometry import BoardLayer, Box2, Vector2
from kicadstamp.exceptions import ValidationError
from kicadstamp.scheme_list_capture import (
    _prefilter_copper,
    _segment_intersects_box,
    build_scheme_list_diff,
    capture_scheme_list,
)
from kicadstamp.utils.units import MM

F = BoardLayer.BL_F_Cu
B = BoardLayer.BL_B_Cu
IN1 = BoardLayer.BL_In1_Cu

_V5 = "/Channel_0/AMP/+5V"
_GND = "/Channel_0/AMP/GND"


def _mm_xy(x_mm, y_mm):
    return Vector2.from_xy_mm(x_mm, y_mm)


def _fp(ref, x_mm, y_mm, angle=0.0, layer=F, sheet_uuids=()):
    return Footprint(ref=ref, uuid=f"uuid-{ref}", position=_mm_xy(x_mm, y_mm),
                     angle_deg=angle, layer=layer,
                     sheet_path_uuids=tuple(sheet_uuids))


def _pad(fp_ref, x_mm, y_mm, net, number="1"):
    return Pad(number=number, net_name=net, position=_mm_xy(x_mm, y_mm),
               size=Vector2.from_xy_mm(1.0, 1.0))


def _track(x1, y1, x2, y2, net, layer=F, width=0.25):
    return Track(uuid=f"t-{x1}-{y1}-{x2}-{y2}", start=_mm_xy(x1, y1),
                 end=_mm_xy(x2, y2), net_name=net, width_mm=width, layer=layer)


def _via(x_mm, y_mm, net, drill=0.3, diam=0.6):
    return Via(uuid=f"v-{x_mm}-{y_mm}", position=_mm_xy(x_mm, y_mm),
               net_name=net, drill_mm=drill, diameter_mm=diam)


class FakeAdapter:
    """Mock board adapter: pads keyed by footprint ref, boxes around items."""

    def __init__(self, footprints, tracks, vias, pads_by_ref):
        self._fps = footprints
        self._tracks = tracks
        self._vias = vias
        self._pads = pads_by_ref

    def get_footprints(self):
        return list(self._fps)

    def get_tracks(self):
        return list(self._tracks)

    def get_vias(self):
        return list(self._vias)

    def get_footprint_pads(self, fp):
        return list(self._pads.get(fp.ref, []))

    def get_bounding_boxes(self, items):
        out = []
        for it in items:
            if isinstance(it, Footprint):
                half = int(2.0 * MM)
            elif isinstance(it, Pad):
                half = int(0.5 * MM)
            elif isinstance(it, Via):
                half = max(int((it.diameter_mm / 2) * MM), int(0.25 * MM))
            else:
                out.append(None)
                continue
            p = it.position
            out.append(Box2(pos=Vector2.from_xy(p.x - half, p.y - half),
                            size=Vector2.from_xy(2 * half, 2 * half)))
        return out


def _scenario():
    """The docstring geometry; returns (adapter, refs)."""
    r1 = _fp("R1", 10, 10)
    c1 = _fp("C1", 20, 10, angle=90.0)
    c2 = _fp("C2", 24, 10)
    j1 = _fp("J1", 15, 12)
    fps = [r1, c1, c2, j1]
    pads = {
        "R1": [_pad("R1", 10, 10, _V5)],
        "C1": [_pad("C1", 20, 10, _V5)],
        "C2": [_pad("C2", 24, 10, _V5)],
        "J1": [_pad("J1", 15, 12, _GND)],
    }
    t1 = _track(10, 10, 20, 10, _V5, layer=F)
    t2 = _track(20, 10, 24, 10, _V5, layer=IN1)
    tf = _track(15, 12, 15, 14, _GND, layer=F)  # J1 stub -> boundary
    v1 = _via(20, 10, _V5)
    adapter = FakeAdapter(fps, [t1, t2, tf], [v1], pads)
    return adapter, ["R1", "C1", "C2"]


def test_capture_records_anchor_rotation_deg():
    """Addendum P2.x: capture stores the anchor's live angle_deg EXPLICITLY on
    the record, while the components keep their RAW absolute rotations (the
    anchor rotation is NOT subtracted — same convention as a Cell)."""
    adapter, refs = _scenario()
    r1 = next(fp for fp in adapter._fps if fp.ref == "R1")
    r1.angle_deg = 45.0
    cfg = capture_scheme_list("amp", refs, "R1", adapter=adapter)
    assert cfg.anchor_rotation_deg == pytest.approx(45.0)
    # the anchor component stays raw: absolute fp angle, offset (0, 0)
    anchor_comp = cfg.components[0]
    assert anchor_comp.ref == "R1"
    assert anchor_comp.rotation_deg == pytest.approx(45.0)
    assert anchor_comp.offset_along_mm == 0.0 and anchor_comp.offset_across_mm == 0.0
    # other components keep their raw board-frame offsets/rotations
    c1 = next(c for c in cfg.components if c.ref == "C1")
    assert c1.offset_along_mm == pytest.approx(10.0)
    assert c1.rotation_deg == pytest.approx(90.0)


class TestCaptureHappyPath:
    def setup_method(self):
        self.adapter, self.refs = _scenario()
        self.cfg = capture_scheme_list("amp", self.refs, "R1", adapter=self.adapter)

    def test_identity_and_anchor(self):
        assert self.cfg.name == "amp"
        assert self.cfg.anchor_ref == "R1"
        assert self.cfg.anchor_pad is None
        # anchor R1 is the offset origin -> offset (0, 0)
        assert self.cfg.components[0].ref == "R1"
        assert self.cfg.components[0].offset_along_mm == 0.0
        assert self.cfg.components[0].offset_across_mm == 0.0

    def test_components_literal_refs_and_offsets(self):
        comps = {c.ref: c for c in self.cfg.components}
        assert list(comps) == ["R1", "C1", "C2"]
        # origin is R1 centre (10,10) — board-frame dx/dy offsets
        assert comps["C1"].offset_along_mm == pytest.approx(10.0)
        assert comps["C1"].offset_across_mm == pytest.approx(0.0)
        assert comps["C1"].rotation_deg == pytest.approx(90.0)  # absolute rotation
        assert comps["C2"].offset_along_mm == pytest.approx(14.0)

    def test_tracks_literal_net_and_inner_layer_string(self):
        by_layer = {t.layer: t for t in self.cfg.tracks}
        assert set(by_layer) == {"F.Cu", "In1.Cu"}  # inner layer NOT collapsed
        assert by_layer["F.Cu"].net == _V5
        assert by_layer["In1.Cu"].net == _V5
        assert by_layer["F.Cu"].width_mm == pytest.approx(0.25)
        # offsets in the anchor frame: t1 (10,10)->(20,10) becomes (0,0)->(10,0)
        assert by_layer["F.Cu"].start_along_mm == pytest.approx(0.0)
        assert by_layer["F.Cu"].end_along_mm == pytest.approx(10.0)

    def test_via_literal_record(self):
        assert len(self.cfg.vias) == 1
        via = self.cfg.vias[0]
        assert via.net == _V5
        assert via.offset_along_mm == pytest.approx(10.0)  # at C1 (20,10)
        assert via.offset_across_mm == pytest.approx(0.0)
        assert via.drill_mm == pytest.approx(0.3)
        assert via.diameter_mm == pytest.approx(0.6)

    def test_foreign_stub_is_boundary_not_captured(self):
        # J1's GND stub was dropped (not silently captured into tracks)
        assert all(t.net != _GND for t in self.cfg.tracks)
        assert len(self.cfg.boundary_nets) == 1
        bn = self.cfg.boundary_nets[0]
        assert bn.net == _GND
        assert bn.action == "exclude"
        assert bn.external_ref == "J1"  # diagnostics: which external fp dragged it

    def test_source_sheet_none_without_sheet_names(self):
        # No sheet_names map -> no derivation; the record is "in place only".
        assert self.cfg.source_sheet is None


class TestSourceSheetDerivation:
    """5a.2 (plan_2026_09_06_scheme_list_sheet_capture.md): source_sheet is
    derived from the anchor footprint's OWN full resolved sheet path via the
    sheet_names {uuid: name} map — NOT a network-prefix guess (the removed
    channel_copy.sheet_name_of_fp hack only saw one hierarchy level and failed
    on global-only footprints). Works the same for both Record tabs because
    every mode has exactly one anchor and its path resolves identically."""

    def _capture(self, sheet_uuids=(), names=None, **kwargs):
        adapter, refs = _scenario()
        for fp in adapter._fps:  # put every captured ref on the same sheet
            if fp.ref in refs:
                fp.sheet_path_uuids = tuple(sheet_uuids) + (fp.uuid,)
        return capture_scheme_list("amp", refs, "R1", adapter=adapter,
                                   sheet_names=names, **kwargs)

    def test_derives_source_sheet_from_anchor_sheet_path(self):
        cfg = self._capture(sheet_uuids=("sheet-ch0",),
                            names={"sheet-ch0": "Channel_0"})
        assert cfg.source_sheet == "Channel_0"

    def test_nested_hierarchy_keeps_the_full_path(self):
        names = {"sheet-top": "Top", "sheet-ch0": "Channel_0"}
        cfg = self._capture(sheet_uuids=("sheet-top", "sheet-ch0"), names=names)
        assert cfg.source_sheet == "Top/Channel_0"

    def test_unresolved_segment_leaves_source_sheet_none(self):
        # sheet-ch0 is NOT in the map -> a None path segment -> no derivation.
        cfg = self._capture(sheet_uuids=("sheet-top", "sheet-ch0"),
                            names={"sheet-top": "Top"})
        assert cfg.source_sheet is None

    def test_explicit_source_sheet_override_wins_over_derivation(self):
        cfg = self._capture(sheet_uuids=("sheet-ch0",),
                            names={"sheet-ch0": "Channel_0"},
                            source_sheet="Top/Other")
        assert cfg.source_sheet == "Top/Other"

    def test_root_sheet_footprint_derives_nothing(self):
        # A footprint with no parent-sheet uuids (chain empty) -> no path.
        cfg = self._capture(names={"sheet-ch0": "Channel_0"})
        assert cfg.source_sheet is None


class TestCaptureFatals:
    def test_missing_refs_reported_in_one_fatal(self):
        adapter, refs = _scenario()
        with pytest.raises(ValidationError) as ei:
            capture_scheme_list("amp", refs + ["ZZ9", "QQ7"], "R1", adapter=adapter)
        msg = str(ei.value)
        assert "ZZ9" in msg and "QQ7" in msg

    def test_empty_refs_fatal(self):
        adapter, _ = _scenario()
        with pytest.raises(ValidationError):
            capture_scheme_list("amp", [], "R1", adapter=adapter)

    def test_anchor_not_among_refs_fatal(self):
        adapter, refs = _scenario()
        with pytest.raises(ValidationError):
            capture_scheme_list("amp", refs, "J1", adapter=adapter)

    def test_anchor_pad_not_found_fatal(self):
        adapter, refs = _scenario()
        with pytest.raises(ValidationError, match="pad"):
            capture_scheme_list("amp", refs, "R1", anchor_pad="9", adapter=adapter)

    def test_anchor_pad_origin_used_when_found(self):
        adapter, refs = _scenario()
        cfg = capture_scheme_list("amp", refs, "C1", anchor_pad="1", adapter=adapter)
        # origin = C1's pad = C1 centre (20,10) -> C2 offset (4, 0)
        comps = {c.ref: c for c in cfg.components}
        assert comps["C2"].offset_along_mm == pytest.approx(4.0)
        assert cfg.anchor_pad == "1"


class TestFarForeignCopperIsOutOfRegion:
    def test_far_copper_is_neither_captured_nor_boundary(self):
        adapter, refs = _scenario()
        far = _fp("J2", 100, 100)
        adapter._fps.append(far)
        adapter._pads["J2"] = [_pad("J2", 100, 100, "/Channel_1/AMP/CLK")]
        adapter._tracks.append(_track(100, 100, 100, 104, "/Channel_1/AMP/CLK", layer=B))
        cfg = capture_scheme_list("amp", refs, "R1", adapter=adapter)
        # far copper neither recorded nor reported — it is outside the region
        assert all(t.net != "/Channel_1/AMP/CLK" for t in cfg.tracks)
        assert all(bn.net != "/Channel_1/AMP/CLK" for bn in cfg.boundary_nets)


class TestPrefilterHelpers:
    def _region(self, x0, y0, x1, y1):
        return Box2(pos=Vector2.from_xy_mm(x0, y0), size=Vector2.from_xy_mm(x1 - x0, y1 - y0))

    def test_segment_crossing_region_is_kept_with_both_ends_outside(self):
        # Long track passes THROUGH the region but both endpoints are outside —
        # the plan's criterion (segment intersects bbox) must keep it.
        region = self._region(10, 10, 20, 20)
        crossing = _track(5, 15, 25, 15, "NET")
        assert _segment_intersects_box(crossing.start, crossing.end, region) is True
        kept_t, kept_v = _prefilter_copper([crossing], [], region)
        assert kept_t == [crossing]

    def test_parallel_track_outside_is_dropped(self):
        region = self._region(10, 10, 20, 20)
        outside = _track(5, 30, 25, 30, "NET")  # y=30 > 20, parallel, no crossing
        assert _segment_intersects_box(outside.start, outside.end, region) is False
        kept_t, _ = _prefilter_copper([outside], [], region)
        assert kept_t == []


# --- Reread diff (P3) -------------------------------------------------------

def _line_board(c2_x_mm=24.0):
    """R1(10,10) --t1 F.Cu--> C1(20,10) --t2 In1.Cu--> C2(c2_x,10), via at C1.
    No foreign component (no boundary nets)."""
    r1 = _fp("R1", 10, 10)
    c1 = _fp("C1", 20, 10, angle=90.0)
    c2 = _fp("C2", c2_x_mm, 10)
    fps = [r1, c1, c2]
    pads = {
        "R1": [_pad("R1", 10, 10, _V5)],
        "C1": [_pad("C1", 20, 10, _V5)],
        "C2": [_pad("C2", c2_x_mm, 10, _V5)],
    }
    t1 = _track(10, 10, 20, 10, _V5, layer=F)
    t2 = _track(20, 10, c2_x_mm, 10, _V5, layer=IN1)
    v1 = _via(20, 10, _V5)
    return FakeAdapter(fps, [t1, t2], [v1], pads)


def _line_board_plus(extra_refs, c2_x_mm=24.0):
    """_line_board + extra footprints appended on the same line beyond C2
    (pads only, no extra copper — enough for a 5c added-ref capture)."""
    adapter = _line_board(c2_x_mm=c2_x_mm)
    for i, ref in enumerate(extra_refs):
        x = 24.0 + 6.0 * (i + 1)
        adapter._fps.append(_fp(ref, x, 10))
        adapter._pads[ref] = [_pad(ref, x, 10, _V5)]
    return adapter


class TestRereadDiff:
    def test_no_changes_when_board_is_identical(self):
        adapter, refs = _scenario()
        stored = capture_scheme_list("amp", refs, "R1", adapter=adapter)
        diff = build_scheme_list_diff(stored, adapter)
        assert diff.changed is False
        assert diff.components_moved == []
        assert diff.vias_added == [] and diff.vias_removed == []
        assert diff.tracks_added == [] and diff.tracks_removed == []
        assert diff.boundary_nets_added == [] and diff.boundary_nets_gone == []
        assert diff.refs_not_found == [] and diff.anchor_missing is False

    def test_movement_within_tolerance_is_not_reported(self):
        adapter = _line_board(24.0)
        stored = capture_scheme_list("amp", ["R1", "C1", "C2"], "R1", adapter=adapter)
        # C2 + 0.005 mm and its In1.Cu stub + 0.005 mm — inside the 0.01 mm tol
        adapter2 = _line_board(24.005)
        diff = build_scheme_list_diff(stored, adapter2)
        assert diff.changed is False

    def test_movement_beyond_tolerance_reports_component_and_redrawn_track(self):
        adapter = _line_board(24.0)
        stored = capture_scheme_list("amp", ["R1", "C1", "C2"], "R1", adapter=adapter)
        adapter2 = _line_board(24.5)  # C2 moved +0.5 mm -> t2 re-drawn too
        diff = build_scheme_list_diff(stored, adapter2)
        moved = {c.ref for c in diff.components_moved}
        assert moved == {"C2"}
        change = next(c for c in diff.components_moved if c.ref == "C2")
        assert change.old_offset_along_mm == pytest.approx(14.0)
        assert change.new_offset_along_mm == pytest.approx(14.5)
        # the old In1.Cu stub disappeared and a new one appeared
        assert len(diff.tracks_removed) == 1
        assert len(diff.tracks_added) == 1
        assert not diff.vias_added and not diff.vias_removed
        assert diff.changed is True

    def test_rotation_beyond_tolerance_reports_moved(self):
        adapter = _line_board(24.0)
        stored = capture_scheme_list("amp", ["R1", "C1", "C2"], "R1", adapter=adapter)
        adapter2 = _line_board(24.0)
        # only C1's angle changes 90 -> 92 deg (> ANGLE_TOLERANCE_DEG)
        c1 = next(fp for fp in adapter2._fps if fp.ref == "C1")
        c1.angle_deg = 92.0
        diff = build_scheme_list_diff(stored, adapter2)
        assert {c.ref for c in diff.components_moved} == {"C1"}
        assert diff.changed is True

    def test_missing_ref_reported_not_fatal(self):
        adapter, refs = _scenario()
        stored = capture_scheme_list("amp", refs, "R1", adapter=adapter)
        # C2 disappears from the board (copper stays behind, anchored at C1)
        adapter._fps = [fp for fp in adapter._fps if fp.ref != "C2"]
        diff = build_scheme_list_diff(stored, adapter)
        assert diff.refs_not_found == ["C2"]
        assert diff.anchor_missing is False
        assert diff.changed is True

    def test_anchor_missing_guards_the_diff(self):
        adapter, refs = _scenario()
        stored = capture_scheme_list("amp", refs, "R1", adapter=adapter)
        adapter._fps = [fp for fp in adapter._fps if fp.ref != "R1"]
        diff = build_scheme_list_diff(stored, adapter)
        assert "R1" in diff.refs_not_found
        assert diff.anchor_missing is True
        assert diff.changed is True

    def test_new_boundary_net_is_reported_for_decision(self):
        adapter, refs = _scenario()
        stored = capture_scheme_list("amp", refs, "R1", adapter=adapter)
        assert [bn.net for bn in stored.boundary_nets] == [_GND]
        # a NEW foreign component drags a NEW boundary net near the region
        _CLK = "/Channel_0/AMP/CLK"
        adapter._fps.append(_fp("JX", 18, 11))
        adapter._pads["JX"] = [_pad("JX", 18, 11, _CLK)]
        adapter._tracks.append(_track(18, 11, 18, 13, _CLK, layer=F))
        diff = build_scheme_list_diff(stored, adapter)
        assert diff.boundary_nets_added == [_CLK]
        assert diff.boundary_nets_gone == []
        assert diff.changed is True
        # the new stub is a boundary, never silently captured into tracks
        assert all(t.net != _CLK for t in diff.tracks_added)

    def test_boundary_net_gone_is_reported(self):
        adapter, refs = _scenario()
        stored = capture_scheme_list("amp", refs, "R1", adapter=adapter)
        # J1's GND stub disappears -> GND no longer a boundary net
        adapter._tracks = [t for t in adapter._tracks if t.net_name != _GND]
        adapter._fps = [fp for fp in adapter._fps if fp.ref != "J1"]
        diff = build_scheme_list_diff(stored, adapter)
        assert diff.boundary_nets_gone == [_GND]
        assert diff.boundary_nets_added == []
        assert diff.changed is True


# ── Reread with a CHANGEABLE ref set (5c.2/5c.3, plan scheme_list 5c) ───────
# build_scheme_list_diff(stored, adapter, scope_refs=...) adds refs that are in
# the CURRENT scope but not recorded (components_added) and reports recorded
# refs that are physically present but outside the scope (refs_removed_from_
# scope) — distinct from refs_not_found ("must be, but absent").

class TestRereadDiffChangeableScope:
    def test_no_scope_keeps_legacy_fixed_set_categories_empty(self):
        """5c regression guard — scope_refs=None (no scope change) is the
        legacy Reread: the new 5c categories stay empty, nothing is added or
        removed from the set."""
        adapter, refs = _scenario()
        stored = capture_scheme_list("amp", refs, "R1", adapter=adapter)
        diff = build_scheme_list_diff(stored, adapter)  # no scope_refs
        assert diff.components_added == []
        assert diff.refs_removed_from_scope == []
        assert diff.changed is False

    def test_added_ref_in_scope_reports_component_added_with_geometry(self):
        """5c.3 — a ref in the CURRENT scope but never recorded lands in
        components_added WITH its fresh offset/rotation (the diff's capture is
        built over the widened set)."""
        adapter0 = _line_board(24.0)
        stored = capture_scheme_list("amp", ["R1", "C1", "C2"], "R1",
                                     adapter=adapter0)
        # C4 now physically exists on the board and is inside the scope.
        adapter1 = _line_board_plus(["C4"])
        diff = build_scheme_list_diff(stored, adapter1,
                                      scope_refs=["R1", "C1", "C2", "C4"])
        added = {c.ref: c for c in diff.components_added}
        assert set(added) == {"C4"}
        # C4 at (30,10); anchor R1 at (10,10) -> offset_along 20.0
        assert added["C4"].offset_along_mm == pytest.approx(20.0)
        assert added["C4"].offset_across_mm == pytest.approx(0.0)
        assert diff.refs_removed_from_scope == []
        assert diff.refs_not_found == []
        assert diff.changed is True

    def test_removed_present_ref_reports_removed_from_scope_not_moved(self):
        """5c.3 — a ref that is physically PRESENT but no longer in the scope
        goes to refs_removed_from_scope ONLY (never also components_moved)."""
        adapter = _line_board(24.0)
        stored = capture_scheme_list("amp", ["R1", "C1", "C2"], "R1",
                                     adapter=adapter)
        # C2 still on the board, but the current scope drops it.
        diff = build_scheme_list_diff(stored, adapter,
                                      scope_refs=["R1", "C1"])
        assert diff.refs_removed_from_scope == ["C2"]
        assert {c.ref for c in diff.components_moved} == set()
        assert diff.components_added == []
        assert diff.refs_not_found == []
        assert diff.changed is True

    def test_missing_and_out_of_scope_refs_do_not_overlap(self):
        """5c.3 — C2 physically absent AND out of scope stays a refs_not_found;
        C3 physically present but out of scope is refs_removed_from_scope. The
        two categories never double-count the same ref."""
        adapter0 = _line_board_plus(["C3"])  # C2 + C3 both physically present
        stored = capture_scheme_list("amp", ["R1", "C1", "C2", "C3"], "R1",
                                     adapter=adapter0)
        # C2 is removed from the BOARD entirely; the scope drops both C2 and C3
        # (only R1+C1 are re-selected / still on the recorded leaves).
        adapter1 = _line_board_plus(["C3"])
        adapter1._fps = [fp for fp in adapter1._fps if fp.ref != "C2"]
        diff = build_scheme_list_diff(stored, adapter1,
                                      scope_refs=["R1", "C1"])
        # C2: absent + out of scope -> ONLY refs_not_found (not double-counted)
        assert diff.refs_not_found == ["C2"]
        # C3: present + out of scope -> removed-from-scope
        assert diff.refs_removed_from_scope == ["C3"]
        assert diff.changed is True

    def test_combined_add_and_remove_reports_both_without_moved_noise(self):
        """5c.3 — a single Reread can add AND remove: C4 enters the scope
        (components_added), C2 leaves it while staying on the board
        (refs_removed_from_scope) — C2 is not also reported as moved."""
        adapter0 = _line_board(24.0)
        stored = capture_scheme_list("amp", ["R1", "C1", "C2"], "R1",
                                     adapter=adapter0)
        adapter1 = _line_board_plus(["C4"])
        diff = build_scheme_list_diff(stored, adapter1,
                                      scope_refs=["R1", "C1", "C4"])
        assert {c.ref for c in diff.components_added} == {"C4"}
        assert diff.refs_removed_from_scope == ["C2"]
        assert {c.ref for c in diff.components_moved} == set()
        assert diff.changed is True
