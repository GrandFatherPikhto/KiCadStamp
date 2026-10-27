# tests/placement/test_registry_anchor_prefix_protection.py
"""У5.1 (plan §6 + Д2 of plan_2026_10_08_remove_placer): every anchor_id a run
can produce — AND every LEGACY key form a registry ON DISK may still carry —
must have its prefix in the ONE protection list
`registry.PROTECTED_ANCHOR_PREFIXES`, so `reconcile()` never prunes an
--only-filtered record's copper.

Regression: `point:` was missing from the inline tuple (found 2026-10-04) — a
Point-anchored clone_placement lost its vias/tracks whenever another record was
selected with --only.

2026-10-13 (Д2): the clone builder no longer PRODUCES `anchor:` / `role:` /
`point:` keys — the retired `clone_placements:` section was their only source,
and `clone_anchor_id` now has the `name:` branch alone. The prefixes STAY in the
list (Denis, 12.10: registries on disk may hold those keys, and
`registry_upgrade._map_anchor_id` still reads them), so the parametrized cell
below covers them as LITERAL keys. A cell that built them through the builder
would from now on assert a form no product path can create.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from kicadstamp.config import ClonePlacement, Entity
from kicadstamp.net_trace_planner import net_trace_anchor_id
from kicadstamp.placement.services.clone_position_calculator import (
    clone_anchor_id,
    entity_anchor_id,
    nested_anchor_id,
)
from kicadstamp.placement.services.via_planner import thermal_anchor_id
from kicadstamp.registry import (
    PROTECTED_ANCHOR_PREFIXES,
    PlacementRegistry,
    RegistryEntry,
    make_registry_key,
)
from tests.fakes.format3 import det_uuid


def _one_clone():
    # A record without a uuid is refused under the gate (Р-У5.7) — the only form
    # the builder still produces is the name: fallback, so the identity IS the
    # thing to supply.
    return clone_anchor_id(ClonePlacement(
        cluster="c", xy=(0.0, 0.0), cell="c",
        uuid=det_uuid("clone_placements:c")))


def _protected_anchor_ids():
    """One anchor_id per prefix a /--only run must protect: LIVE builder forms,
    then the LEGACY literal keys a registry on disk may still carry."""
    yield _one_clone()                                          # name: (fallback)
    yield entity_anchor_id(Entity(name="E", cell="c",
                                  uuid=det_uuid("entities:E")))  # name: (entity)
    yield thermal_anchor_id(SimpleNamespace(                     # thermal:
        name="q1_thermal", uuid=det_uuid("thermal_via_arrays:q1_thermal")))
    yield net_trace_anchor_id(SimpleNamespace(                   # net:
        name="nt", net="N", uuid=det_uuid("net_traces:nt")))
    # nested cell placement — "<outer>/<nested.name>"; the "/" must not change
    # the protected prefix (У5.1б). A slash is legal in a cluster name (Р7).
    yield nested_anchor_id(
        entity_anchor_id(Entity(name="E", cell="c", uuid=det_uuid("entities:E"))),
        "inner")                                                                # name: + "/"
    # LEGACY (retired clone_placements: physical-binding forms, Д2) — literal.
    yield "anchor:IC1::0.0000:0.0000"                            # anchor:
    yield "role:R::0.0000:0.0000"                                # role:
    yield "point:Origin:4.0000:-110.0000"                        # point:
    yield "role:R::0.0000:0.0000/inner"                          # role: + "/"


_ANCHOR_IDS = list(_protected_anchor_ids())
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
    # The copper IS on the board: prune names a deletion only for an item the
    # board still carries (Ч1 of plan_2026_10_27_orphan_anchor_keys_own_nothing —
    # a uuid the board does not carry is dropped together with its key).
    _to_create, to_delete = reg.reconcile(
        [], known_anchor_ids=known, live_items=[SimpleNamespace(uuid="u-1")])
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
