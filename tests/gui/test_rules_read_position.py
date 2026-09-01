#!/usr/bin/env python3
"""ChainDock "Read current position" — the Origin tab's INFORMATIONAL readout
of the chain anchor's live position (design 2026_08_29_config_tree_read_live_
position.md §1.4) + guard tests locking the "no button" decisions
(§1.5 spoke / §1.6 Points / §1.9 nested clone). (2026-09-01, plan
rules_to_chains: RuleDock -> ChainDock in gui/docks/chain.py.)

Headless: the live resolver (read_anchor_live) is monkeypatched — the test
drives the dock's orchestration (adapter check, anchor read, label text,
failure warning) exactly like test_trees_dock.py drives _resolve_live_offset.
"""
import gui.docks.chain as rules_mod
from gui.docks.cell_editor import CellDock
from gui.docks.chain import ChainDock as RuleDock
from gui.docks.live_position import LiveRead
from gui.docks.points import PointsDock
from kicadstamp.domain.geometry import Vector2
from kicadstamp.exceptions import ValidationError
from kicadstamp.utils.units import MM


class _FakeBoard:
    def __init__(self):
        self.adapter = object()


def test_rule_origin_read_position_shows_anchor_readout(main_window, monkeypatch):
    """The origin readout is INFORMATIONAL: the anchor's live position/rotation
    is shown as a label, and NOTHING is written into any config field."""
    dock = RuleDock(main_window)
    main_window.connection.board = _FakeBoard()
    dock.origin_widget.load(mode="anchor", ref="U3")
    monkeypatch.setattr(rules_mod, "read_anchor_live", lambda *a, **k: LiveRead(
        position=Vector2.from_xy(int(12.5 * MM), int(-7.0 * MM)),
        rotation_deg=90.0, footprint=None))

    dock._on_origin_read_position()

    label = dock.anchor_position_label.text()
    assert "U3" in label
    assert "12.500" in label
    assert "-7.000" in label
    assert "90.0" in label


def test_rule_origin_read_position_warns_when_no_live_connection(main_window, monkeypatch):
    """No live board connection -> a warning, the readout label stays empty."""
    dock = RuleDock(main_window)
    dock.origin_widget.load(mode="anchor", ref="U3")
    warnings = []
    monkeypatch.setattr(rules_mod.QMessageBox, "warning",
                        lambda *a, **k: warnings.append(a) or None)

    dock._on_origin_read_position()

    assert warnings
    assert dock.anchor_position_label.text() == ""


def test_rule_origin_read_position_resolution_failure_warns(main_window, monkeypatch):
    """A resolution fatal (0/2+ anchor matches) -> warning, label left empty."""
    dock = RuleDock(main_window)
    main_window.connection.board = _FakeBoard()
    dock.origin_widget.load(mode="anchor", ref="U3")

    def _boom(*a, **k):
        raise ValidationError("ambiguous anchor")
    monkeypatch.setattr(rules_mod, "read_anchor_live", _boom)
    warnings = []
    monkeypatch.setattr(rules_mod.QMessageBox, "warning",
                        lambda *a, **k: warnings.append(a) or None)

    dock._on_origin_read_position()

    assert warnings
    assert "ambiguous anchor" in str(warnings[0])
    assert dock.anchor_position_label.text() == ""


# ── Guard tests: the "no button" decisions (design §1.5/§1.6/§1.9) ─────────


def test_rule_spoke_has_no_read_position_button(main_window):
    """A ManualSpoke has NO fixed ref at config-edit time (pool-based) — a
    read button there would guess (design §1.5). The rule has exactly ONE
    "Read current position" button, on the Origin tab."""
    dock = RuleDock(main_window)
    assert hasattr(dock, "read_position_button")          # the origin readout
    assert not hasattr(dock, "spoke_read_position_button")  # never a spoke one


def test_points_has_no_read_position_button(main_window):
    """Points already have "Resolve" (which reads the live board via
    resolve_point_chain) — a separate "Read current position" would be a
    duplicate (design §1.6)."""
    dock = PointsDock(main_window)
    assert hasattr(dock, "resolve_button")
    assert not hasattr(dock, "read_position_button")


def test_nested_clone_has_no_read_position_button(main_window):
    """A nested CellPlacement has no anchor (closed boundary) and its position
    is relative to the parent cell — no unique live referent to read (design
    §1.9)."""
    dock = CellDock(main_window)
    assert not hasattr(dock, "read_position_button")
    assert not hasattr(dock, "nested_read_position_button")
