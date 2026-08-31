# tests/test_profile_copy.py
"""Direct unit tests for kicadstamp/config/profile_copy.py — copying
Cell/Entity/Rule from another profile BY VALUE (plan_2026_08_31_copy_cell_
entity_from_profile.md): the closure of dependencies lands in the target, name
collisions are a clear refusal that leaves the target file untouched, and a
missing source/name/dependency is an explicit ValidationError. All fixtures
are synthetic .sexp/.json files (Denis's live profile is never touched)."""
import json
from pathlib import Path

import pytest

from kicadstamp.config import load_cell, load_entity, load_rule
from kicadstamp.config.profile_copy import copy_cell, copy_entity, copy_rule
from kicadstamp.config.sexp_format import dict_to_sexp, sexp_to_dict
from kicadstamp.exceptions import ValidationError


# ── helpers ────────────────────────────────────────────────────────────────

def _write_sexp(path: Path, data: dict) -> Path:
    path.write_text(dict_to_sexp(data), encoding="utf-8")
    return path


def _write_json(path: Path, data: dict) -> Path:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _read(path: Path) -> dict:
    if path.suffix == ".json":
        return json.loads(path.read_text(encoding="utf-8")) or {}
    return sexp_to_dict(path.read_text(encoding="utf-8")) or {}


def _make_source(tmp_path, extra: dict | None = None) -> Path:
    """A small, realistic source profile: a leaf cell, a composite cell two
    levels deep, an entity and a rule (with a chained point)."""
    data = {
        "layer": "F.Cu",
        "cells": {
            "leaf": {
                "layer": "F.Cu",
                "components": [{"role": "R1", "offset_along_mm": 1.0}],
                "vias": [{"offset_along_mm": 0.5, "drill_mm": 0.3}],
            },
            "mid": {
                "layer": "F.Cu",
                "clone_placements": [{"name": "n1", "cell": "leaf", "xy": [1.0, 2.0]}],
            },
            "top": {
                "layer": "F.Cu",
                "clone_placements": [{"name": "n2", "cell": "mid", "xy": [3.0, 4.0]}],
            },
        },
        "points": {
            "origin": {"anchor_origin": "grid"},
            "fpga_origin": {"anchor_point": "origin"},
        },
        "entities": [
            {"name": "E_FILTER", "cell": "top", "cluster": "CH0",
             "nets": {"R1": "/NET_A"}, "params": {"X": "1"}, "sheet": "Sheet_0"},
        ],
        "rules": [
            {"name": "rule_5v", "net": "+5V",
             "anchor_point": "fpga_origin",
             "spokes": [{"pad": "1", "cell": "top", "shift_x_mm": 1.0},
                        {"pad": "2", "cell": "mid", "shift_x_mm": 1.0}]},
        ],
    }
    if extra:
        # deep-merge only the given top-level keys (keeps the fixture readable)
        for key, value in extra.items():
            data[key] = value
    return _write_sexp(tmp_path / "source.sexp", data)


# ── copy_cell ──────────────────────────────────────────────────────────────

def test_copy_leaf_cell_roundtrip_identical(tmp_path):
    source = _make_source(tmp_path)
    target = _write_sexp(tmp_path / "target.sexp", {"cells": {}})

    copied = copy_cell(source, "leaf", target)

    assert copied == ["leaf"]
    target_data = _read(target)
    assert target_data["cells"]["leaf"] == _read(source)["cells"]["leaf"]
    # structurally valid: the copied raw dict passes the single-cell loader
    cell = load_cell("leaf", target_data["cells"]["leaf"])
    assert cell.name == "leaf"
    assert cell.components[0].role == "R1"


def test_copy_composite_cell_brings_whole_closure(tmp_path):
    source = _make_source(tmp_path)
    target = _write_sexp(tmp_path / "target.sexp", {"cells": {}})

    copied = copy_cell(source, "top", target)

    assert copied[0] == "top"
    assert set(copied) == {"top", "mid", "leaf"}
    target_cells = _read(target)["cells"]
    assert set(target_cells) == {"top", "mid", "leaf"}
    # nested reference survives and is resolvable
    assert target_cells["top"]["clone_placements"][0]["cell"] == "mid"
    assert target_cells["mid"]["clone_placements"][0]["cell"] == "leaf"


def test_copy_cell_from_included_source_file(tmp_path):
    """The cell can live in an INCLUDED file of the source graph, not only in
    the source root — resolve_includes() flattens the source before lookup."""
    _write_sexp(tmp_path / "sub.sexp", {"cells": {
        "hidden": {"layer": "F.Cu", "components": [{"role": "R9"}]}}})
    source = _write_sexp(tmp_path / "source.sexp", {"include": ["sub.sexp"]})
    target = _write_sexp(tmp_path / "target.sexp", {"cells": {}})

    copied = copy_cell(source, "hidden", target)

    assert copied == ["hidden"]
    assert "hidden" in _read(target)["cells"]


def test_copy_cell_collision_refuses_and_leaves_file_unchanged(tmp_path):
    source = _make_source(tmp_path)
    target = _write_sexp(tmp_path / "target.sexp", {"cells": {
        "leaf": {"layer": "F.Cu"}}})
    before = target.read_bytes()

    with pytest.raises(ValidationError) as exc:
        copy_cell(source, "leaf", target)

    assert "already exists" in str(exc.value)
    assert target.read_bytes() == before  # nothing written


def test_copy_cell_dependency_collision_refuses(tmp_path):
    """The ROOT cell is free but a dependency collides — still refused, and
    the root cell must NOT be half-written."""
    source = _make_source(tmp_path)
    target = _write_sexp(tmp_path / "target.sexp", {"cells": {
        "mid": {"layer": "F.Cu"}}})  # top is free, mid collides
    before = target.read_bytes()

    with pytest.raises(ValidationError) as exc:
        copy_cell(source, "top", target)

    assert "mid" in str(exc.value)
    assert target.read_bytes() == before


def test_copy_cell_collision_seen_across_target_include_graph(tmp_path):
    """The colliding cell lives in an INCLUDED file of the target, not in the
    target root itself — the collision check walks the whole target graph."""
    _write_sexp(tmp_path / "inc.sexp", {"cells": {"leaf": {"layer": "F.Cu"}}})
    target = _write_sexp(tmp_path / "target.sexp", {"include": ["inc.sexp"]})
    source = _make_source(tmp_path)
    before = target.read_bytes()

    with pytest.raises(ValidationError):
        copy_cell(source, "leaf", target)

    assert target.read_bytes() == before


def test_copy_cell_missing_name_is_explicit_error(tmp_path):
    source = _make_source(tmp_path)
    target = _write_sexp(tmp_path / "target.sexp", {"cells": {}})

    with pytest.raises(ValidationError) as exc:
        copy_cell(source, "nope", target)

    assert "not found" in str(exc.value)


def test_copy_cell_missing_dependency_in_source_is_explicit_error(tmp_path):
    source = _write_sexp(tmp_path / "source.sexp", {"cells": {
        "broken": {"layer": "F.Cu",
                   "clone_placements": [{"name": "x", "cell": "missing"}]}}})
    target = _write_sexp(tmp_path / "target.sexp", {"cells": {}})

    with pytest.raises(ValidationError) as exc:
        copy_cell(source, "broken", target)

    assert "missing" in str(exc.value)


def test_missing_source_file_is_explicit_error(tmp_path):
    target = _write_sexp(tmp_path / "target.sexp", {"cells": {}})

    with pytest.raises(ValidationError) as exc:
        copy_cell(tmp_path / "no_such.sexp", "leaf", target)

    assert "not found" in str(exc.value)


def test_json_source_works(tmp_path):
    source = _write_json(tmp_path / "source.json", {"cells": {
        "leaf": {"layer": "F.Cu", "components": [{"role": "R1"}]}}})
    target = _write_sexp(tmp_path / "target.sexp", {"cells": {}})

    copied = copy_cell(source, "leaf", target)

    assert copied == ["leaf"]
    assert _read(target)["cells"]["leaf"]["components"][0]["role"] == "R1"


# ── copy_entity ────────────────────────────────────────────────────────────

def test_copy_entity_copies_record_and_cell_closure(tmp_path):
    source = _make_source(tmp_path)
    target = _write_sexp(tmp_path / "target.sexp", {"cells": {}, "entities": []})

    copied = copy_entity(source, "E_FILTER", target)

    assert set(copied) == {"top", "mid", "leaf"}
    data = _read(target)
    # the record itself + the cell closure
    assert data["entities"] == [
        {"name": "E_FILTER", "cell": "top", "cluster": "CH0",
         "nets": {"R1": "/NET_A"}, "params": {"X": "1"}, "sheet": "Sheet_0"}]
    assert set(data["cells"]) == {"top", "mid", "leaf"}
    # structurally valid
    entity = load_entity(data["entities"][0])
    assert entity.cell == "top"
    assert entity.nets == {"R1": "/NET_A"}


def test_copy_entity_collision_refuses_and_leaves_file_unchanged(tmp_path):
    source = _make_source(tmp_path)
    target = _write_sexp(tmp_path / "target.sexp", {
        "cells": {}, "entities": [{"name": "E_FILTER", "cell": "other"}]})
    before = target.read_bytes()

    with pytest.raises(ValidationError) as exc:
        copy_entity(source, "E_FILTER", target)

    assert "already exists" in str(exc.value)
    assert target.read_bytes() == before  # neither entity nor closure written


def test_copy_entity_missing_name_is_explicit_error(tmp_path):
    source = _make_source(tmp_path)
    target = _write_sexp(tmp_path / "target.sexp", {"entities": []})

    with pytest.raises(ValidationError) as exc:
        copy_entity(source, "nope", target)

    assert "not found" in str(exc.value)


# ── copy_rule ──────────────────────────────────────────────────────────────

def test_copy_rule_copies_rule_cells_and_points(tmp_path):
    source = _make_source(tmp_path)
    target = _write_sexp(tmp_path / "target.sexp", {
        "cells": {}, "points": {}, "rules": []})

    copied = copy_rule(source, "rule_5v", target)

    assert set(copied) == {"top", "mid", "leaf", "fpga_origin", "origin"}
    data = _read(target)
    assert data["rules"][0]["name"] == "rule_5v"
    assert data["rules"][0]["anchor_point"] == "fpga_origin"
    assert set(data["cells"]) == {"top", "mid", "leaf"}
    assert set(data["points"]) == {"fpga_origin", "origin"}  # chained point copied
    rule = load_rule(data["rules"][0])
    assert rule.spokes[0].cell == "top"
    assert rule.spokes[1].cell == "mid"


def test_copy_rule_matches_by_net_fallback(tmp_path):
    """A rule WITHOUT name: is matched by its net: identity (rule_effective_
    name's fallback) — the same identity used for the collision check."""
    _write_sexp(tmp_path / "sub.sexp", {"cells": {"c1": {"layer": "F.Cu"}}})
    source = _write_sexp(tmp_path / "source.sexp", {
        "cells": {"c1": {"layer": "F.Cu", "components": [{"role": "R1"}]}},
        "rules": [{"net": "+3V3", "anchor_role": "FPGA",
                   "spokes": [{"pad": "1", "cell": "c1"}]}]})
    target = _write_sexp(tmp_path / "target.sexp", {"cells": {}, "rules": []})

    copied = copy_rule(source, "+3V3", target)

    assert copied == ["c1"]
    assert _read(target)["rules"][0]["net"] == "+3V3"


def test_copy_rule_collision_refuses_and_leaves_file_unchanged(tmp_path):
    source = _make_source(tmp_path)
    target = _write_sexp(tmp_path / "target.sexp", {
        "cells": {}, "points": {}, "rules": [{"name": "rule_5v", "net": "+5V"}]})
    before = target.read_bytes()

    with pytest.raises(ValidationError) as exc:
        copy_rule(source, "rule_5v", target)

    assert "already exists" in str(exc.value)
    assert target.read_bytes() == before


def test_copy_rule_missing_identity_is_explicit_error(tmp_path):
    source = _make_source(tmp_path)
    target = _write_sexp(tmp_path / "target.sexp", {"rules": []})

    with pytest.raises(ValidationError) as exc:
        copy_rule(source, "no_such_net", target)

    assert "not found" in str(exc.value)
