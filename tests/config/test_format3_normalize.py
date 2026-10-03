# tests/config/test_format3_normalize.py
"""У2 cells — UUID resolution in ONE point (the loader), plan §4.

Rule 35 table: reference FORM x {honest hint / lying hint (missing) / lying
hint (another existing record) / no UUID -> fatal}. The reference forms are the
ones `loader._f3_refs` knows (the SAME description drives the У1.3 dangling
check and the loader's normalization, so a form cannot be covered by one and
missed by the other); every form is one parametrized row. A reference that does
NOT resolve by UUID (a lying hint, У2.3) must land on the target the UUID names,
because the loader overwrites the name field before any consumer reads it.

Only tests pin the build to format 3 (`format3`); the product still refuses a
format-3 file, so everything here sleeps outside these cells.
"""
import json
from pathlib import Path

import pytest

from kicadstamp.config.includes import _load_config_file
from kicadstamp.config.loader import _f3_refs, load_config
from kicadstamp.config.sexp_format import dict_to_sexp, sexp_to_dict
from kicadstamp.config_writer import _read_data
from kicadstamp.exceptions import ValidationError
from kicadstamp.utils.file_cache import cached_file_read
from tests.fakes.format3 import det_uuid, format3  # noqa: F401 (fixture import)

# The two records per section: TARGET is named by the UUID, DECOY is what a
# LYING hint claims to be (У2.3(б) — the main case: a rename reached one file
# and not the other). MISSING is a hint naming no record at all (У2.3(а)).
TARGET = "A"
DECOY = "B"
MISSING = "zz-no-such-record"

POINT_P = det_uuid("points:p")


def _u(key: str) -> str:
    return det_uuid(key)


def _cells() -> dict:
    return {TARGET: {"layer": "B.Cu", "uuid": _u("cell:A")},
            DECOY: {"layer": "B.Cu", "uuid": _u("cell:B")}}


def _points() -> dict:
    return {TARGET: {"anchor_ref": "IC1", "uuid": _u("point:A")},
            DECOY: {"anchor_ref": "IC2", "uuid": _u("point:B")}}


# ── one builder per reference form: (data, read the resolved name) ─────────

def _f_entity_cell(hint):
    return ({"cells": _cells(),
             "entities": [{"name": "E", "cell": hint, "cell_uuid": _u("cell:A"),
                           "uuid": _u("entity:E")}]},
            lambda cfg: cfg.entities[0].cell)


def _f_entity_imprint(hint):
    return ({"imprints": [{"name": TARGET, "uuid": _u("imp:A"),
                           "components": [{"ref": "C1"}]},
                          {"name": DECOY, "uuid": _u("imp:B"),
                           "components": [{"ref": "C2"}]}],
             "entities": [{"name": "E", "imprint": hint,
                           "imprint_uuid": _u("imp:A"), "uuid": _u("entity:E")}]},
            lambda cfg: cfg.entities[0].imprint)


def _f_clone_cell(hint):
    return ({"cells": _cells(),
             "clone_placements": [{"cluster": "K", "cell": hint,
                                   "cell_uuid": _u("cell:A"), "xy": [0.0, 0.0],
                                   "name": "cp", "uuid": _u("cp:1")}]},
            lambda cfg: cfg.clone_placements[0].cell)


def _f_clone_anchor_point(hint):
    return ({"cells": _cells(), "points": _points(),
             "clone_placements": [{"cluster": "K", "cell": TARGET,
                                   "cell_uuid": _u("cell:A"),
                                   "anchor_point": hint,
                                   "anchor_point_uuid": _u("point:A"),
                                   "name": "cp", "uuid": _u("cp:1")}]},
            lambda cfg: cfg.clone_placements[0].anchor_point)


def _f_chain_anchor_point(hint):
    return ({"points": _points(),
             "chains": [{"net": "GND", "name": "ch", "anchor_point": hint,
                         "anchor_point_uuid": _u("point:A"),
                         "uuid": _u("chain:ch"), "spokes": []}]},
            lambda cfg: cfg.chains[0].anchor_point)


def _f_chain_spoke_cell(hint):
    return ({"cells": _cells(),
             "chains": [{"net": "GND", "name": "ch", "anchor_ref": "IC1",
                         "uuid": _u("chain:ch"),
                         "spokes": [{"pad": "1", "cell": hint,
                                     "cell_uuid": _u("cell:A")}]}]},
            lambda cfg: cfg.chains[0].spokes[0].cell)


def _f_coordinate_anchor_point(hint):
    return ({"points": _points(),
             "coordinate_placements": [{"name": "c", "cluster": "CL", "role": "R",
                                        "anchor_point": hint,
                                        "anchor_point_uuid": _u("point:A"),
                                        "uuid": _u("coord:c")}]},
            lambda cfg: cfg.coordinate_placements[0].anchor_point)


def _f_tva_anchor_point(hint):
    return ({"points": _points(),
             "thermal_via_arrays": [{"name": "tva", "anchor_point": hint,
                                     "anchor_point_uuid": _u("point:A"),
                                     "uuid": _u("tva:1")}]},
            lambda cfg: cfg.thermal_via_arrays[0].anchor_point)


def _f_point_anchor_point(hint):
    return ({"points": {**_points(),
                        "C": {"anchor_point": hint,
                              "anchor_point_uuid": _u("point:A"),
                              "uuid": _u("point:C")}}},
            lambda cfg: cfg.points["C"].anchor_point)


def _f_cell_nested_clone_cell(hint):
    cells = _cells()
    cells["parent"] = {"layer": "B.Cu", "uuid": _u("cell:parent"),
                       "clone_placements": [{"cluster": "K", "cell": hint,
                                             "cell_uuid": _u("cell:A"),
                                             "xy": [0.0, 0.0], "name": "n1"}]}
    return ({"cells": cells},
            lambda cfg: cfg.cells["parent"].clone_placements[0].cell)


def _f_tree_node_ref(hint):
    return ({"cells": _cells(),
             "entities": [{"name": TARGET, "cell": TARGET,
                           "cell_uuid": _u("cell:A"), "uuid": _u("entity:A")},
                          {"name": DECOY, "cell": TARGET,
                           "cell_uuid": _u("cell:A"), "uuid": _u("entity:B")}],
             "trees": [{"name": "t", "anchor": {"origin": True},
                        "nodes": [{"ref": hint, "kind": "placement",
                                   "ref_uuid": _u("entity:A"),
                                   "xy": [0.0, 0.0]}]}]},
            lambda cfg: cfg.trees[0].nodes[0].ref)


def _f_tree_anchor_point(hint):
    return ({"points": _points(),
             "trees": [{"name": "t",
                        "anchor": {"point": hint,
                                   "point_uuid": _u("point:A")}}]},
            lambda cfg: cfg.trees[0].anchor.point)


def _f_tree_instance_anchor_point(hint):
    return ({"points": _points(),
             "trees": [{"name": "tpl", "anchor": {"origin": True}}],
             "tree_instances": [{"template": "tpl", "name": "inst", "sheet": "S",
                                 "anchor": {"point": hint,
                                            "point_uuid": _u("point:A")}}]},
            lambda cfg: cfg.tree_instances[0].anchor["point"])


def _f_sheet_clone_cell(hint):
    return ({"cells": _cells(),
             "sheet_templates": {"tpl": {
                 "uuid": _u("tpl:clonecell"),
                 "sheets": ["S"],
                 "clone_placements": [{"cluster": "K", "cell": hint,
                                       "cell_uuid": _u("cell:A"),
                                       "xy": [0.0, 0.0]}]}}},
            lambda cfg: cfg.clone_placements[0].cell)


def _f_sheet_clone_anchor_point(hint):
    return ({"cells": _cells(), "points": _points(),
             "sheet_templates": {"tpl": {
                 "uuid": _u("tpl:cloneanchor"),
                 "sheets": ["S"],
                 "clone_placements": [{"cluster": "K", "cell": TARGET,
                                       "cell_uuid": _u("cell:A"),
                                       "anchor_point": hint,
                                       "anchor_point_uuid": _u("point:A")}]}}},
            lambda cfg: cfg.clone_placements[0].anchor_point)


def _f_sheet_coordinate_anchor_point(hint):
    return ({"points": _points(),
             "sheet_templates": {"tpl": {
                 "uuid": _u("tpl:coordanchor"),
                 "sheets": ["S"],
                 "coordinate_placements": [{"name": "c", "cluster": "CL",
                                            "role": "R", "anchor_point": hint,
                                            "anchor_point_uuid": _u("point:A")}]}}},
            lambda cfg: cfg.coordinate_placements[0].anchor_point)


_FORMS = {
    "entity_cell": _f_entity_cell,
    "entity_imprint": _f_entity_imprint,
    "clone_cell": _f_clone_cell,
    "clone_anchor_point": _f_clone_anchor_point,
    "chain_anchor_point": _f_chain_anchor_point,
    "chain_spoke_cell": _f_chain_spoke_cell,
    "coordinate_anchor_point": _f_coordinate_anchor_point,
    "tva_anchor_point": _f_tva_anchor_point,
    "point_anchor_point": _f_point_anchor_point,
    "cell_nested_clone_cell": _f_cell_nested_clone_cell,
    "tree_node_ref": _f_tree_node_ref,
    "tree_anchor_point": _f_tree_anchor_point,
    "tree_instance_anchor_point": _f_tree_instance_anchor_point,
    "sheet_clone_cell": _f_sheet_clone_cell,
    "sheet_clone_anchor_point": _f_sheet_clone_anchor_point,
    "sheet_coordinate_anchor_point": _f_sheet_coordinate_anchor_point,
}


def _write_sexp(path, data) -> None:
    path.write_text(dict_to_sexp(data, format_number=3), encoding="utf-8")


# ── У2.3: a lying hint never reaches a consumer; the UUID decides ──────────

@pytest.mark.parametrize("hint_kind", ["missing", "other"])
@pytest.mark.parametrize("form", sorted(_FORMS))
def test_reference_resolves_by_uuid_not_by_hint(format3, tmp_path, form, hint_kind):
    hint = MISSING if hint_kind == "missing" else DECOY
    data, read = _FORMS[form](hint)
    p = tmp_path / f"{form}-{hint_kind}.sexp"
    _write_sexp(p, data)
    cfg, _ = load_config(str(p))
    assert read(cfg) == TARGET


# ── У2.2: a format-3 reference without a UUID is a fatal ───────────────────

@pytest.mark.parametrize("form", sorted(_FORMS))
def test_reference_without_uuid_is_fatal(format3, tmp_path, form):
    data, _read = _FORMS[form](DECOY)
    refs = list(_f3_refs(data))
    assert refs, "the form's data must contain at least one reference"
    for ref in refs:
        ref.holder[ref.uuid_field] = None
    p = tmp_path / f"{form}-nouuid.sexp"
    _write_sexp(p, data)
    with pytest.raises(ValidationError) as e:
        load_config(str(p))
    assert "has no uuid" in str(e.value)


# ── У2.1.4: the tree anchor's (point …) carries a UUID ─────────────────────

def test_tree_anchor_point_uuid_roundtrips_s_expr(format3):
    data = {"points": {"p": {"anchor_ref": "IC1", "uuid": POINT_P}},
            "trees": [{"name": "t", "anchor": {"point": "p",
                                               "point_uuid": POINT_P}}]}
    text = dict_to_sexp(data, format_number=3)
    assert f'(uuid "{POINT_P}")' in text
    assert "point_uuid" not in text          # nested in s-expr, never a key
    assert sexp_to_dict(text) == data


def test_tree_anchor_point_uuid_loads_from_json(format3, tmp_path):
    data = {"points": {"p": {"anchor_ref": "IC1", "uuid": POINT_P}},
            "trees": [{"name": "t", "anchor": {"point": "p",
                                               "point_uuid": POINT_P}}]}
    p = tmp_path / "config.json"
    p.write_text(json.dumps({**data, "version": 3}), encoding="utf-8")
    cfg, _ = load_config(str(p))
    assert cfg.trees[0].anchor.point_uuid == POINT_P


def test_tree_anchor_point_dangling_uuid_is_fatal(format3, tmp_path):
    data = {"points": {"p": {"anchor_ref": "IC1", "uuid": POINT_P}},
            "trees": [{"name": "t", "anchor": {"point": "p",
                                               "point_uuid": det_uuid("nope")}}]}
    p = tmp_path / "config.sexp"
    _write_sexp(p, data)
    with pytest.raises(ValidationError) as e:
        load_config(str(p))
    assert "references uuid" in str(e.value)


def test_tree_anchor_point_rejects_a_non_uuid_child(format3):
    text = ("(kicadstamp-config\n  (version 3)\n"
            "  (trees\n    (tree\n      (name \"t\")\n"
            "      (anchor (point \"p\" (bogus \"x\")))\n    )\n  )\n)\n")
    with pytest.raises(ValidationError) as e:
        sexp_to_dict(text)
    assert "may follow a (point" in str(e.value)


def test_tree_anchor_point_uuid_without_a_point_is_fatal(format3, tmp_path):
    data = {"points": {"p": {"anchor_ref": "IC1", "uuid": POINT_P}},
            "trees": [{"name": "t", "anchor": {"point_uuid": POINT_P}}]}
    p = tmp_path / "config.json"
    p.write_text(json.dumps({**data, "version": 3}), encoding="utf-8")
    with pytest.raises(ValidationError) as e:
        load_config(str(p))
    assert "point_uuid needs a point" in str(e.value)


# ── К1: the pass must not mutate the SHARED file-cache objects ─────────────

def test_normalization_does_not_corrupt_the_shared_file_cache(format3, tmp_path):
    """К1: `upgrade_graph_on_disk` reads every graph file first, so the loader's
    own read is a cache HIT returning the SHARED cached dict; normalizing it in
    place would rewrite the hints for later readers (and a writer read) while the
    disk still holds the old bytes. After a load, the cache and
    config_writer._read_data must still show the on-disk hint — root AND an
    included file (the check is independent of the Config, which DID resolve)."""
    sub = tmp_path / "sub.sexp"
    root = tmp_path / "root.sexp"
    _write_sexp(sub, {"entities": [{"name": "E2", "cell": "WRONG-SUB",
                                    "cell_uuid": _u("cell:A"),
                                    "uuid": det_uuid("entity:E2")}]})
    _write_sexp(root, {"include": ["sub.sexp"],
                       "cells": {"c": {"layer": "B.Cu", "uuid": _u("cell:A")}},
                       "entities": [{"name": "E", "cell": "WRONG-ROOT",
                                     "cell_uuid": _u("cell:A"),
                                     "uuid": det_uuid("entity:E")}]})

    cfg, _ = load_config(str(root))
    # the loader DID resolve by UUID...
    assert {e.name: e.cell for e in cfg.entities} == {"E": "c", "E2": "c"}

    # ...but the shared cache and a writer read still see the on-disk hint.
    for path, lie in ((root, "WRONG-ROOT"), (sub, "WRONG-SUB")):
        hit = cached_file_read(Path(path), _load_config_file)
        assert hit["entities"][0]["cell"] == lie, "the shared file cache was mutated"
        assert _read_data(Path(path))["entities"][0]["cell"] == lie
