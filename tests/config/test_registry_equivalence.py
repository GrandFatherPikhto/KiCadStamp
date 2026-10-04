# tests/config/test_registry_equivalence.py
"""У5.4 (plan §6) — the THROUGH-EQUIVALENCE cell: a format-2 profile's copper
survives the format-2 -> format-3 transition untouched.

For one key FORM at a time: a format-2 config is applied to a fake board (real
planners, real registries: copper created, the registry filled with NAME keys) →
the SAME graph is rewritten as format 3 (the У2 stub ``mint_format3``) and
re-loaded (``load_config`` lifts the registry schema 1 -> 2, name keys ->
uuid keys) → the SAME form is planned again and reconciled: ``to_create`` and
``to_delete`` are BOTH empty and not a single board UUID changed. A registry
whose keys did NOT lift would report every command as "create" and the old
(name-keyed) entries as "prune" — i.e. delete + recreate all of this form's
copper, which is exactly what this cell forbids.

Parameterized over every form the У5.0 inventory lists, so one broken form
cannot hide the others: ``name:`` of a tree Entity, ``name:`` of an absolute
clone_placement, ``point:``, ``role:``, ``anchor:``, ``thermal:``, ``net:``,
``pad:`` (a chain spoke), a nested ``…/nested``, the two ``tree_instances``
copies of ONE entity (DIFFERENT keys), and the ``sheet_templates`` copies at one
sheet and at several.

The apply is the REGISTRY path only (reconcile + create + ``record_created``):
the positional pre-check is an independent mechanism for UNREGISTERED copper and
would make the cell vacuous for forms whose geometry overlaps.
"""
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# apply_pipeline first: it pulls placement/executor in, which avoids the
# pre-existing registry-first import cycle (see У5.3).
from kicadstamp.apply_pipeline import _compute_all_anchor_ids
from kicadstamp.config import (
    format_version,
)
from kicadstamp.config.loader import load_config
from kicadstamp.config.sexp_format import dict_to_sexp
from kicadstamp.domain.geometry import Vector2
from kicadstamp.exceptions import ValidationError
from kicadstamp.net_trace_planner import plan_net_traces
from kicadstamp.placement.entity_placement import materialize_entity_placements
from kicadstamp.placement.services.clone_position_calculator import (
    ClonePositionCalculator,
)
from kicadstamp.placement.services.manual_position_calculator import (
    ManualPositionCalculator,
)
from kicadstamp.placement.services.via_planner import ViaPlanner
from kicadstamp.registry import PlacementRegistry, TrackRegistry
from kicadstamp.utils.paths import (
    registry_path_for_config,
    registry_paths_for_config,
    track_registry_path_for_config,
)
from tests.fakes.format3 import mint_format3
from tests.fakes.live_board import FakeLiveBoardAdapter

MM = 1_000_000


# ── the fake board ──────────────────────────────────────────────────────────



@pytest.fixture(autouse=True)
def _pin_current_format_2(monkeypatch):
    """The fixtures of this module are a FORMAT-2 config graph (dict literals
    written with ``dict_to_sexp`` and loaded back). Format 3 requires every
    record and every reference to carry a UUID (plan §1/§4 У2.2), which those
    fixtures do not; so the module is pinned to format 2 — У3.5 К3, Денис
    04.10: pin is allowed for files whose DATA is a format-2 graph. A cell that
    requests the ``format3`` fixture still wins (its monkeypatch is applied
    after this autouse one)."""
    from kicadstamp.config import format_version

    monkeypatch.setattr(format_version, "CURRENT_FORMAT", 2)

def _make_fp():
    """U1 at (65,-65) mm, Role/Cluster/Sheet = FPGA, pad '1' on GND — resolves
    every anchor the fixture names (ref U1, role FPGA, point P1 -> U1)."""
    fp = MagicMock()
    fp.ref = "U1"
    fp.uuid = "fp-uuid"
    fp.position = Vector2.from_xy(65.0 * MM, -65.0 * MM)
    fp.angle_deg = 0.0
    fp.rotation = 0.0

    def _field(field):
        return "FPGA" if field in ("Role", "Cluster", "Sheet") else None
    fp.get_field_value = _field

    pad = MagicMock()
    pad.number = "1"
    pad.net_name = "GND"
    pad.position = Vector2.from_xy(65.0 * MM, -65.0 * MM)
    pad.size = Vector2.from_xy(4.0 * MM, 4.0 * MM)
    pad.angle_rad = 0.0
    fp.pads = {"1": pad}

    def _pad(num):
        return fp.pads.get(str(num))
    fp.pad = _pad
    fp.definition = MagicMock(items=[])
    return fp


class _Adapter(FakeLiveBoardAdapter):
    def __init__(self):
        super().__init__(_make_fp())

    def get_bounding_boxes(self):
        return []


# ── the fixture graph (every key form, anchors resolvable on _Adapter) ───────

def _data() -> dict:
    return {
        "cells": {
            "leaf": {
                "vias": [{"offset_along_mm": 0.0, "offset_across_mm": 0.0,
                          "net": "GND", "drill_mm": 0.3, "diameter_mm": 0.6}],
                "tracks": [{"start_along_mm": -1.0, "start_across_mm": 0.0,
                            "end_along_mm": 1.0, "end_across_mm": 0.0,
                            "width_mm": 0.25, "net": "GND"}],
            },
            "lvl2": {"clone_placements": [
                {"name": "inner2", "cell": "leaf", "xy": [1.0, 0.0]}]},
            "lvl1": {"clone_placements": [
                {"name": "inner1", "cell": "lvl2", "xy": [1.0, 0.0]}]},
        },
        "points": {"P1": {"anchor_ref": "U1"}},
        "entities": [{"name": "E", "cell": "leaf"}],
        "trees": [{"name": "etree",
                   "anchor": {"role": "FPGA", "anchor_sheet": "FPGA",
                              "anchor_cluster": "FPGA"},
                   "nodes": [{"ref": "E", "kind": "placement", "xy": [0.0, 0.0]}]}],
        "tree_instances": [
            {"template": "etree", "name": "ti1", "sheet": "S1"},
            {"template": "etree", "name": "ti2", "sheet": "S2"},
        ],
        "clone_placements": [
            {"name": "cabs", "cluster": "cabs", "cell": "leaf", "xy": [0.0, 0.0]},
            {"name": "cref", "cluster": "cref", "cell": "leaf", "xy": [0.0, 0.0],
             "anchor_ref": "U1"},
            {"name": "crole", "cluster": "crole", "cell": "leaf", "xy": [0.0, 0.0],
             "anchor_role": "FPGA", "anchor_sheet": "FPGA", "anchor_cluster": "FPGA"},
            {"name": "cpoint", "cluster": "cpoint", "cell": "leaf", "xy": [0.0, 0.0],
             "anchor_point": "P1"},
            {"name": "cnest", "cluster": "cnest", "cell": "lvl1", "xy": [0.0, 0.0]},
        ],
        "chains": [{"net": "GND", "name": "ch1", "anchor_ref": "U1",
                    "spokes": [{"pad": "1", "cell": "leaf"}]}],
        "thermal_via_arrays": [{"name": "tva1", "anchor_ref": "U1", "pad": "1",
                                "net": "GND", "rows": 1, "cols": 1, "margin_mm": 0.0,
                                "pattern": "grid", "drill_mm": 0.3, "diameter_mm": 0.6}],
        "net_traces": [{"name": "nt1", "net": "GND", "anchor_role": "FPGA",
                        "anchor_sheet": "FPGA", "anchor_cluster": "FPGA",
                        "tracks": [{"start_along_mm": 0.0, "start_across_mm": 0.0,
                                    "end_along_mm": 2.0, "end_across_mm": 0.0,
                                    "width_mm": 0.25, "net": "GND", "layer": "F.Cu"}]}],
        "sheet_templates": {
            "one": {"sheets": ["S3"],
                    "clone_placements": [{"name": "csheet1", "cluster": "csheet1",
                                          "cell": "leaf", "anchor_point": "P1",
                                          "xy": [0.0, 0.0]}]},
            "many": {"sheets": ["S4", "S5"],
                     "clone_placements": [{"name": "csheet2", "cluster": "csheet2",
                                           "cell": "leaf", "anchor_point": "P1",
                                           "xy": [0.0, 0.0]}]},
        },
    }


def _write(path, data: dict, version: int) -> None:
    path.write_text(dict_to_sexp(data, format_number=version), encoding="utf-8")


# ── per-form command planning (only that form's records) ────────────────────

def _clone_cmds(cfg, adapter, clones):
    _p, vias, tracks = ClonePositionCalculator(adapter, cfg, {}).compute_raw_positions(
        list(clones))
    return vias, tracks


def _clones_named(cfg, names):
    return [c for c in cfg.clone_placements if c.name in names]


def _entity_clones(adapter, cfg, names):
    from kicadstamp.config.models import clone_placement_effective_name
    return [c for c in materialize_entity_placements(adapter, cfg, {})
            if clone_placement_effective_name(c) in names]


def _commands(form, cfg, adapter):
    if form == "name_entity":
        return _clone_cmds(cfg, adapter, _entity_clones(adapter, cfg, {"E"}))
    if form == "tree_instance":
        return _clone_cmds(cfg, adapter, _entity_clones(adapter, cfg, {"E__ti1", "E__ti2"}))
    if form == "name_clone":
        return _clone_cmds(cfg, adapter, _clones_named(cfg, {"cabs"}))
    if form == "role":
        return _clone_cmds(cfg, adapter, _clones_named(cfg, {"crole"}))
    if form == "anchor":
        return _clone_cmds(cfg, adapter, _clones_named(cfg, {"cref"}))
    if form == "point":
        return _clone_cmds(cfg, adapter, _clones_named(cfg, {"cpoint"}))
    if form == "nested":
        return _clone_cmds(cfg, adapter, _clones_named(cfg, {"cnest"}))
    if form == "sheet_single":
        return _clone_cmds(cfg, adapter, _clones_named(cfg, {"csheet1"}))
    if form == "sheet_multi":
        return _clone_cmds(cfg, adapter, _clones_named(cfg, {"S4_csheet2", "S5_csheet2"}))
    if form == "thermal":
        return ViaPlanner(adapter, cfg).plan_vias([], []), []
    if form == "pad":
        _p, vias, tracks = ManualPositionCalculator(adapter, cfg).compute_raw_positions(
            list(cfg.chains))
        return vias, tracks
    if form == "net":
        vias, tracks = plan_net_traces(adapter, list(cfg.net_traces))
        return vias, tracks
    raise AssertionError(form)


_FORMS = ["name_entity", "name_clone", "point", "role", "anchor", "thermal",
          "net", "pad", "nested", "tree_instance", "sheet_single", "sheet_multi"]


# ── the registry-path apply (reconcile + create + record) ───────────────────

def _registry_paths(cfg, root):
    """The SAME two registry paths an apply uses (Н3): the config's explicit
    ``registry_path:``/``track_registry_path:`` when set, else the defaults.
    Mirrors ``apply_pipeline._execute`` through the shared helper, so a cell can
    never test a different file than the product writes."""
    return registry_paths_for_config(str(root), getattr(cfg, "registry_path", None),
                                     getattr(cfg, "track_registry_path", None))


def _registry_bytes(cfg, root) -> dict:
    """{path: bytes | None} for both registry files, so a cell can prove a file
    was NOT touched (same bytes, still schema 1)."""
    out = {}
    for path in _registry_paths(cfg, root):
        p = Path(path)
        out[path] = p.read_bytes() if p.exists() else None
    return out


def _stub_lift(monkeypatch):
    """Make the on-disk registry lift a no-op for THIS profile open — the Н3
    scenario where it did not run (read-only registry directory, a failed write,
    a working set that stood it down).

    Patched on the MODULE ``kicadstamp.config.registry_upgrade``, deliberately
    NOT on ``loader``: ``load_config`` imports the symbol LAZILY inside the
    function body (``from .registry_upgrade import upgrade_registries_on_disk``),
    so a stub set on ``loader``'s module namespace is never consulted."""
    import kicadstamp.config.registry_upgrade as ru
    monkeypatch.setattr(ru, "upgrade_registries_on_disk", lambda *a, **k: [])


def _apply(adapter, cfg, root, vias, tracks):
    known = _compute_all_anchor_ids(cfg)
    via_path, trk_path = _registry_paths(cfg, root)

    vreg = PlacementRegistry(adapter, via_path)
    live_vias = adapter.get_vias()
    v_create, v_delete = vreg.reconcile(vias, known_anchor_ids=known, live_items=live_vias)
    if v_delete:
        adapter.remove_by_ids(v_delete)
    for cmd in v_create:
        net = adapter.get_net_by_name(cmd.net_name)
        item = adapter.create_via(cmd.position, net, cmd.drill_mm, cmd.diameter_mm)
        adapter.create_items([item])
        adapter.live_vias.append(item)
        vreg.record_created(cmd, item.uuid)

    treg = TrackRegistry(adapter, trk_path)
    live_tracks = adapter.get_tracks()
    t_create, t_delete = treg.reconcile(tracks, known_anchor_ids=known, live_items=live_tracks)
    if t_delete:
        adapter.remove_by_ids(t_delete)
    for cmd in t_create:
        net = adapter.get_net_by_name(cmd.net_name)
        item = adapter.create_track(cmd.start, cmd.end, cmd.width_mm, net, cmd.layer)
        adapter.create_items([item])
        adapter.live_tracks.append(item)
        treg.record_created(cmd, item.uuid)

    return v_create, v_delete, t_create, t_delete


def _board_uuids(adapter):
    return sorted(i.uuid for i in adapter.live_vias + adapter.live_tracks)


@pytest.mark.parametrize("form", _FORMS)
def test_a_format2_apply_survives_the_format3_registry_lift(form, tmp_path, monkeypatch):
    root = tmp_path / "root.sexp"
    _write(root, _data(), 2)
    adapter = _Adapter()

    # Pass 1 — format 2 (name keys), copper created, registry saved schema 1.
    cfg2, _ = load_config(str(root))
    vias2, tracks2 = _commands(form, cfg2, adapter)
    assert vias2 or tracks2, f"{form}: the fixture planned no copper (vacuous row)"
    _apply(adapter, cfg2, root, vias2, tracks2)
    board_before = _board_uuids(adapter)
    assert board_before, f"{form}: nothing reached the board"

    # Pass 2 — the SAME graph as format 3; load_config lifts the registry.
    root.write_text(dict_to_sexp(mint_format3(_data()), format_number=3),
                    encoding="utf-8")
    monkeypatch.setattr(format_version, "CURRENT_FORMAT", 3)
    cfg3, _ = load_config(str(root))
    vias3, tracks3 = _commands(form, cfg3, adapter)

    v_create, v_delete, t_create, t_delete = _apply(adapter, cfg3, root, vias3, tracks3)

    assert v_create == [] and t_create == [], (
        f"{form}: the second apply wants to CREATE copper — a registry key did "
        f"not lift to its uuid form")
    assert v_delete == [] and t_delete == [], (
        f"{form}: the second apply wants to DELETE copper — the name-keyed "
        f"registry entry was seen as stale")
    assert _board_uuids(adapter) == board_before, (
        f"{form}: the board's UUID set changed across the lift")


# ── Н3 (У5.4 rework): the lift that did NOT happen must not delete copper ────
#
# When the lift is skipped the registry stays schema 1 (NAME keys) while the
# plan carries UUID keys. Reading it would make reconcile "create" every command
# and "prune" every stored entry — deleting and recreating the profile's copper.
# Р-У5.7 says that mix is worse than a fatal, so a format-3 apply REFUSES at
# registry load, before reconcile, leaving the board and the file untouched.


@pytest.mark.parametrize("form", _FORMS)
def test_an_unlifted_registry_is_a_fatal_before_any_copper_moves(form, tmp_path, monkeypatch):
    """The lift did not run → a format-3 apply is a FATAL, and NOTHING moves."""
    root = tmp_path / "root.sexp"
    _write(root, _data(), 2)
    adapter = _Adapter()

    cfg2, _ = load_config(str(root))
    vias2, tracks2 = _commands(form, cfg2, adapter)
    _apply(adapter, cfg2, root, vias2, tracks2)
    board_before = _board_uuids(adapter)
    assert board_before, f"{form}: nothing reached the board"
    bytes_before = _registry_bytes(cfg2, root)
    assert any(v is not None for v in bytes_before.values()), (
        f"{form}: no registry file was written")

    root.write_text(dict_to_sexp(mint_format3(_data()), format_number=3),
                    encoding="utf-8")
    monkeypatch.setattr(format_version, "CURRENT_FORMAT", 3)
    _stub_lift(monkeypatch)
    cfg3, _ = load_config(str(root))
    vias3, tracks3 = _commands(form, cfg3, adapter)

    with pytest.raises(ValidationError):
        _apply(adapter, cfg3, root, vias3, tracks3)

    assert _board_uuids(adapter) == board_before, (
        f"{form}: the board changed — copper was deleted or created")
    assert _registry_bytes(cfg3, root) == bytes_before, (
        f"{form}: the schema-1 registry file was modified")


def test_an_unlifted_registry_is_a_fatal_under_only_too(tmp_path, monkeypatch):
    """The --only path of the Н3 probe: the Point-anchored clone ``cpoint`` is on
    the board and in the name-keyed registry; an apply --only ``cabs`` is planned
    with the FULL known-anchor-id set. Under the mix, ``cpoint``'s name-keyed
    anchor_id matches no uuid-keyed known id and would be PRUNED. With the fix
    the apply refuses BEFORE reconcile, so ``cpoint``'s copper stays."""
    root = tmp_path / "root.sexp"
    _write(root, _data(), 2)
    adapter = _Adapter()

    cfg2, _ = load_config(str(root))
    v1, t1 = _commands("name_clone", cfg2, adapter)   # cabs
    v2, t2 = _commands("point", cfg2, adapter)        # cpoint
    _apply(adapter, cfg2, root, v1 + v2, t1 + t2)
    board_before = _board_uuids(adapter)
    assert board_before
    bytes_before = _registry_bytes(cfg2, root)

    root.write_text(dict_to_sexp(mint_format3(_data()), format_number=3),
                    encoding="utf-8")
    monkeypatch.setattr(format_version, "CURRENT_FORMAT", 3)
    _stub_lift(monkeypatch)
    cfg3, _ = load_config(str(root))
    # --only cabs: only cabs' commands, but _apply still passes the FULL known
    # anchor-id set (what apply_pipeline._compute_all_anchor_ids returns).
    vias3, tracks3 = _commands("name_clone", cfg3, adapter)

    with pytest.raises(ValidationError):
        _apply(adapter, cfg3, root, vias3, tracks3)

    assert _board_uuids(adapter) == board_before, (
        "the --only apply deleted cpoint's copper")
    assert _registry_bytes(cfg3, root) == bytes_before


def test_explicit_registry_paths_are_lifted_and_equivalent(tmp_path, monkeypatch):
    """Н3 part 2, one decision for both files: a config with explicit
    ``registry_path:``/``track_registry_path:`` has THOSE files lifted, and the
    second apply is 0 / 0. On the old code the lift always used the defaults, so
    the explicit files stayed schema 1 (and, after the load gate, the apply would
    refuse)."""
    data = _data()
    data["registry_path"] = "alt/via.registry.json"
    data["track_registry_path"] = "alt/trk.registry.json"
    root = tmp_path / "root.sexp"
    _write(root, data, 2)
    adapter = _Adapter()

    cfg2, _ = load_config(str(root))
    via2, trk2 = _registry_paths(cfg2, root)
    assert via2 == str(tmp_path / "alt" / "via.registry.json")
    assert trk2 == str(tmp_path / "alt" / "trk.registry.json")

    vias2, tracks2 = _commands("name_clone", cfg2, adapter)
    _apply(adapter, cfg2, root, vias2, tracks2)
    board_before = _board_uuids(adapter)
    assert board_before
    assert Path(via2).exists() and Path(trk2).exists()
    # the DEFAULT files were never written — proving the explicit paths were used
    assert not Path(registry_path_for_config(str(root))).exists()
    assert not Path(track_registry_path_for_config(str(root))).exists()

    root.write_text(dict_to_sexp(mint_format3(data), format_number=3),
                    encoding="utf-8")
    monkeypatch.setattr(format_version, "CURRENT_FORMAT", 3)
    cfg3, _ = load_config(str(root))       # lifts the EXPLICIT files
    assert json.loads(Path(via2).read_text(encoding="utf-8"))["schema_version"] == 2
    assert json.loads(Path(trk2).read_text(encoding="utf-8"))["schema_version"] == 2

    vias3, tracks3 = _commands("name_clone", cfg3, adapter)
    v_create, v_delete, t_create, t_delete = _apply(adapter, cfg3, root, vias3, tracks3)
    assert v_create == [] and t_create == []
    assert v_delete == [] and t_delete == []
    assert _board_uuids(adapter) == board_before


# ── H2 (У5.5): the track loader's refusal, with nothing masking it ──────────
#
# The Н3 cells above stub the lift for BOTH files, so the VIA loader refuses
# first and masks the track loader. Here the via registry is lifted (or the
# profile has no via registry at all — a tracks-only profile), leaving the
# UNLIFTED track registry as the only thing that can refuse. The file has no
# `schema_version` (the legacy form): `check_schema_version` accepts a missing
# field, so `_refuse_unlifted_registry` is the only guard. Without it reconcile
# would treat every uuid-keyed track command as "create" and every name-keyed
# entry as "prune" — deleting and redrawing the tracks (exactly what H2 was).

_TRK_ENTRY = {"uuid": "u-trk", "start_x_mm": 0.0, "start_y_mm": 0.0,
              "end_x_mm": 1.0, "end_y_mm": 0.0, "width_mm": 0.25,
              "net": "GND", "layer": "F.Cu"}


@pytest.mark.parametrize("via_present", [True, False],
                         ids=["via-lifted-track-not", "tracks-only-profile"])
def test_an_unlifted_track_registry_is_a_fatal_when_nothing_masks_it(
        via_present, tmp_path, monkeypatch):
    root = tmp_path / "root.sexp"
    _write(root, _data(), 2)
    adapter = _Adapter()
    cfg2, _ = load_config(str(root))
    vias2, tracks2 = _commands("name_clone", cfg2, adapter)
    _apply(adapter, cfg2, root, vias2, tracks2)
    board_before = _board_uuids(adapter)
    assert board_before

    root.write_text(dict_to_sexp(mint_format3(_data()), format_number=3),
                    encoding="utf-8")
    monkeypatch.setattr(format_version, "CURRENT_FORMAT", 3)
    cfg3, _ = load_config(str(root))                     # lifts both to schema 2
    via_path, trk_path = _registry_paths(cfg3, root)
    assert json.loads(Path(via_path).read_text(encoding="utf-8"))["schema_version"] == 2

    # ONE failed write: the track registry stays UNLIFTED (no `schema_version`
    # — the legacy schema-1-by-convention form the reader must still refuse).
    Path(trk_path).write_text(
        json.dumps({"name:cabs|leaf|__spoke__|0": _TRK_ENTRY}),
        encoding="utf-8")
    track_before = Path(trk_path).read_text(encoding="utf-8")
    if not via_present:
        Path(via_path).unlink()                          # tracks-only profile

    vias3, tracks3 = _commands("name_clone", cfg3, adapter)
    if not via_present:
        vias3 = []                                       # no via records planned

    with pytest.raises(ValidationError):
        _apply(adapter, cfg3, root, vias3, tracks3)

    assert _board_uuids(adapter) == board_before, "the tracks were pruned"
    assert Path(trk_path).read_text(encoding="utf-8") == track_before, (
        "the schema-1 track registry was modified")
