# tests/placement/test_nested_anchor_ids_protection.py
"""У5.1б (plan §6, accept note 04.10): the anchor_id of a NESTED cell placement
is "<outer>/<nested.name>", and reconcile() matches an anchor_id WHOLE. Only the
outer id used to land in known_anchor_ids, so any --only run that excluded the
placement pruned the nested cell's copper too (deleting board copper the user
never selected — the same class as the point: bug of У5.1). A product bug of
today's format: fixed WITHOUT the format-3 gate.

Fixed by adding every nested id to apply_pipeline._compute_all_anchor_ids via
the ONE builder clone_position_calculator.anchor_ids_with_nested (which composes
"<outer>/<name>" — never a key split on "/", because a slash is legal in a
cluster name, Р7).
"""
from unittest.mock import MagicMock

from kicadstamp.apply_pipeline import _compute_all_anchor_ids
from kicadstamp.config.models import (
    Cell,
    CellPlacement,
    ClonePlacement,
    Config,
    Entity,
)
from kicadstamp.placement.services.clone_position_calculator import (
    anchor_ids_with_nested,
    nested_anchor_id,
)
from kicadstamp.registry import (
    PlacementRegistry,
    RegistryEntry,
    make_registry_key,
)


def _cells(*, nested: bool = True) -> dict:
    """cells with one composite 'outer' cell that nests 'inner'."""
    return {
        "inner": Cell(name="inner"),
        "outer": Cell(
            name="outer",
            clone_placements=[CellPlacement(name="inner", cell="inner")]
            if nested else [],
        ),
    }


def _clone_cfg(*, nested: bool = True) -> Config:
    clone = ClonePlacement(cluster="c", xy=(0.0, 0.0), cell="outer")
    return Config(cells=_cells(nested=nested), clone_placements=[clone])


def _entity_cfg(*, nested: bool = True) -> Config:
    return Config(cells=_cells(nested=nested), entities=[Entity(name="E", cell="outer")])


def _seed_and_reconcile(anchor_id, known, tmp_path, template="inner"):
    reg = PlacementRegistry(MagicMock(), str(tmp_path / "reg.json"))
    key = make_registry_key(anchor_id, template, None, 0)
    reg.entries[key] = RegistryEntry(uuid="u-1", x_mm=1.0, y_mm=2.0, net="GND",
                                     drill_mm=0.3, diameter_mm=0.6)
    _to_create, to_delete = reg.reconcile([], known_anchor_ids=known,
                                          live_items=[])
    return reg, key, to_delete


# ── the builder ───────────────────────────────────────────────────────────────

def test_nested_anchor_id_composes_by_construction_not_by_splitting():
    assert nested_anchor_id("name:c", "inner") == "name:c/inner"
    # a slash in the OUTER id (legal in a cluster name, Р7) is preserved
    assert nested_anchor_id("name:FPGA_PWR_BANK/VCCIO/139", "inner") == \
        "name:FPGA_PWR_BANK/VCCIO/139/inner"


def test_anchor_ids_with_nested_walks_every_level():
    cells = {
        "outer": Cell(name="outer",
                      clone_placements=[CellPlacement(name="mid", cell="mid")]),
        "mid": Cell(name="mid",
                    clone_placements=[CellPlacement(name="leaf", cell="leaf")]),
        "leaf": Cell(name="leaf"),
    }
    assert anchor_ids_with_nested("name:c", "outer", cells) == {
        "name:c", "name:c/mid", "name:c/mid/leaf"}


def test_anchor_ids_with_nested_diamond_keeps_both_branches():
    """The SAME cell reached through two SIBLING branches is a diamond, not a
    cycle: both branches yield their own, DIFFERENT nested ids."""
    cells = {
        "outer": Cell(name="outer", clone_placements=[
            CellPlacement(name="b", cell="b"), CellPlacement(name="c", cell="c")]),
        "b": Cell(name="b", clone_placements=[CellPlacement(name="d", cell="d")]),
        "c": Cell(name="c", clone_placements=[CellPlacement(name="d", cell="d")]),
        "d": Cell(name="d"),
    }
    ids = anchor_ids_with_nested("name:x", "outer", cells)
    assert {"name:x/b/d", "name:x/c/d"} <= ids


def test_anchor_ids_with_nested_cycle_is_skipped_not_raised():
    """An in-memory cycle must not hang or raise here — load_config rejects one
    (check_no_cell_definition_cycles), and the protection set only has to be a
    superset."""
    cells = {
        "a": Cell(name="a", clone_placements=[CellPlacement(name="b", cell="b")]),
        "b": Cell(name="b", clone_placements=[CellPlacement(name="a", cell="a")]),
    }
    ids = anchor_ids_with_nested("name:c", "a", cells)
    assert "name:c/b" in ids and "name:c/b/a" in ids


# ── _compute_all_anchor_ids ───────────────────────────────────────────────────

def test_compute_all_anchor_ids_includes_a_clone_nested_id():
    assert {"name:c", "name:c/inner"} <= _compute_all_anchor_ids(_clone_cfg())


def test_compute_all_anchor_ids_includes_an_entity_cell_nested_id():
    assert {"name:E", "name:E/inner"} <= _compute_all_anchor_ids(_entity_cfg())


def test_compute_all_anchor_ids_slash_in_cluster_is_preserved():
    """A clone whose identity itself contains a slash (cluster name, Р7)."""
    clone = ClonePlacement(cluster="BANK/VCCIO/139", xy=(0.0, 0.0), cell="outer")
    ids = _compute_all_anchor_ids(Config(cells=_cells(), clone_placements=[clone]))
    assert "name:BANK/VCCIO/139/inner" in ids


# ── the behaviour: nested copper survives --only, stale nested copper does not ─

def test_nested_anchor_is_protected_from_prune(tmp_path):
    """Red on base: the nested key vanished from known_anchor_ids, so an --only
    run that excluded the placement pruned it (to_delete == ['u-1'])."""
    known = _compute_all_anchor_ids(_clone_cfg())
    anchor_id = "name:c/inner"
    assert anchor_id in known
    reg, key, to_delete = _seed_and_reconcile(anchor_id, known, tmp_path)
    assert to_delete == []
    assert key in reg.entries


def test_entity_cell_nested_anchor_is_protected_from_prune(tmp_path):
    known = _compute_all_anchor_ids(_entity_cfg())
    anchor_id = "name:E/inner"
    assert anchor_id in known
    reg, key, to_delete = _seed_and_reconcile(anchor_id, known, tmp_path)
    assert to_delete == []
    assert key in reg.entries


def test_nested_anchor_is_pruned_when_no_longer_in_the_cell(tmp_path):
    """Boundary: the fix must NOT protect a nested id that no longer exists in
    the cell — the placement really shrank, so its old copper is stale."""
    known = _compute_all_anchor_ids(_clone_cfg(nested=False))
    reg, key, to_delete = _seed_and_reconcile("name:c/inner", known, tmp_path)
    assert to_delete == ["u-1"]
    assert key not in reg.entries
