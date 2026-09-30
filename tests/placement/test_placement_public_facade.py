"""Tests for the public facade of the placement domain.

``kicadstamp/placement/__init__.py`` now re-exports the functions external
consumers actually use, so future callers (notably Phase 2 of the auto-nets
plan) have a sanctioned door into the domain instead of importing
``placement.services.<module>.<name>`` directly (architecture review
2026-08-28, revisit; plan_2026_08_28_placement_public_facade.md).

These tests assert IDENTITY (not copies): the facade must not wrap or re-bind
the underlying functions, only add an import path. The private name
``_anchor_offset_mm`` is re-exported under the public alias ``anchor_offset_mm``
— the alias must be the very same function object.
"""

import pytest

import kicadstamp.placement as placement
import kicadstamp.placement.services.board_items_resolver as board_items_resolver
import kicadstamp.placement.services.clone_position_calculator as clone_position_calculator
import kicadstamp.placement.services.clone_role_resolver as clone_role_resolver
import kicadstamp.placement.services.coordinate_position_calculator as coordinate_position_calculator
import kicadstamp.placement.services.point_resolver as point_resolver

# facade name -> the ORIGINAL object in its source module.
_ORIGINS = {
    "resolve_roles_by_nets": clone_role_resolver.resolve_roles_by_nets,
    "resolve_roles_by_selection": clone_role_resolver.resolve_roles_by_selection,
    "clone_uses_selection_mode": clone_role_resolver.clone_uses_selection_mode,
    "suggest_role_nets_from_cluster": clone_role_resolver.suggest_role_nets_from_cluster,
    "resolve_single_role_candidate": clone_role_resolver.resolve_single_role_candidate,
    "clone_anchor_id": clone_position_calculator.clone_anchor_id,
    "resolve_clone_board_items": board_items_resolver.resolve_clone_board_items,
    "clone_world_origin": board_items_resolver.clone_world_origin,
    "resolve_point_chain": point_resolver.resolve_point_chain,
    "anchor_offset_mm": coordinate_position_calculator._anchor_offset_mm,
}

_FACADE_NAMES = list(_ORIGINS)


@pytest.mark.parametrize("name", _FACADE_NAMES)
def test_facade_name_is_the_same_object_as_origin(name):
    """The facade must add an import path, never a copy."""
    assert getattr(placement, name) is _ORIGINS[name], (
        f"kicadstamp.placement.{name} must be the same object as its source "
        f"{_ORIGINS[name].__module__}.{name}"
    )


@pytest.mark.parametrize("name", _FACADE_NAMES)
def test_facade_name_is_listed_in_all(name):
    """Every new public name must be part of the documented public API."""
    assert name in placement.__all__, f"{name} must be listed in placement.__all__"


def test_existing_facade_names_still_exported():
    """The facade is additive — the pre-existing names must keep working."""
    for name in ("BatchExecutor", "PlacementPlanner", "MoveCommand",
                 "ViaCommand", "PlacedComponentInfo"):
        assert name in placement.__all__
        assert hasattr(placement, name)
