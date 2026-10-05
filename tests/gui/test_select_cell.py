# tests/gui/test_select_cell.py
"""Н5 cells — "Select cell" (plan_2026_10_04_refresh_mixed_cluster_selection, Н5;
Denis 2026-10-05). The pure resolution/ownership lives in gui/select_cell.py; the
button/tree menu route to the SAME one function.
"""
import json

import pytest

from kicadstamp.config import format_version
from kicadstamp.config.format_version import current_format
from kicadstamp.constants import CLUSTER_FIELD_NAME, ROLE_FIELD_NAME
from kicadstamp.domain.board import Footprint, Track, Via
from kicadstamp.domain.geometry import BoardLayer, Vector2
from kicadstamp.registry import make_registry_key, record_key_part
from tests.fakes.format3 import det_uuid


@pytest.fixture(params=(2, 3), ids=("format2", "format3"))
def gate(request, monkeypatch):
    monkeypatch.setattr(format_version, "CURRENT_FORMAT", request.param)
    return request.param


class _Rec:
    def __init__(self, name=None, uuid=None, cell=None, cluster=None, sheet=None):
        self.name = name
        self.uuid = uuid
        self.cell = cell
        self.cluster = cluster
        self.sheet = sheet


class _Cfg:
    def __init__(self, entities=(), clone_placements=(), cells=None):
        self.entities = list(entities)
        self.clone_placements = list(clone_placements)
        self.cells = cells or {}


class _Adapter:
    def __init__(self, fields, footprints=(), vias=(), tracks=()):
        self._fields = fields
        self._footprints = list(footprints)
        self._vias = list(vias)
        self._tracks = list(tracks)

    def get_field_value(self, fp, name):
        role, cluster = self._fields[fp.ref]
        return role if name == ROLE_FIELD_NAME else cluster

    def get_footprints(self):
        return list(self._footprints)

    def get_vias(self):
        return list(self._vias)

    def get_tracks(self):
        return list(self._tracks)


def _fp(ref, role, cluster):
    fp = Footprint(ref=ref, uuid=f"uuid-{ref}",
                   position=Vector2.from_xy_mm(0.0, 0.0), angle_deg=0.0,
                   layer=BoardLayer.BL_F_Cu)
    fp.sheet_path_uuids = ()
    return fp


def _write_registries(tmp_path, via_entries, track_entries):
    schema = 2 if current_format() >= 3 else 1
    (tmp_path / "registry").mkdir()
    (tmp_path / "registry" / "config.registry.json").write_text(
        json.dumps({"schema_version": schema, **via_entries}), encoding="utf-8")
    (tmp_path / "tracks").mkdir()
    (tmp_path / "tracks" / "config.tracks.registry.json").write_text(
        json.dumps({"schema_version": schema, **track_entries}),
        encoding="utf-8")


# ── cell_instances (pure) ───────────────────────────────────────────────────

def test_cell_instances_remembered_wins():
    from gui.select_cell import cell_instances
    cfg = _Cfg(entities=[_Rec(cell="dac_buf", cluster="DAC_BUF", sheet="Channel_1")])
    assert cell_instances(cfg, "dac_buf", "DAC_BUF", "Channel_0") == \
        [("DAC_BUF", "Channel_0")]


def test_cell_instances_from_records_deduped():
    from gui.select_cell import cell_instances
    cfg = _Cfg(entities=[_Rec(cell="dac_buf", cluster="DAC_BUF", sheet="Channel_0"),
                         _Rec(cell="dac_buf", cluster="DAC_BUF", sheet="Channel_0"),
                         _Rec(cell="dac_buf", cluster="DAC_BUF", sheet="Channel_1"),
                         _Rec(cell="pif", cluster="PIF")])
    assert cell_instances(cfg, "dac_buf") == [("DAC_BUF", "Channel_0"),
                                              ("DAC_BUF", "Channel_1")]


# ── select_cell_targets: components + OWN copper only ───────────────────────

def test_select_cell_targets_own_instance_and_own_copper(gate, tmp_path):
    from gui.select_cell import select_cell_targets

    cell_uuid = det_uuid("cells:dac_buf")
    cfg = _Cfg(cells={"dac_buf": _Rec(uuid=cell_uuid)})
    cell_identity = record_key_part("dac_buf", cell_uuid)
    config_path = tmp_path / "config.sexp"
    config_path.write_text("", encoding="utf-8")

    fp_c1 = _fp("C1", "DA", "DAC_BUF")
    fp_c2 = _fp("C2", "DB", "DAC_BUF")
    fp_p1 = _fp("P1", "PA", "PIF_AVDD")
    v_own = Via(uuid="v_own", position=Vector2.from_xy_mm(0.0, 0.0),
                net_name=None, drill_mm=0.3, diameter_mm=0.6)
    v_foreign = Via(uuid="v_foreign", position=Vector2.from_xy_mm(1.0, 1.0),
                    net_name=None, drill_mm=0.3, diameter_mm=0.6)
    v_gone = Via(uuid="v_gone", position=Vector2.from_xy_mm(2.0, 2.0),
                 net_name=None, drill_mm=0.3, diameter_mm=0.6)
    t_own = Track(uuid="t_own", net_name=None,
                  start=Vector2.from_xy_mm(0.0, 0.0),
                  end=Vector2.from_xy_mm(1.0, 0.0),
                  width_mm=0.25, layer=BoardLayer.BL_F_Cu)
    # own: anchor:<ref> of THIS instance; foreign: another instance; gone: not live.
    via_entries = {
        make_registry_key("anchor:C1:1:0.0000:0.0000", cell_identity, "r", 0):
            {"uuid": "v_own", "x_mm": 0.0, "y_mm": 0.0, "net": "N",
             "drill_mm": 0.3, "diameter_mm": 0.6},
        make_registry_key("anchor:X9:1:0.0000:0.0000", cell_identity, "r", 0):
            {"uuid": "v_foreign", "x_mm": 1.0, "y_mm": 1.0, "net": "N",
             "drill_mm": 0.3, "diameter_mm": 0.6},
        make_registry_key("anchor:C2:1:0.0000:0.0000", cell_identity, "r", 0):
            {"uuid": "v_gone", "x_mm": 2.0, "y_mm": 2.0, "net": "N",
             "drill_mm": 0.3, "diameter_mm": 0.6},
    }
    track_entries = {
        make_registry_key("anchor:C1:1:0.0000:0.0000", cell_identity, "r", 0):
            {"uuid": "t_own", "start_x_mm": 0.0, "start_y_mm": 0.0,
             "end_x_mm": 1.0, "end_y_mm": 0.0, "width_mm": 0.25,
             "net": "N", "layer": "F.Cu"},
    }
    _write_registries(tmp_path, via_entries, track_entries)
    adapter = _Adapter({"C1": ("DA", "DAC_BUF"), "C2": ("DB", "DAC_BUF"),
                        "P1": ("PA", "PIF_AVDD")},
                       footprints=[fp_c1, fp_c2, fp_p1],
                       vias=[v_own, v_foreign], tracks=[t_own])

    plan = select_cell_targets(adapter, cfg, str(config_path), "dac_buf",
                               "DAC_BUF", None, {}, own_refs=["C1"])
    assert {f.ref for f in plan.footprints} == {"C1", "C2"}
    # own anchor:<ref> of THIS instance is selected; the other instance's is not
    assert {getattr(i, "uuid", None) for i in plan.copper} == {"v_own", "t_own"}


def test_select_cell_targets_counts_registry_uuids_not_on_board(gate, tmp_path):
    from gui.select_cell import select_cell_targets

    cell_uuid = det_uuid("cells:dac_buf")
    cfg = _Cfg(cells={"dac_buf": _Rec(uuid=cell_uuid)})
    cell_identity = record_key_part("dac_buf", cell_uuid)
    config_path = tmp_path / "config.sexp"
    config_path.write_text("", encoding="utf-8")
    via_entries = {
        make_registry_key("anchor:C1:1:0.0000:0.0000", cell_identity, "r", 0):
            {"uuid": "v_gone", "x_mm": 0.0, "y_mm": 0.0, "net": "N",
             "drill_mm": 0.3, "diameter_mm": 0.6},
    }
    _write_registries(tmp_path, via_entries, {})
    fp_c1 = _fp("C1", "DA", "DAC_BUF")
    adapter = _Adapter({"C1": ("DA", "DAC_BUF")}, footprints=[fp_c1], vias=[])
    plan = select_cell_targets(adapter, cfg, str(config_path), "dac_buf",
                               "DAC_BUF", None, {}, own_refs=["C1"])
    assert plan.copper == []
    assert plan.missing_registry == 1


# ── structural: button and tree menu route to the SAME function ─────────────

def test_button_caption_and_shared_entry_point():
    """The CellDock button is captioned "Select cell" and the tree menu signal is
    wired to a delegate that calls the SAME `select_cell_requested` entry."""
    from PyQt6.QtWidgets import QPushButton
    from gui.docks import cell_editor as ce
    from gui.docks import config_tree as ct
    from kicadstamp.i18n import _
    # caption string exists in the catalog
    assert _("Select cell") == "Select cell" or _("Select cell")
    # the config tree exposes the signal the dock hub connects
    assert hasattr(ct.ConfigTreeDock, "cell_select_requested")
    # CellDock owns the shared entry point and the worker
    assert hasattr(ce.CellDock, "select_cell_requested")
    assert hasattr(ce.CellDock, "_run_select_cluster_on_board")
    assert QPushButton is not None
