"""P6 Stage 4 — ".cell consumer" audit for scheme_list-based Entities
(handoff_2026_09_06_scheme_list_p6_stage3_done.md Stage 4, design §9 п.9).

A scheme_list-based Entity (plan_2026_09_05_scheme_list.md §5.1) has
cell=None by construction — it is a refdes-literal clone of a recorded
snapshot, so every remaining consumer of Entity.cell that assumes a
non-empty string must close its None-branch. The Apply/Redraw path (P4
`_walk` skip, scheme_list_apply.py) and `check_entity_cells_exist` are
already covered in test_scheme_list_apply.py / test_scheme_list_config.py;
this file pins the REST of the audit:

  * placement/anchor_identity — the shared self-anchor helpers return
    None/False for a scheme_list Entity (never rely on cfg.cells.get(None));
  * gui/docks/tree_from_selection role/zero-slot derivations return None;
  * anchor_graph's producer index never registers a scheme_list Entity as a
    board component producer and never crashes on its cell=None;
  * the Stage-4 canary: a Place-written Entity (build_scheme_list_entity +
    append_tree_child_node) survives load_config round-trip, link_trees
    resolves its node to the real Entity record, materialize_entity_placements
    emits NO ClonePlacement(cell=None), and check_entity_cells_exist passes.

Headless (no live board, no QApplication) — same shape as test_scheme_list_
place.py.
"""
from pathlib import Path
from types import SimpleNamespace

from gui.docks.tree_from_selection import (
    _mount_baked_angle_deg,
    _zero_slot_role,
    build_scheme_list_entity,
    tree_anchor_from_cluster_entity,
)
from kicadstamp.anchor_graph import build_producer_index, build_records
from kicadstamp.config import Config, load_config
from kicadstamp.config.models import Entity
from kicadstamp.config.sexp_format import dict_to_sexp
from kicadstamp.config_writer import append_tree_child_node, upsert_entity
from kicadstamp.link_trees import link_trees
from kicadstamp.placement.anchor_identity import (
    entity_anchor_identity,
    entity_cell_has_copper,
    entity_is_self_anchor,
    entity_matches_role_anchor,
)
from kicadstamp.placement.entity_placement import materialize_entity_placements
from kicadstamp.validation import check_entity_cells_exist


def _scheme_entity(name="S1", sheet="Channel_1"):
    return Entity(name=name, scheme_list="psu", sheet=sheet)


def _role_anchor(role="DAC"):
    return SimpleNamespace(role=role, anchor_sheet=None, anchor_cluster=None)


def _scheme_record_dict(name="psu"):
    """A minimal VALID scheme_lists: entry for a file round-trip load."""
    return {
        "name": name,
        "anchor_ref": "R1",
        "source_sheet": "Channel_0",
        "anchor_rotation_deg": 0.0,
        "components": [
            {"ref": "R1", "offset_along_mm": 0.0, "offset_across_mm": 0.0,
             "rotation_deg": 0.0},
            {"ref": "C1", "offset_along_mm": 10.0, "offset_across_mm": 0.0,
             "rotation_deg": 0.0},
        ],
    }


def _placement_node(ref, x=1.0, y=2.0, rotation=None):
    node = {"ref": ref, "kind": "placement", "xy": [x, y]}
    if rotation:
        node["rotation"] = rotation
    return node


# ── placement/anchor_identity ───────────────────────────────────────────────

class TestAnchorIdentityForSchemeListEntity:
    def test_anchor_identity_is_none_not_a_cell_lookup(self):
        """A scheme_list Entity has no cell/anchor_role — it can never be an
        anchor subject, so entity_anchor_identity returns None even when the
        config has cells (the guard is explicit, not cfg.cells.get(None))."""
        cfg = Config()
        assert entity_anchor_identity(_scheme_entity(), cfg) is None

    def test_cell_has_copper_is_false(self):
        """The scheme_list Entity's copper lives in the recorded snapshot, not
        in a cell — never flagged as a copper-carrying self-anchor duplicate."""
        assert entity_cell_has_copper(_scheme_entity(), Config()) is False

    def test_matches_role_anchor_and_self_anchor_are_false(self):
        ent = _scheme_entity()
        assert entity_matches_role_anchor(ent, Config(), _role_anchor()) is False
        assert entity_is_self_anchor(ent, Config(), _role_anchor()) is False


# ── gui/docks/tree_from_selection derivations ───────────────────────────────

class TestTreeFromSelectionDerivationsForSchemeListEntity:
    def test_zero_slot_role_is_none(self):
        ent = _scheme_entity()
        assert _zero_slot_role(ent, Config()) is None

    def test_tree_anchor_from_cluster_entity_carries_no_role(self):
        anchor = tree_anchor_from_cluster_entity(_scheme_entity(), Config())
        assert anchor.role is None
        assert anchor.anchor_sheet == "Channel_1"

    def test_mount_baked_angle_is_none(self):
        """_mount_baked_angle_deg resolves via entity_anchor_identity -> None
        for a scheme_list Entity (no cell slot to read a baked angle from)."""
        assert _mount_baked_angle_deg(_scheme_entity(), Config()) is None


# ── anchor_graph producer index ─────────────────────────────────────────────

def test_producer_index_omits_scheme_list_entity_without_crashing():
    """A scheme_list Entity is a record (kind "placement") in the anchor graph
    but PRODUCES no board component roles (its literal copper/refs are not the
    role/cluster producer machinery), and its cell=None must not crash the
    index build."""
    cfg = Config(entities=[_scheme_entity(name="S1")])
    recs = build_records(cfg)
    assert any(r.kind == "placement" and r.name == "S1" for r in recs)
    # No producer entry at all — the scheme_list Entity never becomes a parent.
    assert build_producer_index(cfg, recs) == {}


# ── Stage-4 canary: the Place-written Entity end to end ─────────────────────

def _write(path: Path, data: dict) -> None:
    path.write_text(dict_to_sexp(data), encoding="utf-8")


def test_place_written_entity_roundtrip_no_cell_none_pathology(tmp_path):
    """The Stage-4 canary: a Place-created scheme_list Entity + an appended
    top-level placement node (written through the Stage-1 core:
    build_scheme_list_entity + append_tree_child_node) survives load_config,
    link_trees resolves the node to the real Entity record (never a cell=None
    clone), materialize_entity_placements emits nothing for it, and
    check_entity_cells_exist passes (plan P4 canary (b)). Reaching the P4
    scheme_list branch itself is pinned in test_scheme_list_apply.py."""
    root = tmp_path / "root.sexp"
    _write(root, {
        "scheme_lists": [_scheme_record_dict()],
        "entities": [],
        "trees": [{"name": "main", "anchor": {"origin": True}, "nodes": []}],
    })

    upsert_entity(root, build_scheme_list_entity("SL1", "psu", sheet="Channel_1"))
    append_tree_child_node(root, "main", None,
                           _placement_node("SL1", 5.0, 6.0, rotation=90.0))

    cfg, _ = load_config(str(root))
    ent = next(e for e in cfg.entities if e.name == "SL1")
    assert ent.cell is None
    assert ent.scheme_list == "psu"
    assert ent.sheet == "Channel_1"

    linked = link_trees(cfg, cfg.trees)[0]
    assert [ln.node.ref for ln in linked.nodes] == ["SL1"]
    assert linked.nodes[0].record is not None
    assert linked.nodes[0].record.kind == "placement"
    assert linked.nodes[0].record.name == "SL1"

    # Canary (a): the cell path must NOT materialize the scheme_list node —
    # materialize_entity_placements returns zero clones (no ClonePlacement(cell=None)).
    clones = materialize_entity_placements(None, cfg, {})
    assert clones == []
    assert all(c.cell is not None for c in clones)  # vacuously true

    # Canary (b): the structural entity/cell check passes for the scheme_list Entity.
    check_entity_cells_exist(cfg)
