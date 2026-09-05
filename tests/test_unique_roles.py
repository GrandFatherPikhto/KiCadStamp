#!/usr/bin/env python3
"""Role uniqueness within a cell (config/loader.py, YAML loading)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from kicadstamp.config import _load_cell
from kicadstamp.exceptions import ValidationError


def test_unique_roles_load_fine():
    cell = _load_cell("t", {"components": [{"role": "LIGHT"}, {"role": "HEAVY"}]})
    assert len(cell.components) == 2
    assert {c.role for c in cell.components} == {"LIGHT", "HEAVY"}


def test_duplicate_role_raises_validation_error():
    with pytest.raises(ValidationError, match="LIGHT"):
        _load_cell("t", {"components": [{"role": "LIGHT"}, {"role": "LIGHT"}]})


def test_three_duplicates_all_named_in_error():
    """Several distinct duplicates at once — all must land in one message."""
    with pytest.raises(ValidationError) as exc_info:
        _load_cell("t", {"components": [
            {"role": "A"}, {"role": "A"}, {"role": "B"}, {"role": "B"}, {"role": "C"},
        ]})
    msg = str(exc_info.value)
    assert "'A'" in msg and "'B'" in msg and "'C'" not in msg  # C is not a duplicate — must not be named


def test_single_role_no_duplicates():
    cell = _load_cell("t", {"components": [{"role": "SOLO"}]})
    assert len(cell.components) == 1


class TestRoleRequired:
    """Regression (found live 2026-08-06, Denis: Conn_PM5V): a missing/null
    role used to either crash with a bare KeyError or silently propagate a
    None role into placement, surfacing as a confusing runtime "role None is
    in cell but not found anywhere on board" instead of a clear load-time
    error."""

    def test_missing_role_key_raises_validation_error(self):
        with pytest.raises(ValidationError, match="without a role"):
            _load_cell("t", {"components": [{"offset_along_mm": 1.0}]})

    def test_null_role_raises_validation_error(self):
        with pytest.raises(ValidationError, match="without a role"):
            _load_cell("t", {"components": [{"role": None}]})

    def test_empty_string_role_raises_validation_error(self):
        with pytest.raises(ValidationError, match="without a role"):
            _load_cell("t", {"components": [{"role": ""}]})


class TestCellAnchor:
    """anchor_xy/anchor_role/anchor_pad — the cell's mount point A + identity
    (design_2026_09_05 v2), see Cell's own docstring in config/models.py.
    anchor_xy may coexist with anchor_role (point + identity); a role-only
    anchor_xy must match the role's centre. Validated here for shape and
    cross-reference correctness."""

    def test_anchor_xy_loads(self):
        cell = _load_cell("t", {"components": [{"role": "A"}], "anchor_xy": [1.5, -2.0]})
        assert cell.anchor_xy == (1.5, -2.0)
        assert cell.anchor_role is None

    def test_anchor_role_loads(self):
        cell = _load_cell("t", {"components": [{"role": "A"}], "anchor_role": "A"})
        assert cell.anchor_role == "A"
        assert cell.anchor_xy is None

    def test_anchor_role_with_pad_loads(self):
        cell = _load_cell("t", {"components": [{"role": "A"}],
                                "anchor_role": "A", "anchor_pad": "1"})
        assert cell.anchor_role == "A"
        assert cell.anchor_pad == "1"

    def test_no_anchor_at_all_is_fine(self):
        cell = _load_cell("t", {"components": [{"role": "A"}]})
        assert cell.anchor_xy is None
        assert cell.anchor_role is None
        assert cell.anchor_pad is None

    def test_anchor_xy_and_anchor_role_together_loads_when_consistent(self):
        """v2: anchor_xy (the mount point) may carry anchor_role as its identity
        — a role-only anchor must agree with the role's centre."""
        cell = _load_cell("t", {"components": [{"role": "A"}],
                                "anchor_xy": [0, 0], "anchor_role": "A"})
        assert cell.anchor_xy == (0.0, 0.0)
        assert cell.anchor_role == "A"

    def test_anchor_xy_mismatching_role_centre_is_fatal(self):
        with pytest.raises(ValidationError, match="does not match anchor_role"):
            _load_cell("t", {"components": [{"role": "A", "offset_along_mm": 2.0,
                                             "offset_across_mm": 1.0}],
                             "anchor_xy": [0, 0], "anchor_role": "A"})

    def test_anchor_pad_without_anchor_role_is_fatal(self):
        with pytest.raises(ValidationError, match="anchor_pad without anchor_role"):
            _load_cell("t", {"components": [{"role": "A"}], "anchor_pad": "1"})

    def test_anchor_role_not_a_component_is_fatal(self):
        with pytest.raises(ValidationError, match="not a component of cell"):
            _load_cell("t", {"components": [{"role": "A"}], "anchor_role": "NOT_HERE"})

    def test_anchor_xy_wrong_shape_is_fatal(self):
        with pytest.raises(ValidationError, match="2-element"):
            _load_cell("t", {"components": [{"role": "A"}], "anchor_xy": [1.0]})
