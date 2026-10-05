# tests/explode/test_explode_plan.py
"""Cells for the read-only explode PLAN (plan_2026_10_05_explode_r1_core.md §2).

Rule 35 — one cell per row of the plan's list: whole-instance removal, registry
ownership through ``is_own_key``, unregistered copper, the ``net_traces`` table
with cell/<cluster>/none/tee and ticks, the pad-LAYER rule (Ответ Демону), and
the vectors. `load_registry_entries` and `find_live_copper` are stubbed at the
module (both have their own owners/tests); everything else is the real code.
"""
import pytest

from types import SimpleNamespace

from kicadstamp.domain.geometry import Box2, Vector2
from kicadstamp.net_trace_planner import net_trace_registry_key
from kicadstamp.registry import make_registry_key
from kicadstamp import explode as explode_mod

from tests.explode.board import ExplodeBoard, fp, via, track, pad, F_CU, B_CU


def _nt(name="rec", **extra):
    base = dict(name=name, net="N", anchor_role=None, anchor_sheet=None,
                anchor_cluster=None, retired=False, skip=False,
                vias=(), tracks=())
    base.update(extra)
    return SimpleNamespace(**base)


def _scenario(monkeypatch, *, owner=None, net_pieces=None):
    """A cell instance (CELL/U1) beside a foreign PIF instance (C1, C2) and a
    far FAR instance (X1). Pads: U1 at (0,0), C1 at (3,0). Returns
    (board, cfg, cell)."""
    u1 = fp("c1", "U1", "CELL", 0.0, 0.0, role="IC")
    p1 = fp("p1", "C1", "PIF", 3.0, 0.0, role="A")
    # C2 sits FAR from the area (with margin 1) but is the SAME instance: the
    # whole instance must leave, not only the part in the area.
    p2 = fp("p2", "C2", "PIF", 3.0, 5.0, role="B")
    x1 = fp("f1", "X1", "FAR", 100.0, 100.0, role="Z")

    # copper: t1 is the PIF instance's own (registry), t2 is unregistered and
    # touches only a PIF pad, nt1/nt2 are the net_traces pieces.
    t1 = track("t1", F_CU, 3.0, 0.0, 3.5, 0.0)
    t2 = track("t2", F_CU, 3.0, 0.0, 3.0, 0.4)
    ot = track("ot", F_CU, 50.0, 50.0, 50.5, 50.0)   # another instance of the cell
    nt1 = track("nt1", F_CU, 0.0, 0.0, 0.2, 0.0)   # touches the CELL pad
    nt2 = track("nt2", F_CU, 3.0, 0.0, 3.2, 0.0)   # touches the PIF pad
    v1 = via("v1", 0.0, 0.4)                        # a via on a CELL pad

    board = ExplodeBoard(
        footprints=[u1, p1, p2, x1], tracks=[t1, t2, ot, nt1, nt2], vias=[v1],
        pads={"c1": [pad("1", 0.0, 0.0)],
              "p1": [pad("1", 3.0, 0.0)],
              "p2": [pad("1", 3.0, 5.0)]},
        board_name="board.kicad_pcb", project=("proj", "/tmp/proj"))

    pif_entry = SimpleNamespace(cell="pif_cell", cluster="PIF", sheet=None,
                                name="pe", uuid="peu")
    cell_entry = SimpleNamespace(cell="dac_buf", cluster="CELL", sheet=None,
                                 name="ce", uuid="ceu")
    cfg = SimpleNamespace(
        cells={"dac_buf": SimpleNamespace(uuid="cu"),
               "pif_cell": SimpleNamespace(uuid="pu")},
        entities=[cell_entry, pif_entry], clone_placements=[],
        # anchor_cluster matching the CELL instance so the Р1а-4 pre-filter keeps
        # this record (a record with no address falls to the anchor resolve).
        net_traces=[_nt("rec", anchor_cluster="CELL")])

    # t1's key names the PIF instance's cell as its OWN (anchor:C1). The key's
    # TEMPLATE part is the CELL identity, built by the SAME helper the plan and
    # the real redraw use (Р1а-3) — never by hand.
    pif_identity = explode_mod.cell_identity("pif_cell", cfg.cells["pif_cell"])
    own_key = make_registry_key("anchor:C1", pif_identity, None, 0)
    owner = {} if owner is None else dict(owner)
    owner.setdefault("t1", own_key)
    # "ot" names the SAME cell but a DIFFERENT instance (anchor ref ZZZ is not
    # one of this PIF instance's refs) — it must NOT move with PIF.
    owner.setdefault("ot", make_registry_key("anchor:ZZZ", pif_identity, None, 0))
    # The net_traces pieces are REGISTERED (net: keys) so they are not read as
    # unregistered copper; net: keys are never own of a cell, so they stay.
    owner.setdefault("nt1", make_registry_key("net:n1", "n1", None, 0))
    owner.setdefault("nt2", make_registry_key("net:n2", "n2", None, 0))
    monkeypatch.setattr(explode_mod, "load_registry_entries",
                        lambda *a, **k: ({}, {}, owner))

    pieces = list(net_pieces or [SimpleNamespace(live=nt1),
                                 SimpleNamespace(live=nt2)])
    monkeypatch.setattr(explode_mod, "find_live_copper",
                        lambda *a, **k: SimpleNamespace(pieces=pieces, reason=None))
    return board, cfg


def _plan(board, cfg, **kwargs):
    opts = {"margin_mm": 5.0, "gap_mm": 5.0}
    opts.update(kwargs)
    return explode_mod.plan_explode(
        board, cfg, "/tmp/config.sexp", "dac_buf", "CELL", None, {}, **opts)


# ── what moves ──────────────────────────────────────────────────────────────

def test_touching_instance_leaves_whole_and_the_cell_stays(monkeypatch):
    board, cfg = _scenario(monkeypatch)
    plan = _plan(board, cfg, margin_mm=1.0)      # only C1's FRAME is in the area
    assert [inst.label for inst in plan.instances] == ["PIF"]
    moved = {inst.label: sorted(inst.refs) for inst in plan.instances}
    assert moved == {"PIF": ["C1", "C2"]}       # BOTH PIF parts, not only C1
    moved_uuids = {mv.uuid for mv in plan.moves}
    assert {"p1", "p2"}.issubset(moved_uuids)
    assert "c1" not in moved_uuids               # the cell never moves
    assert "f1" not in moved_uuids               # the far instance stays


def test_instance_copper_via_registry_and_unregistered(monkeypatch):
    board, cfg = _scenario(monkeypatch)
    plan = _plan(board, cfg)
    pif = plan.instances[0]
    moved = {it.uuid for it in pif.copper}
    assert "t1" in moved                         # registry: is_own_key -> PIF
    assert "t2" in moved                         # unregistered, only PIF pads
    assert "nt1" not in moved and "nt2" not in moved  # net_traces copper stays
    assert "ot" not in moved                     # another instance's copper stays
    assert "ot" not in {mv.uuid for mv in plan.moves}


def test_cell_copper_never_leaves(monkeypatch):
    board, cfg = _scenario(monkeypatch)
    # A track recorded for the CELL cell at this instance is NOT foreign.
    cell_key = make_registry_key(
        "anchor:U1",
        explode_mod.cell_identity("dac_buf", cfg.cells["dac_buf"]), None, 0)
    board, cfg = _scenario(monkeypatch, owner={"ct": cell_key})
    from tests.explode.board import track as _t
    board._tracks.append(_t("ct", F_CU, 0.0, 0.0, 0.4, 0.0))
    plan = _plan(board, cfg)
    assert "ct" not in {mv.uuid for mv in plan.moves}


# ── the inter-cluster table ─────────────────────────────────────────────────

def test_table_classifies_cell_and_foreign_and_ticks(monkeypatch):
    board, cfg = _scenario(monkeypatch)
    plan = _plan(board, cfg)
    rows = {r.uuid: r for r in plan.table}
    assert rows["nt1"].touches == "cell" and rows["nt1"].ticked is False
    assert rows["nt2"].touches == "PIF" and rows["nt2"].ticked is True
    moved = {mv.uuid for mv in plan.moves}
    assert "nt2" in moved                        # a ticked piece travels
    assert "nt1" not in moved                    # an unticked piece stays


def test_tick_overrides_change_the_default(monkeypatch):
    board, cfg = _scenario(monkeypatch)
    plan = _plan(board, cfg, tick_overrides={"nt2": False, "nt1": True})
    rows = {r.uuid: r for r in plan.table}
    assert rows["nt2"].ticked is False
    assert "nt2" not in {mv.uuid for mv in plan.moves}
    assert rows["nt1"].ticked is True


def test_ordinary_intercluster_piece_is_foreign_not_tee(monkeypatch):
    """Р1а-1 (D23-like): ONE cell pad + ONE foreign pad is the FOREIGN label and
    a normal tick — NOT tee, and NO tee warning."""
    board, cfg = _scenario(monkeypatch)
    from tests.explode.board import track as _t
    nt5 = _t("nt5", F_CU, 0.0, 0.0, 3.0, 0.0)   # U1.1 -> PIF pad
    board._tracks.append(nt5)
    monkeypatch.setattr(explode_mod, "find_live_copper",
                        lambda *a, **k: SimpleNamespace(
                            pieces=[SimpleNamespace(live=nt5)], reason=None))
    plan = _plan(board, cfg)
    row = next(r for r in plan.table if r.uuid == "nt5")
    assert row.touches == "PIF" and row.ticked is True
    assert not any("T-branch" in w for w in plan.warnings)


def test_tee_is_two_cell_pads_plus_foreign(monkeypatch):
    """Р1а-1: a component joining TWO DIFFERENT cell pads AND a foreign pad is
    the dangerous tee — the cell's inner piece leaves with the foreign cluster."""
    board, cfg = _scenario(monkeypatch)
    from tests.explode.board import pad as _pad, track as _t
    board._pads["c1"] = [_pad("1", 0.0, 0.0), _pad("2", 1.0, 0.0)]
    nt4 = _t("nt4", F_CU, 0.0, 0.0, 1.0, 0.0)   # U1.1 -> U1.2 (inner)
    nt3 = _t("nt3", F_CU, 1.0, 0.0, 3.0, 0.0)   # U1.2 -> PIF pad
    board._tracks += [nt4, nt3]
    monkeypatch.setattr(explode_mod, "find_live_copper",
                        lambda *a, **k: SimpleNamespace(
                            pieces=[SimpleNamespace(live=nt3)], reason=None))
    plan = _plan(board, cfg)
    row = next(r for r in plan.table if r.uuid == "nt3")
    assert row.touches == "tee" and row.ticked is True
    assert any("T-branch" in w for w in plan.warnings)


def test_multi_is_several_foreign_clusters(monkeypatch):
    """Р1а-1/2: two foreign instances at once — `multi`, with its own warning."""
    board, cfg = _scenario(monkeypatch)
    from tests.explode.board import fp as _fp, pad as _pad, track as _t
    far = _fp("p3", "C3", "PIF2", 4.0, 0.0, role="C")
    board._fps.append(far)
    board._pads["p3"] = [_pad("1", 4.0, 0.0)]
    nt6 = _t("nt6", F_CU, 3.0, 0.0, 4.0, 0.0)   # PIF pad -> PIF2 pad
    board._tracks.append(nt6)
    monkeypatch.setattr(explode_mod, "find_live_copper",
                        lambda *a, **k: SimpleNamespace(
                            pieces=[SimpleNamespace(live=nt6)], reason=None))
    plan = _plan(board, cfg)
    row = next(r for r in plan.table if r.uuid == "nt6")
    assert row.touches == "multi" and row.ticked is True
    assert any("several foreign" in w for w in plan.warnings)


# ── Р1б: copper connectivity by SHAPE ───────────────────────────────────────

def test_ticked_cell_piece_leaves_on_its_own_ray(monkeypatch):
    """Р1б-2: a tick means the piece LEAVES. A hand-ticked cell piece gets its
    own ray — there is no "ticked but stays"."""
    board, cfg = _scenario(monkeypatch)
    plan = _plan(board, cfg, tick_overrides={"nt1": True})
    assert "nt1" in {mv.uuid for mv in plan.moves}


def test_cell_stub_to_pif_via_is_foreign(monkeypatch):
    """Р1б-1(д): a DAC_BUF pad -> track (stopping 5 um short) -> the PIF's via ->
    the PIF's B.Cu run -> PIF pad is ONE component, so the piece is `<PIF>` and
    travels with the PIF. Point-exact matching would miss the 5 um gap."""
    u1 = fp("c1", "U1", "CELL", 0.0, 0.0, role="IC")
    p1 = fp("p1", "C1", "PIF", 3.0, 0.0, role="A")
    ct = track("ct", F_CU, 0.0, 0.0, 0.995, 0.0)     # 5 um short of the via
    v1 = via("v1", 1.0, 0.0)                         # the PIF's via
    pt = track("pt", B_CU, 1.0, 0.0, 3.0, 0.0)       # the PIF's B.Cu run
    board = ExplodeBoard(footprints=[u1, p1], tracks=[ct, pt], vias=[v1],
                         pads={"c1": [pad("1", 0.0, 0.0)],
                               "p1": [pad("1", 3.0, 0.0)]})
    cfg = SimpleNamespace(cells={"dac_buf": SimpleNamespace(uuid="cu")},
                          entities=[], clone_placements=[],
                          net_traces=[_nt("rec", anchor_cluster="CELL")])
    monkeypatch.setattr(explode_mod, "load_registry_entries",
                        lambda *a, **k: ({}, {}, {}))
    monkeypatch.setattr(explode_mod, "find_live_copper",
                        lambda *a, **k: SimpleNamespace(
                            pieces=[SimpleNamespace(live=ct)], reason=None))
    plan = _plan(board, cfg)
    row = next(r for r in plan.table if r.uuid == "ct")
    assert row.touches == "PIF" and row.ticked is True
    assert "ct" in {mv.uuid for mv in plan.moves}


def test_t_junction_connects_tracks(monkeypatch):
    """Р1б-1(б): a track ending on the MIDDLE of another is connected."""
    u1 = fp("c1", "U1", "CELL", 0.0, 0.0, role="IC")
    cross = track("a", F_CU, 0.0, 0.0, 4.0, 0.0)
    stem = track("b", F_CU, 2.0, 0.0, 2.0, 3.0)      # a T on the crossbar
    board = ExplodeBoard(footprints=[u1], tracks=[cross, stem],
                         pads={"c1": [pad("1", 0.0, 0.0)]})
    classes = explode_mod._copper_classes(
        board.get_tracks(), [], explode_mod._pad_areas(board, {"cell": [u1]}))
    assert classes["a"] == classes["b"]              # one component


def test_parallel_thin_tracks_with_a_gap_are_not_connected():
    """Р1б-1(в): the graph predicate says two thin tracks 0.1 mm apart are NOT
    connected (the pure capsule test is in test_copper_connect.py)."""
    a = track("a", F_CU, 0.0, 0.0, 4.0, 0.0, width_mm=0.05)
    b = track("b", F_CU, 0.0, 0.1, 4.0, 0.1, width_mm=0.05)
    ea = explode_mod._copper_extent(a)
    eb = explode_mod._copper_extent(b)
    assert not explode_mod._copper_touch(a, b, ea[1], eb[1])


def test_cross_sheet_record_with_a_cell_piece_is_read(monkeypatch):
    """Р1б-3: an anchor on ANOTHER sheet (FPGA) is still read when a piece the
    REGISTRY wrote for it touches a cell pad."""
    board, cfg = _scenario(monkeypatch)
    rec = _nt("fpga_rec", anchor_sheet="FPGA", tracks=[object()], uuid="ru")
    cfg.net_traces = [rec]
    key = net_trace_registry_key(rec, 0)
    entry = SimpleNamespace(uuid="nt1")              # nt1 touches the cell pad
    monkeypatch.setattr(explode_mod, "load_registry_entries",
                        lambda *a, **k: ({}, {key: entry}, {}))
    monkeypatch.setattr(explode_mod, "find_live_copper",
                        lambda *a, **k: SimpleNamespace(
                            pieces=[SimpleNamespace(live=
                                next(t for t in board.get_tracks()
                                     if t.uuid == "nt1"))], reason=None))
    plan = _plan(board, cfg)
    assert "nt1" in {r.uuid for r in plan.table}


# ── pad layers (Ответ Демону) ───────────────────────────────────────────────

def test_smd_f_cu_pad_is_not_connected_to_a_b_cu_track(monkeypatch):
    """The mutation "pad layers ignored" must die here: a B.Cu track ending
    inside an F.Cu-only pad is NOT connected, so it does not borrow the pad's
    class."""
    u1 = fp("c1", "U1", "CELL", 0.0, 0.0, role="IC")
    p1 = fp("p1", "C1", "PIF", 3.0, 0.0, role="A")
    # ft (F.Cu) touches the pad; bt (B.Cu) sits at the SAME point. bt must NOT
    # borrow ft's class through the shared point (different layer; no via).
    f_track = track("ft", F_CU, 0.0, 0.0, 0.2, 0.0)
    b_track = track("bt", B_CU, 0.0, 0.0, 0.2, 0.0)
    board = ExplodeBoard(footprints=[u1, p1], tracks=[f_track, b_track],
                         pads={"c1": [pad("1", 0.0, 0.0, copper_layers=(F_CU,))]})
    cfg = SimpleNamespace(cells={"dac_buf": SimpleNamespace(uuid="cu")},
                          entities=[], clone_placements=[], net_traces=[])
    monkeypatch.setattr(explode_mod, "load_registry_entries",
                        lambda *a, **k: ({}, {}, {}))
    plan = _plan(board, cfg)
    assert plan.table == ()          # no net_traces record at all
    # The connectivity itself: bt touches nothing (no component class).
    classes = explode_mod._copper_classes(
        board.get_tracks(), board.get_vias(),
        explode_mod._pad_areas(board, {"cell": [u1]}))
    assert classes["bt"] == set()


def test_through_pad_connects_both_layers(monkeypatch):
    u1 = fp("c1", "U1", "CELL", 0.0, 0.0, role="IC")
    p1 = fp("p1", "C1", "PIF", 3.0, 0.0, role="A")
    f_track = track("ft", F_CU, 0.0, 0.0, 0.2, 0.0)
    b_track = track("bt", B_CU, 0.0, 0.0, 0.2, 0.0)
    board = ExplodeBoard(footprints=[u1, p1], tracks=[f_track, b_track],
                         pads={"c1": [pad("1", 0.0, 0.0,
                                          copper_layers=(F_CU, B_CU))]})
    classes = explode_mod._copper_classes(
        board.get_tracks(), board.get_vias(),
        explode_mod._pad_areas(board, {"cell": [u1]}))
    assert classes["ft"] == {"cell:U1:1"} and classes["bt"] == {"cell:U1:1"}


# ── vectors ─────────────────────────────────────────────────────────────────

def test_vector_pushes_the_frame_out_of_the_area_plus_the_gap(monkeypatch):
    board, cfg = _scenario(monkeypatch)
    plan = _plan(board, cfg, margin_mm=1.0, gap_mm=2.0)
    pif = plan.instances[0]
    dx, dy = pif.vector
    for box in explode_mod._box_map(board, pif.footprints).values():
        shifted = Box2(pos=Vector2(box.pos.x + dx, box.pos.y + dy),
                       size=box.size)
        assert not explode_mod._boxes_overlap(shifted, plan.area)


def test_zero_length_ray_goes_plus_x(monkeypatch):
    # area [-10,10]; box [-5,5]: +X must clear box.left (-5 + t) past 10 -> t=15.
    area = Box2(pos=Vector2(-10, -10), size=Vector2(20, 20))
    box = Box2(pos=Vector2(-5, -5), size=Vector2(10, 10))
    dx, dy = explode_mod._offset_to_leave(area, box, 0.0, 0.0, 0)
    assert dx == 15 and dy == 0
