# tests/placement/test_registry_anchor_prefix_protection.py
"""У5.1 (plan §6): every anchor_id form a builder produces must be in the ONE
protection list `registry.PROTECTED_ANCHOR_PREFIXES`, so `reconcile()` never
prunes an --only-filtered record's copper.

Regression: `point:` was missing from the inline tuple (found 2026-10-04) — a
Point-anchored clone_placement (clone_anchor_id's FIRST branch since 2026-08-06)
lost its vias/tracks whenever another record was selected with --only. The
parametrized cell below is the linkage: a builder that starts producing a NEW
prefix fails it until the prefix is added to the list.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from kicadstamp.config import Chain, ClonePlacement, Entity, ManualSpoke
from kicadstamp.net_trace_planner import net_trace_anchor_id
from kicadstamp.placement.services.clone_position_calculator import (
    clone_anchor_id,
    entity_anchor_id,
    nested_anchor_id,
)
from kicadstamp.placement.services.manual_position_calculator import (
    chain_anchor_ids,
)
from kicadstamp.placement.services.via_planner import thermal_anchor_id
from kicadstamp.registry import (
    PROTECTED_ANCHOR_PREFIXES,
    PlacementRegistry,
    RegistryEntry,
    make_registry_key,
)


def _one_clone(**anchor):
    return clone_anchor_id(ClonePlacement(cluster="c", xy=(0.0, 0.0), cell="c",
                                          **anchor))


def _builder_anchor_ids():
    """One anchor_id per builder branch that a /--only run must protect."""
    yield _one_clone()                                          # name: (fallback)
    yield _one_clone(anchor_ref="IC1")                          # anchor:
    yield _one_clone(anchor_role="R")                           # role:
    yield _one_clone(anchor_point="P")                          # point:
    yield entity_anchor_id(Entity(name="E", cell="c"))          # name: (entity)
    yield thermal_anchor_id(SimpleNamespace(name="q1_thermal"))  # thermal:
    yield net_trace_anchor_id(SimpleNamespace(name="nt", net="N"))  # net:
    yield next(iter(chain_anchor_ids(                           # pad: (spoke)
        Chain(net="N", spokes=[ManualSpoke(pad="17", cell="c")]))))
    # nested cell placement — "<outer>/<nested.name>"; the "/" must not change
    # the protected prefix (У5.1б). A slash is legal in a cluster name (Р7).
    yield nested_anchor_id(_one_clone(anchor_role="R"), "inner")           # role: + "/"
    yield nested_anchor_id(entity_anchor_id(Entity(name="E", cell="c")), "inner")  # name: + "/"


_ANCHOR_IDS = list(_builder_anchor_ids())
_IDS = [f"{i}-{a.split(':', 1)[0]}" for i, a in enumerate(_ANCHOR_IDS)]


@pytest.mark.parametrize("anchor_id", _ANCHOR_IDS, ids=_IDS)
def test_every_builder_prefix_is_protected(anchor_id):
    prefix = anchor_id.split(":", 1)[0] + ":"
    assert prefix in PROTECTED_ANCHOR_PREFIXES, (
        f"anchor_id {anchor_id!r} uses {prefix!r}, which reconcile() does not "
        f"protect from an --only prune (PROTECTED_ANCHOR_PREFIXES="
        f"{PROTECTED_ANCHOR_PREFIXES})")


def _registry(tmp_path, live_items=None):
    return PlacementRegistry(MagicMock(), str(tmp_path / "reg.json"))


def _entry(uuid):
    return RegistryEntry(uuid=uuid, x_mm=1.0, y_mm=2.0, net="GND",
                         drill_mm=0.3, diameter_mm=0.6)


def _reconcile_one(anchor_id, known, tmp_path):
    reg = _registry(tmp_path)
    key = make_registry_key(anchor_id, "cell", None, 0)
    reg.entries[key] = _entry("u-1")
    _to_create, to_delete = reg.reconcile([], known_anchor_ids=known,
                                          live_items=[])
    return reg, key, to_delete


def test_point_anchor_is_protected_from_prune(tmp_path):
    """The У5.1 regression: a `point:` anchor with its id in known_anchor_ids
    must NOT be pruned (before the fix it was, deleting real board copper)."""
    anchor_id = "point:Origin:4.0000:-110.0000"
    reg, key, to_delete = _reconcile_one(anchor_id, {anchor_id}, tmp_path)
    assert to_delete == []
    assert key in reg.entries


def test_role_anchor_is_protected_from_prune(tmp_path):
    """Control: the long-working `role:` form stays protected (same code path)."""
    anchor_id = "role:CONN_PM5V::Conn_PM5V:1:3.0000:0.0000"
    reg, key, to_delete = _reconcile_one(anchor_id, {anchor_id}, tmp_path)
    assert to_delete == []
    assert key in reg.entries


def test_point_anchor_is_pruned_when_not_in_known_anchor_ids(tmp_path):
    """The fix must not protect unconditionally: a `point:` anchor whose record
    is really gone (not in known_anchor_ids) is still pruned."""
    anchor_id = "point:Origin:4.0000:-110.0000"
    reg, key, to_delete = _reconcile_one(anchor_id, set(), tmp_path)
    assert to_delete == ["u-1"]
    assert key not in reg.entries
