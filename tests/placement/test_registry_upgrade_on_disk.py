# tests/placement/test_registry_upgrade_on_disk.py
"""У5.4 (plan §6, Р-У5.1/Р-У5.5/Р-У5.6): lifting the copper REGISTRIES from
schema 1 (name-keyed) to schema 2 (uuid-keyed) when a format-3 profile opens.

Two axes, one table each (rule 35):

- the KEY TABLE — ``map_registry_key`` over every form the У5.0 inventory lists
  (``name:`` entity / clone, ``point:``, ``role:``, ``anchor:``, ``thermal:``,
  ``net:``, ``pad:``, nested) maps ONLY the record-name parts to uuids and
  leaves the physics (``anchor:``/``role:``/``pad:``, offsets, ``index``, the
  ``thermal_via_array`` literal, the nested ``/…`` suffix) untouched;
- R-У5.6 — an orphan name and a name that resolves in TWO sections at once are
  left UNTOUCHED (never rewritten or dropped);
- the SWEEP — ``.bak`` keeps the previous bytes; a repeat open touches nothing
  (no write, no parse — one os.stat); a NEWER schema refuses before the first
  write; the working set stands the sweep down; both registry files are lifted;
  without the format-3 gate nothing is written at all.

This module imports ``kicadstamp.config`` first, which populates the module
graph and would MASK a reintroduced ``registry -> placement.commands ->
placement/__init__ -> executor -> registry`` cycle. U5.5 broke that cycle (the
``ViaCommand``/``TrackCommand`` import moved under ``TYPE_CHECKING``);
``test_kicadstamp_registry_imports_in_a_fresh_process`` below is the explicit
witness that it stays broken — the rest of the suite imports config first and
would never notice.
"""
import json
import logging
from pathlib import Path

import pytest

from kicadstamp.config import (
    Cell, CellPlacement, Chain, ClonePlacement, Config, Entity, ManualSpoke,
    NetTrace, Point, TemplateTrack, ThermalViaArrayConfig,
)
from kicadstamp.config import format_version
from kicadstamp.config import registry_upgrade as ru
from kicadstamp.config.loader import load_config
from kicadstamp.config.registry_upgrade import _build_index, map_registry_key
from kicadstamp.config_working_set import WORKING_SET
from kicadstamp.config.sexp_format import dict_to_sexp
from kicadstamp.exceptions import ValidationError
from kicadstamp.registry import (load_registry, load_track_registry,
                                 registries_empty_for)
from kicadstamp.utils.paths import (registry_path_for_config,
                                    track_registry_path_for_config)
from tests.fakes.format3 import det_uuid, format3, mint_format3  # noqa: F401




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

@pytest.fixture(autouse=True)
def _isolate_working_set():
    """The working set is a process-global singleton — a leaked staged state
    would decide the «working set stands the sweep down» cell for every later
    test (same isolation as tests/config/test_config_format_upgrade_on_disk.py)."""
    ws = WORKING_SET
    old_enabled = ws.enabled
    old_listeners = list(ws._listeners)
    ws.clear()
    ws._listeners = []
    ws.enabled = False
    yield
    ws.clear()
    ws._listeners = []
    ws.enabled = old_enabled
    for fn in old_listeners:
        ws.add_listener(fn)


# ── the key table (Р-У5.1): record names -> uuids, physics untouched ────────

# Deterministic uuids of the fixture records.
_UUID = {
    "leaf": "uuid-leaf", "lvl1": "uuid-lvl1", "lvl2": "uuid-lvl2",
    "P1": "uuid-P1",
    "cabs": "uuid-cabs", "cref": "uuid-cref", "crole": "uuid-crole",
    "cpoint": "uuid-cpoint", "cnest": "uuid-cnest",
    "E": "uuid-E", "tva1": "uuid-tva1", "nt1": "uuid-nt1",
}


def _leaf():
    return Cell(name="leaf", layer="F.Cu", uuid=_UUID["leaf"])


def _cfg() -> Config:
    return Config(
        layer="F.Cu",
        cells={
            "leaf": _leaf(),
            "lvl2": Cell(name="lvl2", uuid=_UUID["lvl2"], clone_placements=[
                CellPlacement(name="inner2", cell="leaf", xy=(1.0, 0.0))]),
            "lvl1": Cell(name="lvl1", uuid=_UUID["lvl1"], clone_placements=[
                CellPlacement(name="inner1", cell="lvl2", xy=(1.0, 0.0))]),
        },
        points={"P1": Point(name="P1", xy=(10.0, 20.0), uuid=_UUID["P1"])},
        clone_placements=[
            ClonePlacement(cluster="cabs", cell="leaf", xy=(0.0, 0.0),
                           uuid=_UUID["cabs"]),
            ClonePlacement(cluster="cref", cell="leaf", xy=(0.0, 0.0),
                           anchor_ref="U1", uuid=_UUID["cref"]),
            ClonePlacement(cluster="crole", cell="leaf", xy=(0.0, 0.0),
                           anchor_role="FPGA", anchor_sheet="FPGA",
                           anchor_cluster="FPGA", uuid=_UUID["crole"]),
            ClonePlacement(cluster="cpoint", cell="leaf", xy=(0.0, 0.0),
                           anchor_point="P1", anchor_point_uuid=_UUID["P1"],
                           uuid=_UUID["cpoint"]),
            ClonePlacement(cluster="cnest", cell="lvl1", xy=(0.0, 0.0),
                           uuid=_UUID["cnest"]),
        ],
        entities=[Entity(name="E", cell="leaf", cluster="FPGA",
                         uuid=_UUID["E"], cell_uuid=_UUID["leaf"])],
        thermal_via_arrays=[ThermalViaArrayConfig(
            name="tva1", uuid=_UUID["tva1"], anchor_ref="U1", pad="1", net="GND",
            rows=1, cols=1, margin_mm=0.0, pattern="grid", drill_mm=0.3,
            diameter_mm=0.6)],
        net_traces=[NetTrace(
            name="nt1", uuid=_UUID["nt1"], net="GND", anchor_role="FPGA",
            anchor_sheet="FPGA", anchor_cluster="FPGA")],
    )


# form -> (old key, new key). The physics and the nested suffix are byte-equal
# on both sides by construction — that is the whole point of the table.
_KEY_CASES = {
    "name_entity": ("name:E|leaf|__spoke__|0",
                    "name:uuid-E|uuid-leaf|__spoke__|0"),
    "name_clone": ("name:cabs|leaf|__spoke__|0",
                   "name:uuid-cabs|uuid-leaf|__spoke__|0"),
    "point": ("point:P1:0.0000:0.0000|leaf|__spoke__|0",
              "point:uuid-P1:0.0000:0.0000|uuid-leaf|__spoke__|0"),
    "anchor": ("anchor:U1::0.0000:0.0000|leaf|__spoke__|0",
               "anchor:U1::0.0000:0.0000|uuid-leaf|__spoke__|0"),
    "role": ("role:FPGA:FPGA:FPGA::0.0000:0.0000|leaf|__spoke__|0",
             "role:FPGA:FPGA:FPGA::0.0000:0.0000|uuid-leaf|__spoke__|0"),
    "thermal": ("thermal:tva1|thermal_via_array|__spoke__|0",
                "thermal:uuid-tva1|thermal_via_array|__spoke__|0"),
    "net": ("net:nt1|nt1|__spoke__|0",
            "net:uuid-nt1|uuid-nt1|__spoke__|0"),
    "pad": ("pad:1|leaf|__spoke__|0",
            "pad:1|uuid-leaf|__spoke__|0"),
    "nested": ("name:cnest/inner1/inner2|leaf|__spoke__|0",
               "name:uuid-cnest/inner1/inner2|uuid-leaf|__spoke__|0"),
    "nested_own_cell": ("name:cnest|lvl1|__spoke__|0",
                        "name:uuid-cnest|uuid-lvl1|__spoke__|0"),
}


@pytest.mark.parametrize("form", sorted(_KEY_CASES))
def test_the_key_table_maps_record_names_only(form):
    old, expected = _KEY_CASES[form]
    new, problem = map_registry_key(old, _build_index(_cfg()))
    assert problem is None, f"{form}: unexpectedly left untouched ({problem})"
    assert new == expected


def test_a_key_with_the_wrong_shape_is_left_untouched():
    """``imprint:`` keys (never written to a registry, but defended) are not a
    four-part key — the sweep must not touch them."""
    new, problem = map_registry_key("imprint:E:via|track:0", _build_index(_cfg()))
    assert new == "imprint:E:via|track:0"
    assert problem == "unknown"


def test_a_slash_inside_a_cluster_name_survives():
    """Р7: a slash is legal inside a cluster name (``FPGA_PWR_BANK/VCCIO/139``),
    so a ``name:`` key whose value contains ``/`` must resolve the RECORD by its
    full name, never by splitting the key on ``/``."""
    cfg = _cfg()
    clone = ClonePlacement(cluster="FPGA_PWR_BANK/VCCIO/139", cell="leaf",
                           xy=(0.0, 0.0), uuid="uuid-slash")
    cfg.clone_placements.append(clone)
    idx = _build_index(cfg)
    new, problem = map_registry_key(
        "name:FPGA_PWR_BANK/VCCIO/139|leaf|__spoke__|0", idx)
    assert problem is None
    assert new == "name:uuid-slash|uuid-leaf|__spoke__|0"


# ── R-У5.6: a key that does not resolve is never rewritten ──────────────────

def test_an_orphan_name_is_left_untouched():
    old = "name:ghost|leaf|__spoke__|0"
    new, problem = map_registry_key(old, _build_index(_cfg()))
    assert new == old
    assert problem == "orphan"


def test_an_orphan_template_name_is_left_untouched():
    old = "name:cabs|ghost_cell|__spoke__|0"
    new, problem = map_registry_key(old, _build_index(_cfg()))
    assert new == old
    assert problem == "orphan"


def test_a_name_in_two_sections_is_ambiguous_and_left_untouched():
    """``name:X`` may be an Entity AND a clone_placement named X — two sections,
    two uuids — so the key is ambiguous and must not be guessed (Р-У5.6)."""
    cfg = _cfg()
    cfg.clone_placements.append(
        ClonePlacement(name="E", cluster="e2", cell="leaf", xy=(0.0, 0.0),
                       uuid="uuid-E2"))
    old = "name:E|leaf|__spoke__|0"
    new, problem = map_registry_key(old, _build_index(cfg))
    assert new == old
    assert problem == "ambiguous"


# ── the sweep on real files ────────────────────────────────────────────────

def _config_data() -> dict:
    return {
        "cells": {
            "leaf": {},
            "lvl2": {"clone_placements": [
                {"name": "inner2", "cell": "leaf", "xy": [1.0, 0.0]}]},
            "lvl1": {"clone_placements": [
                {"name": "inner1", "cell": "lvl2", "xy": [1.0, 0.0]}]},
        },
        "points": {"P1": {"anchor_ref": "U1"}},
        "entities": [{"name": "E", "cell": "leaf"}],
        "clone_placements": [
            {"cluster": "cabs", "name": "cabs", "cell": "leaf", "xy": [0.0, 0.0]},
            {"cluster": "cref", "name": "cref", "cell": "leaf", "xy": [0.0, 0.0],
             "anchor_ref": "U1"},
            {"cluster": "crole", "name": "crole", "cell": "leaf", "xy": [0.0, 0.0],
             "anchor_role": "FPGA", "anchor_sheet": "FPGA", "anchor_cluster": "FPGA"},
            {"cluster": "cpoint", "name": "cpoint", "cell": "leaf", "xy": [0.0, 0.0],
             "anchor_point": "P1"},
            {"cluster": "cnest", "name": "cnest", "cell": "lvl1", "xy": [0.0, 0.0]},
        ],
        "chains": [{"net": "GND", "name": "ch1", "anchor_point": "P1",
                    "spokes": [{"pad": "1", "cell": "leaf"}]}],
        "thermal_via_arrays": [{"name": "tva1", "anchor_ref": "U1", "pad": "1",
                                "net": "GND", "rows": 1, "cols": 1, "margin_mm": 0.0,
                                "pattern": "grid", "drill_mm": 0.3, "diameter_mm": 0.6}],
        "net_traces": [{"name": "nt1", "net": "GND", "anchor_role": "FPGA",
                        "anchor_sheet": "FPGA", "anchor_cluster": "FPGA",
                        "tracks": [{"start_along_mm": 0.0, "start_across_mm": 0.0,
                                    "end_along_mm": 2.0, "end_across_mm": 0.0,
                                    "width_mm": 0.25, "net": "GND", "layer": "F.Cu"}]}],
    }


_VIA = {"uuid": "u-via", "x_mm": 0.0, "y_mm": 0.0, "net": "GND",
        "drill_mm": 0.3, "diameter_mm": 0.6}
_TRK = {"uuid": "u-trk", "start_x_mm": 0.0, "start_y_mm": 0.0, "end_x_mm": 1.0,
        "end_y_mm": 0.0, "width_mm": 0.25, "net": "GND", "layer": "F.Cu"}

_VIA_OLD = {
    "name:E|leaf|__spoke__|0": _VIA,
    "thermal:tva1|thermal_via_array|__spoke__|0": _VIA,
    "name:cabs|leaf|__spoke__|0": _VIA,
}
_TRK_OLD = {
    "net:nt1|nt1|__spoke__|0": _TRK,
    "point:P1:0.0000:0.0000|leaf|__spoke__|0": _TRK,
    "anchor:U1::0.0000:0.0000|leaf|__spoke__|0": _TRK,
    "role:FPGA:FPGA:FPGA::0.0000:0.0000|leaf|__spoke__|0": _TRK,
    "pad:1|leaf|__spoke__|0": _TRK,
}


def _expect_mapped() -> tuple[dict, dict]:
    E, leaf = det_uuid("entities:E"), det_uuid("cells:leaf")
    tva1 = det_uuid("thermal_via_arrays:tva1")
    cabs = det_uuid("clone_placements:cabs")
    nt1 = det_uuid("net_traces:nt1")
    P1 = det_uuid("points:P1")
    via_new = {
        f"name:{E}|{leaf}|__spoke__|0": _VIA,
        f"thermal:{tva1}|thermal_via_array|__spoke__|0": _VIA,
        f"name:{cabs}|{leaf}|__spoke__|0": _VIA,
    }
    trk_new = {
        f"net:{nt1}|{nt1}|__spoke__|0": _TRK,
        f"point:{P1}:0.0000:0.0000|{leaf}|__spoke__|0": _TRK,
        f"anchor:U1::0.0000:0.0000|{leaf}|__spoke__|0": _TRK,
        f"role:FPGA:FPGA:FPGA::0.0000:0.0000|{leaf}|__spoke__|0": _TRK,
        f"pad:1|{leaf}|__spoke__|0": _TRK,
    }
    return via_new, trk_new


def _write_graph(tmp_path: Path, name: str = "root.sexp") -> Path:
    path = tmp_path / name
    path.write_text(dict_to_sexp(mint_format3(_config_data()), format_number=3),
                    encoding="utf-8")
    return path


def _write_registry(path: str, entries: dict, schema: int = 1) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"schema_version": schema, **entries},
                            indent=2, ensure_ascii=False), encoding="utf-8")


def _read(path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _setup(tmp_path: Path, schema: int = 1):
    root = _write_graph(tmp_path)
    via = registry_path_for_config(str(root))
    trk = track_registry_path_for_config(str(root))
    _write_registry(via, _VIA_OLD, schema=schema)
    _write_registry(trk, _TRK_OLD, schema=schema)
    return root, via, trk


def test_the_sweep_maps_both_files_and_keeps_the_previous_bytes(tmp_path, format3):  # noqa: F811
    root, via, trk = _setup(tmp_path)
    via_old = Path(via).read_text(encoding="utf-8")
    trk_old = Path(trk).read_text(encoding="utf-8")

    load_config(str(root))

    via_new, trk_new = _expect_mapped()
    via_after, trk_after = _read(via), _read(trk)
    assert via_after["schema_version"] == ru.TARGET_SCHEMA_VERSION
    assert trk_after["schema_version"] == ru.TARGET_SCHEMA_VERSION
    assert {k: v for k, v in via_after.items() if k != "schema_version"} == via_new
    assert {k: v for k, v in trk_after.items() if k != "schema_version"} == trk_new

    # .bak keeps the PREVIOUS bytes, both files.
    via_baks = list(Path(via).parent.glob("*.bak.*"))
    trk_baks = list(Path(trk).parent.glob("*.bak.*"))
    assert len(via_baks) == 1 and len(trk_baks) == 1
    assert via_baks[0].read_text(encoding="utf-8") == via_old
    assert trk_baks[0].read_text(encoding="utf-8") == trk_old


def test_a_repeat_open_touches_nothing_and_does_not_parse(tmp_path, monkeypatch, format3):  # noqa: F811
    root, via, trk = _setup(tmp_path)
    load_config(str(root))                       # 1: lifts both
    mtime = Path(via).stat().st_mtime_ns
    load_config(str(root))                       # 2: cold probe again (new mtime)
    assert Path(via).stat().st_mtime_ns == mtime, "the lift happened once"
    baks = sorted(p.name for p in Path(via).parent.glob("*.bak.*"))
    assert len(baks) == 1

    calls = []
    real = ru._read_schema_uncached

    def counting(path):
        calls.append(str(path))
        return real(path)

    monkeypatch.setattr(ru, "_read_schema_uncached", counting)
    load_config(str(root))                       # 3: everything warm

    assert calls == [], "the warm probe must not parse the registry again"
    assert Path(via).stat().st_mtime_ns == mtime
    assert sorted(p.name for p in Path(via).parent.glob("*.bak.*")) == baks


def test_a_newer_schema_refuses_before_any_write(tmp_path, format3):  # noqa: F811
    root, via, trk = _setup(tmp_path)
    _write_registry(via, _VIA_OLD, schema=ru.TARGET_SCHEMA_VERSION + 1)
    via_before = Path(via).read_text(encoding="utf-8")
    trk_before = Path(trk).read_text(encoding="utf-8")

    with pytest.raises(ValueError, match="schema_version"):
        load_config(str(root))

    assert Path(via).read_text(encoding="utf-8") == via_before
    assert Path(trk).read_text(encoding="utf-8") == trk_before
    assert list(Path(via).parent.glob("*.bak.*")) == []
    assert list(Path(trk).parent.glob("*.bak.*")) == []


def test_the_working_set_stands_the_sweep_down(tmp_path, monkeypatch, format3):  # noqa: F811
    root, via, trk = _setup(tmp_path)
    via_before = Path(via).read_text(encoding="utf-8")
    monkeypatch.setattr(WORKING_SET, "is_dirty", lambda: True)

    load_config(str(root))

    assert Path(via).read_text(encoding="utf-8") == via_before, "the registry was NOT written"
    assert list(Path(via).parent.glob("*.bak.*")) == []


def test_without_the_gate_nothing_is_written(tmp_path):
    """Format 2 (the product today): the sweep is a no-op — the registry keeps
    its schema-1 bytes. A gate widened to ``>= 2`` turns this cell red."""
    root = tmp_path / "root.sexp"
    root.write_text(dict_to_sexp(_config_data(), format_number=2), encoding="utf-8")
    via = registry_path_for_config(str(root))
    trk = track_registry_path_for_config(str(root))
    _write_registry(via, _VIA_OLD)
    _write_registry(trk, _TRK_OLD)
    via_before = Path(via).read_text(encoding="utf-8")
    trk_before = Path(trk).read_text(encoding="utf-8")

    load_config(str(root))

    assert Path(via).read_text(encoding="utf-8") == via_before
    assert Path(trk).read_text(encoding="utf-8") == trk_before
    assert list(Path(via).parent.glob("*.bak.*")) == []


def test_an_orphan_and_an_ambiguous_key_are_kept_with_a_warning(tmp_path, caplog, format3):  # noqa: F811
    root, via, trk = _setup(tmp_path)
    _write_registry(via, {
        "name:ghost|leaf|__spoke__|0": _VIA,          # orphan
        "name:cabs|leaf|__spoke__|0": _VIA,           # maps
    })
    # Make ``name:E`` ambiguous (an Entity AND a clone_placement named E).
    data = _config_data()
    data["clone_placements"].append(
        {"cluster": "e2", "name": "E", "cell": "leaf", "xy": [0.0, 0.0]})
    root.write_text(dict_to_sexp(mint_format3(data), format_number=3), encoding="utf-8")
    _write_registry(trk, {"name:E|leaf|__spoke__|0": _TRK})   # ambiguous

    with caplog.at_level(logging.WARNING, logger="kicadstamp.config.registry_upgrade"):
        load_config(str(root))

    via_after = _read(via)
    assert "name:ghost|leaf|__spoke__|0" in via_after, "the orphan key was kept"
    assert "name:cabs|leaf|__spoke__|0" not in via_after, "the resolvable key was mapped"
    trk_after = _read(trk)
    assert "name:E|leaf|__spoke__|0" in trk_after, "the ambiguous key was kept"
    warnings = [r.getMessage() for r in caplog.records
                if r.name == "kicadstamp.config.registry_upgrade"
                and r.levelno == logging.WARNING]
    assert any("NOT lifted" in m for m in warnings), warnings
    assert any("name:ghost" in m for m in warnings)
    assert any("name:E" in m for m in warnings)


# ── the READ gate (Н3): schema 1 is refused under format 3, read under 2 ─────
#
# The mirror image of the sweep: the lift WRITES schema 2, and the reader must
# REFUSE to read schema 1 back under the gate — otherwise reconcile sees a
# name-keyed registry against a uuid-keyed plan and prunes the copper. Below the
# gate (the product, format 2) schema 1 is read exactly as before.

def test_format2_still_reads_a_schema1_registry(tmp_path):
    """Boundary: below the gate a schema-1 (name-keyed) registry loads, as it
    always did — the refusal is format-3 only."""
    p = tmp_path / "via.registry.json"
    _write_registry(str(p), {"pad:1|leaf|__spoke__|0": _VIA}, schema=1)
    entries = load_registry(str(p))
    assert set(entries) == {"pad:1|leaf|__spoke__|0"}


def test_format3_reads_a_schema2_registry(tmp_path, format3):  # noqa: F811
    """Under the gate the LIFTED schema 2 (what the sweep writes) reads fine."""
    p = tmp_path / "via.registry.json"
    _write_registry(str(p), {"pad:1|leaf|__spoke__|0": _VIA}, schema=2)
    entries = load_registry(str(p))
    assert set(entries) == {"pad:1|leaf|__spoke__|0"}


def test_format3_refuses_a_schema1_registry(tmp_path, format3):  # noqa: F811
    """The gate: a schema-1 registry is a FATAL under format 3 — not a lenient
    read that would delete the copper (Н3)."""
    p = tmp_path / "via.registry.json"
    _write_registry(str(p), {"pad:1|leaf|__spoke__|0": _VIA}, schema=1)
    with pytest.raises(ValidationError, match="not lifted"):
        load_registry(str(p))


def test_format3_refuses_an_unlifted_track_registry(tmp_path, format3):  # noqa: F811
    """H2 (У5.5): the read gate is on the TRACK loader too. In `apply` the
    placement registry is built FIRST and masked a track loader that had stopped
    refusing; a tracks-only profile has no via file to mask at all.

    The file has NO ``schema_version`` (the legacy form, schema 1 by convention):
    ``check_schema_version`` ACCEPTS a missing field, so ``_refuse_unlifted_
    registry`` is the ONLY thing standing between such a file and a lenient read
    that would let its name-keyed tracks go to prune (exactly H2)."""
    p = tmp_path / "t.registry.json"
    p.write_text(json.dumps({"net:nt1|nt1|__spoke__|0": _TRK}), encoding="utf-8")
    with pytest.raises(ValidationError, match="not lifted"):
        load_track_registry(str(p))


# ── H11 (У5.5): the first-run hint reads the SAME files apply/lift use ───────

@pytest.mark.parametrize("explicit_empty, expected", [
    (True, True),    # the explicit pair is empty, the defaults are not
    (False, False),  # the explicit pair is not empty, the defaults are
])
def test_registries_empty_for_reads_the_explicit_paths(tmp_path, explicit_empty, expected):
    """H11 (У5.5): ``registries_empty_for`` must inspect the files an apply uses
    — the config's explicit ``registry_path:``/``track_registry_path:`` when set,
    not the defaults. EACH row makes the default answer WRONG: a function that
    fell back to the defaults would report the opposite of ``expected``."""
    config = tmp_path / "root.json"
    config.write_text(json.dumps({
        "registry_path": "alt/via.registry.json",
        "track_registry_path": "alt/trk.registry.json",
    }), encoding="utf-8")
    explicit = {} if explicit_empty else _VIA_OLD
    default = _VIA_OLD if explicit_empty else {}
    _write_registry(str(tmp_path / "alt" / "via.registry.json"), explicit)
    _write_registry(str(tmp_path / "alt" / "trk.registry.json"), explicit)
    _write_registry(registry_path_for_config(str(config)), default)
    _write_registry(track_registry_path_for_config(str(config)), default)

    assert registries_empty_for(str(config)) is expected


def test_registries_empty_for_does_not_raise_on_an_unlifted_registry(tmp_path, format3):  # noqa: F811
    """У5.5 (а): under the gate an UNLIFTED registry makes ``load_registry``
    raise (Н3). ``registries_empty_for`` is called from a Qt redraw slot that
    must never raise (``gui/docks/_common.py`` calls it OUTSIDE a try — a
    ValidationError escaping a slot takes PyQt6 down), so it treats the unlifted
    registry as NOT empty: the "first run" hint stays silent and the run itself
    refuses with its own fatal. A profile with no registries at all is still
    honestly empty."""
    config = tmp_path / "root.json"
    config.write_text(json.dumps({}), encoding="utf-8")

    # No registry files at all -> empty, and no raise.
    assert registries_empty_for(str(config)) is True

    # A single UNLIFTED (fieldless, schema-1-by-convention) via registry.
    via = registry_path_for_config(str(config))
    Path(via).parent.mkdir(parents=True, exist_ok=True)
    Path(via).write_text(json.dumps({"name:cabs|leaf|__spoke__|0": _VIA}),
                         encoding="utf-8")

    assert registries_empty_for(str(config)) is False


# ── V8 (У5.5): the import cycle stays broken in a FRESH process ──────────────

def test_kicadstamp_registry_imports_in_a_fresh_process():
    """V8 (У5.5): ``import kicadstamp.registry`` must succeed as the FIRST
    import in a fresh interpreter. U5.5 broke the ``registry <-> placement.
    executor`` cycle by moving ``ViaCommand``/``TrackCommand`` under
    ``TYPE_CHECKING``, and NOTHING else checks it: pytest imports
    ``kicadstamp.config`` first (this file, and the whole rig guard set), which
    populates the module graph and MASKS a reintroduced cycle.

    A copy of the fix reverted into ``registry.py`` makes a bare
    ``python -c "import kicadstamp.registry"`` fail while the rest of the suite
    stays green — this cell is the only witness."""
    import os
    import subprocess
    import sys

    from tests.paths import REPO_ROOT

    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        part for part in (str(REPO_ROOT), env.get("PYTHONPATH", "")) if part)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    proc = subprocess.run(
        [sys.executable, "-c", "import kicadstamp.registry"],
        cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=60, env=env)
    assert proc.returncode == 0, (
        "a fresh `import kicadstamp.registry` failed — the registry <-> "
        "placement import cycle is back:\n" + proc.stderr)


def test_a_registry_backup_that_cannot_be_taken_refuses_the_lift(
        tmp_path, monkeypatch, format3):  # noqa: F811
    """Р-У3.5 (У3.3): a `.bak` that CANNOT be taken refuses the whole lift — the
    registry keeps its schema-1 bytes and is not touched. The same escalation as
    the config sweep, whose `.bak` failure is a refusal too."""
    root, via, trk = _setup(tmp_path)
    via_before = Path(via).read_text(encoding="utf-8")
    trk_before = Path(trk).read_text(encoding="utf-8")

    def boom(path):
        raise OSError("read-only directory")

    monkeypatch.setattr(ru, "backup_file", boom)
    with pytest.raises(ValueError, match="backup"):
        load_config(str(root))

    assert Path(via).read_text(encoding="utf-8") == via_before
    assert Path(trk).read_text(encoding="utf-8") == trk_before
    assert list(Path(via).parent.glob("*.bak.*")) == []
    assert list(Path(trk).parent.glob("*.bak.*")) == []
