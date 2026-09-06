# tests/test_scheme_list_config.py
"""Config-side tests for the Scheme List feature (design_2026_09_05_scheme_
list.md, plan P1): the SchemeListConfig dataclasses + loader validation +
.json/.sexp round-trips + the Entity.cell-or-scheme_list cross-validation and
the canary that a scheme_list-based Entity never trips the cell-existence
structural check.

Pure config tests — no live board, no adapter, no GUI. The capture/Reread/
Apply logic itself is P2/P3/P4 and lives in its own test files.
"""
import json
from pathlib import Path

import pytest

from kicadstamp.config import (
    Config,
    load_config,
    load_entity,
    load_scheme_list,
)
from kicadstamp.exceptions import ValidationError
from kicadstamp.validation import check_entity_cells_exist


def _record(name="psu", anchor_ref="C1", components=None, **extra):
    """A valid scheme_lists entry dict (anchor_ref among its own components)."""
    rec = {
        "name": name,
        "anchor_ref": anchor_ref,
        "source_sheet": "Channel_0",
        "components": components if components is not None else [
            {"ref": "C1"},
            {"ref": "R1", "offset_along_mm": 1.0, "rotation_deg": 90.0},
        ],
    }
    rec.update(extra)
    return rec


def _write_json(path: Path, data: dict) -> Path:
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


# ── per-record loading (_load_scheme_list / public load_scheme_list) ────────

def test_single_record_loads_with_nested_copper():
    """A full record (components/vias/tracks/boundary_nets) parses into the
    dataclasses; track.layer is a free string (In1.Cu — a multilayer board
    layer, see P0.1), boundary action defaults to exclude."""
    sl = load_scheme_list(_record(
        vias=[{"offset_along_mm": 2.0, "drill_mm": 0.3, "net": "/Channel_0/GND"}],
        tracks=[{"start_along_mm": 0.0, "end_along_mm": 1.0, "width_mm": 0.25,
                 "layer": "In1.Cu", "net": "/Channel_0/+3V3"}],
        boundary_nets=[{"net": "/Channel_0/OUT", "external_ref": "J1"}],
    ))
    assert sl.name == "psu"
    assert sl.anchor_ref == "C1"
    assert sl.source_sheet == "Channel_0"
    assert [c.ref for c in sl.components] == ["C1", "R1"]
    assert sl.components[1].rotation_deg == 90.0
    assert len(sl.vias) == 1 and sl.vias[0].drill_mm == 0.3
    assert len(sl.tracks) == 1 and sl.tracks[0].layer == "In1.Cu"
    assert sl.boundary_nets[0].action == "exclude"  # v1 default
    # addendum P2.x — the new explicit anchor-rotation field
    assert sl.anchor_rotation_deg == 0.0  # absent -> default


def test_record_loads_explicit_anchor_rotation_deg():
    """anchor_rotation_deg (addendum P2.x) is read from the record; non-zero
    survives."""
    sl = load_scheme_list(_record(anchor_rotation_deg=90.0))
    assert sl.anchor_rotation_deg == 90.0


def test_record_scope_sheet_paths_defaults_to_none():
    """5c.1 — scope_sheet_paths is OPTIONAL (None default): an absent/empty
    value means a "By selection"-record (or a pre-5c legacy record) whose
    Reread scope is NOT persisted."""
    sl = load_scheme_list(_record())
    assert sl.scope_sheet_paths is None
    sl2 = load_scheme_list(_record(scope_sheet_paths=[]))
    assert sl2.scope_sheet_paths is None


def test_record_loads_scope_sheet_paths():
    """5c.1 — the checked leaf paths of a "By sheet" record round-trip through
    the loader as list[list[str]] (single-segment paths included)."""
    sl = load_scheme_list(_record(
        scope_sheet_paths=[["Top", "Channel_0"], ["Top"]]))
    assert sl.scope_sheet_paths == [["Top", "Channel_0"], ["Top"]]


def test_record_scope_sheet_paths_must_be_path_lists():
    """5c.1 — a malformed scope_sheet_paths is a fatal (a path is a non-empty
    list of non-empty sheet-name strings), never a silent garbage accept."""
    with pytest.raises(ValidationError, match="scope_sheet_paths"):
        load_scheme_list(_record(scope_sheet_paths="Channel_0"))
    with pytest.raises(ValidationError, match="scope_sheet_paths"):
        load_scheme_list(_record(scope_sheet_paths=[["Channel_0"], 42]))
    with pytest.raises(ValidationError, match="scope_sheet_paths"):
        load_scheme_list(_record(scope_sheet_paths=[["Channel_0"], []]))
    with pytest.raises(ValidationError, match="scope_sheet_paths"):
        load_scheme_list(_record(scope_sheet_paths=[["Channel_0"], [""]]))


def test_record_scope_presets_default_to_empty():
    """Named presets (plan_2026_09_06_scheme_list_named_presets.md §2) are
    OPTIONAL ([] default): an absent/empty value means the record simply has no
    saved checklist variants (a "By selection"-record never fills it)."""
    sl = load_scheme_list(_record())
    assert sl.scope_presets == []
    sl2 = load_scheme_list(_record(scope_presets=[]))
    assert sl2.scope_presets == []


def test_record_loads_named_scope_presets():
    """plan §2 — scope_presets round-trip through the loader as
    SchemeListScopePreset {name, sheet_paths: list[list[str]]} (single-segment
    paths included)."""
    sl = load_scheme_list(_record(scope_presets=[
        {"name": "full",
         "sheet_paths": [["Top", "Channel_0"], ["Top", "Channel_1"]]},
        {"name": "ch0-only", "sheet_paths": [["Top"]]},
    ]))
    assert [(p.name, p.sheet_paths) for p in sl.scope_presets] == [
        ("full", [["Top", "Channel_0"], ["Top", "Channel_1"]]),
        ("ch0-only", [["Top"]]),
    ]


def test_record_duplicate_preset_name_within_record_is_fatal():
    """plan §2 — a preset name must be UNIQUE within the record's OWN
    scope_presets (the same ambiguity scope_sheet_paths' between-records
    uniqueness guards against, one level down)."""
    with pytest.raises(ValidationError, match="duplicate preset name"):
        load_scheme_list(_record(scope_presets=[
            {"name": "full", "sheet_paths": [["Top"]]},
            {"name": "full", "sheet_paths": [["Top", "Channel_0"]]},
        ]))


def test_record_scope_presets_must_be_valid_presets():
    """plan §2 — malformed scope_presets is a fatal: not a list / an entry
    without a (non-empty) name or sheet_paths / an entry whose sheet_paths is
    not a non-empty list of non-empty sheet-name strings."""
    with pytest.raises(ValidationError, match="scope_presets"):
        load_scheme_list(_record(scope_presets="full"))
    with pytest.raises(ValidationError, match="scope_presets"):
        load_scheme_list(_record(scope_presets=[{"name": ""}]))
    with pytest.raises(ValidationError, match="scope_presets"):
        load_scheme_list(_record(scope_presets=[{"name": "full"}]))
    with pytest.raises(ValidationError, match="sheet_paths"):
        load_scheme_list(_record(scope_presets=[
            {"name": "full", "sheet_paths": "Top"}]))
    with pytest.raises(ValidationError, match="sheet_paths"):
        load_scheme_list(_record(scope_presets=[
            {"name": "full", "sheet_paths": [["Top"], []]}]))


def test_record_requires_name_and_anchor_ref():
    with pytest.raises(ValidationError, match="without name"):
        load_scheme_list({"anchor_ref": "C1", "components": [{"ref": "C1"}]})
    with pytest.raises(ValidationError, match="without anchor_ref"):
        load_scheme_list({"name": "psu", "components": [{"ref": "C1"}]})


def test_record_requires_nonempty_components():
    with pytest.raises(ValidationError, match="without components"):
        load_scheme_list({"name": "psu", "anchor_ref": "C1", "components": []})


def test_record_anchor_ref_must_be_own_component():
    with pytest.raises(ValidationError, match="not one of its own components"):
        load_scheme_list(_record(anchor_ref="R9"))


def test_record_rejects_unknown_keys():
    with pytest.raises(ValidationError, match="unknown fields"):
        load_scheme_list(_record(bogus=1))


def test_record_accepts_truncate_action():
    """action 'truncate' (geometric clipping at the capture boundary,
    design_2026_09_06_boundary_truncate_and_zones.md Part A) now loads; the
    net's decision is persisted verbatim so a later Reread re-clips with the
    same choice."""
    sl = load_scheme_list(_record(boundary_nets=[
        {"net": "/Channel_0/OUT", "action": "truncate", "external_ref": "J1"}]))
    assert len(sl.boundary_nets) == 1
    bn = sl.boundary_nets[0]
    assert bn.net == "/Channel_0/OUT"
    assert bn.action == "truncate"
    assert bn.external_ref == "J1"


def test_record_rejects_unknown_boundary_action():
    """ANY action other than 'exclude'/'truncate' must stay an explicit error,
    not a silent accept."""
    with pytest.raises(ValidationError, match="action"):
        load_scheme_list(_record(boundary_nets=[
            {"net": "/Channel_0/OUT", "action": "something-else"}]))


def test_record_track_layer_must_be_string():
    with pytest.raises(ValidationError, match="layer must be a string"):
        load_scheme_list(_record(tracks=[
            {"start_along_mm": 0.0, "end_along_mm": 1.0, "layer": 4}]))
    # non-copper nonsense is NOT validated here (a string is accepted as-is);
    # the layer is a literal identifier, P0.1 leaves enum-restriction out.


# ── Entity: exactly one of cell:/scheme_list: ───────────────────────────────

def test_entity_requires_exactly_one_of_cell_and_scheme_list():
    with pytest.raises(ValidationError, match="exactly one of cell"):
        load_entity({"name": "E1", "cell": "c_dac", "scheme_list": "psu"})
    with pytest.raises(ValidationError, match="exactly one of cell"):
        load_entity({"name": "E1"})


def test_entity_scheme_list_forbids_role_resolution_fields():
    """cluster/by_selection/refs/nets/params/net_overrides are meaningless on a
    recorded snapshot (it already carries literal refs and literal nets)."""
    for key in ("cluster", "by_selection", "refs", "nets", "params", "net_overrides"):
        value = {"cluster": "X", "by_selection": True, "refs": {"R": "C1"},
                 "nets": {"R": "/NET"}, "params": {"p": 1},
                 "net_overrides": {"R": "/NET"}}[key]
        with pytest.raises(ValidationError, match="scheme_list-based entity"):
            load_entity({"name": "E1", "scheme_list": "psu", key: value})


def test_entity_scheme_list_loads_cell_is_none():
    ent = load_entity({"name": "E1", "scheme_list": "psu", "sheet": "Channel_1"})
    assert ent.scheme_list == "psu"
    assert ent.cell is None
    assert ent.sheet == "Channel_1"


def test_entity_cell_based_still_loads():
    """A plain cell-based Entity is unchanged (backward compat)."""
    ent = load_entity({"name": "E1", "cell": "c_dac", "cluster": "DAC"})
    assert ent.cell == "c_dac"
    assert ent.scheme_list is None


# ── canary: scheme_list Entity passes the structural cell-existence check ───

def test_check_entity_cells_exist_passes_for_scheme_list_entity():
    """The canary from plan P4: a scheme_list-based Entity (cell=None) must
    NOT trip check_entity_cells_exist's "cell not found" fatal — before the
    scheme_list branch this was exactly the spurious-fatal path."""
    cfg = Config(
        entities=[
            load_entity({"name": "E1", "scheme_list": "psu"}),
            load_entity({"name": "E2", "cell": "c_dac"}),
        ],
        cells={"c_dac": _CELL_STUB},
    )
    check_entity_cells_exist(cfg)


class _CellStub:
    """Minimal cells: entry — check_entity_cells_exist only tests membership
    (ent.cell in cfg.cells), so a name attribute is all it needs."""
    name = "c_dac"


_CELL_STUB = _CellStub()


# ── load_config: section wiring, includes, duplicates ───────────────────────

def test_config_json_include_roundtrip(tmp_path: Path):
    """scheme_lists records physically live in an included .json file (design
    §3) — include: concatenates the list section with the main file."""
    main = _write_json(tmp_path / "cfg.json", {
        "include": ["scheme_lists.json"],
        "entities": [{"name": "E1", "scheme_list": "psu"}],
    })
    _write_json(tmp_path / "scheme_lists.json", {
        "scheme_lists": [_record()],
    })
    cfg, _ = load_config(str(main))
    assert len(cfg.scheme_lists) == 1
    assert cfg.scheme_lists[0].name == "psu"
    assert cfg.entities[0].scheme_list == "psu"
    assert cfg.entities[0].cell is None


def test_config_duplicate_scheme_list_name_fatal(tmp_path: Path):
    main = _write_json(tmp_path / "cfg.json", {
        "scheme_lists": [
            _record(name="psu"),
            _record(name="psu", anchor_ref="C9", components=[{"ref": "C9"}]),
        ],
    })
    # same name, disjoint refs (so this isolates the name check from the
    # cross-record ref-uniqueness check below)
    with pytest.raises(ValidationError, match="duplicate name"):
        load_config(str(main))


def test_config_duplicate_ref_across_scheme_lists_fatal(tmp_path: Path):
    """A real ref may be recorded in at most ONE Scheme List (design §9.2 /
    plan §0.2): cloning one record would move a component another expects."""
    main = _write_json(tmp_path / "cfg.json", {
        "scheme_lists": [
            _record(name="psu"),
            _record(name="psu2", anchor_ref="R1", components=[{"ref": "R1"}, {"ref": "C1"}]),
        ],
    })
    with pytest.raises(ValidationError, match="more than one scheme_lists"):
        load_config(str(main))


def test_config_records_from_multiple_included_files_concatenate(tmp_path: Path):
    main = _write_json(tmp_path / "cfg.json", {
        "include": ["a.json", "b.json"],
    })
    _write_json(tmp_path / "a.json", {"scheme_lists": [_record(name="a_sl")]})
    # disjoint refs from a_sl (C1/R1) — records concatenate across include
    # files, but the cross-record ref-uniqueness rule still applies.
    _write_json(tmp_path / "b.json", {
        "scheme_lists": [_record(name="b_sl", anchor_ref="C2",
                                 components=[{"ref": "C2"}])]})
    cfg, _ = load_config(str(main))
    assert [sl.name for sl in cfg.scheme_lists] == ["a_sl", "b_sl"]
    assert {c.ref for sl in cfg.scheme_lists for c in sl.components} == {"C1", "R1", "C2"}


def test_entity_scheme_list_must_reference_existing_record(tmp_path: Path):
    """A scheme_list-based Entity naming a non-existent scheme_lists entry is a
    load-time fatal (mirror of check_entity_cells_exist for the cell side) —
    a dangling name would otherwise only blow up at Apply/Redraw (P4)."""
    main = _write_json(tmp_path / "cfg.json", {
        "entities": [{"name": "E1", "scheme_list": "missing"}],
    })
    with pytest.raises(ValidationError, match="missing scheme_lists entry"):
        load_config(str(main))
