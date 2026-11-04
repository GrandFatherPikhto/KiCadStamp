#!/usr/bin/env python3
"""plan_2026_10_28_entity_outside_trees_in_place, cell table §8.4 (1–16).

Denis's rule (28.10.2026): «ячейка через запись должна читаться, перечитываться и
находить свою медь вне зависимости от того, стоит запись в дереве или нет». The
branch under test is `kicadstamp/placement/in_place_entities.py`: an Entity that NO
tree places is materialized at its LIVE place, behind `in_place_entities=True` (the
two READING doors — «Select cell» and «Subtract selected copper» — both dry runs).

The cells (the plan's numbers live in the docstrings, per rule 37 — the NAME says
the property):

  * a record outside trees takes its pose and its role map from the live cluster;
  * a record a tree places is decided by the TREE — the live read is never made;
  * --only/--cluster are asked BEFORE the board is read;
  * a record with no live place, a non-rigid cluster, a mirror disagreement, an
    imprint or `skip: true` yields NO clone and ONE visible warning, while its
    neighbours still materialize;
  * a cell that cannot be identified without the user's selection is refused;
  * two instances of one cell never swap their role maps;
  * the flag itself: off = today byte for byte, on + `dry_run=False` = ValueError;
  * `trees=0` (the HP-Power shape) and `adapter is None` (every config-only test);
  * both door wirings pass the flag.

Board-free: the "live board" is a fake adapter, and — except in the cells that are
ABOUT the live read — `live_cluster_frame` is a spy, so a cell can say exactly
which pose the branch was given without re-implementing the fit.
"""
import pytest

from kicadstamp.config import Cell, Config, Entity, TemplateComponentSlot
from kicadstamp.constants import CLUSTER_FIELD_NAME, ROLE_FIELD_NAME
from kicadstamp.domain.board import Footprint
from kicadstamp.domain.geometry import BoardLayer, Vector2
from kicadstamp.exceptions import NonRigidClusterError
from kicadstamp.geometry.clone_geometry import apply_clone_geometry
from kicadstamp.link_trees import link_trees
from kicadstamp.live_cluster_frame import LiveClusterFrame
from kicadstamp.placement import in_place_entities as ipe
from kicadstamp.placement.entity_placement import materialize_entity_placements
from kicadstamp.trees import Tree, TreeAnchor, TreeNode
from tests.fakes.format3 import det_uuid

FRONT = BoardLayer.BL_F_Cu
BACK = BoardLayer.BL_B_Cu
CLUSTER = "CL_A"
WARNER = "kicadstamp.placement.in_place_entities"


# ── fakes ────────────────────────────────────────────────────────────────────

class _ClusterAdapter:
    """The adapter surface the live reader touches: the footprint list, the
    Role/Cluster field reads, one footprint by ref, and the refresh it would do."""

    def __init__(self, footprints):
        self._fps = list(footprints)
        self.refreshes = 0
        self.sweeps = 0

    def refresh_board(self):
        self.refreshes += 1

    def get_footprints(self):
        self.sweeps += 1
        return list(self._fps)

    def get_field_value(self, fp, name):
        if name == ROLE_FIELD_NAME:
            return fp.role
        if name == CLUSTER_FIELD_NAME:
            return fp.cluster
        return None

    def get_footprint(self, ref):
        return next((fp for fp in self._fps if fp.ref == ref), None)

    def get_footprint_pads(self, fp):
        return []


def _live_fp(ref, role, x_mm, y_mm, angle=0.0, layer=FRONT, cluster=CLUSTER):
    fp = Footprint(ref=ref, uuid=f"uuid-{ref}",
                   position=Vector2.from_xy_mm(x_mm, y_mm),
                   angle_deg=angle, layer=layer)
    fp.role = role
    fp.cluster = cluster
    fp.sheet_path_uuids = []
    return fp


def _cell(name="two", slots=(("A", 0.0, 0.0), ("B", 10.0, 0.0))):
    return Cell(name=name, layer="F.Cu",
                components=[TemplateComponentSlot(role=role, offset_along_mm=along,
                                                  offset_across_mm=across,
                                                  angle_deg=0.0)
                            for role, along, across in slots])


def _placed_live(cell, *, origin_mm=(50.0, 60.0), rotation_deg=0.0,
                 cluster=CLUSTER, prefix="", layer=FRONT):
    """The live footprints of a PRODUCTION placement of `cell` — positioned by
    `apply_clone_geometry`, which is what a redraw would place."""
    refs = {slot.role: f"{prefix}{slot.role}_1" for slot in cell.components}
    clone = _clone_of(cell, origin_mm, rotation_deg)
    layout = apply_clone_geometry(clone, cell, refs)
    return [
        _live_fp(placed.ref, placed.role, placed.position.x / 1e6,
                 placed.position.y / 1e6, angle=placed.angle_deg,
                 layer=layer, cluster=cluster)
        for placed in layout.components]


def _clone_of(cell, origin_mm, rotation_deg=0.0):
    from kicadstamp.config import ClonePlacement
    return ClonePlacement(cluster=CLUSTER, cell=cell.name, xy=origin_mm,
                          rotation_deg=rotation_deg)


def _cfg(cells, entities, trees=()):
    """A Config with the format-3 identities the registry key needs (Р-У5.1)."""
    entities = list(entities)
    for e in entities:
        if getattr(e, "uuid", None) is None:
            e.uuid = det_uuid(f"entities:{e.name}")
    for cell in cells.values():
        if getattr(cell, "uuid", None) is None:
            cell.uuid = det_uuid(f"cells:{cell.name}")
    return Config(cells=dict(cells), entities=entities, trees=list(trees))


def _tree_placing(ref, xy=(1.0, 2.0)):
    return Tree(name="t", anchor=TreeAnchor(is_origin=True),
                nodes=[TreeNode(ref=ref, kind="placement", xy=xy, polar=None,
                                rotation=0.0, name=None, group=None, children=[])])


def _no_selection(monkeypatch, answer=False):
    """The branch asks the ONE owner whether the cell needs a selection. Every
    cell that is not ABOUT that question pins the answer, so the cell says what it
    tests instead of what the fake board happens to derive."""
    seen = []

    def fake(clone, *, adapter=None, cell=None, sheet_names=None):
        seen.append(clone)
        return answer

    monkeypatch.setattr(ipe, "clone_uses_selection_mode", fake)
    return seen


def _materialize(adapter, cfg, *, only=None, cluster=None):
    return ipe.materialize_in_place_entity_placements(
        adapter, cfg, link_trees(cfg, cfg.trees), {}, only=only, cluster=cluster)


# ── 1: the pose and the role map come from the LIVE cluster ──────────────────

def test_a_record_outside_trees_is_materialized_at_its_live_place(monkeypatch):
    """Cell 1. The frame is the REAL reader's: origin 50/60 mm, 90°, unmirrored,
    and the clone carries the very role map the frame was fitted against."""
    _no_selection(monkeypatch)
    cell = _cell()
    live = _placed_live(cell, origin_mm=(50.0, 60.0), rotation_deg=90.0)
    adapter = _ClusterAdapter(live)
    cfg = _cfg({"two": cell}, [Entity(name="E", cell="two", cluster=CLUSTER,
                                      sheet="S")])

    clones = _materialize(adapter, cfg)

    assert [c.name for c in clones] == ["E"]
    clone = clones[0]
    assert clone.cell == "two"
    assert clone.xy == pytest.approx((50.0, 60.0), abs=1e-9)
    assert clone.rotation_deg == pytest.approx(90.0)
    assert clone.mirror is False
    assert clone.refs == {fp.role: fp.ref for fp in live}
    # the branch does NOT refresh the board per record: the caller just did
    assert adapter.refreshes == 0


# ── 2: a record a tree places is decided by the TREE ─────────────────────────

def test_a_record_a_tree_places_is_not_read_live_again(monkeypatch):
    """Cell 2. Two records, one in a tree: the tree's clone comes from the tree
    (xy 1/2 mm from the node), the other from the live place, and the live reader
    is asked EXACTLY once — for the record no tree places."""
    _no_selection(monkeypatch)
    seen = []
    real_frame = ipe.live_cluster_frame

    def spy(adapter, cell, cluster, sheet, sheet_names, role_to_ref=None, **kw):
        seen.append((cluster, sheet))
        return real_frame(adapter, cell, cluster, sheet, sheet_names, role_to_ref,
                          **kw)

    monkeypatch.setattr(ipe, "live_cluster_frame", spy)
    cell = _cell()
    live = _placed_live(cell, cluster=CLUSTER)
    adapter = _ClusterAdapter(live)
    cfg = _cfg({"two": cell},
               [Entity(name="in_tree", cell="two", cluster=CLUSTER, sheet="S"),
                Entity(name="outside", cell="two", cluster=CLUSTER, sheet="S")],
               [_tree_placing("in_tree")])

    clones = materialize_entity_placements(adapter, cfg, {}, in_place=True)

    by_name = {c.name: c for c in clones}
    assert set(by_name) == {"in_tree", "outside"}
    assert by_name["in_tree"].xy == (1.0, 2.0)          # the node, not the board
    assert by_name["outside"].xy == pytest.approx((50.0, 60.0), abs=1e-9)
    assert seen == [(CLUSTER, "S")]                     # ONE live read, for the outsider


# ── 3 / 12: --only and --cluster are asked BEFORE the board ──────────────────

@pytest.mark.parametrize("kwargs", [
    {"only": ["OTHER"]},
    {"only": ["some_thermal_array"]},
    {"cluster": ["NOT_THIS_CLUSTER"]},
    {"only": []},           # empty == no narrowing, so this one DOES materialize
])
def test_the_selection_axes_are_asked_before_any_board_read(monkeypatch, kwargs):
    """Cells 3 and 12. A run that did not ask for the record reads NOTHING: the
    fake adapter's sweep counter stays at zero (only=[] is the M4 rule: an empty
    list is no narrowing, so that case does materialize and does read)."""
    _no_selection(monkeypatch)
    cell = _cell()
    adapter = _ClusterAdapter(_placed_live(cell))
    cfg = _cfg({"two": cell}, [Entity(name="E", cell="two", cluster=CLUSTER,
                                      sheet="S")])

    clones = _materialize(adapter, cfg, **kwargs)

    if kwargs.get("only") == []:
        assert [c.name for c in clones] == ["E"]
        assert adapter.sweeps > 0
    else:
        assert clones == []
        assert adapter.sweeps == 0


# ── 4: no live place -> one warning, neighbours still go ─────────────────────

def test_a_record_with_no_live_place_is_one_warning_and_the_rest_proceed(
        monkeypatch, caplog):
    """Cell 4. The first record's cluster is not on the board at all: ONE warning
    naming it, no clone for it — and the second record still materializes."""
    _no_selection(monkeypatch)
    cell = _cell()
    live = _placed_live(cell, cluster=CLUSTER)
    adapter = _ClusterAdapter(live)
    cfg = _cfg({"two": cell},
               [Entity(name="ghost", cell="two", cluster="NOWHERE", sheet="S"),
                Entity(name="E", cell="two", cluster=CLUSTER, sheet="S")])

    with caplog.at_level("WARNING", logger=WARNER):
        clones = _materialize(adapter, cfg)

    assert [c.name for c in clones] == ["E"]
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1
    assert "ghost" in warnings[0].getMessage()
    assert "not on the live board" in warnings[0].getMessage()


@pytest.mark.parametrize("missing", ["no_cell", "no_cluster", "cell_gone"])
def test_a_record_that_cannot_be_read_live_is_one_warning(caplog, missing):
    """Cell 4 (the config half): no cell, no cluster, or a cell the config does
    not hold — the same three words the GUI's fallback reader uses, one line each,
    never a crash and never a silent skip."""
    cells = {"two": _cell()}
    entity = Entity(name="E", cell="two", cluster=CLUSTER, sheet="S")
    if missing == "no_cell":
        entity.cell = None
    elif missing == "no_cluster":
        entity.cluster = None
    else:
        entity.cell = "absent"
    cfg = _cfg(cells, [entity])
    adapter = _ClusterAdapter(_placed_live(cells["two"]))

    with caplog.at_level("WARNING", logger=WARNER):
        clones = _materialize(adapter, cfg)

    assert clones == []
    assert len([r for r in caplog.records if r.levelname == "WARNING"]) == 1
    assert adapter.sweeps == 0


# ── 5 / 15: imprint and skip are not this branch's business ──────────────────

@pytest.mark.parametrize("field,value", [("imprint", "imp"), ("skip", True),
                                         ("retired", True)])
def test_imprint_skip_and_retired_records_yield_no_clone(monkeypatch, field, value):
    """Cells 5 and 15. An imprint has its own plan (imprint_apply), a skipped or
    retired record places nothing — and none of the three reads the board."""
    _no_selection(monkeypatch)
    cell = _cell()
    entity = Entity(name="E", cell="two", cluster=CLUSTER, sheet="S")
    setattr(entity, field, value)
    cfg = _cfg({"two": cell}, [entity])
    adapter = _ClusterAdapter(_placed_live(cell))

    assert _materialize(adapter, cfg) == []
    assert adapter.sweeps == 0


# ── 10: a cell that needs a selection is refused, never guessed ──────────────

def test_a_cell_that_needs_a_selection_is_refused_with_one_warning(monkeypatch,
                                                                  caplog):
    """Cell 10. The ONE owner says "this cell needs the user's selection", so the
    branch refuses: an apply must never depend on what happens to be selected. The
    owner IS asked (the spy sees the transient clone), and the answer stops the
    record before any clone exists."""
    asked = _no_selection(monkeypatch, answer=True)
    cell = _cell()
    adapter = _ClusterAdapter(_placed_live(cell))
    cfg = _cfg({"two": cell}, [Entity(name="E", cell="two", cluster=CLUSTER,
                                      sheet="S")])

    with caplog.at_level("WARNING", logger=WARNER):
        clones = _materialize(adapter, cfg)

    assert clones == []
    assert len(asked) == 1
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1
    assert "cannot be identified without a selection" in warnings[0].getMessage()


# ── 11: two instances of one cell never swap their role maps ─────────────────

def test_two_instances_of_one_cell_each_carry_their_own_refs(monkeypatch):
    """Cell 11. The live twin defect in miniature: two clusters of the SAME cell,
    eleven identical roles. Each clone must carry ITS instance's refdes — the map
    the frame was fitted against — never the other's."""
    _no_selection(monkeypatch)
    cell = _cell()
    a = _placed_live(cell, origin_mm=(10.0, 10.0), cluster="CL_A", prefix="a_")
    b = _placed_live(cell, origin_mm=(40.0, 40.0), cluster="CL_B", prefix="b_")
    adapter = _ClusterAdapter(a + b)
    cfg = _cfg({"two": cell},
               [Entity(name="E_A", cell="two", cluster="CL_A", sheet="S"),
                Entity(name="E_B", cell="two", cluster="CL_B", sheet="S")])

    clones = {c.name: c for c in _materialize(adapter, cfg)}

    assert clones["E_A"].refs == {"A": "a_A_1", "B": "a_B_1"}
    assert clones["E_B"].refs == {"A": "b_A_1", "B": "b_B_1"}
    assert clones["E_A"].xy == pytest.approx((10.0, 10.0), abs=1e-9)
    assert clones["E_B"].xy == pytest.approx((40.0, 40.0), abs=1e-9)


# ── 13 / 14: non-rigid and a mirror disagreement are refusals ────────────────

def test_a_non_rigid_cluster_is_refused_with_one_warning(monkeypatch, caplog):
    """Cell 13. `strict=True` is what the branch asks for: a cluster that is not a
    rigid copy is a GUESS, and a guess must not become copper. The warning names
    the deviation."""
    _no_selection(monkeypatch)

    def refuse(*args, **kwargs):
        raise NonRigidClusterError("live cluster is not a rigid copy",
                                   residual_mm=0.123)

    monkeypatch.setattr(ipe, "live_cluster_frame", refuse)
    cell = _cell()
    cfg = _cfg({"two": cell}, [Entity(name="E", cell="two", cluster=CLUSTER,
                                      sheet="S")])

    with caplog.at_level("WARNING", logger=WARNER):
        clones = _materialize(_ClusterAdapter([]), cfg)

    assert clones == []
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1
    assert "0.123" in warnings[0].getMessage()


def test_the_strict_read_refuses_a_non_rigid_cluster_for_real(caplog):
    """Cell 13 (the real-reader half). One role stands 5 mm off its place: the
    production reader with `strict=True` refuses, the branch turns that into ONE
    warning, and no clone is built. This is the cell that dies if the branch ever
    asks for `strict=False` — a guessed pose must not become copper."""
    cell = _cell()
    a, b = _placed_live(cell)
    moved = _live_fp(b.ref, b.role, b.position.x / 1e6 + 5.0,
                     b.position.y / 1e6, angle=b.angle_deg)
    adapter = _ClusterAdapter([a, moved])
    cfg = _cfg({"two": cell}, [Entity(name="E", cell="two", cluster=CLUSTER,
                                      sheet="S")])

    with caplog.at_level("WARNING", logger=WARNER):
        clones = _materialize(adapter, cfg)

    assert clones == []
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1
    assert "rigid copy" in warnings[0].getMessage()


def test_a_mirror_disagreement_is_refused_with_one_warning(monkeypatch, caplog):
    """Cell 14. The record says «flip it», the board says «not flipped»: drawing
    either way moves the copper to the other side of the board, so the record is
    skipped and both values are named."""
    _no_selection(monkeypatch)
    cell = _cell()
    adapter = _ClusterAdapter(_placed_live(cell))
    entity = Entity(name="E", cell="two", cluster=CLUSTER, sheet="S")
    entity.mirror = True
    cfg = _cfg({"two": cell}, [entity])

    with caplog.at_level("WARNING", logger=WARNER):
        clones = _materialize(adapter, cfg)

    assert clones == []
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1
    assert "mirror=True" in warnings[0].getMessage().replace(" ", "")


def test_a_mirrored_instance_is_carried_through(monkeypatch):
    """Cell 1 (the B.Cu half): the live instance stands on the BACK side, so the
    clone carries mirror=True — the copper lands where the board has it."""
    _no_selection(monkeypatch)
    cell = _cell()
    live = _placed_live(cell, layer=BACK)
    adapter = _ClusterAdapter(live)
    entity = Entity(name="E", cell="two", cluster=CLUSTER, sheet="S")
    entity.mirror = True                  # the record AGREES with the board
    cfg = _cfg({"two": cell}, [entity])

    clones = _materialize(adapter, cfg)

    assert len(clones) == 1
    assert clones[0].mirror is True


# ── 8 / 9: the shapes that have no trees, and the callers with no board ──────

def test_the_hp_power_shape_with_no_trees_materializes_every_record(monkeypatch):
    """Cell 8. `trees=0` — the very shape the plan was opened with: the branch is
    the ONLY thing that can answer, and it answers for every record it was asked
    about."""
    _no_selection(monkeypatch)
    cell = _cell()
    a = _placed_live(cell, origin_mm=(10.0, 10.0), cluster="CL_A", prefix="a_")
    b = _placed_live(cell, origin_mm=(40.0, 40.0), cluster="CL_B", prefix="b_")
    adapter = _ClusterAdapter(a + b)
    cfg = _cfg({"two": cell},
               [Entity(name="E_A", cell="two", cluster="CL_A", sheet="S"),
                Entity(name="E_B", cell="two", cluster="CL_B", sheet="S")])
    assert cfg.trees == []

    clones = _materialize(adapter, cfg)

    assert sorted(c.name for c in clones) == ["E_A", "E_B"]


def test_without_a_board_the_branch_is_silent():
    """Cell 9. Every config-only caller (hundreds of existing tests, the loader,
    validation) materializes with `adapter=None`: the branch must answer [] and
    touch nothing."""
    cfg = _cfg({"two": _cell()}, [Entity(name="E", cell="two", cluster=CLUSTER,
                                         sheet="S")])

    assert ipe.materialize_in_place_entity_placements(None, cfg, [], {}) == []
    assert materialize_entity_placements(None, cfg, {}, in_place=True) == []


# ── 7: the flag itself ───────────────────────────────────────────────────────

def test_the_flag_off_is_todays_behaviour_and_on_needs_a_dry_run(monkeypatch):
    """Cell 7. `in_place=False` is the pre-branch answer byte for byte (a record
    outside trees yields nothing), and the reading flag may not be armed on a real
    run: the pipeline refuses that combination when it is created."""
    from kicadstamp.apply_pipeline import ApplyPipeline

    _no_selection(monkeypatch)
    cell = _cell()
    adapter = _ClusterAdapter(_placed_live(cell))
    cfg = _cfg({"two": cell}, [Entity(name="E", cell="two", cluster=CLUSTER,
                                      sheet="S")])

    assert materialize_entity_placements(adapter, cfg, {}, in_place=False) == []
    assert adapter.sweeps == 0
    assert len(materialize_entity_placements(adapter, cfg, {}, in_place=True)) == 1

    with pytest.raises(ValueError, match="read-only"):
        ApplyPipeline("x.sexp", in_place_entities=True, dry_run=False)
    assert ApplyPipeline("x.sexp", in_place_entities=True, dry_run=True).in_place_entities


# ── 6 / 16: both reading doors arm the flag ─────────────────────────────────

class _FakePipeline:
    calls = []

    def __init__(self, *args, **kwargs):
        type(self).calls.append(kwargs)
        self.adapter = _FakeAdapter()
        self.at_current_place = type("R", (), {"bound": None})()

    def run(self):
        pass

    def plan_copper(self):
        return [], []

    def close(self):
        pass


class _FakeAdapter:
    selected = []

    def select_items(self, items):
        type(self).selected.append(list(items))


def test_select_cell_arms_the_reading_flag(monkeypatch):
    """Cell 6. «Select cell» is one of the two doors that may ask for a live-place
    clone, and it asks on a DRY run — that is the whole wiring of the fix."""
    from types import SimpleNamespace

    from gui import select_cell, select_cell_copper

    record = Entity(name="E", cell="two", cluster=CLUSTER, sheet="S")
    cfg = _cfg({"two": _cell()}, [record])
    _FakePipeline.calls = []
    monkeypatch.setattr("kicadstamp.apply_pipeline.ApplyPipeline", _FakePipeline)
    monkeypatch.setattr("kicadstamp.config.load_config",
                        lambda path: (cfg, SimpleNamespace(sheet_names={})))
    monkeypatch.setattr(select_cell, "instance_recording_name",
                        lambda *a, **k: "E")
    # No recorded copper on the record: the door then takes the PLANNED-copper
    # branch (the one the fix feeds), not the registry-only fallback.
    monkeypatch.setattr(select_cell, "cell_has_recorded_copper", lambda cell: False)
    monkeypatch.setattr(
        select_cell, "select_cell_copper_targets",
        lambda *a, **k: SimpleNamespace(footprints=[], copper=[], copper_by_registry=0,
                                        copper_by_geometry=0, copper_missing=0,
                                        copper_planned=0, line="ok"))

    result = select_cell_copper._run_with_copper(
        {"root_path": "x.sexp", "cell_name": "two", "cluster": CLUSTER,
         "sheet": "S", "timeout_ms": 10, "refs": {}})

    assert result["line"] == "ok"
    assert _FakePipeline.calls and _FakePipeline.calls[0]["in_place_entities"] is True
    assert _FakePipeline.calls[0]["dry_run"] is True


def test_subtract_selected_copper_arms_the_reading_flag(monkeypatch, tmp_path):
    """Cell 16. «Subtract selected copper» builds its record -> copper map from a
    dry run of the recording, so it needs the same branch — otherwise a record
    outside trees has no pairs at all."""
    from kicadstamp import absent_copper_prune as acp

    _FakePipeline.calls = []
    monkeypatch.setattr("kicadstamp.apply_pipeline.ApplyPipeline", _FakePipeline)
    monkeypatch.setattr(acp, "record_copper_map_for",
                        lambda *a, **k: acp.RecordCopperMap())

    acp.instance_record_copper_map(str(tmp_path / "cfg.sexp"), "E", 10, None)

    assert _FakePipeline.calls and _FakePipeline.calls[0]["in_place_entities"] is True
