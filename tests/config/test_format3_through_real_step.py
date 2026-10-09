# tests/config/test_format3_through_real_step.py
"""У3.4 (plan §7, Б) — the THROUGH cell through the REAL 2 -> 3 step.

The У5.4 main cell (``tests/config/test_registry_equivalence.py``) proves a
format-2 profile's copper survives the transition when the graph is REWRITTEN in
memory by the test stub ``mint_format3``. This cell is the twin that removes the
stub: the graph is written to ``tmp_path`` AS FORMAT 2 (``(version 2)``, no
uuid and no ``mint_format3`` anywhere), applied to a fake board (registry
schema 1, NAME keys), then the SAME graph is lifted to format 3 by the PRODUCT'S
OWN open path — ``load_config`` -> ``upgrade_graph_on_disk`` ->
``upgrade_registries_on_disk`` — and the SAME commands are planned again. The
second apply must be ``create 0 / delete 0 / shift 0``, and the on-disk registry
must now be keyed by UUIDs.

Why a separate cell and not just the У5.4 one. The У5.4 cell exercises the
planners against a HAND-BUILT format-3 dict; if the real ``_step_2_to_3`` minted
a DIFFERENT uuid for a record than ``det_uuid`` (the Р-У3.4 "one seed" promise),
or if the real on-disk lift never ran, the У5.4 cell would stay green. This cell
is what makes the "one seed, real step, real open" claim observable: it never
mentions a uuid, and it FAILS if the lift is disabled, misfiled, or keyed by
name.

Property table (rule 35). Rows are independent (each section / reference form is
either present and carried, or the cell is vacuous):

  section            what it adds to the property
  -----------------  ----------------------------------------------------------
  include:           the graph is >1 file; a reference in the root resolves to a
                     record in the included file (Р-1 seed WITHOUT the path)
  cells/points       the anchor records every other form points at
  entities           a tree-placed entity -> a materialized clone, keyed `name:`
  clone_placements   absolute / anchor_ref / anchor_role / anchor_point / nested
  thermal_via_arrays a `thermal:` keyed matrix
  net_traces         a `net:` keyed trace
  trees              the tree that places the entity
  tree_instances     two generated copies of one entity (DIFFERENT keys)
  sheet_templates    generated copies, DISTINCT keys: `one` keeps anchor_point
                     P1 but SHIFTED (its own point:P1:3.0000:0.0000, not
                     cpoint's), `many` is ABSOLUTE -> name:S4_csheet2 /
                     name:S5_csheet2

Before the lift (phase 1) the cell also asserts the SET of registry anchor parts
carries EVERY form above as its OWN binding (Н1, 04.10.2026): a copy that slips
onto another form's key — the three ``sheet_templates`` copies used to collapse
onto ``cpoint``'s ``point:P1:0.0000:0.0000``, so losing the whole section in the
step was invisible — leaves its own form missing and reddens HERE, in the cell.

The apply is the REGISTRY path only (reconcile + create + ``record_created``),
exactly as the У5.4 main cell: the positional pre-check is an independent
mechanism for UNREGISTERED copper and would make the cell vacuous for forms
whose geometry overlaps.
"""
import json
import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# apply_pipeline first: it pulls placement/executor in, which avoids the
# pre-existing registry-first import cycle (see У5.3).
from kicadstamp.apply_pipeline import _compute_all_anchor_ids
from kicadstamp.config import format_version
from kicadstamp.config.format_version import read_version
from kicadstamp.config.loader import load_config
from kicadstamp.config.sexp_format import dict_to_sexp
from kicadstamp.domain.geometry import Vector2
from kicadstamp.net_trace_planner import plan_net_traces
from kicadstamp.persistence import REGISTRY_SCHEMA_VERSION_FORMAT3
from kicadstamp.placement.entity_placement import materialize_entity_placements
from kicadstamp.placement.services.clone_position_calculator import (
    ClonePositionCalculator,
)
from kicadstamp.placement.services.via_planner import ViaPlanner
from kicadstamp.registry import PlacementRegistry, TrackRegistry
from kicadstamp.utils.paths import registry_paths_for_config
from tests.fakes.format3 import format3  # noqa: F401  (the fixture)
from tests.fakes.live_board import FakeLiveBoardAdapter

MM = 1_000_000

_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


# ── the fake board ──────────────────────────────────────────────────────────

def _make_fp():
    """U1 at (65,-65) mm, Role/Cluster/Sheet = FPGA, pad '1' on GND — resolves
    every anchor the fixture names (ref U1, role FPGA, point P1/P2 -> U1)."""
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


# ── the fixture graph: a real include: graph written AS FORMAT 2 ─────────────

def _root_data() -> dict:
    return {
        "include": ["sub.sexp"],
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
            # cross-file: both the cell and the point live in sub.sexp — the Р-1
            # seed carries no file path, so this reference resolves without a walk.
            {"name": "csub", "cluster": "csub", "cell": "subcell",
             "anchor_point": "P2", "xy": [0.0, 0.0]},
        ],
        # NO ``chains`` section: a profile that still carries one has its registry
        # DELIBERATELY left untouched by the lift (Д2 доделка п.1), so it could not
        # witness "the whole graph lifts and the second apply is empty" — that
        # subject now belongs to the chain-free forms below.
        "thermal_via_arrays": [{"name": "tva1", "anchor_ref": "U1", "pad": "1",
                                "net": "GND", "rows": 1, "cols": 1, "margin_mm": 0.0,
                                "pattern": "grid", "drill_mm": 0.3, "diameter_mm": 0.6}],
        "net_traces": [{"name": "nt1", "net": "GND", "anchor_role": "FPGA",
                        "anchor_sheet": "FPGA", "anchor_cluster": "FPGA",
                        "tracks": [{"start_along_mm": 0.0, "start_across_mm": 0.0,
                                    "end_along_mm": 2.0, "end_across_mm": 0.0,
                                    "width_mm": 0.25, "net": "GND", "layer": "F.Cu"}]}],
        "sheet_templates": {
            # Н1: `one` keeps the anchor_point reference (exercised INSIDE the
            # section) but is SHIFTED to [3.0, 0.0], so its key is its own —
            # not cpoint's point:P1:0.0000:0.0000.
            "one": {"sheets": ["S3"],
                    "clone_placements": [{"name": "csheet1", "cluster": "csheet1",
                                          "cell": "leaf", "anchor_point": "P1",
                                          "xy": [3.0, 0.0]}]},
            # `many` copies are ABSOLUTE (no anchor_point): multi-sheet naming
            # gives S4_csheet2 / S5_csheet2, two DISTINCT `name:` keys — the
            # derived copy UUIDs also ride through the lift.
            "many": {"sheets": ["S4", "S5"],
                     "clone_placements": [{"name": "csheet2", "cluster": "csheet2",
                                           "cell": "leaf", "xy": [0.0, 0.0]}]},
        },
    }


def _sub_data() -> dict:
    """The included file — a cell and a point the ROOT references (the Р-1
    cross-file case), so the include: is load-bearing, not decoration."""
    return {
        "cells": {
            "subcell": {
                "vias": [{"offset_along_mm": 2.0, "offset_across_mm": 0.0,
                          "net": "GND", "drill_mm": 0.3, "diameter_mm": 0.6}],
            },
        },
        "points": {"P2": {"anchor_ref": "U1"}},
    }


def _write(path: Path, data: dict, version: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dict_to_sexp(data, format_number=version), encoding="utf-8")


def _write_graph(tmp_path: Path) -> Path:
    """A format-2 graph on disk — NO uuid, NO mint_format3."""
    root = tmp_path / "root.sexp"
    _write(root, _root_data(), 2)
    _write(tmp_path / "sub.sexp", _sub_data(), 2)
    return root


# ── the whole-graph command plan (every form at once) ───────────────────────

def _all_commands(cfg, adapter):
    """Every command the profile produces: all clone_placements (hand-written,
    entity-materialized, tree_instances- and sheet_templates-generated), the
    matrix and the net traces. Combined so the second apply is the WHOLE graph's,
    not one form's."""
    vias, tracks = [], []
    clones = list(cfg.clone_placements) + materialize_entity_placements(
        adapter, cfg, {})
    _p, v, t = ClonePositionCalculator(adapter, cfg, {}).compute_raw_positions(clones)
    vias += v
    tracks += t
    vias += ViaPlanner(adapter, cfg).plan_vias([], [])
    v, t = plan_net_traces(adapter, list(cfg.net_traces))
    vias += v
    tracks += t
    return vias, tracks


# ── the registry-path apply (reconcile + create + record), У5.4 twin ─────────

def _registry_paths(cfg, root):
    """The SAME two registry paths an apply uses (Н3): the config's explicit
    ``registry_path:``/``track_registry_path:`` when set, else the defaults."""
    return registry_paths_for_config(str(root), getattr(cfg, "registry_path", None),
                                     getattr(cfg, "track_registry_path", None))


def _apply(adapter, cfg, root, vias, tracks):
    known = _compute_all_anchor_ids(cfg)
    via_path, trk_path = _registry_paths(cfg, root)

    vreg = PlacementRegistry(adapter, via_path)
    live_vias = adapter.get_vias()
    v_create, v_delete = vreg.reconcile(vias, known_anchor_ids=known,
                                        live_items=live_vias)
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
    t_create, t_delete = treg.reconcile(tracks, known_anchor_ids=known,
                                        live_items=live_tracks)
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


def _board_positions(adapter):
    """Every live item's geometry, keyed by uuid — the "shift 0" snapshot. A
    delete + recreate would change the uuid SET; a move would change a value
    under an unchanged uuid."""
    out = {}
    for v in adapter.live_vias:
        out[v.uuid] = ("via", v.position.x, v.position.y, v.drill_mm, v.diameter_mm)
    for t in adapter.live_tracks:
        out[t.uuid] = ("track", t.start.x, t.start.y, t.end.x, t.end.y,
                       t.width_mm, t.layer)
    return out


def _anchor_parts(commands) -> set:
    """The ANCHOR part — the record identity, left of the first ``|`` — of every
    planned registry key. The form-space the phase-1 non-vacuity check asserts."""
    return {cmd.registry_key.split("|", 1)[0]
            for cmd in commands if cmd.registry_key}


def _form_is_bound(parts: set, form: str) -> bool:
    """True when some anchor part IS this form. A ``name:`` form must match
    EXACTLY: ``name:E`` must NOT be satisfied by ``name:E__ti1`` (a
    tree_instances copy) — that collapse is exactly the Н1 the cell catches.
    The physics forms carry offsets after the identity, so the form counts when
    it is the whole part OR a prefix followed by ``:`` (``point:P1`` binds
    ``point:P1:0.0000:0.0000``)."""
    if form.startswith("name:"):
        return form in parts
    return any(part == form or part.startswith(form + ":") for part in parts)


def _assert_every_form_is_bound(commands) -> None:
    """Н1 (04.10.2026): the union of the two registries' anchor parts must carry
    EVERY form the fixture declares, each as its OWN binding. Two forms that
    share one key leave one unbound and redden HERE, in the through cell — the
    old ``csheet1`` / ``S4_csheet2`` / ``S5_csheet2`` copies all sat on
    ``cpoint``'s ``point:P1:0.0000:0.0000``, so losing the whole
    ``sheet_templates`` block in the step was invisible."""
    parts = _anchor_parts(commands)
    assert parts, "no registry key was planned — the form check would be vacuous"

    missing = [form for form in (
        "name:E",                     # the tree-placed entity itself
        "name:E__ti1",                # tree_instances copy 1 (its own key)
        "name:E__ti2",                # tree_instances copy 2 (its own key)
        "name:cabs",                  # absolute clone_placement
        "name:cnest/inner1/inner2",   # nested cell, path-composed at depth
        "name:S4_csheet2",            # sheet_template `many`, absolute, sheet S4
        "name:S5_csheet2",            # sheet_template `many`, absolute, sheet S5
        "point:P1",                   # cpoint — anchor_point at origin
        "point:P2",                   # csub — cross-file point
        "anchor:U1",                  # cref — anchor_ref
        "role:FPGA",                  # crole — anchor_role
        "thermal:tva1",               # thermal_via_arrays matrix
        "net:nt1",                    # net_traces trace
    ) if not _form_is_bound(parts, form)]

    # The `one` sheet_template keeps `anchor_point P1` but is SHIFTED to
    # [3.0, 0.0]: either key proves the copy survived the step AND stayed
    # distinct from `cpoint`.
    if not (any(p == "name:csheet1" for p in parts)
            or any(p.startswith("point:P1:3") for p in parts)):
        missing.append("name:csheet1 | point:P1:3")

    assert not missing, (
        "the declared form(s) "
        + ", ".join(repr(m) for m in missing)
        + " produced no own registry binding — collapsed onto another form; "
        f"anchor parts: {sorted(parts)}")


def _schema(path) -> int | None:
    p = Path(path)
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8")).get("schema_version")


def _registry_keys(path) -> set:
    p = Path(path)
    if not p.exists():
        return set()
    return {k for k in json.loads(p.read_text(encoding="utf-8"))
            if k != "schema_version"}


def _all_record_uuids(cfg) -> set:
    out = set()
    for cell in cfg.cells.values():
        out.add(cell.uuid)
    for point in cfg.points.values():
        out.add(point.uuid)
    for seq in (cfg.clone_placements, cfg.entities, cfg.chains,
                cfg.thermal_via_arrays, cfg.net_traces, cfg.coordinate_placements,
                cfg.imprints):
        for rec in seq:
            uid = getattr(rec, "uuid", None)
            if uid:
                out.add(uid)
    return out


def _assert_registry_keys_are_uuids(cfg3, via_path, trk_path):
    """Every RECORD-identifying part of a lifted registry key is the record's
    UUID (Р-У5.1): a uuid found in a `name:`/`thermal:`/`net:`/`point:` anchor
    and, unless it is the literal `thermal_via_array`, in the template part.
    Physics prefixes (`pad:`/`anchor:`/`role:`) are left alone."""
    uuids = _all_record_uuids(cfg3)
    keys = _registry_keys(via_path) | _registry_keys(trk_path)
    assert keys, "the lift produced no registry keys — the check would be vacuous"
    for key in keys:
        anchor, template, _role, _index = key.split("|")
        if anchor.startswith(("name:", "thermal:", "net:", "point:")):
            found = _UUID_RE.findall(anchor)
            assert found, f"{key!r}: the record part is not a UUID"
            assert found[0] in uuids, (
                f"{key!r}: the anchor UUID is not one of the config's records")
        if template != "thermal_via_array":
            assert _UUID_RE.fullmatch(template), (
                f"{key!r}: the template part is not a UUID")
            assert template in uuids, (
                f"{key!r}: the template UUID is not one of the config's records")


# ── the cells ───────────────────────────────────────────────────────────────

def test_a_format2_graph_lifted_on_open_keeps_the_second_apply_empty(
        format3, tmp_path, monkeypatch):
    """The whole graph, written as format 2, applied, then lifted by the PRODUCT
    open path: the second apply creates, deletes and shifts NOTHING, and the
    registry is keyed by UUIDs."""
    root = _write_graph(tmp_path)
    sub = tmp_path / "sub.sexp"
    adapter = _Adapter()

    # ── phase 1 — format 2 (the product's own format): no lift, name keys ──
    monkeypatch.setattr(format_version, "CURRENT_FORMAT", 2)
    cfg2, _ = load_config(str(root))
    assert read_version(root) == 2 and read_version(sub) == 2, (
        "the fixture graph is not format 2 — the cell would be vacuous")
    via_path, trk_path = _registry_paths(cfg2, root)

    vias2, tracks2 = _all_commands(cfg2, adapter)
    assert vias2 or tracks2, "the fixture planned no copper (vacuous cell)"
    _assert_every_form_is_bound(list(vias2) + list(tracks2))
    _apply(adapter, cfg2, root, vias2, tracks2)
    board_before = _board_uuids(adapter)
    positions_before = _board_positions(adapter)
    assert board_before, "nothing reached the board"
    assert _schema(via_path) == 1, "the pre-lift registry is not schema 1"

    # ── phase 2 — format 3: the REAL open path lifts graph AND registries ──
    monkeypatch.setattr(format_version, "CURRENT_FORMAT", 3)
    cfg3, _ = load_config(str(root))
    assert read_version(root) == 3 and read_version(sub) == 3, (
        "load_config did not lift the graph on disk")
    assert (_schema(via_path) == REGISTRY_SCHEMA_VERSION_FORMAT3
            and _schema(trk_path) == REGISTRY_SCHEMA_VERSION_FORMAT3), (
        "load_config did not lift both registries to the current schema")
    _assert_registry_keys_are_uuids(cfg3, via_path, trk_path)

    vias3, tracks3 = _all_commands(cfg3, adapter)
    v_create, v_delete, t_create, t_delete = _apply(adapter, cfg3, root,
                                                    vias3, tracks3)

    assert v_create == [] and t_create == [], (
        "the second apply wants to CREATE copper — a registry key did not lift "
        "to its uuid form")
    assert v_delete == [] and t_delete == [], (
        "the second apply wants to DELETE copper — the name-keyed registry "
        "entry was seen as stale")
    assert _board_uuids(adapter) == board_before, (
        "the board's UUID set shifted across the lift")
    assert _board_positions(adapter) == positions_before, (
        "an item moved across the lift (shift != 0)")


def test_a_second_open_after_the_lift_writes_nothing(format3, tmp_path,
                                                     monkeypatch):
    """Re-open after the lift is a no-op: not a byte of the graph or of either
    registry changes, and each lifted file has exactly ONE ``.bak``."""
    root = _write_graph(tmp_path)
    sub = tmp_path / "sub.sexp"
    adapter = _Adapter()

    monkeypatch.setattr(format_version, "CURRENT_FORMAT", 2)
    cfg2, _ = load_config(str(root))
    vias2, tracks2 = _all_commands(cfg2, adapter)
    _apply(adapter, cfg2, root, vias2, tracks2)
    via_path, trk_path = _registry_paths(cfg2, root)

    monkeypatch.setattr(format_version, "CURRENT_FORMAT", 3)
    load_config(str(root))                       # the one lift

    watched = [root, sub, Path(via_path), Path(trk_path)]
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in watched
              if p.exists()}
    assert before, "nothing to watch — the lift wrote nothing"

    load_config(str(root))                       # the SECOND open — must write 0

    after = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in watched
             if p.exists()}
    assert after == before, (
        "the second open rewrote a graph/registry file or touched its mtime")
    for p in watched:
        assert not p.exists() or len(list(p.parent.glob(p.name + ".bak*"))) == 1, (
            f"{p.name}: expected exactly one .bak")
