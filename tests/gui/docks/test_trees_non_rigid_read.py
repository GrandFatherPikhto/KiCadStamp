#!/usr/bin/env python3
"""Ч1-А of plan_2026_10_15_honest_live_read.md at the TREE level: the non-rigid
refusal reaches the Log (level error, the whole text with roles and both exits)
and NEVER a modal; the node/form keep their values; a node whose PARENT diverged
is refused naming the PARENT, not itself.

Denis's decision (20.10), verbatim in the plan: "Отказ — строки в Лог (уровень
error, весь текст с ролями и двумя выходами), без модального окна. Если пишущий
потребитель сегодня показывает фаталы чтения модалкой, то ЭТОТ отказ всё равно
идёт в Лог." The remaining read fatals keep their modal until Ч2 of the extract
cleanup — that is why the two paths are pinned side by side here.

The `QMessageBox.warning` of this suite is ARMED to explode (tests/gui/conftest
`_no_modal_message_boxes`), so every cell below proves "no modal was opened" by
simply not installing a stand-in: reaching one would fail the cell.
"""
import logging
from types import SimpleNamespace

import pytest

from kicadstamp.config import Cell, TemplateComponentSlot
from kicadstamp.constants import CLUSTER_FIELD_NAME, ROLE_FIELD_NAME
from kicadstamp.domain.board import Footprint
from kicadstamp.domain.geometry import BoardLayer, Vector2
from kicadstamp.exceptions import NonRigidClusterError, ValidationError
from kicadstamp.trees import Tree, TreeAnchor, TreeNode

import gui.docks.trees_dock as trees_mod
from gui.docks import live_position as lp
from gui.docks.trees_dock import TreesDock, _NodeDialog


@pytest.fixture(autouse=True)
def _qt_app(qapp):
    """A QApplication must exist even when one of these cells runs FIRST in a
    pytest-xdist worker — the form cells build a real widget."""
    return qapp


class _ClusterAdapter:
    """The adapter surface `_live_cluster_frame` touches, nothing else."""

    def __init__(self, footprints):
        self._fps = list(footprints)

    def refresh_board(self):
        pass

    def get_footprints(self):
        return list(self._fps)

    def get_field_value(self, fp, name):
        if name == ROLE_FIELD_NAME:
            return fp.role
        if name == CLUSTER_FIELD_NAME:
            return fp.cluster
        return None

    def get_footprint_pads(self, fp):
        return []


def _live_fp(ref, role, x_mm, y_mm, angle=0.0):
    fp = Footprint(ref=ref, uuid=f"uuid-{ref}",
                   position=Vector2.from_xy_mm(x_mm, y_mm),
                   angle_deg=angle, layer=BoardLayer.BL_F_Cu)
    fp.role = role
    fp.cluster = "mcu_mcu"
    fp.sheet_path_uuids = []
    return fp


def _mcu_cell():
    """The mcu_mcu fact cell: U5 and R_BOOT1_PD 50.3 mm apart."""
    return Cell(name="mcu_mcu", layer="F.Cu", anchor_role="U5",
                components=[
                    TemplateComponentSlot(role="U5", offset_along_mm=0.0,
                                          offset_across_mm=0.0, angle_deg=0.0),
                    TemplateComponentSlot(role="R_BOOT1_PD",
                                          offset_along_mm=50.3,
                                          offset_across_mm=0.0, angle_deg=0.0)])


def _mcu_adapter():
    """The board of the fact: R_BOOT1_PD stands next to U5 instead of 50.3 mm
    away — 49.8 mm of deviation, the measured number."""
    return _ClusterAdapter([
        _live_fp("U5", "U5", 100.0, 100.0, angle=90.0),
        _live_fp("R7", "R_BOOT1_PD", 100.5, 100.0, angle=-90.0)])


def _node(**overrides):
    base = dict(ref="CHILD", kind="placement", xy=(1.5, -2.5), polar=None,
                rotation=45.0, name=None, group=None, children=[])
    base.update(overrides)
    return TreeNode(**base)


# ── The worker: the refusal is CLASSIFIED, never swallowed ──────────────────

def _worker_payload(**overrides):
    payload = dict(cfg=SimpleNamespace(), adapter=object(), sheet_names={},
                   tree=SimpleNamespace(name="t"), parent_node=None,
                   ref="CHILD", kind="placement", base_anchor=None)
    payload.update(overrides)
    return payload


def test_the_reread_worker_marks_a_non_rigid_refusal_log_only(monkeypatch):
    """The worker answers plain data, and the ONE thing it adds for Ч1-А is the
    `log_only` flag — the UI half cannot tell a non-rigid refusal from any other
    by the text alone."""
    def _boom(*_a, **_k):
        raise NonRigidClusterError("mcu_mcu is not a rigid copy")

    monkeypatch.setattr(trees_mod, "_resolve_live_offset", _boom)

    result = trees_mod.run_node_reread_worker(_worker_payload())

    assert result["ok"] is False
    assert result["log_only"] is True
    assert "mcu_mcu" in result["error"]


def test_the_reread_worker_keeps_the_modal_for_the_other_refusals(monkeypatch):
    """Ч2 of the extract cleanup moves the REST of the read fatals to the Log —
    not this task. Every other refusal keeps its modal, so the flag stays off."""
    def _boom(*_a, **_k):
        raise ValidationError("ref not on board")

    monkeypatch.setattr(trees_mod, "_resolve_live_offset", _boom)

    result = trees_mod.run_node_reread_worker(_worker_payload())

    assert result["ok"] is False
    assert result["log_only"] is False


# ── The dock's UI half: a Log line, the node untouched, no modal ────────────

def test_a_non_rigid_reread_lands_in_the_log_and_the_node_keeps_its_numbers(
        caplog):
    """THE policy cell (Denis 20.10): the refusal is an ERROR line in the Log
    carrying the whole text — roles and both exits — and the node keeps every
    number it had. `QMessageBox.warning` is armed to explode, so a modal would
    fail this cell instead of hanging it."""
    dock = TreesDock.__new__(TreesDock)
    dock._active_op = "something"
    node = _node()

    text = ("FATAL ERROR: live cluster 'mcu_mcu' is not a rigid copy of cell "
            "'mcu_mcu' (worst deviation 49.800 mm)\n"
            "  → worst roles: R_BOOT1_PD (R7): 49.800 mm\n"
            "  → put the component(s) back onto the cell's places (Redraw) — "
            "or, if the board is right now, re-read the cell from the selection")
    with caplog.at_level(logging.ERROR, logger="gui.docks.trees_dock"):
        dock._finish_reread_node_flow(
            {"ok": False, "error": text, "log_only": True}, node)

    assert dock._active_op is None
    assert node.xy == (1.5, -2.5) and node.rotation == 45.0
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert errors, "the non-rigid refusal must be an ERROR line in the Log"
    logged = "\n".join(r.getMessage() for r in errors)
    assert "R_BOOT1_PD (R7)" in logged
    assert "re-read the cell from the selection" in logged


def test_the_other_refusals_say_it_in_a_modal(monkeypatch):
    """The same UI half, a refusal of the OLD class: the modal is still opened
    (Ч2 of the extract cleanup is the task that moves it), so the routing cell
    above cannot be satisfied by "never show anything"."""
    shown = []
    monkeypatch.setattr(trees_mod.QMessageBox, "warning",
                        lambda *a: shown.append(a))
    dock = TreesDock.__new__(TreesDock)
    dock._active_op = None

    dock._finish_reread_node_flow({"ok": False, "error": "ref not on board"},
                                  _node())

    assert shown and shown[0][2] == "ref not on board"


# ── A node under a diverged PARENT is refused naming the PARENT ─────────────

def test_a_diverged_parent_is_named_instead_of_the_node(monkeypatch):
    """Plan, Denis 20.10: "Если разошёлся родитель узла, текст называет
    родителя, а не сам узел." The child's own cluster is rigid and its own cell
    is never mentioned — the frame being READ is the parent's, so that is the
    cluster the reader refuses on.

    The real `_resolve_node_base_pose` and the real `read_record_live_pose` run
    here; only the config probe (which record a node ref names) is stubbed, and
    the adapter is the fact's own board."""
    record = SimpleNamespace(
        kind="placement",
        obj=SimpleNamespace(name="PARENT_ENTITY", cell="mcu_mcu",
                            cluster="mcu_mcu", sheet=None))
    monkeypatch.setattr(trees_mod, "_resolve_probe_ref",
                        lambda _cfg, _ref, _kind: (record, False))
    cfg = SimpleNamespace(cells={"mcu_mcu": _mcu_cell()})
    tree = Tree(name="t", anchor=TreeAnchor(is_origin=True), nodes=[])
    parent_node = TreeNode(ref="PARENT", kind="placement", xy=None, polar=None,
                           rotation=0.0, name=None, group=None, children=[])

    with pytest.raises(NonRigidClusterError) as ei:
        trees_mod._resolve_node_base_pose(cfg, _mcu_adapter(), {}, tree,
                                          parent_node, None)

    text = str(ei.value)
    assert "mcu_mcu" in text and "R_BOOT1_PD (R7)" in text
    assert "CHILD" not in text          # the node itself is not accused


def test_the_form_read_puts_the_refusal_in_the_log_without_a_modal(
        caplog, monkeypatch):
    """The node FORM's "Read current position": the non-rigid refusal is a Log
    line and the offset fields keep exactly what the user typed. Every other
    validation error in this very method still opens its modal."""
    def _boom(*_a, **_k):
        raise NonRigidClusterError(
            "FATAL ERROR: live cluster 'mcu_mcu' is not a rigid copy of cell "
            "'mcu_mcu' — put the component(s) back onto the cell's places "
            "(Redraw)")

    monkeypatch.setattr(trees_mod, "_resolve_live_offset", _boom)
    tree = Tree(name="t", anchor=TreeAnchor(is_origin=True), nodes=[_node()])
    cfg = SimpleNamespace(cells={"mcu_mcu": _mcu_cell()})
    dlg = _NodeDialog(None, [], set(), "Edit node", cfg=cfg, adapter=object(),
                      sheet_names={}, tree=tree, parent_node=None,
                      existing=_node())
    before = (dlg.offset_widget.x_edit.text(), dlg.offset_widget.y_edit.text())

    with caplog.at_level(logging.ERROR, logger="gui.docks.trees_dock"):
        dlg._on_read_position()

    assert (dlg.offset_widget.x_edit.text(),
            dlg.offset_widget.y_edit.text()) == before
    logged = "\n".join(r.getMessage() for r in caplog.records
                       if r.levelno == logging.ERROR)
    assert "not a rigid copy" in logged
    assert "(Redraw)" in logged


# ── The anchor page's three workers: ONE of them writes, so ONE is strict ────

def test_the_marker_read_is_strict_and_the_two_drawing_workers_are_not(
        monkeypatch):
    """`_read_marker_worker`'s answer is written into the node's `anchor_xy`, so
    it asks for the strict read; `_ensure_bbox_worker` / `_ensure_marker_worker`
    only DRAW what stands on the board and never write a config, so they keep
    the default. Spying on the ONE reader is the honest way to pin the wiring:
    the values they draw with are the reader's business (Ж5 above).

    Mutation check: drop `strict=True` from the marker read (or add it to a
    drawing worker) and this cell names which side moved."""
    from gui.entity import anchor_workers as workers_mod
    from kicadstamp.domain.geometry import Vector2 as _V

    seen = []

    def _spy(*_a, **kwargs):
        seen.append(kwargs)
        return _V.from_xy(0, 0), 0.0, False

    monkeypatch.setattr(workers_mod, "_live_cluster_frame", _spy)
    monkeypatch.setattr(workers_mod.overlay_markers.owner, "read_position",
                        lambda *_a, **_k: (1.0, 2.0))
    monkeypatch.setattr(workers_mod.overlay_markers.owner, "ensure_bbox",
                        lambda *_a, **_k: "bbox-uuid")
    monkeypatch.setattr(workers_mod.overlay_markers.owner, "ensure_marker",
                        lambda *_a, **_k: "marker-uuid")
    cell = _mcu_cell()
    adapter = _mcu_adapter()

    workers_mod._read_marker_worker(adapter, cell, "mcu_mcu", "", {}, "key")
    workers_mod._ensure_bbox_worker(adapter, cell, "mcu_mcu", "", {}, "key",
                                    "F.Cu")
    workers_mod._ensure_marker_worker(adapter, cell, "mcu_mcu", "", {}, "key",
                                      "F.Cu")

    assert len(seen) == 3
    assert seen[0].get("strict") is True, "the marker READ writes the config"
    assert seen[1].get("strict", False) is False
    assert seen[2].get("strict", False) is False
