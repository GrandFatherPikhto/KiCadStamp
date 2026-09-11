#!/usr/bin/env python3
"""ChainDock "Read current position" — the Origin tab's INFORMATIONAL readout
of the chain anchor's live position (design 2026_08_29_config_tree_read_live_
position.md §1.4) + guard tests locking the "no button" decisions
(§1.5 spoke / §1.6 Points / §1.9 nested clone). (2026-09-01, plan
rules_to_chains: RuleDock -> ChainDock in gui/docks/chain.py.)

2026-09-12 (task К, plan_2026_09_12_point_read_from_marker.md): the Points
guard changed shape, NOT meaning — see its own docstring below. §1.6 rejected
a button with the SAME meaning as Resolve ("where does this point resolve
now"); what Points gained is a read of the DRAGGED OVERLAY MARKER's centre.

Headless: the live resolver (read_anchor_live) is monkeypatched — the test
drives the dock's orchestration (adapter check, anchor read, label text,
failure warning) exactly like test_trees_dock.py drives _resolve_live_offset.
"""
import logging

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


def test_rule_origin_read_position_logs_error_when_no_live_connection(
        main_window, monkeypatch, caplog):
    """No live board connection -> ONE ERROR line in the Log (never a modal —
    plan_2026_09_11_no_modals_and_busy_kicad X.1), the readout label stays
    empty."""
    dock = RuleDock(main_window)
    dock.origin_widget.load(mode="anchor", ref="U3")

    def _no_boxes(*a, **k):
        raise AssertionError("a connection-state error must not open a QMessageBox")
    monkeypatch.setattr(rules_mod.QMessageBox, "warning", _no_boxes)
    caplog.clear()

    dock._on_origin_read_position()

    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1
    assert "No live board connection" in errors[0].message
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


def test_points_has_no_live_readout_and_the_marker_read_is_not_it(main_window):
    """design §1.6: Points already have "Resolve" (which reads the live board
    via resolve_point_chain and reports WHERE the point resolves), so a second
    button with that same meaning would be a duplicate — there is still none:
    no live-position readout label/button exists here.

    2026-09-12 (task К, plan_2026_09_12_point_read_from_marker.md, design
    §О.4): the dock DID gain a button, and it is deliberately not that readout
    — it reads the DRAGGED OVERLAY MARKER's centre back into the form (the
    very shape this dock drew with Resolve), i.e. a geometry hand-off from the
    user's own mouse drag, not "where does this anchor resolve right now".
    §1.6 therefore stands as a "no live readout" decision, and the new button
    must not become a second Resolve (its own behaviour is pinned down by
    tests/gui/test_points_dock.py's К.5 section)."""
    dock = PointsDock(main_window)
    assert hasattr(dock, "resolve_button")
    # No live-position readout anywhere on this dock (§1.6) …
    assert not hasattr(dock, "anchor_position_label")
    # … while the marker read exists and is a DIFFERENT button than Resolve.
    assert hasattr(dock, "read_position_button")
    assert dock.read_position_button is not dock.resolve_button
    assert dock.read_position_button.text() != dock.resolve_button.text()


def test_nested_clone_has_no_read_position_button(main_window):
    """A nested CellPlacement has no anchor (closed boundary) and its position
    is relative to the parent cell — no unique live referent to read (design
    §1.9)."""
    dock = CellDock(main_window)
    assert not hasattr(dock, "read_position_button")
    assert not hasattr(dock, "nested_read_position_button")
