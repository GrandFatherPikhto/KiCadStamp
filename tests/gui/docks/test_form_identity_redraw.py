# tests/gui/docks/test_form_identity_redraw.py
"""Part 0 of plan_2026_10_05_uuid_tails: «Перерисовать» from a dock's form must
carry the identity of the record it replaces.

Each dock builds the record from its WIDGETS and splices it into the loaded
Config, so under format 3 the spliced record used to lose its `uuid` and the
registry key refused (Denis, 05.10: thermal vias, «Размещение не удалось: …
у записи 'dac_0_thermal_via_pad' нет uuid»). The rule now lives in ONE place —
`kicadstamp.config.form_identity.identify` — and the five form docks call it.

These cells go through the docks' REAL collection half (`_collect_redraw_inputs`
/ `_collect_redraw_payload`) against a REAL format-3 `load_config` in `tmp_path`
(the files are written as format 2 and the loader lifts them, minting the
records' UUIDs). The board is never touched: `ApplyPipeline` is not reached —
the identity is decided on the UI side, before the worker.

Cells (rule 35), per the plan's list:
  * thermal via: saved record keeps its uuid AND resolves anchor_point_uuid;
  * thermal via: a rename in the form still edits the SAME record (same uuid);
  * thermal via: a brand-new record keeps ONE uuid across Redraw and Save;
  * thermal via: an anchor_point that names no point is refused (Log line);
  * net_trace: saved record keeps its uuid;
  * clone_placement: saved record keeps its uuid AND cell_uuid;
  * chain: the tree's chain dict keeps its uuid and its spoke's cell_uuid.
"""
import pytest

from gui.docks.chain import ChainDock
from gui.docks.net_trace import NetTraceDock
from gui.docks.placer import PlacerDock
from gui.docks.thermal_via import ThermalViaArrayDock
from kicadstamp.config import load_config, load_chain
from kicadstamp.config.sexp_format import dict_to_sexp
from kicadstamp.placement.services.via_planner import thermal_anchor_id


@pytest.fixture(autouse=True)
def _qt_app(qapp):
    """Some docks build real widgets; a QApplication must exist even when one of
    these cells runs FIRST in a pytest-xdist worker (Р3а-5)."""
    return qapp


@pytest.fixture(autouse=True)
def _restore_active_graph_root():
    """У3.5 (в): the format-3 writer stamp resolves references against the
    process-wide ACTIVE GRAPH ROOT; `_write` points it at the file it wrote and
    this restores it, so nothing leaks between tests."""
    from kicadstamp.config_working_set import active_graph_root, set_active_graph_root

    previous = active_graph_root()
    yield
    set_active_graph_root(previous)


def _write(path, data) -> None:
    path.write_text(dict_to_sexp(data, format_number=2), encoding="utf-8")
    from kicadstamp.config_working_set import set_active_graph_root

    set_active_graph_root(path)


# ── thermal via ────────────────────────────────────────────────────────────

def _thermal_dock(main_window, tmp_path):
    target = tmp_path / "root.sexp"
    _write(target, {
        # A thermal array's anchor_point must anchor on a FOOTPRINT (it looks up
        # a specific pad), so the point carries an anchor_role, not a bare xy.
        "points": {"p1": {"anchor_role": "FPGA"}},
        "thermal_via_arrays": [
            {"name": "tva1", "pad": "1", "anchor_point": "p1"},
        ],
    })
    dock = ThermalViaArrayDock(main_window)
    dock.set_root_path(target)
    saved = load_config(str(target))[0].thermal_via_arrays[0]
    return dock, target, saved


def _point_uuid(tmp_path, target) -> str:
    return load_config(str(target))[0].points["p1"].uuid


def test_thermal_redraw_keeps_the_saved_uuids(main_window, tmp_path):
    """The saved record is spliced with ITS OWN uuid and its anchor point's uuid
    — so the registry key `thermal:<uuid>` is the one the copper was placed
    under, instead of a fatal «record … has no uuid»."""
    dock, target, saved = _thermal_dock(main_window, tmp_path)
    point_uuid = _point_uuid(tmp_path, target)
    dock.load_entry({"name": "tva1", "pad": "1", "anchor_point": "p1",
                     "uuid": saved.uuid})

    payload = dock._collect_redraw_inputs()

    assert payload is not None
    record = payload["cfg"].thermal_via_arrays[0]
    assert record.uuid == saved.uuid
    assert record.anchor_point_uuid == point_uuid
    assert thermal_anchor_id(record) == f"thermal:{saved.uuid}"


def test_thermal_rename_in_the_form_still_edits_the_same_record(main_window, tmp_path):
    """A name typed over the loaded one must NOT mint a new identity: the record
    loaded from the form keeps its uuid (the same rule the writer's stamp
    applies to an in-place edit)."""
    dock, _target, saved = _thermal_dock(main_window, tmp_path)
    dock.load_entry({"name": "tva1", "pad": "1", "anchor_point": "p1",
                     "uuid": saved.uuid})
    dock.name_edit.setText("tva1_renamed")

    payload = dock._collect_redraw_inputs()

    assert payload is not None
    spliced = payload["cfg"].thermal_via_arrays[-1]
    assert spliced.name == "tva1_renamed"
    assert spliced.uuid == saved.uuid


def test_thermal_new_record_keeps_one_uuid_from_redraw_to_save(main_window, tmp_path):
    """A brand-new record is minted ONCE: the uuid the Redraw preview placed the
    copper under is the uuid the Save writes — otherwise the next apply would
    prune the preview's copper as foreign."""
    target = tmp_path / "root.sexp"
    _write(target, {"points": {"p1": {"anchor_role": "FPGA"}}, "thermal_via_arrays": []})
    dock = ThermalViaArrayDock(main_window)
    dock.set_root_path(target)
    dock.new_thermal_via(target)
    dock.name_edit.setText("fresh")
    dock.pad_edit.setText("1")
    dock.origin_widget.load(mode="point", point="p1")

    payload = dock._collect_redraw_inputs()
    assert payload is not None
    preview_uuid = payload["cfg"].thermal_via_arrays[-1].uuid
    assert preview_uuid

    # A second Redraw reuses the same uuid (it is remembered while the form
    # still holds that brand-new record).
    payload2 = dock._collect_redraw_inputs()
    assert payload2["cfg"].thermal_via_arrays[-1].uuid == preview_uuid

    dock._on_save()
    written = load_config(str(target))[0].thermal_via_arrays
    fresh = next(t for t in written if t.name == "fresh")
    assert fresh.uuid == preview_uuid


def test_thermal_anchor_point_that_names_no_point_is_refused(
        main_window, tmp_path, caplog):
    """The form's point name is a REFERENCE: a name the graph does not have is a
    refusal reported in the Log (no modal), and nothing is handed to the
    worker."""
    dock, _target, saved = _thermal_dock(main_window, tmp_path)
    dock.load_entry({"name": "tva1", "pad": "1", "anchor_point": "typo_point",
                     "uuid": saved.uuid})

    with caplog.at_level("ERROR"):
        payload = dock._collect_redraw_inputs()

    assert payload is None
    assert "names no existing points record" in caplog.text
    assert "typo_point" in caplog.text


# ── net_trace ──────────────────────────────────────────────────────────────

def test_net_trace_redraw_keeps_the_saved_uuid(main_window, tmp_path):
    target = tmp_path / "root.sexp"
    _write(target, {"net_traces": [{"net": "CL", "anchor_role": "FPGA"}]})
    dock = NetTraceDock(main_window)
    dock.set_root_path(target)
    saved = load_config(str(target))[0].net_traces[0]
    dock.load_entry({"net": "CL", "anchor_role": "FPGA", "uuid": saved.uuid})

    payload = dock._collect_redraw_inputs()

    assert payload is not None
    assert payload["cfg"].net_traces[-1].uuid == saved.uuid


# ── clone_placement ────────────────────────────────────────────────────────

def test_clone_placement_redraw_keeps_its_uuid_and_cell_uuid(main_window, tmp_path):
    cells = tmp_path / "cells.sexp"
    _write(cells, {"cells": {"pi_filter": {"components": [], "vias": [], "tracks": []}}})
    target = tmp_path / "root.sexp"
    _write(target, {
        "include": ["cells.sexp"],
        "clone_placements": [
            {"cell": "pi_filter", "cluster": "C1", "name": "C1", "xy": [1.0, 2.0]},
        ],
    })
    dock = PlacerDock(main_window)
    dock.set_root_path(target)
    cfg = load_config(str(target))[0]
    saved = cfg.clone_placements[0]
    cell_uuid = cfg.cells["pi_filter"].uuid
    dock.load_placement({"cell": "pi_filter", "cluster": "C1", "name": "C1",
                         "xy": [1.0, 2.0], "uuid": saved.uuid})

    payload = dock._collect_redraw_inputs()

    assert payload is not None
    spliced = payload["cfg"].clone_placements[-1]
    assert spliced.uuid == saved.uuid
    assert spliced.cell_uuid == cell_uuid


# ── coordinate placement ───────────────────────────────────────────────────

def test_coordinate_redraw_keeps_its_uuid_and_anchor_point_uuid(main_window, tmp_path):
    """0а-2: the coordinate path needs the same identity — its own uuid AND the
    anchor point's uuid (the reference resolution reads it)."""
    target = tmp_path / "root.sexp"
    _write(target, {
        "points": {"p1": {"anchor_role": "FPGA"}},
        "coordinate_placements": [
            {"name": "cp1", "cluster": "C1", "role": "R1",
             "anchor_point": "p1", "x_mm": 1.0, "y_mm": 2.0},
        ],
    })
    cfg = load_config(str(target))[0]
    saved = cfg.coordinate_placements[0]
    dock = PlacerDock(main_window)
    dock.set_root_path(target)
    dock.load_placement({"name": "cp1", "cluster": "C1", "role": "R1",
                         "anchor_point": "p1", "x_mm": 1.0, "y_mm": 2.0,
                         "uuid": saved.uuid})

    payload = dock._collect_redraw_inputs()

    assert payload is not None
    spliced = payload["cfg"].coordinate_placements[-1]
    assert spliced.uuid == saved.uuid
    assert spliced.anchor_point_uuid == saved.anchor_point_uuid


# ── chain ──────────────────────────────────────────────────────────────────

def test_chain_redraw_keeps_the_chain_uuid_and_the_spoke_cell_uuid(main_window, tmp_path):
    """The Config tree hands the chain as a raw dict; the redraw must splice that
    chain with its own uuid AND resolve each spoke's `cell` to `cell_uuid`."""
    target = tmp_path / "root.sexp"
    _write(target, {
        "cells": {"cap": {"components": [], "vias": [], "tracks": []}},
        "chains": [{"net": "+3V3", "anchor_role": "FPGA",
                    "spokes": [{"pad": "17", "cell": "cap"}]}],
    })
    dock = ChainDock(main_window)
    dock.set_root_path(target)
    cfg = load_config(str(target))[0]
    saved = cfg.chains[0]
    cell_uuid = cfg.cells["cap"].uuid
    # The tree hands the chain as an identity WITHOUT a uuid (the В36 lift minted
    # its name into the file, so this is exactly what a name-keyed dict carries).
    chain_dict = {"name": saved.name, "net": "+3V3", "anchor_role": "FPGA",
                  "spokes": [{"pad": "17", "cell": "cap"}]}

    payload = dock._collect_redraw_payload(
        [load_chain(chain_dict)], raw_entries=[chain_dict])

    assert payload is not None
    spliced = payload["cfg"].chains[-1]
    assert spliced.uuid == saved.uuid
    assert spliced.spokes[0].cell_uuid == cell_uuid
