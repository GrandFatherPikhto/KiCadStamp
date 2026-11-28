# tests/gui/docks/test_instances_nested_sheet_paths.py
"""The GUI doors of plan_2026_11_27_tree_instances_nested_sheet_paths (Т09/Т10/Т12/Т15).

The case: `Ch_L` is a POINT-anchored template with ONE top-level placement and six
NEIGHBOURING `net_trace` records; its eight placement records live on `ChannelL`,
`ChannelL/ArmP` and `ChannelL/ArmN`. The cells below drive the REAL doors — the
Instances dialog's own `_apply`/`accept`, the trees dock's own refresh, the hub's
own select-components delegate — and read the result in the CONSUMER, not in a
helper's return value:

  * Т09 — the row is created and re-read without `root:`, and the reopened profile
    carries the nested addresses (8 Entity + 6 net_trace, `ChannelR/ArmP|ArmN`),
    the source untouched and only the DECLARATION in the working set;
  * Т10 — editing the TEMPLATE (through the dock's staging) refreshes `Ch_R` with
    the new geometry and keeps the nested addresses — no restart, no flattening,
    and no copy of the generated objects written back;
  * Т12 — with the board door ARMED to raise, opening and accepting the dialog
    raises no UiThreadBoardReadRefused: the dialog, the source-sheet derivation
    and the expansion never read the board;
  * Т15 — "Select components" on the generated arm's Entity hands the REAL hub
    delegate the arm's OWN address (`ChannelR/ArmP`), never the flat `ChannelR`.

The graph is `tests/trees/nested_sheet_fixture.py` — synthetic, no `profiles/`.
"""
from __future__ import annotations

import logging

import pytest

from kicadstamp.config import load_config
from kicadstamp.config.sexp_format import dict_to_sexp, sexp_to_dict

from gui.docks.instances_dialog import TreeInstancesDialog

from tests.fakes.write_later import write_later
from tests.trees.nested_sheet_fixture import declare, nested_template_graph


@pytest.fixture(autouse=True)
def _active_graph_root(tmp_path):
    """The format-3 writer resolves references against the ACTIVE GRAPH ROOT; these
    cells write self-contained configs, so the root is a path that does not exist."""
    from kicadstamp.config_working_set import set_active_graph_root

    set_active_graph_root(tmp_path / "active_root.sexp")
    yield
    set_active_graph_root(None)


@pytest.fixture
def hub(main_window):
    """A real DockHub — the trees dock's `saved` wiring and the select-components
    delegate are the doors under test, not a direct call."""
    from gui.dock_hub import DockHub

    hub = DockHub(main_window, connection=main_window.connection, verbose=False)
    yield hub
    hub.log_dock.remove_handler()
    if hub._log_file_handler is not None:
        logging.getLogger().removeHandler(hub._log_file_handler)
        hub._log_file_handler.close()


def _write_nested(tmp_path, rows=None):
    """The synthetic Ch_L graph plus the given declaration rows, on disk."""
    data = nested_template_graph()
    data["tree_instances"] = list(rows or [])
    root = tmp_path / "root.sexp"
    write_later(root, dict_to_sexp(data, format_number=2))
    return root


def _on_disk(root) -> dict:
    return sexp_to_dict(root.read_text(encoding="utf-8"))


def _add_ch_r_row(dlg) -> None:
    """The row Denis would type: Ch_R / ChannelR / its OWN point anchor."""
    dlg._add_row()
    dlg.table.item(0, 0).setText("Ch_R")
    dlg.table.item(0, 1).setText("ChannelR")
    dlg.table.cellWidget(0, 3).set_anchor({"point": "Origin",
                                           "shift": [63.91, 40.0]})


# ── Т09: create + Save + reopen through the dialog's own accept ───────────

def test_instances_dialog_creates_and_reopens_nested_channel(main_window, tmp_path):
    """No `root:` is needed and none is written: the dialog's own `accept` proves
    the row with the loader, writes ONLY the declaration, and the reopened profile
    carries the eight nested Entity addresses and the six anchors."""
    root = _write_nested(tmp_path)
    cfg, _ctx = load_config(str(root))
    dlg = TreeInstancesDialog(main_window, root, cfg)
    assert dlg.current_template() == "Ch_L"

    _add_ch_r_row(dlg)
    assert dlg._apply() is True, dlg.refusal_label.text()

    rows = _on_disk(root)["tree_instances"]
    assert [(r["name"], r["sheet"]) for r in rows] == [("Ch_R", "ChannelR")]
    assert "root" not in rows[0], "the working case must not require Root"
    # the working set holds a DECLARATION, not materialized copies
    assert not [e["name"] for e in _on_disk(root)["entities"]
                if e["name"].endswith("__Ch_R")]

    cfg2, _ctx2 = load_config(str(root))
    sheets = {e.name: e.sheet for e in cfg2.entities
              if e.name.endswith("__Ch_R")}
    assert len(sheets) == 8
    assert sheets["opamp_channel_channell__Ch_R"] == "ChannelR"
    assert sheets["arm_in_channell_armp__Ch_R"] == "ChannelR/ArmP"
    assert sheets["arm_in_channell_armn__Ch_R"] == "ChannelR/ArmN"
    assert sheets["arm_fb_channell_armp__Ch_R"] == "ChannelR/ArmP"
    assert sheets["arm_fb_channell_armn__Ch_R"] == "ChannelR/ArmN"
    anchors = {t.name.removesuffix("__Ch_R"): t.anchor_sheet
               for t in cfg2.net_traces if (t.name or "").endswith("__Ch_R")}
    assert sorted(anchors.values()) == ["ChannelR", "ChannelR/ArmN",
                                       "ChannelR/ArmN", "ChannelR/ArmP",
                                       "ChannelR/ArmP", "ChannelR/ArmP"]
    left = {e.sheet for e in cfg2.entities if not e.name.endswith("__Ch_R")}
    assert left == {"ChannelL", "ChannelL/ArmP", "ChannelL/ArmN"}, (
        "the template's own records must keep their addresses")
    assert [t.name for t in cfg2.trees] == ["Ch_L", "Ch_R"]


# ── Т10: the TEMPLATE's edit refreshes the copy, nested addresses intact ──

def test_template_edit_refreshes_nested_instance_without_restart(hub, tmp_path):
    """The dock's own staging (`_mark_dirty`) plus the refresh timer's own tail
    (`flush`): the copy gets the template's NEW geometry, its several sheets stay
    `ChannelR`, `ChannelR/ArmP` and `ChannelR/ArmN`, and no generated object is
    written back to the file."""
    root = _write_nested(tmp_path, [declare()])
    dock = hub.trees_dock
    dock.set_root_file(root)
    # the format-4 file the loader left on disk writes THROUGH the active graph
    # root (the same plumbing every existing follow-template cell relies on)
    from kicadstamp.config_working_set import set_active_graph_root

    set_active_graph_root(root)
    template = next(t for t in dock._trees if t.name == "Ch_L")
    clone = next(t for t in dock._trees if t.name == "Ch_R")
    assert clone.nodes[0].xy == template.nodes[0].xy

    template.nodes[0].xy = (5.0, 6.0)
    dock._mark_dirty()
    written = _on_disk(root)
    assert dock._instances_refresh.flush() is True

    clone = next(t for t in dock._trees if t.name == "Ch_R")
    assert clone.nodes[0].xy == (5.0, 6.0), "the copy must follow the template"
    sheets = {e.name: e.sheet for e in dock._cfg.entities
              if e.name.endswith("__Ch_R")}
    assert sheets["arm_in_channell_armp__Ch_R"] == "ChannelR/ArmP"
    assert sheets["arm_in_channell_armn__Ch_R"] == "ChannelR/ArmN"
    after = _on_disk(root)
    assert after["tree_instances"] == written["tree_instances"]
    assert not [e["name"] for e in after["entities"]
                if e["name"].endswith("__Ch_R")], (
        "the refresh must not stage the generated copies as hand-written records")


# ── Т12: the dialog never reads the board on the UI thread ────────────────

def test_instances_ui_paths_do_not_read_board_on_ui_thread(real_main_window,
                                                            tmp_path, monkeypatch):
    """With the door ARMED to raise, opening the dialog on the nested profile and
    accepting a correct row raises no UiThreadBoardReadRefused — possible only
    because the source-sheet derivation and the expansion are config-only."""
    from tests.gui.test_board_door_offenders import _arm_the_door

    root = _write_nested(tmp_path)
    cfg, _ctx = load_config(str(root))
    from kicadstamp.config_working_set import set_active_graph_root

    set_active_graph_root(root)
    _arm_the_door(monkeypatch)

    dlg = TreeInstancesDialog(real_main_window, root, cfg)
    _add_ch_r_row(dlg)
    assert dlg._apply() is True, dlg.refusal_label.text()

    cfg2, _ctx2 = load_config(str(root))
    sheets = {e.name: e.sheet for e in cfg2.entities
              if e.name.endswith("__Ch_R")}
    assert sheets["arm_in_channell_armp__Ch_R"] == "ChannelR/ArmP"


# ── Т15: "Select components" carries the arm's OWN address ────────────────

@pytest.mark.parametrize("entity_name,expected_sheet,expected_cluster", [
    ("arm_in_channell_armp__Ch_R", "ChannelR/ArmP", "ARM_IN"),
    ("arm_in_channell_armn__Ch_R", "ChannelR/ArmN", "ARM_IN"),
])
def test_generated_arm_component_selection_uses_its_own_address(
        real_main_window, tmp_path, monkeypatch,
        entity_name, expected_sheet, expected_cluster):
    """The real hub delegate -> the real `entity_doors` door -> the selection entry
    point (the only substituted seam). The address handed over is the generated
    arm's OWN — never the flat `ChannelR`, which the arms share."""
    root = _write_nested(tmp_path, [declare()])
    cfg, _ctx = load_config(str(root))
    arm = next(e for e in cfg.entities if e.name == entity_name)
    hub = real_main_window._dock_hub
    captured = {}

    def _capture(name, file_path, cluster=None, sheet=None, **kwargs):
        captured.update(name=name, file_path=file_path, cluster=cluster,
                        sheet=sheet, **kwargs)

    monkeypatch.setattr(hub.cells_dock, "select_cell_components_requested",
                        _capture)
    hub._select_cell_components_from_tree(arm.cell, root, arm.cluster,
                                          arm.sheet, arm.name)

    address = captured["expected_address"]
    assert (address.sheet, address.cluster) == (expected_sheet, expected_cluster)
    assert captured["cluster"] == expected_cluster
    assert captured["sheet"] == expected_sheet
    assert captured["name"] == arm.cell
