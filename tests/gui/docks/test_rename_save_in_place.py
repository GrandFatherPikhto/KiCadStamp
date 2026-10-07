# tests/gui/docks/test_rename_save_in_place.py
"""Part 0а-1 of plan_2026_10_05_uuid_tails: «Сохранить» after a RENAME in the
form must rename the record IN PLACE.

Part 0 made every dock's Redraw carry the loaded record's uuid, and Save reuses
that same uuid. Matching by NAME alone then found no record under the new name
and appended a second one — with the same uuid — and the format-3 writer stamp
refused the whole write ("duplicate uuid"), so a renamed record became
un-saveable. The rule now lives in the ONE write primitive,
`config_writer.upsert_list_entry`, and every Save path below goes through it.

One cell per Save path (five), plus the refusal when the new name is held by
ANOTHER record (nothing is written).
"""
import pytest

from kicadstamp.config.sexp_format import dict_to_sexp, sexp_to_dict
from kicadstamp.config_writer import RecordNameTaken


@pytest.fixture(autouse=True)
def _qt_app(qapp):
    """Docks build real widgets; Р3а-5."""
    return qapp


@pytest.fixture(autouse=True)
def _restore_active_graph_root():
    from kicadstamp.config_working_set import active_graph_root, set_active_graph_root

    previous = active_graph_root()
    yield
    set_active_graph_root(previous)


def _write(path, data) -> None:
    path.write_text(dict_to_sexp(data, format_number=3), encoding="utf-8")
    from kicadstamp.config_working_set import set_active_graph_root

    set_active_graph_root(path)


def _seed_fmt2(path, data) -> None:
    """Write format 2 and point the ACTIVE GRAPH ROOT at the file: the loader's
    lift mints every record's uuid AND its references' uuids, so the cell then
    works with the real identity of a format-3 graph (a hand-written format-3
    fixture would have to spell `cell_uuid`/`anchor_point_uuid` itself)."""
    path.write_text(dict_to_sexp(data, format_number=2), encoding="utf-8")
    from kicadstamp.config_working_set import set_active_graph_root

    set_active_graph_root(path)


def _section(path, section):
    return (sexp_to_dict(path.read_text(encoding="utf-8")) or {}).get(section) or []


# ── thermal_via_arrays ─────────────────────────────────────────────────────

def test_thermal_rename_saves_in_place(main_window, tmp_path):
    from gui.docks.thermal_via import ThermalViaArrayDock

    target = tmp_path / "root.sexp"
    _write(target, {"thermal_via_arrays": [
        {"name": "tva_a", "uuid": "U-A", "pad": "1", "anchor_role": "FPGA"},
        {"name": "tva_b", "uuid": "U-B", "pad": "2", "anchor_role": "FPGA"},
    ]})
    dock = ThermalViaArrayDock(main_window)
    dock.set_root_path(target)
    dock.load_entry({"name": "tva_a", "uuid": "U-A", "pad": "1", "anchor_role": "FPGA"})
    dock.name_edit.setText("tva_a_renamed")

    dock._on_save()

    records = _section(target, "thermal_via_arrays")
    assert [r["name"] for r in records] == ["tva_a_renamed", "tva_b"]
    assert [r["uuid"] for r in records] == ["U-A", "U-B"]


def test_thermal_rename_onto_a_taken_name_is_refused(main_window, tmp_path, caplog):
    from gui.docks.thermal_via import ThermalViaArrayDock

    target = tmp_path / "root.sexp"
    _write(target, {"thermal_via_arrays": [
        {"name": "tva_a", "uuid": "U-A", "pad": "1", "anchor_role": "FPGA"},
        {"name": "tva_b", "uuid": "U-B", "pad": "2", "anchor_role": "FPGA"},
    ]})
    before = target.read_text(encoding="utf-8")
    dock = ThermalViaArrayDock(main_window)
    dock.set_root_path(target)
    dock.load_entry({"name": "tva_a", "uuid": "U-A", "pad": "1", "anchor_role": "FPGA"})
    dock.name_edit.setText("tva_b")          # already used by the OTHER record

    with caplog.at_level("ERROR"):
        dock._on_save()                       # must not raise out of a Qt slot

    assert target.read_text(encoding="utf-8") == before
    assert "already used by another record" in caplog.text


# ── net_traces ─────────────────────────────────────────────────────────────

def test_net_trace_rename_saves_in_place(main_window, tmp_path):
    from gui.docks.net_trace import NetTraceDock

    target = tmp_path / "root.sexp"
    _write(target, {"net_traces": [
        {"net": "CL", "uuid": "U-NT", "anchor_role": "FPGA"},
    ]})
    dock = NetTraceDock(main_window)
    dock.set_root_path(target)
    dock.load_entry({"net": "CL", "uuid": "U-NT", "anchor_role": "FPGA"})
    dock.net_edit.setCurrentText("CL2")       # the form's rename = a new net

    dock._on_save()

    records = _section(target, "net_traces")
    assert [r["net"] for r in records] == ["CL2"]
    assert [r["uuid"] for r in records] == ["U-NT"]


# ── clone_placements ───────────────────────────────────────────────────────

def _clonable_project(tmp_path):
    """cells.sexp + root.sexp (format 2 -> lifted on load): ONE clone_placement."""
    cells = tmp_path / "cells.sexp"
    _seed_fmt2(cells, {"cells": {"pi_filter": {"components": [], "vias": [], "tracks": []}}})
    target = tmp_path / "root.sexp"
    _seed_fmt2(target, {
        "include": ["cells.sexp"],
        "clone_placements": [
            {"name": "C1", "cluster": "C1", "cell": "pi_filter", "xy": [1.0, 2.0]},
        ],
    })
    from kicadstamp.config import load_config

    cfg, _ = load_config(str(target))    # the lift already rewrote the file to 3
    return target, cfg.clone_placements[0]


def _section(path, section):
    return (sexp_to_dict(path.read_text(encoding="utf-8")) or {}).get(section) or []


def test_clone_placement_rename_saves_in_place(main_window, tmp_path):
    from gui.docks.placer import PlacerDock

    target, saved = _clonable_project(tmp_path)
    dock = PlacerDock(main_window)
    dock.set_root_path(target)
    dock.load_placement({"name": saved.name, "uuid": saved.uuid,
                         "cluster": saved.cluster, "cell": saved.cell,
                         "xy": [1.0, 2.0]})
    dock.placer_name_edit.setText("C1_renamed")

    dock._do_save()

    records = _section(target, "clone_placements")
    assert [r.get("name") for r in records] == ["C1_renamed"]
    assert [r["uuid"] for r in records] == [saved.uuid]


def test_coordinate_placement_rename_saves_in_place(main_window, tmp_path):
    from gui.docks.placer import PlacerDock

    target = tmp_path / "root.sexp"
    _seed_fmt2(target, {
        "points": {"p1": {"anchor_role": "FPGA"}},
        "coordinate_placements": [
            {"name": "cp1", "cluster": "C1", "role": "R1",
             "anchor_point": "p1", "x_mm": 1.0, "y_mm": 2.0},
        ],
    })
    from kicadstamp.config import load_config

    cfg, _ = load_config(str(target))
    saved = cfg.coordinate_placements[0]
    dock = PlacerDock(main_window)
    dock.set_root_path(target)
    dock.load_placement({"name": saved.name, "cluster": saved.cluster,
                         "role": saved.role, "anchor_point": saved.anchor_point,
                         "uuid": saved.uuid})
    dock.coordinate_form.name_edit.setText("cp1_renamed")

    dock._do_save()

    records = _section(target, "coordinate_placements")
    assert [r.get("name") for r in records] == ["cp1_renamed"]
    assert [r["uuid"] for r in records] == [saved.uuid]
    assert [r["anchor_point_uuid"] for r in records] == [saved.anchor_point_uuid]


def test_chain_rename_saves_in_place(main_window, tmp_path):
    from gui.docks.chain import ChainDock

    target = tmp_path / "root.sexp"
    _seed_fmt2(target, {
        "cells": {"cap": {"components": [], "vias": [], "tracks": []}},
        "chains": [{"name": "CH1", "net": "+3V3", "anchor_role": "FPGA",
                    "spokes": [{"pad": "17", "cell": "cap"}]}],
    })
    from kicadstamp.config import load_config

    cfg, _ = load_config(str(target))
    saved = cfg.chains[0]
    dock = ChainDock(main_window)
    dock.set_root_path(target)
    dock.load_chain({"name": saved.name, "net": saved.net,
                     "anchor_role": "FPGA", "uuid": saved.uuid,
                     "spokes": [{"pad": "17", "cell": "cap"}]})
    dock.name_edit.setText("CH1_renamed")

    dock._persist_chain("")

    records = _section(target, "chains")
    assert [r.get("name") for r in records] == ["CH1_renamed"]
    assert [r["uuid"] for r in records] == [saved.uuid]
